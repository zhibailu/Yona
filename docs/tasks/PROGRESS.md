# 当前状态 / 进度(tasks)

> 来自旧 `MAP.md` 三节。完成 = 收口 + 测试绿;服务端契约/UI 现状见 docs/STRUCTURE.md。

## 三、当前状态(2026-09-05 快照)

- **端点**:24 个(实测 `routes` 数),契约与旧 UI 对齐;聊天 SSE
  (token/tool_status/busy/done)、治理(shadow/replace)、观测
  (workspace/agent-feed/runtime)、图片/背景、会话 CRUD。
- **测试**:12 个测试文件全绿(`test/test_*.py`,Mock + 真模型双路可跑)。
- **探针/工具**:`backfill_probe`(dist/inv/table/real)、`backfill_scan`、
  `k_compare`(K 对比)、`gate_probe`(心跳闸门蒙特卡洛)、`rate_curve.py` +
  `rate_curve_plot.py`(→ `assets/design/rate_curve.png`)、`route_table.py`
  (路由教学/排障)。
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
- **验证**:11 测试文件全绿 + 真模型 SSE 冒烟(kinds = text/finish/usage,
  usage 数值正确)。
- **协议决策(2026-09,记录不实现)**:单 chat/completions 适配,adapter 缝在
  `core/llm.py`;responses/Anthropic 等 = 将来独立任务(见 DESIGN §11)。
- **连接管理(2026-09 任务③)**:`.env` 不再是产品配置 —— 唯一入口 = UI
  首启向导(`/admin/llm-config` 测通→落盘 `data/llm.local.json`→引擎热重配
  免重启);模型下拉 = 当前端点可用列表,下拉切换 = 同端点换 model id
  (`run_turn` 可选字段,列表内校验);未连接 = 引擎禁用待引导。11 个测试
  文件全绿 + 进程内真模型冒烟(建引擎→切 pro→usage 锚点)。
- **任务 4/5(2026-09,纯 UI 收口)**:感官三按钮(发图/语音/朗读)+ 视觉
  粘贴/拖图通道 + 空壳 action 菜单 + 舞台"桌面物件"pane 全部从 UI 撤除并
  断掉调用链(sendMessage 不再造附件/不走语音钩子、done 分支不再等物件);
  死代码留在 `app-objects-sensory.js` 冻结区(文件头已标注),感官接回时复用。
- **任务 6 · 会话快照(2026-09,用户拍板"塞进档案袋")**:三层合并链
  当轮 > 会话快照 > 默认(`engine.merge_turn_settings` 纯函数,路线 B 服务端
  补齐);四件套 {人格覆盖/温度/轮数/model};快照存 `{sid}.meta.json` 的
  settings 键(`PATCH /sessions/{sid}/settings`,{} = 清空);UI 自动+防抖
  保存、切会话回填、恢复默认 = 清快照;预设 = 命名快照(应用 = 复制进快照)。
  DESIGN §12 权威,测试 test_snapshot.py。
- **任务 7 · 每卡一套 life + Yona 常驻旗舰(2026-09 用户拍板)**:不再有匿名
  全局 `_life`(旧文件拍板丢弃)—— 会话即角色卡,每张卡自己的 chat.log 里
  存 source=self 生活流;自走/脉冲/补写写给"最近激活的卡"(Yona 兜底,
  `store.life_target_session_id`),目标卡快照人格在自走轮同样生效;内心面板
  跟随当前卡;Yona = 常驻保底(flagship 标记、列表第一、删了先归档到
  archive/ 再重建空);所有卡删除都归档;存储改**会话=目录制**
  (sessions/<sid>/{chat.log,meta.json,images/},图片跟卡走,URL 不变,
  store 启动一次性迁移旧平铺)。DESIGN §12b 权威。
