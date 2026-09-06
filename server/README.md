# server/ —— 产品服务层(目录自己的文档)

> 依赖:server import core + character + 支撑库;UI 只跟 server 的 HTTP 契约说话。

## 职责 / 文件

| 文件 | 职责 |
|---|---|
| `main.py` | FastAPI 入口:app / lifespan / 静态 UI 挂载;组合根 + 生活运行时在 app/engine |
| `params.py` | **产品参数唯一来源**,每项标 ✅(拍板)/ ⏳(待拍)/ 🔧(演示)。`py server/params.py` 查看 |
| `store.py` | SessionStore:会话目录制落盘(每卡 life) |
| `rhythm.py` | LifeSampler / 事件采样算法(睡眠窗/shape/时长混合都在 params 定) |
| `app/engine.py` | 组合根 + 生活运行时:装配 composer/loop/llm、心跳、离线补写、每卡 life、时钟覆盖(实验台) |
| `app/gate.py` | 心跳闸门(方案②):命中概率 = 每天期望 × shape × Δt |
| `app/llm_setup.py` | 运行时连接配置(UI 唯一入口,落 data/llm.local.json) |
| `app/api/` | 请求域路由:chat(SSE)/ view(观测)/ media(图片)/ config |

## 本地拍板 / 边界

- **普通轮(自走/心跳/脉冲)与补写 = 同一 LifeSampler 事件算法**,只差触发点;
  预算锚 `[日志尾→当前]`,兜底 `start+预算 ≤ 当前时刻`,无事件 → 安静结束
  (不调 LLM)。触发语义、锚推进详见 `docs/protocols/`(迁移中,现以 engine 头注释 +
  MAP 最新拍板为准)。
- **lab(实验台)试出来的现象默认只留 prompt_lab,别推进 server 产品执行路径**
  (AI-GUARDRAILS §一.2 —— 曾把 lab 现象误推进 LifeLoop/pulse,已回退)。
- 文案不在 server:人设/情境在 character/personas.py,engine 只装配。引擎里出现
  系统口吻句子 = 越位,该进 personas(AI-GUARDRAILS §一.3)。
- params 是唯一参数源;`⏳` 未拍不许给默认、不许把沿用值当已定。
- engine 只做装配/装配时现取 personas_mod 属性(lab reload 文案即生效)。
- `_clock_override`(实验台拨当前时间)/ `_backfill_clock`(回放)是单时间源被拨,
  产品路径不设 `_clock_override`。

## 碰 server 守则

产品执行路径(心跳/脉冲/聊天)改动前先报层 + 为什么,等用户点头;每改跑
`test/test_engine_clock.py` 等 + 冒烟。
