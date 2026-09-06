"""小夜子 · 提示词实验台(prompt_lab) —— 真模型,驱动**真实引擎装配**

用法:  py test/prompt_lab.py            交互式(聊天/自走/补写/预览)
       py test/prompt_lab.py --preview   只打印当前输入预览(不调模型,零花费)

它做什么:
- **从实际生效的代码取数**(2026-09 修正):不再平行复刻 composer/llm/tools。
  直接 import `server.app.engine`(eng),每次轮次前:
    1. reload `character/personas`(改文案立即生效)
    2. `eng._build_engine(cfg)` —— 用引擎同一装配函数重建 composer/loop/llm
    3. 之后只用 `eng._loop.run_turn(...)` / `eng.system_component_sections(...)`
  实验台看到的 system、历史、前缀、llm 调用 = 引擎真实路径,llm-log 也照记。
- **会话不落盘**:聊天日志用进程内 SessionLog(退出即弃,不写 data/)。
- 每次真实调用前打印 **SYSTEM 组件拆分**(persona/situation/world/state/
  tool_usages,来自引擎真实 composer)+ 完整 messages;调用后流式打印输出。
- **自走轮手动触发**:按键即"心跳此刻醒来";回车 = 无情境(引擎纯心跳
  占位),输入一句话 = 情境自走。时间 = 真实墙钟(引擎单时间源,VISION 决策 8)。
- **补写窗口模拟**(b):选 她最后活跃 → 补写到此刻,LifeSampler 采样,逐件
  `set_time_cursor` 以历史时刻回放(引擎同款 note/空工具/时间游标),日志里
  落下**带历史时间戳的自语**;之后 1 陪聊即见"孤立 assistant 被打前缀+时间戳"。
- **自语前缀**(t):file 档 = personas.SELF_TALK_PREFIX 你写的值(引擎装配读
  它);演示档只临时改 `eng._loop.self_talk_prefix`,方便对比,不改文件。
- LLM 报错不炸台:捕获后报一句回菜单。

菜单:
  1 陪聊轮    2 自走轮    3 输入预览    b 补写模拟
  t 自语前缀  m 换模型    w 上下文窗口  c 清空历史  q 退出
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

# ---------- 真实引擎(装配/composer/loop/llm 全从这里来) ----------

from server.app import engine as eng          # noqa: E402  真实装配目标
from server.app import llm_setup              # noqa: E402
from server.params import LLM_DEFAULT_TEMPERATURE  # noqa: E402(仅展示用)
from server.rhythm import LifeSampler         # noqa: E402
from core.session_log import SessionLog       # noqa: E402

# personas 模块对象:reload 它,引擎装配时现取属性(engine 已改为 personas_mod.*)
import character.personas as personas_mod      # noqa: E402


# ---------- 运行时连接(与引擎同一来源:data/llm.local.json) ----------

DATA_DIR = ROOT / "data"
_cfg = llm_setup.load_runtime(DATA_DIR)
if not _cfg or not _cfg.get("api_key") or not _cfg.get("base_url"):
    print("[llm] 未找到运行时连接(data/llm.local.json):请先在 UI 完成「连接你的模型」。")
    sys.exit(1)


# ---------- 实验室状态(全在进程内,不落盘) ----------

_log = SessionLog("prompt-lab")   # 本实验台的"会话卡";loop/工具/人设全用引擎的
_model = (_cfg.get("model") or "").strip() or ""
_max_rounds: int | None = None    # None=全量
_prefix_mode = "file"             # file | off | 〔自语〕 | 〔自语·{time}〕(演示)

_MODELS = [m for m in (_cfg.get("models") or []) if isinstance(m, str)] or [_model]


def _rebuild() -> None:
    """reload 文案 → 用引擎真实装配函数重建 composer/loop/llm。

    引擎 `eng._build_engine` 内部现取 personas_mod.* 属性,所以 reload 后
    重建即读到新文案 —— 这正是"从实际生效代码取数"。
    """
    importlib.reload(personas_mod)
    eng._build_engine(_cfg)  # 装配进 eng._loop / eng._composers / eng._llm
    if _prefix_mode != "file":
        # 演示档:临时覆盖真实 loop 的前缀(不改文件,对比用)
        eng._loop.self_talk_prefix = _effective_prefix()


def _effective_prefix() -> str:
    """自语前缀:file 档 = personas.py 里你写的值(引擎同源);演示档 = 内置。"""
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
    now = time.time()
    for fmt in ("%Y-%m-%d %H:%M", "%m-%d %H:%M", "%H:%M"):
        try:
            dt = datetime.strptime(text, fmt)
            now_dt = datetime.fromtimestamp(now)
            if fmt == "%H:%M":
                dt = dt.replace(year=now_dt.year, month=now_dt.month, day=now_dt.day)
            elif fmt == "%m-%d %H:%M":
                dt = dt.replace(year=now_dt.year)
            return dt.timestamp()
        except ValueError:
            continue
    print("时间格式没看懂,示例: 23:30 / 09-06 23:30 / 2026-09-06 23:30")
    return None


# ---------- 展示(组件来自引擎真实 composer) ----------

def _blocks_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            b.get("text", "") for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        )
    return ""


def _print_system(source: str, log=None) -> None:
    """SYSTEM 组件拆分 = 引擎真实 composer 的段(eng.system_component_sections)。"""
    log = log if log is not None else _log
    print("── SYSTEM(组件拆分,来自引擎真实装配)──")
    for name, text in eng.system_component_sections(source, log):
        print(f"  ◆ {name}: {text}")


def _build_messages(source: str, log=None) -> list[dict]:
    """引擎真实喂模型的 messages:直接调 eng._loop._build_messages(同进程私有,
    与 run_turn 内部完全一致 —— system + 历史投影,前缀由引擎 loop 现读)。"""
    log = log if log is not None else _log
    return eng._loop._build_messages(
        eng._tools, source, log,
        max_rounds=_max_rounds, system_prompt=None,
    )


def print_messages(messages: list[dict], source: str = "user", log=None) -> None:
    log = log if log is not None else _log
    if messages and messages[0]["role"] == "system":
        _print_system(source, log)
        print("── messages(实际发送) ──")
    for m in messages:
        txt = _blocks_text(m.get("content")) or ""
        txt = txt if len(txt) <= 600 else txt[:600] + "…"
        print(f"[{m.get('role')}] {txt}")


def _stream_cb(chunk: dict) -> None:
    kind = chunk.get("kind")
    if kind == "text":
        print(chunk.get("text", ""), end="", flush=True)
    elif kind == "tool_call":
        print(f"\n  ⚙ [tool] {chunk.get('name', '')}", flush=True)


def _run_turn(source: str, user_input: str | None = None, self_note: str | None = None,
              log=None) -> None:
    """用**引擎真实 loop**(eng._loop)跑一轮;LLM 报错只报一句不炸台。"""
    log = log if log is not None else _log
    tag = {"self": "自走轮", "user": "陪聊轮"}.get(source, source)
    clock = _fmt_ts(time.time())
    print(f"\n===== {tag} · {_model or '(默认)'} · 墙钟 {clock} · "
          f"前缀={_effective_prefix() or '(空)'} =====")
    try:
        msgs = _build_messages(source, log)
    except Exception as exc:  # noqa: BLE001
        msgs = []
        print(f"(预览失败:{exc})")
    print_messages(msgs, source, log)
    print("────── 输出 ──────")
    try:
        result = eng._loop.run_turn(
            user_input=user_input, source=source, log=log,
            self_note=self_note, on_chunk=_stream_cb,
            model=(_model or None), max_rounds=_max_rounds,
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
    except Exception as exc:  # noqa: BLE001
        print(f"\n⚠ LLM 调用失败(回菜单可继续): {exc}")


def _run_backfill() -> None:
    """补写窗口模拟(引擎同款机制):采样 → 每件 set_time_cursor 历史时刻回放。"""
    print("\n── 补写窗口模拟(她离线期间怎么过的)──")
    last_text = input("她最后活跃时刻(回车=往前 10 小时; 或 昨天 20:00): ").strip()
    end_text = input("补写到此刻(回车=现在): ").strip()
    end_ts = _parse_ts(end_text) or time.time()
    start_ts = _parse_ts(last_text) if last_text else end_ts - 10 * 3600
    if start_ts >= end_ts:
        print("开始须早于结束。")
        return
    print(f"离线窗口: {_fmt_ts(start_ts)} → {_fmt_ts(end_ts)}"
          f"(共 {_human_gap(end_ts - start_ts)})")

    seed = random.randrange(2**31)
    events = LifeSampler(start_ts, end_ts, seed=seed).sample()
    if not events:
        print(f"采样器无事件(seed {seed})—— 换窗口或再试。")
        return
    print(f"采样到 {len(events)} 件事(seed {seed}):")
    for e in events:
        print(f"  · {_fmt_ts(e.start)} 起,约 {e.budget_min:.0f} 分钟")
    if input("开始逐件回放?(回车继续 / q 取消): ").strip().lower() == "q":
        return

    from core.tools import ToolRegistry  # noqa: PLC0415
    empty_tools = ToolRegistry([])
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
        eng._backfill_clock["ts"] = e.start   # 引擎补写 composer 的世界时钟
        try:
            _run_turn("self", self_note=note, log=_log)
        finally:
            _log.clear_time_cursor()
            eng._backfill_clock["ts"] = 0.0
    print(f"\n补写完成 {len(events)} 件。按 1 陪聊轮 → 历史里可看到这些自语"
          "带前缀+离线时间戳(前缀为空时按 t 开演示档)。")


# ---------- 交互循环 ----------

def _menu() -> None:
    print("\n" + "─" * 60)
    print(f"模型 {_model or '(引擎默认)'} · 窗口 {_max_rounds or '全量'} · "
          f"自语前缀 {_effective_prefix() or '(空)'}")
    print("1 陪聊轮   2 自走轮   3 输入预览   b 补写模拟")
    print("t 前缀     m 模型    w 窗口   c 清空历史   q 退出")


def main() -> None:
    global _log, _model, _max_rounds, _prefix_mode
    _rebuild()
    if "--preview" in sys.argv:
        print("=== 输入预览(不调模型)· user 轮(你说:在吗) ===")
        print_messages(_build_messages("user", _log), "user", _log)
        _prefix_mode = "file"
        print("\n=== 同上· self 轮(前缀=文件值) ===")
        print_messages(_build_messages("self", _log), "self", _log)
        return

    print("小夜子提示词实验台 · 驱动真实引擎 · 改 personas.py 后下一轮自动重建")
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
            _rebuild()
            msg = input("你对她说: ").strip()
            if not msg:
                continue
            _run_turn("user", user_input=msg)
        elif choice == "2":
            _rebuild()
            note = input("自走情境(回车=纯心跳无情境; 输入=情境自走): ").strip()
            _run_turn("self", self_note=note or None)
        elif choice == "b":
            _rebuild()
            _run_backfill()
        elif choice == "3":
            _rebuild()
            src = input("预览哪轮(user/self): ").strip().lower()
            src = "self" if src == "self" else "user"
            print_messages(_build_messages(src, _log), src, _log)
        elif choice == "t":
            modes = ["file", "off", "〔自语〕", "〔自语·{time}〕"]
            _prefix_mode = modes[(modes.index(_prefix_mode) + 1) % len(modes)]
            _rebuild()  # 让引擎 loop 前缀 = 新档
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
            _log = SessionLog("prompt-lab")
            print("会话历史已清空")
        else:
            print("?")


if __name__ == "__main__":
    main()
