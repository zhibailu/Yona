"""【实验台 · 不是内核】Yona 子运行队列(RunQueue)—— 编排

⚠️ 与 subrun.py 同因在 lab/:实验未收口,不配进 core。core/ 这轮零改动。

状态机(对标 dsh JobStatus:running | stopping | completed | killed | failed;
本轮多一个 queued —— dsh 的 job 不排队,我们的有并发上限,所以要排队):

    queued ──▶ running ──▶ completed | failed
       │
       └──▶ killed(出队前取消)

职责边界(对照 dsh jobs 的 "运行时拥有身份与生命周期,生产者拥有执行资源"):
本模块只管**编排** —— 排队、并发上限、状态、取消、等待、可观测。
它不认识"角色""她""人格":队列里排的是**活**,不是人。
runner 由调用方注入,所以这里没有任何角色内容,也没有任何 LLM 细节。

诚实边界(第二阶段再补,先不做假承诺):
- cancel 只对**排队中**的活有效。已经在跑的活不能掐 —— 一次阻塞中的
  LLM 调用没法被安全打断,谎称"已取消"比不支持更糟。
- 没有重试、没有优先级、没有截止时间、没有累计预算。都是有意的下一步。
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable

from .subrun import STATUS_FAILED, STATUS_KILLED, SubRunRecord, SubRunSpec

QUEUED = "queued"
RUNNING = "running"

#: 非终态(还在排队或正在跑)
LIVE_STATES = (QUEUED, RUNNING)


@dataclass
class JobView:
    """一件活的只读快照 —— 给 UI / 探针看,永远不是队列内部状态本身。"""

    run_id: str
    label: str
    status: str
    submitted_at: float
    parent: str | None = None
    started_at: float | None = None
    finished_at: float | None = None
    detail: str = ""
    output: str = ""
    steps: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "label": self.label,
            "status": self.status,
            "parent": self.parent,
            "submitted_at": self.submitted_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "waited": (
                None
                if self.started_at is None
                else max(0.0, self.started_at - self.submitted_at)
            ),
            "duration": (
                None
                if self.started_at is None or self.finished_at is None
                else max(0.0, self.finished_at - self.started_at)
            ),
            "detail": self.detail,
            "output": self.output,
            "steps": self.steps,
        }


class RunQueue:
    """子运行队列:入队 → 并发上限内跑 → 终态可查。

    用法::

        q = RunQueue(runner, workers=2)       # runner(spec, run_id) -> SubRunRecord
        rid = q.submit(spec)                  # 入队即返回,不阻塞
        rec = q.wait(rid, timeout=60)         # 需要结果时再等
        q.stop()

    线程安全;submit 会自动拉起 worker(幂等),不必先 start()。
    """

    def __init__(
        self,
        runner: Callable[[SubRunSpec, str], SubRunRecord],
        workers: int = 2,
        on_change: Callable[[JobView], None] | None = None,
    ) -> None:
        self._runner = runner
        self._workers = max(1, int(workers))
        self._on_change = on_change  # 可观测性钩子(每次状态变化回调)
        self._cond = threading.Condition()
        self._pending: deque[str] = deque()
        self._specs: dict[str, SubRunSpec] = {}
        self._jobs: dict[str, JobView] = {}
        self._records: dict[str, SubRunRecord] = {}
        self._threads: list[threading.Thread] = []
        self._running = False

    # ---------- 生命周期 ----------

    def start(self) -> None:
        with self._cond:
            if self._running:
                return
            self._running = True
            for i in range(self._workers):
                thread = threading.Thread(
                    target=self._worker, daemon=True, name=f"yona-subrun-{i}"
                )
                thread.start()
                self._threads.append(thread)

    def stop(self, timeout: float = 5.0) -> None:
        """停止接活;仍在排队的活老实结算成 killed(不假装跑过)。"""
        with self._cond:
            if not self._running:
                return
            self._running = False
            while self._pending:
                run_id = self._pending.popleft()
                job = self._jobs.get(run_id)
                if job is not None and job.status == QUEUED:
                    job.status = STATUS_KILLED
                    job.detail = "queue-stopped"
                    job.finished_at = time.time()
                    self._notify(job)
            self._cond.notify_all()
            threads = list(self._threads)
            self._threads.clear()
        for thread in threads:
            thread.join(timeout=timeout)

    # ---------- 投递与查询 ----------

    def submit(self, spec: SubRunSpec, run_id: str | None = None) -> str:
        """入队,立刻返回 run_id(不阻塞)。需要结果时再 wait()。"""
        rid = run_id or _alloc_run_id(self)
        with self._cond:
            self._specs[rid] = spec
            self._jobs[rid] = JobView(
                run_id=rid,
                label=spec.label or spec.task[:40],
                status=QUEUED,
                submitted_at=time.time(),
                parent=spec.parent,
            )
            self._pending.append(rid)
            self._cond.notify_all()
        self.start()  # 幂等:第一次 submit 自动拉起 worker
        return rid

    def snapshot(self, run_id: str) -> dict[str, Any] | None:
        with self._cond:
            job = self._jobs.get(run_id)
            return job.to_dict() if job is not None else None

    def list_jobs(self) -> list[dict[str, Any]]:
        with self._cond:
            return [job.to_dict() for job in self._jobs.values()]

    def record(self, run_id: str) -> SubRunRecord | None:
        with self._cond:
            return self._records.get(run_id)

    def wait(self, run_id: str, timeout: float | None = None) -> SubRunRecord | None:
        """等到终态。超时/未完成返回 None(不抛,调用方自己决定怎么办)。"""
        deadline = None if timeout is None else time.time() + timeout
        with self._cond:
            while True:
                job = self._jobs.get(run_id)
                if job is None:
                    return None
                if job.status not in LIVE_STATES:
                    return self._records.get(run_id)
                remaining = None if deadline is None else deadline - time.time()
                if remaining is not None and remaining <= 0:
                    return None
                self._cond.wait(remaining)

    def wait_all(self, timeout: float | None = None) -> list[dict[str, Any]]:
        """等到队列空(所有活离开非终态)。返回快照列表。"""
        deadline = None if timeout is None else time.time() + timeout
        with self._cond:
            while True:
                if not any(j.status in LIVE_STATES for j in self._jobs.values()):
                    return [job.to_dict() for job in self._jobs.values()]
                remaining = None if deadline is None else deadline - time.time()
                if remaining is not None and remaining <= 0:
                    return [job.to_dict() for job in self._jobs.values()]
                self._cond.wait(remaining)

    def cancel(self, run_id: str) -> bool:
        """取消一件活。只对排队中的有效 —— 正在跑的返回 False(见模块头"诚实边界")。"""
        with self._cond:
            job = self._jobs.get(run_id)
            if job is None or job.status != QUEUED:
                return False
            job.status = STATUS_KILLED
            job.detail = "cancelled"
            job.finished_at = time.time()
            self._specs.pop(run_id, None)  # 惰性出队:worker 拿到时发现已取消
            self._notify(job)
            self._cond.notify_all()
            return True

    def status(self) -> dict[str, Any]:
        """队列总览(给 UI 的"子运行"面板/探针)。"""
        with self._cond:
            jobs = list(self._jobs.values())
            return {
                "workers": self._workers,
                "started": self._running,
                "queued": sum(1 for j in jobs if j.status == QUEUED),
                "running": sum(1 for j in jobs if j.status == RUNNING),
                "completed": sum(1 for j in jobs if j.status == "completed"),
                "failed": sum(1 for j in jobs if j.status == STATUS_FAILED),
                "killed": sum(1 for j in jobs if j.status == STATUS_KILLED),
                "total": len(jobs),
            }

    # ---------- worker ----------

    def _worker(self) -> None:
        while True:
            with self._cond:
                while self._running and not self._pending:
                    self._cond.wait(0.2)
                if not self._running and not self._pending:
                    return
                run_id = self._pending.popleft()
                job = self._jobs.get(run_id)
                spec = self._specs.pop(run_id, None)
                if job is None or spec is None or job.status != QUEUED:
                    continue  # 已被取消(取消时 spec 已摘走)
                job.status = RUNNING
                job.started_at = time.time()
                self._notify(job)
                self._cond.notify_all()

            try:
                rec = self._runner(spec, run_id)
            except Exception as exc:  # noqa: BLE001
                # runner 抛了也不能让 worker 死掉:结算成 failed,队列继续。
                now = time.time()
                rec = SubRunRecord(
                    run_id=run_id,
                    label=spec.label,
                    status=STATUS_FAILED,
                    detail=f"error: {exc}",
                    started_at=job.started_at or now,
                    finished_at=now,
                    parent=spec.parent,
                )

            with self._cond:
                job.status = rec.status
                job.detail = rec.detail
                job.output = rec.output
                job.steps = rec.steps
                job.finished_at = rec.finished_at or time.time()
                self._records[run_id] = rec
                self._notify(job)
                self._cond.notify_all()

    def _notify(self, job: JobView) -> None:
        """状态变化回调(在锁内调用;回调必须轻,别阻塞队列)。"""
        if self._on_change is None:
            return
        try:
            self._on_change(job)
        except Exception:  # noqa: BLE001
            pass


def _alloc_run_id(queue: RunQueue) -> str:
    from .subrun import new_run_id

    while True:
        rid = new_run_id()
        if queue.snapshot(rid) is None:
            return rid
