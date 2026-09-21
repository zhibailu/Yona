"""Yona 应用层 · 组合根与生活运行时(engine)

布局 B(2026-09 用户拍板):本模块 = 组合根 —— 把支撑库(store/rhythm)、
内核(core)、角色(character)装配成"这个进程唯一的那一份",并管
start()/stop() 生命周期。同时收着"生活运行时":心跳启动、离线补写装配
(规则类 ServerGate 已拆去 gate.py;LifeLoop/补写链与引擎接线紧,暂留)。

router(server/app/api/*)通过 `from .. import engine` 在**运行时**取
`engine.xxx`,不在 import 时解包(因为 lifespan 启动后才赋值)。
"""

from __future__ import annotations

import itertools
import os
import queue as _queue
import threading
import time
from collections import deque
from pathlib import Path

from .llm_setup import load_runtime

from character.persona import build_small_night_composer
from character import personas as personas_mod  # noqa: E402 文案(内容层);装配时现取属性,
# 不用 from-import 绑死 —— 改文案后 reload 模块 + 重建引擎即生效(2026-09)
from character.state import CharacterState
from character.tools import (
    make_change_outfit_tool, make_launch_subagent_tool, make_recall_tool,
)
from core.composer import SystemSection, make_timeline_section  # noqa: E402
from core import embed as embed_mod
from core.heartbeat import Heartbeat
from core.loop import AgentLoop
from core.memory import MemoryIndex, rows_from_events
from core.memory_cache import MemoryCache
from core.openai_compat import OpenAICompatibleLLM
from core.session_log import SessionLog
from core.subrun import SubRunSpec, execute as execute_subrun
from core.tools import ToolRegistry

from ..rhythm import LifeSampler
from ..store import SessionStore
from .gate import ServerGate
from .worker_tools import make_read_only_tools

# 产品语义参数唯一来源 = server/params.py(带拍板状态;查看: py server/params.py)
from ..params import (  # noqa: E402
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
    MEMORY_CACHE_DIRNAME,
    MEMORY_POLL_SEC,
    MEMORY_DEBT_MAX_WAIT_SEC,
    SELF_WAKES_PER_DAY,
    SUBAGENT_FILE_ROOT,
    SUBAGENT_MAX_STEPS,
    SUBAGENT_OUTPUT_MAX_TOKENS,
    WAKE_AFTER_GAP_SECONDS,
)

# ---------- 路径 ----------
ROOT = Path(__file__).resolve().parent.parent.parent
# 数据目录:默认 data/;YONA_DATA_DIR 可指到独立目录(验证/演示不脏真实数据)。
DATA_DIR = Path(os.environ.get("YONA_DATA_DIR", str(ROOT / "data")))

# 记忆索引缓存:顶层 cache/(用户 2026-09-21 拍板 —— 它是从 chat.log 派生的
# **可重建**产物,放 data/ 会让人以为它要备份)。与 DATA_DIR 一起可被
# YONA_DATA_DIR 之外的环境变量单独指走,验证/演示不脏真实缓存。
MEMORY_DIR = Path(os.environ.get(
    "YONA_CACHE_DIR", str(ROOT / MEMORY_CACHE_DIRNAME)))

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

# ---------- 子代理(工人身份的 loop 复用)装配(2026-09-16,用户批准) ----------
# 她看不见工人包里有什么工具、什么参数;她只看得见"这包能干什么"(一句散文,
# 由下面 _worker_capabilities() 从**真正接进去的工具**生成,不手抄)。
# 给工人用哪些工具 = 这里算,不是她挑 —— 与 dsh 的 toolFilter 同一位置。
#
# 三条约束都是实测过的,**顺序不能动**(证据 test/test_subagent_wiring.py):
#   ① 工具在**模块加载时**注册一次,llm 靠可变句柄晚绑定。因为 _build_engine
#      会重跑(下面三条调用点 + turn_lab),而 ToolRegistry.register 撞重名会抛
#      「已注册」(core/tools.py:48)—— 写在 _build_engine 里会炸在"用户换连接"这条路上。
#   ② 注册必须发生在 _loop 构造**之前**:AgentLoop 只在构造时快照一遍
#      retain_result(core/loop.py:75-78),晚注册的痕迹会在后续轮被折掉,血缘从视图消失。
#   ③ 子运行的 SYSTEM 走**每轮覆盖**(execute 内部传 system_prompt=),绝不把
#      sys_by_source 递进去 —— 它认不出"工人轮",会发小夜子的人设。
_worker_llm: dict[str, object] = {"llm": None}

# 工人手上是哪几件 —— 名字 ↔ 能力说法。**加工具必须同时加说法**,
# 否则能力句会漏掉它(下面那道检查会当场喊,而不是静默漏)。
_WORKER_LABELS: tuple[tuple[str, str], ...] = (
    ("web_search", "上网查"),
    ("http_get", "上网查"),        # 与搜索同为一件能力:上网
    ("list_files", "翻本地文件"),
    ("read_text_file", "读本地文件"),
)
# 本轮只接**上网的手**:文件工具要一个沙箱根,而根目录 = "她能读到用户的什么",
# 是**隐私边界**不是技术参数,还没拍(SUBAGENT_FILE_ROOT 空 = 不接)。
# ⚠ 给工厂的那个根现在是**惰性的**:构造只 resolve 路径、不做任何 IO,
#   过滤掉之后文件工具根本不会进注册表 —— 它不代表我们采纳了那个根。
_WORKER_WEB_TOOLS = ("web_search", "http_get")
_worker_tools = ToolRegistry([
    tool for tool in make_read_only_tools(SUBAGENT_FILE_ROOT or DATA_DIR)
    if tool.name in _WORKER_WEB_TOOLS
])


def _worker_capabilities() -> tuple[str, ...]:
    """工人能替她做什么(短说法)—— 从**真正接进去的工具**推,不手抄。

    规矩出处:character/persona.py:62「能力唯一来源 = 本轮 schema + 工具用法段;
    人设写死能力 → 工具子集变化时,模型仍以为有这工具」。能力句与工具集是同一类
    事实,所以同样不许写死 —— 写死就会在工具集变化后变成陈旧信息,
    而陈旧的能力描述会让她**凭假前提做决定**。
    """
    wired = set(_worker_tools.names())
    labelled = {name for name, _ in _WORKER_LABELS if name in wired}
    if labelled != wired:
        raise RuntimeError(
            f"工人的工具没有能力说法: {sorted(wired - labelled)} —— "
            "去 engine._WORKER_LABELS 补,否则能力句会漏掉它(静默漏最危险)"
        )
    # 去重保序:web_search / http_get 是同一件能力(上网),只说一次
    return tuple(dict.fromkeys(
        label for name, label in _WORKER_LABELS if name in wired
    ))


def _run_worker(task: str, label: str) -> dict:
    """工人跑一次(**阻塞**);工具壳只认这一个形状 —— 给它任务、拿回事实。

    阻塞是 v1 的语义:她这一轮会等它跑完。这不是 bug —— 工具执行本来就发生在
    `run_turn` 的锁内,"她一次只做一件事"照旧成立,只是这一件变成了"等工人"。
    ⏳ 异步 / 排队 / 撤回见 docs/protocols/SUBAGENT.md §3
      (test/lab/scheduler.py 是那部分的实验台,**未毕业**)。
    """
    llm = _worker_llm.get("llm")
    if llm is None:
        # 引擎没起来(未配置连接):老实说没干,别让她以为干过了
        return {"status": "failed", "detail": "no-llm", "output": ""}
    record = execute_subrun(
        SubRunSpec(
            task=task,
            # 文案走内容层;engine 一个字都不写(server/README 「文案不在 server」)。
            # [步数预算]段由**产品参数**填 {steps} —— 上限在 params,"怎么说"在
            # personas,引擎只做这一句插值(与自走轮的 WAKE_BUDGET_TEMPLATE 同款)。
            # 不填这段的后果实测过:工人不知道步数有限,把每步都花在"再搜一次"上,
            # 撞上限时一个字没写 → failed + 空结论,白烧上百秒和几万 token。
            system=(
                personas_mod.SUBAGENT_SYSTEM
                + "\n"
                + personas_mod.SUBAGENT_BUDGET_TEMPLATE.format(steps=SUBAGENT_MAX_STEPS)
            ),
            label=label,
            tools=_worker_tools,
            max_steps=SUBAGENT_MAX_STEPS,
            max_tokens=SUBAGENT_OUTPUT_MAX_TOKENS,
        ),
        llm,
    )
    return {
        "run_id": record.run_id,
        "status": record.status,
        "detail": record.detail,
        "steps": record.steps,
        "duration": round(record.duration, 2),
        "usage": record.usage,
        "output": record.output,
    }


_tools.register(make_launch_subagent_tool(
    _run_worker, capabilities=_worker_capabilities()))

# ---------- 记忆索引(recall 的底座;2026-09-21 装配) ----------
# 一张卡一个缓存文件 + 一个常驻后台线程("吃性能剩饭"补向量)。
# 设计判定全在 core/memory_cache.py 与 core/embed.py 的模块头,这里只说接线:
#
# ① **一进程一份嵌入器**:torch 是进程级的,几张卡共用同一个模型(否则一张卡
#    一份 1.2 GB 权重)。它**懒加载**:拿到句柄不代表加载了权重。
# ② **暖机排在补账前面**:后台线程第一件事是 warm(实测 4.18 s),所以那 4 秒
#    落在"引擎刚起来、UI 还在连"那段空白,而不是"她刚问完、正等回答"那一刻。
# ③ guard = `_lock`(非阻塞让路):turn worker 跑一项的整段时间都持它,所以
#    后台**永不在她那一轮里抢 CPU**;欠账过期才插一脚(见 params)。
_memory: dict[str, MemoryCache] = {}
_memory_lock = threading.Lock()
_index_cache: dict[str, tuple] = {}     # sid -> ((行数, 欠账数), MemoryIndex)
_embedder_box: dict = {"tried": False, "emb": None}


def _embedder():
    """进程唯一那份嵌入器。拿不到就返回 None(= 检索退化成纯关键词,不抛)。"""
    if not _embedder_box["tried"]:
        _embedder_box["tried"] = True
        _embedder_box["emb"] = embed_mod.get_embedder()
        if _embedder_box["emb"] is None:
            print("[memory] 没有 torch/transformers —— 记忆检索退化成只有关键词")
    return _embedder_box["emb"]


def memory_cache(session_id: str) -> MemoryCache:
    """拿一张卡的索引缓存(第一次会建表 + 起后台线程)。

    ⚠️ 建表是**毫秒级**的(core/memory_cache.py 实测 18.4 ms),所以放在这里
       同步做没问题;真正贵的那 4.18 秒(暖机)在后台线程里。
    """
    with _memory_lock:
        c = _memory.get(session_id)
        if c is None:
            c = MemoryCache.for_session(MEMORY_DIR, session_id, embedder=_embedder(),
                                        guard=_lock, poll=MEMORY_POLL_SEC,
                                        max_wait=MEMORY_DEBT_MAX_WAIT_SEC)
            c.ensure()
            c.start()
            _memory[session_id] = c
        return c


def memory_sync(session_id: str) -> None:
    """把**盘上那份日志**派生成记忆行,同步进缓存(新增/删改都要调)。

    没走 turn 队列的改动(UI 删消息 / PATCH 正文)必须在 api 层显式调它 ——
    否则缓存会继续把**她已经删掉的话**翻出来念给她听。
    """
    if _store is None:
        return
    log = _store.load_log(session_id)
    rows = rows_from_events(log.events, strip_prefix=personas_mod.LIFE_EVENT_PREFIX)
    memory_cache(session_id).sync(rows)
    _index_cache.pop(session_id, None)   # 行变了 → 索引下次现建


def memory_forget(session_id: str) -> bool:
    """卡片被删/归档 → 它的缓存也跟着走(残骸里是她和那个角色的全部对话)。

    ⚠️ 已经建过的不走 `memory_cache()`:那会**先把缓存建出来**再删 ——
       白起一个后台线程、白写一个 sqlite 文件。
    """
    with _memory_lock:
        c = _memory.pop(session_id, None)
    _index_cache.pop(session_id, None)
    if c is not None:
        return c.prune_file()
    p = MemoryCache.path_for(MEMORY_DIR, session_id)
    if p.exists():
        p.unlink()
        return True
    return False


def recall_index() -> MemoryIndex | None:
    """**现在这张卡**的记忆索引(`recall` 工具的注入点)。

    "现在这张卡" = 正在跑的那一轮的卡,由 turn worker 设(`_recall_sid`)。
    ⚠️ **不能**用 `life_session_id()` 代替:它算的是"最近有人聊过的卡",而
       "刚换到一张新卡、说的第一句"那一刻,新卡的日志在盘上还没有真人消息
       (`_has_user_talk` 为假),它会指回**上一张卡** —— 正好错在最常见的那一步。
    """
    sid = _recall_sid["sid"]
    if sid is None or _store is None:
        return None
    cache = memory_cache(sid)
    rev = cache.counts()                       # (行数, 已补向量数)
    rev = (rev["total"], rev["total"] - rev["pending"])
    ent = _index_cache.get(sid)
    if ent is not None and ent[0] == rev:
        return ent[1]
    rows, vecs = cache.load()
    idx = MemoryIndex(rows, cache.embedder, vecs=vecs)
    _index_cache[sid] = (rev, idx)
    return idx


_tools.register(make_recall_tool(recall_index))

_store: SessionStore | None = None
_loop: AgentLoop | None = None
# "现在正在跑哪张卡" —— turn worker 每项设一次,recall 工具现取(见 recall_index)。
# 用可变 dict 而不是 global:工具闭包抓到的是这个盒子,赋新值不用重绑名字。
_recall_sid: dict[str, str | None] = {"sid": None}
_heartbeat: Heartbeat | None = None
_life_gate: "ServerGate | None" = None  # 补写/心跳跑完也 mark_self,节奏衔接
# 回放轮世界时钟:补写轮跑之前设为 slot 起点,backfill_composer 的 world section
# 每 step 现取它 —— 模型看到的时间 = 历史时刻,墙钟不混入。仅在回放轮内被设。
_backfill_clock: dict[str, float] = {"ts": 0.0}
# 实验台/演示"当前时间"覆盖(turn_lab 注入间隔/时刻用,2026-09):ts>0 时,
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
# 那件事件的 start 记在这里,**暴露给要锚定叙述视图的调用方(实验台 turn_lab:
# 强制/命中轮把 [当前时间] 拨到事件 start、时间线从它派生)**。产品自走/心跳/
# 脉冲路径不用它 —— 它们照旧在触发时刻叙述,不改产品输出(锚定试验暂只留
# lab)。0 = 无事件/未激活;end_self_wake 一起清。
_wake_anchor: dict[str, float] = {"start": 0.0}
_lock = threading.Lock()  # 全局引擎锁(loop 内部已有 turn 锁,这里护 store 落盘)

# ---------- turn 队列(2026-09-17 用户拍板:把"抢锁"换成"排队") ----------
# 四个来源(聊天 / 心跳自走 / 补写 / 脉冲)**不再各自抢 _lock**,而是把一次 turn
# 塞进这个队列;一条 worker 线程一个一个取,**永不并发**。
#
# **为什么是队列而不是锁**:锁能排,但**不能插队** —— 它是先到先得。而我们要的是
#   **user 请求 > self 请求**:你一发消息,立刻排到所有自走/补写前面。
#   这就是那条拍板规则:"已经解绑的会话来了 user 请求,其余的都靠后被插队"。
#
# **队列项粒度:一张卡的全部补写轮 = 一项**(不可分割)。否则你插到某张卡补写的
#   中间,后面几轮的事件就排到你消息之后了 —— 又回到 seq/时间戳错位那个老坑。
#
# _lock 保留:worker 每一项仍在它里面跑 —— 第二道保险(万一有人绕过队列直接调),
#   也继续兑现它"护 store 落盘"那句注释。
_QUEUE_USER = 0    # 数值小的先出 → user 永远排在 self 前面
_QUEUE_SELF = 1
_turn_queue: "_queue.PriorityQueue" = _queue.PriorityQueue()
_turn_seq = itertools.count()
_turn_worker: threading.Thread | None = None
_turn_worker_guard = threading.Lock()
_turn_busy = threading.Event()   # worker 此刻正在跑一项


def _turn_worker_loop() -> None:
    while True:
        _prio, _seq, job, box, done, sid = _turn_queue.get()
        _turn_busy.set()
        # "现在这张卡"只在**这一项**的运行期内有效。三个来源(聊天/自走/补写)
        # 都从 _submit_turn 进来,所以这里是唯一要设的地方 —— 少设一处,
        # recall 会翻错卡(而且是静默的:她有记忆,只是**别人的**)。
        prev_sid = _recall_sid["sid"]
        _recall_sid["sid"] = sid
        try:
            with _lock:
                box["result"] = job()
                # 这一轮把日志写完了 → 把新行同步进索引缓存(喂给后台补向量)。
                # ⚠️ 必须在**锁内**:`_store.load_log` 有一条被 test_log_transaction
                #    测住的不变量 —— 它只许在锁内被调用(加载→改→存盘是一段)。
                #    这里虽然只是只读,但放宽那条不变量得用户点头才算数。
                #    自己的 try:索引坏了不该让**她这一轮**失败。
                if sid is not None:
                    try:
                        memory_sync(sid)
                    except Exception as exc:  # noqa: BLE001
                        print(f"[memory] 同步索引缓存失败({sid}): {exc}")
        except Exception as exc:  # noqa: BLE001  原位重抛给提交方
            box["exc"] = exc
        finally:
            _recall_sid["sid"] = prev_sid
            _turn_busy.clear()
            done.set()


def _ensure_turn_worker() -> None:
    """按需起 worker(懒起:测试、重配引擎都不必显式 start)。"""
    global _turn_worker
    with _turn_worker_guard:
        if _turn_worker is None or not _turn_worker.is_alive():
            _turn_worker = threading.Thread(
                target=_turn_worker_loop, daemon=True, name="yona-turn"
            )
            _turn_worker.start()


def _submit_turn(job, *, priority: int = _QUEUE_SELF, on_wait=None,
                 sid: str | None = None):
    """把一次 turn 塞进队列并**等它跑完**;返回 job 的返回值。

    job 在 worker 线程里、**持 _lock** 执行 —— 所以 job 内部不要再碰 _lock。
    job 抛异常则在这里原位重抛,由调用方决定怎么呈现。
    on_wait:排队期间每 ~0.5s 回调一次(打印台"…排队中 Xs"现场用)。
    sid:这一项是**哪张卡**的。给 recall 用,也给"跑完同步索引缓存"用。
         三个来源都从这里进,所以传对了就全对。
    """
    _ensure_turn_worker()
    done = threading.Event()
    box: dict = {}
    _turn_queue.put((priority, next(_turn_seq), job, box, done, sid))
    while not done.wait(0.5):
        if on_wait is not None:
            on_wait()
    if "exc" in box:
        raise box["exc"]
    return box.get("result")


def turn_is_busy() -> bool:
    """此刻队列上有没有东西排在前面(喂 UI 的"她在忙"提示;近似值即可)。"""
    return _turn_busy.is_set() or _turn_queue.qsize() > 0


# 还没补完的卡 —— 优先级不是**两级平铺**,是**看目标的**(2026-09-17 用户拍板):
#   > "未解绑的会话中,补写 > user;如果 user 发给了已经解绑的会话,
#   >  那它就大于未解绑的补写。"
# 所以 user 请求的优先级要问"目标卡补完了没":
#   未补完 → 排在那张卡的补写**后面**(不给它插队,否则它会顶掉该卡的补写窗口,
#            那张卡的离线空白就永远补不上了);
#   已补完 → 插到**所有**未解绑补写的前面("已经解绑的先能正常对话")。
_pending_backfill: set[str] = set()
_pending_backfill_lock = threading.Lock()


def user_turn_priority(sid: str) -> int:
    """user 请求该用哪个优先级 —— 取决于**目标卡是否还没补完**。"""
    with _pending_backfill_lock:
        return _QUEUE_SELF if sid in _pending_backfill else _QUEUE_USER


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

        def _job():
            # 加载 → 决策 → 跑轮 → 落盘 = **一个事务**;worker 已经持 _lock,
            # 这里不要再碰它。(2026-09-17 修)store.load_log 无缓存(每次读盘返回
            # 新对象)、写盘整体覆盖 —— 加载与落盘之间绝不能松手。
            # 见 docs/pitfalls/HISTORY.md §四。
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
            try:
                # 该卡快照的人格覆盖(若有)在自走轮同样生效
                snap = _store.get_session_settings(sid)
                result = _loop.run_turn(
                    source=source, tools=tools, log=log, self_note=self_note,
                    system_prompt=(snap.get("system_prompt")
                                   if snap.get("system_prompt") else None),
                )
                _store.save_log(sid, log)
                self.gate.mark_self()  # 真跑了一轮 → 进入冷却
                return result
            finally:
                # 清预算/事件锚:必须在 worker 里、紧贴这一轮 —— 否则下一项
                # 可能先看到上一轮残留的 _wake_budget。
                if is_self:
                    end_self_wake()

        try:
            result = _submit_turn(_job, priority=_QUEUE_SELF, sid=sid)
            if result is not None:
                _live(f"{tag}完成,耗时 {time.time() - t0:.1f}s")
            return result
        except Exception as exc:  # noqa: BLE001
            _live(f"{tag}失败: {exc}")
            raise


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
    从 start 派生、生活事件落事件结束)由**调用方**(实验台)决定 —— 本函数只把
    该事件的 start 记进 `_wake_anchor`,不改产品自走/心跳的输出(产品路径照旧
    在触发时刻叙述;2026-09 用户拍板:这套事件锚定暂只留在 turn_lab 试验)。

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
    # 工人用的就是这份 llm 客户端(同一个闸口 —— 工人的调用也进 llm-log 面板,
    # 看得见它干了什么)。**晚绑定**:工具在模块加载时就注册好了,这里只换句柄,
    # 所以换连接/重建引擎都不会撞「已注册」(见上面装配处的约束①)。
    # 子运行要自己的输出上限:4096 是拍给聊天轮的,推理模型会把 reasoning
    # 算进 output、4096 直接吃满(实测),由 _run_worker 每轮覆盖。
    _worker_llm["llm"] = llm
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
        # 折叠视图开(2026-09-19 用户拍板,原为 False):
        # **已结束轮里,没声明 retain_result 的工具痕迹不再进模型输入**,只留她
        # 说过的话。要的就是用户那句「tool 的 result 是个**瞬时产物**,不拼进
        # 常驻内容,即使 system 内也不放」—— 需要就现调。
        # 谁保住痕迹由**工具自己声明**(core/tools.py:26-28):
        #   launch_subagent = True  (一次性委派,重查不了 → 跨轮保真)
        #   recall / change_outfit = False(需要就现调;穿着在 [当前角色状态] 段里)
        # 改之前实测过 False 的后果:recall 的原文跨轮留在上下文里
        # (prompt_lab/check_transient.py,0 花费可复跑)。
        # 折叠**只改投影,日志原文一个字不动**(core/session_log.py:274)。
        fold_tool_traces=True,
        # ⚠️ 这里**没有** life_event_prefix(2026-09-21 拆除):生活事件整条不进投影,
        # 取用路径只剩 `recall` 工具。判定见 core/session_log.derive_messages。
        # 真人消息进上下文的时间戳(内容层模板;见 personas.USER_TIME_PREFIX)。
        # 2026-09-17:历史里没有时间轴,她算不出"距上一条多久" —— 两条相隔
        # 69 分钟的对话被她读成了连续的("刚不是说了嘛,你连着问两遍")。
        # 只作用于 source=="user" 的消息,自走占位不打标(见 derive_messages)。
        user_time_prefix=personas_mod.USER_TIME_PREFIX,
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
    证据);补写 = 目标卡醒来补日子。**触发时机只有进程启动这一处**
    (start() → 本函数);用户消息永远不是补写触发点。
    **补几张**(2026-09-17 拍板,方案"乙"):遍历**所有有历史的卡**,按
    **离线间隔升序**(= updated_at 降序)一张一张来 —— 当前卡天然第一。

    **2026-09-17「方案 A」✅ 用户拍板(见 LIFE_BACKFILL §9):补写占队首。**

    补写的语义是"**你不在时**她的生活" —— 你的消息一进日志,那段生活就结束了。
    所以整张卡("读日志尾 → 判断 → 采样 → 跑完 N 轮 → 落盘")是**一个不可分割的
    队列项**:决策依据(日志尾)和基于它的动作之间一旦松手,依据就可能过期。
    你插队只会插在**整张卡**的前面或后面,不会插到它中间。

    旧版把 `sleep(BACKFILL_START_DELAY_SEC)` 放在锁外 = **主动让路给用户** ——
    既违反"补写最优先",又会让补写事件 append 在用户消息**之后**(日志的
    seq 顺序与时间戳顺序错位)。该参数随本方案删除(见 server/params.py)。
    """

    def _card_job(sid: str):
        """**一张卡**的补写 = **一个队列项**(不可分割,见本函数 docstring)。"""
        card_log = _store.load_log(sid)
        last_active = card_log.events[-1].time if card_log.events else None
        trigger, reason, gap = _wake_decision(last_active)
        print(f"[backfill] 卡片 {sid}: {reason}")
        if not trigger:
            return
        now = time.time()
        sampler = LifeSampler(last_active, now)
        events = sampler.sample()
        if not events:
            print("[backfill] 采样器无事件(离线太短 / 落在稀疏时段 /"
                  "恰好没判定中),跳过")
            return
        empty_tools = ToolRegistry([])  # 补写是"那段日子怎么过的",不该有实时工具
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

    def _run() -> None:
        # 2026-09-17 拍板(方案"乙"):启动时遍历**所有有历史的卡**,按
        # **离线间隔升序**(= updated_at 降序)一张一张来 —— 所以当前卡天然
        # 排第一,补完你就能立刻对话,其余在后台接着补;你一发消息还会插队
        # 到剩余补写项前面(user > self,见 turn 队列)。
        order = _store.life_backfill_order()
        if not order:
            print("[backfill] 没有聊过的卡,跳过")
            return
        print(f"[backfill] 待补 {len(order)} 张卡(离线间隔升序)")
        for sid in order:
            # 一张卡 = 一个队列项;lambda 默认参绑定 sid,避免闭包晚绑定
            # 标成"未解绑":期间发给这张卡的 user 请求会排队在它**后面**
            with _pending_backfill_lock:
                _pending_backfill.add(sid)
            try:
                _submit_turn(lambda s=sid: _card_job(s), priority=_QUEUE_SELF, sid=sid)
            except Exception as exc:  # noqa: BLE001
                print(f"[backfill] 卡片 {sid} 补写失败: {exc}")
            finally:
                # 跑完(含"没事件跳过")= 这张卡解绑,之后发给它的 user 请求
                # 就能插到其余未解绑卡的前面了
                with _pending_backfill_lock:
                    _pending_backfill.discard(sid)

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
    """服务停止:停心跳与记忆索引后台线程(都是 daemon,主要是收尾干净)。"""
    if _heartbeat is not None:
        _heartbeat.stop()
    with _memory_lock:
        caches = list(_memory.values())
        _memory.clear()
    _index_cache.clear()
    for c in caches:
        try:
            # close 而不是 stop:stop 只停线程,sqlite 连接还开着 ——
            # 在 Windows 上那等于**文件被占住**(删不掉、也搬不走)。
            c.close()
        except Exception as exc:  # noqa: BLE001
            print(f"[memory] 关索引缓存失败: {exc}")
