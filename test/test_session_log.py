"""SessionLog 自测：一轮带工具的完整 turn，验证投影与回放。"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.loop import AgentLoop
from core.session_log import SessionLog, strip_copied_prefix
from core.tools import ToolRegistry

from character import personas as P


def build_turn() -> SessionLog:
    log = SessionLog("test-session")
    log.append("turn/start", turn=1)
    log.append("user/message", content=[{"type": "text", "text": "查一下今天的天气"}])
    log.append("step/start", turn=1, step=1)
    log.append(
        "assistant/message",
        content=[
            {"type": "reasoning", "text": "用户要天气，需要联网搜索。"},
            {
                "type": "tool-call",
                "id": "call_001",
                "name": "web_search",
                "arguments": '{"query": "today weather"}',
            },
        ],
    )
    log.append(
        "tool/result",
        tool_call_id="call_001",
        content=[{"type": "text", "text": "晴天，28°C"}],
    )
    log.append("step/end", turn=1, step=1)
    log.append("step/start", turn=1, step=2)
    log.append(
        "assistant/message",
        content=[{"type": "text", "text": "今天晴天，28 度。"}],
    )
    log.append("step/end", turn=1, step=2)
    log.append("turn/end", turn=1, reason={"kind": "completed"})
    return log


def test_derive_messages():
    log = build_turn()
    msgs = log.derive_messages()
    assert len(msgs) == 4, f"应 4 条消息，实际 {len(msgs)}"

    # 1. 用户消息
    assert msgs[0]["role"] == "user"
    assert msgs[0]["content"][0]["text"] == "查一下今天的天气"
    # 2. assistant 带 tool-call
    assert msgs[1]["role"] == "assistant"
    assert msgs[1]["content"][1]["type"] == "tool-call"
    assert msgs[1]["content"][1]["id"] == "call_001"
    # 3. tool 结果回挂同一个 id
    assert msgs[2]["role"] == "tool"
    assert msgs[2]["tool_call_id"] == "call_001"
    # 4. 最终回答
    assert msgs[3]["role"] == "assistant"
    assert msgs[3]["content"][0]["text"] == "今天晴天，28 度。"


def test_replay_and_recover():
    log = build_turn()
    # 模拟"崩溃在 tool/result 之后"，从那里回放
    crash_seq = log.of_type("tool/result")[0].seq
    tail = log.replay_from(crash_seq)
    assert [e.type for e in tail] == ["tool/result", "step/end", "step/start", "assistant/message", "step/end", "turn/end"]

    # 用 tail 重建一个新 log，derive 应得到"工具结果之后"的消息
    rebuilt = SessionLog(log.session_id, tail)
    msgs = rebuilt.derive_messages()
    assert msgs[0]["role"] == "tool"
    assert msgs[0]["tool_call_id"] == "call_001"


def test_persistence_roundtrip():
    log = build_turn()
    lines = log.to_lines()
    rebuilt = SessionLog.from_lines("test-session", lines)
    assert rebuilt.derive_messages() == log.derive_messages()
    assert rebuilt.last_seq() == log.last_seq()


def test_derive_skips_empty_assistant():
    """空 assistant 消息(无文本/无 tool-call)不进投影,防止下一轮 400。"""
    log = SessionLog("test")
    log.append("turn/start", turn=1)
    log.append("user/message", content=[{"type": "text", "text": "hi"}])
    log.append("assistant/message", content=[])  # 空(如历史遗留)
    log.append("assistant/message", content=[{"type": "text", "text": "正常回答"}])
    msgs = log.derive_messages()
    assert len(msgs) == 2, f"空消息未跳过: {msgs}"
    assert msgs[1]["content"][0]["text"] == "正常回答"


def build_two_turns() -> SessionLog:
    """两轮对话:Q1/A1(轮1)、Q2/A2(轮2)。返回 log 和各自事件 seq。"""
    log = SessionLog("test")
    log.append("turn/start", turn=1)
    u1 = log.append("user/message", content=[{"type": "text", "text": "第一问"}])
    a1 = log.append("assistant/message", content=[{"type": "text", "text": "第一答"}])
    log.append("turn/end", turn=1, reason={"kind": "completed"})
    log.append("turn/start", turn=2)
    u2 = log.append("user/message", content=[{"type": "text", "text": "第二问"}])
    a2 = log.append("assistant/message", content=[{"type": "text", "text": "第二答"}])
    log.append("turn/end", turn=2, reason={"kind": "completed"})
    return log, {"u1": u1.seq, "a1": a1.seq, "u2": u2.seq, "a2": a2.seq}


def test_shadow_hides_from_projection_but_keeps_log():
    """遮蔽 = 打 tag 声明不用:投影跳过,日志原文一字不动。"""
    log, seqs = build_two_turns()
    before = len(log.events)

    # 删掉"第一问 + 第一答"(轮 1 整段)
    log.shadow(seqs["u1"], seqs["a1"], reason="user-deleted")

    msgs = log.derive_messages()
    assert len(msgs) == 2, f"应只剩轮2两条,实际 {msgs}"
    assert msgs[0]["role"] == "user" and msgs[0]["content"][0]["text"] == "第二问"
    assert msgs[1]["role"] == "assistant" and msgs[1]["content"][0]["text"] == "第二答"
    # 日志原文没少,只是多了条注解
    assert len(log.events) == before + 1
    u1_event = next(e for e in log.events if e.seq == seqs["u1"])
    assert u1_event.data["content"][0]["text"] == "第一问"  # 还在


def test_shadow_single_seq_and_states():
    """遮蔽单条 + surface 三态查询。"""
    log, seqs = build_two_turns()
    log.shadow(seqs["u1"], reason="typo")

    assert log.shadowed_seqs() == {seqs["u1"]}
    states = log.surface_states()
    assert states[seqs["u1"]] == "shadowed"
    assert states[seqs["a1"]] == "current"  # 回复还在(只删了问,留答是调用方选择)
    assert states[seqs["u2"]] == "current"
    # turn/start 是 log-only
    first = next(e for e in log.events if e.type == "turn/start")
    assert states[first.seq] == "log-only"


def test_shadow_persists_roundtrip():
    """遮蔽注解随日志持久化:重载后投影一致。"""
    log, seqs = build_two_turns()
    log.shadow(seqs["u1"], seqs["a1"], reason="deleted")
    lines = log.to_lines()
    rebuilt = SessionLog.from_lines("test", lines)
    assert rebuilt.shadowed_seqs() == {seqs["u1"], seqs["a1"]}
    assert rebuilt.derive_messages() == log.derive_messages()


def test_shadow_combines_with_fold_view():
    """遮蔽与折叠视图正交:两者同时生效不冲突。"""
    log = SessionLog("test")
    log.append("turn/start", turn=1)
    u = log.append("user/message", content=[{"type": "text", "text": "换衣服"}])
    log.append("step/start", turn=1, step=1)
    a = log.append(
        "assistant/message",
        content=[
            {"type": "tool-call", "id": "c1", "name": "change_outfit",
             "arguments": '{"clothes": "红卫衣"}'},
        ],
    )
    log.append("tool/result", tool_call_id="c1",
               content=[{"type": "text", "text": "已换"}])
    log.append("step/end", turn=1, step=1)
    log.append("turn/end", turn=1, reason={"kind": "completed"})

    # 遮蔽整段(用户消息 + 带 tool-call 的 assistant + tool/result),
    # 折叠视图也无从谈起:投影为空(注解本身是 log-only 不进投影)
    tool_result_seq = log.of_type("tool/result")[0].seq
    log.shadow(u.seq, tool_result_seq)
    assert log.derive_messages(fold_tool_traces=True) == []


def test_shadow_partial_leaves_orphan_tool():
    """只遮 user+assistant、漏 tool/result 会被拒绝 —— 配对必须同段
    (对齐 dsh fork 约束:不许切出孤儿 tool 消息,否则投影不合法)。"""
    log = SessionLog("test")
    log.append("turn/start", turn=1)
    u = log.append("user/message", content=[{"type": "text", "text": "换衣服"}])
    log.append("step/start", turn=1, step=1)
    a = log.append(
        "assistant/message",
        content=[
            {"type": "tool-call", "id": "c1", "name": "change_outfit",
             "arguments": '{"clothes": "红卫衣"}'},
        ],
    )
    log.append("tool/result", tool_call_id="c1",
               content=[{"type": "text", "text": "已换"}])
    log.append("step/end", turn=1, step=1)
    log.append("turn/end", turn=1, reason={"kind": "completed"})

    try:
        log.shadow(u.seq, a.seq)  # 漏了 tool/result
        raise AssertionError("应当拒绝切开工配对的遮蔽")
    except ValueError as exc:
        assert "tool-call #c1" in str(exc) and "同段" in str(exc)

    # 遮整段(含 tool/result)则合法
    tool_result_seq = log.of_type("tool/result")[0].seq
    log.shadow(u.seq, tool_result_seq)
    assert log.derive_messages(fold_tool_traces=True) == []


def test_shadow_rejects_future_seq():
    """不能遮蔽还没发生的事件(范围超 last_seq 拒绝)。"""
    log, seqs = build_two_turns()
    try:
        log.shadow(seqs["u1"], seqs["u2"] + 5)
        raise AssertionError("应当拒绝超范围遮蔽")
    except ValueError as exc:
        assert "超出已发生事件" in str(exc)


def test_shadow_tail_cut_semantics():
    """UI 删除语义 = fork 类 tail-cut:从选中消息切到日志末尾,后面全作废。
    这里只验证内核行为:shadow 到末尾后,末尾之后新追加的事件不受影响。"""
    log, seqs = build_two_turns()
    # 模拟用户撤回第二问:遮蔽 u2..末尾(最后一条是 turn/end,无工具配对问题)
    log.shadow(seqs["u2"], log.last_seq(), reason="user-retract")
    msgs = log.derive_messages()
    # 只剩轮 1 的两条
    assert [m["content"][0]["text"] for m in msgs] == ["第一问", "第一答"]
    # 日志原文仍在(可审计)
    assert log.of_type("surface/shadow")[0].data["reason"] == "user-retract"


def test_replace_renders_at_shadow_start():
    """compact 原语:遮蔽中间段 + 摘要渲染在遮蔽起点,后续对话顺序不乱。"""
    log = SessionLog("test")
    # 轮 1(将被压缩)
    log.append("turn/start", turn=1)
    u1 = log.append("user/message", content=[{"type": "text", "text": "今天天气?"}])
    a1 = log.append("assistant/message", content=[{"type": "text", "text": "晴天 28 度"}])
    log.append("turn/end", turn=1, reason={"kind": "completed"})
    # 轮 2(将保留,继续对话)
    log.append("turn/start", turn=2)
    u2 = log.append("user/message", content=[{"type": "text", "text": "那明天呢?"}])
    a2 = log.append("assistant/message", content=[{"type": "text", "text": "明天也晴"}])
    log.append("turn/end", turn=2, reason={"kind": "completed"})

    # 压缩轮 1:遮蔽 u1..a1,摘要渲染在轮 1 位置
    log.replace(
        u1.seq, a1.seq,
        [{"type": "text", "text": "【摘要】用户问过天气,答晴天 28 度。"}],
        reason="compact",
    )
    msgs = log.derive_messages()
    texts = [m["content"][0]["text"] for m in msgs]
    assert texts == [
        "【摘要】用户问过天气,答晴天 28 度。",  # 摘要顶在遮蔽段起点
        "那明天呢?",                            # 轮 2 对话在摘要之后,顺序不乱
        "明天也晴",
    ], f"replace 渲染位置错误: {texts}"
    # 被压缩的原文仍在日志(可审计)
    assert log.of_type("assistant/message")[0].data["content"][0]["text"] == "晴天 28 度"


def test_replace_head_tail_and_multiple():
    """replace 可压头部/可连续多次;shadowed 与替换互不干扰。"""
    log = SessionLog("test")
    log.append("turn/start", turn=1)
    u1 = log.append("user/message", content=[{"type": "text", "text": "q1"}])
    log.append("assistant/message", content=[{"type": "text", "text": "a1"}])
    log.append("turn/end", turn=1, reason={"kind": "completed"})
    log.append("turn/start", turn=2)
    u2 = log.append("user/message", content=[{"type": "text", "text": "q2"}])
    log.append("assistant/message", content=[{"type": "text", "text": "a2"}])
    log.append("turn/end", turn=2, reason={"kind": "completed"})

    # 压两轮 → 两条摘要,都渲染在各自遮蔽段起点,按日志顺序排
    log.replace(u1.seq, log.of_type("assistant/message")[0].seq,
                [{"type": "text", "text": "S1"}])
    log.replace(u2.seq, log.of_type("assistant/message")[1].seq,
                [{"type": "text", "text": "S2"}])
    texts = [m["content"][0]["text"] for m in log.derive_messages()]
    assert texts == ["S1", "S2"], f"多 replace 顺序错误: {texts}"


def test_replace_persists_roundtrip():
    """replace 注解+摘要随日志持久化,重载后渲染一致。"""
    log = SessionLog("test")
    log.append("turn/start", turn=1)
    u1 = log.append("user/message", content=[{"type": "text", "text": "q1"}])
    log.append("assistant/message", content=[{"type": "text", "text": "a1"}])
    log.append("turn/end", turn=1, reason={"kind": "completed"})
    log.replace(u1.seq, log.last_seq(), [{"type": "text", "text": "S"}])
    lines = log.to_lines()
    rebuilt = SessionLog.from_lines("test", lines)
    assert rebuilt.derive_messages() == log.derive_messages()
    assert rebuilt.shadowed_seqs() == log.shadowed_seqs()


def _append_completed_turn(log, n: int) -> None:
    """手造一轮完整已结束轮(user -> assistant 一句)。"""
    log.append("turn/start", turn=n, source="user")
    log.append("user/message", content=[{"type": "text", "text": f"问{n}"}],
               source="user", turn=n)
    log.append("step/start", turn=n, step=1)
    log.append("assistant/message",
               content=[{"type": "text", "text": f"答{n}"}], turn=n, step=1)
    log.append("step/end", turn=n, step=1)
    log.append("turn/end", turn=n, reason={"kind": "completed"})


def _append_completed_self_turn(log, n: int, text: str, at: float) -> None:
    """手造一轮完整已结束自走轮(source=self):占位 user + 一条生活事件。"""
    log.append("turn/start", turn=n, source="self", at=at)
    log.append("user/message",
               content=[{"type": "text", "text": "【自动轮】占位"}],
               source="self", turn=n, at=at)
    log.append("step/start", turn=n, step=1, at=at)
    log.append("assistant/message",
               content=[{"type": "text", "text": text}], turn=n, step=1, at=at)
    log.append("step/end", turn=n, step=1, at=at)
    log.append("turn/end", turn=n, reason={"kind": "completed"}, at=at)


def _append_human_turn(log, n: int, at: float, text: str | None = None) -> None:
    """手造一轮完整已结束的**真人**轮(user -> assistant),时刻可钉。"""
    log.append("turn/start", turn=n, source="user", at=at)
    log.append("user/message",
               content=[{"type": "text", "text": text or f"问{n}"}],
               source="user", turn=n, at=at)
    log.append("step/start", turn=n, step=1, at=at)
    log.append("assistant/message",
               content=[{"type": "text", "text": f"答{n}"}], turn=n, step=1, at=at)
    log.append("step/end", turn=n, step=1, at=at)
    log.append("turn/end", turn=n, reason={"kind": "completed"}, at=at)


def _stamp(at: float) -> str:
    """测试里算期望的时间戳(与内核 _stamp_prefix 的默认格式一致)。"""
    return time.strftime("%m-%d %H:%M", time.localtime(at))


# ---------- 真人消息时间戳(2026-09-17:给历史补时间轴) ----------
# 背景:历史里没有时间轴 —— 每条消息都不带时刻,`[当前时间]` 每轮被覆盖。
# 于是"现在几点"她知道,"距上一条多久"她算不出来。实测症状:22:14 与 23:24
# 那两句(隔 69 分钟)被她读成连续的("刚不是说了嘛,你连着问两遍")。
# 这一组钉住三件事:**只标真人侧**、**只标窗口内的**、**空串 = 行为不变**。

T0 = 1_700_000_000.0


def test_user_time_prefix_marks_human_messages_only():
    """真人消息带时间戳;assistant 与自走占位【都不许】带。

    为什么只标真人侧:模型模仿的是**它自己的输出** —— 生活事件那条标记就是这么
    被抄进正文的(日志里已有 2 条 assistant 正文自带标记)。再给 assistant
    侧加一个可抄模板 = 重蹈覆辙。她的回复与真人消息同轮、时刻几乎相同,
    标了真人就等于把她的也夹住了。
    """
    log = SessionLog("t")
    _append_completed_self_turn(log, 1, "星期天晚上,不想动。", at=T0)
    _append_human_turn(log, 2, at=T0 + 4200)

    msgs = log.derive_messages(life_event_prefix="〔生活事件·{time}〕",
                               user_time_prefix="[{time}]")

    users = [m for m in msgs if m["role"] == "user"]
    assert len(users) == 1, f"自走占位串不该进历史: {users}"
    assert users[0]["content"][0]["text"] == f"[{_stamp(T0 + 4200)}]\n问2", users[0]

    # assistant 侧:生活事件带**前缀**标记(原样),真人轮的回复一个字不加
    assistants = [m for m in msgs if m["role"] == "assistant"]
    assert assistants[0]["content"][0]["text"].startswith("〔生活事件·"), assistants[0]
    assert assistants[1]["content"][0]["text"] == "答2", (
        f"assistant 被标了时间戳 —— 那正是会被抄进正文的形式: {assistants[1]}"
    )


def test_user_time_prefix_placeholder_and_zero_semantics():
    """{time} 换成时刻;不含 {time} 则后面补一个;空串(默认)= 完全不改。"""
    log = SessionLog("t")
    _append_human_turn(log, 1, at=T0)
    stamp = _stamp(T0)

    placed = log.derive_messages(user_time_prefix="〔{time}〕")
    assert placed[0]["content"][0]["text"] == f"〔{stamp}〕\n问1", placed[0]
    # 与 life_event_prefix 同款约定:模板没写 {time} 就自动补一个时间戳
    appended = log.derive_messages(user_time_prefix="〔投递〕")
    assert appended[0]["content"][0]["text"] == f"〔投递〕 {stamp}\n问1", appended[0]
    # 空串 = 原行为(回归保护)
    assert log.derive_messages()[0]["content"][0]["text"] == "问1"
    assert log.derive_messages() == log.derive_messages(user_time_prefix="")


def test_user_time_prefix_only_covers_the_window():
    """标签只跟着**已经在窗口里**的消息:窗口内一致覆盖。

    裁剪发生在打标之前,所以不会出现"同一条历史里有的有、有的没有" ——
    那种不一致是邀请模型去补齐规律。
    """
    log = SessionLog("t")
    for n in (1, 2, 3):
        _append_human_turn(log, n, at=T0 + n * 3600)

    msgs = log.derive_messages(last_turns=1, user_time_prefix="[{time}]")
    users = [m for m in msgs if m["role"] == "user"]
    assert len(users) == 1, users
    assert users[0]["content"][0]["text"].startswith(f"[{_stamp(T0 + 3 * 3600)}]"), users
    # 窗口外的整轮连标签带正文一起不在
    joined = str(msgs)
    assert "问1" not in joined and "问2" not in joined and "问3" in joined

    # 全量窗口:三条真人消息都带,一条不落
    allmsgs = log.derive_messages(user_time_prefix="[{time}]")
    users_all = [m for m in allmsgs if m["role"] == "user"]
    assert len(users_all) == 3
    assert all(m["content"][0]["text"].startswith("[") for m in users_all), users_all


def test_user_time_prefix_labels_first_text_block_only():
    """一条消息只标一次:多块消息不在正文里叠出第二行标记。"""
    log = SessionLog("t")
    log.append("turn/start", turn=1, source="user", at=T0)
    log.append("user/message",
               content=[{"type": "text", "text": "前半"},
                        {"type": "text", "text": "后半"}],
               source="user", turn=1, at=T0)

    blocks = log.derive_messages(user_time_prefix="[{time}]")[0]["content"]
    assert blocks[0]["text"] == f"[{_stamp(T0)}]\n前半", blocks
    assert blocks[1]["text"] == "后半", f"第二条文本块被重复标注: {blocks}"


def test_agent_loop_passes_user_time_prefix_down():
    """装配层给的模板真的到达投影(透传,不是只存了个属性)。"""
    log = SessionLog("t")
    _append_human_turn(log, 1, at=T0)
    registry = ToolRegistry([])

    loop = AgentLoop(log, None, registry, user_time_prefix="〔{time}〕")
    msgs = loop._build_messages(registry, "user", log)
    users = [m for m in msgs if m["role"] == "user"]
    assert users and users[0]["content"][0]["text"] == f"〔{_stamp(T0)}〕\n问1", msgs

    # 不传 = 行为不变(内核默认关)
    bare = AgentLoop(log, None, registry)
    msgs2 = bare._build_messages(registry, "user", log)
    assert [m for m in msgs2 if m["role"] == "user"][0]["content"][0]["text"] == "问1"


def test_life_event_prefix_marks_ended_self_turns_only():
    """自走轮生活事件进上下文:已结束自走轮的 assistant 前拼「前缀 时间戳」行,
    真人轮的消息不加(2026-09:避免孤立 assistant 冒充对用户说的话)。"""
    log = SessionLog("t")
    _append_completed_self_turn(
        log, 1, "星期天晚上,不想动。", at=1_700_000_000.0)
    _append_completed_turn(log, 2)  # 真人轮:问2/答2

    # 不带前缀 = 原行为(生活事件裸文本保留)
    bare = [m for m in log.derive_messages() if m["role"] == "assistant"]
    assert bare[0]["content"][0]["text"] == "星期天晚上,不想动。"

    msgs = log.derive_messages(life_event_prefix="〔生活事件〕")
    self_txt = msgs[0]["content"][0]["text"]
    # 前缀 + 空格 + 时间戳(%m-%d %H:%M)+ 换行 + 正文
    import re
    assert re.match(r"^〔生活事件〕 \d{2}-\d{2} \d{2}:\d{2}\n", self_txt), self_txt
    assert self_txt.endswith("星期天晚上,不想动。"), self_txt
    # 真人轮 assistant 不被加前缀
    user_turn_txt = msgs[2]["content"][0]["text"]
    assert user_turn_txt == "答2", user_turn_txt


def test_life_event_prefix_time_placeholder_and_zero_semantics():
    """{time} 占位换成时间;空前缀(默认)= 完全不改。"""
    log = SessionLog("t")
    _append_completed_self_turn(log, 1, "晚风凉凉的。", at=1_700_000_000.0)
    _append_completed_self_turn(log, 2, "想睡了。", at=1_700_010_000.0)

    # {time} 占位:直接替换,不带多余空格
    msgs = log.derive_messages(life_event_prefix="〔生活事件·{time}〕")
    first = msgs[0]["content"][0]["text"].split("\n")[0]
    assert first.startswith("〔生活事件·"), first
    assert first.endswith("〕"), first
    # 空前缀 = 不改(回归保护)
    assert log.derive_messages() == log.derive_messages(life_event_prefix="")


def test_life_event_prefix_not_doubled_when_she_copied_it():
    """**投影必须幂等**:正文里已经有那行标记时,不许再叠一层。

    背景(2026-09-21 实测的脏数据):模型会**模仿自己的输出**,把投影加的那行
    「前缀 + 时间戳」当正文写了下来 —— 真卡片里有 2 条。不剥的话下一次投影
    拼出**两重**标记,而且内层那个时间戳是**过期的**
    (轮 14:外层 09-12 07:18、内层 09-11 20:41)。
    根因是 role 过载(见 docs/decisions/TIMELINE.md「三、方向探索」);
    这里测的是那道读侧补丁。
    """
    tpl = P.LIFE_EVENT_PREFIX  # "user不在时，角色产生的生活事件：{time}"
    log = SessionLog("t")
    # 她抄进去的那行:时间戳是**过期的**(09-11 20:41),与这一轮的时刻不同
    dirt = "user不在时，角色产生的生活事件：09-11 20:41\n（从外面回来…）"
    _append_completed_self_turn(log, 1, dirt, at=1_700_000_000.0)

    text = log.derive_messages(life_event_prefix=tpl)[0]["content"][0]["text"]
    # 只出现一次(外层是投影加的),正文完整
    assert text.count("user不在时，角色产生的生活事件：") == 1, text
    assert text.endswith("（从外面回来…）"), text
    # 而且内层那个**过期时间戳**不许留在正文里
    assert "09-11 20:41" not in text, text

    # 连抄多次也一次剥干净(她那轮真的叠过两行:09-12 07:18 + 09-14 09:33)
    log2 = SessionLog("t")
    doubled = ("user不在时，角色产生的生活事件：09-12 07:18\n"
               "user不在时，角色产生的生活事件：09-14 09:33\n正文。")
    _append_completed_self_turn(log2, 1, doubled, at=1_700_000_000.0)
    t2 = log2.derive_messages(life_event_prefix=tpl)[0]["content"][0]["text"]
    assert t2.count("user不在时，角色产生的生活事件：") == 1, t2
    assert t2.endswith("正文。"), t2


def test_strip_copied_prefix_only_touches_the_leading_run():
    """**只剥行首** —— 写在中间的不动(那种得人看一眼,别静默改语义)。"""
    tpl = P.LIFE_EVENT_PREFIX
    leading = "user不在时，角色产生的生活事件：09-11 20:41\n正文"
    assert strip_copied_prefix(leading, tpl) == "正文"

    middle = "前面的话。" + leading
    assert strip_copied_prefix(middle, tpl) == middle

    # 模板为空 = 什么都不做(内核默认关)
    assert strip_copied_prefix(leading, "") == leading
    # 不含 {time} 的模板:允许尾部跟一个时间戳
    assert strip_copied_prefix("〔生活事件〕 09-11 20:41\n正文",
                               "〔生活事件〕") == "正文"


def test_last_turns_window_keeps_recent_ended_plus_open_turn():
    """轮窗口:保留最近 N 个已结束轮 + 当前未结束轮;0/None = 全量。"""
    log = SessionLog("t")
    for i in (1, 2, 3):
        _append_completed_turn(log, i)
    # 当前未结束轮(在跑,永不裁)
    log.append("turn/start", turn=4, source="user")
    log.append("user/message", content=[{"type": "text", "text": "问4"}],
               source="user", turn=4)
    log.append("step/start", turn=4, step=1)
    log.append("assistant/message",
               content=[{"type": "text", "text": "答4(还没说完)"}], turn=4, step=1)

    msgs = log.derive_messages(last_turns=2)
    texts = [m["content"][0]["text"] for m in msgs]
    # 最近 2 个已结束轮 = 轮 2、3;轮 1 被裁;当前轮 4 永不裁
    assert texts == ["问2", "答2", "问3", "答3", "问4", "答4(还没说完)"], texts
    # 0 / None = 全量(行为不变)
    assert len(log.derive_messages(last_turns=0)) == 8
    assert len(log.derive_messages()) == 8
    assert len(log.derive_messages(last_turns=10)) == 8  # 窗口比轮数大 -> 全量


def test_last_turns_drops_whole_turns_never_splits_tool_pairs():
    """窗口外的已结束轮整轮消失(含其 tool 配对),窗口内的配对保持完整。"""
    log = SessionLog("t")
    for n in (1, 2):
        log.append("turn/start", turn=n, source="user")
        log.append("user/message", content=[{"type": "text", "text": f"问{n}"}],
                   source="user", turn=n)
        log.append("step/start", turn=n, step=1)
        # 先调工具(工具对在日志里),再答
        log.append("assistant/message",
                   content=[{"type": "tool-call", "id": f"c{n}", "name": "weather",
                             "arguments": "{}"}], turn=n, step=1)
        log.append("tool/call", turn=n, step=1, call_id=f"c{n}",
                   name="weather", arguments="{}")
        log.append("tool/result", turn=n, step=1, tool_call_id=f"c{n}",
                   content=[{"type": "text", "text": f"结果{n}"}], is_error=False)
        log.append("step/end", turn=n, step=1)
        log.append("assistant/message",
                   content=[{"type": "text", "text": f"答{n}"}], turn=n, step=2)
        log.append("step/end", turn=n, step=2)
        log.append("turn/end", turn=n, reason={"kind": "completed"})
    # 当前轮 3(无工具)
    log.append("turn/start", turn=3, source="user")
    log.append("user/message", content=[{"type": "text", "text": "问3"}],
               source="user", turn=3)

    msgs = log.derive_messages(last_turns=1)
    # 只剩轮 2(工具对完整:tool 消息在) + 当前轮 3;轮 1 整轮没了
    assert [m["role"] for m in msgs] == \
        ["user", "assistant", "tool", "assistant", "user"], msgs
    assert any(m["role"] == "tool" for m in msgs)  # 保留轮的配对在
    all_text = str(msgs)
    assert "问1" not in all_text and "答1" not in all_text
    assert "问2" in all_text and "问3" in all_text


if __name__ == "__main__":
    test_derive_messages()
    test_replay_and_recover()
    test_persistence_roundtrip()
    test_derive_skips_empty_assistant()
    test_shadow_hides_from_projection_but_keeps_log()
    test_shadow_single_seq_and_states()
    test_shadow_persists_roundtrip()
    test_shadow_combines_with_fold_view()
    test_shadow_partial_leaves_orphan_tool()
    test_shadow_rejects_future_seq()
    test_shadow_tail_cut_semantics()
    test_replace_renders_at_shadow_start()
    test_replace_head_tail_and_multiple()
    test_replace_persists_roundtrip()
    test_last_turns_window_keeps_recent_ended_plus_open_turn()
    test_last_turns_drops_whole_turns_never_splits_tool_pairs()
    test_user_time_prefix_marks_human_messages_only()
    test_user_time_prefix_placeholder_and_zero_semantics()
    test_user_time_prefix_only_covers_the_window()
    test_user_time_prefix_labels_first_text_block_only()
    test_agent_loop_passes_user_time_prefix_down()
    test_life_event_prefix_marks_ended_self_turns_only()
    test_life_event_prefix_time_placeholder_and_zero_semantics()
    test_life_event_prefix_not_doubled_when_she_copied_it()
    test_strip_copied_prefix_only_touches_the_leading_run()
    print("SessionLog all tests passed")
