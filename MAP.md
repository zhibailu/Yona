# Yona 重写 · 进度地图(MAP)

> 唯一进度来源。**每次动代码前先看,动完更新。**
> 相关文档:VISION(愿景/路线)/ DESIGN(设计决策)/ STRUCTURE(结构身躯)/
> LIFE_BACKFILL(离线生活补写模块)/ `server/params.py`(参数拍板状态,权威)。

---

## 一、目标架构(会诊迁居地图,2026-09-01 定,仍有效)

```
用户/前端 ──→ 应用服务层(server)──→ 内核(core)
                                        ├─ 事件日志(SessionLog)← 脊柱
                                        ├─ 干净循环(AgentLoop)
                                        ├─ 上下文装配(Composer)
                                        └─ 工具三件套(Tool/Registry)
冻结区(不进核心):sensory / objects / actions —— 旧 Yona 仅参考,不移植
```

分层规则(2026-09 拍板,STRUCTURE §1):core 不 import server/character;
character(含人格文案)依赖 core;server(应用壳)依赖 core + character。

---

## 二、拍板时间线(逐历史对齐;⏳ = 未拍,禁止默认)

### 阶段 0 · 会诊(09-01)
旧 Yona 诊断为"架构糊":server.py 单体 / 附属系统无干净位置 / 状态非事件源。
好资产(rag/eval/presets/composer/memory)与冻结区(sensory/autonomy/agents/life)
分类见 `D:\MyProject\Yona-架构会诊.md`。**结论沿用:旧价值 = 参考非移植。**

### P0-P4 · 内核与会话(09-02/03,旧条目 0-18)
事件日志 / 单循环 / 工具三件套 / 流式+chunk / SYSTEM 装配(builder)/ surface
(shadow+replace)/ 心跳(source=self)/ 会话存储 + thin router + 旧 UI 复用。
落点:`core/`、`server/store.py`、`character/`。9 个测试文件自此建立(2026-09 加 test_llm_setup/test_snapshot → 11)。

### S1 · 离线生活补写(09-04,大段拍板史)
- **醒来轮 v1 废弃**(旧条目 19):"开机补一句话"= 假体验。
- **收编重构**(旧条目 22):补写轮 = 主 `_loop.run_turn`,**无第二 AgentLoop**;
  差异 = log 有无时间游标;内核加 `SessionLog.time_cursor` + builder 三参。
- **§10 协议拍板**(用户逐轮拍):
  - 连续概率判定 rate(t)=K×shape(t);shape 归一化 ∫=1;期望 = ∫rate = K
  - 正交:K 只定件数、shape 只定形状(两个独立旋钮)
  - 结束条件 = 判定机会耗尽即止(不是撞 now)
  - 事件 = 起始时间 + 内容,无终止时间;budget 不进日志,只当轮可见
  - 一个时间段 = 一件事;相对时间 = start − (prev.start+prev.budget)
  - 内容 = 当下视角(随手记一笔/发状态);禁回溯/自报时间/情绪独白
- **K = 1.5 ✅(09-04 拍板)**;**时长分布 mix ✅(09-04 拍板"就这样用")**;
  **shape 曲线 ⏳(现用晚间峰那版候选)**。→ 权威:params.py
- 探针/扫描/不变量/曲线图全套建立:`test/backfill_*`、`k_compare`、
  `rate_curve*.py` → 资产 `assets/design/rate_curve.png`

### 结构阶段(09-05,用户定调:rewrite 是产品,旧代码不移植)
- **UI 处置**:旧 static 复用,只剪 404 死按钮(`_unused/`);快照逻辑留空;
  保留 session 切换 —— **每会话独立 log**(`data/sessions/{id}.log` +
  `{id}.meta.json`,已实现);人设文案只在代码一处。
- **thin router 布局 B(用户拍板)**:`server/main.py` 只留路由薄壳(24 端点,
  2026-09 实测数,文档曾误写 25 已改);业务分层:
  `server/app/{engine,gate}` + `server/app/api/{chat,view,media}` +
  `server/{store,rhythm}`(支撑库留包根)。
- **facade 移除 ✅(2026-09)**:main.py 不再再导出符号;测试/探针直达
  `server.app.engine`。
- **params.py 收敛 ✅(2026-09)**:拍板参数唯一来源;`py server/params.py`
  查看全景。**内含 ⏳ 未拍项(见 §四),禁止当已定使用。**
- **personas 归位 ✅(2026-09)**:人格文案在 `character/personas.py`
  (内容层);`character/persona.py` 是段工厂;engine 只装配。
- **gate 方案② ✅(2026-09 方向拍板)**:与补写同一原语(概率 = 每天期望 ×
  shape × Δt);**三个数值 ⏳ 待拍(cooldown / interval / wakes_per_day)**
- 人设笔误史:曾把 BACKFILL 写成"16 岁女孩"(与 21 岁冲突),已修回 ——
  教训:搬移必须逐字,见 §五 规则 5。

---

## 三、当前状态(2026-09-05 快照)

- **端点**:24 个(实测 `routes` 数),契约与旧 UI 对齐;聊天 SSE
  (token/tool_status/busy/done)、治理(shadow/replace)、观测
  (workspace/agent-feed/runtime)、图片/背景、会话 CRUD。
- **测试**:11 个测试文件全绿(`test/test_*.py`,Mock + 真模型双路可跑)。
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

---

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

---

## 五、规则(防跑偏,2026-09 更新)

1. 每个新功能先写测试,能跑才叫完成。
2. 新代码只进 `yona-rewrite`,不碰旧 `Yona`。
3. 每完成一块,更新本 MAP 与 STRUCTURE,并把改动说清楚。
4. 冻结区(sensory/objects/actions)不进内核,除非用户明确要。
5. **参数与形态不替我拍(血的教训,2026-09 加)**:执行只落"用户拍过"的值;
   未拍的一律 ⏳ 停在 params.py / 文档,不许我给默认、不许我把"沿用值"
   写成"已定"。拍板后才写 ✅ 并记日期。
6. **搬移必须逐字**:换文件/换形态时不得手打重写(曾把 persona 打成
   "16 岁女孩");用脚本移动 + 原文核对。
