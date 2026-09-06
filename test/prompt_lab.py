"""小夜子 · 提示词实验台(prompt_lab) —— 真模型,看实际输入/输出

用法:  py test/prompt_lab.py            交互式(聊天/自走/组件预览)
       py test/prompt_lab.py --preview   只打印当前输入预览(不调模型,零花费)

它做什么:
- **完全复用引擎的装配链**(character 层 composer + core AgentLoop + 真 LLM),
  不启动 server、不碰内核、不写 data/ —— 会话日志只在进程内,退出即弃。
- **每次轮次前自动重新加载 character/personas.py**:你在编辑器里改
  PERSONA / 情境 / SELF_TALK_PREFIX,回到本实验台下一轮立即生效,不用重启。
- 每次真实调用前打印 **组件拆分的 SYSTEM**(persona / situation / world /
  state / tool usage 各段分开)+ 完整 messages 输入;调用后流式打印输出。
- **自走轮手动触发**:不需要等心跳闸门/间隔 —— 按键即"心跳此刻醒来";
  直接回车 = 无情境(引擎纯心跳占位),输入一句话 = 情境自走/补写同款 note。
- **自语前缀即时切换**:不改文件也能看 SELF_TALK_PREFIX 空 / 〔自语〕 /
  〔自语·{time}〕三种效果(改文件后选 file 档即用你写的前缀)。

菜单:
  1 陪聊轮(user)    —— 输入你对她说的话
  2 自走轮(self)    —— 回车=纯心跳占位;输入情境=脉冲/补写同款
  3 输入预览         —— 只打印将发送的输入(不调模型)
  t 自语前缀         —— file(你写在 personas 里的)⇄ 空 ⇄ 〔自语〕 ⇄ 〔自语·{time}〕
  m 换模型           —— 当前端点可用列表轮换
  w 上下文窗口       —— 全量 ⇄ 最近 3 轮 ⇄ 最近 8 轮
  c 清空会话历史     —— 重开一段
  q 退出
"""

from __future__ import annotations

import importlib
import sys
import time
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

# 三个 composer 由每次 reload 后的文案重建(见 _reload_personas)
_composers: dict[str, object] = {}


def _reload_personas() -> None:
    """重新加载 character/personas.py,并用新文案重建 composer。

    这样改 PERSONA/情境/SELF_TALK_PREFIX 不用重启实验台,下一轮即生效。
    """
    importlib.reload(personas_mod)
    p = personas_mod
    _composers.clear()
    for mode, sit in (("chat", p.CHAT_SITUATION),
                      ("self", p.SELF_SITUATION),
                      ("backfill", p.BACKFILL_SITUATION)):
        _composers[mode] = persona_factory.build_small_night_composer(
            p.PERSONA, _state, _tools, situation=sit,
            world_now=(None if mode != "backfill" else
                       lambda: time.localtime(_backfill_clock["ts"])),
        )
    _backfill_clock["ts"] = time.time()


_backfill_clock: dict[str, float] = {"ts": 0.0}


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


# ---------- SYSTEM builder(与引擎同款:按 source 选 composer,注入 VALUES+registry) ----------

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
    """打印实际输入:system 组件拆分 + 每条消息(role/纯文本)。

    source/log 用于组件拆分展示(与发送的 system 同款 composer)。
    """
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


_last_source = "user"


def _stream_cb(chunk: dict) -> None:
    kind = chunk.get("kind")
    if kind == "text":
        print(chunk.get("text", ""), end="", flush=True)
    elif kind == "reasoning":
        pass
    elif kind == "tool_call":
        print(f"\n  ⚙ [tool] {chunk.get('name', '')}", flush=True)
    elif kind == "finish":
        pass


def _current_slot(source: str, user_input: str | None, self_note: str | None) -> dict:
    """当前轮 user 槽消息(展示用):user=你说的话;self=情境 note 或占位。

    真实 run_turn 会先把这条 append 进日志再构建输入;这里手动补上,
    让"打印的输入"和"实际发送的输入"一致。
    """
    if source == "user":
        return {"role": "user", "content": user_input or ""}
    if self_note:
        return {"role": "user", "content": self_note}
    return {"role": "user",
            "content": "【自动轮】此为自我触发的轮次,没有用户自主消息(内核占位串,"
                       "可改 personas 文案或给它输情境来替代)。"}


def _run_turn(source: str, user_input: str | None = None, self_note: str | None = None) -> None:
    """跑一轮真模型:先打印输入,再流式输出,最后报 usage/finish。"""
    global _last_source
    _last_source = source
    print(f"\n===== {'自走轮' if source == 'self' else '陪聊轮'} · {_model} · "
          f"前缀={_effective_prefix() or '(空)'} =====")
    preview = _build_messages(source, _log) + [_current_slot(source, user_input, self_note)]
    print_messages(preview, source, _log)
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
            user_input=user_input, source=source, log=_log,
            self_note=self_note, on_chunk=_stream_cb, model=_model,
            max_rounds=_max_rounds,
        )
        print()
        reason = result.reason
        usage = None
        for e in reversed(_log.events):
            if e.type == "assistant/message" and e.data.get("turn") == result.turn:
                usage = e.data.get("usage")
                break
        u = usage or {}
        print(f"──── 完成: {reason.get('kind')} · "
              f"{u.get('input_tokens', '?')} in / {u.get('output_tokens', '?')} out "
              f"(回合 {result.turn}, {result.steps} 步) ────")
    except KeyboardInterrupt:
        print("\n(中断)")


# ---------- LLM 客户端 ----------

_llm = OpenAICompatibleLLM(
    api_key=_API_KEY, base_url=_BASE_URL, model=_DEFAULT_MODEL,
    temperature=LLM_DEFAULT_TEMPERATURE, max_tokens=LLM_OUTPUT_MAX_TOKENS,
)


# ---------- 交互循环 ----------

def _menu() -> None:
    print("\n" + "─" * 60)
    print(f"模型 {_model} · 窗口 {_max_rounds or '全量'} · "
          f"自语前缀 {_effective_prefix() or '(空)'}")
    print("1 陪聊轮   2 自走轮   3 输入预览   t 前缀   m 换模型")
    print("w 窗口     c 清空历史   q 退出")


def main() -> None:
    global _log, _model, _max_rounds, _prefix_mode
    if "--preview" in sys.argv:
        _reload_personas()
        print("=== 输入预览(不调模型)· user 轮(你说:在吗) ===")
        preview = _build_messages("user", _log) + \
            [_current_slot("user", "在吗", None)]
        print_messages(preview, "user", _log)
        _prefix_mode = "〔自语·{time}〕"
        print("\n=== 同上· self 轮(前缀演示 〔自语·{time}〕,情境:刚醒) ===")
        preview = _build_messages("self", _log) + \
            [_current_slot("self", None, "你刚醒过来,一个人待着。")]
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
        elif choice == "3":
            _reload_personas()
            src = input("预览哪轮(user/self): ").strip().lower()
            src = "self" if src == "self" else "user"
            print_messages(_build_messages(src, _log), src, _log)
        elif choice == "t":
            modes = ["file", "off", "〔自语〕", "〔自语·{time}〕"]
            _prefix_mode = modes[(modes.index(_prefix_mode) + 1) % len(modes)]
            print(f"自语前缀 → {_prefix_mode} "
                  f"({_effective_prefix() or '(空)'})")
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
