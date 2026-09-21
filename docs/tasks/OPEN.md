# 开放项 / 待拍(tasks)

> 来自旧 `MAP.md` 四节。⏳ 未拍 = 不许默认、不许把沿用值当已定。参数级 ⏳ 权威在 server/params.py。
> **落盘 2026-09-17 20:37;最近核对 2026-09-21 22:35。** 核对过就必须改这一行。

## 开放项 / 待拍(⏳ 未拍 = 不许默认)

| 项 | 状态 | 落点 |
|---|---|---|
| gate 三值:cooldown=90s / interval=60s / wakes=3次每天 | ✅ 2026-09 拍板 | params.py |
| shape 曲线最终形状 | ⏳ 现用"晚间峰那版"候选 | params.py |
| 心跳调度(startup/min/max) | ⏳ 沿用值(params.py:165 标 ⏳ 调度约束) | params.py |
| personas / params 内容形态(是否转 yaml/json) | ⏳ 用户质疑 py 形态,待拍 | character/personas.py |
| UI"角色设定"假入口(system_prompt 字段后端不消费) | ✅ 真接(2026-09):留空=旗舰,builder 覆盖 | chat.py + loop.run_turn |
| 温度/轮数/token → run_turn 可选字段 | ✅ 2026-09 落:温度 0.9(可覆盖)/输出上限 4096(固定不暴露)/轮窗口默认 20(**只数真人对白轮**,独处轮不占名额,2026-09-21 拍板) | loop/params/chat |
| usage/finish_reason 上游一次捕获 | ✅ 2026-09 落:openai_compat 归一化 + assistant/message 锚点 | openai_compat.py |
| UI"Token 预算"滑块 | ✅ 2026-09 撤:无效滑块 → 只读上下文说明;摘要开关置灰占位 | index.html |
| 预设 = 快照整合(会话级 meta.json 覆盖) | ✅ **已落地**(原写 ⏳ 待磋商,2026-09-21 22:50 更正)—— 三层覆盖 `当轮 > 会话快照 > 默认`,`engine.merge_turn_settings`;预设 = 命名快照 | engine.py:1183 + main.py:193 |
| .env → 运行时连接(UI 向导) | ✅ 2026-09:产品配置 = data/llm.local.json(热重配免重启);.env 仅脚本/探针 | llm_setup.py |
| 同端点切模型(下拉) | ✅ 2026-09:run_turn model 可选字段,可用列表内校验 | core/loop.py + chat.py |
| UI 快照逻辑 | ✅ **已落地**(原写 ⏳ 留空,2026-09-21 22:50 更正)—— UI 自动 + 防抖保存、切会话回填、恢复默认 = 清快照 | static/app-core.js:199-222 |
| ~~git init + 干净基线~~ | ✅ **早已完成,此行过时** —— 仓库已是 git repo,`main` 有完整提交史 | — |
| demo(test/legacy)内容 | ⏳ 用户声明不重要,不管 | — |
| **「一条往事是什么」**(可索引的语义单位) | ⏳ **唯一还活着的语义级未决项**(2026-09-21 收敛) | TIMELINE 09-17 §三 第 4 条 |
| `SUBAGENT_OUTPUT_MAX_TOKENS = 8192` | ⏳ 待拍(实验值,非产品值;实测 4096 会被 reasoning 吃满) | params.py:133 |
| `SUBAGENT_MAX_STEPS = 8` | ⏳ 待拍(实测 4 步不够一次网页调研) | params.py:140 |
| `SUBAGENT_FILE_ROOT = ""` | ⏳ 待拍(**隐私边界**,不是技术参数;空串 = 不接文件工具) | params.py:146 |
| `MEMORY_DEBT_MAX_WAIT_SEC = 120.0` | ⏳ 待拍(防饿死兜底;实测 147 ms/条) | params.py:120 |
| `launch_subagent` description 补"收益"那段 | ⚠️ 待核 —— 可能已被 `tools.py:98` 半覆盖 | character/tools.py |
| `W_SPARSE` 融合权重 | ⚙ 实测选定 0.2(**非拍板**),⏳ 可调 | core/memory.py |
| 阶段一尚未覆盖:多工具同时合适时挑哪个 / 更复杂 query | ⏳ 未测 | test/recall_stage1_lab.py |
