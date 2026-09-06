# 开放项 / 待拍(tasks)

> 来自旧 `MAP.md` 四节。⏳ 未拍 = 不许默认、不许把沿用值当已定。参数级 ⏳ 权威在 server/params.py。

## 四、开放项 / 待拍(⏳ 未拍 = 不许默认)

| 项 | 状态 | 落点 |
|---|---|---|
| gate 三值:cooldown=90s / interval=60s / wakes=3次每天 | ✅ 2026-09 拍板 | params.py |
| shape 曲线最终形状 | ⏳ 现用"晚间峰那版"候选 | params.py |
| 心跳调度(startup/min/max)、补写启动延迟 | ⏳ 沿用值 | params.py |
| personas / params 内容形态(是否转 yaml/json) | ⏳ 用户质疑 py 形态,待拍 | character/personas.py |
| UI"角色设定"假入口(system_prompt 字段后端不消费) | ✅ 真接(2026-09):留空=旗舰,builder 覆盖 | chat.py + loop.run_turn |
| 温度/轮数/token → run_turn 可选字段 | ✅ 2026-09 落:温度 0.9(可覆盖)/输出上限 4096(固定不暴露)/轮窗口默认 20 | loop/params/chat |
| usage/finish_reason 上游一次捕获 | ✅ 2026-09 落:openai_compat 归一化 + assistant/message 锚点 | openai_compat.py |
| UI"Token 预算"滑块 | ✅ 2026-09 撤:无效滑块 → 只读上下文说明;摘要开关置灰占位 | index.html |
| 预设 = 快照整合(会话级 meta.json 覆盖) | ⏳ 架构待磋商(2026-09 起方向:三层覆盖 默认←快照←当轮) | 待定 |
| .env → 运行时连接(UI 向导) | ✅ 2026-09:产品配置 = data/llm.local.json(热重配免重启);.env 仅脚本/探针 | llm_setup.py |
| 同端点切模型(下拉) | ✅ 2026-09:run_turn model 可选字段,可用列表内校验 | core/loop.py + chat.py |
| UI 快照逻辑 | ⏳ 留空(用户定) | static/ |
| git init + 干净基线 | ⏳ 发布前做 | — |
| demo(test/legacy)内容 | ⏳ 用户声明不重要,不管 | — |
