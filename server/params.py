"""Yona · 产品参数唯一来源(2026-09 收敛)—— 所有"语义旋钮"集中一处。

背景:参数曾散在 rhythm.py/gate.py/engine.py(每处注释"待拍/拍板"),用户
拍板**收敛单点**:代码只从这里取;每项注释带拍板状态,防止"做完就忘"。
查看参数全景: py server/params.py   (类似 dsh 的 --dump-config)
密钥(.env LLM_*)不在此文件 —— 那是 secrets,见 config.py。

拍板状态图例:
  ✅ = 用户拍板(日期)
  ⏳ = 待拍(2026-09 收编时的沿用值;拍板后改这里,改完把 ⏳ 换成 ✅)
  🔧 = 开发/演示参数,非产品语义(仅 YONA_GATE_HOT 演示用)
"""

from __future__ import annotations

# ============================================================
# 一、睡眠窗 / 判定粒度 / 最短事件(离线补写协议 §10)
# ============================================================

SLEEP_START_H = 23.5   # ✅ 2026-09 §10:睡眠窗起点(23:30)
SLEEP_END_H = 6.5      # ✅ 2026-09 §10:睡眠窗终点(次日 06:30),窗内 shape=0
GRID_SEC = 60.0        # ✅ 2026-09 §10:判定粒度,默认分钟级(13h≈780 格 <1ms)
MIN_EVENT_SEC = 300.0  # ✅ 2026-09 §10:被睡眠窗/终点截到不足 5 分钟的事件不硬凑

# ============================================================
# 二、离线生活补写:rate = K × shape(§10,唯一协议)
# ============================================================

# 候选形状表(未归一化,"晚间峰那版";睡眠窗外各时刻相对密度,0-6h 已是 0,
# 23:00 残余在归一化时 clip)。形状 = 只定"事件爱在几点出"。
# ⏳ 待最终拍:现用"晚间峰那版"(K/mix 已拍,形状曲线没最终确认)。
SHAPE_TABLE: dict[int, float] = {
    0: 0.00, 1: 0.00, 2: 0.00, 3: 0.00, 4: 0.00, 5: 0.00, 6: 0.00,
    7: 0.05, 8: 0.06, 9: 0.07, 10: 0.07, 11: 0.08,
    12: 0.11, 13: 0.09, 14: 0.07, 15: 0.06, 16: 0.08,
    17: 0.12, 18: 0.15, 19: 0.18, 20: 0.18, 21: 0.14, 22: 0.10, 23: 0.05,
}

DEFAULT_K = 1.5  # ✅ 2026-09 拍板:总期望件数(正交旋钮,只定件数与形状无关)
# budget 时长混合分布(分钟):(概率, [下限, 上限])
# ✅ 2026-09 拍板"就这样用":短事 15% 10-25m / 中事 55% 30-90m / 长事 30% 100-200m
DEFAULT_DURATION_MIX = [
    (0.15, (10, 25)),
    (0.55, (30, 90)),
    (0.30, (100, 200)),
]

# ============================================================
# 三、心跳 / 离线补写触发(2026-09 收编进 params,多为沿用值)
# ============================================================

# ✅ 2026-09(S1 定):离线超过该时长重启 → 触发离线生活补写
WAKE_AFTER_GAP_SECONDS = 30 * 60

# ✅ 2026-09 拍板:gate 三值(cooldown=90s / interval=60s / wakes=3/天)
#   check 命中概率 = SELF_WAKES_PER_DAY × shape(t) × Δt
#   形状自动来自 SHAPE_TABLE(深夜=0 不醒);wakes 对标补写的 K(活着时的自语
#   频率,与"值得记的事"是两码事);验证:test/gate_probe.py。
SELF_WAKES_PER_DAY = 3.0   # ✅ 2026-09 拍板:每天期望自发醒次数
HEARTBEAT_COOLDOWN_SEC = 90.0   # ✅ 2026-09 拍板:刚自走过,多久内不醒
HEARTBEAT_INTERVAL_SEC = 60.0   # ✅ 2026-09 拍板:心跳多久判定一次

# ⏳ 待拍(沿用 P3 调度默认):心跳线程节奏约束
HEARTBEAT_STARTUP_DELAY = 15.0   # 启动后多久开始问门
HEARTBEAT_MIN_INTERVAL = 45.0    # 间隔下限
HEARTBEAT_MAX_INTERVAL = 600.0   # 间隔上限

# ⏳ 待拍(沿用 P3):补写线程在服务起来后等多久再跑(防抢用户首条消息)
BACKFILL_START_DELAY_SEC = 5.0

# 🔧 演示模式(YONA_GATE_HOT=1):判定更密/冷却更短/标度更大 —— 想看她动
# 时不用等概率;非产品参数。
HOT_COOLDOWN_SEC = 20.0
HOT_INTERVAL_SEC = 20.0
HOT_WAKES_PER_DAY = 240.0


# ============================================================
# 四、LLM 调用默认(2026-09 拍板;输出上限是服务端固定值,不暴露给 UI)
# ============================================================

# ✅ 2026-09 拍板:默认温度 = UI 旧默认 0.9(请求不带 temperature 时用这个;
#    UI 滑块可改,每轮覆盖)。注意:心跳自走/离线补写轮不带覆盖参数,
#    同样吃这个默认 —— 行为变更:此前 engine 未传参吃的是 openai_compat
#    代码默认 0.3,2026-09 起全局默认 0.9(拍板值)
LLM_DEFAULT_TEMPERATURE = 0.9

# ✅ 2026-09 拍板:单次输出上限 = 4096。固定服务端(用户不该改 —— 调太矮
#    会截断长回复/工具 JSON,调太高超模型上限直接 400);每个 step 各自受它限制
LLM_OUTPUT_MAX_TOKENS = 4096


# ============================================================
# 参数全景打印(py server/params.py —— 像 dsh --dump-config)
# ============================================================

_ROWS: list[tuple[str, str, str]] = [
    ("SLEEP_START_H / SLEEP_END_H", f"{SLEEP_START_H}h / {SLEEP_END_H}h", "✅ §10 睡眠窗"),
    ("GRID_SEC", f"{GRID_SEC:.0f}s", "✅ §10 判定粒度(分钟级)"),
    ("MIN_EVENT_SEC", f"{MIN_EVENT_SEC / 60:.0f}min", "✅ §10 最短事件"),
    ("DEFAULT_K", str(DEFAULT_K), "✅ 2026-09 总期望件数"),
    ("DEFAULT_DURATION_MIX", "15%10-25m / 55%30-90m / 30%100-200m", "✅ 2026-09 就这样用"),
    ("SHAPE_TABLE", "晚间峰那版(见下表)", "⏳ 形状待最终拍"),
    ("WAKE_AFTER_GAP_SECONDS", f"{WAKE_AFTER_GAP_SECONDS / 60:.0f}min", "✅ S1 补写阈值"),
    ("SELF_WAKES_PER_DAY", str(SELF_WAKES_PER_DAY), "✅ 2026-09 每天期望自发醒次数"),
    ("HEARTBEAT_COOLDOWN_SEC", f"{HEARTBEAT_COOLDOWN_SEC:.0f}s", "✅ 2026-09 自走冷却"),
    ("HEARTBEAT_INTERVAL_SEC", f"{HEARTBEAT_INTERVAL_SEC:.0f}s", "✅ 2026-09 心跳判定间隔"),
    ("HEARTBEAT_STARTUP_DELAY / MIN / MAX", f"{HEARTBEAT_STARTUP_DELAY:.0f} / "
     f"{HEARTBEAT_MIN_INTERVAL:.0f} / {HEARTBEAT_MAX_INTERVAL:.0f}s", "⏳ 调度约束(沿用)"),
    ("BACKFILL_START_DELAY_SEC", f"{BACKFILL_START_DELAY_SEC:.0f}s", "⏳ 补写启动延迟(沿用)"),
    ("LLM_DEFAULT_TEMPERATURE", str(LLM_DEFAULT_TEMPERATURE), "✅ 2026-09 默认温度(UI 可覆盖)"),
    ("LLM_OUTPUT_MAX_TOKENS", str(LLM_OUTPUT_MAX_TOKENS), "✅ 2026-09 输出上限(固定,不暴露 UI)"),
    ("HOT_*(YONA_GATE_HOT=1)", f"冷却{HOT_COOLDOWN_SEC:.0f}s / 间隔{HOT_INTERVAL_SEC:.0f}s / "
     f"期望{HOT_WAKES_PER_DAY:.0f}次每天", "🔧 演示模式"),
]


def dump() -> None:
    print("Yona 参数全景 · 唯一事实来源 server/params.py(py server/params.py 随时可看)")
    print("=" * 88)
    print(f"{'参数':<44} {'值':<30} 状态")
    print("-" * 88)
    for name, value, status in _ROWS:
        print(f"{name:<44} {value:<30} {status}")
    print("-" * 88)
    print("SHAPE_TABLE 整点值(未归一化;归一化/积分见 server/rhythm.py):")
    for h in range(0, 24, 6):
        seg = ", ".join(f"{i}:00={SHAPE_TABLE[i]:.2f}" for i in range(h, h + 6))
        print(f"  {seg}")
    print("=" * 88)
    print("规则:代码只从这里取参;拍板后把 ⏳ 换成 ✅ 并写日期。"
          "对比工具:test/k_compare.py(K)、test/gate_probe.py(gate)。")


if __name__ == "__main__":
    dump()
