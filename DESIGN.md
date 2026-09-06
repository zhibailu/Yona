# Yona 重写 · 设计决策与取舍(DESIGN)

> 权威设计记录:每个决策的"为什么 + 取舍 + 证据",防止跑偏。
> 地位:VISION(愿景/路线)上游之下、MAP(进度)的决策层。改动设计先改这里,再动代码。

---

## 0. 总纲:三件事是分开的,别混

| 维度 | 解决什么 | 机制 | 管在哪 |
|---|---|---|---|
| 本轮能调哪些工具 | 能力边界 | schema 数组 + SYSTEM 用法散文(同批) | `run_turn(tools=子集)` |
| 历史里的工具痕迹 | 防"误调旧工具" + 省 token | 折叠视图(fold_tool_traces) | AgentLoop 构造开关 |
| 模型撒谎/声称完成 | 防不住,只兜底 | 状态权威 + 日志可观测 | 不是上下文能解决的 |

**教训(踩过)**:曾把三者混为一谈,导致"折叠能防撒谎"的错误期待。
**规则**:维度 3 永不试图用 prompt/折叠去"防",只兜底。

---

## 1. 能力 ≈ 工具(不变,来自 VISION)

- 工具三件套:`core/tools.py` 的 `Tool(schema + func + usage)`,`usage` 散文进 SYSTEM 教"怎么用得好",schema 进 tools[] 教"调用格式"。
- **人设/散文里禁止写死能力清单**("你会看时间、换衣服等")。能力唯一来源 = 本轮 schema + usage 段。
- 证据:人设写死能力 → 子集变化时模型仍以为有该工具 → 撒谎/表演调用(真模型复现)。删掉后行为改善。

## 2. 事件源:日志是真相,喂模型的是视图

- `SessionLog` append-only,事件原样保留,可回放/审计。
- `derive_messages()` 是**投影**,不是日志本身;折叠只改投影,永不改日志。
- 两层表示(对齐 dsh):chunk 层(原始流真相)↔ message 层(安全投影)。
- **推论**:任何"删/藏"都发生在投影或 message 层;日志永远可重建完整真相。

## 3. 本轮白名单:不给 = 不存在

- `run_turn(user_input, tools=子集)`:本轮 schema、SYSTEM 用法散文、执行权三者**同批**(都指向 active registry)。
- **白名单外调用 → message 层静默剔除**(`core/loop.py` `_stream_and_assemble`):不执行、**不喂 "tool unavailable" 错误**——错误会把工具名泄露给模型,违背"不给就当不存在"。
- 原始调用仍在 `assistant/chunk` 事件(可观测性保留),只是不进投影、不触发执行。
- 取代早期方案(执行→unavailable→模型自纠):那会泄露工具名且多烧一轮。
- 证据:真模型在子集下会调历史见过的白名单外工具;静默剔除后无 tool/result、状态不动。

## 4. 折叠视图:跨轮工具痕迹,默认忠实、可选折叠

- `derive_messages(fold_tool_traces=True)`:
  - 已结束轮次(出现过 turn/end)的工具痕迹折叠:assistant 剔 tool-call 块、tool/result 跳过,只留沉淀成人话的文本。
  - 当前未结束轮**不折叠**(step 间要拿工具结果当原料)。
- `Tool.retain_result=True`:该工具痕迹跨轮保留(保真)。语义 = Anthropic Context Editing 的 `ExcludeTools`(名单式),我们是工具自述(布尔),更内聚。
- 折叠与"本轮白名单"是两个维度:白名单管"本轮能给什么",折叠管"历史痕迹给不给看"。子集 + 折叠开 = 模型彻底不知道旧工具。
- **当前默认 False(忠实,业界主流),意图:旗舰(mvp/小夜子)开启折叠**。内核保留双视图,默认不破坏主流语义。
- 参照:dsh compaction = 整段摘要替换 + 结果裁剪,不删配对(靠 tool-pairing 平衡切点);Anthropic = 服务端声明式自动管理。我们是客户端纯函数视图,更细、零成本、日志兜底。
- **代价(已知)**:折叠后模型只能依赖"说出口的话 + 状态段",翻不了工具原文;需要旧数据就现调(retain_result 的工具例外)。

## 5. 撒谎:不防,只兜底(核心态度)

- **判断**:话题闭合是模型本能,prompt 纪律("不要假装完成")压不住。证据:人设含动作纪律后真模型仍两次声称换好黑裤、零工具调用、状态未变(`test/lie_repro_test.py`)。
- **兜底三层**:
  1. 执行层只认白名单:嘴上天花乱坠,状态一分不动(机制,非散文)。
  2. 状态是唯一真相源:`state.project()` 每轮注入 SYSTEM;文本声称与状态冲突时,下轮状态段照实显示,自曝。
  3. 事件日志可观测:"声称完成"与"无 tool/call"并列,撒谎可见。
- **面试叙事**:模型文本不可信是常态,系统不与它较劲,只保证"假话无副作用、真话有记录、状态永远对"。

## 6. 产品规则:动作型工具永远全量给

- **动作型工具**(改状态的,如 change_outfit):永不子集化。不给工具却让用户提动作请求 = 逼模型撒谎。
- **子集机制只用于信息型工具**的成本/安全控制(如几十个搜索/文件工具按轮给 top-k)。
- 证据:真模型对照——全量时换衣真调真改(状态变);子集藏工具时换衣纯文本声称(状态不变)。给够按钮,它才真做。

## 7. 已知待定项

- 折叠默认值:内核默认 False,旗舰默认 True——是否把内核默认也翻成 True,待 P5 旗舰定型后定。
- 坏 JSON 参数解析失败:当前静默回退 `{}`、`is_error=False`——是否标错,待定。

---

## 8. 收束口:外部需求如何进内核(防"UI 绑架内核")

**问题**:UI/插件需求来了,若直接给内核开口子(加特例、塞状态、改形状),内核长出一堆
特权形状,消费关系就反了。dsh 踩过:compaction 诞生前,"历史操作"靠顺序敏感的监听器
改写派生请求,每个新需求都要改一次 `deriveMessages()`(见 dsh notes
2026-06-18-session-surface Problem)。收束口 = 需求进内核前必经的一层过滤。

**操作序列**(需求来了 → 依序判断):

| # | 判断 | 动作 | dsh 实例 |
|---|---|---|---|
| 1 | 能收成内核原语? | 收,但带校验(只许按内核形状动) | surface replace(校验:只改 content、血缘覆盖、非法拒绝) |
| 2 | 收不成,但语义对? | **砍 UI 入口**,能力与按钮同生共死 | drop-user-message-edit-stub(无后端支撑的编辑按钮,删) |
| 3 | 语义方向本身错? | **整个撤回**,让位给未来的正确实现 | user-bubbles-drop-the-branch-action(分支语义反了,删) |
| 4 | 落错层? | 从内核拔掉,归还正确的 owner | unwrap-injected-content-envelopes(framing 归 caller) |
| 5 | 某状态不该可操作? | 让它**结构上无法表示**,不糊弄 | message-feedback(半截消息无 messageId,无法被评分) |

**两条元规则**:需求必须能重述成内核表达的操作,重述不了就砍 UI/拒绝,不许给内核开后门;
UI 只消费内核的安全视图(`surface_states()` 等),不反向塑造存储。

**fork vs compact 分层(重要,防混)**:
- **fork 类**(UI 删除/撤回/编辑历史消息)= **tail-cut**:从选中的消息切到日志末尾,
  之后全部遮蔽作废。dsh:fork = "a log-prefix cut at turn/end"。切点必须落在轮次边界,
  不许切出孤儿 tool 消息(assistant 的 tool-call 与其 result 必须同段,否则投影不合法)。
  **先做这个**(当前 UI 删除需求都是 fork 类)。
- **compact 类**(压缩旧上下文)= **中间挖**:把头部/中间已沉淀的旧段折叠成摘要,
  保留尾部最近对话继续。dsh:compaction 选范围后 LLM 出摘要,以一条 user/message
  (checkpoint)replace 遮蔽段。**还没遇到需求,不做**;内核 shadow 原语已覆盖一半,
  缺"摘要替换"那半,将来要做时看第 9 节。

**教训(踩过)**:曾把"遮蔽中间一段、保留后面"(compact 语义)当 UI 删除的用法——
那是错的:UI 删除 = 遮蔽到末尾(fork 语义),中间挖只属于压缩。两条语义在
`derive_messages` 里都是"跳过被遮蔽事件",区别只在**调用方选的范围**,内核不猜。

## 9. 未来:compact(压缩)怎么做(参照 dsh compaction-basic)

- 选范围(`selectCompactableRange`):token 计量表面节点 → 从尾部向前累计,
  保留"最近 retainTokens"不压缩 → 头部就是要压的段;切点用
  tool-pairing 平衡检查,永不切开 assistant tool-call/result 对。
- 摘要:把选中段的用户消息+工具往返+回答喂给 LLM → 一条"checkpoint"摘要。
- 替换:append 一条 `user/message`(摘要内容,带 surfaceOp replace + 被遮 seqs 血缘),
  遮蔽选中段;shadowed 事件留日志,投影只见摘要。compaction 自己的
  start/end 标记是 log-only,不进表面。
- 事务:compaction/start → 摘要(异步,可能失败/被取消)→ 校验 surface 没变 →
  compaction/end;start 未配 end 视为"压缩进行中"(锁),防并发。
- **已落内核(原语侧闭环)**:`SessionLog.replace(start, end, content)` = shadow +
  带 `replaces` 声明的摘要 user/message;投影按 anchor(=replaces.start)排序渲染,
  摘要顶在遮蔽段起点、后续对话顺序不乱 —— 压缩中间旧段也能工作,
  不限于头部。链路侧(选范围/token 计量/摘要组装/并发锁)留到真需求。
  derive_messages 从纯线性扫描改为 (anchor, order) 稳定排序,普通消息顺序不变。

## 10. 离线生活补写(用户拍板后的协议;server/rhythm.py 已按此重写,本节为权威)

**目标(用户定义)**:她离线(进程死)期间也在生活。重启后把这段值得记下的日子
补进 `_life`,像发朋友圈/日记——"一天难得有一两件值得记的事"。
**交付形态**:UI 内心活动看到的是连续的时间线(若干事件,各有起始时间),不是
"开机补一句话"(那是假体验,已废弃)。

**期望的定义(用户纠正两轮,最终形态)**:
- 错 1:λ(t) = 期望事件/小时(速率,隐含"长度×λ")。否决。
- 对:**连续概率判定**。一条可微概率曲线 rate(t)(件/单位时间),程序从
  last_active 到 now 按固定粒度逐格点伯努利判定:命中概率 = rate(t)·Δt。
  命中 → 生成事件;不中 → 下一格点。**期望事件数 = ∫ rate(t) dt**
  (曲线下面积,可微积分预计算:"7~9 点预期 0.8 件")。
- 粒度成本:分钟级(13h≈780 格点,<1ms)/秒级(≈47k,~5ms),均可忽略;
  默认分钟级 + 线性插值。

**正交(用户明确要求:事件期望与生成概率是两个独立旋钮)**:
```
rate(t) = K × shape(t)
  shape(t): 归一化曲线(∫shape=1)—— 只定"事件爱在几点出"(形状)
            (睡眠窗 23:30-06:30 为 0,晚间高,凌晨低)
  K:        总期望件数 —— 只定"这次补写一共期望几件"
期望件数 = K·∫shape = K   ← 与形状无关
落点分布 = shape(t)        ← 与件数无关
```
K 已拍板固定 = **1.5**(DEFAULT_K,不随长度)。实际期望 = K × ∫shape(窗口):
长度通过"窗口覆盖了多少 shape 面积"影响件数 —— 覆盖全天醒着(∫shape=1)
→ 期望 = K;只覆盖稀疏段 → 期望小;48h(两个白天)≈ 2K。长度本身不放大
件数,是"这段时间落在一天里的位置"在起作用(对比表:test/k_compare.py)。

**结束条件(用户上一条纠正)**:不是"时间撞到 now 才完",而是**判定序列走到
"不再有判定机会"为止** —— 例如只给过 8 点一次判定、没中,后面即使名义上
还有一小时预算,但没有判定点就不会再出事件,没有可消费的了,直接结束。

**事件消耗的时间**:命中后抽 budget(时长分布,如 20-180 分钟)。作用:
①给模型当限制("这段时间只做一件事、别做做不完的事")②跳过后续格点
③算相对时间(start − (prev.start+prev.budget))。**不进日志** —— 事件只有
起始时间 + 内容,无终止时间(用户定的)。一个时间段 = 一个事件,模型每段
只产一件事(prompt 强调)。

**收编(已实现,保留)**:补写轮 = 主 `_loop.run_turn`,无第二 AgentLoop;
差异 = log 有无时间游标(有 → 回放轮:补写 persona、世界时间=历史;
无 → 普通轮:墙钟)。内核扩展:SessionLog.time_cursor + system builder 三参
(registry, source, log)。

**内容视角(用户拍板)**:当下视角——"此刻约[当前时间],刚做完/正在做的这
一件事";像随手记一笔/发给在意的人的状态;不回溯别的时间段、不自报时间、
不写情绪独白/自怨自艾。每件事具体、有头有尾。

**p/shape 曲线**:绘图脚本 test/rate_curve_plot.py(重画成 shape 图)。
**旋钮现状(用户拍板)**:参数**唯一事实来源 = `server/params.py`**(2026-09
收敛,每项带拍板状态;查看全景: `py server/params.py`)。K 默认 **1.5**
(params.py DEFAULT_K,已定);时长分布(mix)沿用,用户拍板"就这样用"
(小部分,不追求完美);shape 形状待拨(现用"晚间峰那版"候选)。
对比工具:test/k_compare.py(K)、test/gate_probe.py(gate)。

**已废弃的错误方向(记录防重犯)**:
- 醒来轮/self_note 开机补一句话 —— 假体验,废。
- 一次 LLM 调用生成整段 JSON 再手工伪造轮次 —— 绕开 loop + 伪造,废。
- 第二 AgentLoop(补写专用 loop/composer)—— 她只有一个大脑,收编,废。
- λ(t) 速率 + ∫λ dt 把离线长度卷进期望 —— 用户否决,改连续概率判定。
- 预取一批时间段再填 / 空档后定点判定 —— 用户要的是逐格点连续概率判定,
  空档概念消失(稀疏时段自然大片不命中),无单独"空档参数"。

> **完整模块记录见 `LIFE_BACKFILL.md`**(算法/架构/内核扩展/参数现状/代码地图/
> 测试与验证数据/废弃方向/开放项)。本节是协议权威,两者保持一致。

## 11. LLM 入口:单协议适配(现在)+ adapter 缝(将来)(2026-09 用户定性"要紧的事")

**问题**:代码只讲 OpenAI 系 `chat/completions` 一种线上协议;配置里
"url + key + model" 换的是**兼容端点**,不是协议。OpenAI 已把新能力
(o 系/GPT-5 系/reasoning 参数形态)往 **responses** 接口放、chat/completions
标记 legacy → 将来想接"responses-only / Anthropic / Gemini 原生"的模型,
现在这套接不上,不是改 url 能解决的。

**定论(2026-09,记录不实现)**:入口要茁壮,但当前任务不做多协议 →
本决策 = 记录"现在是什么、缝在哪、将来怎么加",防误解与返工。
- 现状:**单协议适配**。`core/openai_compat.py` 是唯一实现(请求打到
  `{base_url}/chat/completions`);能用的是"恰好也讲 chat/completions"的
  端点(DeepSeek 官方 / vLLM / Ollama /v1 / Moonshot / 各类代理)。
  不是"多选一"(预接厂商清单),也不是"多协议"。
- 为什么够:VISION 单模型 .env 决策下,目标是兼容端点即可;DeepSeek
  目前就是纯 chat/completions 形态(含 usage/cache,已实测)。
- **架构缝(已具备)**:`core/llm.py` 的 `LLM` Protocol + `AssistantOutput`
  = adapter seam(对齐 dsh 的 `ctx.llm` + 每厂商 adapter)。加协议 =
  新增一个实现类(自管 wire→内部 chunk/usage 归一化),engine 按 profile
  装配。dsh 对照:它的 `dsh-llm-deepseek` / `dsh-llm-pi-ai` / anthropic sdk
  并存,各自 serialize/usage/容量/错误归一化到统一接口 —— 那才是
  "兼容多种协议"的完整形态,工程量与现在不是一个量级。
- 已对齐 dsh 的部分(2026-09):usage 互斥桶字段、finish_reason、
  per-call 覆盖参数(temperature/max_tokens)。
- **待办重构点(多协议化时,现在不做)**:把 TokenUsage 桶结构从
  `openai_compat._parse_usage` 的私有实现提升为 `core/llm.py` 共享类型,
  各 adapter 各自映射(同 dsh `TokenUsage` interface)。本次已按该字段
  命名,届时纯机械搬迁。
- **正交**:协议轴(怎么说话 = adapter)与产品模型切换轴(说哪个 = UI/配置)
  独立。② 模型区若拍"撤 UI、只读显示 env 模型",与"协议缝留好"不冲突。
- 推论:responses-only 模型接入 = 未来独立任务(新 adapter + profile),
  不是改配置;UI 模型发现假定端点兼容 chat/completions 的 /models,
  responses-only 端点不适用。

> STRUCTURE §2 取舍表与 MAP 开放项已挂本条;参数与模型装配见 params.py。
