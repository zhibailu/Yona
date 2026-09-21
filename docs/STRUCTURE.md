# Yona 2.0 · 结构身躯(STRUCTURE)—— 当前形态、取舍与演进

> 记录 rewrite 的**身躯结构**:目录布局、层次职责、功能取舍、UI 现状、演进方向。
> 地位:与 DESIGN(设计决策)/ MAP(进度)/ LIFE_BACKFILL(核心模块)平级;
> 旧 Yona 的诊断与资产清单见 `D:\MyProject\Yona-架构会诊.md`(参考,不移植)。

---

## 0. 立场(用户 2026-09 拍板)

- rewrite 已经是**可用的逻辑闭环 / 产品**,不再做"移植完整旧逻辑"。
- 旧 Yona 的价值 = **参考**(会诊结论 / 好资产思路 / 踩过的坑),**不是仿照**;
  新结构由我们按现在的认知自己塑造。
- 未实现的功能:优先**留文档注释**(说明"做什么、为什么不做、旧项目在哪可参考")
  或**从 UI 剔除入口**;不把旧的半成品胶水贴进来。

---

## 1. 当前布局(源码平铺在根,无 src/)

```
yona-rewrite/
├─ core/  引擎内核(纯逻辑,不 import server/character):loop.py(主循环)/ session_log.py(事件源+投影)/ composer.py(段装配)/ memory.py + memory_cache.py + embed.py(recall 的记忆底座)/ heartbeat.py / assembler.py / openai_compat.py / llm.py / tools.py / subrun.py(工人子运行)。人格装配在 character/。
├─ server/      产品服务层(FastAPI thin router;布局 B 见 §3)
│   main.py(309 行)      入口:app / lifespan / include_router(无兼容再导出 —— facade 2026-09 已移除)
│   params.py          产品参数唯一来源(拍板/待拍旋钮集中;py server/params.py 查看)
│   app/engine.py      组合根 + 生活运行时(全局单例、start/stop)
│   app/gate.py        心跳闸门(方案②:概率 = 每天期望 × shape × Δt)
│   app/api/           请求域路由:chat(SSE)/view(观测)/media(图片)/config(预设 CRUD + 模型发现)—— 同层收子目录
│   store.py rhythm.py 支撑库(测试直连,留包根,见 §3 说明)
│   app/llm_setup.py   连接配置读写 + 实测拉模型列表(2026-09 任务③,
│                      落盘 data/llm.local.json;engine 热重配免重启)
├─ character/   旗舰角色层(小夜子:persona/state/tools,依赖 core)
├─ static/      旧 Yona UI 复用(零构建;仅剪 404 死按钮,死文件在 _unused/,见 §4)
├─ assets/design/  设计资产 PNG(rate_curve.png 等;生成脚本 test/rate_curve_plot.py 直出此目录)
├─ test/        测试(23 个 test_*.py,2026-09-21 实测;数字以实际跑 test/test_*.py 为准)+ 探针 + 绘图脚本 + legacy/(早期演示,见 §6)
├─ data/        运行数据:sessions/<sid>/{chat.log,meta.json,images/} + archive/ + presets/ + llm.local.json(gitignore,不入库)
├─ cache/       记忆索引 sqlite(从 chat.log 派生的可重建缓存,gitignore;可用 YONA_CACHE_DIR 指走)
├─ config.py    .env 加载(单模型 LLM_BASE_URL/API_KEY/MODEL)
├─ docs/README.md(文档地图)· docs/STRUCTURE.md · docs/decisions/{VISION,DESIGN,TIMELINE,RECALL_MVP,TRAPS}.md · docs/protocols/{LIFE_BACKFILL,SUBAGENT,TOOL_VISIBILITY}.md · docs/tasks/ · docs/pitfalls/ · docs/public/
├─ turn_lab.py   跑一整轮的实验台(陪聊/自走/补写回放 + 拨时钟 + 换模型)
├─ prompt_lab/   提示词文案实验台(按被试对象分:tool_ / persona_ / loop_)
```

> 【2026-09-21 23:20 更正】① `core/` 树原列 8 个行数里 7 个已过期,且现存 4 个模块完全没在树里 —— 真相(2026-09-21 实测行数):`core/loop.py`=461、`core/session_log.py`=445、`core/composer.py`=182、`core/heartbeat.py`=155、`core/openai_compat.py`=265、`core/assembler.py`=74、`core/tools.py`=90、`core/llm.py`=56;目录里另有 `core/embed.py`(175)、`core/memory.py`(300)、`core/memory_cache.py`(389)、`core/subrun.py`(335)。行数会漂,以代码为准。
> 【2026-09-21 23:20 更正】② 原写 `main.py(~235)` 且"兼容再导出",与本文件 §3「已移除 facade(2026-09)」自相矛盾 —— 真相:`server/main.py` 实测 **309 行**;全文只有 `from .app import engine` / `from .app.engine import ROOT` / `from .params import ...`(main.py:32-35),没有任何符号再导出;正典 `docs/decisions/TIMELINE.md:63-64`「facade 移除 ✅(2026-09):main.py 不再再导出符号」。
> 【2026-09-21 23:20 更正】③ 原写 `test/`「12 文件」,与本文件 §3「9 测试」、§5「12 个测试文件」三个口径互相打架 —— 真相:`test/test_*.py` 实测 **23** 个(数字以实际跑 test/test_*.py 为准)。
> 【2026-09-21 23:20 更正】④ 原写 data/「会话日志 + _life + images」—— `_life` 已不存在(与本文件 §5「不再有匿名全局 `_life.log`」矛盾),而且新的顶层 `cache/` 没进树 —— 真相:`data/` 实测 = `archive/ images/ presets/ sessions/ llm.local.json`;会话是目录制 `data/sessions/<sid>/{chat.log, meta.json, images/}`(`server/store.py:59-71`),`data/archive/`(`store.py:53`),`data/presets/`(`server/app/api/config.py:29-32`),`data/llm.local.json`(`server/app/llm_setup.py:24-28`)。记忆索引住**顶层 `cache/`**:`server/params.py:105-108` `MEMORY_CACHE_DIRNAME = "cache"`、`server/app/engine.py:75-79` `YONA_CACHE_DIR`,`.gitignore` 里有 `cache/`。
> 【2026-09-21 23:20 更正】⑤ 原根文档清单指向的路径一半是错的,还含一个全仓不存在的 `MAP.md` —— 真相:`MAP.md` 全仓库不存在;根目录只有 `README.md` / `AI-GUARDRAILS.md` 两个 .md;`DESIGN.md`/`VISION.md` 在 `docs/decisions/`、`LIFE_BACKFILL.md` 在 `docs/protocols/`、`STRUCTURE.md` 在 `docs/`。树里另补 `turn_lab.py` / `prompt_lab/` 两行(拍板见 `docs/decisions/TIMELINE.md` 09-19「实验台改名 + 开 prompt_lab/ 目录」)。

依赖方向(单向):`core` 不 import `server`/`character`;`character` 可 import
`core`;`server` import `core` + `character`;UI 只跟 `server` 的 HTTP 契约说话。

---

## 2. 功能取舍表(2026-09,替代旧系统的口径)

| 旧 Yona 功能 | 决策 | 状态 / 依据 |
|---|---|---|
| 内核 / 后台生命循环 | ✅ 已重做且更强 | 事件源 + 单循环 + surface + busy + 离线生活补写(K×shape 连续判定,见 LIFE_BACKFILL.md) |
| **RAG 长期记忆** | ✅ 已迁成标准工具(2026-09-21) | `recall` 注册在 server/app/engine.py:294;底座 core/memory.py + core/memory_cache.py(sqlite 索引住顶层 cache/,语义路 = 可选 BGE,core/embed.py)。相似度阈值 / rerank 那条已实测证伪(见 decisions/TIMELINE.md 2026-09-19 §二) |
| 预设 presets | ✅ 已实现(2026-09) | config.py 的 CRUD + data/presets/*.json;应用预设 = 复制进会话快照(当轮 > 快照 > 默认三层) |
| 多模型注册/切换 | ❌ 不做(UI 层的事,只影响 POST 参数) | rewrite 单模型 .env;UI 模型选择显示默认模型。协议轴 = 单 chat/completions 适配 + adapter 缝(将来),见 DESIGN §11 |
| 评测 eval | 📝 留注释(不迁) | 旧 LLM-as-judge 思路可参考 `D:\MyProject\Yona\src\eval\*`,不贴胶水 |
| 感官 sensory(ASR/TTS/vision/OCR) | 📝 留注释(不迁,冻结区) | 参考旧 `src/sensory/*`;UI 按钮点空静默 |
| objects/actions | 📝 留注释 | workspace.objects 空占位;旧 `src/objects+actions` 冻结区 |
| 管理端点(stats/rebuild-vector/export/clean-empty) | ✂️ 已剪 UI 入口 | 后端没有对应能力;按钮随 app-admin.js 移 `static/_unused/` |

> 【2026-09-21 23:20 更正】上面两行是全仓最新拍板的反面 —— 真相:① `docs/decisions/TIMELINE.md:1201-1214`「`recall` 工具从 `test/recall_probe.py` 毕业进产品」,`server/app/engine.py:294` `_tools.register(make_recall_tool(recall_index))`,`core/memory.py` / `core/memory_cache.py` / `core/embed.py`(可选 BGE)均存在,故原「🔜 迁成标准工具 / rewrite 暂无向量记忆,记忆 = 日志回放」已过时;② `server/app/api/config.py:53-102` 四个端点(GET 列表 / GET 单个 / POST 保存 / DELETE),落盘 `data/presets/*.json`(`config.py:29-32`),`static/app-presets.js:12/29/67/93` 真在调,故原「⏸️ 延后 / `/presets` 返回 `[]`」已过时(且与本文件 §4 冲突)。

规则:不是"旧系统有的都要有",而是"旧系统 = 踩坑与思路参考;上不上线看它
在**当前认知的闭环**里站不站得住"。

---

## 3. 为什么没有 src/(与薄厚 router)

- 项目从单文件(mvp.py)长出来,按模块绞杀重写,从未做目录层级重组 —— 所以
  没有旧系统那种 `src/` 一层。
- **src/ 化决策(2026-09):暂不迁**。成本 = 全量改 import + 重验全部测试;
  收益目前只是观感。条件满足(功能闭环稳定、要对外发布/面试定稿)后再做
  一次性机械迁移:`src/yona/{core,server,character}` + `docs/`,并把本文件
  及 DESIGN/VISION/MAP 一并迁入 docs/。
- **thin router(2026-09,两轮)**:`server/main.py` 从 917 行瘦到 309 行(行数 / 端点数以代码为准,2026-09-21 实测 **309 行 / 32 端点**)。
  **布局 B(用户拍板)**:请求域路由收进 `server/app/api/`(chat/view/media,
  同层一个子目录);组合根 + 生活运行时 `server/app/engine.py`;规则类独立
  `server/app/gate.py`;支撑库 store/rhythm 留在包根(测试/探针大量直连,
  收子包要留 facade 反而绕)。每个模块文件头有"学习对照"注释
  (chat.py 详解转发/缓冲:线程→asyncio.Queue→async 转发、busy 排队)。
  main.py 底部保留兼容再导出(旧测试/探针 import server.main 取符号,逻辑
  在 engine —— 已移除 facade(2026-09),测试直达 engine)。32 端点契约零变化
  (2026-09-21 实测),23 个测试全绿(2026-09-21 实测;测试数以实际跑
  test/test_*.py 为准)+ 真服务冒烟过。
  **用户口径**:先做一版供学习,学完 FastAPI 转发/缓冲再回来优化。

> 【2026-09-21 23:20 更正】行数 / 端点数 / 测试数一律以代码为准(2026-09-21 实测 **309 行 / 32 端点**)—— `server/main.py` 实测 309 行;端点实测 32 个(`main.py` 13 + `api/config.py` 7 + `api/media.py` 5 + `api/view.py` 6 + `api/chat.py` 1);测试实测 23 个 `test/test_*.py`。下文 §5「服务:24 端点」同此更正。

**心跳节奏参数(2026-09,gate.py 方案② —— 与补写同一原语)**:
- 概率形状继承 `rhythm.DEFAULT_SHAPE`(拍板曲线:深夜=0、晚间高):
  `命中概率 = SELF_WAKES_PER_DAY × shape(t) × Δt` —— 时刻倾向自动来自那张图,
  不再拍"深夜 5%/白天 30%"这类概率。
- **三值已拍板(2026-09)**:每天期望自发醒 **3 次**、冷却 **90s**、心跳间隔
  **60s**(params.py ✅;~~旧拍脑袋概率 0.05/0.30 + hot 分支已废弃~~;旧的硬编码概率 0.05 / 0.30 已废弃;**演示模式仍在**:`YONA_GATE_HOT=1` → `params.HOT_*`,接线见 `server/app/engine.py:981`)。

> 【2026-09-21 23:20 更正】原文"hot 分支已废弃"是错的(与 `docs/public/FAQ.md:19` 指令相反,FAQ 那条才是对的)—— 真相:`server/app/engine.py:981` 是 `hot = os.environ.get("YONA_GATE_HOT") == "1"`;`server/params.py:73-77` 有 `HOT_COOLDOWN_SEC = 20.0` / `HOT_INTERVAL_SEC = 20.0` / `HOT_WAKES_PER_DAY = 240.0`,并在 `_ROWS` 里作为 🔧 演示参数打印(`params.py:168-169`);废弃的只是**旧的硬编码概率**(深夜 0.05 / 白天 0.30)。
- 探针 `test/gate_probe.py`:30 天蒙特卡洛命中/天 3.07 ≈ 期望 3.0、深夜恒 0、
  命中时刻分布跟随 shape(18-20 点最密)。

---

## 4. UI 现状(旧 static 复用,重要发现)

旧 UI 9 文件原样拷入,但**文件名与功能不对应** —— 剔除空壳页面不能整文件删:

| UI 文件 | 真能力 | 空壳/占位 |
|---|---|---|
| app-core.js | 主聊天逻辑、设置(真接线)、会话切换、连接管理(模型下拉/向导) | — |
| app-messages.js | 消息渲染 + busy 帧提示(已加 rewrite 分支) | — |
| app-sessions.js | 会话管理(真) | — |
| app-presets.js | **life-events 内心活动面板(rewrite 核心展示)** + 预设 CRUD(2026-09 真存盘) | 预设已作用于运行时:应用预设 = 复制进会话快照(`{sid}.meta.json` 的 `settings`),当轮 > 快照 > 默认三层合并;权威见 DESIGN §12 + TIMELINE 任务 6(2026-09-17 更正:旧文写"尚未作用,等快照整合",该整合已落) |
| app-objects-sensory.js | **workspace 桌面(动作轨迹/生活事件/脉冲)** | 感官(发图/语音/朗读)+物件舞台:后端无端点/恒空,UI 入口已撤(2026-09 任务4/5),代码尸体标注冻结区,感官接回时复用 |
| app-media-debug.js | **LLM 输入输出调试(已接真日志:engine._TracingLLM 环形缓冲,折叠不空轮询;2026-09 起每调用带 token 用量与截断标记)** | — |
| app-admin.js | — | 已剪:stats/rebuild-vector/export/clean-empty 四按钮 404,移 `static/_unused/` |

**设置面板(2026-09 第 1 轮收紧,真接线)**:温度滑块 / 角色设定(留空=
旗舰,填写=本轮起覆盖人格段) / 上下文窗口轮数(默认 20,0=全量,按轮
边界整轮裁剪) 已真生效(loop.run_turn 可选字段);"Token 预算"滑块已撤
为只读说明(输出上限固定 4096 服务端,见 params.py);"摘要压缩"开关置灰
占位(compact 未接)。

**连接管理(2026-09 任务③,UI 是唯一配置入口)**:模型下拉列出**当前连接
端点真正可用**的模型(引擎快照,切换即生效 = 同端点换 model id,见
core/loop.py run_turn model 字段);「连接/更换模型」按钮 = 首启向导
(base_url + key → 实测拉通列表 → 落盘 `data/llm.local.json` → engine
热重配,免重启进程)。**`.env` 不再是产品配置**(config.py 只给脚本/探针;
未连接 = 引擎禁用,UI 弹向导,聊天/心跳待连后启动)。旧"自动发现"
手填区已撤,`/admin/discover-models` 端点保留未用。key 落本机明文文件
(gitignored 的 data/),HTTP 一律不回传(engine.llm_state 已 sanitize)。

**决策**:整体不剪 UI —— 聊天主流程、workspace、life-events 全靠它,契约完整;
空壳按钮点击为空/静默,不影响主体验,且未来接回 RAG/感官时 UI 现成。
只剪**确认无后端、独立成块**的入口(本轮:action 菜单 4 按钮)。未来要精简
表面积时,按"能力与按钮同生共死"(DESIGN §8 收束口)逐按钮清。

---

## 5. 内核能力清单(当前闭环,可对外讲)

- 事件源 SessionLog:append-only / 回放 / 投影 / surface(shadow+replace)/ 时间游标
- AgentLoop:单循环 / 工具驱动 / source=user|self / 子集白名单 / busy 锁 / fold 视图
  - **每轮可选字段(2026-09)**:temperature / max_tokens(覆盖到 LLM 调用,
    不给用实例默认)/ max_rounds(上下文窗口:保留最近 N 个**真人对白轮** + 当前轮 —— 独处轮不占名额(2026-09-21 拍板),整轮裁不切散工具配对)/ system_prompt(本轮人格覆盖串,替换 builder)

> 【2026-09-21 23:20 更正】`max_rounds` 语义 2026-09-21 改过 —— 真相:`core/session_log.py:368-421`「⚠️ **独处轮(自走 / 补写)不占这个名额**(2026-09-21 用户拍板)…`ordered_ended = sorted(t for t in ended_turns if t not in self_turns)`」;`server/params.py:94-96` `DEFAULT_CONTEXT_ROUNDS = 20`。
- 上下文:SystemComposer 段装配 + 变量插值 + builder 一/二/三参(registry, source, log)
- **LLM 调用元信息(2026-09,上游一次捕获)**:openai_compat 归一化 usage
  (input=cache 命中剔除/cache_read/output/reasoning,见 `_parse_usage`)+
  finish_reason;流式带 `stream_options.include_usage`;usage 锚到
  assistant/message,chunk 层留原始 —— 消费方(计量/成本/截断率)只读日志,
  不再回头改适配器
- 生命周期:Heartbeat 闸门(纯规则)+ LifeLoop 自走轮(source=self)
- **离线生活补写**(核心算法):rate = K×shape 连续概率判定,收编主 loop,
  无第二 AgentLoop —— 详见 `docs/protocols/LIFE_BACKFILL.md`
- 服务:32 端点(2026-09-21 实测;行数 / 端点数以代码为准)/ SSE 流式(asyncio.Queue)/ busy 帧 / workspace+life-events 观测
- 数据:每卡一套 life(2026-09 拍板)—— 生活流写卡**自己**的
  `chat.log(source=self)`,不再有匿名全局 `_life.log`;存储 = 会话目录制
  `sessions/<sid>/{chat.log, meta.json, images/}`。
  2026-09-17:**补写改为遍历所有有历史的卡**(见 DESIGN §12b 尾注 / TIMELINE 09-17)。

验证:23 个测试文件全绿(2026-09-21 实测;测试数以实际跑 `test/test_*.py` 为准;Mock + 真模型双路);探针/扫描/绘图工具齐(见
docs/protocols/LIFE_BACKFILL.md §6)。

---

## 6. 遗留与后续小项

- demo 已收进 `test/legacy/`(mvp / demo_real / heartbeat_demo):历史演示,
  跑法 `py test/legacy/xxx.py`(项目根目录下);验证职责已被 test/ 探针取代。
  2026-09 曾因文件编码误操作损坏、已从 pyc 逐字恢复字符串并等价重建。
- 设计资产收进 `assets/design/`(rate_curve.png 等):公开仓库里 test/ 可选择性
  发布,资产与文档应保留可看 —— 生成脚本输出直指该目录。
- `.gitignore` 已含 `data/`(2026-09):会话/生活日志与图片不入库。

> 2026-09 修订:本文为"结构身躯"(布局/取舍/UI)历史成稿,若干过时项清单见
> `docs/pitfalls/HISTORY.md §五`(git init 早已做、per-card life 取代 `_life`、
> 测试数/行数以代码为准)。当前真值排序与完整文档地图见 `docs/README.md`。
>
> 【2026-09-21 23:20 核准】

## 7. 演进规则(小步可闭合)

每加一个能力:① 在本文件/DESIGN 记取舍 → ② 内核或服务层实现 → ③ UI 复用
或小改(收束口)→ ④ 测试+探针 → ⑤ 更新 MAP / STRUCTURE。不积攒大爆炸改动。
