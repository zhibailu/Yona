# 当前状态 / 进度(tasks)

> 来自旧 `MAP.md` 三节。完成 = 收口 + 测试绿;服务端契约/UI 现状见 docs/STRUCTURE.md。
> **最近核对 2026-09-21 22:50** —— 本节数字(端点数 / 测试数)会随代码漂,核对过就改这一行。

## 当前状态

> ⚠️ **下面的"任务 N"是**分阶段落地史**,按时间从上往下追加;最新的在文件末尾
> (「任务 8」起)。**别把中间某一段当成现状快照** —— 它只是那段时间的切片。

### 基线快照(2026-09-05,数字已于 2026-09-21 22:50 复核修正)

- **端点**:**32 个**(2026-09-21 实测装饰器数:`main.py` 13 + `api/config.py` 7 +
  `api/media.py` 5 + `api/view.py` 6 + `api/chat.py` 1)。
  ~~原写 24~~ —— 那是 09-05 的数,之后加了 LLM 配置向导与记忆相关端点。
  契约与旧 UI 对齐;聊天 SSE(token/tool_status/busy/done)、治理(shadow/replace)、
  观测(workspace/life-events/runtime)、图片/背景、会话 CRUD。
  > ⛔ 【2026-09-22 10:45 更正】把 `workspace / life-events` 与 runtime 并列成"观测面",
  > 读起来像它们是 rewrite 的产品交付面 —— 这层分量**是擅自加的**。**用户 2026-09-22 10:45
  > 判定内心活动(life-events)与桌面(workspace)两个面板不留**,现状**标注待砍、不摘**
  > (本次判定只点名这两个面板)。详见 `server/app/api/view.py` 的模块头。
- **测试**:**23 个** `test/test_*.py`(2026-09-21 实测),Mock + 真模型双路可跑。
  ~~原写 12~~;本文件下方两处 ~~"11 个"~~ 是各自当时的数,已就地标注。
- **探针/工具**(老的一批):`backfill_probe`(dist/inv/table/real)、`backfill_scan`、
  `k_compare`(K 对比)、`gate_probe`(心跳闸门蒙特卡洛)、`rate_curve.py` +
  `rate_curve_plot.py`(→ `assets/design/rate_curve.png`)、`route_table.py`
  (路由教学/排障)。
- **探针/工具**(记忆检索那一批,2026-09-19 起):`recall_probe.py`(路由 + 阈值标定)、
  `recall_router_probe.py`(真模型填参数)、`recall_stage1_lab.py`(阶段一实验台)、
  `recall_e2e.py`(端到端 + 判据自查)、`recall_bench.py`(排序策略 bench)、
  `prompt_lab/tool_recall.py`(提示词文案实验台,交互式)。
- **模块地图**:见 STRUCTURE §1;核心模块 LIFE_BACKFILL.md;参数 params.py。

### 设置面板真接线 + usage 上游捕获(2026-09,第 1 轮 UI 收紧)

- **内核**:`run_turn` 新增可选字段(温度/输出上限/轮窗口/人格覆盖串),
  不给就默认;`derive_messages` 新增 `last_turns` 轮边界收口(整轮裁,
  不切散工具配对);`OpenAICompatibleLLM` invoke/stream 支持每轮覆盖 +
  `stream_options.include_usage`,usage 归一化成互斥桶随流带出。
- **记录**:usage/finish 锚到 assistant/message data(同 dsh 语义);
  assistant/chunk 原始层留 usage(可回放)。
- **服务**:params.py 落 温度 0.9 / 输出上限 4096(✅ 拍板);chat.py 把
  temperature/max_rounds/system_prompt 传进 run_turn(max_tokens/summarize
  字段保留占位);/settings 回真实默认(显示即真相)。
- **UI**:温度滑块/角色设定/轮数滑块真生效;Token 预算滑块撤为只读说明;
  摘要开关置灰占位(compact 后解锁)。
- **验证**:11 测试文件全绿(**那是 2026-09 当时的数,现为 23**) + 真模型 SSE 冒烟
  (kinds = text/finish/usage,usage 数值正确)。
- **协议决策(2026-09,记录不实现)**:单 chat/completions 适配,adapter 缝在
  `core/llm.py`;responses/Anthropic 等 = 将来独立任务(见 DESIGN §11)。
- **连接管理(2026-09 任务③)**:`.env` 不再是产品配置 —— 唯一入口 = UI
  首启向导(`/admin/llm-config` 测通→落盘 `data/llm.local.json`→引擎热重配
  免重启);模型下拉 = 当前端点可用列表,下拉切换 = 同端点换 model id
  (`run_turn` 可选字段,列表内校验);未连接 = 引擎禁用待引导。11 个测试
  文件全绿(**当时的数,现为 23**) + 进程内真模型冒烟(建引擎→切 pro→usage 锚点)。
- **任务 4/5(2026-09,纯 UI 收口)**:感官三按钮(发图/语音/朗读)+ 视觉
  粘贴/拖图通道 + 空壳 action 菜单 + 舞台"桌面物件"pane 全部从 UI 撤除并
  断掉调用链(sendMessage 不再造附件/不走语音钩子、done 分支不再等物件);
  死代码留在 `app-objects-sensory.js` 冻结区(文件头已标注),感官接回时复用。
- **任务 6 · 会话快照(2026-09,用户拍板"塞进档案袋")**:三层合并链
  当轮 > 会话快照 > 默认(`engine.merge_turn_settings` 纯函数,路线 B 服务端
  补齐);四件套 {人格覆盖/温度/轮数/model};快照存 `sessions/<sid>/meta.json`
  的 settings 键(`PATCH /sessions/{sid}/settings`,{} = 清空);
  > 【2026-09-21 22:50 更正】这里原写「`{sid}.meta.json`」—— 那是**任务 7 之前的
  > 平铺布局**;任务 7 已改成**会话=目录制**(见本节末尾),`server/store.py` 的
  > `_meta_path()` 现在是 `sessions/<sid>/meta.json`,平铺写法只在
  > `_migrate_legacy_layout()` 的**一次性
  > 迁移代码**里出现。UI 自动+防抖
  保存、切会话回填、恢复默认 = 清快照;预设 = 命名快照(应用 = 复制进快照)。
  DESIGN §12 权威,测试 test_snapshot.py。
- **任务 7 · 每卡一套 life + Yona 常驻旗舰(2026-09 用户拍板)**:不再有匿名
  全局 `_life`(旧文件拍板丢弃)—— 会话即角色卡,每张卡自己的 chat.log 里
  存 source=self 生活流;自走/脉冲写给"最近激活的卡"(Yona 兜底,
  `store.life_target_session_id`),**补写在 2026-09-17 改为遍历所有有历史的卡**
  (见 DESIGN §12b 尾注),目标卡快照人格在自走轮同样生效;内心面板
  跟随当前卡;Yona = 常驻保底(flagship 标记、列表第一、删了先归档到
  archive/ 再重建空);所有卡删除都归档;存储改**会话=目录制**
  (sessions/<sid>/{chat.log,meta.json,images/},图片跟卡走,URL 不变,
  store 启动一次性迁移旧平铺)。DESIGN §12b 权威。
  > ⛔ 【2026-09-22 10:45 更正】本任务里的"**内心面板跟随当前卡**"改的是**目标卡选择**
  > (自走/补写打哪张卡),**不等于用户承认这块面板** —— 把它写成 rewrite 的展示重点
  > 是写文档时擅自加的。**用户 2026-09-22 10:45 判定内心活动(life-events)与桌面
  > (workspace)两个面板不留**,现状**标注待砍、不摘**。详见 `server/app/api/view.py` 的模块头。

### 任务 8 · 记忆检索接进主链路 + 往事不常驻(2026-09-21,用户拍板)

**权威记录在 `docs/decisions/TIMELINE.md` 末尾两节**,这里只登记"产品侧落了什么":

- **`recall` 工具从 `test/` 探针毕业进产品**(`character/tools.py` 的
  `make_recall_tool`),由 `server/app/engine.py` 装配注册;底座 =
  `core/memory.py`(MemoryIndex,dense 余弦 + BM25 融合)+ `core/memory_cache.py`
  (每卡一个 sqlite 缓存)+ `core/embed.py`(BGE,**可选依赖**,装不上就降级成纯
  关键词,四态走 `degraded`)。
- **往事不常驻**:生活事件**整条退出模型上下文** —— 既不再折成 SYSTEM 段,
  也不再以 `assistant` 身份投影。只通过 `recall` 按需召回。
  注入侧拆干净(`core/session_log.py` 已无 `life_event_prefix` 参数);
  **读侧清洗保留**(`strip_copied_prefix` / `view.py` / `rows_from_events`)——
  日志里那 2 条脏字是唯一证据,日志一字不动。
- **窗口 = 对话窗口**:`sliding_window`(默认 20,`server/params.py` 的 `DEFAULT_CONTEXT_ROUNDS`)**只数真人对白轮**,
  独处轮不占名额(`core/session_log.py` 的 `allowed_turns`)。实测该卡
  (19 自走 + 4 对话):20/10/5 三档窗口下 4 段真人对白**都完整可见**(改前是 3/2/2)。
- **索引住顶层 `cache/`**(从 `chat.log` 派生的可重建产物,不是数据;已进
  `.gitignore`)。暖机排在启动时(不懒加载),填账走**空闲吃性能剩饭**的后台 worker。
- **新增测试**:`test_embed.py`、`test_memory_cache.py`、`test_recall_tool.py`、
  `test_recall_wiring.py`。
- **新增文档**:`docs/decisions/RECALL_MVP.md`(零件③往事段**已标不做**)、
  `docs/decisions/TRAPS.md`。

### 任务 9 · 文档真值维护(2026-09-21 23:00,用户下指令)

- 全量审计 `docs/` 与根文档,清掉矛盾 / 过时 / 歧义;新规则落进 skill
  `decision-doc-maintenance` 与 `AI-GUARDRAILS.md §五`。
- 规则:**每条记录带 `YYYY-MM-DD HH:MM`**;新拍板当场回旧节标作废;
  前后对不上立刻报用户、默认以更晚那条为准;已讨论清楚的方案自觉写进文档。
