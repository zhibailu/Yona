# 小夜子 · Self Loop 离线生活补写 —— 模块完整记录

> 这是 yona-rewrite 的核心模块之一(算法 + 架构已基本落定)。
> **协议权威**:`docs/decisions/DESIGN.md §10`(用户拍板版)。本文件是它的完整展开:
> 算法、数学性质、架构、内核扩展、内容视角、参数现状、代码地图、
> 测试与验证数据、废弃方向、剩余开放项。两者应保持一致:改协议先改
> DESIGN.md §10,再同步本文件。拍板条目见 `docs/decisions/TIMELINE.md`。

> **2026-09 修订(每卡 life + 情境合一,用户拍板)**:生活流不再落匿名全局 `_life`
> 文件 —— 旧 `_life.log` 已拍板丢弃;补写/自走/脉冲写给**"最近激活的那张卡"**
> (Yona 常驻旗舰兜底,`store.life_target_session_id`),写进该卡自己的
> chat.log(source=self);目标卡快照的人格覆盖串在自走轮同样生效(卡独处 =
> 卡本人,否则旗舰)。补写锚点 = 目标卡日志尾部。补写轮**复用 SELF_SITUATION**
> (曾 BACKFILL_SITUATION 已删)。完整规则见 `docs/decisions/DESIGN.md §12b`。

---

## 0. 一句话

她离线(进程死)期间**也在生活**。重启后把这段"值得记下的事"补进
**"最近激活的那张卡"的 chat.log(source=self)**(2026-09 每卡 life 拍板;不再有
匿名全局 `_life`),像朋友圈/日记 —— "一天难得有一两件值得记的事"。
交付形态:**UI 内心活动(agent-feed / workspace)看到一条连续的时间线**
(若干事件,各有起始时间),不是"开机补一句话"(那是假体验,已废弃)。

---

## 1. 核心算法:连续概率判定(§10,用户拍板)

### 1.1 期望的定义(纠正两轮后的最终形态)

否决过 λ(t) = 期望事件/小时(速率,隐含"长度×λ");最终定为:

> **连续概率判定**。一条可微概率曲线 rate(t),程序从 last_active 到 now
> 按固定粒度逐格点伯努利判定:命中概率 = rate(t)·Δt。
> 命中 → 生成事件;不中 → 下一格点。**期望事件数 = ∫ rate(t) dt**
> (曲线下面积,可微积分预计算:"7~9 点预期 0.8 件")。

- 粒度:默认**分钟级**(13h ≈ 780 格点,<1ms);秒级 ≈ 47k、~5ms 也可接受。
- 概率含义:rate(t) 是"该时刻生成事件的概率密度",不是件数速率。

### 1.2 正交:rate(t) = K × shape(t)(用户明确要求的两个独立旋钮)

```
rate(t) = K × shape(t)
  shape(t): 归一化曲线(∫shape = 1)—— 只定"事件爱在几点出"(形状)
            (睡眠窗 23:30-06:30 为 0:在睡,不判定)
  K:        总期望件数 —— 只整体缩放,不改变形状
期望件数 = K·∫shape(窗口)  ← 件数与形状无关(正交)
落点分布 = shape(t)        ← 形状与件数无关
```

- K 已拍板固定 = **1.5**(不随长度放大)。
- 长度如何影响件数:通过"窗口覆盖了多少 shape 面积"——
  覆盖全天醒着(∫shape=1) → 期望 = K;只覆盖稀疏段 → 期望小;
  48h(两个白天)≈ 2K。**不是长度本身卷进期望**,是位置在起作用。

### 1.3 事件与预算(用户拍的语义)

- 命中后抽 **budget**(时长分布,分钟)。作用三件:
  ① 给模型当上限("这段时间约 X 分钟,只做一件事、别做做不完的事");
  ② 跳过 budget 内的格点(这段时间她在做那件事,不再判定 —— 天然产生空档);
  ③ 算相对时间。
- **budget 不进日志** —— 事件只有**起始时间 + 内容,无终止时间**。
- **一个时间段 = 一件事**(模型每段只产一件,prompt 强调)。
- 被睡眠窗 / 区间终点截得不足 5 分钟的事件不硬凑(丢弃,继续判定)。

### 1.4 结束条件(用户上一条纠正)

不是"时间撞到 now 才完",而是**判定序列走到"不再有判定机会"为止**:
例如只给过 8 点一次判定、没中,后面即使名义上还有一小时预算,但
没有判定点就不会再出事件,没有可消费的了,直接结束。

### 1.5 相对时间(用户定义)

`next.start − (prev.start + prev.budget)` = 两件事之间**空档**(不是两个
start 相减)。gap > 60s → 当轮提示"距上一件事做完已过约 X";否则
"刚做完不久"。只当轮可见,不进日志。

---

## 2. 参数现状(旋钮表,2026 落定快照)

> **参数唯一事实来源 = `server/params.py`**(2026-09 收敛:所有语义旋钮集中
> 一处、每项带拍板状态;查看全景: `py server/params.py`)。下表"出处"是
> 拍板时的落点,现值以 params.py 为准。

| 旋钮 | 值 | 状态 | 出处 |
|---|---|---|---|
| K(总期望件数) | **1.5** | ✅ 用户拍板 | `server/params.py` `DEFAULT_K` |
| shape 曲线 | "晚间峰那版"表(下) | ⚠️ 候选,待拨 | `SHAPE_TABLE`(归一化 `ShapeCurve`) |
| budget 时长分布 | 短15%/10–25m,中55%/30–90m,长30%/100–200m | ✅ 用户拍板"就这样用" | `DEFAULT_DURATION_MIX` |
| 判定粒度 | 60s(分钟级) | ✅ | `GRID_SEC` |
| 睡眠窗 | 23:30–06:30(shape=0) | ✅ | `SLEEP_START_H` / `SLEEP_END_H` |
| 最短事件 | 5 分钟(不足不记) | ✅ | `MIN_EVENT_SEC` |
| 补写触发阈值 | 离线 ≥ 30 分钟 | ✅ | `server/main.py` `WAKE_AFTER_GAP_SECONDS` |

候选 shape 表(未归一化,即旧密度表"晚间峰那版"形状;
归一化常数 Z = ∫表(睡眠窗外) ≈ 1.660;峰值 ≈ 10.9%/小时 @19–20 点):

```
0-6h: 全 0(睡眠)    12: 0.11    18: 0.15    23: 0.05(23:30 起 clip)
7: 0.05  8: 0.06     13: 0.09    19: 0.18
9: 0.07 10: 0.07     14: 0.07    20: 0.18
11: 0.08            15: 0.06    21: 0.14
                    16: 0.08    22: 0.10
                    17: 0.12
```

---

## 3. 架构:收编(无第二 AgentLoop)

补写轮 = **普通自走轮**:`_loop.run_turn(source="self", log=<目标卡 log>,
tools=ToolRegistry([]), self_note=note)`。她只有一个大脑 —— 差异只在
**log 有没有时间游标**:

| | 普通轮(心跳/脉冲/自走) | 补写轮(回放) |
|---|---|---|
| log 时间游标 | 无 → 墙钟 | 有 → 世界时间 = 历史时刻 |
| 事件时间戳 | 墙钟 | 落历史时刻 |
| 情境 | SELF_SITUATION(独处轮) | **复用同一份 SELF_SITUATION**(2026-09 情境合一,曾另写 BACKFILL_SITUATION 已删) |
| 世界时间(world section) | 墙钟 | `_backfill_clock["ts"]`(每 step 现取) |
| tools | 全量(可换装) | 空(`ToolRegistry([])`:补写是"那段日子怎么过的") |

> 2026-09 补充(普通轮同款,与补写对齐):普通自走/心跳/脉冲用同一条
> LifeSampler 对 [日志尾→当前] 采样出预算,无事件 → 安静结束不调 LLM;
> 事件轮"当前时间 = 事件 start"的锚定**只留 lab**,不改产品输出。

### 3.1 内核扩展(已落,通用能力)

`core/session_log.py`:
- `set_time_cursor(ts)` / `clear_time_cursor()` / 只读 `time_cursor`
- `append(type, *, at=None, **data)`:时间 = `at` 显式 > 时间游标 > 墙钟(默认)

`core/loop.py`:
- system builder 支持第三参 `(registry, source, log)`,按 `_builder_arity`
  (0/1/2/3)喂参 —— 旧 builder 零改动向后兼容
- `run_turn(..., self_note=None)` 等原有接口不变

### 3.2 启动链路(server/main.py)

```
lifespan 启动
 └─ _maybe_backfill_life()            # 启动检测
     ├─ _last_active_anywhere()       # 全日志(会话+_life)最后一条事件的时间
     ├─ _wake_decision()              # 纯函数:首启不补 / gap<30min 正常重启 /
     │                                #   gap≥30min → 补写
     └─ 后台线程(等 5s,防抢用户首条消息)
         ├─ LifeSampler(last_active, now).sample() → events[](见 §4)
         ├─ 若无事件 → 跳过(离线太短/稀疏/没判定中,正常)
         └─ 逐事件(全在 _lock 内):
             ├─ 组 self_note(预算上限 + gap_note,见 §5)
             ├─ life_log.set_time_cursor(e.start); _backfill_clock["ts"] = e.start
             ├─ _loop.run_turn(source="self", log=life_log, tools=空, self_note=note)
             └─ finally: clear_time_cursor(); _backfill_clock["ts"] = 0.0
 收尾: _life_gate.mark_self()(进入心跳冷却,节奏衔接)
```

- `_last_active_anywhere`:锚点**不能只看 `_life`** —— 用户会话里跟主人的对话
  也是她活着的证据(她这个人跨会话一致)。取所有日志最后一条事件的时间。
- 事件之间日志历史累积 → 逐段叙事连贯自发涌现(不需要显式桥接)。
- `_backfill_clock` 只在回放轮内被设;补写 composer 的 world section 现取它
  (模型看到的时间 = 历史时刻,墙钟不混入)。

---

## 4. 采样器实现(server/rhythm.py)

```
public:
  SHAPE_TABLE / DEFAULT_K / DEFAULT_DURATION_MIX / GRID_SEC / MIN_EVENT_SEC
  density_at(hour, table=None)        线性插值(未归一化)
  ShapeCurve(table=None)              ∫=1、睡眠窗=0;构造时缓存归一化常数 z
  DEFAULT_SHAPE                       默认候选曲线
  shape_at(hour) / rate_at(hour,k)    K×shape(件/小时 = 命中概率密度)
  integral_shape(h0,h1)               ∫shape(h1 可跨天)
  Event(start, budget_min)            end = start+budget 只是统计用派生属性
  LifeSampler(t0, t1, *, rng/seed, K, shape/shape_table, duration_mix)

sample() 逐格点:
  t 从 t0 走到 t1,格长 GRID_SEC:
    睡眠窗内 shape=0 → 永不命中(自然跳过)
    否则 命中概率 = K·shape(h)·(GRID_SEC/3600)
    命中 → 抽 raw budget → end = min(t+budget, t1, 下一个睡眠窗起点)
          end−t ≥ 5min 才记 Event(start=t, budget_min=(end−t)/60)
          然后 t = end(跳过该段);否则 t += 格长
  判定机会耗尽(t ≥ t1)即结束
```

- 睡眠窗截断:23:00 命中、抽到长 budget,名义消费裁到 23:30 前
  (她"要做也会在睡前做完",不给模型"睡两小时"的怪上限)。
- 全部随机走注入 rng/seed → 单测、固定复现。
- 模型零时间权力:事件在几点出、预算多少全由采样器定;模型只拿 start。

---

## 5. 内容视角(用户拍板)与当轮提示

### 5.1 情境文案(2026-09 起:补写轮复用 SELF_SITUATION)

曾单列 `BACKFILL_PERSONA`(旧 BACKFILL_SITUATION,"当下视角、刚做完正在做、
随手记一笔、别回溯…")—— **2026-09 用户拍板删 BACKFILL_SITUATION**:补写轮 =
自走轮的离线回放,复用同一份 SELF_SITUATION("现在是你自己的生活时间,参考
时间线,生成符合生活现象的事件",见 character/personas.py)。想改"独处轮怎么
说"只改这一处。下为历史 BACKFILL_SITUATION 原文存档(内容已并入语境由
SELF_SITUATION + 动态 note 覆盖,不再单独生效):

```
"此刻大约是上方的 [当前时间],你一个人,刚做完或正在做一件具体的事。
把这件事说出来……像此刻随手记一笔、像发一条给在意的人看的状态。
就写当下这一件,有头有尾,平静、具体、有生活气,两三句话以内。"
禁令(历史):不回溯别的时段 / 不自报时间 / 不写情绪独白自怨自艾
(点名禁"眼泪掉进碗里")/ 不要任务汇报腔、不提"工具/系统/模型"。
```

### 5.2 self_note(每事件,只当轮可见)

```
此刻没有人在跟你说话,你一个人过着平常的一天。
这段时间(约 X)里你只做了**一件事**——就是现在刚做完/正在做的这一件。
时间由系统给你,别自己报时间;不要写别的时间段的事。
[gap_note]
gap_note(i>0): 距上一件事做完已过约 X(>60s) / 你上一件事刚做完不久。
```

> 注:self_note 是**动态数据**(预算上限 + 间隔),保留在引擎/lab 组 note 处;
> 静态"独处轮怎么说"已在 SELF_SITUATION,不在这里重复(2026-09 归位)。

### 5.3 观测到的输出特征(2026-09 真模型多样本)

- 视角正确:全部"刚做完/正在做这一件",无回溯、无自报时间、无情绪独白。
- 一段一件事:单一动作链(剪薄荷枝/搬薄荷进屋/晾衬衫/擦绿萝叶/叠衣收柜)。
- 时间衔接自然:短空档(如 15:13→16:23,空 ~23 分钟)模型能自然接续。
- ⚠️ 潜在越线点(待用户定):"想起是去年冬天骑车摔的""去年在夜市随手买的"
  这类**物件来历**一句回忆 —— 属"细节",不属"回溯别的时间段做了什么",
  当前允许;要不要禁由用户拍板。

---

## 6. 测试与验证工具(test/)

| 工具 | 用法 | 内容 |
|---|---|---|
| test_backfill.py | `py test\test_backfill.py` | 14 断言:触发判定×3、shape 归一化、事件有序/不重叠/区间内、budget 范围、均值≈K∫shape、K 正交、睡眠窗零事件、事件不越过 23:30、seed 可复现、时间依赖、time_cursor×2 |
| backfill_probe.py | `dist` / `inv` / `table` / `real <间隔> [seed列表]` | 分布(60 seed)、不变量扫描(500 seed×7 间隔)、明细表、**真模型多样本**(real 13h 自动挑 1/2/3 件 seed;每 seed 独立 SessionLog) |
| backfill_scan.py | `py test\backfill_scan.py [间隔] [seed数]` | 丰富度扫描:形状分组/代表样本/预算分布 |
| k_compare.py | `py test\k_compare.py [seeds]` | K 旋钮对比表(理论/实测均值/0件率/分布/均值÷K 验证正交) |
| rate_curve.py | `py test\rate_curve.py` | ASCII shape 曲线 + K×∫ 窗口期望 |
| rate_curve_plot.py | `py test\rate_curve_plot.py` | 三张子图 PNG(`test/rate_curve.png`):shape / K 正交 / 判定轨迹示例 |
| sse_edu.py / busy_probe.py | 各自 runner | SSE 教学 / 忙态探针(保留) |

间隔定义(probe/scan 共用):`2h` 12-14、`4h` 13-17、`6h` 9-15、
`13h` **11:35→次日 00:30(对齐用户真实 case)**、`24h` 6:00→次日 6:00、
`night` 22-03、`long` 48h。

### 6.1 验证数据快照(2026-09,§10 实现后)

- 数学不变量:7 间隔 × 500 seed **零违规**(区间/有序/不重叠/budget 界/睡眠窗)。
- 分布(probe dist,60 seed):2h 均值 0.2(空 49/60)、6h 0.5、13h 1.2、
  24h 1.5(=K)、night 0.1、long(48h)3.0(≈2K) —— 与"期望 = K×∫shape(窗口)"吻合。
- k_compare(K=1 vs K=1.5,200 seed/格):
  - 正交:同场景两 K 的 均值/K ≈ 常数(0.09/0.23/0.25/0.73/0.90/0.04)。
  - 13h 隔夜:K=1 → 均值 0.72、空 44%;K=1.5 → 均值 1.11、空 28%。
  - 24h:K=1 → 0.88、空 36%;K=1.5 → 1.36、空 20%。
  - 2h 两者都常空(91% vs 86%):"短间隔常空"是 shape 决定的,与 K 无关。
- 实测均值 ≈ 理论 K×∫shape(差 0.1-0.2,抽样误差内;分钟离散 + 5min 下限微偏)。
- 真模型样例(real 13h,seed1=1件 19:58 剪薄荷 / seed0=2件 12:15 搬薄荷、
  19:36 晾衬衫 / seed5=3件 15:13 擦绿萝、16:23 收牛仔裤、19:57 叠衣收柜)。

---

## 7. 已废弃的错误方向(记录防重犯)

- 醒来轮 / self_note 开机补一句话 —— 假体验,废。
- 一次 LLM 调用生成整段 JSON 再手工伪造轮次 —— 绕开 loop + 伪造,废。
- 第二 AgentLoop(补写专用 loop/composer)—— 她只有一个大脑,收编,废。
- λ(t) 速率 + ∫λ dt 把离线长度卷进期望 —— 否决,改连续概率判定。
- 预取一批时间段再填 / 空档后定点判定 —— 要的是逐格点连续概率判定,
  空档概念消失(稀疏时段自然大片不命中),无单独"空档参数"。
- 探针跨 seed 复用同一 SessionLog —— 上一 seed 的未来时间历史串进下一 seed,
  时间倒流乱码;每 seed 独立日志。

---

## 8. 剩余开放项(不阻塞,待用户拨/后续)

1. **shape 形状数值**:现用"晚间峰那版"候选;要改只动 `SHAPE_TABLE`,
   图/测试/表自动跟着变。
2. **物件来历回忆**:是否在 persona 禁掉(见 §5.3)。
3. **真实重启验证**:真实数据最后活跃 = 用户会话 2026-09-04 11:35;下个
   长离线重启,应看到 _life 补出连续时间线(用 `YONA_DATA_DIR` 独立目录验证)。
4. **目录 src 化**:模块横切 server/core/character,当前不拆;整体稳定后
   若要整理布局(引入 src/docs),本模块文档随迁。
5. 演示:`YONA_GATE_HOT=1`(心跳更勤);`YONA_DATA_DIR` 指向临时目录防脏。
