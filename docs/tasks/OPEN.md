# 开放项 / 待拍(tasks)

> 来自旧 `MAP.md` 四节。⏳ 未拍 = 不许默认、不许把沿用值当已定。参数级 ⏳ 权威在 server/params.py。
> **落盘 2026-09-17 20:37;最近核对 2026-09-23 00:01(工人 SYSTEM 装配:段清单 vs 静态串)。** 核对过就必须改这一行。
>
> ⚠️ **下面分三块**:最上面**临时任务栏**(用户点名"一会再测"的,测完删);然后两张表 ——
> 第一张是**语义/内容层**要你拍的;第二张是**代码审查挖出的接线与真值问题**
> (2026-09-22 那次彻查,全部有证据)。都不许替用户默认。

---

## ⏰ 临时任务栏(临时登记,**测完删这一节**,2026-09-22 10:20 开)

> 这里只放"用户已经知道、并且明确说'一会再测/先别动'的"。**不是长期待办,别在这积累东西。**

| # | 事 | 用户原话 / 现状 | 怎么测 |
|---|---|---|---|
| T1 | **`CHAT_SITUATION` 尾巴那半句「你也有一些tool可用」要不要删** | ✅ **已办(2026-09-22 11:20,用户"1删了")。** 实测先做(真模型 24 次):那半句**测不出作用**,不是承重结构;再由用户拍板删掉。`character/personas.py` 的 `CHAT_SITUATION` 现在是 `"用户网名叫 {owner} 和你是网友关系，平时会和你发消息聊天"`(那半句连同前导逗号一起删了)。**删的理由是结构性的**:它是一句不跟随 registry 的能力声明,违反 `character/persona.py` 的铁律"能力唯一来源 = 本轮 schema + [可用工具用法] 段" | 复测反向(加回去会不会更好):`test/subagent_prompt_lab.py` 的 `A2` 换回注释里那个带半句的串,然后 `py test/subagent_prompt_lab.py --variants SHIPPED --tasks t1,t2,t3,t4,s1,s3 --reps 4` |

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
| **内心面板 / 桌面工作区** | ✅ **已裁决并摘除(2026-09-22 11:20,用户「3 摘掉吧那就」)。** 前一轮判定"不留"的依据:用户原话「你要分清楚,我发现了,你之前说的动过的部分其实都是你自己写文档的时候顺手的,**并不是我真的操刀过这一盘**……所以项1 应该是不留且**文档有擅自成分,得改**」;`view.py` + 两个前端文件的首个 commit 都是 `2903b2a baseline`,我上一轮拿 `8138640` 的"内心跟随当前卡"当"用户操刀过面板"是**过度解读**(那条改的是**目标卡选择**)。**摘除清单**:后端 `GET /workspace` + `GET /admin/life-events` 两端点与四个私有投影 helper;前端两个 pane(`#inner-life` / `#object-drawer`,含脉冲按钮)+ `_refreshInnerLife()` + `_setFeedHeading()` + 各处 `_refreshObjects/_refreshWorkspace` 调用;`app-objects-sensory.js` → `static/_unused/`;`test/test_view_trails.py` 随函数删;配套文档 8 处状态改为"已摘除";端点总数 32 → 30。⚠️ **`app-presets.js` 的预设 CRUD 保留**(真产品功能) | —(已办) |
| **工人工具的 `description`/`usage` 住哪** | ✅ **已办(2026-09-22 10:45)。** 用户提醒"我记得是一种工具分层思想的" → 查到**两条已有成文规矩**,都指向内容层:`docs/README.md`「内容层文案 = personas.py + character/tools.py(**每个工具的 description/usage —— 也是内容层,别漏**)」+ `TRAPS.md` 二.2「内容层文案只有一处来源」。已把 **15 条常量**(4 description + 4 usage + 11 参数说明…按工具去重后 18 个 `WORKER_*`)搬进 `character/tools.py`,`worker_tools.py` 只 import。**对拍:改前/改后渲染的 description/usage/参数说明逐字相同** | —(已办) |
| **工人 SYSTEM 的段清单(第五条,2026-09-23 00:01 补)** | ✅ **已裁决 (甲A) 并落地。** 发现的缺口:工人的 SYSTEM 是一条**静态串**(`SubRunSpec.system: str`),而 `core/loop.py` 的 `_build_messages()` 里 `if system_prompt is not None:` 是**替换**不是叠加 → **composer 整条不跑** → 工人**五天没拿到 `[可用工具用法]`**。实测:工人真实收到的 `messages[0]` 只有 268 字,两条 usage 与段标题**一个都不在**。**根因不是漏看**:`SUBAGENT.md` 的设计(2026-09-16 12:43)比实现(2026-09-17 01:48)**早 13 小时**就说"加一个 `source` 值,装配路由";而 §4.2 那张"该看到什么"的表里**没有"工具用法"这一行**,所以照表核对时**这一格从未被核对**。**用户两条口径**:①「世界段……**噪音太大,不进 subagent**」(⏳→❌ 已拍);②「**可见工具肯定是要补进去的**」(✅)。**落点**:`SubRunSpec.system` 放宽成 `str \| Callable` + `character/persona.py` 新增 `build_worker_composer()` + `engine._run_worker` 传 builder。详见 `docs/decisions/TIMELINE.md`「2026-09-23 00:01」 | ⏳ **仍剩一件、但已分账**:`source` 正名(工人轮日志里记的是 `"user"`)归 `TOOL_VISIBILITY.md` §4 的判定键那条待拍,**本轮特意没绑进来**(理由:走分支路由一旦写错,失败形态是"工人穿上人设"且无症状) |
| **删光消息的卡还算"激活"吗** | ✅ **已裁决并落地(2026-09-22 11:20,用户"2 要")。** 口径 ——「如果是说当前会话 0 消息了的话,**应该是整个对应日志归档**,会被补写等过滤掉,当然**不继续补东西,就是死掉了**」。**两条一起才凑成"死掉"**:① `_has_user_talk()` 判据改成**可见消息**(排 `shadowed_seqs()`)→ 卡退出 `life_target()` 与 `life_backfill_order()`,不再往里写生活(回归测试 `test/test_server_store.py` 的 `test_deleted_all_messages_kills_the_card_as_life_target`,反演验证过会红);② **整段日志归档** —— 删除端点在"一条可见消息都不剩"时调 `store.archive_log()`,把 `chat.log` 移进 `archive/<ts>-<sid>/`,并 `memory_forget` 掉索引缓存。前端读到响应里的 `archived` 会提示"这段对话已归档"并重拉会话列表 | ⚠️ 一个**设计取舍请你知道**:`archive_log()` **只搬日志、不搬卡**(留 `meta.json`/`images`/`subruns`)。两个原因:① 你说的原话是「当前会话**0 消息**了」——这本身就假设**会话还在**;② UI 的"重新生成/重试"走的是**同一个**删除端点、删完立刻重发,整卡搬走会让重发落进一个**没有 meta.json 的目录** → 会话从侧边栏消失而聊天还在写(静默错位)。要"连卡一起归档"就说一声(那是 `delete_session()` 的行为,一行的事) |
| **子运行要不要留轨迹** | ✅ **已裁决并接线(2026-09-22 10:45)。** 用户:「自运行时单独日志,**要留日志**」「最先的诉求是**日志要隔离开**……不要同目录等级下有不同会话的主日志又有各自的 subagent」。落点 = `sessions/<sid>/subruns/<run_id>.jsonl`,装配在 `engine._subrun_store_for()` / `_sid_now()`,路径由 `store.subruns_dir()` 算;`parent` 同时接上。**连带解决"清理策略"**:跟卡同生共死,`delete_session()` 整袋归档。**实测**:两卡各住各的、归档带走 jsonl、sid 拿不到时不落盘 | ⚠️ **你提的更大那把刀本轮没动**(你说"可能动的刀子比较大"):一个会话的**配置**(预设/快照)要不要也收进同一目录 + **快照统一 yaml 管理**。现在仍是 `meta.json` + 全局预设。要做请单独说 |
| **主轮的步数上限进不进 params** | ✅ **已办(2026-09-22 10:45)**:`params.py` 新增 `MAIN_MAX_STEPS = 8`,engine 改用它。**值一个没变**,只是不再与 `SUBAGENT_MAX_STEPS`(子运行那个 8)裸撞 | —(已办) |
| **心跳抖动 `jitter` 进不进 params** | ✅ **已办(2026-09-22 10:45)**:`params.py` 新增 `HEARTBEAT_JITTER = 0.2`,engine 显式传它 —— **全仓第一次有调用点传这个值**。值一个没变,只是从此看得见、可拍(0 = 关掉抖动,心跳变精确准点) | —(已办) |
| **内核默认值 vs params(上一轮这行写错了,已更正)** | ⚠️ **2026-09-22 10:45 重新取证后更正**:产品装配处**3 组都显式传了** —— `engine.py` 建 `OpenAICompatibleLLM` 时传 `temperature=LLM_DEFAULT_TEMPERATURE (0.9)` / `max_tokens=LLM_OUTPUT_MAX_TOKENS (4096)`;建子运行时传 `max_steps=SUBAGENT_MAX_STEPS (8)`;建 `Heartbeat` 时传 `startup_delay=15` / `min_interval=45` / `max_interval=600`。**上一轮我写的"heartbeat 默认 5/10/3600 vs params 15/45/600、漏传就密 4.5 倍"是错的** —— params 的 `HEARTBEAT_MIN/MAX_INTERVAL` 是 45/600 且都传了,内核那三个数只是**兜底默认,产品路径不走**。**真正"谁都没传、静默生效"的只有一个:`core/heartbeat.py` 的 `jitter=0.2`** —— 全仓(含 `server/`、`test/`)零调用点传它,所以"间隔 ±20% 抖动"这条产品行为**只存在于内核默认值里** | **只剩 `jitter` 一条要定**:提进 params(值不变,只是变成可见/可拍的)/ 认定"内核自带的机械防抖,不算产品参数"。其余三组**不用动**(已显式传;要不要把内核默认改成 `None` 强制装配处必填,是纯洁癖,可做可不做) |
| **`_FLOOR` 相似度地板** | ✅ **已裁决(2026-09-21):不接线** —— 实测负提升(big 语料 315 条上,负例最高余弦 0.439 > 可答最低 0.206,重叠;地板会把真答案当"确实没有"挡掉) | —(已按"有正提升就做,没有就保持原样"办) |
