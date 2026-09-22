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

✅ **上面两条"已取得"现在都成立了(2026-09-22 用户拍板接线)。**
   "独立日志"成立(子运行确实有自己的 `SessionLog`);**轨迹落 run store**
   与**血缘 id**这两条原先是"给了接口、产品没接",**现已接上**:
   - 产品唯一的调用点 `server/app/engine.py` 的 `_worker_capabilities()` 里那个
     `execute_subrun(...)` 现在传 `store=SubRunStore(sessions/<sid>/subruns)`
     与 `parent=<正在跑的那张卡>`;
   - 于是**工人干了什么,事后查得到**:主日志那一行 tool/result 的 `run_id`
     真的指向一个文件,`parent` 也真的是派它的那张卡。

   用户拍板原话:
   「自运行时单独日志,**要留日志**」「最先的诉求是**日志要隔离开**,同族的就放一起,
     不要同目录等级下有不同会话的主日志又有各自的 subagent,管理和回看会很乱」
   → 所以仓库根取**卡的目录**(`sessions/<sid>/subruns/`),不另开全局平铺目录;
   连带把"清理策略"那条顾虑也消掉了(跟卡同生共死,`delete_session()` 整袋归档)。
   细节见 `SubRunStore` 的 docstring。

   仍**没接**的只剩"可取消调度"那条:`STATUS_KILLED` 与 `execute()` 里那份无条件
   事件副本还是占位状态(各自的标注在下面),它们与本次接线无关。

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
    # ✅ **2026-09-22 已接线:产品现在真的传 `parent=`,不再恒 None。**
    # 落点:`server/app/engine.py` 的 `_worker_capabilities()` 里那个
    # `execute_subrun(...)` 现在传 `parent=_sid_now()` —— 即**派活那一刻正在跑的
    # 那张卡**(turn worker 每项设一次,见 `recall_index()` 的注释)。
    # 它与同一处传进去的 `store=`(`sessions/<sid>/subruns/`)是**一件事的两半**:
    # 有了 store,这个血缘 id 才查得到东西。
    #
    # (下面是接线前的"占位"沿革,留作记录 —— 结论已被上面推翻。)
    # ⏸ 占位期间:产品从不传 `parent=`,字段恒 None;唯一真传值的是实验侧
    #    `test/subrun_probe.py`、`test/test_subagent_wiring.py`、
    #    `test/test_subrun.py` 的 `test_store_roundtrip_keeps_record_and_trajectory()`。
    # ⚠️ 字段与 `to_json`/`from_json` 的键**一个字没动**(那是已落盘格式:
    #    旧 jsonl 里可能已经有 `parent` 键),所以接线只改装配处,不改格式。
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

    ✅ **2026-09-22 已接线(用户拍板)**:产品路径现在**真的落盘了**。
    用户原话:
    「自运行时单独日志,**要留日志**」「最先的诉求是**日志要隔离开**,同族的就放一起,
      不要同目录等级下有不同会话的主日志又有各自的 subagent,管理和回看会很乱」

    接法(见 `server/app/engine.py` 的 `_subrun_store_for()` 与
    `server/store.py` 的 `subruns_dir()`):仓库根 = **这张卡自己的目录**,
    即 `sessions/<sid>/subruns/`,一跑一个 `<run_id>.jsonl`。
    同时传了 `SubRunSpec.parent`(血缘 = 派活那一刻正在跑的那张卡)。

    为什么塞进卡的目录而不是另开一个全局仓(三条都是用户诉求的直接结论):
      · **同族同处**:一张卡的全部日志在同一层(chat.log / subruns/ / meta.json /
        images/)—— 不会出现"同一层级下既有不同会话的主日志、又有各自的 subagent";
      · **清理策略不用另立**:它跟卡同生共死 —— `delete_session()` 把
        `sessions/<sid>/` 整袋搬进 `archive/<ts>-<sid>/`,轨迹自然跟着走,
        不会在 data/ 下积一个只增不减的目录(这正是当初"不接线"的一条理由);
      · **不会串卡**:以前设想的 `data/subruns/` 是全局平铺,只能靠 run_id 猜是哪张卡的。

    ⚠️ sid 拿不到时(实验台/探针里跑工人,没有"正在跑哪张卡")调用方会传 `store=None`
    → **不落盘**,这是有意的:宁可不写,也不要写到一个猜出来的卡目录里。

    ⚠️ 它**不是"派生可重建产物"**:轨迹是**证据**(trace),删了就没了,所以既不进
    `cache/`(那儿的约定是"删掉只损失一次重建时间"),也不该被随手清。
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

        ✅ **2026-09-22 起产品有仓可列了**(`sessions/<sid>/subruns/`,见类 docstring),
        但**这个函数仍然只有实验侧在调** —— 产品没有"列出某张卡跑过的所有工人"
        这个入口(那属于 UI/观测面,用户 2026-09-22 已把观测面板判为不留)。
        现在是 `test/subrun_probe.py` 与 `test/test_subrun.py` 的
        `test_store_roundtrip_keeps_record_and_trajectory()`(那句
        `assert rec.run_id in store.list_ids()`)在调。

        留着的理由:它是**"人肉复盘"的现成入口** —— 接线后盘上真有 jsonl 了,
        要查"这次派出去了什么"就得按 run_id 找到文件(目录里 `ls` 也行,但有了
        这张卡目录之后,按 id 列一遍比翻目录稳)。真要删,连着 `SubRunStore` 一起删,
        别单独留一个没人用的读取器。
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
    # ⚠️ **这份全量事件副本是"无条件"构造的 —— 请保持这样,别加 `if store is not None:` 守卫。**
    #
    # 2026-09-22 复核(接线落盘之前之后都成立):
    #   ① 生产路径现在**会**传 `store=` 了(见模块头 ✅ 段),所以这份副本
    #      **不再"白构造"** —— 它是落盘的内容本身。
    #   ② 但"不传 store 的调用方也读得到 rec.events"这条**仍然要保住**:
    #      `test/test_subrun.py` 的 `test_subrun_log_is_its_own_and_events_are_captured`
    #      在**不传 store** 的情况下直接 `[e["type"] for e in rec.events]` ——
    #      加守卫会让它当场 IndexError;`test/test_subagent_wiring.py` 与
    #      `test/subagent_prompt_view.py` 的 `make_demo_runner()` 也是同款。
    #   ③ 语义上它是对的:`SubRunRecord.events` 是**本模块对外的形状** ——
    #      调用方拿到 rec 就能复盘整条子运行,**不必先建一个 store**。
    #      把复制搬进 `store.save()` 会让"不落盘的调用方"再也拿不到轨迹,那是行为变化。
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
