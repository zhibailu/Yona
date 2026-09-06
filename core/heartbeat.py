"""Yona 新内核 · 后台心跳(Heartbeat)—— P3 后台生命循环

VISION 决策 2/7:进程活着她就活着。心跳线程按节奏醒来:
  醒来 → 问门(Gate):"值得让 LLM 醒一次吗?"(纯规则,零 LLM 成本)
       → 门说值得:loop.run_turn(source="self")(同一内核、同一套工具)
       → 门说不值得:继续睡,门决定下次多久再醒

设计点:
- Gate 是可插拔的(小夜子给规则,内核只定义接口)—— 纯规则闸门,
  subagent 增强 = 未来倾向(VISION 决策 7)。
- 前台让步:用户 turn 来时心跳不抢(同一时刻只跑一个 turn,loop 内部锁保证)。
- 状态可查(status):供 UI 显示"她正在忙/在睡/闲"。
- 借鉴旧 Yona autonomy 调度骨架:启动延迟、间隔抖动、失败退避。
"""

from __future__ import annotations

import random
import threading
import time
from dataclasses import dataclass
from typing import Callable, Protocol


class Gate(Protocol):
    """低成本闸门:纯规则判断"现在值不值得让 LLM 醒一次"。"""

    def check(self, now: float) -> bool: ...  # True = 值得醒

    def next_interval(self, now: float) -> float: ...  # 下次多久醒来(秒)


@dataclass
class HeartbeatResult:
    cycle: int  # 第几次心跳
    woke: bool  # 这次是否过了门(调了 LLM)
    reason: str  # "gate-rejected" | "ran-turn" | "error"
    interval: float  # 下次间隔(秒)


class Heartbeat:
    """后台心跳:按节奏醒来问 Gate,过了门才让 loop 自走一轮。"""

    def __init__(
        self,
        loop,
        gate: Gate,
        startup_delay: float = 5.0,
        min_interval: float = 10.0,
        max_interval: float = 3600.0,
        jitter: float = 0.2,  # 间隔 ±20% 抖动,防机械准点
        on_error: Callable[[Exception], None] | None = None,
        tools=None,  # 自走轮的工具(默认 loop 全量)
    ) -> None:
        self.loop = loop
        self.gate = gate
        self.startup_delay = startup_delay
        self.min_interval = min_interval
        self.max_interval = max_interval
        self.jitter = jitter
        self.on_error = on_error
        self._tools = tools  # None = loop 全量
        self._running = False
        self._thread: threading.Thread | None = None
        self._cycles = 0
        self._last_result: HeartbeatResult | None = None
        self._last_wake_at: float | None = None
        self._busy_until: float = 0.0  # 前台占用时,心跳让路到此刻

    # ---------- 生命周期 ----------

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="yona-heartbeat"
        )
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    # ---------- 查询(UI 用) ----------

    def status(self) -> dict:
        return {
            "running": self._running,
            "cycles": self._cycles,
            "last": self._last_result,
            "last_wake_at": self._last_wake_at,
            "busy_until": self._busy_until,
        }

    # ---------- 内部 ----------

    def _run(self) -> None:
        if self.startup_delay > 0:
            if not self._sleep_while_running(self.startup_delay):
                return
        while self._running:
            self._last_wake_at = time.time()
            self._cycles += 1
            interval = self._cycle_once()
            self._last_result = interval
            delay = self._jittered(interval.interval)
            if not self._sleep_while_running(delay):
                return

    def _cycle_once(self) -> HeartbeatResult:
        now = time.time()
        # 前台占用(loop 锁被用户 turn 拿着)时,让路
        if now < self._busy_until:
            return HeartbeatResult(
                cycle=self._cycles, woke=False,
                reason="yield-foreground",
                interval=self.min_interval,
            )
        try:
            if not self.gate.check(now):
                return HeartbeatResult(
                    cycle=self._cycles, woke=False,
                    reason="gate-rejected",
                    interval=self.gate.next_interval(now),
                )
        except Exception as exc:  # noqa: BLE001
            if self.on_error:
                self.on_error(exc)
            return HeartbeatResult(
                cycle=self._cycles, woke=False, reason="error",
                interval=self._bounded(60.0),
            )
        # 过了门:让 LLM 自走一轮(同一内核、同一套工具)
        try:
            self.loop.run_turn(source="self", tools=self._tools)
            return HeartbeatResult(
                cycle=self._cycles, woke=True, reason="ran-turn",
                interval=self.gate.next_interval(time.time()),
            )
        except Exception as exc:  # noqa: BLE001
            if self.on_error:
                self.on_error(exc)
            return HeartbeatResult(
                cycle=self._cycles, woke=False, reason="error",
                interval=self._bounded(120.0),
            )

    # ---------- 工具 ----------

    def _sleep_while_running(self, seconds: float) -> bool:
        """分段睡眠,stop() 能及时打断。返回 False = 被要求停止。"""
        end = time.time() + seconds
        while self._running and time.time() < end:
            time.sleep(min(0.2, end - time.time()))
        return self._running

    def _jittered(self, seconds: float) -> float:
        return self._bounded(seconds * (1.0 + random.uniform(-self.jitter, self.jitter)))

    def _bounded(self, seconds: float) -> float:
        return max(self.min_interval, min(self.max_interval, seconds))

    def mark_foreground_busy(self, seconds: float) -> None:
        """前台用户 turn 占用期间,心跳让路(循环锁 + 此处让路双保险)。"""
        self._busy_until = time.time() + seconds
