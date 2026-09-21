"""小夜子 · 整轮实验台(turn_lab) —— 真模型,驱动**真实引擎装配**

2026-09-19 改名:`prompt_lab.py` → `turn_lab.py`。它做的不是"提示词"实验,
是**跑一整轮**(陪聊/自走/补写回放 + 拨时钟 + 换模型/窗口)—— 名字要跟实际
作用对上,`prompt_lab` 这个称呼让给了新的 `prompt_lab/` 目录
(提示词文案实验台,按被试对象分类,见 prompt_lab/README.md)。

用法:  py turn_lab.py            交互式(聊天/自走/补写/预览)
       py turn_lab.py --preview   只打印当前输入预览(不调模型,零花费)

它做什么:
- **从实际生效的代码取数**(2026-09 修正):不再平行复刻 composer/llm/tools。
  直接 import `server.app.engine`(eng),每次轮次前:
    1. reload `character/personas`(改文案立即生效)
    2. `eng._build_engine(cfg)` —— 用引擎同一装配函数重建 composer/loop/llm
    3. 之后只用 `eng._loop.run_turn(...)` / `eng.system_component_sections(...)`
  实验台看到的 system、历史、前缀、llm 调用 = 引擎真实路径,llm-log 也照记。
- **会话不落盘**:聊天日志用进程内 SessionLog(退出即弃,不写 data/)。
- **每次真实 LLM 调用都打输入**(2026-09,lab 包 llm 代理,内核零改动):
  跑 run_turn 前把 `eng._loop.llm` 换成打印代理 —— 内核每步(每次真实调用)
  调 `llm.stream(messages)` 时,代理先打 **SYSTEM 组件拆分**(persona/
  situation/world/state/tool_usages,来自引擎真实 composer)+ 完整 messages,
  再转发给真实 llm。一轮里调几次 LLM 就打印几次(调工具后下一步的输入带
  tool/result,和上一步不一样);随后流式打印输出,工具执行(参数/返回)
  按日志顺序补打。跑完还回真实 llm。
- **自走轮手动触发**:按键即"心跳此刻醒来";回车 = 无情境(引擎纯心跳
  占位),输入一句话 = 情境自走。纯醒来(回车)那轮先按拍板语义采样判定:
  窗口无事件 → **加深一级问"是否强制触发"**(2026-09 拍板)—— 选 y:窗口内
  随机选区作事件 start(随机数①)+ 抽预算(随机数②,同款时长分布,兜底截断
  ≤ 当前时刻),按命中轮跑;回车:安静结束,不调 LLM(打印判定与窗口)。
  **事件轮的 [当前时间] = 事件 start**(2026-09 拍板):命中(自然/强制)就把
  世界钟拨到事件起点 —— 模型看到 [当前时间]=start、[时间线] 从 start 派生
  (不再是整段空窗的"+4h 前"),生活事件落事件结束时刻(start+预算,下一轮锚从
  这起)。情境自走不 gate。
- **虚拟时钟 = 可注入间隔/时刻(2026-09 用户三提后拍板玩法)**:每次运行/
  清空,时钟锚到今天上午 9 点;跑自走轮(2)前先打印参照 = 日志里最近
  一轮/事件的末时间,再输入你决定的假冒时间(如 23:30 / +2h / 09-08 09:00),
  默认不跨天。引擎世界 section 与事件时间戳都跟着拨 —— 测"刚聊完 vs 久别"
  两种自走效果就是拨不同的时刻(单时间源被拨,仍是引擎那条钟)。
- **补写窗口模拟**(b):选 她最后活跃 → 补写到此刻,LifeSampler 采样,逐件
  `set_time_cursor` 以历史时刻回放(引擎同款 note/空工具/时间游标),日志里
  落下**带历史时间戳的生活事件**。
- (2026-09-21 拆)生活事件不再进模型上下文,取用只走 `recall` 工具 ——
  原来那个「生活事件前缀」演示档(菜单 t)随注入侧一起删除。
- LLM 报错不炸台:捕获后报一句回菜单。

菜单:
  1 陪聊轮    2 自走轮    3 输入预览    b 补写模拟
  v 注入时刻  m 换模型      w 上下文窗口
  c 清空历史   q 退出
"""

from __future__ import annotations

import importlib
import random
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent   # 本文件在项目根(2026-09 从 test/ 上移)
sys.path.insert(0, str(ROOT))

try:  # Windows 控制台直接跑时输出中文不乱码
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# ---------- 真实引擎(装配/composer/loop/llm 全从这里来) ----------

from server.app import engine as eng          # noqa: E402  真实装配目标
from server.app import llm_setup              # noqa: E402
from server.rhythm import LifeSampler, draw_budget_min  # noqa: E402
from server.params import MIN_EVENT_SEC       # noqa: E402  最短事件(强制事件兜底用)
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
# 虚拟时钟偏移(2026-09 用户三提后拍板的玩法):实验台"篡改当前时间"——
# 虚拟当前时刻 = time.time() + offset。每次运行/清空后锚到**今天上午 9 点**,
# 测自走轮前先打印日志尾巴(最近一轮末事件时间)作参照,再输入假冒时间。
# 引擎侧挂接:eng._clock_override["ts"](世界 section 的钟)+ log 时间游标
# (事件盖虚拟时间戳)—— 改的仍是引擎单时间源,不是另起一套钟。
_clock_offset: float = 0.0

_MODELS = [m for m in (_cfg.get("models") or []) if isinstance(m, str)] or [_model]


def _vnow() -> float:
    """虚拟当前时刻(epoch 秒);offset=0 时 = 真实墙钟。"""
    return time.time() + _clock_offset


def _anchor_morning() -> None:
    """锚定虚拟时钟:让"现在" = 今天上午 9 点(每次运行/清空后调)。"""
    global _clock_offset
    now_dt = datetime.fromtimestamp(time.time())
    today_09 = now_dt.replace(hour=9, minute=0, second=0, microsecond=0)
    _clock_offset = today_09.timestamp() - time.time()


def _apply_clock(log) -> None:
    """把虚拟当前时刻推进引擎:世界 section 的钟 + 事件盖时间戳的游标。"""
    eng._clock_override["ts"] = _vnow()
    log.set_time_cursor(_vnow())


def _clear_clock(log) -> None:
    """跑完一轮,撤掉虚拟钟(引擎回真实墙钟;游标清掉,后面 append 恢复默认)。"""
    eng._clock_override["ts"] = 0.0
    log.clear_time_cursor()


def _tail_time(log) -> float | None:
    """日志尾巴时间:最近一轮(对话或事件)的最末事件时间;空日志=None。"""
    if not log.events:
        return None
    return log.events[-1].time


def _parse_ts(text: str, *, base: float | None = None,
              relative: bool = False) -> float | None:
    """解析时间输入 → epoch;空输入/认不出的格式打印提示并返回 None。

    ⚠️ 这是**一个**函数服务两处调用,而两处的口径不同 —— 靠下面两个开关区分。
    合并前是 `_parse_fake`(虚拟钟 + 相对量)与 `_parse_ts`(墙钟 + 无相对量)
    两个函数,三者里有 90% 逐字重复(同样三种 strptime 格式、同样 `%H:%M`
    补年月日、同样 `%m-%d` 补年),只有这两处差异。

    认的格式(两处完全一样):
      HH:MM              基准日的那个时刻
      MM-DD HH:MM        基准年内的某天(可跨天)
      YYYY-MM-DD HH:MM   绝对时刻(可跨年)
    只有 `relative=True` 时额外认:
      +90m / +2h / +3d   相对基准时刻往后拨

    两处调用各自的口径(**不许合并掉,合并了就改行为**):
      · `_inject_time`(菜单 v / 自走轮前)—— `base=_vnow()`, `relative=True`。
        基准是**虚拟钟**:这里问的是"台上假装的现在往后拨多久",
        `+2h` 必须是"虚拟现在 + 2 小时"而不是"墙钟 + 2 小时";
        拨完 `_inject_time` 再拿它反算 `_clock_offset`。
      · `_run_backfill`(菜单 b 的窗口两端)—— 都不传,即
        `base=time.time()`(墙钟)、`relative=False`。
        那里输入的是**真实**的"昨天 20:00",和台上的虚拟钟无关;
        两端也必须是绝对时刻,相对量在"窗口起点/终点"上没意义。
    """
    text = text.strip()
    if not text:
        return None
    now = time.time() if base is None else float(base)
    # 相对注入:+N m/h/d(只在 relative=True 时认)
    if relative and text.startswith("+"):
        rel = text[1:]
        if len(rel) >= 2 and rel[-1] in "mhd":
            try:
                n = float(rel[:-1])
            except ValueError:
                n = -1.0
            unit = {"m": 60.0, "h": 3600.0, "d": 86400.0}[rel[-1]]
            if n > 0:
                return now + n * unit
    now_dt = datetime.fromtimestamp(now)
    for fmt in ("%Y-%m-%d %H:%M", "%m-%d %H:%M", "%H:%M"):
        try:
            dt = datetime.strptime(text, fmt)
            if fmt == "%H:%M":
                dt = dt.replace(year=now_dt.year, month=now_dt.month, day=now_dt.day)
            elif fmt == "%m-%d %H:%M":
                dt = dt.replace(year=now_dt.year)
            return dt.timestamp()
        except ValueError:
            continue
    print("时间格式没看懂,示例: 11:30(今天)/ 09-08 09:00 / 2026-09-08 09:00"
          + ("/ +2h(相对)" if relative else ""))
    return None


def _inject_time(log=None) -> None:
    """注入假冒时间(自走轮测试前/随时 v 用):先打印参照 = 最近一轮/事件的
    末时间(虚拟时钟),再按你的输入拨钟。回车 = 沿用现有虚拟时钟。"""
    global _clock_offset
    log = log if log is not None else _log
    print("\n── 注入时刻(篡改当前时间,默认不跨天)──")
    tail = _tail_time(log)
    if tail is None:
        print(f"  · 日志还没有事件 · 当前虚拟时钟 {_fmt_ts(_vnow())}")
    else:
        print(f"  · 参照:最近一轮/事件的末时间 = {_fmt_ts(tail)}(虚拟时钟)")
        print(f"  · 相对现在约 {_human_gap(max(0.0, _vnow() - tail))}")
    text = input("  假冒当前时间(回车=沿用; 例 23:30 / +2h / 09-08 09:00 / "
                 "真实): ").strip()
    low = text.lower()
    if not text:
        print(f"  → 保持 {_fmt_ts(_vnow())}")
        return
    if low in ("真实", "now", "wall"):
        _clock_offset = 0.0
        print("  → 已回真实墙钟")
        return
    ts = _parse_ts(text, base=_vnow(), relative=True)  # 虚拟钟 + 认相对量
    if ts is None:
        return
    _clock_offset = ts - time.time()
    print(f"  → 虚拟时钟 → {_fmt_ts(_vnow())}")


def _rebuild() -> None:
    """reload 文案 → 用引擎真实装配函数重建 composer/loop/llm。

    引擎 `eng._build_engine` 内部现取 personas_mod.* 属性,所以 reload 后
    重建即读到新文案 —— 这正是"从实际生效代码取数"。
    """
    importlib.reload(personas_mod)
    eng._build_engine(_cfg)  # 装配进 eng._loop / eng._composers / eng._llm


# ⚠ 与 prompt_lab/tool_recall.py 的 `_fmt_ts` **同源但不逐字**:这儿是
#   `%m-%d %H:%M`(不带年份),那儿是 `%Y-%m-%d %H:%M`。改一处前先确认差异是否故意。
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


# ---------- 展示(组件来自引擎真实 composer) ----------

# ⚠ 与 prompt_lab/tool_recall.py 的 `_blocks_text` 是**同一件(逐字)** ——
#   两处各有一份,改一处必须改另一处(故意没抽公共模块:不许新建顶层文件)。
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
    """SYSTEM 组件拆分 = 引擎真实 composer 的段(eng.system_component_sections)。

    ⚠ 这个观测口固定读全局 `eng._tools`,**看不见本轮实际开放的子集**(产品侧
    注释里点明了这是有意的:观测口答的是"完整装配起来长什么样")。所以补写回放
    (`_run_backfill` 那轮传的是 `tools=empty_tools`)那一轮的 [可用工具用法] 段
    其实**不会发出去**,这里却会照打出来 —— 打印比实际多。台子上要按本轮工具
    逐段看,得走 `prompt_lab/tool_recall.py` 的 `sections()`(它自己传 registry)。
    """
    log = log if log is not None else _log
    print("── SYSTEM(组件拆分,来自引擎真实装配)──")
    for name, text in eng.system_component_sections(source, log):
        print(f"  ◆ {name}: {text}")


def _build_messages(source: str, log=None) -> list[dict]:
    """引擎真实喂模型的 messages:直接调 eng._loop._build_messages(同进程私有,
    与 run_turn 内部完全一致 —— system + 历史投影)。"""
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


def _make_console_cb(log):
    """当轮控制台渲染回调:文本直打,工具执行按日志顺序补打。

    流式只给 LLM 的 chunk;工具是引擎在**步与步之间同步执行**的,结果先写进
    日志(tool/call + tool/result),之后下一步的输入回调才到 —— 所以在每次
    LLM 调用前先扫日志,把新增的工具调用(带完整参数)与返回内容打出来,
    控制台顺序才是真实的:第 N 次 LLM → ⚙ 调用(参数) → 返回内容 → 她的下一句。

    log:本轮日志(引擎 run_turn 边跑边往里写;同一对象引用)。
    """

    shown_upto = -1  # 已显示到的日志 seq(引擎 append 单调递增)

    def _flush_log_events() -> None:
        nonlocal shown_upto
        for e in log.events:
            if e.seq <= shown_upto:
                continue
            shown_upto = e.seq
            t = e.type
            if t == "tool/call":
                name = e.data.get("name", "")
                args = e.data.get("arguments", "")
                print(f"  ⚙ [tool] {name} {args}".rstrip(), flush=True)
            elif t == "tool/result":
                txt = _blocks_text(e.data.get("content")) or ""
                if e.data.get("is_error"):
                    print(f"  ✗ 工具报错: {txt}", flush=True)
                else:
                    print(f"  ↳ 工具返回: {txt}", flush=True)

    def cb(chunk: dict) -> None:
        kind = chunk.get("kind")
        if kind == "text":
            # 下一条文字 = 新一轮 LLM 开始(或新一步):先补打已落日志的工具内容
            _flush_log_events()
            print(chunk.get("text", ""), end="", flush=True)

    cb.flush = _flush_log_events  # 轮末兜底:中断/无后续文字时也能带出工具内容
    return cb


# ⚠ 与 prompt_lab/tool_recall.py 的 `_InputPrintProxy` 是**同一件(逐字)** ——
#   两处各有一份,改一处必须改另一处(同上,不抽公共模块)。
class _InputPrintProxy:
    """lab 侧 llm 代理:每次真实 LLM 调用前打印组件拆分 + messages(实际发送)。

    只改 lab 显示,不碰内核:引擎 loop 每步调 `self.llm.stream(messages, ...)` ——
    lab 在 run_turn 前把 `eng._loop.llm` 换成这个代理,代理先打印再转发给真实
    llm。一轮里调几次 LLM 就打印几次(调工具后下一步的输入带 tool/result,
    与上一步不一样)。
    """

    def __init__(self, inner, on_call) -> None:
        self._inner = inner
        self._on_call = on_call
        self.model = getattr(inner, "model", None)

    def stream(self, messages, tools=None, temperature=None, max_tokens=None,
               model=None):
        self._on_call(messages)
        return self._inner.stream(
            messages, tools=tools, temperature=temperature,
            max_tokens=max_tokens, model=model,
        )

    def invoke(self, messages, tools=None, temperature=None, max_tokens=None,
               model=None):
        self._on_call(messages)
        return self._inner.invoke(
            messages, tools=tools, temperature=temperature,
            max_tokens=max_tokens, model=model,
        )


def _run_turn(source: str, user_input: str | None = None, self_note: str | None = None,
              log=None, tools=None) -> None:
    """用**引擎真实 loop**(eng._loop)跑一轮;LLM 报错只报一句不炸台。

    tools: 本轮开放的工具子集(`ToolRegistry` 或 list);None = 引擎构造时的全量
        (`run_turn(tools=...)` 语义,见内核 `core/loop.py` 的 run_turn 参数说明)。
        **补写回放必须传空表** —— 理由与产品同款(下详)。

    虚拟时钟(2026-09):非补写回放时,先把引擎世界钟拨到 _vnow()、给 log 盖
    虚拟时间游标 —— 这轮的事件/生活事件时间戳都落在虚拟时刻,跑完撤掉(引擎回墙钟)。
    普通自走轮(2 自走 / 情境)也走引擎同款 **时间预算**(begin_self_wake(log),
    与心跳/脉冲一致 —— 用户拍板:普通轮与补写同一事件算法,预算锚到日志尾
    →当前时刻 的可消费区间,不是浮空抽数);补写回放例外。
    **无事件自走轮 = 安静结束,不调 LLM**(2026-09 拍板):纯醒来(回车无情境)
    时 begin_self_wake 返回 0 = 窗口 [日志尾 → 当前] 采样判定无事件 → 该轮
    不触发任何事件;lab 在这里**加深一级**(用户拍板):问是否强制触发 ——
    选 y → 窗口内随机选区作事件 start + 抽预算(两个随机数,同款时长分布,
    兜底截断 start+预算 ≤ 当前),填 `_wake_budget` 后按命中轮跑;回车 →
    安静结束,不调模型。情境自走(显式 self_note)= 人工指定情境的测试轮,
    不 gate,照跑。
    **事件轮的当前时间 = 事件 start(2026-09 拍板)**:命中(自然/强制)的自走
    轮把世界钟拨到事件 start —— 模型看到的 [当前时间] = 事件起点(不是注入
    的 +4h 那种"现在"),[时间线] 从 start 派生,不再是整段空窗;本轮生活事件落
    事件结束时刻(start+预算,锚推进,下一轮窗口从这起)。
    """
    log = log if log is not None else _log
    replaying = bool(eng._backfill_clock["ts"])  # 补写回放:游标归调用方管
    ordinary_self = source == "self" and not replaying
    quiet_skip = False
    # 事件轮锚:start + 预算(自然命中由 begin_self_wake 记;强制由下方现场抽)
    anchor_s = 0.0
    anchor_b = 0.0
    if not replaying:
        _apply_clock(log)
    if ordinary_self:
        # 2026-09 拍板落地:无事件的自走轮**安静结束,不调 LLM** —— 只有
        # begin_self_wake 判定命中事件(预算 > 0)才跑,跑了必有 [时间预算]。
        # 情境自走(显式 self_note)= 人工指定情境的测试轮,不 gate(照跑)。
        budget = eng.begin_self_wake(log)
        anchor_s = eng._wake_anchor["start"]
        anchor_b = budget
        quiet_skip = self_note is None and budget <= 0
    try:
        tag = {"self": "自走轮", "user": "陪聊轮"}.get(source, source)
        if replaying:
            clock_ts, clock_tag = eng._backfill_clock["ts"], "回放"
        else:
            clock_ts, clock_tag = _vnow(), ("虚拟" if _clock_offset else "真实")
        # 事件轮的 [当前时间] = 事件 start(2026-09 拍板):世界钟拨到 start,
        # [时间线] 从它派生(不再是整段空窗的"4 小时前")。
        if ordinary_self and not replaying and not quiet_skip and anchor_s > 0:
            clock_ts, clock_tag = anchor_s, "事件start"
        print(f"\n===== {tag} · {_model or '(默认)'} · {clock_tag}时钟 "
              f"{_fmt_ts(clock_ts)} =====")
        if quiet_skip:
            tail = _tail_time(log)
            print(f"── 采样判定 [{_fmt_ts(tail) if tail else '—'} → "
                  f"{_fmt_ts(clock_ts)}] 无事件 → 该轮不触发任何事件 ──")
            # 更深一级(2026-09 用户拍板):要不要**强制触发**?要 → 窗口内
            # 随机选区作事件 start(一个随机数)+ 抽预算(另一个随机数),
            # 按命中轮跑 —— 两个数都会落在当轮视图里。
            try:
                force = input("  强制触发自走事件?(y = 窗口内随机 start + 抽预算,"
                              "然后跑这轮; 回车 = 安静结束): ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print("\n  安静结束(不调 LLM)。")
                return
            if force not in ("y", "yes"):
                print("  安静结束(不调 LLM)。")
                return
            if tail is None:
                print("  没有日志尾(还没跟主人说过话)→ 没有可消费区间,"
                      "无从随机选区,安静结束。")
                return
            if clock_ts - MIN_EVENT_SEC <= tail:
                print(f"  窗口不足最短事件({MIN_EVENT_SEC / 60:.0f} 分钟),"
                      "放不下一个事件,安静结束。")
                return
            # 强制事件 = ① 区间 [日志尾, 当前] 内均匀随机一个点作 start;
            # ② 预算 = 另抽一个数(与 LifeSampler 同一时长分布),再走**兜底
            # 截断** start+预算 ≤ 当前时刻(和引擎命中轮同一把尺)。
            rng = random.Random()
            s = rng.uniform(tail, clock_ts - MIN_EVENT_SEC)
            budget_min = min(draw_budget_min(rng=rng),
                             (clock_ts - s) / 60.0)
            eng._wake_budget["min"] = budget_min
            anchor_s, anchor_b = s, budget_min
        # 事件轮锚定(自然命中或强制,纯 lab):世界钟 = start、生活事件游标 =
        # start+预算(这轮生活事件落事件结束,下一轮锚从这起)。产品心跳/脉冲不
        # 这么做(2026-09 用户拍板:锚定试验只留 lab)。
        if ordinary_self and not replaying and anchor_s > 0:
            eng._clock_override["ts"] = anchor_s
            log.set_time_cursor(anchor_s + anchor_b * 60.0)
            print(f"── 事件锚:start={_fmt_ts(anchor_s)} · 预算约 {anchor_b:.0f} 分钟"
                  f"(结束 {_fmt_ts(anchor_s + anchor_b * 60.0)},生活事件落这里;"
                  "下一轮窗口从这起)──")
        console_cb = _make_console_cb(log)

        # 包一层 llm 代理:每次真实 LLM 调用前打印组件拆分 + 实际 messages
        # (一轮里调几次就打几次;调工具后下一步的输入带 tool/result)。
        # 只改 lab 显示,内核/引擎零改动:内核 loop 调 self.llm.stream 时,
        # 走到的是这里包的代理,打印完再转发给真实 llm。
        call_no = {"n": 0}

        def _print_call_input(messages) -> None:
            console_cb.flush()  # 上一步的工具执行先落地,再接下一步的输入
            call_no["n"] += 1
            print(f"\n──── 第 {call_no['n']} 次 LLM 调用 · 输入 ────")
            print_messages(messages, source, log)
            print("────── 输出 ──────")

        inner_llm = eng._loop.llm
        eng._loop.llm = _InputPrintProxy(inner_llm, _print_call_input)
        try:
            result = eng._loop.run_turn(
                user_input=user_input, source=source, log=log,
                self_note=self_note, on_chunk=console_cb,
                model=(_model or None), max_rounds=_max_rounds,
                tools=tools,
            )
            console_cb.flush()  # 兜底:最后一步的工具内容若没被文字带到,这里补上
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
        finally:
            eng._loop.llm = inner_llm  # 还回引擎真实 llm(每轮重建,双保险)
    finally:
        if ordinary_self:
            eng.end_self_wake()
        if not replaying:
            _clear_clock(log)


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
    # ⚠ 补写回放**必须用空工具表**,这不是台子的发明而是产品的行为:
    #   产品补写(`server/app/engine.py` 的 `_maybe_backfill_life`)里就是
    #   `empty_tools = ToolRegistry([])` 然后
    #   `loop.run_turn(source="self", log=card_log, tools=empty_tools, ...)`;
    #   同文件 `_build_engine` 的说明写着理由 ——"那段日子怎么过的,**不该有实时工具**"。
    #   (顺带:两个调用点取值不同是**有意的**,`sys_by_source` 收本轮真实注册表,
    #    只读观测口 `system_component_sections` 固定收全局 `_tools`,别硬合并。)
    #   改之前:这里造了 `empty_tools` 却从未使用,而 `_run_turn` 压根没有 tools 参数
    #   → 补写回放**带着全套工具**在跑,与产品不一致(2026-09-22 修)。
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
            _run_turn("self", self_note=note, log=_log, tools=empty_tools)
        finally:
            _log.clear_time_cursor()
            eng._backfill_clock["ts"] = 0.0
    print(f"\n补写完成 {len(events)} 件。按 1 陪聊轮 —— 这些生活事件"
          "**不进上下文**(2026-09-21 起),要看到它们得让她调 recall。")


# ---------- 交互循环 ----------

def _menu() -> None:
    print("\n" + "─" * 60)
    tag = "真实墙钟" if not _clock_offset else "虚拟时钟"
    print(f"模型 {_model or '(引擎默认)'} · 窗口 {_max_rounds or '全量'} · "
          f"现在 {_fmt_ts(_vnow())}({tag})")
    print("1 陪聊轮   2 自走轮   3 输入预览   b 补写模拟")
    print("v 注入时刻  m 模型      w 窗口")
    print("c 清空历史  q 退出")


def main() -> None:
    global _log, _model, _max_rounds
    _anchor_morning()  # 每次运行:从今天上午 9 点开始(用户拍板)
    _rebuild()
    if "--preview" in sys.argv:
        eng._clock_override["ts"] = _vnow()  # 预览也落在 09:00 起的虚拟时钟
        print("=== 输入预览(不调模型)· user 轮(你说:在吗) ===")
        print_messages(_build_messages("user", _log), "user", _log)
        print("\n=== 同上· self 轮 ===")
        print_messages(_build_messages("self", _log), "self", _log)
        return

    print(f"小夜子提示词实验台 · 驱动真实引擎 · 虚拟时钟今天 09:00 起"
          f"(2 自走轮 / v 可注入时刻)")
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
            _inject_time(_log)  # 先给参照(最近一轮末时间)+ 注入假冒时刻
            note = input("自走情境(回车=纯心跳无情境; 输入=情境自走): ").strip()
            _run_turn("self", self_note=note or None)
        elif choice == "v":
            _rebuild()
            _inject_time(_log)
        elif choice == "b":
            _rebuild()
            _run_backfill()
        elif choice == "3":
            _rebuild()
            src = input("预览哪轮(user/self): ").strip().lower()
            src = "self" if src == "self" else "user"
            eng._clock_override["ts"] = _vnow()
            try:
                print_messages(_build_messages(src, _log), src, _log)
            finally:
                eng._clock_override["ts"] = 0.0
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
            _anchor_morning()  # 新一段也从上午 9 点开始
            print("会话历史已清空(虚拟时钟回今天 09:00)")
        else:
            print("?")


if __name__ == "__main__":
    main()
