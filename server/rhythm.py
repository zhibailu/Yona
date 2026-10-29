"""小夜子 · 离线生活补写采样器 —— DESIGN.md §10 连续概率判定模型。

模型(用户拍板,DESIGN.md §10):
    rate(t) = K × shape(t)
      shape(t): 归一化曲线(∫shape = 1)—— 只定"事件爱在几点出";
                睡眠窗(23:30-06:30)内为 0:她在睡,不判定。
      K:        总期望件数 —— 只整体缩放、不改变形状(与形状正交的旋钮)。
    判定:命中概率 = rate(t)·Δt —— 命中 → 生成一个事件(start 时刻 + budget),
          并跳过 budget 段(这段时间她在做那件事,不再判定)。
          期望事件数 = ∫rate dt = K·∫shape(曲线下面积,可微积分预计算)。
    结束:判定机会耗尽即止(不是"时间撞到 t1 才算完")—— 稀疏时段自然大片
          不命中;若命中的事件被睡眠窗/t1 截得不足 5 分钟,视为"没来得及
          做",不记(与"不硬凑一条"同义)。

判定的**抽法**(2026-10-08 换;模型一个字没改,只换抽法):
    原版 = 从 t0 按分钟(GRID_SEC)逐格点走到 t1,每格掷一次伯努利骰子。
           成本 O(窗口/格点) —— 窗口一长就白掷几十万次空骰子
           (365 天 = 525,601 格,几乎全部落在不命中/睡眠窗上)。
    现版 = **thinning(Lewis-Shedler 拒绝采样)** 直接抽事件时刻:
           按 rate 的**上界**抽一个候选间隔 → 按 rate(t)/上界 接受或丢掉。
           成本 O(事件数) —— 同一窗口从 43,200 格降到约 39 件,只换抽法、
           分布不变(实测 30 天 33×、365 天 128×;对拍与数字见
           `docs/decisions/TIMELINE.md` 2026-10-08 那节)。
    等价关系:逐格伯努利在 Δt→0 时的极限 = 这条连续过程;thinning 抽的是
           **极限本身**,原版抽的是它的**离散步进** —— 所以原版的事件时刻
           被量化在分钟刻度上,现版不会。这不改变"期望件数 = K·∫shape"。
    ⚠️ 单位:rate 是"件/**小时**"、t 是 Unix **秒** —— 抽指数间隔必须用
       "每秒"速率(÷3600),接受概率必须用"每小时"上界。两者混用差 3600 倍:
       接受概率恒 > 1 → 件数翻倍、shape 被抹平成"清醒时段均匀"。
       (这个错在实验台上踩过两次,所以下面的变量名带 `_h` / `_s` 后缀。)

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

    def peak(self) -> float:
        """曲线在睡眠窗外的最大值(1/小时)—— thinning 的接受上界。

        表是**整数小时结点上的线性插值**,睡眠窗是**阶跃**(窗内恒 0);
        分段线性函数的极值必落在折点上 —— 所以只需在 0..23 与睡眠窗终点
        (SLEEP_END_H)这些位置求值,不必扫细步。共 26 次求值。

        ⚠️ 这个上界**必须是真上界**:thinning 的接受概率 = rate(t)/上界,
        上界估小 → 概率 > 1 → 全部接受 → 分布被抹平。见模块头的单位警告。
        """
        pts = [float(h) for h in range(24)] + [SLEEP_END_H]
        return max(self(h) for h in pts)


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


def draw_budget_min(
    rng: random.Random | None = None,
    duration_mix: list[tuple[float, tuple[int, int]]] | None = None,
) -> float:
    """抽一次事件预算(分钟)—— 命中后/醒来那轮的"这段时间约 X 分钟"。

    普通轮(心跳/自走)与补写轮共用同一个时长分布(2026-09 用户拍板:
    普通轮也用 LifeSampler 同款预算),数值来源唯一 = params.DEFAULT_DURATION_MIX。
    rng 注入(可测/可固定复现)。
    """
    mix = duration_mix or DEFAULT_DURATION_MIX
    rng = rng if rng is not None else random.Random()
    r = rng.random()
    acc = 0.0
    for prob, (lo, hi) in mix:
        acc += prob
        if r <= acc:
            return rng.uniform(lo, hi)
    lo, hi = mix[-1][1]
    return rng.uniform(lo, hi)


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
    """§10 连续概率判定 —— 用 thinning 直接抽事件时刻(见模块头"判定的抽法")。

    构造签名 / `Event` / 截断规则与原逐格点版**一字未改**;变的只有"怎么抽"。
    同一 seed 下逐事件结果与原版不同(两个不同的随机过程),但**分布相同**
    —— 调用方都不持久化 seed(引擎走 `rng=None`),所以没有兼容问题。
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
        lam_max_h = self.K * self._shape.peak()   # 件/小时 —— 接受概率用
        if lam_max_h <= 0.0:
            return events                         # 全天 shape=0:没有醒着的时候
        lam_max_s = lam_max_h / 3600.0            # 件/秒 —— 抽间隔用(t 是秒)
        t = self.t0
        while True:
            t = self._next_event_after(t, lam_max_h, lam_max_s)
            if t is None:
                break                             # 到 t1 都没有判定机会了
            raw_min = self._duration_min()
            end = min(t + raw_min * 60.0, self.t1, _sleep_start_after(t))
            if end - t >= MIN_EVENT_SEC:
                # 事件只记 start;budget = 截断后的当轮时长(睡前/收尾
                # 自然会短),跳过这段,期间她"在做那件事",不再判定
                events.append(Event(start=t, budget_min=(end - t) / 60.0))
                t = end
            else:
                # 被睡眠窗/t1 截得不足最短事件 = "没来得及做",不记。
                # 往前挪一格再抽(与原逐格点版一致 —— 不挪的话会在睡前
                # 反复抽到几乎同一刻,空转)
                t += GRID_SEC
        return events

    def _next_event_after(self, t: float, lam_max_h: float,
                          lam_max_s: float) -> float | None:
        """t 之后第一个被接受的判定时刻;到 t1 都没有则 None。

        thinning:先按上界抽一个候选(指数间隔),再按 rate(t)/上界 接受。
        睡眠窗内 rate = 0 —— 必然拒绝,连骰子都不用掷,直接整段跳过。
        """
        while True:
            t += self.rng.expovariate(lam_max_s)
            if t >= self.t1:
                return None
            hour = _hour_of(t)
            if in_sleep(hour):
                continue                          # rate=0:必然拒绝
            lam_h = self.K * self._shape(hour)
            if lam_h <= 0.0:
                continue
            # 上界由 ShapeCurve.peak() 精确给出;min 只兜浮点毛刺
            if self.rng.random() < min(1.0, lam_h / lam_max_h):
                return t

    def _duration_min(self) -> float:
        """抽时长(与模块级 draw_budget_min 同一实现,复用单点)。"""
        return draw_budget_min(rng=self.rng, duration_mix=self.mix)
