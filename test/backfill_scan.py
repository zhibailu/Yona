"""采样丰富度批量扫描:同一间隔 × 很多 seed → 形状多样性,找算法目标偏差。

用法: py test/backfill_scan.py [间隔] [seed数]
间隔: 2h/4h/6h/13h/24h/night/long(默认 13h)
seed数: 默认 80

输出:
1. 形状分组:空 / 1件 / 2件 / 3件 / 4件+ 各多少个(seed 落在哪组)
2. 每组的代表性时间段样本(时间戳 + 时长)—— 看聚集/稀疏/位置是否多样
3. 一段内的时长时间分布:是否存在"段长离谱"或"全是一样长"的规律性

不调模型,纯采样。跑 80 seed 秒级完成。
"""
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

from server.rhythm import LifeSampler  # noqa: E402


def _ts(month, day, hour, minute):
    return time.mktime((2026, month, day, hour, minute, 0, 0, 0, -1))


def _fmt(ts):
    return time.strftime("%m-%d %H:%M", time.localtime(ts))


INTERVALS = {
    "2h": (_ts(9, 4, 12, 0), _ts(9, 4, 14, 0)),
    "4h": (_ts(9, 4, 13, 0), _ts(9, 4, 17, 0)),
    "6h": (_ts(9, 4, 9, 0), _ts(9, 4, 15, 0)),
    "13h": (_ts(9, 4, 11, 35), _ts(9, 5, 0, 30)),
    "24h": (_ts(9, 4, 6, 0), _ts(9, 5, 6, 0)),
    "night": (_ts(9, 4, 22, 0), _ts(9, 5, 3, 0)),
    "long": (_ts(9, 3, 8, 0), _ts(9, 5, 8, 0)),
}


def _shape_sig(evs):
    """形状签名:起始时刻 + 预算 → 看多样性。"""
    return " | ".join(
        f"{_fmt(e.start).split(' ')[1]}~{e.budget_min:.0f}m" for e in evs
    )


def scan(label, n_seeds=80):
    t0, t1 = INTERVALS[label]
    print("=" * 74)
    print(f"扫描 {label}: {_fmt(t0)} → {_fmt(t1)}  × {n_seeds} seed")
    print("=" * 74)

    # 按形状分组(事件数)
    groups: dict[int, list[tuple[int, list]]] = {}
    for seed in range(n_seeds):
        evs = LifeSampler(t0, t1, seed=seed).sample()
        groups.setdefault(len(evs), []).append((seed, evs))

    print("\n【形状分布】")
    for n in sorted(groups):
        seeds = groups[n]
        print(f"  {n} 件: {len(seeds)}/{n_seeds}  seed 例: "
              f"{sorted(s[0] for s in seeds)[:8]}")

    # 每组展示几个代表性事件样本(前 4 个)
    print("\n【代表性样本(每形状取前 4 个 seed 的事件)】")
    for n in sorted(groups):
        print(f"  --- {n} 件 ---")
        for seed, evs in groups[n][:4]:
            print(f"    seed {seed:3d}: {_shape_sig(evs)}")
            if n == 0:
                break

    # 预算分布(统计所有 seed 的每件事 budget;budget 不进日志,只当轮可见)
    all_budgets = []
    for seed in range(n_seeds):
        for e in LifeSampler(t0, t1, seed=seed).sample():
            all_budgets.append(e.budget_min)
    if all_budgets:
        mean = statistics.mean(all_budgets)
        print(f"\n【预算分布】n={len(all_budgets)} 均值 {mean:.0f}m "
              f"min {min(all_budgets):.0f}m max {max(all_budgets):.0f}m")
        print(f"  分布(10~30/30~60/60~120/120+m): "
              f"{sum(1 for d in all_budgets if 10<=d<30)}/"
              f"{sum(1 for d in all_budgets if 30<=d<60)}/"
              f"{sum(1 for d in all_budgets if 60<=d<120)}/"
              f"{sum(1 for d in all_budgets if d>=120)}")


if __name__ == "__main__":
    label = sys.argv[1] if len(sys.argv) > 1 else "13h"
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 80
    scan(label, n)
