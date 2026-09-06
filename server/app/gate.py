"""Yona 应用层 · 心跳闸门规则(gate)

方案②(2026-09 用户拍板方向):gate 与离线补写**同一个原语** ——
概率形状继承 rhythm.DEFAULT_SHAPE(用户拍板 K 版曲线:深夜 23:30-06:30 = 0、
晚间高、午后小峰),逐次伯努利判定:

    命中概率 = SELF_WAKES_PER_DAY × shape(t) × Δt
    (Δt ≈ 心跳间隔 base_interval,单位小时)

因此:
  - 时刻倾向**自动继承那张图**:深夜 shape=0 → 自然不醒(在睡);
    午后/晚间更容易"有点想法" —— 不再需要拍"深夜 5%/白天 30%"这类概率
  - 标度只剩一个:**每天期望自发醒次数**(待拍;补写那边对应 K)
  - 命中 → 引擎跑一轮自走(source=self,同一 loop);之后进入冷却
    (cooldown 待拍:刚自走过,一段时间内不再判定)

纯规则、零 LLM、随机注入 rng(可测)。旧版拍脑袋概率已废弃
(深夜 0.05/白天 0.30 + hot 分支,见 git 前 server/engine.py)。
"""

from __future__ import annotations

import random
import time

from ..params import (  # noqa: E402  唯一事实来源;查看: py server/params.py
    HEARTBEAT_COOLDOWN_SEC,
    HEARTBEAT_INTERVAL_SEC,
    SELF_WAKES_PER_DAY,
)
from ..rhythm import DEFAULT_SHAPE


class ServerGate:
    """心跳闸门:check 值不值得让 LLM 醒一次(纯规则,零 LLM)。

    参数默认值来自 server/params.py —— 2026-09 已拍板
    (cooldown=90s / interval=60s / wakes=3 次每天;见 params.py)。
    """

    def __init__(
        self,
        cooldown: float = HEARTBEAT_COOLDOWN_SEC,   # 冷却(秒):刚自走过,多久内不醒
        base_interval: float = HEARTBEAT_INTERVAL_SEC,  # 心跳间隔(秒):多久判定一次
        wakes_per_day: float | None = None,  # None → SELF_WAKES_PER_DAY
        rng: random.Random | None = None,
    ) -> None:
        self.cooldown = cooldown
        self.base = base_interval
        self.wakes = SELF_WAKES_PER_DAY if wakes_per_day is None else wakes_per_day
        self._rng = rng if rng is not None else random.Random()
        self._last_self_at = 0.0

    def check(self, now: float) -> bool:
        if now - self._last_self_at < self.cooldown:
            return False  # 冷却:刚自走过不久,不醒
        lt = time.localtime(now)
        hour = lt.tm_hour + lt.tm_min / 60.0 + lt.tm_sec / 3600.0
        dt_h = self.base / 3600.0  # 每次判定覆盖的时长(≈心跳间隔)
        p = self.wakes * DEFAULT_SHAPE(hour) * dt_h
        if p <= 0:
            return False  # 深夜(shape=0):在睡,不醒
        return self._rng.random() < p

    def next_interval(self, now: float) -> float:
        return self.base

    def mark_self(self):
        """真跑了一轮自走后才调(由 LifeLoop 在 run_turn 后调)→ 进入冷却。"""
        self._last_self_at = time.time()
