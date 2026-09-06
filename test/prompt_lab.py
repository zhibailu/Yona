"""小夜子 · 提示词实验台(prompt_lab) —— 真模型,看实际输入/输出

用法:  py test/prompt_lab.py            交互式(聊天/自走/补写/预览)
       py test/prompt_lab.py --preview   只打印当前输入预览(不调模型,零花费)

它做什么:
- **完全复用引擎的装配链**(character 层 composer + core AgentLoop + 真 LLM),
  不启动 server、不碰内核、不写 data/ —— 会话日志只在进程内,退出即弃。
- **每次轮次前自动重新加载 character/personas.py**:你在编辑器里改
  PERSONA / 情境 / SELF_TALK_PREFIX,回到本实验台下一轮立即生效,不用重启。
- 每次真实调用前打印 **组件拆分的 SYSTEM**(persona / situation / world /
  state / tool usage 各段分开)+ 完整 messages 输入;调用后流式打印输出。
- **自走轮手动触发**:按键即"心跳此刻醒来";回车 = 无情境(引擎纯心跳
  占位),输入一句话 = 情境自走。
- **虚拟"当前时间"**:`v` 可把世界时间拨到任意时刻(如深夜 23:30),看她
  在"那个时刻"怎么独处 —— 不必等真实时钟走到那个点。
- **补写窗口模拟**:`b` 选"她最后活跃时刻 → 补写到此刻",用引擎同款
  LifeSampler 采样离线事件,逐件以历史时刻回放 —— 日志里落下**带历史
  时间戳的自语**;之后跑 1 陪聊,就能看"孤立 assistant 被打前缀+时间戳"。
- **LLM 报错不炸台**:网络/传输错误捕获后报一句,回菜单。

菜单:
  1 陪聊轮    2 自走轮    3 输入预览    b 补写模拟
  v 设虚拟时间  t 自语前缀  m 换模型    w 上下文窗口
  c 清空历史   q 退出
"""

from __future__ import annotations

import importlib
import random
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:  # Windows 控制台直接跑时输出中文不乱码
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# ---------- 装配材料(与 server/app/engine.py 同款,但不 import 引擎本体) ----------

from character import persona as persona_factory  # noqa: E402
from character import personas as personas_mod     # noqa: E402  会被 reload
from character.state import CharacterState          # noqa: E402
from character.tools import make_change_outfit_tool  # noqa: E402
from core.loop import AgentLoop                      # noqa: E402
from core.openai_compat import OpenAICompatibleLLM   # noqa: E402
from core.session_log import SessionLog              # noqa: E402
from core.tools import ToolRegistry                  # noqa: E402
from server.app import llm_setup                     # noqa: E402
from server.params import LLM_DEFAULT_TEMPERATURE, LLM_OUTPUT_MAX_TOKENS  # noqa: E402
from server.rhythm import LifeSampler                # noqa: E402

# ---------- 运行时连接(与引擎同一来源:data/llm.local.json,UI 写入的) ----------

DATA_DIR = ROOT / "data"
_cfg = llm_setup.load_runtime(DATA_DIR)
if not _cfg or not _cfg.get("api_key") or not _cfg.get("base_url"):
    print("[llm] 未找到运行时连接(data/llm.local.json):请先在 UI 完成「连接你的模型」。")
    sys.exit(1)
_API_KEY = _cfg["api_key"]
_BASE_URL = _cfg["base_url"]
_MODELS = [m for m in (_cfg.get("models") or []) if isinstance(m, str)] or \
    [m for m in [_cfg.get("model")] if m]
_DEFAULT_MODEL = (_cfg.get("model") or "").strip() or (_MODELS[0] if _MODELS else "")

# ---------- 实验室状态(全在进程内,不落盘) ----------

_state = CharacterState({"clothes": "白衬衫", "pants": "牛仔裤"})
_tools = ToolRegistry([make_change_outfit_tool(_state)])
_log = SessionLog("prompt-lab")          # 本实验台的"会话卡"(Yona 替身)
_model = _DEFAULT_MODEL
_max_rounds: int | None = None           # None=全量;引擎产品默认见 params.DEFAULT_CONTEXT_ROUNDS
_prefix_mode = "file"                    # file | off | 〔自语〕 | 〔自语·{time}〕
_virtual_now: float | None = None        # 虚拟"当前时间";None = 真实时钟
_backfill_clock: dict[str, float] = {"ts": 0.0}   # 补写轮世界时间(回放游标)

# 三个 composer 由每次 reload 后的文案重建(见 _reload_personas)
_composers: dict[str, object] = {}


def _now() -> float:
    """世界时钟:虚拟时间(若有)否则真实。"""
    return _virtual_now if _virtual_now is not None else time.time()


def _reload_personas() -> None:
    """重新加载 character/personas.py,并用新文案重建 composer。

    这样改 PERSONA/情境/SELF_TALK_PREFIX 不用重启实验台,下一轮即生效。
    world 段的时间源 = _now()(虚拟时间可拨),补写轮 = 回放游标。
    """
    importlib.reload(personas_mod)
    p = personas_mod
    _composers.clear()
    for mode, sit in (("chat", p.CHAT_SITUATION),
                      ("self", p.SELF_SITUATION),
                      ("backfill", p.BACKFILL_SITUATION)):
        world = (lambda: time.localtime(_backfill_clock["ts"])) if mode == "backfill" \
            else (lambda: time.localtime(_now()))
        _composers[mode] = persona_factory.build_small_night_composer(
            p.PERSONA, _state, _tools, situation=sit, world_now=world,
        )


def _effective_prefix() -> str:
    """自语前缀:file 档 = personas.py 里用户写的;演示档 = 内置三种。"""
    p = personas_mod
    if _prefix_mode == "file":
        return p.SELF_TALK_PREFIX
    if _prefix_mode == "off":
        return ""
    if _prefix_mode == "〔自语〕":
        return "〔自语〕"
    if _prefix_mode == "〔自语·{time}〕":
        return "〔自语·{time}〕"
    return p.SELF_TALK_PREFIX


def _fmt_ts(ts: float) -> str:
    return time.strftime("%m-%d %H:%M", time.localtime(ts))


def _human_gap(seconds: float) -> str:
    """时长 → 人话(与 engine._human_gap 同款)。"""
    total_min = max(1, int(seconds // 60))
    if total_min < 60:
        return f"{total_min} 分钟"
    hours, mins = divmod(total_min, 60)
    if hours < 24:
        return f"{hours} 小时" + (f" {mins} 分" if mins else "")
    days, hours = divmod(hours, 24)
    return f"{days} 天" + (f" {hours} 小时" if hours else "")


def _parse_ts(text: str) -> float | None:
    """解析时间输入:空=现在;HH:MM=今天;MM-DD HH:MM=今年;YYYY-MM-DD HH:MM。"""
    text = text.strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M", "%m-%d %H:%M", "%H:%M"):
        try:
            dt = datetime.strptime(text, fmt)
            now = datetime.fromtimestamp(_now())
            if fmt == "%H:%M":
                dt = dt.replace(year=now.year, month=now.month, day=now.day)
            elif fmt == "%m-%d %H:%M":
                dt = dt.replace(year=now.year)
            return dt.timestamp()
        except ValueError:
            continue
    print("时间格式没看懂,示例: 23:30 / 09-06 23:30 / 2026-09-06 23:30")
    return None


# ---------- SYSTEM builder(与引擎同款:按 source/时间游标选 composer) ----------

def sys_by_source(registry, source, log=None):
    mode = "backfill" if (log is not None and log.time_cursor is not None) \
        else ("self" if source == "self" else "chat")
    composer = _composers[mode]
    return composer.compose({**personas_mod.VALUES, "registry": registry})


def _composer_sections(source: str, log=None) -> list[tuple[str, str]]:
    """组件拆分视图:composer 每段单独渲染,标段名 —— 看"哪些是一起的"。

    注意:这只是**展示**;实际发给模型的 system = compose() 拼好的整条。
    """
    mode = "backfill" if (log is not None and log.time_cursor is not None) \
        else ("self" if source == "self" else "chat")
    composer = _composers[mode]
    values = {**personas_mod.VALUES, "registry": _tools}
    return [(s.name, s.render(values)) for s in composer.sections()
            if s.render(values) and s.render(values).strip()]


def _blocks_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            b.get("text", "") for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        )
    return ""


def _build_messages(source: str, log=None) -> list[dict]:
    """复刻 AgentLoop._build_messages:system + 历史投影(带自语前缀)。"""
    log = log if log is not None else _log
    messages: list[dict] = []
    sys_text = sys_by_source(_tools, source, log)
    if sys_text:
        messages.append({"role": "system", "content": sys_text})
    messages.extend(
        log.derive_messages(
            fold_tool_traces=False,
            retained_tools=set(),
            last_turns=_max_rounds or None,
            self_talk_prefix=_effective_prefix(),
        )
    )
    return messages


def print_messages(messages: list[dict], source: str = "user", log=None) -> None:
    """打印实际输入:system 组件拆分 + 每条消息(role/纯文本)。"""
    log = log if log is not None else _log
    if messages and messages[0]["role"] == "system":
        print("── SYSTEM(组件拆分) ──")
        for name, text in _composer_sections(source, log):
            print(f"  ◆ {name}: {text}")
        print("── messages(实际发送) ──")
    for m in messages:
        txt = _blocks_text(m.get("content")) or ""
        txt = txt if len(txt) <= 600 else txt[:600] + "…"
        print(f"[{m.get('role')}] {txt}")


def _stream_cb(chunk: dict) -> None:
    kind = chunk.get("kind")
    if kind == "text":
        print(chunk.get("text", ""), end="", flush=True)
    elif kind == "reasoning":
        pass
    elif kind == "tool_call":
        print(f"\n  ⚙ [tool] {chunk.get('name', '')}", flush=True)


def _current_slot(source: str, user_input: str | None, self_note: str | None) -> dict:
    """当前轮 user 槽消息(展示用):user=你说的话;self=情境 note 或占位。"""
    if source == "user":
        return {"role": "user", "content": user_input or ""}
    if self_note:
        return {"role": "user", "content": self_note}
    return {"role": "user",
            "content": "【自动轮】此为自我触发的轮次,没有用户自主消息(内核占位串,"
                       "可改 personas 文案或给它输情境来替代)。"}


def _run_turn(source: str, user_input: str | None = None, self_note: str | None = None,
              log=None) -> None:
    """跑一轮真模型:先打印输入,再流式输出;LLM 报错只报一句不炸台。"""
    log = log if log is not None else _log
    tag = {"self": "自走轮", "user": "陪聊轮"}.get(source, source)
    clock = _fmt_ts(_backfill_clock["ts"]) if log.time_cursor is not None \
        else _fmt_ts(_now())
    print(f"\n===== {tag} · {_model} · 当前 {clock} · "
          f"前缀={_effective_prefix() or '(空)'} =====")
    preview = _build_messages(source, log) + [_current_slot(source, user_input, self_note)]
    print_messages(preview, source, log)
    print("────── 输出 ──────")
    loop = AgentLoop(
        SessionLog("_boot"),
        _llm,
        _tools,
        system_prompt=sys_by_source,
        max_steps=8,
        self_talk_prefix=_effective_prefix(),
    )
    try:
        result = loop.run_turn(
            user_input=user_input, source=source, log=log,
            self_note=self_note, on_chunk=_stream_cb, model=_model,
            max_rounds=_max_rounds,
        )
        print()
        usage = None
        for e in reversed(log.events):
            if e.type == "assistant/message" and e.data.get("turn") == result.turn:
                usage = e.data.get("usage")
                break
        u = usage or {}
        print(f"──── 完成: {result.reason.get('kind')} · "
              f"{u.get('input_tokens', '?')} in / {u.get('output_tokens', '?')} out "
              f"(回合 {result.turn}, {result.steps} 步) ────")
    except KeyboardInterrupt:
        print("\n(中断)")
    except Exception as exc:  # noqa: BLE001  网络/传输/限流都在这 —— 不炸台
        print(f"\n⚠ LLM 调用失败(回菜单可继续): {exc}")


def _run_backfill() -> None:
    """补写窗口模拟:选 最后活跃 → 补写到此刻,采样离线事件逐件回放。

    与引擎 _maybe_backfill_life 同款:LifeSampler 采样 → 每件 set_time_cursor
    (事件时间戳落历史时刻)+ 空工具 + note,写进本实验台的会话日志。
    跑完回菜单按 1 陪聊,历史里即见"带前缀+历史时间戳的自语"。
    """
    print("\n── 补写窗口模拟(她离线期间怎么过的)──")
    last_text = input("她最后活跃时刻(回车=当前时间往前 10 小时; 或 昨天 20:00): ").strip()
    end_text = input("补写到此刻(回车=当前; 可先按 v 拨虚拟时间): ").strip()
    end_ts = _parse_ts(end_text) or _now()
    start_ts = _parse_ts(last_text) if last_text else end_ts - 10 * 3600
    if start_ts >= end_ts:
        print("开始须早于结束。")
        return
    print(f"离线窗口: {_fmt_ts(start_ts)} → {_fmt_ts(end_ts)}"
          f"(共 {_human_gap(end_ts - start_ts)})")

    # 采样(与引擎同款;换个 seed 就能换一批生活)
    seed = random.randrange(2**31)
    events = LifeSampler(start_ts, end_ts, seed=seed).sample()
    if not events:
        print(f"采样器无事件(seed {seed})—— 窗口太短/落在稀疏时段/恰好没判定中。"
              "换个窗口或再试一次。")
        return
    print(f"采样到 {len(events)} 件事(seed {seed}):")
    for e in events:
        print(f"  · {_fmt_ts(e.start)} 起,约 {e.budget_min:.0f} 分钟")
    if input("开始逐件回放?(回车继续 / q 取消): ").strip().lower() == "q":
        return

    empty_tools = ToolRegistry([])  # 补写是"那段日子怎么过的",不该有实时工具
    for i, e in enumerate(events):
        if i == 0:
            gap_note = ""
        else:
            prev = events[i - 1]
            gap = e.start - (prev.start + prev.budget_min * 60)
            gap_note = (
                f"\n距离你上一件事做完已经过了约 {_human_gap(gap)}"
                "(中间的时间平平淡淡,没发生值得记的事)。"
                if gap > 60 else "\n你上一件事刚做完不久。"
            )
        note = (
            f"这段时间(约 {_human_gap(e.budget_min * 60)})里你只做了"
            "**一件事**——就是现在刚做完/正在做的这一件。"
            f"{gap_note}"
        )
        _log.set_time_cursor(e.start)
        _backfill_clock["ts"] = e.start
        try:
            _run_turn("self", self_note=note, log=_log)
        finally:
            _log.clear_time_cursor()
            _backfill_clock["ts"] = 0.0
    print(f"\n补写完成 {len(events)} 件。按 1 陪聊轮 → 历史里可看到这些自语"
          "带前缀+离线时间戳(前缀为空时按 t 先开演示前缀)。")


# ---------- LLM 客户端 ----------

_llm = OpenAICompatibleLLM(
    api_key=_API_KEY, base_url=_BASE_URL, model=_DEFAULT_MODEL,
    temperature=LLM_DEFAULT_TEMPERATURE, max_tokens=LLM_OUTPUT_MAX_TOKENS,
)


# ---------- 交互循环 ----------

def _menu() -> None:
    clock = _fmt_ts(_now()) if _virtual_now is not None else "真实时钟"
    print("\n" + "─" * 60)
    print(f"模型 {_model} · 窗口 {_max_rounds or '全量'} · 自语前缀 "
          f"{_effective_prefix() or '(空)'} · 当前 {clock}")
    print("1 陪聊轮   2 自走轮   3 输入预览   b 补写模拟")
    print("v 设虚拟时间  t 前缀  m 模型  w 窗口  c 清空历史  q 退出")


def _prefix_hint() -> None:
    if not _effective_prefix():
        print("提示: 自语前缀当前为空(SELF_TALK_PREFIX 未填/未选演示档),"
              "打标不显示。按 t 开演示前缀,或去 character/personas.py 填。")


def main() -> None:
    global _log, _model, _max_rounds, _prefix_mode, _virtual_now
    if "--preview" in sys.argv:
        _reload_personas()
        print("=== 输入预览(不调模型)· user 轮(你说:在吗) ===")
        print_messages(_build_messages("user", _log), "user", _log)
        _prefix_mode = "〔自语·{time}〕"
        _virtual_now = _parse_ts("23:30")
        print("\n=== 同上· self 轮(虚拟 23:30,前缀 〔自语·{time}〕,情境:睡不着) ===")
        preview = _build_messages("self", _log) + \
            [_current_slot("self", None, "这个点你睡不着,一个人待着。")]
        print_messages(preview, "self", _log)
        return

    _reload_personas()
    print("小夜子提示词实验台 · 真模型 · 改 personas.py 后下一轮自动生效")
    while True:
        _menu()
        try:
            choice = input("> ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\n再见")
            return
        if choice in ("q", "exit", "quit"):
            print("再见")
            return
        elif choice == "1":
            _reload_personas()
            msg = input("你对她说: ").strip()
            if not msg:
                continue
            _run_turn("user", user_input=msg)
        elif choice == "2":
            _reload_personas()
            note = input("自走情境(回车=纯心跳无情境; 输入=情境自走): ").strip()
            _run_turn("self", self_note=note or None)
            _prefix_hint()
        elif choice == "b":
            _reload_personas()
            _run_backfill()
        elif choice == "3":
            _reload_personas()
            src = input("预览哪轮(user/self): ").strip().lower()
            src = "self" if src == "self" else "user"
            print_messages(_build_messages(src, _log), src, _log)
        elif choice == "v":
            text = input("虚拟当前时间(回车=恢复真实时钟; 例 23:30 / 09-06 23:30 / "
                         "2026-09-06 23:30): ").strip()
            _virtual_now = _parse_ts(text)
            print(f"当前时间 → {_fmt_ts(_now()) if _virtual_now is not None else '真实时钟'}")
        elif choice == "t":
            modes = ["file", "off", "〔自语〕", "〔自语·{time}〕"]
            _prefix_mode = modes[(modes.index(_prefix_mode) + 1) % len(modes)]
            print(f"自语前缀 → {_prefix_mode} ({_effective_prefix() or '(空)'})")
        elif choice == "m":
            if _MODELS:
                _model = _MODELS[(_MODELS.index(_model) + 1) % len(_MODELS)] \
                    if _model in _MODELS else _MODELS[0]
            print(f"模型 → {_model}")
        elif choice == "w":
            _max_rounds = {None: 3, 3: 8, 8: None}[_max_rounds]
            print(f"上下文窗口 → {_max_rounds or '全量'}")
        elif choice == "c":
            _log = SessionLog("prompt-lab")  # 重开一段(进程内,不落盘)
            print("会话历史已清空")
        else:
            print("?")


if __name__ == "__main__":
    main()
