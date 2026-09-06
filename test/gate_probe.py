"""心跳闸门(gate)探针 —— 方案②验证:概率 = 每天期望 × shape(t) × Δt。

用法: py test/gate_probe.py [days]
纯计算不调模型。验证:
1. 深夜(shape=0)永不醒 —— 概率为 0
2. 蒙特卡洛:模拟 N 天逐分钟判定(间隔=gate.base),命中/天 ≈ wakes_per_day
   (因为 ∫shape(醒着) = 1 —— 形状是那张拍板图,标度=期望次数)
3. 冷却:刚自走过 cooldown 秒内不醒
4. 输出一日命中时刻分布(看是否跟 shape 走:午后/晚间密、上午稀)
"""
import random
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from server.app.gate import SELF_WAKES_PER_DAY, ServerGate  # noqa: E402
from server.rhythm import shape_at  # noqa: E402


def _ts(hour: float) -> float:
    """把"某天的小时数"变成时间戳(用 2026-09-04 为基准日,纯模拟用)。"""
    day0 = time.mktime((2026, 9, 4, 0, 0, 0, 0, 0, -1))
    return day0 + hour * 3600.0


def _fmt_hour(ts: float) -> str:
    lt = time.localtime(ts)
    return f"{lt.tm_hour:02d}:{lt.tm_min:02d}"


def main() -> None:
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    print("=" * 76)
    print(f"gate 探针(方案②): 概率 = wakes_per_day × shape(t) × Δt  "
          f"| wakes_per_day = {SELF_WAKES_PER_DAY}(✅ 2026-09 拍板)")
    print("=" * 76)

    # 1. 深夜永不醒
    n_deep = 5000
    woke_deep = 0
    g = ServerGate(rng=random.Random(1))
    g._last_self_at = 0.0
    t_deep = _ts(2.0)  # 凌晨 2 点(shape=0)
    for _ in range(n_deep):
        if g.check(t_deep):
            woke_deep += 1
        g._last_self_at = 0.0  # 清冷却,只看概率本身
    print(f"1) 凌晨 2 点(shape=0):{n_deep} 次判定醒 {woke_deep} 次"
          f"(应恒 0,概率为 0)")

    # 2. 蒙特卡洛:模拟 days 天,每分钟判定一次(间隔 base)
    gate = ServerGate(rng=random.Random(42))
    hits = []
    for day in range(days):
        day0 = time.mktime((2026, 9, 4 + day, 0, 0, 0, 0, 0, -1))
        count = 0
        t = day0
        for _ in range(24 * 60):
            if gate.check(t):
                count += 1
                gate._last_self_at = t  # 模拟:真醒了一轮 → 冷却(用模拟时刻)
            t += gate.base
        hits.append(count)
    mean = statistics.mean(hits)
    print(f"2) 蒙特卡洛 {days} 天(间隔 {gate.base:.0f}s 判定):"
          f"命中/天 均值 {mean:.2f}(期望 {SELF_WAKES_PER_DAY:.2f})  "
          f"分布 {sorted(hits)}")

    # 3. 冷却
    g3 = ServerGate(rng=random.Random(7))
    g3._last_self_at = _ts(10.0) - 10  # 10 秒前刚自走过
    t10 = _ts(10.0)
    c1 = g3.check(t10)          # 冷却内 → False
    g3._last_self_at = _ts(10.0) - g3.cooldown - 1
    c2 = g3.check(_ts(10.0))    # 冷却外 → 按概率
    print(f"3) 冷却:刚醒 10s 后 check = {c1}(应 False);"
          f"冷却外({g3.cooldown:.0f}s 后)= {c2}(可能 True)")

    # 4. 一日命中时刻分布(单天多次模拟,看形状跟随)
    g4 = ServerGate(rng=random.Random(99))
    hour_hits = [0] * 24
    for day in range(200):
        day0 = time.mktime((2026, 9, 4 + day, 0, 0, 0, 0, 0, -1))
        t = day0
        for _ in range(24 * 60):
            if g4.check(t):
                lt = time.localtime(t)
                hour_hits[lt.tm_hour] += 1
                g4._last_self_at = t
            t += g4.base
    print("4) 命中时刻分布(200 天合计,3h 一段;深夜 0、午后/晚间密 —— 跟随 shape):")
    for h in range(0, 24, 3):
        seg = sum(hour_hits[i] for i in range(h, min(h + 3, 24)))
        print(f"   {h:02d}:00-{min(h+2, 23):02d}:59  {'█' * (seg // 10)}"
              f"{seg}")
    peak = max(shape_at(h) for h in range(0, 24))
    print(f"   (对照 shape 峰值 {peak * 100:.1f}%/小时 @ 19-20 点)")


if __name__ == "__main__":
    main()
