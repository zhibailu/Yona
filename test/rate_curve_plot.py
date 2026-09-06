"""画 §10 连续概率判定模型的图 → assets/design/rate_curve.png(设计资产,公开可看)

协议(DESIGN.md §10,用户拍板版;曲线/采样全部来自 server/rhythm.py 真实实现):
  rate(t) = K × shape(t)
    shape(t): 归一化曲线(∫shape = 1)—— 只定"事件爱在几点出";
              睡眠窗 23:30-06:30 为 0(在睡,不判定)
    K:        总期望件数 —— 只整体缩放,不改变形状(正交旋钮)
  判定:逐格点伯努利,命中概率 = rate(t)·Δt;命中 → 事件(start+budget),
        跳过 budget 段;判定机会耗尽即结束。期望件数 = ∫rate dt。

图内容:
  上: 候选 shape(t)(晚间峰那版,归一化 ∫=1)—— 形状旋钮,数值待拨
  中: 正交演示:K = 0.5/1.0/1.5/2.0 只缩放不整形,面积 = 期望件数 = K
  下: 一次补写判定轨迹(真实 LifeSampler,固定 seed):红点 = 事件起始,
       灰条 = 该事件的 budget 段(段内不再判定),空白 = 稀疏,
       右端虚线 = 判定机会耗尽,结束

纯计算 + matplotlib,不调模型。需要: py -m pip install matplotlib
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import matplotlib  # noqa: E402
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import font_manager  # noqa: E402

# Windows 中文字体(微软雅黑/黑体);找不到就退回默认(会变方框)
for cand in ("Microsoft YaHei", "SimHei", "SimSun", "Noto Sans CJK SC"):
    try:
        font_manager.findfont(cand, fallback_to_default=False)
        plt.rcParams["font.sans-serif"] = [cand]
        plt.rcParams["axes.unicode_minus"] = False
        break
    except Exception:
        continue

from server.rhythm import (  # noqa: E402
    DEFAULT_K,
    LifeSampler,
    integral_shape,
    rate_at,
    shape_at,
)

OUT = Path(__file__).resolve().parent.parent / "assets" / "design" / "rate_curve.png"

DAY0 = time.mktime((2026, 9, 4, 0, 0, 0, 0, 0, -1))  # 演示基准日(本地时区)


def _ts(hour: float) -> float:
    """当天 hour(可 >24)的时刻戳。"""
    return DAY0 + hour * 3600.0


def fmt_hour(h: float) -> str:
    """小时(可 >24)→ 'HH:MM' 当天时刻。"""
    hh = int(h) % 24
    mm = int(round((h - int(h)) * 60))
    if mm == 60:
        hh = (hh + 1) % 24
        mm = 0
    return f"{hh:02d}:{mm:02d}"


# ---------- 图 ----------

def plot_shape(ax):
    hours = [h / 4 for h in range(0, 97)]  # 0..24 每 15 分钟
    ys = [shape_at(h) for h in hours]
    ax.plot(hours, ys, color="#d97706", lw=2.2,
            label="shape(t):归一化概率曲线(∫shape = 1)")
    ax.fill_between(hours, ys, color="#fef3c7", alpha=0.7)
    ax.axvspan(23.5, 24, color="#1f2937", alpha=0.15)
    ax.axvspan(0, 6.5, color="#1f2937", alpha=0.15)
    ax.text(23.75, 0.075, "睡眠\nshape = 0", fontsize=8.5, color="#374151",
            ha="center", va="center")
    ax.text(3.2, 0.075, "睡眠窗 23:30-06:30:不判定,零事件", fontsize=8.5,
            color="#374151", ha="center", va="center")
    ax.axhline(1.0 / 24.0, color="#6b7280", ls="--", lw=1.0)
    ax.text(0.15, 1.0 / 24.0 + 0.002, "均匀参考 1/24(若事件全天均匀撒的密度)",
            fontsize=7.5, color="#6b7280")
    ax.annotate("晚高峰:晚饭 / 自由时间(≈19-20 点)",
                xy=(19.5, shape_at(19.5)), xytext=(13.2, shape_at(19.5) + 0.02),
                arrowprops=dict(arrowstyle="->", color="#b45309"),
                fontsize=9.5, color="#92400e")
    ax.annotate("午后小峰", xy=(12.5, shape_at(12.5)),
                xytext=(9.0, shape_at(12.5) + 0.015),
                arrowprops=dict(arrowstyle="->", color="#b45309"),
                fontsize=9.5, color="#92400e")
    ax.set_xlim(0, 24)
    ax.set_ylim(0, max(ys) * 1.35)
    ax.set_xticks(range(0, 25, 2))
    ax.set_xlabel("一天的时刻 (时)")
    ax.set_ylabel("shape(t)  概率密度 (1/小时)")
    ax.grid(True, ls=":", alpha=0.4)
    ax.legend(loc="upper left", fontsize=9)
    ax.set_title("上: shape(t)—— 只定『事件爱在几点出』,曲线下面积恒 = 1"
                 f"(峰值 {shape_at(19.0) * 100:.2f}%/小时,待拨旋钮)",
                 fontsize=10.5)


def plot_k(ax):
    hours = [h / 4 for h in range(0, 97)]
    ks = [0.5, 1.0, 1.5, 2.0]
    colors = ["#fbbf24", "#f59e0b", "#ea580c", "#7c2d12"]
    for k, c in zip(ks, colors):
        ys = [rate_at(h, k) for h in hours]
        lw = 3.0 if k == DEFAULT_K else 1.4
        ax.plot(hours, ys, color=c, lw=lw, label=f"K = {k}  (期望 {k} 件)")
    ax.axvspan(23.5, 24, color="#1f2937", alpha=0.12)
    ax.axvspan(0, 6.5, color="#1f2937", alpha=0.12)
    ys15 = [rate_at(h, DEFAULT_K) for h in hours]
    ax.fill_between(hours, ys15, color="#ea580c", alpha=0.18)
    ax.text(23.2, rate_at(19.0, DEFAULT_K) * 0.55,
            f"K = {DEFAULT_K} 的曲线下面积\n= ∫rate dt = {DEFAULT_K} = 期望件数",
            fontsize=9.5, color="#9a3412", ha="right")
    ax.set_xlim(0, 24)
    ax.set_ylim(0, rate_at(19.0, 2.0) * 1.35)
    ax.set_xticks(range(0, 25, 2))
    ax.set_xlabel("一天的时刻 (时)")
    ax.set_ylabel("rate(t) = K×shape(t)  件/小时")
    ax.grid(True, ls=":", alpha=0.4)
    ax.legend(loc="upper left", fontsize=9)
    ax.set_title("中: 正交旋钮 K —— 只整体缩放、不改变形状;期望件数 = K,"
                 "形状 = shape(t)(两者互不干扰)", fontsize=10.5)


def plot_trace(ax):
    # 演示窗口:06:30 起床 → 19:30(13h 醒着,含下午+晚间峰);真实 LifeSampler
    h0, h1 = 6.5, 19.5
    k = DEFAULT_K
    chosen = None
    for seed in range(0, 2000):
        evs = LifeSampler(_ts(h0), _ts(h1), seed=seed, K=k).sample()
        if len(evs) == 2 and (evs[0].start - _ts(h0)) > 0.5 * 3600 \
                and (_ts(h1) - evs[1].start) > 0.5 * 3600:
            chosen = (seed, evs)
            break
    assert chosen, "2000 个 seed 里没找到 2 件的?检查模型"
    seed, evs = chosen

    hs = [h / 60 for h in range(int(h0 * 60), int(h1 * 60) + 1)]
    rates = [rate_at(h, k) for h in hs]
    ax.plot(hs, rates, color="#d97706", lw=1.2, alpha=0.8)
    ax.fill_between(hs, rates, color="#fef3c7", alpha=0.5)

    for i, ev in enumerate(evs, 1):
        s = (ev.start - _ts(h0)) / 3600.0 + h0        # 事件时刻(小时)
        e = min(s + ev.budget_min / 60.0, h1)
        ax.axvspan(s, e, color="#9ca3af", alpha=0.45)
        ax.plot([s], [rate_at(s, k)], "o", color="#dc2626", ms=9, zorder=5)
        ax.annotate(f"事件 {i}\n起始 {fmt_hour(s)}", xy=(s, rate_at(s, k)),
                    xytext=(s + 0.15, rate_at(s, k) * 1.6),
                    fontsize=9, color="#b91c1c",
                    arrowprops=dict(arrowstyle="->", color="#dc2626", lw=1.0))
        ax.text((s + e) / 2, max(rates) * 0.06,
                f"这段时间在做事 {i}\n(不再判定)", fontsize=7.5,
                color="#4b5563", ha="center")
    ax.axvline(h1, color="#111827", ls="--", lw=1.4)
    ax.text(h1 - 0.05, max(rates) * 0.95, "判定机会耗尽 → 结束",
            fontsize=9, color="#111827", ha="right", va="top")

    expect = k * integral_shape(h0, h1)
    ax.set_xlim(h0, h1)
    ax.set_ylim(0, max(rates) * 1.9)
    ax.set_xticks(range(7, 20))
    ax.set_xlabel("时刻 (时)")
    ax.set_ylabel("命中概率密度 (件/小时)")
    ax.grid(True, ls=":", alpha=0.4)
    starts = ", ".join(fmt_hour((e.start - _ts(h0)) / 3600.0 + h0) for e in evs)
    ax.set_title(
        f"下: 一次补写的判定轨迹(seed={seed}, K={k}, 06:30→19:30, 共 {len(evs)} 件:"
        f"{starts})\n期望 = K×∫shape = {expect:.2f} 件 —— 13 小时只中 2 次,"
        f"中间大片空白就是稀疏的正常样子", fontsize=10.5)


def main():
    fig, (ax1, ax2, ax3) = plt.subplots(
        3, 1, figsize=(10.5, 13.5),
        gridspec_kw={"height_ratios": [1, 1, 1.25]},
    )
    fig.suptitle("小夜子 · 离线生活补写 —— 连续概率判定模型  rate(t) = K × shape(t)\n"
                 "每分钟掷一次骰子,命中概率 = rate(t)·Δt;期望件数 = ∫rate dt = K,"
                 "与形状无关", fontsize=13)
    plot_shape(ax1)
    plot_k(ax2)
    plot_trace(ax3)

    fig.tight_layout(rect=(0, 0, 1, 0.94))
    fig.savefig(OUT, dpi=130)
    print(f"saved -> {OUT}")
    print(f"候选 shape 峰值 = {shape_at(19.0) * 100:.2f}%/小时(19-20 点平台)")
    print("下子图:06:30→19:30 窗口  ∫shape = "
          f"{integral_shape(6.5, 19.5):.3f}, K={DEFAULT_K} → 期望 "
          f"{DEFAULT_K * integral_shape(6.5, 19.5):.2f} 件")


if __name__ == "__main__":
    main()
