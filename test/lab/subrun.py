"""【实验台 · 不是内核】Yona 子运行(SubRun)—— 会死的隔离工作单元

⚠️ 为什么在 lab/ 而不是 core/(2026-09 用户质疑后归位):
   这个原语的**失败契约还没定**,实验也没跑出结论,所以它不配进 core。
   真机实测已经证明它现在是有问题的:撞输出上限时结算成 failed + **空结论**,
   而这对产品是"静默空回"级风险。等下面这些问题拍定了再谈毕业进 core:
     - 单次调用上限 vs 累计预算(谁定、定多少)—— 现在它借的是聊天轮的全局值;
     - "干完了但没结论"要不要独立终态;
     - 委派失败时父该怎么被通知。
   core/ 在这轮实验中**一个文件都没加、没改**(git diff 可查)。

想验证的东西:
  子运行不是"第二个她",是"一个执行期特别长的工具"。
  - 不带人格:SYSTEM 是任务级的(由内容层给),不装配 persona;
  - 会死:跑完即止,不进心跳/闸门/life,不产生自走轮;
  - 不进主日志:主日志只留 tool/call + tool/result(带 run_id 血缘),
    子运行的完整轨迹落独立 run store —— 真相没被稀释,只是主日志
    只留"她的那一层"(与"折叠=视图不是日志"同一哲学)。

对照 dsh(dsh-subagent / dsh-tool-subagent 实物):
  dsh 的子代理是一个独立 Session(自己的日志)+ 血缘戳记
  (childSessionMeta.lineageSeedLength = 父日志前多少事件是继承的)
  + 委托深度预算(resolveChildDepth / maxDepth);结果经 Job(one-shot)
  或子自己的 report 工具(continuable,delivery = next-step | quiet)回父。
  本模块先取"独立日志 + 血缘 id"两条,不做深度预算 / 持久 resume / 后台 report。

不写文案:SYSTEM 由调用方(内容层)传,本模块只提供容器与执行器。
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.llm import LLM
from core.loop import AgentLoop
from core.session_log import SessionLog
from core.tools import ToolRegistry

# ---------- 终态(对齐 dsh JobOutcome.status:completed | killed | failed) ----------

STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
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
            events=list(raw.get("events") or []),
        )


# ---------- 轨迹仓 ----------


class SubRunStore:
    """子运行轨迹仓:一次子运行一个 jsonl,不进主日志,靠 run_id 血缘可追。

    首行 = 结算记录(kind=record),其后每行 = 一条子运行事件(kind=event)。
    主日志里只有一行 tool/result 带 run_id —— 要复盘就顺着 id 到这里。
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
        rec.events = events
        return rec

    def list_ids(self) -> list[str]:
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
