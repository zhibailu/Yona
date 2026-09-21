"""Yona 内核 · 子运行(SubRun)—— 会死的隔离工作单元

**2026-09-16 用户批准从实验台毕业进 core**(原住 test/lab/subrun.py)。
毕业的依据不是"跑通了",是**接口形状成立**:本模块只 import core 自己的
四个原语(llm / loop / session_log / tools),不碰 HTTP、不碰 store、
不碰 personas、不碰时钟 —— 正好是 core 的依赖边界与"纯逻辑、单一职责、可测"。

子运行不是"第二个她",是"一个执行期特别长的工具":
  - 不带人格:SYSTEM 是任务级的(由内容层给),不装配 persona;
  - 会死:跑完即止,不进心跳/闸门/life,不产生自走轮;
  - 不进主日志:主日志只留 tool/call + tool/result(带 run_id 血缘),
    子运行的完整轨迹落独立 run store —— 真相没被稀释,只是主日志
    只留"她的那一层"(与"折叠=视图不是日志"同一哲学)。

**复用装配,不复用实例**(不是选择,是硬约束):
  父的工具体里调父自己的 run_turn 会**永久卡死** —— `core/loop.py` 的
  `AgentLoop.__init__` 里
  `self._turn_lock = threading.Lock()` 是 `threading.Lock`(不可重入),而工具执行
  发生在 `run_turn` 的 `with self._turn_lock:`(`core/loop.py`)块内。
  所以这里每次都新建一个 AgentLoop,共享 llm 与执行器。
  (2026-09:这一处原来指的是 `core/loop.py:82` —— 那个行号**在我之前就已经飘了**
   (82 落在 `self.fold_tool_traces = ...` 上),现在按符号重新钉准。)
  证据:`test/test_subagent_wiring.py::test_same_instance_inside_a_tool_deadlocks`。

对照 dsh(dsh-subagent / dsh-tool-subagent 实物):
  dsh 的子代理是一个独立 Session(自己的日志)+ 血缘戳记
  (childSessionMeta.lineageSeedLength = 父日志前多少事件是继承的)
  + 委托深度预算(resolveChildDepth / maxDepth);结果经 Job(one-shot)
  或子自己的 report 工具(continuable,delivery = next-step | quiet)回父。
  **一个已查实的差别**:dsh 的子默认**加入父的 preset**(工具集与父相同,
  `applyChildComposition` 的 `composeFrom(childCtx, parent.ctx)`),收窄工具
  要靠 preset 里配 `toolFilter`(出厂没配)。所以 dsh 的委派理由是"省上下文",
  我们的是"能力" —— 子有父没有的工具。两者不可互换,详见 SUBAGENT.md §4.4。
  本模块先取"独立日志 + 血缘 id"两条,不做深度预算 / 持久 resume / 后台 report。

⚠️ **上面两条"已取得"要打折看(2026-09 清理时如实标注)。**
   "独立日志"成立(子运行确实有自己的 `SessionLog`);但**轨迹落 run store**
   与**血缘 id**这两条,本模块**给了接口、产品没接**:
   - 产品唯一的调用点 `server/app/engine.py` 的 `_worker_capabilities()` 里那个 `execute_subrun(...)`
     **不传 `store=`、不传 `parent=`**(那里不要改 —— 属产品决策);
   - 所以派出去的工人干了什么,**事后查不到**:主日志只有一行 tool/result,
     而 `run_id` 指向的那个 jsonl 从来没被写出来;**`parent` 也恒为 None**。
   三处占位标注(现状/为什么留/启用条件/手术删哪几行)分别写在
   `SubRunStore`、`SubRunRecord.parent`、`STATUS_KILLED`;`execute()` 里那份
   无条件的事件副本也有自己的一条。**要接线请先让用户拍板**(落盘是新目录,
   属产品决策,不是清理能定的)。

不写文案:SYSTEM 由调用方(内容层)传,本模块只提供容器与执行器。

⏳ 仍未收口(不假装已定):
  - **单次调用上限 vs 累计预算**(谁定、定多少)—— 产品侧参数还没落到
    server/params.py,现在是调用方显式给;
  - **"干完了但没结论"要不要独立终态** —— 现在撞上限 = failed + 空 output,
    回执里带 detail,算不上"静默空回",但也没给她一个"它没查完"的说法;
  - **委派失败时父该怎么被通知** —— 现在只靠回执里的 status/detail。
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .llm import LLM
from .loop import AgentLoop
from .session_log import SessionLog
from .tools import ToolRegistry

# ---------- 终态(对齐 dsh JobOutcome.status:completed | killed | failed) ----------

STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
# ⏸ **占位:生产路径不可能产出这个值 —— `execute()` 里没有取消路径。**
# (2026-09 清理时如实标注,不接线不删。)
#
# ① 现状:`execute()` 是"同步跑到底"的(`loop.run_turn` 一跑到底,`StreamInterrupted`
#    只来自流层),里面**没有**任何"被取消/被 kill"的落点 —— 所以经 `execute()`
#    跑出来的记录永远只有 completed / failed 两种。这个值的真实消费者在
#    **实验台**:`test/lab/scheduler.py`(那两处 `job.status = STATUS_KILLED`)
#    与 `test/test_subrun.py` 的队列用例 —— 那是"可取消的排队调度"那部分,
#    尚未毕业进产品(见本模块头 ⏳ 段与 `docs/protocols/SUBAGENT.md` §3)。
# ② 为什么留着:它是**三态契约里的一态**,而"撤回一个派出去的活"是真需求
#    (产品现在只敢同步阻塞,见 `server/app/engine.py` 的 `_run_worker` 注释
#    "异步 / 排队 / 撤回见 SUBAGENT.md §3")。删掉它,lab 那份实验台当场断,
#    而它恰恰是未来接线时的现成落点;字段取值也已在台账里用过。
# ③ 什么条件才启用:产品真的接线**可取消的调度**(异步/排队/撤回毕业)时 ——
#    那一刻 `execute()` 或它的替代者必须有一条"中途放弃"的路径,
#    把 rec.status 落成这个值。
# ④ 将来手术要删哪几行:本常量这一行(含上面这段注释);
#    连带改 `test/lab/scheduler.py`(顶部 import 与那三处 `STATUS_KILLED` 使用)
#    与 `test/test_subrun.py`(顶部 import 与那三处 `STATUS_KILLED` 断言)。
#    ⚠️ 别只删常量 —— 那三个文件会 ImportError。
STATUS_KILLED = "killed"


def new_run_id() -> str:
    """一次子运行的 id(血缘锚):主日志的 tool/result 与 run store 靠它对齐。"""
    return f"sub-{int(time.time()):x}-{uuid.uuid4().hex[:6]}"


# ---------- 规格与结算 ----------


@dataclass
class SubRunSpec:
    """一次子运行的规格 —— 派活的人只描述"做什么",不描述"你是谁"。

    system 必填且由内容层提供:core 不写文案(guardrail)。
    这就是"子运行没有第二个人格"在类型上的落法 —— 它拿不到 persona,
    只能拿到一份任务说明。
    """

    task: str  # 任务书(子运行唯一的输入,进它自己的 user 槽)
    system: str  # 任务级 SYSTEM(内容层提供)
    label: str = ""  # 一行标签:队列面板显示 / 给模型的结果通知用
    tools: list | ToolRegistry | None = None  # 工具白名单(None/空 = 无工具)
    max_steps: int = 4  # 步数上限:子运行不是无底洞
    model: str | None = None  # 档位预留:同端点换模型(本地弱模型实验的接口)
    temperature: float | None = None
    max_tokens: int | None = None
    parent: str | None = None  # 血缘:谁派的(父会话 id)


@dataclass
class SubRunRecord:
    """一次子运行的结算记录。

    output 是**蒸馏后的结论**,不是它的脑内过程 —— 只有这一段会回填给父。
    events 是它的完整轨迹(落 run store,主日志看不到)。
    """

    run_id: str
    label: str = ""
    status: str = STATUS_FAILED
    output: str = ""
    detail: str = ""  # 终结原因:completed | max-steps | max-tokens | error:...
    steps: int = 0
    started_at: float = 0.0
    finished_at: float = 0.0
    usage: dict[str, Any] | None = None
    # ⏸ **占位:产品从不传 `parent=`,这个字段恒为 None。**
    # (2026-09 清理时如实标注,不接线不删 —— 见下面"血缘"那条。)
    #
    # ① 现状:本模块头把"独立日志 + **血缘 id**"列为已经取得的两条之一,
    #    但产品唯一的调用点 `server/app/engine.py` 的 `_worker_capabilities()` 里那个 `execute_subrun(...)`
    #    **只传 `SubRunSpec` 与 `llm`** —— 没有 `parent=`(也没有 `store=`)。
    #    于是 `SubRunSpec.parent` 缺省 None → 这条记录里的 `parent` 恒 None。
    #    唯一真传值的全是实验侧:`test/subrun_probe.py` 与
    #    `test/test_subagent_wiring.py` 的 `test_fresh_instance_inside_a_tool_works()`、
    #    `test/test_subrun.py` 的 `test_store_roundtrip_keeps_record_and_trajectory()`
    #    (断言 `loaded.parent == "session:1"`)。
    # ② 为什么留着:它是**血缘的一半**,也是"主日志那行 tool/result 追到 run store"
    #    能双向走通的前提(另一半是 `run_id`)。将来接 store 时它是现成的落点,
    #    删掉这条字段,`to_json`/`from_json` 的键也要跟着动 —— 而它们是**
    #    已落盘的格式**(旧 jsonl 里可能已经有这个键)。
    # ③ 什么条件才启用:产品开始为子运行落 store 时(见 `SubRunStore` 那条标注),
    #    顺带把 `parent=<父会话 id>` 传进来 —— 两件事本来就是一件事:
    #    没有 store,血缘 id 记了也没地方查。
    # ④ 将来手术要删哪几行:本字段这一行;`SubRunSpec.parent` 那一行;
    #    `execute()` 里 `parent=spec.parent` 那一行;`to_json()` 的 `"parent": self.parent`
    #    与 `from_json()` 的 `parent=raw.get("parent")`。
    #    连带改上面列的四个测试文件。⚠️ 动 `to_json` 键 = 动已落盘格式。
    parent: str | None = None
    events: list[dict[str, Any]] = field(default_factory=list)

    @property
    def duration(self) -> float:
        return max(0.0, self.finished_at - self.started_at)

    @property
    def ok(self) -> bool:
        return self.status == STATUS_COMPLETED

    def to_json(self) -> dict[str, Any]:
        """不含 events(轨迹单独按行写,避免重复)。"""
        return {
            "run_id": self.run_id,
            "label": self.label,
            "status": self.status,
            "output": self.output,
            "detail": self.detail,
            "steps": self.steps,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "usage": self.usage,
            "parent": self.parent,
        }

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> "SubRunRecord":
        """从**结算记录那一个 dict** 还原(不含轨迹)。

        2026-09 清理:原来这里还有一行 `events=list(raw.get("events") or [])` ——
        **它永远是空列表**,因为写侧 `to_json()` 明确不写 events(见那里的注释:
        "轨迹单独按行写,避免重复"),`raw` 从来就没有这个键。删掉它,
        让"events 只能由 `load()` 从事件行装回来"成为**唯一真相**
        (见 `load()` 末尾那两行)。要装轨迹请走 `load()`,别指望这里。
        """
        return cls(
            run_id=raw.get("run_id", ""),
            label=raw.get("label", ""),
            status=raw.get("status", STATUS_FAILED),
            output=raw.get("output", ""),
            detail=raw.get("detail", ""),
            steps=int(raw.get("steps", 0) or 0),
            started_at=float(raw.get("started_at", 0.0) or 0.0),
            finished_at=float(raw.get("finished_at", 0.0) or 0.0),
            usage=raw.get("usage"),
            parent=raw.get("parent"),
        )


# ---------- 轨迹仓 ----------


class SubRunStore:
    """子运行轨迹仓:一次子运行一个 jsonl,不进主日志,靠 run_id 血缘可追。

    首行 = 结算记录(kind=record),其后每行 = 一条子运行事件(kind=event)。
    主日志里只有一行 tool/result 带 run_id —— 要复盘就顺着 id 到这里。

    ⏸ **占位:产品从不构造它 —— `execute()` 的 `store=` 生产路径零传参。**
    (2026-09 清理时如实标注,不接线不删:落盘是新目录,属产品决策。)

    ① 现状:`server/app/engine.py` 的 `_worker_capabilities()` 里那个 `execute_subrun(...)` 只传
       `SubRunSpec` 与 `llm`,**没有 `store=`** → `execute()` 里
       `if store is not None: store.save(rec)` 永不执行。构造它的全是实验侧:
       `test/subrun_probe.py` 的 `STORE` 常量、`test/test_subagent_wiring.py` 的 `_store()` 工厂、
       `test/test_subrun.py`(三处)。`list_ids()` 同理(只有那两处实验在调)。
       所以本模块头那句"子运行的完整轨迹落独立 run store"**是设计,不是我现状**;
       `prompt_lab/tool_recall.py` 里那条注释已经说对了("子运行的
       SubRunStore 也是 `store=None`")。
    ② 为什么留着:它是**唯一现成的轨迹仓实现**,而"查得到工人干过什么"是
       真需求 —— 现在这一层证据**是空的**:主日志只留 tool/call + tool/result
       两行 + run_id,而 run_id 指向的文件根本不存在,`parent` 又是 None,
       于是**事后无法复盘**。删掉它等于把这条路一起删了,以后要做得从头写。
       另外 `save`/`load` 的 jsonl 格式(首行 record、其后 event)是**已落盘的
       约定**,实验侧已经有文件按它写。
    ③ 什么条件才启用:产品决定**为子运行落盘**时(用户拍板的事项之一)——
       那一刻:(a) 找一个 `root` 目录(新目录,不在 `data/`/`cache/` 里,
       要用户定);(b) 在 `engine._run_worker` 构造一个 `SubRunStore` 并传
       `store=`;(c) 顺带传 `parent=<父会话 id>`(见 `SubRunRecord.parent` 那条)。
       ⚠️ 接线会立刻引出两个新问题,别以为只是加一个参数:落盘的**清理策略**
       (卡被删时这些 jsonl 谁来删 —— 参考 `MemoryCache.prune_file` 的教训)
       与**磁盘增长**(每个工人一次一个文件)。
    ④ 将来手术要删哪几行:本类整个(`__init__` / `path` / `save` / `load` /
       `list_ids`,共约 58 行含注释);`execute()` 签名里的 `store: SubRunStore | None = None`
       与末尾的 `if store is not None: store.save(rec)`;`SubRunRecord.events` 字段。
       连带改:`test/subrun_probe.py`(顶部 import + `STORE` 常量 + 三处 `store=STORE`)、
       `test/test_subagent_wiring.py`(`_store()` 工厂 + 两处)、
       `test/test_subrun.py`(三处 `SubRunStore(...)` 构造 + 三个用例)。
       ⚠️ 别只删类 —— 那三个文件会 ImportError。
    """

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def path(self, run_id: str) -> Path:
        return self.root / f"{run_id}.jsonl"

    def save(self, rec: SubRunRecord) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        target = self.path(rec.run_id)
        with target.open("w", encoding="utf-8") as fh:
            fh.write(
                json.dumps({"kind": "record", **rec.to_json()}, ensure_ascii=False)
                + "\n"
            )
            for event in rec.events:
                fh.write(
                    json.dumps({"kind": "event", **event}, ensure_ascii=False) + "\n"
                )
        return target

    def load(self, run_id: str) -> SubRunRecord | None:
        target = self.path(run_id)
        if not target.exists():
            return None
        record: dict[str, Any] | None = None
        events: list[dict[str, Any]] = []
        with target.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if row.get("kind") == "record":
                    record = row
                elif row.get("kind") == "event":
                    row.pop("kind", None)
                    events.append(row)
        if record is None:
            return None
        rec = SubRunRecord.from_json(record)
        # events **只能在这里**装回来:结算记录那行不含轨迹(见 `to_json`),
        # 轨迹是它后面那些 `kind=event` 行。这是唯一真相 —— 别在 `from_json`
        # 里再开一个口子(那里原来有一行死代码,已删,见它的 docstring)。
        rec.events = events
        return rec

    def list_ids(self) -> list[str]:
        """仓里所有 run_id(按名排序)。

        ⏸ 占位:产品零调用(它跟着 `SubRunStore` 一起来的,见类 docstring ④)。
        现在只有 `test/subrun_probe.py` 与 `test/test_subrun.py` 的
        `test_store_roundtrip_keeps_record_and_trajectory()` 在调(后者那句
        `assert rec.run_id in store.list_ids()`)。
        删它请连着 `SubRunStore` 一起删,别单独留一个没人用的读取器。
        """
        if not self.root.exists():
            return []
        return sorted(p.stem for p in self.root.glob("*.jsonl"))


# ---------- 执行器 ----------


def execute(
    spec: SubRunSpec,
    llm: LLM,
    *,
    run_id: str | None = None,
    store: SubRunStore | None = None,
) -> SubRunRecord:
    """跑一次子运行(同步、阻塞)。

    隔离是**结构性**的,不是靠约定:
    - 自己的 SessionLog(事件不进父日志);
    - 自己的工具白名单(spec.tools,空 = 无工具);
    - 自己的 SYSTEM(任务级,拿不到 persona);
    - llm 由调用方注入 —— 这就是"本地弱模型当子代理服务"的接口。

    任何异常都不外抛:子运行的死法不能拖垮父的那一轮(对齐 dsh:
    run 的失败与释放失败都结算成 failed,而不是让异常穿透)。
    """
    rid = run_id or new_run_id()
    rec = SubRunRecord(
        run_id=rid,
        label=spec.label,
        parent=spec.parent,
        status=STATUS_FAILED,
        started_at=time.time(),
    )

    sub_log = SessionLog(f"subrun:{rid}")
    registry = (
        spec.tools
        if isinstance(spec.tools, ToolRegistry)
        else ToolRegistry(list(spec.tools or []))
    )
    loop = AgentLoop(
        sub_log,
        llm,
        registry,
        system_prompt=spec.system,
        max_steps=spec.max_steps,
    )

    overrides: dict[str, Any] = {}
    if spec.temperature is not None:
        overrides["temperature"] = spec.temperature
    if spec.max_tokens is not None:
        overrides["max_tokens"] = spec.max_tokens
    if spec.model is not None:
        overrides["model"] = spec.model

    try:
        result = loop.run_turn(spec.task, source="user", **overrides)
        kind = (result.reason or {}).get("kind") or ""
        rec.steps = int(result.steps or 0)
        # 只有正常收尾才算完成;撞上限(max-steps/max-tokens)是"没干完",老实记 failed。
        rec.status = STATUS_COMPLETED if kind == "completed" else STATUS_FAILED
        rec.detail = kind
    except Exception as exc:  # noqa: BLE001
        rec.status = STATUS_FAILED
        rec.detail = f"error: {exc}"

    rec.finished_at = time.time()
    rec.output = _final_text(sub_log)
    rec.usage = _sum_usage(sub_log)
    # ⏸ **占位:这份全量事件副本在生产路径上白构造、当场丢掉 —— 但没有改。**
    #
    # ① 现状:下面这行**无条件**把整条子日志复制成 list[dict]。而生产路径
    #    `server/app/engine.py` 的 `_worker_capabilities()` 不传 `store=` → 复制完立刻随 `rec` 被丢掉
    #    (唯一消费者是 `store.save`)。
    # ② **为什么没按"改成 `if store is not None:`"动**:按本仓库"删前自己 grep
    #    确认零引用"的规矩去查,发现**前提不成立** —— 有一批用例在
    #    **不传 store** 的情况下读 `rec.events`:
    #    · `test/test_subrun.py` 的 `test_subrun_log_is_its_own_and_events_are_captured`
    #      —— 里面直接 `[e["type"] for e in rec.events]` → 加守卫会 IndexError;
    #    · `test/test_subagent_wiring.py` 的 `test_subrun_turn_is_indistinguishable_from_a_chat_turn()` 那条虽然传了 store,但同款写法;
    #    · `test/subagent_prompt_view.py` 的 `make_demo_runner()` 里 `run()` 也是不传 store 的调用点。
    #    任务书里"那些用例都传了 store"这一条**与仓库实际不符**,所以按
    #    "拿不准 → 只加标注,别删"留原样。
    # ③ 为什么留着(不止"懒得改"):`SubRunRecord.events` 是**本模块对外的
    #    形状**之一 —— 调用方拿到 rec 就能复盘整个子运行,不必先去建一个 store。
    #    把复制搬进 `store.save` 会让"不落盘的调用方"再也拿不到轨迹,
    #    那是**行为变化**,属产品决策。
    # ④ 将来真要做,两条路(都要用户拍板,且连带改上面那些用例):
    #    (a) 原地加守卫 `if store is not None:` —— 要先改上面三个调用点/用例;
    #    (b) 把构造搬进 `store.save(rec)`(签名改成收 `events` 或收 `log`)——
    #        更彻底,但 `save(rec)` 的现有调用方与 round-trip 用例都要跟着动。
    rec.events = [
        {"seq": e.seq, "type": e.type, "time": e.time, "data": e.data}
        for e in sub_log.events
    ]
    if store is not None:
        store.save(rec)
    return rec


def _final_text(log: SessionLog) -> str:
    """取最后一条 assistant 消息的文本块 —— 这就是本次子运行的蒸馏结论。"""
    for event in reversed(log.of_type("assistant/message")):
        if event.data.get("interrupted"):
            continue
        parts = [
            block.get("text", "")
            for block in event.data.get("content", [])
            if block.get("type") == "text" and block.get("text")
        ]
        text = "".join(parts).strip()
        if text:
            return text
    return ""


def _accumulate(total: dict[str, Any], usage: Any) -> None:
    if not isinstance(usage, dict):
        return
    for key, value in usage.items():
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            total[key] = total.get(key, 0) + value


def _sum_usage(log: SessionLog) -> dict[str, Any] | None:
    """把子运行各步的 usage 求和(父的成本账要看得到"这活花了多少")。

    两条来源都要看,否则会漏:
    - assistant/message 上锚定的 usage(成功写出正文的那次调用);
    - assistant/chunk 里的 usage chunk —— **失败轮**(撞输出上限、无正文、
      流中断)不会写出 assistant/message,用量只在 chunk 层留有痕。
      成本账不能因为"这活没干成"就丢掉 —— 恰恰是失败的那些最该被看见。
      按 (turn, step) 去重,已锚定的步骤不重复计。
    """
    total: dict[str, Any] = {}
    anchored = {
        (e.data.get("turn"), e.data.get("step"))
        for e in log.of_type("assistant/message")
    }
    for event in log.of_type("assistant/message"):
        _accumulate(total, event.data.get("usage"))
    for event in log.of_type("assistant/chunk"):
        if (event.data.get("turn"), event.data.get("step")) in anchored:
            continue
        chunk = event.data.get("chunk") or {}
        if chunk.get("kind") == "usage":
            _accumulate(total, chunk.get("usage"))
    return total or None
