"""离线生活补写自测(§10 协议):触发判定 + 连续概率采样器 + 时间游标。

对应:
- server/main.py 的 _wake_decision(触发判定,纯函数)
- server/rhythm.py 的 LifeSampler(§10:rate=K×shape 连续概率判定,
  判定本身没变、**抽法** = thinning 直接采样(2026-10-08 换);
  期望件数 = K×∫shape(窗口);事件 = start + budget,无终止时间)
- core/session_log.py 的 time_cursor(补写轮事件落历史时刻)
纯函数/确定性,不调模型。
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

from server.app import engine as sm  # noqa: E402  (原 server.main facade 已移除,直达 engine)
from core.session_log import SessionLog  # noqa: E402
from server.rhythm import (  # noqa: E402
    DEFAULT_DURATION_MIX,
    GRID_SEC,
    LifeSampler,
    ShapeCurve,
    integral_shape,
)

# 注入用"密集表":全天等密度 2.0 → 归一化后均匀 ∫=1。
# 性质测试用它保证区间内必有事件(不依赖真实曲线的稀疏性)。
_DENSE = {h: 2.0 for h in range(24)}


def _ts(hour: int, minute: int, day: int = 5) -> float:
    """构造 09 月 day 日 hour:minute 的时间戳。"""
    return time.mktime((2026, 9, day, hour, minute, 0, 0, 0, -1))


def _mean_events(t0, t1, k, seeds=400, shape=None):
    """seeds 个种子下的事件数均值(蒙特卡洛期望)。"""
    total = 0
    for s in range(seeds):
        total += len(LifeSampler(t0, t1, seed=s, K=k, shape=shape).sample())
    return total / seeds


# ---------- 触发判定 ----------

def test_decision_no_history_no_backfill():
    tr, reason, _ = sm._wake_decision(None, now=_ts(0, 30))
    assert tr is False and "首启" in reason


def test_decision_short_gap_no_backfill():
    last = _ts(0, 20)
    tr, reason, _ = sm._wake_decision(last, now=_ts(0, 25))
    assert tr is False and "正常重启" in reason


def test_decision_long_gap_backfills():
    last = _ts(0, 20, day=4)  # 昨天
    tr, reason, gap = sm._wake_decision(last, now=_ts(0, 30))
    assert tr is True and "补写" in reason
    assert gap > sm.WAKE_AFTER_GAP_SECONDS


def test_anchor_takes_the_later_of_tail_and_cursor():
    """锚点 = max(最后一条事件的时刻, 补写游标)。2026-10-29 补游标。"""
    # 游标比事件新(上次补到更晚)→ 用游标
    assert sm._life_anchor(100.0, 200.0) == 200.0
    # 事件比游标新(你后来又聊过天)→ 用事件
    assert sm._life_anchor(300.0, 200.0) == 300.0
    # 从没补写过(游标 0)→ 退回事件(与改前行为一致)
    assert sm._life_anchor(300.0, 0.0) == 300.0
    # 全无历史 / 只有游标
    assert sm._life_anchor(None, 0.0) is None
    assert sm._life_anchor(None, 200.0) == 200.0


def test_backfill_cursor_closes_the_gap_on_restart():
    """补写完游标 = now → 下次重启不再判成离线(2026-10-29 用户口径)。

    这是"重启又补了一遍"那条 bug 的回归测试:没游标时必然再触发。
    """
    now = _ts(0, 30)
    tail_event = now - 16 * 3600  # 最后一条事件在 16 小时前(事件平均间隔)
    # 改前:锚点只看事件 → 判成离线,反复补
    assert sm._wake_decision(tail_event, now=now)[0] is True
    # 补写跑完把游标推到 now(窗口终点)之后:不再触发
    anchor = sm._life_anchor(tail_event, now)
    tr, reason, _ = sm._wake_decision(anchor, now=now)
    assert tr is False and "正常重启" in reason


# ---------- §10 采样器:连续概率判定语义 ----------

def test_shape_normalized_and_sleep_zero():
    """归一化:∫shape(全天) = 1;睡眠窗内恒 0。"""
    assert abs(integral_shape(0.0, 24.0) - 1.0) < 0.01
    for h in (0.0, 3.5, 6.0, 23.75):
        assert ShapeCurve()(h) == 0.0, h


def test_events_in_range_ordered_nonoverlap():
    """事件 start 在区间内、递增、不重叠;事件"名义结束"不越过 t1。"""
    t0, t1 = _ts(9, 0), _ts(21, 0)
    evs = LifeSampler(t0, t1, seed=42, K=8.0,
                      shape=ShapeCurve(_DENSE)).sample()
    assert len(evs) >= 1
    prev_end = t0
    for e in evs:
        assert t0 <= e.start < e.end <= t1, (e.start, e.end)
        assert e.start >= prev_end, "事件重叠/乱序"
        prev_end = e.end
    # 事件无终止时间:日志语义上只有 start;end 只是统计用的派生属性
    assert all(e.end == e.start + e.budget_min * 60 for e in evs)


def test_events_budget_within_mix_bounds():
    """budget(分钟)落在时长分布覆盖范围;被截断也不低于下限。"""
    t0, t1 = _ts(9, 0), _ts(21, 0)
    evs = LifeSampler(t0, t1, seed=7, K=8.0,
                      shape=ShapeCurve(_DENSE)).sample()
    lo = min(lo_ for _, (lo_, hi_) in DEFAULT_DURATION_MIX)
    hi = max(hi_ for _, (lo_, hi_) in DEFAULT_DURATION_MIX)
    assert 5 <= lo
    for e in evs:
        assert 5 <= e.budget_min <= hi, e


def test_mean_matches_k_times_window_integral():
    """期望件数 = K × ∫shape(窗口):蒙特卡洛均值应贴近积分值。

    晚间 18-21 是真实曲线的高密度区,期望非零且足够验证数值自洽。
    """
    t0, t1 = _ts(18, 0), _ts(21, 0)
    expect = 1.5 * integral_shape(18.0, 21.0)
    mean = _mean_events(t0, t1, k=1.5)
    assert abs(mean - expect) < 0.25, f"均值 {mean:.3f} vs 期望 {expect:.3f}"


def test_k_orthogonal_means_scale():
    """K 只缩放件数:同窗口下 均值/K ≈ 常数(与形状无关)。"""
    t0, t1 = _ts(18, 0), _ts(21, 0)
    means = {k: _mean_events(t0, t1, k=k) for k in (1.0, 1.5, 2.0)}
    base = means[1.0] / 1.0
    for k, m in means.items():
        assert abs(m / k - base) < 0.15, f"K={k} 均值/件数比 {m/k:.3f} 偏离 {base:.3f}"


def test_sleep_window_produces_no_events():
    """深夜 23:30 → 次日 06:30 睡眠窗:shape=0 → 无事件(她在睡)。"""
    evs = LifeSampler(_ts(23, 40), _ts(6, 0, day=6), seed=3).sample()
    assert evs == []


def test_no_event_crosses_sleep_start():
    """事件不把名义消费拖进睡眠窗:22:30 后的命中被裁到 23:30 前。"""
    bad = 0
    total = 0
    for seed in range(200):
        evs = LifeSampler(_ts(22, 30), _ts(23, 35), seed=seed, K=4.0).sample()
        total += len(evs)
        for e in evs:
            if not (e.start < _ts(23, 30) and e.end <= _ts(23, 30) + 1):
                bad += 1
    assert total > 0, "K=4 的 23:30 前窗口不应 200 seed 全空"
    assert bad == 0


def test_events_seeded_reproducible():
    a = LifeSampler(_ts(9, 0), _ts(21, 0), seed=42, K=8.0,
                    shape=ShapeCurve(_DENSE)).sample()
    b = LifeSampler(_ts(9, 0), _ts(21, 0), seed=42, K=8.0,
                    shape=ShapeCurve(_DENSE)).sample()
    assert [(e.start, e.budget_min) for e in a] == \
        [(e.start, e.budget_min) for e in b]


def test_density_curve_time_dependent():
    """shape(t) 是时刻的函数:同样 3h,晚间期望明显高于凌晨(≈0)。"""
    late = _mean_events(_ts(18, 0), _ts(21, 0), k=1.5, seeds=200)
    dawn = _mean_events(_ts(3, 0), _ts(6, 0), k=1.5, seeds=200)  # 全在睡眠窗
    assert dawn == 0.0
    assert late > dawn * 5 and late > 0.3, f"晚间 {late} 应远多于凌晨 {dawn}"


# ---------- §10 抽法:thinning(2026-10-08 换;判定语义不变) ----------

def test_peak_is_a_true_upper_bound():
    """thinning 的接受上界必须是**真上界**。

    接受概率 = rate(t)/上界 —— 上界估小 → 概率 > 1 → 全部接受 →
    分布被抹平成"醒着就均匀"。表是整数结点线性插值 + 睡眠窗阶跃,
    分段线性函数的极值必落在折点上,所以折点求值即精确值。
    """
    for table in (None, _DENSE):     # None = 真实 SHAPE_TABLE
        curve = ShapeCurve(table)
        exact = curve.peak()
        # 细扫(千分之一小时)不得扫出比它更大的值
        fine = max(curve(h / 1000.0) for h in range(24000))
        assert abs(exact - fine) < 1e-9, (exact, fine)
        assert all(curve(h / 100.0) <= exact + 1e-12 for h in range(2400))
    # 上界必须 > 0,且落在真实曲线的峰上(晚间 19-20 是那版的最高点)
    real = ShapeCurve()
    assert abs(real.peak() - real(19.0)) < 1e-9


def test_events_track_shape_between_awake_windows():
    """判定分布跟着 shape 走 —— 不是"醒着就均匀"。

    这是换抽法后最该守住的那道闸:单位/上界写错的**症状**是事件在清醒
    时段退化成均匀分布,那样"上午 7-10"与"晚间 18-21"会一样多(比值≈1)。
    正确的比值贴近 ∫shape 之比(≈2.7),不是"时长之比"(=1)。
    """
    morning = _mean_events(_ts(7, 0), _ts(10, 0), k=1.5, seeds=400)
    evening = _mean_events(_ts(18, 0), _ts(21, 0), k=1.5, seeds=400)
    ratio_expect = integral_shape(18.0, 21.0) / integral_shape(7.0, 10.0)
    assert ratio_expect > 2.0, ratio_expect   # 前提:两段 shape 面积确实差得多
    assert morning > 0, "上午窗口一个事件都没有 —— 这个对照就不成立了"
    ratio = evening / morning
    assert ratio > 1.8, (
        f"晚间/上午 = {ratio:.2f} 太接近 1 —— 分布被抹平成'醒着就均匀'了")
    assert abs(ratio - ratio_expect) < 0.8, (
        f"比值 {ratio:.2f} 偏离 ∫shape 之比 {ratio_expect:.2f}")


def test_events_are_not_quantized_to_the_grid():
    """现版抽的是**连续过程本身**:事件时刻不再被量化到 GRID_SEC 刻度上。

    这条同时是"确实走的 thinning、不是原逐格点版"的指纹 ——
    原版的 start 必然落在 t0 + k·GRID_SEC 的格点上,现版不会。
    """
    t0 = _ts(9, 0)
    starts = []
    for seed in range(40):
        starts += [e.start for e in LifeSampler(
            t0, _ts(21, 0), seed=seed, K=8.0,
            shape=ShapeCurve(_DENSE)).sample()]
    assert starts, "40 个 seed 一件都没有 —— 采样器坏了"
    off_grid = [s for s in starts if abs((s - t0) % GRID_SEC) > 1e-6]
    assert off_grid, "所有事件都落在格点上 —— 还是原来的逐格点实现?"


# ---------- SessionLog 时间游标 ----------

def test_time_cursor_stamps_events():
    log = SessionLog("t")
    wall_before = time.time()
    log.set_time_cursor(_ts(14, 7))
    log.append("turn/start", turn=1, source="self")
    log.clear_time_cursor()
    e = log.events[-1]
    # 事件时间 = 游标(历史时刻),不是墙钟
    assert abs(e.time - _ts(14, 7)) < 1.0
    assert not (wall_before - 10 < e.time < wall_before + 10)


def test_time_cursor_cleared_uses_wallclock():
    log = SessionLog("t")
    log.set_time_cursor(_ts(14, 7))
    log.clear_time_cursor()
    log.append("turn/start", turn=1, source="self")
    e = log.events[-1]
    assert abs(e.time - time.time()) < 5.0


if __name__ == "__main__":
    test_decision_no_history_no_backfill()
    test_decision_short_gap_no_backfill()
    test_decision_long_gap_backfills()
    test_anchor_takes_the_later_of_tail_and_cursor()
    test_backfill_cursor_closes_the_gap_on_restart()
    test_shape_normalized_and_sleep_zero()
    test_events_in_range_ordered_nonoverlap()
    test_events_budget_within_mix_bounds()
    test_mean_matches_k_times_window_integral()
    test_k_orthogonal_means_scale()
    test_sleep_window_produces_no_events()
    test_no_event_crosses_sleep_start()
    test_events_seeded_reproducible()
    test_density_curve_time_dependent()
    test_peak_is_a_true_upper_bound()
    test_events_track_shape_between_awake_windows()
    test_events_are_not_quantized_to_the_grid()
    test_time_cursor_stamps_events()
    test_time_cursor_cleared_uses_wallclock()
    print("backfill(离线生活补写·§10 连续概率判定) all tests passed")
