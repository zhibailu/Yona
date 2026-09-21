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
#   形状自动来自 SHAPE_TABLE(深夜=0 不醒);wakes 对标补写的 K(活着时的生活事件
#   频率,与"值得记的事"是两码事);验证:test/gate_probe.py。
SELF_WAKES_PER_DAY = 3.0   # ✅ 2026-09 拍板:每天期望自发醒次数
HEARTBEAT_COOLDOWN_SEC = 90.0   # ✅ 2026-09 拍板:刚自走过,多久内不醒
HEARTBEAT_INTERVAL_SEC = 60.0   # ✅ 2026-09 拍板:心跳多久判定一次

# ⏳ 待拍(沿用 P3 调度默认):心跳线程节奏约束
HEARTBEAT_STARTUP_DELAY = 15.0   # 启动后多久开始问门
HEARTBEAT_MIN_INTERVAL = 45.0    # 间隔下限
HEARTBEAT_MAX_INTERVAL = 600.0   # 间隔上限

# (❌ 2026-09-17 删:BACKFILL_START_DELAY_SEC —— "补写线程等 5s 再跑,防抢用户
#  首条消息"。它与 LIFE_BACKFILL §9「补写占队首」直接冲突:补写的语义是
#  "你不在时她的生活",你的消息一进日志那段就结束了,所以补写必须排在用户
#  前面。那个"锁外先睡 5 秒"正是让用户插队、补写事件排到用户消息之后的元凶。)

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

# ✅ 2026-09 拍板:上下文窗口默认 = UI 旧默认 20 轮(保留最近 N 个已结束轮;
#    UI 滑块 0-40,0 = 不限制;会话快照可覆盖,请求缺省用它)
DEFAULT_CONTEXT_ROUNDS = 20


# ============================================================
# 四之二、记忆索引缓存(2026-09-21 用户拍板;实测数见 core/memory_cache.py)
# ============================================================
# 这三条**不是语义参数,是节拍参数** —— 它们只影响"后台什么时候补向量",
# 不影响任何检索结果。所以它们没有"拍板"的价值,只有"实测出来的合理值"。

# ✅ 用户 2026-09-21 定:索引住**顶层 cache/**(不塞进 data/)。
#    理由:它是从 chat.log 派生的**可重建**缓存,不是要备份的数据;
#    塞进 data/ 会让人误以为它得跟着卡片一起备份。已被 .gitignore 忽略。
MEMORY_CACHE_DIRNAME = "cache"

# 后台线程没活干时的轮询间隔(秒)。它同时是"让路"时的重试间隔。
MEMORY_POLL_SEC = 2.0

# ⏳ 待拍:**欠账等多久就插一脚**。实测背景:一条新记忆有 20 轮(≈60 秒)的余裕
#    等它的向量,而补一条只花 147 ms(CPU,走真实补向量路径)——
#    所以正常情况下后台永远来得及。
#    这条兜底是防**饿死**:连续对话时引擎锁一直不空,后台会一直让路,
#    而饿死的症状是"新聊的事她搜不到",她只会觉得她记性变差。
#    120 秒 = 20 轮的余裕里留了 2 倍安全系数;代价是一次最多给前台加 147 ms
#    (而且那一刻后台本来就在编码,前台等的是**已经在跑**的那一条)。
MEMORY_DEBT_MAX_WAIT_SEC = 120.0


# ============================================================
# 五、子代理(委派 / 工人)—— 2026-09-16 新层,值**全待拍**
# ============================================================

# ⏳ 待拍:子运行的**单次输出上限**。现在是**实验值,不是产品值** ——
#    上面那个 4096 是拍给聊天轮的,而推理模型的 reasoning_tokens 计入 output:
#    实测同一个任务在 4096 下 reasoning 吃满 100%、正文 0 token、结算成 failed,
#    8192 才跑得完。子运行是另一种成本结构的工作,**直接沿用聊天预算就是
#    "把沿用值当已定"**(OPEN.md 明令禁止)。测出来的数在
#    docs/protocols/SUBAGENT.md §4.3。
SUBAGENT_OUTPUT_MAX_TOKENS = 8192

# ⏳ 待拍:子运行的步数上限(工人不是无底洞)。
#    **2026-09-16 实测**:4 步不够一次真实网页调研 —— 工人的节奏是
#    "搜一次 → 打开两三个结果 → 再搜",四个 tool-call 全用在取材料上,
#    还没轮到写结论就撞上限,结算成 failed + **空 output**(实测连撞两次,
#    白烧 110s+81s 和约 10k token)。8 步是照这个节奏给的实验值,**仍待你拍**。
SUBAGENT_MAX_STEPS = 8

# ⏳ 待拍:工人能不能翻**本地文件**。空串 = **不接文件工具**(工人只有上网的手)。
#    这不是能拍脑袋给的默认值:根目录决定"她能读到用户的什么",是**隐私边界**,
#    不是技术参数。没拍之前只接 web_search / http_get,
#    list_files / read_text_file 留在 server/app/worker_tools.py 里不接线。
SUBAGENT_FILE_ROOT = ""


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
    ("LLM_DEFAULT_TEMPERATURE", str(LLM_DEFAULT_TEMPERATURE), "✅ 2026-09 默认温度(UI 可覆盖)"),
    ("LLM_OUTPUT_MAX_TOKENS", str(LLM_OUTPUT_MAX_TOKENS), "✅ 2026-09 输出上限(固定,不暴露 UI)"),
    ("HOT_*(YONA_GATE_HOT=1)", f"冷却{HOT_COOLDOWN_SEC:.0f}s / 间隔{HOT_INTERVAL_SEC:.0f}s / "
     f"期望{HOT_WAKES_PER_DAY:.0f}次每天", "🔧 演示模式"),
    ("SUBAGENT_OUTPUT_MAX_TOKENS", str(SUBAGENT_OUTPUT_MAX_TOKENS), "⏳ 子运行输出上限(实验值)"),
    ("SUBAGENT_MAX_STEPS", str(SUBAGENT_MAX_STEPS), "⏳ 子运行步数上限(实验值)"),
    ("SUBAGENT_FILE_ROOT", SUBAGENT_FILE_ROOT or "(未接)",
     "⏳ 工人能否读本地文件(隐私边界)"),
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
