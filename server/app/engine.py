"""Yona 应用层 · 组合根与生活运行时(engine)

布局 B(2026-09 用户拍板):本模块 = 组合根 —— 把支撑库(store/rhythm)、
内核(core)、角色(character)装配成"这个进程唯一的那一份",并管
start()/stop() 生命周期。同时收着"生活运行时":心跳启动、离线补写装配
(规则类 ServerGate 已拆去 gate.py;LifeLoop/补写链与引擎接线紧,暂留)。

router(server/app/api/*)通过 `from .. import engine` 在**运行时**取
`engine.xxx`,不在 import 时解包(因为 lifespan 启动后才赋值)。
"""

from __future__ import annotations

import os
import threading
import time
from collections import deque
from pathlib import Path

from .llm_setup import load_runtime

from character.persona import build_small_night_composer
from character import personas as personas_mod  # noqa: E402 文案(内容层);装配时现取属性,
# 不用 from-import 绑死 —— 改文案后 reload 模块 + 重建引擎即生效(2026-09)
from character.state import CharacterState
from character.tools import make_change_outfit_tool
from core.composer import SystemSection, make_timeline_section  # noqa: E402
from core.heartbeat import Heartbeat
from core.loop import AgentLoop
from core.openai_compat import OpenAICompatibleLLM
from core.session_log import SessionLog
from core.tools import ToolRegistry

from ..rhythm import LifeSampler
from ..store import SessionStore
from .gate import ServerGate

# 产品语义参数唯一来源 = server/params.py(带拍板状态;查看: py server/params.py)
from ..params import (  # noqa: E402
    BACKFILL_START_DELAY_SEC,
    DEFAULT_CONTEXT_ROUNDS,
    HEARTBEAT_COOLDOWN_SEC,
    HEARTBEAT_INTERVAL_SEC,
    HEARTBEAT_MAX_INTERVAL,
    HEARTBEAT_MIN_INTERVAL,
    HEARTBEAT_STARTUP_DELAY,
    HOT_COOLDOWN_SEC,
    HOT_INTERVAL_SEC,
    HOT_WAKES_PER_DAY,
    LLM_DEFAULT_TEMPERATURE,
    LLM_OUTPUT_MAX_TOKENS,
    SELF_WAKES_PER_DAY,
    WAKE_AFTER_GAP_SECONDS,
)

# ---------- 路径 ----------
ROOT = Path(__file__).resolve().parent.parent.parent
# 数据目录:默认 data/;YONA_DATA_DIR 可指到独立目录(验证/演示不脏真实数据)。
DATA_DIR = Path(os.environ.get("YONA_DATA_DIR", str(ROOT / "data")))

# 2026-09 每卡 life:不再有匿名全局 "_life" 生活流 —— 生活属于卡片本身,
# 写进"最近激活的那张卡"的 chat.log(source=self);常驻保底旗舰卡 = Yona
# (store.flagship_session_id,删了自动重建,先归档再重置)。target 解析见
# engine.life_session_id() → store.life_target_session_id()。

# (离线补写触发阈值 WAKE_AFTER_GAP_SECONDS 已收进 server/params.py)

# ---------- 全局单例(lifespan 启动后才赋值;router 在运行时访问) ----------
# (人格文案已归位 character/personas.py —— 她是谁在内容层,不在 server;
#  engine 只做装配:文案 → composer → sys_by_source builder。)
_state = CharacterState({"clothes": "白衬衫", "pants": "牛仔裤"})
_tools = ToolRegistry([make_change_outfit_tool(_state)])

_store: SessionStore | None = None
_loop: AgentLoop | None = None
_heartbeat: Heartbeat | None = None
_life_gate: "ServerGate | None" = None  # 补写/心跳跑完也 mark_self,节奏衔接
# 回放轮世界时钟:补写轮跑之前设为 slot 起点,backfill_composer 的 world section
# 每 step 现取它 —— 模型看到的时间 = 历史时刻,墙钟不混入。仅在回放轮内被设。
_backfill_clock: dict[str, float] = {"ts": 0.0}
# 实验台/演示"当前时间"覆盖(prompt_lab 注入间隔/时刻用,2026-09):ts>0 时,
# 陪聊/自走 composer 的世界 section 报它,而非墙钟 —— 仍是单时间源,只是源被拨过。
# 产品路径从不设它(恒 0 = 真实墙钟);与 _backfill_clock 分开:回放轮的世界钟
# 跟历史游标走(补写),普通轮的"现在"才读这里。
_clock_override: dict[str, float] = {"ts": 0.0}
# 普通轮时间预算(2026-09 用户拍板修正:自走/心跳/脉冲 = 与补写**同一事件算法**,
# 只是触发点不同 —— 普通轮的预算不是浮空抽数,而是对可消费区间
# [日志尾 = 最后一次交互的时刻, 触发本轮的当前现实时间] 跑同一条 LifeSampler
# 判定:命中 → 本轮事件(start + 预算,LifeSampler 内建 start+预算 ≤ 当前时刻,
# 超出即截断 —— LLM 看到的 [时间预算] = 截完的预算);区间内没触发事件 → 0
# (段不出现,本轮安静结束,无事可叙)。min>0 时 self composer 的 [时间预算]
# 段报它;触发方跑完要清回 0(end_self_wake)。
_wake_budget: dict[str, float] = {"min": 0.0}
# 命中事件的 start(2026-09 用户拍板范围修正):begin_self_wake 把窗口里命中
# 那件事件的 start 记在这里,**暴露给要锚定叙述视图的调用方(实验台 prompt_lab:
# 强制/命中轮把 [当前时间] 拨到事件 start、时间线从它派生)**。产品自走/心跳/
# 脉冲路径不用它 —— 它们照旧在触发时刻叙述,不改产品输出(锚定试验暂只留
# lab)。0 = 无事件/未激活;end_self_wake 一起清。
_wake_anchor: dict[str, float] = {"start": 0.0}
_lock = threading.Lock()  # 全局引擎锁(loop 内部已有 turn 锁,这里护 store 落盘)
# LLM 连接状态(2026-09 任务③ 连接管理):运行时配置的进程内影子 ——
# 谁连的(base_url)/默认模型/该端点可用模型列表。key 只进 _build_engine,不出 HTTP。
_llm_cfg: dict | None = None
_model: str | None = None
_models: list[str] = []

# LLM 调用调试日志(llm-log):环形缓冲,记每次 LLM 调用的输入/输出。
# 记录点 = 装配时的 _TracedLLM 包装(engine 层做,core 不动);
# 两种消费:
#   * /admin/llm-log          —— 一次性快照(UI 打开面板时拉历史)
#   * /admin/llm-log/stream   —— SSE 推送(每次新调用实时广播,无调用零流量)
# 推送 = 订阅者列表:每个活动 SSE 连接 = (event loop, asyncio.Queue)。
# LLM 调用跑在任意线程(心跳/聊天/补写),_log 用 call_soon_threadsafe
# 把新条目投回事件循环(与 chat SSE 同款线程→循环桥)。
_llm_log: deque[dict[str, str]] = deque(maxlen=100)
_llm_log_subs: list = []  # [(loop, asyncio.Queue), ...] 活动订阅
_llm_log_subs_lock = threading.Lock()


def llm_log_snapshot() -> list[dict[str, str]]:
    """llm-log 端点数据:最近 LLM 调用记录(旧 → 新,UI 顺序渲染)。"""
    return list(_llm_log)


def subscribe_llm_log(loop, queue) -> None:
    """SSE 端点注册订阅:新条目会被投到这个队列。"""
    with _llm_log_subs_lock:
        _llm_log_subs.append((loop, queue))


def unsubscribe_llm_log(loop, queue) -> None:
    """SSE 端点断开时注销。"""
    with _llm_log_subs_lock:
        _llm_log_subs[:] = [
            (l, q) for l, q in _llm_log_subs if not (l is loop and q is queue)
        ]


def _llm_log_append(d: str, m: str, c: str) -> None:
    """记一条 + 广播给所有活动订阅(线程安全,失败静默)。"""
    entry = {"t": time.strftime("%H:%M:%S"), "d": d, "m": m, "c": c}
    _llm_log.append(entry)
    with _llm_log_subs_lock:
        subs = list(_llm_log_subs)
    for loop, queue in subs:
        try:
            loop.call_soon_threadsafe(queue.put_nowait, entry)
        except RuntimeError:
            pass  # 事件循环已关(进程退出边缘)


def _clip_for_log(text: str, limit: int = 4000) -> str:
    """日志内容截断:调试面板看得见关键即可,不扛全量历史。"""
    return text if len(text) <= limit else text[:limit] + "\n…(截断)"


def _log_messages_text(messages: list[dict]) -> str:
    """messages(内部格式)→ 调试可读文本:每条 [role] 内容,超长截断。"""
    lines: list[str] = []
    for msg in messages:
        role = msg.get("role", "?")
        content = msg.get("content")
        if isinstance(content, str):
            txt = content
        elif isinstance(content, list):
            parts: list[str] = []
            for b in content:
                if not isinstance(b, dict):
                    continue
                t = b.get("type")
                if t == "text":
                    parts.append(b.get("text", ""))
                elif t == "tool-call":
                    parts.append(
                        f"[tool-call {b.get('name', '')} {b.get('arguments', '')}]"
                    )
            txt = "\n".join(parts)
        else:
            txt = str(content)
        if len(txt) > 1200:
            txt = txt[:1200] + "…"
        lines.append(f"[{role}] {txt}")
    return _clip_for_log("\n\n".join(lines))


def _usage_suffix(out) -> str:
    """invoke 分支的调试后缀:usage(若有)+ finish(截断标红)。"""
    suffix = ""
    u = getattr(out, "usage", None)
    f = getattr(out, "finish_reason", None)
    if u:
        suffix = (f" · {u.get('input_tokens', 0)} in / "
                  f"{u.get('output_tokens', 0)} out")
    if f == "length":
        suffix += " / length 截断"
    elif f and not u:
        suffix += f" · finish={f}"
    return suffix


class _TracingLLM:
    """LLM 包装:每次真实调用记一条调试日志进 _llm_log(llm-log 面板)。

    只在装配层做(engine 包一层),core/loop 完全无感 —— 聊天/自走/补写
    都走同一个 loop 实例,所以这里就是所有真实 LLM 调用的唯一闸口,
    输入输出都在这个闸口被记录,不再需要别处埋点。
    """

    def __init__(self, inner) -> None:
        self._inner = inner
        self.model = getattr(inner, "model", "llm")

    def _log(self, d: str, m: str, c: str) -> None:
        _llm_log_append(d, m, c)

    def invoke(
        self, messages, tools=None, temperature=None, max_tokens=None, model=None
    ):
        chosen = model or self.model
        self._log("→", chosen, _log_messages_text(messages))
        t0 = time.time()
        try:
            out = self._inner.invoke(
                messages, tools,
                temperature=temperature, max_tokens=max_tokens, model=model,
            )
        except Exception as exc:  # noqa: BLE001
            self._log("⚠", f"{chosen} 出错", f"{type(exc).__name__}: {exc}")
            raise
        parts: list[str] = []
        if out.text:
            parts.append(out.text)
        for tc in out.tool_calls:
            parts.append(f"[tool-call] {tc.name} {tc.arguments}")
        self._log(
            "←",
            f"{chosen} · {(time.time() - t0) * 1000:.0f}ms"
            f"{_usage_suffix(out)}",
            _clip_for_log("\n".join(parts)) if parts else "(空输出)",
        )
        return out

    def stream(
        self, messages, tools=None, temperature=None, max_tokens=None, model=None
    ):
        chosen = model or self.model
        self._log("→", chosen, _log_messages_text(messages))
        t0 = time.time()

        def gen():
            texts: list[str] = []
            # 工具调用按 index 聚合(与 Assembler 同一路由):一个调用 =
            # 一个 id/name 首段 + 若干 arguments 增量段 —— 逐段列会把
            # 一次 change_outfit 刷成二十行;聚合后每调用一行完整参数。
            tools_map: dict[int, dict] = {}
            tools_order: list[int] = []
            usage: dict | None = None
            finish: str | None = None
            try:
                for chunk in self._inner.stream(
                    messages, tools,
                    temperature=temperature, max_tokens=max_tokens, model=model,
                ):
                    kind = chunk.get("kind")
                    if kind == "text":
                        texts.append(chunk.get("text", ""))
                    elif kind == "tool_call":
                        idx = chunk.get("index", 0)
                        if idx not in tools_map:
                            tools_map[idx] = {"name": "", "args": []}
                            tools_order.append(idx)
                        if chunk.get("name"):
                            tools_map[idx]["name"] = chunk["name"]
                        if chunk.get("arguments_delta"):
                            tools_map[idx]["args"].append(chunk["arguments_delta"])
                    elif kind == "finish":
                        finish = chunk.get("reason")
                    elif kind == "usage":
                        usage = chunk.get("usage")
                    yield chunk
            except Exception as exc:  # noqa: BLE001
                self._log("⚠", f"{chosen} 出错", f"{type(exc).__name__}: {exc}")
                raise
            body = "".join(texts)
            if tools_order:
                parts = []
                for idx in tools_order:
                    t = tools_map[idx]
                    args = "".join(t["args"])
                    parts.append(f"{t['name']} {args}".strip())
                body += "\n[tool_calls] " + "; ".join(parts)
            suffix = ""
            if usage:
                suffix = (f" · {usage.get('input_tokens', 0)} in / "
                          f"{usage.get('output_tokens', 0)} out"
                          f"{' / length 截断' if finish == 'length' else ''}")
            elif finish:
                suffix = f" · finish={finish}"
            self._log(
                "←",
                f"{chosen} · {(time.time() - t0) * 1000:.0f}ms{suffix}",
                _clip_for_log(body) if body else "(空输出)",
            )

        return gen()


class LifeLoop:
    """把 Heartbeat 的自走轮转发到全局 loop,写给"最近激活的那张卡"。

    2026-09 每卡 life:心跳醒来 = 那张卡醒着 —— 目标卡 = 最近有人聊过的
    会话(store.life_target_session_id,Yona 兜底),自走事件写进它自己的
    chat.log(source=self,聊天视图看不见);若该卡快照里有人格覆盖串,
    自走轮也吃它的人格(卡片独处 = 卡片本人),否则旗舰。
    """

    def __init__(self, gate: ServerGate):
        self.gate = gate

    def run_turn(self, source="user", tools=None, self_note=None, **kw):
        t0 = time.time()
        tag = "情境自走" if self_note else "自走"
        sid = life_session_id()
        _live(f"她开始{tag}(卡片 {sid})(source={source})…")
        # 普通自走轮(心跳/脉冲)= 与补写同一个事件算法,只是触发点不同 →
        # 触发时对 [日志尾, 当前时刻] 跑同一条 LifeSampler 出时间预算
        # (2026-09 拍板;细节见 begin_self_wake)。回放轮另有自己的预算 note
        # 且走 cursor,不在这里重复抽。
        is_self = source == "self"
        try:
            log = _store.load_log(sid)
            if is_self:
                if begin_self_wake(log) <= 0:
                    # 2026-09 拍板落地:窗口 [日志尾, 当前] 无事件 → 该轮不触发
                    # 任何事件,**安静结束** —— 不调 LLM(不产无预算碎碎念)、
                    # 不动日志、不进冷却(心跳从同一锚继续等下一件事件)。
                    tail = _log_tail_epoch(log)
                    now = _clock_override["ts"] or time.time()
                    f_tail = (time.strftime("%m-%d %H:%M", time.localtime(tail))
                              if tail else "—")
                    _live(f"{tag}安静结束:窗口 {f_tail}→"
                          f"{time.strftime('%m-%d %H:%M', time.localtime(now))}"
                          " 无事件,不调 LLM")
                    return None
            with _lock:
                # 该卡快照的人格覆盖(若有)在自走轮同样生效
                snap = _store.get_session_settings(sid)
                result = _loop.run_turn(
                    source=source, tools=tools, log=log, self_note=self_note,
                    system_prompt=(snap.get("system_prompt")
                                   if snap.get("system_prompt") else None),
                )
                _store.save_log(sid, log)
            self.gate.mark_self()  # 真跑了一轮 → 进入冷却
            _live(f"{tag}完成,耗时 {time.time() - t0:.1f}s")
            return result
        except Exception as exc:  # noqa: BLE001
            _live(f"{tag}失败: {exc}")
            raise
        finally:
            if is_self:
                end_self_wake()


# ---------- 打印台 / 小工具 ----------

def _live(msg: str) -> None:
    """打印台实时现场:带时间戳的一行(她自走/忙/排队,肉眼可跟)。"""
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _log_tail_epoch(log) -> float | None:
    """日志尾时间(最后一次交互/事件的时刻);空日志 = None。"""
    if log is None or not log.events:
        return None
    return log.events[-1].time


def begin_self_wake(log=None, rng=None) -> float:
    """普通自走轮(心跳/脉冲/自走,非回放)开跑前:对本轮**可消费区间**采样一次。

    2026-09 用户拍板(修正 612ae32"只是浮空抽一个数"的错):普通轮与补写轮是
    **同一个事件算法**,只是触发点不同(此刻醒来 vs 离线回放)。本轮预算 =
    同一条 LifeSampler 判定,语义:

      - **可消费区间** = [最后一次交互/事件的时刻(日志尾), 触发本轮的
        "当前现实时间"(当前时刻)] —— 她离上次互动到现在,这段时间能消费什么;
      - 在这区间上跑 LifeSampler(逐格点命中 → 事件 start + 预算);
      - **兜底**:start + 预算 ≤ 当前时刻;LifeSampler 内建 end=min(...) 已截断,
        即预算超了会被截成 当前时刻−start(她不能"还没到点就做了超出的事");
      - 命中 → 本轮预算 = 该事件的 budget(截断后,LLM 只看到减后的结果);
      - 没命中 → 预算 0 = 该轮不触发任何事件 → **安静结束(不调 LLM)**
        (2026-09 拍板落地:无事件轮不许再硬跑一轮无预算的碎碎念)。

    **调用方契约**:返回本轮预算(分钟)—— 0 = 该轮不触发任何事件,安静结束:
    不调模型、不写日志、不进冷却(心跳从同一锚继续等下一件);>0 = 有事件可叙,
    跑这一轮。命中事件是否/如何**锚到事件起点叙述**([当前时间] = start、时间线
    从 start 派生、自语落事件结束)由**调用方**(实验台)决定 —— 本函数只把
    该事件的 start 记进 `_wake_anchor`,不改产品自走/心跳的输出(产品路径照旧
    在触发时刻叙述;2026-09 用户拍板:这套事件锚定暂只留在 prompt_lab 试验)。

    rng 可注入(单测固定复现);引擎默认随机。
    """
    _wake_budget["min"] = 0.0
    _wake_anchor["start"] = 0.0
    tail = _log_tail_epoch(log)
    if tail is None:
        return 0.0  # 全无历史:没有"最后一次交互",没有可消费区间,无事可叙
    now = _clock_override["ts"] or time.time()
    if now <= tail:
        return 0.0  # 时间没往前走(同刻/回拨):区间 ≤ 0,该轮不触发任何事件
    events = LifeSampler(tail, now, rng=rng).sample()
    if not events:
        return 0.0  # 该轮不触发任何事件 → 结束(无事可叙,[时间预算] 不出现)
    # 取窗口里最后一件(距 now 最近、正在做/刚做完的那件);LifeSampler 已把
    # 每件 end 截到 ≤ now,start+预算 ≤ 当前时刻 内建成立 —— LLM 看到的 = 截完的预算。
    last = events[-1]
    _wake_budget["min"] = last.budget_min
    _wake_anchor["start"] = last.start  # 暴露给要锚定叙述视图的调用方(实验台)
    return _wake_budget["min"]


def end_self_wake() -> None:
    """跑完清掉预算 + 事件锚(0 = 未激活,self composer 的 [时间预算] 段不出现)。"""
    _wake_budget["min"] = 0.0
    _wake_anchor["start"] = 0.0


def _human_gap(seconds: float) -> str:
    """离线时长 → 人话("3 小时 20 分" / "1 天 2 小时")。"""
    total_min = max(1, int(seconds // 60))
    if total_min < 60:
        return f"{total_min} 分钟"
    hours, mins = divmod(total_min, 60)
    if hours < 24:
        return f"{hours} 小时" + (f" {mins} 分" if mins else "")
    days, hours = divmod(hours, 24)
    return f"{days} 天" + (f" {hours} 小时" if hours else "")


# ---------- 装配 ----------

def _build_engine(cfg: dict | None = None) -> None:
    """建引擎:LLM 客户端 + 一份常驻人设 + 三种情境 composer + 主 AgentLoop。

    2026-09 人设常驻:三套 composer 共享同一份 PERSONA,只换情境段
    (陪聊/自走/补写是同一个她,不是三个人 —— 见 character/personas.py)。
    2026-09 任务③ 连接管理:cfg = 运行时连接配置(UI 向导落盘的
    data/llm.local.json,含 base_url/api_key/model/models)。缺配置/无效 →
    抛 RuntimeError,引擎保持禁用(聊天 503,UI 首启引导)。**.env 不再是
    产品配置**(config.py 只留给脚本/探针);连接的唯一入口是 UI,见
    llm_setup.py。
    """
    global _loop, _llm_cfg, _model, _models
    if cfg is None:
        cfg = load_runtime(DATA_DIR)
    if not cfg or not cfg.get("api_key") or not cfg.get("base_url"):
        _loop, _llm_cfg, _model, _models = None, None, None, []
        raise RuntimeError("未配置 LLM 连接:请在 UI 完成「连接你的模型」")
    api_key = cfg["api_key"]
    base_url = cfg["base_url"]
    models = [m for m in (cfg.get("models") or []) if isinstance(m, str)]
    model = (cfg.get("model") or "").strip() or (models[0] if models else "")
    if model and model not in models:
        models = [model] + models
    _llm_cfg, _model, _models = cfg, model, models
    llm = _TracingLLM(
        OpenAICompatibleLLM(
            api_key=api_key,
            base_url=base_url,
            model=model,
            # 产品默认(2026-09 拍板):温度 0.9、输出上限 4096 固定。
            # 温度可被 UI 每轮覆盖;输出上限客户端改不了(见 server/params.py)。
            temperature=LLM_DEFAULT_TEMPERATURE,
            max_tokens=LLM_OUTPUT_MAX_TOKENS,
        )
    )
    # 一份 PERSONA 常驻,三种轮只换"情境段"(2026-09 拍板修正:
    # 人设 ≠ 轮的属性 —— 陪聊/自走/补写是同一个她,不是三个人)。
    # 文案现取 personas_mod.*:改文案后 reload + 重建引擎即生效。
    # 世界时间源(2026-09 实验台/演示):普通轮(陪聊/自走)读 _clock_override
    # (实验台可拨"当前时间";0 = 真实墙钟,产品路径不设),补写轮读回放游标。
    def _live_clock():
        ts = _clock_override["ts"]
        return time.localtime(ts) if ts else time.localtime()

    def _live_epoch(log=None):
        # 时间线的"现在":与 _live_clock 同一只钟(秒级)。回放轮 = 历史游标;
        # 普通轮 = 实验台拨过的当前时刻,否则真实墙钟。
        if log is not None and log.time_cursor is not None and _backfill_clock["ts"]:
            return _backfill_clock["ts"]
        return _clock_override["ts"] or time.time()

    # VISION 决策 8:世界=绝对时间,时间线=相对时间(距上次真人互动多久)。
    # 段在构造时不闭包 log —— compose 时经 values["log"]/["now_epoch"] 现给
    # (同一只钟,与 world 不打架);没跟真人说过话时该段自然不出现。
    timeline_section = make_timeline_section()

    def _wake_budget_text(values) -> str | None:
        """普通轮时间预算(2026-09 用户拍板:自走/心跳/脉冲与补写同一事件算法,
        只是触发点不同 —— 命中事件的自走轮也产预算,告诉模型"这段时间约 X,
        只做一件事")。min=0 = 未激活,本段不出现(产品不设 = 没这回事)。
        **句子文案在 personas.WAKE_BUDGET_TEMPLATE(内容层归位)**,这里只算
        时长填进 {gap} —— 想改"怎么说"去 personas 改一处,引擎不写文案。
        producer 每次 compose 现取 personas_mod 属性(lab reload 文案即生效)。"""
        m = _wake_budget["min"]
        if m <= 0:
            return None
        gap = _human_gap(m * 60)
        return "[时间预算] " + personas_mod.WAKE_BUDGET_TEMPLATE.format(gap=gap)

    wake_budget_section = SystemSection(
        name="wake_budget", priority=17, producer=_wake_budget_text)

    # 陪聊轮 = 主人正在跟她说话:只给世界时刻([当前时间]),**不挂 [时间线]**
    # —— 那是独处轮(自走/补写)看的:她一个人待着才需要"距上次和主人说话
    # 多久"。正在聊天时组这条是噪音(2026-09 用户指出修正)。
    chat_composer = build_small_night_composer(
        personas_mod.PERSONA, _state, _tools,
        situation=personas_mod.CHAT_SITUATION,
        world_now=_live_clock)
    self_composer = build_small_night_composer(
        personas_mod.PERSONA, _state, _tools,
        situation=personas_mod.SELF_SITUATION,
        world_now=_live_clock,
        extra_sections=[timeline_section, wake_budget_section])
    # 补写回放轮 = 自走轮的离线回放(2026-09 用户拍板合一):与 self_composer
    # **同一份情境文案(SELF_SITUATION)+ 同一组段**(timeline / wake_budget),
    # 差异只剩机制:世界时间 = 回放游标(历史时刻),不是墙钟/当前覆盖。
    # 不再有独立的 BACKFILL_SITUATION 文案(曾写两份独处轮措辞,必漂移)。
    backfill_composer = build_small_night_composer(
        personas_mod.PERSONA, _state, _tools,
        situation=personas_mod.SELF_SITUATION,
        world_now=lambda: time.localtime(_backfill_clock["ts"]),
        extra_sections=[timeline_section, wake_budget_section],
    )
    # 暴露给调试/实验台只读查询(不改产品路径:compose 仍在 sys_by_source 里做)
    _composers["chat"] = chat_composer
    _composers["self"] = self_composer
    _composers["backfill"] = backfill_composer

    def _values(registry, log=None) -> dict:
        """compose 值:人设插值 + 本轮工具 + (时间线用)日志与同一只钟。"""
        return {**personas_mod.VALUES, "registry": registry,
                "log": log, "now_epoch": _live_epoch(log)}

    def sys_by_source(registry, source, log=None):
        # 三参 builder:回放轮 = 时间游标 **且** 补写钟在转(engine 产品补写/lab 补写
        # 模拟都同时设两者)—— 用补写视图(世界时间=历史游标)。实验台"拨当前时间"
        # 的普通轮只设 log 游标(给事件盖时间戳),_backfill_clock 恒 0 → 不算回放,
        # 走陪聊/自走实时视图(世界时间=_clock_override)。(2026-09 判定收窄)
        if log is not None and log.time_cursor is not None and _backfill_clock["ts"]:
            return backfill_composer.compose(_values(registry, log))
        composer = self_composer if source == "self" else chat_composer
        return composer.compose(_values(registry, log))

    _loop = AgentLoop(
        SessionLog("_boot"),
        llm,
        _tools,
        system_prompt=sys_by_source,
        max_steps=8,
        fold_tool_traces=False,
        # 自走轮自语进上下文的前缀(内容层文案;见 personas.SELF_TALK_PREFIX)
        self_talk_prefix=personas_mod.SELF_TALK_PREFIX,
    )


def _session_log(session_id: str) -> SessionLog:
    """按会话 id 取日志(不存在会由 store 建空)。"""
    return _store.load_log(session_id)


# (调试/实验台)引擎真实装配的 composer,供只读组件查询(_build_engine 里填)
_composers: dict[str, object] = {}


def system_component_sections(
    source: str, log=None,
) -> list[tuple[str, str]]:
    """SYSTEM 组件拆分(只读,debug/实验台用):与 sys_by_source 同一 composer。

    与喂模型的 compose 是**同一批段**(真实装配的段,不是实验台另拼),
    只是按段标名渲染出来 —— 看 persona/situation/world/state/tool_usages
    各是谁、边界在哪。渲染仍是段自身的 render(values),不写日志。
    """
    # 与 sys_by_source 同一判定(2026-09 收窄):回放轮 = 游标 **且** 补写钟在转;
    # 实验台"拨当前时间"的普通轮(只设游标)按 source 显示陪聊/自走视图。
    mode = "backfill" if (log is not None and log.time_cursor is not None
                          and _backfill_clock["ts"]) \
        else ("self" if source == "self" else "chat")
    composer = _composers.get(mode)
    if composer is None:
        return []
    # 时间线段的 log/now 用同一只钟(与 world 不打架):普通轮 = 当前覆盖/墙钟,
    # 回放轮 = 补写游标 —— 和 sys_by_source 的 _values 一致。
    log_now = _backfill_clock["ts"] if (log is not None
                                         and log.time_cursor is not None
                                         and _backfill_clock["ts"]) \
        else (_clock_override["ts"] or time.time())
    values = {**personas_mod.VALUES, "registry": _tools,
              "log": log, "now_epoch": log_now}
    return [
        (s.name, s.render(values))
        for s in composer.sections()
        if s.render(values) and s.render(values).strip()
    ]


def _start_heartbeat() -> None:
    """启动自动心跳(进程活着她就活着)。"""
    global _heartbeat, _life_gate
    # 演示模式(YONA_GATE_HOT=1):判定更密、冷却更短、标度更大 —— 想看她动
    # 时不用等概率;数值见 server/params.py(HOT_* / HEARTBEAT_* / SELF_WAKES)。
    hot = os.environ.get("YONA_GATE_HOT") == "1"
    gate = ServerGate(
        cooldown=HOT_COOLDOWN_SEC if hot else HEARTBEAT_COOLDOWN_SEC,
        base_interval=HOT_INTERVAL_SEC if hot else HEARTBEAT_INTERVAL_SEC,
        wakes_per_day=HOT_WAKES_PER_DAY if hot else SELF_WAKES_PER_DAY,
    )
    life_loop = LifeLoop(gate)
    _life_gate = gate

    def _on_hb_error(exc: Exception) -> None:
        import traceback
        err_file = DATA_DIR / "heartbeat_error.log"
        err_file.parent.mkdir(parents=True, exist_ok=True)
        with open(err_file, "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%H:%M:%S')}] {exc}\n")
            f.write(traceback.format_exc())
            f.write("\n")
        print(f"[heartbeat error] {exc}")

    _heartbeat = Heartbeat(
        life_loop, gate,
        startup_delay=HEARTBEAT_STARTUP_DELAY,
        min_interval=HEARTBEAT_MIN_INTERVAL,
        max_interval=HEARTBEAT_MAX_INTERVAL,
        on_error=_on_hb_error,
    )
    _heartbeat.start()
    print("[heartbeat] 已启动 —— 她会按自己的生活节奏醒来")


# ---------- 离线生活补写检测(启动时) ----------

def _wake_decision(
    last_active: float | None, now: float | None = None
) -> tuple[bool, str, float]:
    """生活补写触发判定(纯函数,now 可注入 —— 测试/演示固定时刻)。

    返回 (是否触发, 原因说明, gap 秒)。last_active=None = 全无历史(真正首启)。
    """
    now = time.time() if now is None else now
    if last_active is None:
        return False, "首启:没有任何历史,无从补", 0.0
    gap = now - last_active
    if gap < WAKE_AFTER_GAP_SECONDS:
        return (
            False,
            f"离线 {_human_gap(gap)} < 阈值 {_human_gap(WAKE_AFTER_GAP_SECONDS)},"
            "算正常重启,不补写",
            gap,
        )
    return (
        True,
        f"离线 {_human_gap(gap)} ≥ 阈值 {_human_gap(WAKE_AFTER_GAP_SECONDS)},"
        "安排离线生活补写",
        gap,
    )


def life_session_id() -> str:
    """自走/补写/脉冲写给哪张卡 = 最近激活的卡,没有 = Yona(常驻旗舰)。

    2026-09 每卡 life:生活不再属于匿名全局流,而属于"正在和你说话的那张卡"。
    """
    if _store is None:
        return ""
    return _store.life_target_session_id()


def _maybe_backfill_life() -> None:
    """离线生活补写:目标卡离线超过阈值 → 把它离线期间的生活补进它自己。

    收编进主 loop:补写轮 = 普通自走轮(写目标卡的 chat.log),差异只在
    "log 设了时间游标" —— 事件时间戳落历史时刻,世界时间(backfill_composer)
    报历史时刻,人格切到补写视图。**没有第二个 AgentLoop**:同一张卡、
    同一个 loop,只是时间在跳。锚点 = 该卡日志尾部(卡与你的对话也是它活着的
    证据);补写 = 目标卡醒来补日子,其它卡等下次被激活。
    """
    sid = ""
    last_active: float | None = None
    try:
        sid = life_session_id()
        log = _store.load_log(sid)
        last_active = log.events[-1].time if log.events else None
        trigger, reason, gap = _wake_decision(last_active)
        print(f"[backfill] 卡片 {sid}: {reason}")
        if not trigger:
            return
    except Exception as exc:  # noqa: BLE001
        print(f"[backfill] 检查失败: {exc}")
        return

    def _run() -> None:
        time.sleep(BACKFILL_START_DELAY_SEC)  # 等服务完全起来;延迟见 params.py
        try:
            now = time.time()
            sampler = LifeSampler(last_active, now)
            events = sampler.sample()
            if not events:
                print("[backfill] 采样器无事件(离线太短 / 落在稀疏时段 /"
                      "恰好没判定中),跳过")
                return
            empty_tools = ToolRegistry([])  # 补写是"那段日子怎么过的",不该有实时工具
            with _lock:
                card_log = _store.load_log(sid)
                snap = _store.get_session_settings(sid)
                persona = snap.get("system_prompt") if snap.get("system_prompt") else None
                for i, e in enumerate(events):
                    # 预算限制:budget(约 X 分钟)= "做一件做得完的事"的上限,
                    # 不是事件属性,不进日志,只当轮可见。
                    # 相对时间:距上一件事(名义上在 prev.start+prev.budget 结束)
                    # 的空档,让模型知道中间过了多久(不然它以为"刚才还在做上一件")。
                    # 事件只有起始时间,没有终止/消费时长(用户定的)。
                    if i == 0:
                        gap_note = ""
                    else:
                        prev = events[i - 1]
                        gap = e.start - (prev.start + prev.budget_min * 60)
                        if gap > 60:
                            gap_note = (
                                f"\n距离你上一件事做完已经过了约 {_human_gap(gap)}"
                                "(中间的时间平平淡淡,没发生值得记的事)。"
                            )
                        else:
                            gap_note = "\n你上一件事刚做完不久。"
                    # note 只留"动态"部分(本段多长/隔了多久)——静态部分
                    # (独处轮怎么说)在 personas.SELF_SITUATION(补写复用同一份,
                    # 2026-09 用户拍板合一,曾另写 BACKFILL_SITUATION,已删)。
                    note = (
                        f"这段时间(约 {_human_gap(e.budget_min * 60)})里你只做了"
                        "**一件事**——就是现在刚做完/正在做的这一件。"
                        f"{gap_note}"
                    )
                    card_log.set_time_cursor(e.start)
                    _backfill_clock["ts"] = e.start
                    try:
                        _loop.run_turn(
                            source="self", log=card_log, tools=empty_tools,
                            self_note=note, system_prompt=persona,
                        )
                    finally:
                        card_log.clear_time_cursor()
                        _backfill_clock["ts"] = 0.0
                    _live(
                        f"补写 {time.strftime('%m-%d %H:%M', time.localtime(e.start))} …"
                    )
                _store.save_log(sid, card_log)
            if _life_gate is not None:
                _life_gate.mark_self()  # 补写过 → 心跳进入冷却,节奏衔接
            f0 = time.strftime("%m-%d %H:%M", time.localtime(events[0].start))
            f1 = time.strftime("%m-%d %H:%M", time.localtime(events[-1].start))
            print(f"[backfill] 补写完成 {len(events)} 个事件({f0} → {f1})")
        except Exception as exc:  # noqa: BLE001
            print(f"[backfill] 补写失败: {exc}")

    threading.Thread(target=_run, daemon=True, name="yona-backfill").start()


# ---------- 连接管理(2026-09 任务③;UI 是唯一入口,免重启进程) ----------

def llm_state() -> dict:
    """给 HTTP 的公开连接状态(不含 api_key)。未配置 = configured False。"""
    from . import llm_setup
    s = llm_setup.sanitize(_llm_cfg)
    if s.get("configured") and not s.get("model"):
        s["model"] = _model or (s["models"][0] if s["models"] else "")
    return s


def resolve_model(candidate: str | None) -> str | None:
    """UI 想用哪个模型 → 只认当前端点可用列表内的;否则回默认模型。"""
    if candidate and candidate in _models:
        return candidate
    return _model


def merge_turn_settings(
    snapshot: dict,
    requested: dict | None = None,
    *,
    default_temperature: float = LLM_DEFAULT_TEMPERATURE,
    default_rounds: int = DEFAULT_CONTEXT_ROUNDS,
    default_model: str | None = None,
    available_models: tuple = (),
) -> dict:
    """设置合并链(纯函数,2026-09 任务6 路线 B):当轮 > 会话快照 > 默认。

    - requested 里**没给**(None)的字段才依次落快照/默认;显式给的永远优先。
    - max_rounds 保留 0(=不限制)语义:0 不是"没给",直接透传。
    - system_prompt:None = 旗舰默认;快照里存了覆盖串才覆盖。
    - model:不在 available_models(当前连接可用列表) → 回 default_model
      (连接换端点/模型下线时防呆,不会拿个幽灵模型去调)。
    """
    req = requested or {}

    def pick(key, default):
        v = req.get(key)
        if v is None:
            v = snapshot.get(key)
        return default if v is None else v

    model = pick("model", default_model)
    if available_models and model not in available_models:
        model = default_model
    return {
        "temperature": float(pick("temperature", default_temperature)),
        "max_rounds": pick("max_rounds", default_rounds),
        "system_prompt": pick("system_prompt", None),
        "model": model,
    }


def resolve_turn_settings(
    session_id: str, requested: dict | None = None
) -> dict:
    """服务端缺省补齐(路线 B):读会话快照 → merge_turn_settings。

    **只用于聊天 user 轮**。心跳/补写/脉冲(每卡 life)不进这里 —— 它们由
    LifeLoop/backfill 自己读目标卡快照的人格覆盖串(见上),其余字段用默认。
    """
    snap = _store.get_session_settings(session_id) if _store is not None else {}
    return merge_turn_settings(
        snap, requested or {},
        default_model=_model, available_models=tuple(_models),
    )


def reconfigure_llm(cfg: dict) -> None:
    """保存新连接并热重配(UI 向导调,免重启):锁内停心跳 → 重建引擎 → 重启心跳。

    调用前必须已用新 key 拉通模型列表(见 llm_setup.fetch_models),
    cfg 含 base_url/api_key/model/models。失败抛错,连接保持原样。
    """
    global _heartbeat, _loop
    with _lock:
        if _heartbeat is not None:
            _heartbeat.stop()
            _heartbeat = None
        try:
            _build_engine(cfg)
        except Exception:
            # 建引擎失败:回滚到磁盘上的旧连接(有则),绝不留半活引擎
            try:
                _build_engine(load_runtime(DATA_DIR))
            except Exception:
                _loop = None
            raise
        _start_heartbeat()
        _live(f"LLM 连接已更新: {cfg.get('base_url')} · 默认模型 {_model}")


# ---------- 生命周期入口(lifespan 调用) ----------

def start() -> None:
    """服务启动:建存储 → 读运行时连接 → 有则建引擎/起心跳/补写检测。

    2026-09 任务③ 连接管理:无运行时配置(UI 没连过) = 引擎禁用
    (聊天 503,心跳不起),UI 显示首启引导 —— .env 不再是产品配置。
    """
    global _store
    _store = SessionStore(DATA_DIR)  # 建目录制存储(含旧布局一次性迁移)
    cfg = load_runtime(DATA_DIR)
    if not cfg:
        print("[llm] 未配置 LLM 连接 —— 引擎禁用,等待 UI 首启引导(/admin/llm-config)")
        return
    try:
        _build_engine(cfg)
    except RuntimeError as exc:
        print(f"[warn] {exc} —— 聊天/心跳禁用,静态 UI 仍可浏览")
        return
    _start_heartbeat()
    _maybe_backfill_life()


def stop() -> None:
    """服务停止:停心跳(线程 daemon,主要是收尾干净)。"""
    if _heartbeat is not None:
        _heartbeat.stop()
