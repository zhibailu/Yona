"""回忆工具 · 文案实验台 —— 跑: py prompt_lab/tool_recall.py

**默认给你三样东西**:她怎么答的、她产出了什么工具参数、工具返回了什么
(返回原文整段打出来,不截断)。SYSTEM 的段拆分和每次 LLM 调用的输入留给
`view=full`(或菜单 `x`)—— 那两样才是真正一屏几百行的东西。

⚠ 换个仓库跑之前先看这段 —— 台子读的是**某个人的真聊天记录**,而聊天记录
  不进版本库(`.gitignore` 里的 `data/`)。所以刚 clone 下来时这里**一张卡都
  没有**,直接跑会停在"读真卡片…"那一步。把它改成你自己的,一共四处:
  ────────────────────────────────────────────────────────────────
  ① 卡片      test/recall_probe.py 顶部(模块常量)的 `SID` / `LOG_PATH`
              → 指到 data/sessions/<你的卡>/chat.log
              (不写行号:`test/recall_probe.py` 本会话内正在被改动,行号会漂)
              (还没有卡:按根 README 起服务聊几句,它自己会建一张)
              **没改的话就是这里炸**:"读真卡片…"后面一句 FileNotFoundError,
              路径明写在报错里 —— 那就是这一条。
  ② 事实      §1 的 QUESTIONS
              那 8 个问题是照**原卡里的具体事实**写的(哪天的日记、哪件衣服);
              换卡后全失去意义 → 照你自己卡里真有的东西重写。
              括号里那行"(…)"是**你在看什么**,不是给她的话。
  ③ 时钟      §0 的 DEFAULT_NOW
              要落在**你自己数据的时间**里;钟是三只一起拨的(set_clock 封装好了)
  ④ 指纹      prompt_lab/check_transient.py 的 FINGERPRINT
              改成一句**只在你卡里出现过**的话(原值出自我卡里的日记)
  ────────────────────────────────────────────────────────────────
  还要一张**有独处记录的卡**:往事分两路,`life` 只认 `source="self"` 的轮
  (test/recall_probe.py 的 `collect_memory()`:判据是 `t["source"] == "self"`,
  不看是哪台机器产的),纯陪聊只会产生 `talk` —— 那样 life 全空,
  往事段不出现、scope=life 永远查不到。攒独处记录:真服务放它自己跑一段,
  或 `py turn_lab.py` 里按 `b` 走一遍补写模拟。

  换完先零花费自检(不调模型、不看卡也能跑):
      py prompt_lab/tool_recall.py --list        只印文案表,任何环境都能跑
      py prompt_lab/tool_recall.py --preview --solo   印 SYSTEM(需要卡 + 连接)
  "连接"指 data/llm.local.json —— 台子和真引擎用同一个,没配的话它在构造时
  就退出并说明(§3 开头那几行)。嵌入模型 BGE 也是可选的:拿不到会自动退回
  占位 bigram 并**说清用的是哪个**(test/recall_probe.py 的 `get_embedder()`)。

它是什么
  把 recall 这件工具接进**真实引擎装配**(server/app/engine.py 的
  composer / loop / llm —— 不是另搭一套平行件),然后你说话,看她:
      ① 调不调 recall              ← 阶段一:对的时机调对的工具
      ② query / scope 填了什么      ← 阶段一:工具内参数
      ③ 工具回来什么(四态)          ← 阶段二的素材
      ④ 她拿着这个结果怎么答

  换文案变体**只改两行**(工具的 description 与 usage),别的一律不动 ——
  所以两次跑出来的差异,就是那两行文案的差异。

和别的台的分工(别混)
  prompt_lab/tool_recall.py    本文件:recall 的**工具文案**,交互式,给自己玩
  turn_lab.py(项目根)         整轮实验台:陪聊/自走/补写 + 拨时钟,与 recall 无关
  test/recall_probe.py         文案与工具实现的**单一来源**(本文件 import 它,不抄)
  test/recall_router_probe.py  阶段一的**批量仪器**:交错跑 + N 次重复 + 出数字
  test/recall_e2e.py           阶段二端到端 + 判据自查

⚠ 本台给的是**手感,不是数字**
  一次只跑一个样本,模型本来就有随机性。台上看到"她这次没调"**不能**当结论 ——
  要数字去 test/recall_router_probe.py 跑 reps。
  这条是踩出来的:早期把 503 当成"她没调",又把分块跑的漂移当成变体差异,
  两个假象各自骗了我一整轮(见 docs/decisions/TRAPS.md §一.9 / §一.11)。

⚠ 默认是**真产品全量工具**:问「今天天气怎么样」这种外面的事,她会规规矩矩
  **不调 recall**,但会去派 `launch_subagent` 的联网工人 —— 实测一次 40 秒、
  18.5k token。那不是文案错(产品本来就这么办),但玩之前你要知道。
  想只测回忆就开 `--solo`(或敲 l):本轮只开放 recall + change_outfit。

玩法
  py prompt_lab/tool_recall.py                  交互(菜单里敲编号)
  py prompt_lab/tool_recall.py --solo           隔离档:本轮只开放 recall
  py prompt_lab/tool_recall.py --view full      直接开全展开视图
  py prompt_lab/tool_recall.py --view quiet     只看她的话 + 一行小结
  py prompt_lab/tool_recall.py --no-memory      不挂往事段(默认挂,见下)
  py prompt_lab/tool_recall.py --preview        只看 SYSTEM,不调模型,零花费
  py prompt_lab/tool_recall.py --list           列出所有文案变体
  py prompt_lab/tool_recall.py --ask "..." --variant 定稿

⚠ 台子不写你的真卡片(断路,见 §0.5)
  台子读的是**真卡片**,而台子是拿来反复乱问的 —— 所以它**一个字节都不写 data/**:
  往 data/ 的写入在台子上被物理掐断,而且每轮后自动核一遍文件指纹,动了当场报。
  实测(2026-09-20,跑真轮前后对 data/ 下 10 个文件取 MD5):逐字节一致。

⚠ 曾经不成立、现已成立的一条(2026-09-19 记,2026-09-20 已修)
  用户要的是:「**tool 的 result 是个瞬时产物,不拼进常驻内容,即使 system 内也不放**」。
  当时做不到:`fold_tool_traces` 关着 —— 它是 `engine.py` 里 `_build_engine(...)`
  的**一个参数**(全文只有一处取值,现在是 `fold_tool_traces=True`;旧注释写的
  `engine.py:778` / `:787` 两处行号早已漂掉、且指向无关内容,已改成按符号找)。
  那时它关着 → `retain_result=False` 那行声明**空转**,
  第一轮 recall 的原文还留在第二轮上下文里。
  现在 `fold_tool_traces=True`(同 `_build_engine`),第二轮 roles =
  ['system','user','assistant','user'],没有 tool 消息。回归检查:
  py prompt_lab/check_transient.py(零花费,谁把它翻回 False 会当场喊)。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from datetime import datetime
from pathlib import Path

# 本文件在 prompt_lab/ 里,项目根在上一级;test/ 只为拿内容层的单一来源。
ROOT = Path(__file__).resolve().parent.parent
for _p in (ROOT, ROOT / "test"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

try:  # Windows 控制台直接跑时输出中文不乱码
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import recall_probe as P                       # noqa: E402  文案 + 工具实现,单一来源
from character import personas as PERSONAS     # noqa: E402
from core.session_log import SessionLog        # noqa: E402
from core.tools import ToolRegistry            # noqa: E402
from server.app import engine as eng           # noqa: E402  真实装配目标
from server.app import llm_setup               # noqa: E402


# ============================================================
# 0. 钟 —— 台上"现在"是几只钟共用的那一个刻度
# ============================================================

# ⚠ 这只钟必须与 SYSTEM 里 [当前时间] 写的一致,否则两边打架:
#   模型按 09-18 推"前天" = 09-16,系统按真实今天(早已过了 09-18)去过滤,
#   台子上就会出现莫名其妙的 day_empty,而你看不出是谁的错。
#   本台把它做成**可拨的**(菜单 d):拨完三处一起改。
#   取值与 test/recall_router_probe.py 的 `NOW_TS`、test/recall_stage1_lab.py 的
#   `NOW_TS` 相同(三处同一个字面量 `(2026, 9, 18, 22, 10, 0, 0, 0, -1)`;
#   按名字找,别按行号 —— 那两个文件也在被改动)。
DEFAULT_NOW = time.mktime((2026, 9, 18, 22, 10, 0, 0, 0, -1))


# ============================================================
# 0.5 断路 —— 台上永远不写"真卡片"
# ============================================================
#
# 台子读的是**真卡片**(test/recall_probe.py 顶部的 `SID` / `LOG_PATH` 写死的那张),
# 而台子是拿来
# 反复乱问的。真卡片被实验轮改掉一个字,后面所有测量用的"事实底座"就变了 ——
# 那是**仪器**坏掉,不是产品坏掉,而且事后从台面上看不出来。
#
# 现状实测(2026-09-20,跑真轮前后对 data/ 下 10 个文件取 MD5):**逐字节一致**。
# 原因是写盘只发生在 `eng._store.save_log` 的那几个调用点(`engine.py` 两处、
# `server/app/api/chat.py` 一处、`main.py` 一处),而台子直接调
# eng._loop.run_turn,**不经过它们**;子运行的 SubRunStore 也是 `store=None`
# (core/subrun.py 里 `if store is not None: store.save(rec)` 那条路才落 jsonl);
# 检索库是 sqlite `:memory:`(test/recall_probe.py 的 `build_db()`)。
# (以上都按符号找,不写行号:这几个文件本会话内正在被改动,行号必漂。)
#
# 那为什么还要这道闸 —— 它防的不是今天的代码,是**明天有人在 run_turn 里面
# 加了一句落盘**:那时台子会静默改掉真卡片,你从台面上看不出来。
# 两层,一层防、一层验:
#     ① 物理断路  —— 所有通往 data/ 的写入当场抛 LabWriteBlocked
#     ② 指纹核对  —— 开局记一次,每轮后对一次,不一致当场把文件名喊出来

CARD_DIR = (ROOT / "data").resolve()

_guard_installed = False


class LabWriteBlocked(RuntimeError):
    """台子上有人想往 data/ 写 —— 拦下来了。"""


def _under_card_dir(path) -> bool:
    """这个路径是不是要落进真卡片目录(data/ 之下,或就是 data/ 本身)。"""
    try:
        p = Path(path).resolve()
    except Exception:  # noqa: BLE001  奇怪的 path-like:交回原函数去报它自己的错
        return False
    return p == CARD_DIR or CARD_DIR in p.parents


def install_write_guard() -> bool:
    """① 物理断路:掐断通往真卡片目录的写入。幂等 → 首次装成功返 True。

    拦的是**写模式**(w / a / x / +)且落在 data/ 的 open —— 读一律放行
    (台子每一轮都要读那张卡,那是它存在的意义)。
    挂在 `builtins.open` 与 `pathlib.Path.open` 两处:`Path.write_text` /
    `write_bytes` 在 CPython 里就是走 `self.open(...)` 的,一并盖住。
    底线之外的写法(裸 os.open)拦不住 —— 那正是②指纹存在的理由。
    """
    global _guard_installed
    if _guard_installed:
        return False
    import builtins
    import pathlib

    orig_open = builtins.open
    orig_path_open = pathlib.Path.open

    def _block(target, mode) -> None:
        if any(c in str(mode or "r") for c in "wax+") and _under_card_dir(target):
            raise LabWriteBlocked(
                f"实验台断路:拦下了一次对真卡片的写入 → {target} (mode={mode!r})。\n"
                "    台子禁止写 data/(它是你所有测量的底座)。真要看落盘,去真服务里看。\n"
                "    如果这是**新加的**落盘代码:那正是这道闸要拦的东西 —— "
                "见 prompt_lab/tool_recall.py §0.5。"
            )

    def guarded_open(file, mode="r", *a, **kw):
        _block(file, mode)
        return orig_open(file, mode, *a, **kw)

    def guarded_path_open(self, mode="r", *a, **kw):
        _block(self, mode)
        return orig_path_open(self, mode, *a, **kw)

    builtins.open = guarded_open                    # noqa: A001 (就是要盖住它)
    pathlib.Path.open = guarded_path_open
    _guard_installed = True
    return True


class CardFingerprint:
    """② 真卡片指纹:开局记一次,之后每次 `diff()` 都对一次。

    对的是"这个文件有没有被改过",不是"内容对不对" —— 800 KB 全量哈希
    (10 个文件)约几毫秒,一轮一对,代价可以忽略。
    """

    def __init__(self, root: Path = CARD_DIR) -> None:
        self.root = root
        self.files = self._scan()

    def _scan(self) -> dict[str, tuple[int, str]]:
        out: dict[str, tuple[int, str]] = {}
        if not self.root.exists():
            return out
        for p in sorted(self.root.rglob("*")):
            if p.is_file():
                try:
                    blob = p.read_bytes()
                except OSError:                     # 正在被别处写:下一轮再看
                    continue
                out[str(p.relative_to(self.root))] = (
                    len(blob), hashlib.sha1(blob).hexdigest())
        return out

    def diff(self) -> list[str]:
        """相对开局指纹的差异(空 = 一个字节都没动)。"""
        now = self._scan()
        out: list[str] = []
        for name in sorted(set(self.files) | set(now)):
            was, cur = self.files.get(name), now.get(name)
            if was is None:
                out.append(f"新增 {name}")
            elif cur is None:
                out.append(f"**被删** {name}")
            elif was != cur:
                out.append(f"**被改** {name}")
        return out

    def line(self) -> str:
        bad = self.diff()
        n = len(self.files)
        if not bad:
            return f"  ── 真卡片未动 ✓({n} 个文件指纹一致)· 写入已掐断 ──"
        return (f"  ── ✗ 真卡片**被改了**!{n} 个文件里这些不一样:{bad}"
                " —— 台子被动过,本轮数字作废 ──")


# 装在**导入时**而不是 main() 里:别的台子(check_transient.py 等)会直接
# `from tool_recall import Apparatus` 绕过 main() 去跑真轮 —— 那时闸也得在。
install_write_guard()


# ============================================================
# 1. 预设问题 —— 每条对着一个**已知的真事实**,不是随手写的
# ============================================================

# 真卡片 fffd3cf5 里的事实(见 docs/decisions/RECALL_MVP.md):
#   09-09 07:46 写日记,记的是拿铁凉了
#   09-09 20:44 小论文一口气写完
#   09-09 11:50 食堂菜单一直没定
#   09-15 10:09 灰色薄针织衫加进购物车,**没下单**
# 往事段窗口(最近 8 条 life)= 09-12 ~ 09-17,所以 09-09 那两件不在里面。
# 每条的括号是**这台子上你在看什么**,不是给她的话。
QUESTIONS: list[tuple[str, str]] = [
    ("你之前说早上在写日记,都记了些什么呀", "自造日期高危"),
    ("今天天气怎么样", "不该调"),
    ("你还记得前天中午吃的什么吗", "那天没记录"),
    ("你上次说小论文写完了,是怎么写完的?", "在窗口外,必须查"),
    ("我上次给你推荐的那家店叫什么来着?", "库里根本没有"),
    ("你现在在做什么呀?", "不该调"),
    ("陪我聊会儿吧", "不该调"),
    ("你购物车里那件灰色针织衫后来下单了吗?", "材料没写「下单」"),
]


# ============================================================
# 2. 文案变体
# ============================================================
# 变体的「名字 → 内容」映射全在 `test/recall_probe.py`(**单一来源**,本台不另抄):
#     `P.pick_variant(name)`   名字或前缀 → 变体键(容错逻辑也在那儿)
#     `P.variant_parts(key)`   → (description, usage, scope 参数说明),后两项是两条通道
# 原先这里有两个纯转发包装(`resolve_variant` / `variant_of`,函数体就是
# `return P.xxx(...)`),只是把 `P.*` 改了个名字、自己不带任何逻辑 —— 已删,
# 调用处直接调 `P.*`,少一层"这函数到底干了什么"的追查成本。


# ============================================================
# 3. 装配 —— 真引擎 + 产品里还没有的件
# ============================================================

class Apparatus:
    """把 recall(和可选的往事段)接进真实引擎。

    接线顺序有讲究,**不能动**:

      ① `eng._tools.register(recall)` 必须在 `_build_engine` **之前**。
         AgentLoop 只在**构造时**快照一遍 retain_result(core/loop.py 的
         `AgentLoop.__init__` 里 `self._retained = {...}` 那一段;
         旧注释写的 `core/loop.py:84-87` 已被同文件的重构撑开、不再准确),
         晚注册的工具痕迹在后轮会被折掉,血缘就从视图里消失了。
         这也是 `engine.py` 里那条"工具在**模块加载时**注册一次,llm 靠可变句柄
         晚绑定"的约束的同一条(按文字找,旧注释写的 `engine.py:86-88` 已漂到无关内容)。

      ② 往事段不重建 composer,而是 `register` 进引擎已经建好的那几只
         (eng._composers)。因为 sys_by_source 闭包捕获的就是这几个对象,
         所以**真轮**与台上打印用的是同一批段 —— 打印的绝不会和发出去的不一样。
    """

    def __init__(self, variant: str, *, memory: bool = True,
                 solo: bool = False, view: str = "tools") -> None:
        cfg = llm_setup.load_runtime(eng.DATA_DIR)
        if not cfg or not cfg.get("api_key") or not cfg.get("base_url"):
            print("[llm] 未找到运行时连接(data/llm.local.json):"
                  "请先在 UI 完成「连接你的模型」。")
            raise SystemExit(1)

        # 进程内会话卡,不落盘(退出即弃) —— 台子绝不写 data/。
        self.log = SessionLog("prompt-lab")
        # 系统那只钟当场拨到与工具同一个刻度(`engine.py` 的 `_clock_override`
        # 就是产品里"现在几点"的唯一来源,普通轮/世界段/时间线全读它;
        # 旧注释写的 `engine.py:695-706` 已漂,按符号找)。
        # **log 的时间游标必须一起拨** —— 真人消息进上下文时带的 [HH:MM] 戳走的是
        # 日志事件自己的时间(`core/session_log.py` 的 `derive_messages(...,
        # user_time_prefix=...)` 里 `_stamp_prefix(..., event.time)` 那两行;
        # 旧注释写的 `core/session_log.py:385` 已漂到这个函数之外了),
        # 只拨 _clock_override 的话
        # SYSTEM 说 09-18、消息戳却写真实墙钟(实测:系统 09-18 22:10,消息戳 09-20
        # 18:23,她会以为主人从两天后发来的)—— 两套刻度就是这类鬼影的来源。
        self.now = float(DEFAULT_NOW)
        self.set_clock(self.now)

        self.variant = variant
        self.memory_on = memory
        self.solo = solo
        # 三档视图(菜单 x 循环):
        #   quiet 只有她的话 + 一行小结
        #   tools **默认**:再加「⚙ 她产了什么参数」和「↳ 工具返回了什么」
        #   full  再加 SYSTEM 的段拆分 + 每次 LLM 调用的 messages 输入
        self.view = view if view in ("quiet", "tools", "full") else "tools"
        self.calls: list[dict] = []     # 本轮她调 recall 的参数
        self.results: list[dict] = []   # 对应的结构化结果(四态/命中)

        # 开局指纹:在**读卡之前**立基线,之后每轮对一次(§0.5 ②)
        self.card = CardFingerprint()

        print("读真卡片…", end="", flush=True)
        self.rows, _cleaned = P.collect_memory(P.collect_turns(P.load_log()))
        self.embedder = P.get_embedder()
        self.conn = P.build_db(self.rows, self.embedder)
        print(f" {len(self.rows)} 条往事 · 语义路 {getattr(self.embedder, 'name', '?')}")

        # 与 recall_e2e.py 的 `build_apparatus()` 里那组参数一致(不设 min_score:
        # 阈值当相关性判据已被实测证伪,见 recall_probe.py 的 `_FLOOR` 那一段注释,
        # 只留工具内部那道地板)。
        # verbose=False:明细由本台自己打,这样它才跟着 `x` 开关走。
        self.tool = P.make_recall_tool(
            self.conn, limit=2, embedder=self.embedder,
            now_fn=lambda: self.now, verbose=False,
        )
        self.set_variant(variant)

        # 记下每次调用的参数与结构化结果(小结/明细都靠它,不再重复跑检索)
        inner = self.tool.func

        def _tap(args):
            args = dict(args or {})
            self.calls.append(args)
            try:
                r = self.tool.run_structured(args)
            except Exception as exc:  # noqa: BLE001
                r = {"state": "?", "why": f"{type(exc).__name__}", "items": [],
                     "nearby": [], "query": args.get("query"),
                     "scope": args.get("scope")}
            self.results.append(r)
            return inner(args)

        self.tool.func = _tap

        # ══════════════════════════════════════════════════════════════════
        # 🚨🚨 **本台当前的仪器是断的 —— 变体切换与 ⚙/↳ 两档视图都在空转** 🚨🚨
        # (2026-09-22 查明;**逻辑一字未改**,拍板权留给用户。修法见段末。)
        #
        # ① 现象(你会看到什么)
        #      · 敲 `v` 换文案变体,菜单头确实变了、`--list` 也列得出来,
        #        但**模型收到的永远是"★定稿"那份文案** —— 换变体不影响她怎么答。
        #      · `⚙ 她产了什么参数` / `↳ 工具返回了什么`两档(tools / full 视图)
        #        **永远是空的**,轮末小结**永远**打印「她没调 recall」——
        #        哪怕她其实调了。
        #      · `p 看 SYSTEM` 打的是**产品版**工具文案,而菜单头显示的是**变体**名
        #        —— 同一屏两份不同的话,容易误判成"变体没生效"。
        #
        # ② 证据(为什么必然如此,两步都成立)
        #      (a) 产品那件 recall 在**模块导入时**就注册好了:
        #          `server/app/engine.py` 里有模块级语句
        #          `_tools.register(make_recall_tool(recall_index))`
        #          (与同文件"工具在模块加载时注册一次"那条约束同源)。
        #          而本文件开头 `from server.app import engine as eng` 在本模块
        #          **导入时**就执行 → 等 `Apparatus.__init__` 走到下面那个 `if`,
        #          `eng._tools.names()` 里**已经有** "recall" → **`if` 恒为假**
        #          → 台子自己那件(连着真卡片 sqlite + 真嵌入器的)**永远进不了注册表**。
        #      (b) 产品版的检索口 `recall_index()` 读 `_recall_sid["sid"]`,而
        #          `_recall_sid` **只有 turn worker 设** —— `engine.py` 的
        #          `_turn_worker_loop` 在从 `_turn_queue` 取到一项后才写它、
        #          `finally` 里还原。而台子直接调 `eng._loop.run_turn`
        #          (见本文件下方 `run_turn()`),**绕过了 worker** →
        #          `_recall_sid["sid"]` 恒 `None` → `recall_index()` 返回 `None`
        #          → 产品那件工具**恒返回** `R_DOWN / "no_index"`
        #          ("这会儿想不起来了 —— 检索没能跑起来")。
        #      连带:`self.tool.func = _tap`(下面几行)装在那件**没进注册表**的
        #      工具上 → `_tap` 永不触发 → `app.calls` / `app.results` 恒空
        #      → `summary()` 打着"她没调 recall",而真相是"仪器没接上"。
        #
        # ③ 真要测变体,必须先做这两件事(否则测的永远是定稿)
        #      (i)  **别让产品那件顶包**:走 `run_turn(tools=...)` 这个**公开**口子
        #           (台子的 `solo_reg` 已经在用这条路),传一个**含台子自己那件**的
        #           注册表。注意 `core/tools.py` 的 `ToolRegistry` **没有
        #           `unregister`**(只有 register/get/names/schemas/usage_entries/
        #           execute),所以"把产品那件从 eng._tools 里摘掉"没有干净 API,
        #           传子集是唯一的正路。
        #      (ii) **在跑轮前手动设 `_recall_sid`**:照 `_turn_worker_loop` 的做法
        #           `eng._recall_sid["sid"] = P.SID`,跑完还原(否则它一直是 None)。
        #      ⚠ 做完这两件事 = **换掉本台的测试底座**:探针那件直连 SQL
        #      (现算余弦 + BM25),产品这件走 `core.memory.MemoryIndex` +
        #      `core.memory_cache`。打分口径设计上一致,但"真卡片全量往事"的
        #      取数路径就变了 —— **这是换底座,得用户点头,不是顺手能改的**。
        #
        # ④ 当初为什么这么写 —— ⚠ 这一条是**对作者意图的推断,不是查证**(git 里
        #      没留说明),但可查证的部分都在:
        #      这行 `if "recall" not in eng._tools.names()` 看起来是想写"别重复注册",
        #      因为两件 recall 确实**同名** —— `character/tools.py` 与
        #      `test/recall_probe.py` 各自都有 `RECALL_NAME = "recall"`,
        #      而 `ToolRegistry.register` 对重名是**直接抛 ValueError**
        #      (core/tools.py 的 register)。大概是以为产品那件要等某个装配时机
        #      才出现,于是加了这道判断 —— 但它其实是**导入时**就坐进去的,
        #      于是这道判断从"防冲突"变成了"永远不注册我们的"。
        #      → 可查证的结论是**条件写反了**(想写"产品没注册才注册我们的",
        #      实际效果是"产品已注册所以我们永不注册");意图那半属推断。
        # ══════════════════════════════════════════════════════════════════
        # ① 先注册工具,再建引擎(见类文档)
        if "recall" not in eng._tools.names():   # 🚨 恒为假,见上(②-a)
            eng._tools.register(self.tool)
        eng._build_engine(cfg)
        # 原先这里还有 `self.cfg = cfg`:一个字都没被读过(全仓 grep 无引用),
        # 是写死的属性 —— 已删,别再加回来。"本轮连接"要看就看库里的 `cfg` 局部量。

        # ② 往事段(默认不挂,见模块开头的说明)
        self.mem_sec = None
        # `recent` = 往事段真正写进去的那几条 life —— 是 `make_memory_section`
        # 的**第二个**返回值(默认取最近 8 条 life),空列表 = 该段不出现。
        # 原先这个属性只被赋值、从来没被读过(三处全是赋值);菜单头那行
        # "往事段 开/关(N 条 life)"就是这次给它补上的用途 —— 有它在,
        # `--no-memory` 与"卡里根本没有独处记录"这两种情况才分得开。
        self.recent: list = []
        if memory:
            self.mem_sec, self.recent = P.make_memory_section(self.rows, boundary=True)
            for key in ("chat", "self", "backfill"):
                composer = eng._composers.get(key)
                if composer is not None and "memory" not in composer.names():
                    composer.register(self.mem_sec)

        # ③ 隔离档:本轮只开放 recall + change_outfit,**摘掉 launch_subagent**。
        #    理由与 recall_e2e.py 的 `build_apparatus()` 同:工具竞争是阶段一另外
        #    测过的变量,
        #    要问"记忆文案好不好"就不该让联网工人来搅。
        #    用 `run_turn(tools=...)` 这个**公开**口子(本轮开放子集),不动注册表。
        self.solo_reg = ToolRegistry([
            t for t in (eng._tools.get(n) for n in ("change_outfit", "recall"))
            if t is not None
        ])

    def registry(self):
        """本轮真正开放的注册表(隔离档 = 只有 recall + change_outfit)。"""
        return self.solo_reg if self.solo else eng._tools

    def sections(self, source: str = "user") -> list[tuple[str, str]]:
        """按段渲染 SYSTEM —— 用**本轮实际开放**的注册表。

        为什么不直接调 eng.system_component_sections:它**固定读全局 `eng._tools`**
        (`engine.py` 里那个只读观测口,产品注释说这是有意的)。本轮传了工具子集时,
        它会把"没开放的工具"也念出来,
        台上打印的就跟实际发出去的不是一回事了 —— 那正是这个台子最不该有的谎。
        """
        key = "self" if source == "self" else "chat"
        composer = eng._composers.get(key)
        if composer is None:
            return []
        values = {**PERSONAS.VALUES, "registry": self.registry(),
                  "log": self.log, "now_epoch": self.now}
        out = []
        for sec in composer.sections():
            text = sec.render(values)
            if text and text.strip():
                out.append((sec.name, text))
        return out

    # ---------- 视图档 ----------

    @property
    def show_tools(self) -> bool:
        """打不打「⚙ 工具调用(参数)」与「↳ 工具返回」。"""
        return self.view in ("tools", "full")

    @property
    def show_detail(self) -> bool:
        """打不打 SYSTEM 段拆分与每次 LLM 调用的输入。"""
        return self.view == "full"

    def cycle_view(self) -> str:
        order = ["quiet", "tools", "full"]
        self.view = order[(order.index(self.view) + 1) % len(order)]
        return self.view

    # ---------- 变体 / 时钟 / 往事段 ----------

    def set_variant(self, name: str) -> str:
        key = P.pick_variant(name) or "★定稿"
        desc, usage, scope_desc = P.variant_parts(key)
        self.tool.description = desc
        # ⚠ 台子这件的 usage 是**台子自己贴的**:`test/recall_probe.py` 的
        #    `make_recall_tool` 故意不自带 usage(它那儿写着"进 SYSTEM 段落,
        #    由装配处贴"),不贴这里 [可用工具用法] 段就是空的。
        #    **别照旧注释("与 engine 一致")理解 —— 那句说反了**:产品版的
        #    `character/tools.py` 里 `make_recall_tool` 是**自带**
        #    `usage=RECALL_USAGE` 的,装配处 engine 一个字都不贴
        #    (全仓 `.usage =` 的赋值点里没有 `server/` 的代码)。
        #    两边贴法不同,但值同源 —— 产品那份是 2026-09-21 从探针毕业时
        #    **一字未改**搬过去的(见 `character/tools.py` 那段头注释),
        #    不是各写各的。
        self.tool.usage = usage
        # 参数说明进 schema,与 usage 是**两条不同的通道** —— 变体可以只改这一条
        self.tool.parameters["properties"]["scope"]["description"] = scope_desc
        self.variant = key
        return key

    def set_clock(self, ts: float) -> None:
        """拨"现在" —— 三个刻度**同时**改,不留两套:
        系统 [当前时间](eng._clock_override)、日志事件戳(log 游标)、工具的钟。"""
        self.now = ts
        eng._clock_override["ts"] = ts
        self.log.set_time_cursor(ts)

    def reset_log(self) -> None:
        """清空对话(记忆库不动 —— 它是真卡片,不归台子管)。"""
        self.log = SessionLog("prompt-lab")
        self.log.set_time_cursor(self.now)

    def set_memory(self, on: bool) -> None:
        """往事段开关(同一个 SystemSection 对象注册进三只 composer)。"""
        if on and self.mem_sec is None:
            self.mem_sec, self.recent = P.make_memory_section(
                self.rows, boundary=True)
        for key in ("chat", "self", "backfill"):
            composer = eng._composers.get(key)
            if composer is None:
                continue
            if on and "memory" not in composer.names():
                composer.register(self.mem_sec)
            elif not on and "memory" in composer.names():
                composer.unregister("memory")
        self.memory_on = on

    def variant_chars(self) -> tuple[int, int]:
        return len(self.tool.description), len(self.tool.usage)

    def check_card(self) -> str:
        """核对真卡片指纹(§0.5 ②),返回要打的那一行。每轮末叫一次。"""
        return self.card.line()

    # ---------- 只看不跑 ----------

    def show_system(self, source: str = "user") -> None:
        """SYSTEM 组件拆分 + 真喂模型的 messages。零 LLM 调用。"""
        self.set_clock(self.now)
        print(f"\n── SYSTEM 组件拆分(引擎真实装配 · 变体 {self.variant}"
              f" · 本轮工具 {self.registry().names()})──")
        for name, text in self.sections(source):
            print(f"  ◆ [{name}]")
            for line in (text or "").splitlines():
                print(f"      {line}")
        msgs = eng._loop._build_messages(
            self.registry(), source, self.log, max_rounds=None,
            system_prompt=None)
        print("── messages 实际发送 ──")
        for m in msgs:
            body = _blocks_text(m.get("content")) or ""
            body = body if len(body) <= 800 else body[:800] + "…"
            print(f"  [{m.get('role')}] {body}")
        self.show_tool_prompts()

    def show_tool_prompts(self) -> None:
        """把**在玩的每一件工具的原文**摊开:description 与 usage 各是谁。

        两件东西分属两条通道(core/tools.py 的 `Tool` docstring):
            description -> 进 tools[] 的 schema
            usage       -> 经 make_usage_section 进 SYSTEM 的 [可用工具用法] 段
        看这份就能回答"她那个参数是从哪句话来的"。
        """
        print("\n══ 全部工具的原文(她看得见的就这些)══")
        for name in self.registry().names():
            t = self.registry().get(name)
            print(f"\n── {name} ──")
            print(f"  [schema] description({len(t.description)} 字):")
            for line in (t.description or "(空)").splitlines():
                print(f"     {line}")
            params = (t.parameters or {}).get("properties") or {}
            for pname, spec in params.items():
                print(f"  [schema] 参数 {pname}: "
                      f"{spec.get('description', '(无说明)')}")
            print(f"  [SYSTEM] usage({len(t.usage or '')} 字):")
            for line in (t.usage or "(空 —— 本工具不进 [可用工具用法] 段)").splitlines():
                print(f"     {line}")

        # 工人的 SYSTEM 不在她的 SYSTEM 里 —— 那是另一次运行、另一条通道
        # (core/subrun.py 不带人格,SYSTEM 由内容层给 —— 就是下面这个
        #  PERSONAS.SUBAGENT_SYSTEM,由 engine.py 拼进工人那次运行)。
        print("\n══ 工人(子代理)自己的 SYSTEM —— 她派出去的活跑在这里 ══")
        for line in (PERSONAS.SUBAGENT_SYSTEM or "").splitlines():
            print(f"   {line}")
        print("   --- [步数预算](引擎填 {steps}) ---")
        for line in (PERSONAS.SUBAGENT_BUDGET_TEMPLATE or "").splitlines():
            print(f"   {line}")


# ============================================================
# 4. 展示
# ============================================================

# ⚠ 与 turn_lab.py 的 `_blocks_text` 是**同一件(逐字)** —— 两处各有一份,
#   改一处必须改另一处(故意没抽公共模块:不许新建顶层目录/文件)。
def _blocks_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            b.get("text", "") for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        )
    return ""


# ⚠ 与 turn_lab.py 的 `_fmt_ts` **同源但不逐字**:这儿是 `%Y-%m-%d %H:%M`,
#   turn_lab 那份是 `%m-%d %H:%M`(少了年份)。改一处前先确认那个差异是不是故意的。
def _fmt_ts(ts: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))


# ⚠ 与 turn_lab.py 的 `_InputPrintProxy` 是**同一件(逐字)** —— 两处各有一份,
#   改一处必须改另一处(同上,不抽公共模块)。
class _InputPrintProxy:
    """每次真实 LLM 调用前回调一次(只改显示,不碰内核)。"""

    def __init__(self, inner, on_call) -> None:
        self._inner = inner
        self._on_call = on_call
        self.model = getattr(inner, "model", None)

    def stream(self, messages, tools=None, temperature=None, max_tokens=None,
               model=None):
        self._on_call(messages)
        return self._inner.stream(
            messages, tools=tools, temperature=temperature,
            max_tokens=max_tokens, model=model)

    def invoke(self, messages, tools=None, temperature=None, max_tokens=None,
               model=None):
        self._on_call(messages)
        return self._inner.invoke(
            messages, tools=tools, temperature=temperature,
            max_tokens=max_tokens, model=model)


def _console_cb(log, shown: list[int], show_tools: bool = True):
    """流式打字 + 按日志顺序补打工具调用与返回。

    工具是引擎在**步与步之间**同步执行的:调用与结果先落日志(tool/call →
    tool/result),下一步的输出回调才到 —— 所以在每次出字前先扫一遍日志,
    把新增的调用与返回按真实顺序打出来,控制台的次序才是真的
    (与 turn_lab.py 的 _make_console_cb 同一手法)。

    工具返回后要重新起一个「她: 」前缀,否则接着流出来的字会挂在工具块下面,
    看着像工具输出的一部分。

    同一步里她可以**一次声明好几个**调用(引擎并发跑,见 core/loop.py 的
    `_execute_tools` —— 那里用 `ThreadPoolExecutor` 并行跑,barrier 收齐后
    按声明顺序落日志:先落 N 条 tool/call,再落 N 条 tool/result,
    两边都带同一个 call id)。
    所以两条都编上号,否则「哪个返回对应哪个调用」看不出来。
    """
    state = {"need_prefix": True, "labels": {}, "n": 0}

    def flush_log_events() -> None:
        for e in log.events:
            if e.seq <= shown[0]:
                continue
            shown[0] = e.seq
            if not show_tools:
                continue
            if e.type == "tool/call":
                state["n"] += 1
                state["labels"][e.data.get("call_id") or ""] = state["n"]
                raw = e.data.get("arguments", "")
                try:
                    args = json.loads(raw) if raw else {}
                except (TypeError, ValueError):
                    args = raw
                pretty = (json.dumps(args, ensure_ascii=False)
                          if not isinstance(args, str) else args)
                print(f"\n\n  ⚙ [{state['n']}] {e.data.get('name', '')} 参数 → "
                      f"{pretty}", flush=True)
                state["need_prefix"] = True
            elif e.type == "tool/result":
                n = state["labels"].get(e.data.get("tool_call_id") or "", "?")
                body = _blocks_text(e.data.get("content")) or ""
                tag = "✗ 工具报错" if e.data.get("is_error") else "↳ 工具返回"
                print(f"\n  {tag} [{n}]:", flush=True)
                for line in (body.splitlines() or [""]):
                    print(f"     {line}", flush=True)
                state["need_prefix"] = True

    def cb(chunk: dict) -> None:
        if chunk.get("kind") == "text":
            flush_log_events()
            if state["need_prefix"]:
                print("\n\n  她: ", end="", flush=True)
                state["need_prefix"] = False
            print(chunk.get("text", ""), end="", flush=True)

    cb.flush = flush_log_events
    return cb


def _one_line(text: str, limit: int = 46) -> str:
    """压成一行(`limit` 字后截断)—— 命中原文的紧凑视图。

    默认值原先是 70,而两个调用点**都显式传了 46** → 70 从来没生效过,
    是句误导。现在默认就是实际在用的 46,调用点不必再传。
    """
    s = " ".join((text or "").split())
    return s if len(s) <= limit else s[:limit] + "…"


def _print_detail(app: Apparatus) -> None:
    """细节视图:每次调用的参数 + 工具 SQL + 命中原文。

    ⚠ 签名里**没有 user_msg** —— 原先有,但函数体从不引用它(那个参数是从
    `summary(app, user_msg)` 抄签名时带过来的,而 summary 是真用 user_msg 的)。
    别再加回去。
    """
    for i, (args, r) in enumerate(zip(app.calls, app.results), 1):
        print(f"\n  ── 第 {i} 次 recall 的细节 ──")
        print(f"  参数     : {json.dumps(args, ensure_ascii=False)}")
        if r.get("day"):
            print(f"  认出日期 : {r['day']} → 元数据过滤"
                  f"(送去 embed 的是 {r.get('sem_q')!r})")
        print(f"  SQL      : {r.get('sql') or '(没跑到 SQL)'}")
        print(f"  状态     : {r['state']}"
              + (f" ({r['why']})" if r.get("why") else ""))
        for it, sc in zip(r.get("items") or [], r.get("scores") or []):
            print(f"    · {sc:.3f}  [{it['kind']:4}] {it['time']}  "
                  f"{_one_line(it['text'])}")
        for it in r.get("nearby") or []:
            print(f"    ~ (相邻)   [{it['kind']:4}] {it['time']}  "
                  f"{_one_line(it['text'])}")


def summary(app: Apparatus, user_msg: str) -> list[str]:
    """轮末小结 —— **阶段一的判据当场念出来**,尽量短。"""
    if not app.calls:
        return ["  ── 她没调 recall ──"]

    # 问句里本来有没有日期 = 她写日期算不算自造(与 router 探针的 date_ok 同判据)
    asked_day = P.extract_day_range(user_msg, app.now)
    total = len(app.calls)
    lines = [f"  ── 她调了 recall"
             + (f" ×{total} ──" if total > 1 else " ──")]
    for i, (args, r) in enumerate(zip(app.calls, app.results), 1):
        tag = f"  ({i}/{total})" if total > 1 else ""
        lines.append(f"     query={str(args.get('query') or '')!r}  "
                     f"scope={args.get('scope')!r}{tag}")
        near = r.get("nearby") or []
        tail = r["state"] + (f" ({r['why']})" if r.get("why") else "")
        tail += f" · 命中 {len(r.get('items') or [])} 条"
        if near:
            tail += f" · 另附相邻 {len(near)} 条(结果里明说不是那天的)"
        lines.append(f"     → {tail}")
        got = P.extract_day_range(str(args.get("query") or ""), app.now)
        if got and not asked_day:
            lines.append(f"     ⚠ **自造日期**「{got[2]}」—— 你这句话里没有日期,"
                         "系统会拿它去过滤")
        elif got:
            lines.append(f"     日期「{got[2]}」(你问句里就有,不算自造)")
    return lines


def run_turn(app: Apparatus, user_msg: str) -> None:
    """用**真实引擎 loop**跑一轮(user 轮),LLM 报错只报一句,不炸台。"""
    log = app.log
    app.calls.clear()
    app.results.clear()
    app.set_clock(app.now)
    print(f"\n你: {user_msg}")
    real_llm = eng._loop.llm
    calls = [0]

    def on_call(messages):
        flush = getattr(cb, "flush", None)
        if flush:
            flush()
        calls[0] += 1
        if not app.show_detail:
            return
        print(f"\n  ┈┈ 第 {calls[0]} 次真实调用 ┈┈", flush=True)
        if calls[0] == 1:
            for name, text in app.sections("user"):
                print(f"  ◆ [{name}] {text}")
        for m in messages[-3:]:
            if m.get("role") == "system":
                continue      # 上面已按段拆开打过,这儿再打一遍是纯噪音
            body = _blocks_text(m.get("content")) or ""
            body = body if len(body) <= 500 else "…" + body[-500:]
            print(f"  [{m.get('role')}] {body}")

    cb = _console_cb(log, [log.events[-1].seq if log.events else -1],
                     app.show_tools)
    eng._loop.llm = _InputPrintProxy(real_llm, on_call)
    try:
        eng._loop.run_turn(user_input=user_msg, source="user", log=log,
                           on_chunk=cb, max_rounds=None,
                           tools=app.registry())
    except Exception as exc:  # noqa: BLE001
        print(f"\n  ✗ 这一轮崩了: {type(exc).__name__}: {exc}")
    finally:
        eng._loop.llm = real_llm
        cb.flush()
    print()
    if app.show_detail:
        _print_detail(app)
    for line in summary(app, user_msg):
        print(line)
    # 每轮核一次真卡片指纹:这一行是"台子没弄脏你的底座"的**当场证据**
    print(app.check_card())


# ============================================================
# 5. 菜单
# ============================================================

def menu(app: Apparatus) -> None:
    d, u = app.variant_chars()
    print("\n" + "─" * 62)
    print(f" 她 · recall 文案台      {app.variant}({d + u} 字) · {_fmt_ts(app.now)}")
    print(f" 往事段 {'开' if app.memory_on else '关'}({len(app.recent)} 条 life"
          + ("" if app.recent else " → 段不出现") + ") · "
          f"工具 {app.registry().names()}"
          + ("  ← 隔离档" if app.solo else "")
          + f" · 卡片 {P.SID[:8]}  · 视图 {app.view}")
    print(f" 断路 data/ {'已掐断' if _guard_installed else '⚠未装'}"
          f" · 真卡片 {len(app.card.files)} 文件"
          + ("未动 ✓" if not app.card.diff() else "**被改了** ✗"))
    print("─" * 62)
    for i, (q, why) in enumerate(QUESTIONS, 1):
        print(f"  {i} {q}   ({why})")
    print("─" * 62)
    print("  编号 = 问这句      t 自己说一句")
    print("  v 换文案   d 拨现在   m 往事段开关   l 隔离档开关")
    print("  p 看 SYSTEM(0花费)  x 视图三档(quiet/tools/full)  c 清空  q 退出")


def _pick_variant(app: Apparatus) -> None:
    keys = list(P.RECALL_VARIANTS)
    print("\n可选变体(敲编号或名字前缀):")
    for i, k in enumerate(keys, 1):
        d, u, _s = P.variant_parts(k)
        mark = " ←现在" if k == app.variant else ""
        print(f"  {i:>2} {k}  ({len(d) + len(u)} 字){mark}")
    raw = input("> ").strip()
    if not raw:
        return
    # ⚠ 越界编号必须**当场说清"没换"**:原先 `0` / `99` 会静默走 `P.pick_variant`
    # 拿不到 → `set_variant` 回落 "★定稿" → 你下一次跑出来的就是定稿,
    # 而你会以为"这个变体和定稿一样"。那是**假结论**,比报错坏得多。
    if raw.isdigit():
        n = int(raw)
        if not 1 <= n <= len(keys):
            print(f"编号要在 1~{len(keys)} 之间(敲 `{raw}` 无效)—— "
                  "变体**没有换**,当前仍是你原来那个,别拿这轮当新变体的证据。")
            return
        name = keys[n - 1]
    else:
        name = raw
    print(f"变体 → {app.set_variant(name)}")


def _pick_clock(app: Apparatus) -> None:
    print(f"\n现在 = {_fmt_ts(app.now)}")
    print("  格式: 09-16 21:00 / 2026-09-16 21:00 / +2h(往后挪两小时)")
    raw = input("> ").strip()
    if not raw:
        return
    if raw.startswith("+"):
        try:
            hours = float(raw[1:].rstrip("hH"))
        except ValueError:
            print("没看懂"); return
        app.set_clock(app.now + hours * 3600)
        print(f"现在 → {_fmt_ts(app.now)}")
        return
    for fmt in ("%Y-%m-%d %H:%M", "%m-%d %H:%M"):
        try:
            dt = datetime.strptime(raw, fmt)
            if fmt == "%m-%d %H:%M":
                dt = dt.replace(year=time.localtime(app.now).tm_year)
            app.set_clock(dt.timestamp())
            print(f"现在 → {_fmt_ts(app.now)}")
            return
        except ValueError:
            continue
    print("没看懂")


def main() -> None:
    ap = argparse.ArgumentParser(description="回忆工具 · 文案实验台")
    ap.add_argument("--variant", default="★定稿", help="文案变体(名字或前缀)")
    ap.add_argument("--ask", default=None, help="只问一句就退出")
    ap.add_argument("--preview", action="store_true", help="只看 SYSTEM,不调模型")
    ap.add_argument("--no-memory", action="store_true",
                    help="不挂往事段(默认挂 —— 往事段是 SYSTEM 里的一个位置)")
    ap.add_argument("--solo", action="store_true",
                    help="隔离档:本轮只开放 recall+change_outfit(默认全量)")
    ap.add_argument("--view", default="tools",
                    choices=["quiet", "tools", "full"],
                    help="视图档:quiet 只有她的话 / tools(默认)加工具参数与返回 "
                         "/ full 再加 SYSTEM 与每次调用输入")
    ap.add_argument("--list", action="store_true", help="列出文案变体")
    args = ap.parse_args()

    if args.list:
        for k in P.RECALL_VARIANTS:
            d, u, s = P.variant_parts(k)
            print(f"{k}\n  desc  ({len(d)}): {d}\n  usage ({len(u)}): {u}"
                  f"\n  scope 参数说明: {s}\n")
        return

    app = Apparatus(args.variant, memory=not args.no_memory, solo=args.solo,
                    view=args.view)
    print(f"已接进真引擎 · 变体 {app.variant} · 工具集 {eng._tools.names()}")
    print(f"断路:data/ 写入已掐断(装到内置 open 上)· "
          f"真卡片基线 {len(app.card.files)} 个文件")

    if args.preview:
        app.show_system()
        return

    if args.ask:
        run_turn(app, args.ask)
        return

    print("\n小夜子 · recall 文案台 —— 台上是手感不是数字,结论请去 router 探针跑 reps")
    while True:
        menu(app)
        try:
            choice = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见")
            return
        low = choice.lower()
        if low in ("q", "quit", "exit"):
            print("再见")
            return
        if low == "t":
            msg = input("你对她说: ").strip()
            if msg:
                run_turn(app, msg)
        elif low == "v":
            _pick_variant(app)
        elif low == "d":
            _pick_clock(app)
        elif low == "m":
            app.set_memory(not app.memory_on)
            print(f"往事段 → {'开' if app.memory_on else '关'}")
        elif low == "l":
            app.solo = not app.solo
            print(f"本轮工具 → {app.registry().names()}"
                  + ("(隔离档)" if app.solo else "(真产品全量)"))
        elif low == "x":
            print(f"视图 → {app.cycle_view()}"
                  "   (quiet 只有她的话 / tools 加工具参数与返回 / "
                  "full 再加 SYSTEM 与每次调用输入)")
        elif low == "p":
            app.show_system()
        elif low == "c":
            app.reset_log()
            print("对话已清空(记忆库不动 —— 它是真卡片,不归台子管)")
        elif choice.isdigit() and 1 <= int(choice) <= len(QUESTIONS):
            run_turn(app, QUESTIONS[int(choice) - 1][0])
        else:
            print("?")


if __name__ == "__main__":
    main()
