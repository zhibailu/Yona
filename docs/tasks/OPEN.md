# 开放项 / 待拍(tasks)

> 来自旧 `MAP.md` 四节。⏳ 未拍 = 不许默认、不许把沿用值当已定。参数级 ⏳ 权威在 server/params.py。
> **落盘 2026-09-17 20:37;最近核对 2026-09-22 00:20(代码全量审查后)。** 核对过就必须改这一行。
>
> ⚠️ **下面分两张表**。第一张是**语义/内容层**要你拍的;第二张是**代码审查挖出的接线与真值问题**
> (2026-09-22 那次彻查,全部有 `文件:行` 证据)。两张都**不许替用户默认**。

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
| **产品人设年龄** | `character/personas.py` 的 `PERSONA` 写「**18 岁**女大学生」;而仓库自己的教训记录(`README.md`、`docs/pitfalls/HISTORY.md`、`docs/decisions/TIMELINE.md`)说正典是 **21 岁**,且 **6 个 `test/` 探针仍写 21 岁** | **18 是有意改的还是笔误?** 笔误 → 改一行 + 同步 6 个探针;有意 → 在那段加标注说明探针是过时副本 |
| **`CHAT_SITUATION` 写死能力** | `personas.py` 的陪聊情境段里有一句「**你也有一些tool可用**」,它**不跟随 registry** —— 空工具轮(补写、实验台)也会这么说。违反 `character/persona.py` 的铁律"能力唯一来源 = 本轮 schema + 用法段" | 删掉那半句 / 改成不声明能力的说法 / 认为无害留着 |
| **`core/composer.py` 里三处模型可见文案** | `"[可用工具用法]"`、`_fmt()` 的「刚刚 / N 分钟前」、`"[时间线] 距上次和主人说话: {gap}"` 都**住在内核**;而且 producer 通道**不插值**,「主人」是把 `VALUES["owner"]` **抄死**了。同类的 `WAKE_BUDGET_TEMPLATE` 早已按判例归位到内容层,**这三处是漏网的** | 搬进内容层(要保证默认串逐字相同)/ 认定"段标签属于段实现的机械标记"留内核 |
| **工人工具的 `description`/`usage` 住哪** | 4 段模型可见文案住在 `server/app/worker_tools.py`;而她手上的工具文案住在 `character/tools.py`。文件头自己辩解"这是能力说明、不做人格化" | 写进 `character/README.md` 当**明确的边界例外** / 搬去内容层 |
| **删光消息的卡还算"激活"吗** | `server/store.py` 的 `_has_user_talk` 扫的是**原始事件**(不排 shadow)→ 你把某张卡的消息**全删了**,它仍会被选为自走/补写目标,她继续往一张"清空了的卡"里写生活 | 改(基于可见消息判)/ 不改但写清"原始事件才算证据" |
| **子运行要不要留轨迹** | `core/subrun.py` 的 `SubRunStore` 写得完整、有 round-trip 测试,模块头也承诺"轨迹落独立 run store";而产品装配处**不传 `store=`** → **派出去的工人干了什么,事后查不到**;`record.parent`(血缘)也恒 `None`。已加占位标注,未接线 | 接线(会新增落盘目录,隐私面变大)/ 明确不做、把 docstring 改成事实 |
| **主轮的步数上限进不进 params** | 主聊天轮 `max_steps=8` 硬编码在 `engine.py`,而 params 里的 `SUBAGENT_MAX_STEPS = 8` 是**子运行**的 —— 两个 8 撞数但无关,极易改错。内核兜底还是 20 | 主轮上限也进 params / 只靠注释区分 |
| **心跳抖动 `jitter` 进不进 params** | `core/heartbeat.py` 的 `jitter=0.2`(间隔 ±20%)是**写死在内核的产品行为**,params 里没有对应项;engine 也不传它。"防机械准点"是产品语义 | 提进 params(值不变,只是可见)/ 留内核 |
| **core 的默认值与 params 不一致(4 处)** | `openai_compat` 默认 `temperature=0.3/max_tokens=1024`,params 是 `0.9/4096`;`subrun` 默认 `max_steps=4`,params 是 `8`;`heartbeat` 默认 `5/10/3600s`,params 是 `15/45/600s`(**漏传就比拍板密 4.5 倍**)。今天 engine 显式传了所以不生效,但这是**同一语义的第二份真值** —— 而且 `test/` 里 16 处构造**没传**,那些探针跑的是旧值 | 改成 `None` 由装配处必填 / 只加警告注释 |
| **`_FLOOR` 相似度地板** | ✅ **已裁决(2026-09-21):不接线** —— 实测负提升(big 语料 315 条上,负例最高余弦 0.439 > 可答最低 0.206,重叠;地板会把真答案当"确实没有"挡掉) | —(已按"有正提升就做,没有就保持原样"办) |
