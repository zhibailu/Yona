"""小夜子 · 离线生活补写采样器 —— DESIGN.md §10 连续概率判定模型。

模型(用户拍板,DESIGN.md §10):
    rate(t) = K × shape(t)
      shape(t): 归一化曲线(∫shape = 1)—— 只定"事件爱在几点出";
                睡眠窗(23:30-06:30)内为 0:她在睡,不判定。
      K:        总期望件数 —— 只整体缩放、不改变形状(与形状正交的旋钮)。
    判定:程序从 t0(离线起点)按固定粒度(默认分钟级)逐格点走到 t1(开机),
          每个格点掷一次伯努利骰子:命中概率 = rate(t)·Δt。
          命中 → 生成一个事件(start 时刻 + budget);跳过 budget 内的格点
          (这段时间她在做那件事,不再判定)。不中 → 下一格点。
          期望事件数 = ∫rate dt = K·∫shape(曲线下面积,可微积分预计算)。
    结束:判定机会耗尽即止(不是"时间撞到 t1 才算完")—— 稀疏时段自然大片
          不命中;若命中的事件被睡眠窗/t1 截得不足 5 分钟,视为"没来得及
          做",不记(与"不硬凑一条"同义)。

事件 = 起始时间 + budget(无终止时间,用户定的):
    budget 不进日志;只当轮可见 —— 给模型当"这段时间约 X 分钟,只做
    一件事、别做做不完的事"的上限、跳过后续判定、算相对时间。

模型零时间权力:事件该在几点出、持续多久,全由本采样器决定;模型只拿到
start 时刻,自己决定那时在做什么。一个时间段 = 一件事(prompt 强调)。

所有随机走注入 rng(可单测、可固定复现)。**参数唯一来源 = server/params.py**
(睡眠窗/shape/K/时长分布/粒度全在那,每项带拍板状态;查看: py server/params.py)。
本模块只留算法。图/测试/探针自动跟着 params 变。
"""

from __future__ import annotations

import datetime as _dt
import random
import time
from dataclasses import dataclass

# 语义参数全部来自 server/params.py(唯一事实来源;不要在本文件再定义)
from .params import (  # noqa: E402
    DEFAULT_DURATION_MIX,
    DEFAULT_K,
    GRID_SEC,
    MIN_EVENT_SEC,
    SHAPE_TABLE,
    SLEEP_END_H,
    SLEEP_START_H,
)


def density_at(hour: float, table: dict[int, float] | None = None) -> float:
    """某时刻(小数小时)的表值(线性插值,未归一化)。"""
    t = table or SHAPE_TABLE
    h = hour % 24.0
    lo = int(h) % 24
    hi = (lo + 1) % 24
    frac = h - int(h)
    return t[lo] * (1 - frac) + t[hi] * frac


def in_sleep(hour: float) -> bool:
    """某时刻是否在睡眠窗内(shape 应为 0)。"""
    h = hour % 24.0
    return h >= SLEEP_START_H or h < SLEEP_END_H


def _hour_of(t: float) -> float:
    lt = time.localtime(t)
    return lt.tm_hour + lt.tm_min / 60.0


def _sleep_start_after(t: float) -> float:
    """t 之后下一个睡眠窗起点(当天 23:30;t 已睡时返回 t 本身,不会用到)。"""
    lt = time.localtime(t)
    start = _dt.datetime(lt.tm_year, lt.tm_mon, lt.tm_mday) + _dt.timedelta(
        hours=SLEEP_START_H
    )
    return start.timestamp()


def _integral_raw(table: dict[int, float], step: float = 0.005) -> float:
    """表在睡眠窗外的 24h 积分(归一化常数;睡眠窗内本来就该是 0)。"""
    total = 0.0
    h = 0.0
    while h < 24.0:
        if not in_sleep(h):
            total += density_at(h, table) * step
        h += step
    return total


class ShapeCurve:
    """归一化概率曲线 shape(h):∫shape = 1,睡眠窗内 = 0。

    构造一次就缓存归一化常数;可调用:shape(h) → 概率密度(1/小时)。
    """

    def __init__(self, table: dict[int, float] | None = None) -> None:
        self.table = table or SHAPE_TABLE
        self.z = _integral_raw(self.table)
        assert self.z > 0, "形状表全天积分必须 > 0(睡眠窗外的表值全 0?)"

    def __call__(self, hour: float) -> float:
        if in_sleep(hour):
            return 0.0
        return density_at(hour, self.table) / self.z


DEFAULT_SHAPE = ShapeCurve()   # 默认候选曲线(晚间峰那版,归一化)


def shape_at(hour: float, shape: ShapeCurve | None = None) -> float:
    """默认归一化 shape(h) 的便捷函数(等价 DEFAULT_SHAPE(h))。"""
    return (shape or DEFAULT_SHAPE)(hour)


def rate_at(hour: float, k: float = DEFAULT_K,
            shape: ShapeCurve | None = None) -> float:
    """rate(t) = K × shape(t):件/小时(命中概率密度)。"""
    return k * (shape or DEFAULT_SHAPE)(hour)


def integral_shape(h0: float, h1: float, shape: ShapeCurve | None = None,
                   step: float = 0.005) -> float:
    """∫shape 从 h0 到 h1(小时);h1 可越过 24(跨天,按 %24 取)。"""
    cur = shape or DEFAULT_SHAPE
    total = 0.0
    h = h0
    while h < h1:
        total += cur(h % 24.0) * step
        h += step
    return total


@dataclass
class Event:
    """一次补写事件:起始时刻 + budget(分钟)。

    budget 不进日志、不是事件属性,只当轮可见(限制/跳过/相对时间)。
    end 是派生态(仅统计/不变量用):start + budget。
    """

    start: float
    budget_min: float

    @property
    def end(self) -> float:
        return self.start + self.budget_min * 60.0


class LifeSampler:
    """§10 逐格点连续概率判定:从 t0 走到 t1,每分钟掷一次伯努利骰子。

    命中概率 = rate(t)·Δt;命中 → 事件 + 跳过 budget 段;不中 → 下一格点。
    判定机会耗尽(t >= t1)即结束。期望事件数 = K × ∫shape(窗口)。
    """

    def __init__(
        self,
        t0: float,
        t1: float,
        rng: random.Random | None = None,
        seed: int | None = None,
        K: float = DEFAULT_K,
        shape: ShapeCurve | None = None,
        shape_table: dict[int, float] | None = None,
        duration_mix: list[tuple[float, tuple[int, int]]] | None = None,
    ) -> None:
        self.t0 = t0
        self.t1 = t1
        self.rng = rng if rng is not None else random.Random(seed)
        assert K > 0, "K(总期望件数)必须 > 0"
        self.K = K
        if shape is not None and shape_table is not None:
            raise ValueError("shape 与 shape_table 二选一")
        self._shape = shape or ShapeCurve(shape_table)
        self.mix = duration_mix or DEFAULT_DURATION_MIX

    # ---------- 采样 ----------

    def sample(self) -> list[Event]:
        events: list[Event] = []
        t = self.t0
        dt_h = GRID_SEC / 3600.0     # 格点时长(小时)
        while t < self.t1:
            hour = _hour_of(t)
            if not in_sleep(hour):
                # 命中概率 = rate(t)·Δt(K×shape 件/小时 × Δt 小时)
                if self.rng.random() < self.K * self._shape(hour) * dt_h:
                    raw_min = self._duration_min()
                    end = min(t + raw_min * 60.0, self.t1,
                              _sleep_start_after(t))
                    if end - t >= MIN_EVENT_SEC:
                        # 事件只记 start;budget = 截断后的当轮时长(睡前/收尾
                        # 自然会短),跳过这段,期间她"在做那件事",不再判定
                        events.append(Event(start=t,
                                            budget_min=(end - t) / 60.0))
                        t = end
                        continue
            t += GRID_SEC
        return events

    def _duration_min(self) -> float:
        r = self.rng.random()
        acc = 0.0
        for prob, (lo, hi) in self.mix:
            acc += prob
            if r <= acc:
                return self.rng.uniform(lo, hi)
        lo, hi = self.mix[-1][1]
        return self.rng.uniform(lo, hi)
