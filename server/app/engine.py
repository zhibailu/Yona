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
from character.personas import (  # noqa: E402  人格文案(内容层),见文件头
    BACKFILL_PERSONA,
    CHAT_PERSONA,
    SELF_PERSONA,
    VALUES,
)
from character.state import CharacterState
from character.tools import make_change_outfit_tool
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
IMG_DIR = DATA_DIR / "images"

# "生活"会话:她的自走轮(独处自语/动作)统一落这里,不进任何聊天会话。
# 与"会话日志"并列的另一条流 —— 她活着是全局的,不隶属于某次聊天。
LIFE_SESSION_ID = "_life"

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
            calls: list[str] = []
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
                        calls.append(
                            f"{chunk.get('name', '')} "
                            f"{chunk.get('arguments_delta', '')}"
                        )
                    elif kind == "finish":
                        finish = chunk.get("reason")
                    elif kind == "usage":
                        usage = chunk.get("usage")
                    yield chunk
            except Exception as exc:  # noqa: BLE001
                self._log("⚠", f"{chosen} 出错", f"{type(exc).__name__}: {exc}")
                raise
            body = "".join(texts)
            if calls:
                body += "\n[tool_calls] " + "; ".join(calls)
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
    """把 Heartbeat 的自走轮转发到全局 loop,并指定生活日志(_life)。

    Heartbeat 只认 `run_turn(source, tools, **kw)` 形状;这里补上 log 指向,
    让自动心跳与手动脉冲写同一条生活流(workspace/agent-feed 都从它派生)。
    """

    def __init__(self, gate: ServerGate):
        self.gate = gate

    def run_turn(self, source="user", tools=None, self_note=None, **kw):
        t0 = time.time()
        tag = "情境自走" if self_note else "自走"
        _live(f"她开始{tag}(source={source})…")
        try:
            log = _store.load_log(LIFE_SESSION_ID)
            with _lock:
                result = _loop.run_turn(
                    source=source, tools=tools, log=log, self_note=self_note
                )
                _store.save_log(LIFE_SESSION_ID, log)
            self.gate.mark_self()  # 真跑了一轮 → 进入冷却
            _live(f"{tag}完成,耗时 {time.time() - t0:.1f}s")
            return result
        except Exception as exc:  # noqa: BLE001
            _live(f"{tag}失败: {exc}")
            raise


# ---------- 打印台 / 小工具 ----------

def _live(msg: str) -> None:
    """打印台实时现场:带时间戳的一行(她自走/忙/排队,肉眼可跟)。"""
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


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
    """建引擎:LLM 客户端 + 三套人格 composer + 主 AgentLoop(全局唯一)。

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
    self_composer = build_small_night_composer(SELF_PERSONA, _state, _tools)
    chat_composer = build_small_night_composer(CHAT_PERSONA, _state, _tools)
    # 补写回放轮:世界时间 = log 的时间游标(历史时刻),不是墙钟
    backfill_composer = build_small_night_composer(
        BACKFILL_PERSONA, _state, _tools,
        world_now=lambda: time.localtime(_backfill_clock["ts"]),
    )

    def sys_by_source(registry, source, log=None):
        # 三参 builder:回放轮(log 设了时间游标)用补写视图 —— 世界时间=游标,
        # 人格=正在过普通一天的她;普通轮按 source 用实时视图(墙钟)。
        if log is not None and log.time_cursor is not None:
            return backfill_composer.compose({**VALUES, "registry": registry})
        composer = self_composer if source == "self" else chat_composer
        return composer.compose({**VALUES, "registry": registry})

    _loop = AgentLoop(
        SessionLog("_boot"),
        llm,
        _tools,
        system_prompt=sys_by_source,
        max_steps=8,
        fold_tool_traces=False,
    )


def _session_log(session_id: str) -> SessionLog:
    """按会话 id 取日志(不存在会由 store 建空)。"""
    return _store.load_log(session_id)


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


def _last_active_anywhere() -> float | None:
    """她"上次有意识" = 所有日志(用户会话 + 生活)的最后活动时刻。

    生活补写锚点**不能只看 _life**:用户会话里跟主人的对话也是她活着的证据
    (她这个人跨会话一致,记忆不按"哪条流"分割)。进程死后没有任何流再长,
    所以取全部日志里最后一条事件的时间,就是她离线前最后的意识。
    """
    last: float | None = None
    ids = [s["id"] for s in _store.list_sessions()] + [LIFE_SESSION_ID]
    for lid in ids:
        log = _store.load_log(lid)
        if log.events and (last is None or log.events[-1].time > last):
            last = log.events[-1].time
    return last


def _maybe_backfill_life() -> None:
    """离线生活补写:离上次活跃超过阈值再启动 → 把离线期间的生活补进 _life。

    收编进主 loop:补写轮 = 普通自走轮(log=_life),差异只在"log 设了时间游标"
    —— 事件时间戳落历史时刻,世界时间(backfill_composer)报历史时刻,人格切到
    补写视图。**没有第二个 AgentLoop**:同一个人、同一个 loop,只是时间在跳。
    每段之间日志历史累积 → 逐段叙事连贯自发涌现。
    """
    try:
        last_active = _last_active_anywhere()
        trigger, reason, gap = _wake_decision(last_active)
        print(f"[backfill] {reason}")
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
                life_log = _store.load_log(LIFE_SESSION_ID)
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
                    note = (
                        "此刻没有人在跟你说话,你一个人过着平常的一天。\n"
                        f"这段时间(约 {_human_gap(e.budget_min * 60)})里你只做了"
                        "**一件事**——就是现在刚做完/正在做的这一件。"
                        "时间由系统给你,别自己报时间;不要写别的时间段的事。"
                        f"{gap_note}"
                    )
                    life_log.set_time_cursor(e.start)
                    _backfill_clock["ts"] = e.start
                    try:
                        _loop.run_turn(
                            source="self", log=life_log, tools=empty_tools,
                            self_note=note,
                        )
                    finally:
                        life_log.clear_time_cursor()
                        _backfill_clock["ts"] = 0.0
                    _live(
                        f"补写 {time.strftime('%m-%d %H:%M', time.localtime(e.start))} …"
                    )
                _store.save_log(LIFE_SESSION_ID, life_log)
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

    心跳/补写轮不经过这里(她们写 _life,无会话、永不套快照 —— 自走人格
    永远是旗舰默认)。
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
    IMG_DIR.mkdir(parents=True, exist_ok=True)
    _store = SessionStore(DATA_DIR)
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
