"""
Yona 新内核 · 事件源会话日志 (SessionLog)

脊柱。参照 dsh 的 session 模型：一切状态都来自一条 append-only 事件日志。
- append()  追加事件，返回带 seq 的事件
- derive_messages()  把日志投影成喂给模型的 messages 列表
- 可回放、可恢复、可审计：任何时刻都能从日志重建全部状态

事件类型（对齐 dsh 的 turn/step 边界）：
  turn/start, user/message, assistant/message, tool/call, tool/result,
  step/start, step/end, turn/end
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Literal

# ---------- 消息块类型 ----------

TextBlock = dict[Literal["type", "text"], str]
ReasoningBlock = dict[Literal["type", "text"], str]
ToolCallBlock = dict[Literal["type", "id", "name", "arguments"], str]
ToolResultBlock = dict[Literal["type", "toolCallId"], Any]

Block = TextBlock | ReasoningBlock | ToolCallBlock | ToolResultBlock

Message = dict[str, Any]  # {"role": ..., "content": [...]}


def _stamp_prefix(prefix: str, ts: float, fmt: str = "%m-%d %H:%M") -> str:
    """把"带 {time} 的标记模板"渲染成一行标记(内容层给模板,内核只填时间)。

    {time} 就地替换;模板不含 {time} 则后面补一个时间戳 ——
    与 LIFE_EVENT_PREFIX / USER_TIME_PREFIX 同一套约定。
    """
    stamp = time.strftime(fmt, time.localtime(ts))
    if "{time}" in prefix:
        return prefix.replace("{time}", stamp)
    return f"{prefix} {stamp}"


def _label_first_text(content: Any, label: str, strip_template: str = "") -> Any:
    """给内容列表的**第一条文本块**前面加一行标记;没有文本块则原样返回。

    只标第一条:一条消息可能带多个块(text / tool-call),标一次就够 ——
    每条都标会在正文里叠出多行标记(而正文是模型会"接着写"的地方)。

    strip_template 非空时,先把**被抄进正文的**那一行标记剥掉再加
    (见 strip_copied_prefix)—— 不剥就会拼出**两重**标记。
    """
    for i, b in enumerate(content):
        if isinstance(b, dict) and b.get("type") == "text":
            out = list(content)
            text = b.get("text", "")
            if strip_template:
                text = strip_copied_prefix(text, strip_template)
            out[i] = {**b, "text": f"{label}\n{text}"}
            return out
    return content


# 时间戳识别式:比 _stamp_prefix 的默认格式宽松一档。
# ⚠️ 必须是 `\d{1,4}(?:-\d{1,2}){1,2}` —— 前缀里的时间默认是 **`09-11 20:41`(月-日)**,
#    两段!写成"年月日三段"会一个都匹配不上(2026-09-21 被回归测试当场抓到)。
_TIME_TOKEN = r"\d{1,4}(?:-\d{1,2}){1,2}\s+\d{1,2}:\d{1,2}"


def copied_prefix_re(template: str) -> re.Pattern[str] | None:
    """由标记模板推出「被抄进正文」的识别式(模板为空 → None)。

    `{time}` 位置换成**任意时间戳** —— 抄进去的那个时间往往是**过期的**
    (她抄的是当时看到的那条标记里的时间,不是自己这一轮的时刻),
    按当前轮的 `_stamp_prefix(...)` 去精确匹配是匹配不上的。
    模板不含 `{time}` 时,允许尾部跟一个时间戳(与 `_stamp_prefix` 同款约定)。
    """
    if not template:
        return None
    body = re.escape(template)
    if "{time}" in template:
        body = body.replace(re.escape("{time}"), _TIME_TOKEN)
    else:
        body = body + r"(?:\s+" + _TIME_TOKEN + r")?"
    # 连续抄了多次也要一次剥干净
    return re.compile(r"^(?:" + body + r"\s*)+")


def strip_copied_prefix(text: str, template: str) -> str:
    """剥掉**被模型抄进正文**的那行标记(可能连抄多次)。

    **为什么会发生**:投影层给正文拼了一行「标记 + 时间戳」(她独处时写的事、
    真人消息的时间戳,都是这么拼的),而**模型会模仿自己的输出** ——
    它把这一行当成正文的一部分写了下来。日志里已有实例(2 条)。

    **不剥会怎样**(实测):下一次投影拼出**两重标记**,而且内层那个时间戳
    还是**过期的** —— 轮 14 外层 `09-12 07:18`、内层 `09-11 20:41`。
    模型于是拿到一个错误的前提,这正是本项目最在意的那类错。

    识别式**由 template 现推**:内核不写死任何文案(文案在内容层)。

    ⚠️ **只剥行首**。写在中间的不动 —— 那种得人看一眼,别静默改语义。
    ⚠️ 只治**读侧**:日志原文一个字不动(日志即真相)。
       根因是 **role 过载**(自走轮投影成 `assistant`,和"她对你说的话"同槽位),
       见 `docs/decisions/TIMELINE.md` 「三、方向探索」——
       这里是补丁,**不是**那条未决项的替代。
    """
    pat = copied_prefix_re(template)
    if pat is None:
        return text
    return pat.sub("", text)


@dataclass
class Event:
    """一条不可变日志事件。"""

    seq: int
    type: str
    data: dict[str, Any]
    time: float = field(default_factory=time.time)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Event #{self.seq} {self.type}>"


class SessionLog:
    """append-only 事件日志。"""

    def __init__(self, session_id: str, events: list[Event] | None = None) -> None:
        self.session_id = session_id
        self._events: list[Event] = list(events or [])
        self._next_seq = max((e.seq for e in self._events), default=0) + 1
        # 时间游标:非 None 时,append 不带 at 的事件用游标时刻(历史补写/回放)。
        # 默认 None = 墙钟。loop 零改动:补写轮设游标跑 run_turn,事件全落历史时刻。
        self._time_cursor: float | None = None

    # ---------- 时间游标(补写历史事件) ----------

    def set_time_cursor(self, ts: float) -> None:
        """设游标:之后 append(不带 at)的事件时间戳 = ts(而非墙钟)。"""
        self._time_cursor = ts

    def clear_time_cursor(self) -> None:
        self._time_cursor = None

    @property
    def time_cursor(self) -> float | None:
        """只读:当前时间游标(None = 墙钟)。system builder 据此识别回放轮。"""
        return self._time_cursor

    # ---------- 追加 ----------

    def append(
        self, type: str, *, at: float | None = None, **data: Any
    ) -> Event:
        """追加事件。at 显式指定 > 时间游标 > 墙钟(默认)。"""
        event = Event(
            seq=self._next_seq,
            type=type,
            data=data,
            time=(
                at
                if at is not None
                else (self._time_cursor if self._time_cursor is not None else time.time())
            ),
        )
        self._events.append(event)
        self._next_seq += 1
        return event

    # ---------- 查询 ----------

    @property
    def events(self) -> list[Event]:
        return list(self._events)

    def of_type(self, type: str) -> list[Event]:
        return [e for e in self._events if e.type == type]

    def last_seq(self) -> int:
        return self._next_seq - 1

    def replay_from(self, seq: int) -> list[Event]:
        """回放：返回 seq 之后（含）的全部事件。用于崩溃恢复/续跑。"""
        return [e for e in self._events if e.seq >= seq]

    # ---------- surface 注解(遮蔽) ----------

    def shadow(
        self,
        start_seq: int,
        end_seq: int | None = None,
        *,
        reason: str = "user",
    ) -> Event:
        """遮蔽一段已发生的事件 —— "打 tag:这段先不用了"(VISION 决策 9)。

        追加一条 surface/shadow 注解事件,日志原文一字不动;投影
        (derive_messages)按注解跳过被遮蔽事件,模型面与 UI 转录本随之更新。
        end_seq 缺省 = start_seq(单条)。

        校验(对齐 dsh fork 约束:不许切出孤儿 tool 消息):
        - 范围必须在已发生事件内(不能遮未来)。
        - tool 配对完整性:若被遮蔽段包含某 assistant 消息里的 tool-call,
          其 tool/result 也必须同段;反之亦然 —— 否则投影会留下
          "assistant 调了工具但没有结果"或"孤立 tool 结果",喂给模型不合法。
          当前轮(未结束)的工具痕迹天然不该遮(step 之间还要拿结果当原料),
          故校验按"配对必须同段"强制,调用方选范围时把整段一起纳入。
        """
        if end_seq is None:
            end_seq = start_seq
        if start_seq < 0 or end_seq < start_seq:
            raise ValueError(f"shadow 范围非法: [{start_seq}, {end_seq}]")
        last = self.last_seq()
        if end_seq > last:
            raise ValueError(
                f"shadow 范围 [{start_seq}, {end_seq}] 超出已发生事件(最大 seq {last})"
            )
        self._assert_tool_pairs_intact(start_seq, end_seq)
        return self.append(
            "surface/shadow", start=start_seq, end=end_seq, reason=reason
        )

    def replace(
        self,
        start_seq: int,
        end_seq: int,
        content: list[dict[str, Any]],
        *,
        reason: str = "compact",
    ) -> Event:
        """遮蔽 [start..end] 并用一条摘要消息顶替其位置 —— compact 原语。

        追加一条 surface/shadow 注解 + 一条 user/message(带 replaces 声明):
        日志原文一字不动;投影时被遮蔽段跳过,摘要消息**渲染在遮蔽段的位置**
        (anchor = replaces.start),而不是日志尾部 —— 这样压缩中间旧段后,
        后面的对话仍紧跟在摘要之后,顺序不乱(对照 dsh surface replace)。

        调用方负责生成摘要内容(把遮蔽段的 user/tool/assistant 事实压成一段),
        负责选对范围(配对完整,shadow() 会校验)。
        """
        self.shadow(start_seq, end_seq, reason=reason)
        return self.append(
            "user/message",
            content=content,
            source="compact",
            replaces={"start": start_seq, "end": end_seq},
        )

    def _assert_tool_pairs_intact(self, start_seq: int, end_seq: int) -> None:
        """遮蔽段内 tool-call/result 必须同段:assistant 的 tool-call 与其
        tool/result 一起进或一起留。基于 assistant/message 与 tool/result 事件
        的配对关系(assistant/message 块里的 tool-call id ↔ tool/result 的
        tool_call_id)。"""
        inside: set[int] = set(range(start_seq, end_seq + 1))
        # 收集所有配对:tool_call_id -> assistant seq(可能在消息块里多次出现,取首次)
        call_to_assistant: dict[str, int] = {}
        # tool/result 的 seq,按 tool_call_id
        result_seq_by_call: dict[str, int] = {}
        for e in self._events:
            if e.type == "assistant/message":
                for b in e.data.get("content", []):
                    if b.get("type") == "tool-call" and b.get("id"):
                        call_to_assistant.setdefault(b["id"], e.seq)
            elif e.type == "tool/result":
                cid = e.data.get("tool_call_id", "")
                if cid:
                    result_seq_by_call.setdefault(cid, e.seq)
        for cid, a_seq in call_to_assistant.items():
            r_seq = result_seq_by_call.get(cid)
            if r_seq is None:
                continue  # 无结果(中断遗留),无配对可校验
            a_in = a_seq in inside
            r_in = r_seq in inside
            if a_in != r_in:
                raise ValueError(
                    f"shadow 范围 [{start_seq}, {end_seq}] 切开了工具配对:"
                    f"tool-call #{cid} 在 seq {a_seq},其 result 在 seq {r_seq},"
                    f"必须同段遮蔽。请把整段(含 tool/result)一起纳入范围。"
                )

    def shadowed_seqs(self) -> set[int]:
        """从全部注解事件派生的被遮蔽 seq 集合(日志原文仍在,只是投影跳过)。"""
        hidden: set[int] = set()
        for e in self._events:
            if e.type == "surface/shadow":
                hidden.update(range(e.data["start"], e.data["end"] + 1))
        return hidden

    def surface_states(self) -> dict[int, str]:
        """每条事件的 surface 三态(供可观测/UI 转录本):
        'shadowed'(被遮蔽注解覆盖)/ 'current'(表面事件且未遮蔽)/ 'log-only'(非表面事件)。
        """
        shadowed = self.shadowed_seqs()
        surface_types = {"user/message", "assistant/message", "tool/result"}
        states: dict[int, str] = {}
        for e in self._events:
            if e.seq in shadowed:
                states[e.seq] = "shadowed"
            elif e.type in surface_types:
                states[e.seq] = "current"
            else:
                states[e.seq] = "log-only"
        return states

    # ---------- 投影 ----------

    def derive_messages(
        self,
        last_n: int | None = None,
        fold_tool_traces: bool = False,
        retained_tools: set[str] | None = None,
        last_turns: int | None = None,
        user_time_prefix: str = "",
    ) -> list[Message]:
        """
        把日志投影成喂给模型的 messages（按事件顺序）。

        - user/message       -> {"role": "user", "content": [...]}
        - assistant/message  -> {"role": "assistant", "content": [...]}
        - tool/call + tool/result -> {"role": "tool", ...} 挂回对应调用
        - turn/start、step 边界不产生消息，只用于回放与审计

        surface 遮蔽(VISION 决策 9):被 `shadow()` 注解覆盖的 seq 一律跳过 ——
        视图层当它没发生过(删除/撤回/编辑的落点),日志原文留痕。
        shadow 范围要成对:删一条 user 消息通常连带它引发的 assistant/tool 段。

        fold_tool_traces=True 启用"折叠视图"（日志原文不动，只改投影）：
        - 已结束轮次（出现过 turn/end）的工具痕迹折叠：assistant 消息里的
          tool-call 块剔除、对应 tool/result 事件跳过 —— 只留沉淀成人话的文本
        - retain_result=True 的工具（retained_tools 集合）痕迹跨轮保留（保真）
        - 当前未结束的轮次完整保留：step 之间还要拿工具结果当原料，不能断
        另:自走轮(source=self)的占位 user 消息(协议占位串)在**已结束轮**里跳过——
        那是给系统视角的说明,不该以 user 角色留在对话历史(否则后续看到
        user 说过"没有用户消息"这种怪话);**当前轮**的占位保留(它是本次
        调用的触发点,让模型知道要开新一段,而不是续写上一条 assistant)。

        **生活事件不进投影(2026-09-21 用户拍板)。** 已结束的**独处轮**
        (自走轮 / 补写轮 —— 都是 `source="self"`,触发时机不同、产物是同一个
        东西)的 assistant 消息**整条跳过**,不再以 `assistant` 槽位进上下文。

        为什么:**那是"她一个人时做的事",不是"她对用户说的话"** —— 两者挤在
        `role` 这唯一的结构信道上就是"role 过载"。2026-09 曾经靠一个**文本**
        前缀(`LIFE_EVENT_PREFIX`)去补这个区分,而模型会**照抄那个前缀**
        (日志里有 2 条实证,其中一条还刻了个假时间戳)—— 前缀是补丁,这里才是根因。

        取用路径只剩一条:**她自己的 `recall` 工具**(以 tool result 形式回来)。
        ⚠️ 只跳**已结束**轮:当前轮(还没 turn/end)的 assistant 消息必须留着 ——
        同一轮里 step 之间还要拿 tool-call / tool/result 当原料,跳掉会切出
        **孤儿 tool 消息**,喂给模型不合法。

        UI 聊天流本来就隐藏独处轮(`server/store.py` 的 `_messages_view`),
        内心面板另有出口(`/admin/life-events`),所以这一跳只影响模型上下文。

        ⚠️ `LIFE_EVENT_PREFIX` 那个模板**仍然要留着**:它是**读侧**识别用的 ——
        日志里那 2 条脏字是唯一证据,`strip_copied_prefix` 靠它把被抄进正文的
        标记剥掉(见 `character/personas.py`)。

        last_turns(2026-09 加,与 last_n 不同):按**已结束轮边界**保留最近
        N 轮 + 当前未结束轮,整体裁掉更旧的已结束轮 —— 绝不切散轮内的
        assistant tool-call/tool/result 配对(消息粒度裁剪会切出孤儿工具段)。
        0 或 None = 全量(默认,行为不变)。窗口单位是"轮"不是"消息",
        UI"保留最近 N 轮对话"即此语义;last_n 仍是粗暴的消息尾截,保留给
        调用方自行选择。

        user_time_prefix(2026-09-17 加):真人消息前拼一行「模板(含 {time})」
        再接正文,{time} = 那条消息发生的时刻。**历史里没有时间轴** ——
        每条消息都不带时刻,`[当前时间]` 每轮被覆盖、历史不留痕,于是
        "现在几点"她知道,"距上一条多久"她算不出来(减法没有输入);实测
        两条相隔 69 分钟的话被她读成连续的("刚不是说了嘛,你连着问两遍")。
        **只标真人侧(source=="user")**,理由两条:
          1. 模型模仿的是**它自己的输出** —— 生活事件那条标记就是这么被抄进
             正文的,不能再给 assistant 侧加新的可抄模板;
          2. 她的回复与真人消息同轮、时刻几乎相同,**标了真人就等于夹住了她的**。
        自走轮的占位消息(source="self")不打标;source 缺省按 "user" 处理
        (与 _last_user_epoch 同一口径,老日志不掉标)。
        后缀/前缀形状由调用方给(如 personas.USER_TIME_PREFIX),空串 = 不加
        (默认,行为不变)。**标签只跟着已经在窗口里的消息** —— 裁剪先发生,
        所以窗口内是一致覆盖,不会出现"有的有、有的没有"。
        """
        retained = retained_tools or set()
        # surface 遮蔽(VISION 决策 9):被 surface/shadow 注解覆盖的事件不进投影。
        # 日志原文不动;遮蔽只发生在"喂给模型/UI 的视图"层,与折叠同哲学。
        shadowed = self.shadowed_seqs()
        # replace 摘要(compact):带 replaces 声明的消息要渲染在遮蔽段的起点,
        # 不是日志尾部(线性 append 的默认位置)。收集 anchor:消息自身 seq 或
        # 其 replaces.start。投影时先按遮蔽段把事件切成"可见片段",再按 anchor 排序。
        # 简化做法:可见消息按 (anchor, 原始顺序) 稳定排序 —— 普通消息 anchor=seq,
        # 摘要消息 anchor=遮蔽起点,正好插在遮蔽段的位置,后续对话不受影响。
        # 两个集合:
        # - ended_turns:所有已结束轮(无条件计算)—— 用于跳过自走占位 user
        # - fold_turns:仅折叠视图生效时的已结束轮 —— 用于工具痕迹折叠
        ended_turns = {e.data["turn"] for e in self._events if e.type == "turn/end"}
        fold_turns = ended_turns if fold_tool_traces else set()
        # 自走轮集合(turn/start 带 source):assistant 消息本身不带 source,
        # 靠轮号归属 —— 判断"这条生活事件是谁的"用轮号查 self 轮。
        self_turns = {
            e.data["turn"]
            for e in self._events
            if e.type == "turn/start" and e.data.get("source") == "self"
        }
        # last_turns 轮窗口:允许的轮 = 最近 N 个已结束轮 + 所有未结束轮
        # (当前在跑/被打断没 turn/end 的轮永不裁)。None/0 = 全量。
        allowed_turns: set[int] | None = None
        if last_turns:
            ordered_ended = sorted(ended_turns)
            allowed_turns = set(ordered_ended[-last_turns:]) if ended_turns else set()
            for e in self._events:
                t = e.data.get("turn")
                if isinstance(t, int) and t not in ended_turns:
                    allowed_turns.add(t)
        # tool/result 事件不带工具名，先扫 assistant 消息建 call_id -> name
        id_to_name: dict[str, str] = {}
        if fold_tool_traces:
            for e in self._events:
                if e.type == "assistant/message":
                    for b in e.data.get("content", []):
                        if b.get("type") == "tool-call":
                            id_to_name[b["id"]] = b["name"]

        # 遮蔽段集合:把 shadowed seq 折叠成不重叠区间,便于判断某条消息该排哪。
        # (replace 摘要消息自己不在 shadowed 里,它排在其 replaces.start 处。)
        messages: list[Message] = []
        # (anchor_seq, order, message):稳定排序用。order = 该消息在日志里的出现序,
        # 保证同 anchor 的普通消息保持原始相对顺序(tool 结果紧跟 assistant 等)。
        anchored: list[tuple[int, int, Message]] = []
        order = 0
        for event in self._events:
            if event.seq in shadowed:
                continue  # 已被遮蔽:视图层当它没发生过,日志留痕
            data = event.data
            # 轮窗口裁剪:窗口外的已结束轮**整轮**跳过(轮内工具配对完整保留,
            # 绝不切散;窗口内外的判定只看轮号,与消息内容无关)。
            turn_of = data.get("turn")
            if allowed_turns is not None and isinstance(turn_of, int) \
                    and turn_of not in allowed_turns:
                continue
            anchor = event.seq
            replaces = data.get("replaces") if event.type == "user/message" else None
            if isinstance(replaces, dict) and "start" in replaces:
                anchor = replaces["start"]  # 摘要渲染在遮蔽段起点
            if event.type == "user/message":
                # 自走轮占位串:已结束轮的不进历史(见上注释);当前轮的保留(触发点)。
                if data.get("source") == "self" and data.get("turn") in ended_turns:
                    continue
                content = data["content"]
                # 真人消息打时间戳(2026-09-17):历史里没有时间轴,模型推不出
                # "距上一条多久";只标真人侧,理由见 docstring 的 user_time_prefix。
                if user_time_prefix and data.get("source", "user") == "user":
                    content = _label_first_text(
                        content, _stamp_prefix(user_time_prefix, event.time),
                        strip_template=user_time_prefix,
                    )
                order += 1
                anchored.append(
                    (anchor, order, {"role": "user", "content": content})
                )
            elif event.type == "assistant/message":
                # 生活事件(已结束的独处轮)**不进投影** —— 2026-09-21 用户拍板:
                # "既然都是 toolresult 的召回结果了,肯定不留常驻上下文里了啊"。
                # 理由与范围见 derive_messages 的 docstring(只跳已结束轮,
                # 当前轮的 assistant 必须留着,否则切出孤儿 tool 消息)。
                if data.get("turn") in self_turns \
                        and data.get("turn") in ended_turns:
                    continue
                content = data["content"]
                if data.get("turn") in fold_turns:
                    # 已结束轮:剔除非保真工具的 tool-call 块
                    content = [
                        b
                        for b in content
                        if b.get("type") != "tool-call" or b["name"] in retained
                    ]
                # 空消息(无文本、无 tool-call)协议非法且无信息量,投影时跳过。
                # 源头已在循环侧堵住,这里兜底防历史/外部日志。
                if not content:
                    continue
                order += 1
                anchored.append(
                    (anchor, order, {"role": "assistant", "content": content})
                )
            elif event.type == "tool/result":
                if data.get("turn") in fold_turns:
                    name = id_to_name.get(data.get("tool_call_id", ""), "")
                    if name not in retained:
                        continue  # 已结束轮的非保真工具结果 -> 折叠
                order += 1
                anchored.append(
                    (
                        anchor,
                        order,
                        {
                            "role": "tool",
                            "tool_call_id": data["tool_call_id"],
                            "content": data["content"],
                        },
                    )
                )
        anchored.sort(key=lambda item: (item[0], item[1]))
        messages = [m for _, _, m in anchored]
        return messages[-last_n:] if last_n else messages

    # ---------- 持久化 ----------

    def to_lines(self) -> list[str]:
        import json

        return [
            json.dumps(
                {"seq": e.seq, "type": e.type, "data": e.data, "time": e.time},
                ensure_ascii=False,
            )
            for e in self._events
        ]

    @classmethod
    def from_lines(cls, session_id: str, lines: list[str]) -> "SessionLog":
        import json

        events = [
            Event(
                seq=int(obj["seq"]),
                type=obj["type"],
                data=obj["data"],
                time=float(obj["time"]),
            )
            for line in lines
            if (obj := json.loads(line))
        ]
        return cls(session_id, events)
