"""shape(t) ASCII 曲线 + 期望件数(§10 协议:期望 = K × ∫shape(窗口))。

DESIGN.md §10:rate(t) = K × shape(t),逐格点连续概率判定;
期望事件数 = K·∫shape(窗口)。本脚本画归一化 shape 的 24h 形状,
再列几个窗口的期望件数(K 当前示例 DEFAULT_K,待拨)。
ASCII 直方图,不依赖 matplotlib。纯计算,不调模型。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

from server.rhythm import DEFAULT_K, DEFAULT_SHAPE, integral_shape  # noqa: E402


def draw_curve():
    cur = DEFAULT_SHAPE
    print("24h shape(t)(归一化 ∫=1;睡眠窗 23:30-06:30 = 0,晚间最高)")
    print("-" * 62)
    print("时刻     shape     柱状(每个 ▏≈0.005)")
    print("-" * 62)
    for h in range(24):
        v = cur(h)
        bar = "█" * int(v / 0.005) + ("▏" if (v / 0.005) % 1 >= 0.5 else "")
        print(f" {h:02d}:00   {v:.3f}  {bar}")
    print("-" * 62)

    # 区间期望 = K × ∫shape(不同离线位置)
    print(f"\n期望件数 = K × ∫shape(窗口),K 当前示例 = {DEFAULT_K}(待拨):")
    cases = [
        ("凌晨 03-06 (3h,睡)", 3, 6),
        ("深夜 22:00-02:00 (4h)", 22, 26),
        ("白天 09:00-15:00 (6h)", 9, 15),
        ("晚间 18:00-21:00 (3h)", 18, 21),
        ("整天 06:30-23:30 (17h)", 6.5, 23.5),
        ("13h 隔夜 11:35→00:30", 11.583, 24.5),
        ("48h(两个白天)", 6.5, 54.5),
    ]
    for name, h0, h1 in cases:
        expect = DEFAULT_K * integral_shape(h0, h1)
        print(f"  {name:<26}: {expect:.2f} 件")


if __name__ == "__main__":
    draw_curve()
