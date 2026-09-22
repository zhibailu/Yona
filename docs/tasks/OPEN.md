# 开放项 / 待拍(tasks)

> 来自旧 `MAP.md` 四节。⏳ 未拍 = 不许默认、不许把沿用值当已定。参数级 ⏳ 权威在 server/params.py。
> **落盘 2026-09-17 20:37;最近核对 2026-09-22 10:20(称呼归位 + 脉冲标注后)。** 核对过就必须改这一行。
>
> ⚠️ **下面分三块**:最上面**临时任务栏**(用户点名"一会再测"的,测完删);然后两张表 ——
> 第一张是**语义/内容层**要你拍的;第二张是**代码审查挖出的接线与真值问题**
> (2026-09-22 那次彻查,全部有证据)。都不许替用户默认。

---

## ⏰ 临时任务栏(临时登记,**测完删这一节**,2026-09-22 10:20 开)

> 这里只放"用户已经知道、并且明确说'一会再测/先别动'的"。**不是长期待办,别在这积累东西。**

| # | 事 | 用户原话 / 现状 | 怎么测 |
|---|---|---|---|
| T1 | **`CHAT_SITUATION` 尾巴那半句「你也有一些tool可用」要不要删** | 用户 2026-09-22:「那半句你写进临时任务栏,一会再测。」现状:`character/personas.py` 的 `CHAT_SITUATION` 里仍留着它;`{owner}` 的部分已按她要求改成「用户网名叫 {owner} 和你是网友关系」 | **它从来没被单独测过** —— `test/subagent_prompt_lab.py` 文件头把维度 A 写成"CHAT_SITUATION 要不要提'能派人'",但变体表里 **A0(含这半句)出现在 10 个变体 + `SHIPPED-上线版` 全部行**,唯一的 A1 也照样带着它 → 没有"有它/没它"的对照。测法:在 `test/subagent_prompt_lab.py` 加一条**只差这半句**的变体(A0 vs A0-去半句),其余全同,跑委派率 + 误伤闲聊对照(s3/s4)。**判据**:若两列无差 → 删(能力归 registry + `[可用工具用法]` 段,是 `character/persona.py` 的铁律);若有差 → 证明阶段一还没做完,得查是哪一类活 |

## 开放项 / 待拍(⏳ 未拍 = 不许默认)

| 项 | 状态 | 落点 |
|---|---|---|
| gate 三值:cooldown=90s / interval=60s / wakes=3次每天 | ✅ 2026-09 拍板 | params.py |
| shape 曲线最终形状 | ⏳ 现用"晚间峰那版"候选 | params.py |
| 心跳调度(startup/min/max) | ⏳ 沿用值(`params.py` 的 `HEARTBEAT_MIN_INTERVAL` / `HEARTBEAT_MAX_INTERVAL`,在 `_ROWS` 里标 ⏳ 调度约束) | params.py |
| personas / params 内容形态(是否转 yaml/json) | ⏳ 用户质疑 py 形态,待拍 | character/personas.py |
| UI"角色设定"假入口(system_prompt 字段后端不消费) | ✅ 真接(2026-09):留空=旗舰,builder 覆盖 | chat.py + loop.run_turn |
| 温度/轮数/token → run_turn 可选字段 | ✅ 2026-09 落:温度 0.9(可覆盖)/输出上限 4096(固定不暴露)/轮窗口默认 20(**只数真人对白轮**,独处轮不占名额,2026-09-21 拍板) | loop/params/chat |
| usage/finish_reason 上游一次捕获 | ✅ 2026-09 落:openai_compat 归一化 + assistant/message 锚点 | openai_compat.py |
| UI"Token 预算"滑块 | ✅ 2026-09 撤:无效滑块 → 只读上下文说明;摘要开关置灰占位 | index.html |
| 预设 = 快照整合(会话级 meta.json 覆盖) | ✅ **已落地**(原写 ⏳ 待磋商,2026-09-21 22:50 更正)—— 三层覆盖 `当轮 > 会话快照 > 默认`,`engine.merge_turn_settings`;预设 = 命名快照 | `engine.merge_turn_settings` + `server/main.py` 的 `update_session_settings`(`/sessions/{session_id}/settings`) |
| .env → 运行时连接(UI 向导) | ✅ 2026-09:产品配置 = data/llm.local.json(热重配免重启);.env 仅脚本/探针 | llm_setup.py |
| 同端点切模型(下拉) | ✅ 2026-09:run_turn model 可选字段,可用列表内校验 | core/loop.py + chat.py |
| UI 快照逻辑 | ✅ **已落地**(原写 ⏳ 留空,2026-09-21 22:50 更正)—— UI 自动 + 防抖保存、切会话回填、恢复默认 = 清快照 | static/app-core.js:199-222 |
| ~~git init + 干净基线~~ | ✅ **早已完成,此行过时** —— 仓库已是 git repo,`main` 有完整提交史 | — |
| demo(test/legacy)内容 | ⏳ 用户声明不重要,不管 | — |
| **「一条往事是什么」**(可索引的语义单位) | ⏳ **唯一还活着的语义级未决项**(2026-09-21 收敛) | TIMELINE 09-17 §三 第 4 条 |
| `SUBAGENT_OUTPUT_MAX_TOKENS = 8192` | ⏳ 待拍(实验值,非产品值;实测 4096 会被 reasoning 吃满) | `params.py` 的 `SUBAGENT_OUTPUT_MAX_TOKENS` |
| `SUBAGENT_MAX_STEPS = 8` | ⏳ 待拍(实测 4 步不够一次网页调研) | `params.py` 的 `SUBAGENT_MAX_STEPS` |
| `SUBAGENT_FILE_ROOT = ""` | ⏳ 待拍(**隐私边界**,不是技术参数;空串 = 不接文件工具) | `params.py` 的 `SUBAGENT_FILE_ROOT` |
| `MEMORY_DEBT_MAX_WAIT_SEC = 120.0` | ⏳ 待拍(防饿死兜底;实测 147 ms/条) | `params.py` 的 `MEMORY_DEBT_MAX_WAIT_SEC` |
| `launch_subagent` description 补"收益"那段 | ⚠️ 待核 —— 可能已被 `character/tools.py` 的 `make_launch_subagent_tool()` 里 `description=` 那几行半覆盖 | character/tools.py |
| `W_SPARSE` 融合权重 | ⚙ 实测选定 0.2(**非拍板**),⏳ 可调 | core/memory.py |
| 阶段一尚未覆盖:多工具同时合适时挑哪个 / 更复杂 query | ⏳ 未测 | test/recall_stage1_lab.py |

## 代码审查挖出的待拍(2026-09-22;全部有证据)

> 这几条**不是"你觉得怎么样"式的口味问题** —— 每一条都是"文档/注释说 A、代码是 B",
> 或者"同一件事有两个真值"。**代码侧的错误已经改了或标了;这里列的是要你定"哪个行为"的。**

| 项 | 现状(证据) | 要你定什么 |
|---|---|---|
| **产品人设年龄** | ✅ **已裁决(2026-09-22 10:20):18 岁是用户本人改的,不再议。** 仓库里的 21 全是历史记录或探针自带夹具(6 个探针各写死一份,离线不读产品文件)。已在 `character/personas.py` 的 `PERSONA` 上方加 ⛔ 标注:任何审计/代理不许改回 21、不许再上报 | —(已办) |
| **`CHAT_SITUATION` 写死能力** | ⏰ **已转临时任务栏 T1**(用户 2026-09-22:「那半句你写进临时任务栏,一会再测」)—— 不再在这里等拍 | 见本文件顶部 T1 |
| **`core/composer.py` 里剩下的两处模型可见文案** | ✅ **称呼那处已办(2026-09-22 10:20 用户拍板)**:`[时间线] …和主人说话…` 已归位到 `character/personas.py` 的 `TIMELINE_TEMPLATE`,称呼走 `{owner}`(`VALUES` 唯一来源),producer 内部显式 `interpolate`;engine 装配传该常量。实测:owner=主人 时输出**逐字节不变**,owner 改别的时**跟着变**。**剩下两处仍在内核**:`"[可用工具用法]"` 段标题、`_fmt()` 的「刚刚 / N 分钟前 / N 小时前 / N 天前」。它们**不是称呼、不来自 `VALUES`**,所以没有"改一处就够"的问题 | 搬进内容层(要保证默认串逐字相同)/ 认定"段标签与时间说法属于段实现的机械标记"留内核。**口味问题,不急** |
| **手动脉冲 `/autonomy/pulse`** | ⛔ **已裁决(2026-09-22 10:20):未启用 / 待砍,已标注。** 端点与 UI 按钮仍通,但源码两处(以及绑的那两处 UI)都加了 ⛔ 块说明:它是 **baseline 搬来的旧物**(`git log -S` 第一个 commit 就是 `2903b2a chore: baseline`),不是 rewrite 长出来的;旧 Yona 脉冲**没有准入门槛**,而现在自走轮已有闸门/时间预算/补写/冷却/队列优先级,它**一个都没接**。**不许再把它当"真 bug"报** | 用户:"后面可能会把它整个砍掉"。终局二选一:① 砍 = 删端点 + 删 UI 按钮;② 复活 = 先补齐自走轮门槛("大概率要开很多特权")。**别人不要自己动** |
| **内心面板 / 桌面工作区算不算 rewrite 的东西** | ⚠️ **用户存疑,已核:一半对一半不对。** `server/app/api/view.py` 与两个前端文件都是 **baseline(`2903b2a`)搬来的**,这点用户猜对了;但**她本人后来操刀过**:`8138640 feat: 每卡一套 life + Yona 常驻旗舰(任务7)` 里的"**内心跟随当前卡**"、`aceb7cb`(术语改名 + 端点改名 + 读侧洗脏)。而且**文档里有** —— `docs/STRUCTURE.md` §4 把 `app-presets.js` 写成本表"**life-events 内心活动面板(rewrite 核心展示)**"、`app-objects-sensory.js` 写成"workspace 桌面(动作轨迹/生活事件/脉冲)";`docs/decisions/DESIGN.md` 的"交付形态"直接就是"UI 内心活动看到连续时间线";`docs/protocols/LIFE_BACKFILL.md` 第 28 行同款 | **要你定两件事**:(a) 这两个面板**留不留**(留 = 它们是产品面,那 shadow 不投影就是真缺陷;(b) 上一轮修的"两者都跳 `shadowed_seqs()`"**合不合你的意**)不留 = 标注成冻结/待砍,和脉冲同款处理;(c) 文档把它们写成"rewrite 核心展示"这句**对不对** —— 不对就得改文档 |
| **工人工具的 `description`/`usage` 住哪** | 4 段模型可见文案住在 `server/app/worker_tools.py`;而她手上的工具文案住在 `character/tools.py`。文件头自己辩解"这是能力说明、不做人格化" | 写进 `character/README.md` 当**明确的边界例外** / 搬去内容层 |
| **删光消息的卡还算"激活"吗** | `server/store.py` 的 `_has_user_talk` 扫的是**原始事件**(不排 shadow)→ 你把某张卡的消息**全删了**,它仍会被选为自走/补写目标,她继续往一张"清空了的卡"里写生活 | 改(基于可见消息判)/ 不改但写清"原始事件才算证据" |
| **子运行要不要留轨迹** | `core/subrun.py` 的 `SubRunStore` 写得完整、有 round-trip 测试,模块头也承诺"轨迹落独立 run store";而产品装配处**不传 `store=`** → **派出去的工人干了什么,事后查不到**;`record.parent`(血缘)也恒 `None`。已加占位标注,未接线 | 接线(会新增落盘目录,隐私面变大)/ 明确不做、把 docstring 改成事实 |
| **主轮的步数上限进不进 params** | 主聊天轮 `max_steps=8` 硬编码在 `server/app/engine.py`;params 里的 `SUBAGENT_MAX_STEPS = 8` 是**子运行**的 —— 两个 8 撞数但无关,极易改错。内核兜底是 `core/loop.py` 的 `AgentLoop.__init__(max_steps: int = 20)`,**也没人传**。已加注释区分 | 主轮上限也进 params(如 `MAIN_MAX_STEPS = 8`)/ 只靠注释区分。**工程洁癖级,不急** |
| **心跳抖动 `jitter` 进不进 params** | ⚠️ **改判为真问题(2026-09-22 10:45 取证)**:`core/heartbeat.py` 的 `jitter=0.2`(间隔 ±20%)**全仓零调用点传它** —— `server/` 和 `test/` 都没有,所以这是**唯一一条"只活在内核默认值里的产品行为"**。"防机械准点"是产品语义(用户拍过的心跳节奏的一部分) | 提进 params(值不变,只是可见/可拍)/ 明确认定"内核自带的机械防抖,不是产品参数" |
| **内核默认值 vs params(上一轮这行写错了,已更正)** | ⚠️ **2026-09-22 10:45 重新取证后更正**:产品装配处**3 组都显式传了** —— `engine.py` 建 `OpenAICompatibleLLM` 时传 `temperature=LLM_DEFAULT_TEMPERATURE (0.9)` / `max_tokens=LLM_OUTPUT_MAX_TOKENS (4096)`;建子运行时传 `max_steps=SUBAGENT_MAX_STEPS (8)`;建 `Heartbeat` 时传 `startup_delay=15` / `min_interval=45` / `max_interval=600`。**上一轮我写的"heartbeat 默认 5/10/3600 vs params 15/45/600、漏传就密 4.5 倍"是错的** —— params 的 `HEARTBEAT_MIN/MAX_INTERVAL` 是 45/600 且都传了,内核那三个数只是**兜底默认,产品路径不走**。**真正"谁都没传、静默生效"的只有一个:`core/heartbeat.py` 的 `jitter=0.2`** —— 全仓(含 `server/`、`test/`)零调用点传它,所以"间隔 ±20% 抖动"这条产品行为**只存在于内核默认值里** | **只剩 `jitter` 一条要定**:提进 params(值不变,只是变成可见/可拍的)/ 认定"内核自带的机械防抖,不算产品参数"。其余三组**不用动**(已显式传;要不要把内核默认改成 `None` 强制装配处必填,是纯洁癖,可做可不做) |
| **`_FLOOR` 相似度地板** | ✅ **已裁决(2026-09-21):不接线** —— 实测负提升(big 语料 315 条上,负例最高余弦 0.439 > 可答最低 0.206,重叠;地板会把真答案当"确实没有"挡掉) | —(已按"有正提升就做,没有就保持原样"办) |
