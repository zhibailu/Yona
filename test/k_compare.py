"""K 旋钮效果对比(§10 连续概率判定):K=1 vs K=1.5 的件数分布表。

用法: py test/k_compare.py [seeds]      # 默认 200
纯采样不调模型。要点:
  - 理论期望 = K × ∫shape(窗口),与形状无关(K 只缩放);
  - 实测均值应贴近理论;均值/K 应 ≈ ∫shape(常数,证明正交);
  - K 不碰时刻分布/预算 —— 事件爱在几点出只由 shape 定。
"""
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

from server.rhythm import LifeSampler, integral_shape  # noqa: E402


def _ts(month, day, hour, minute):
    return time.mktime((2026, month, day, hour, minute, 0, 0, 0, -1))


def _fmt(ts):
    return time.strftime("%m-%d %H:%M", time.localtime(ts))


# 典型离线场景:(标签, t0, t1)
SCENES = [
    ("2h  午后 12:00-14:00", _ts(9, 4, 12, 0), _ts(9, 4, 14, 0)),
    ("6h  白天 09:00-15:00", _ts(9, 4, 9, 0), _ts(9, 4, 15, 0)),
    ("3h  晚间 18:00-21:00", _ts(9, 4, 18, 0), _ts(9, 4, 21, 0)),
    ("13h 隔夜 11:35→00:30", _ts(9, 4, 11, 35), _ts(9, 5, 0, 30)),
    ("24h 整天 06:00→次日06:00", _ts(9, 4, 6, 0), _ts(9, 5, 6, 0)),
    ("深夜 22:00-03:00(基本在睡)", _ts(9, 4, 22, 0), _ts(9, 5, 3, 0)),
]
KS = (1.0, 1.5)   # 对比样例(默认值已拍板 1.5;想看别的值改这里)
DAY0 = _ts(9, 4, 0, 0)


def _dist(t0, t1, k, seeds):
    """返回 seeds 个种子的事件数列表。"""
    return [len(LifeSampler(t0, t1, seed=s, K=k).sample()) for s in range(seeds)]


def _row(label, t0, t1, k, seeds):
    counts = _dist(t0, t1, k, seeds)
    n0 = counts.count(0)
    integ = integral_shape((t0 - DAY0) / 3600.0, (t1 - DAY0) / 3600.0)
    mean = statistics.mean(counts)
    dist = {i: counts.count(i) for i in set(counts)}
    over3 = sum(v for i, v in dist.items() if i >= 3)
    pct = lambda x: f"{100.0 * x / seeds:.0f}%"
    return (f"{label:<24} K={k:<4} ∫shape={integ:.3f} "
            f"理论={k * integ:5.2f} | 均值={mean:5.2f} "
            f"| 0件 {pct(n0):>3} 1件 {pct(dist.get(1, 0)):>3} "
            f"2件 {pct(dist.get(2, 0)):>3} 3+件 {pct(over3):>3} "
            f"| 最多 {max(counts)} | 均值/K={mean / k:.3f}")


def main():
    seeds = int(sys.argv[1]) if len(sys.argv) > 1 else 200
    print("=" * 108)
    print(f"K 效果对比 · {seeds} seed/格  (rate = K×shape,逐格点伯努利判定)")
    print("=" * 108)
    header = (f"{'场景':<24} {'K':<6} {'∫shape':<8} {'理论':<6} | "
              f"{'实测均值':<7} | {'0件':<5} {'1件':<5} {'2件':<5} {'3+件':<5} "
              f"| {'最多':<4} | 均值/K")
    print(header)
    print("-" * 108)
    for label, t0, t1 in SCENES:
        for k in KS:
            print(_row(label, t0, t1, k, seeds))
        print("-" * 108)
    print("注:均值/K 应 ≈ ∫shape(每场景常数)→ K 只缩放件数,不碰形状;")
    print("    0件率就是'短间隔/深夜常空'的正常样子(K 越小越常空)。")


if __name__ == "__main__":
    main()
