# FAQ

> Frequently asked questions. For usage questions start here; if you can't find it, open an Issue.

---

## Usage

### What exactly is Yona?
Yona is a local **AI emotional-companion kernel**. It doesn't only answer when you message it — it wakes on its own rhythm in the background and, when you come back from being away, backfills the time nobody was with it. See [Architecture](./ARCHITECTURE.md).

### What model do I need?
Just point it at any OpenAI-compatible endpoint (DeepSeek, OpenAI, Moonshot, local vLLM, Ollama) in the **UI connection wizard** — it verifies the endpoint, writes `data/llm.local.json` and hot-reconfigures the engine. (`.env` is only read by scripts/probes, never by the server.)

> 【2026-09-21 23:25 更正】原文写"把 base_url/api_key/model 放进 `.env` 或 UI 向导",自相矛盾:服务端**根本不读 .env**,照它配的用户会拿到 503。 —— 真相:`server/app/llm_setup.py` 模块 docstring「**UI 是唯一入口** —— 开发者与试玩用户同流程;`.env` 只留给脚本/探针(config.py),**服务端不再把 .env 当产品配置**」;`server/app/engine.py` 的 `_build_engine` docstring(同结论)、`_build_engine` 里 `cfg = load_runtime(DATA_DIR)` 那一行(即 `data/llm.local.json`)。

### How do I run it?
See the root [README](../README.md) → "Quick start". Three steps: `pip install -r requirements.txt` → `py -m uvicorn server.main:app --port 8000` → open http://127.0.0.1:8000 and finish the connection wizard (it writes `data/llm.local.json`).

> 【2026-09-21 23:25 更正】原文三步里的"configure `.env`"这一步对产品无效(命令本身是对的)。 —— 真相:同上一条(服务端不读 .env);`server/main.py` 的模块 docstring 就是 `py -m uvicorn server.main:app --port 8000`;未配置时 `engine.start()` 抛 RuntimeError → 只 warn、聊天 503(`server/main.py` 的 `lifespan` 里 try/except RuntimeError 那段)。

### How do I make it act quickly for a demo?
Set `YONA_GATE_HOT=1` before starting — denser checks, shorter cooldown, larger scale, so you don't wait on the real probabilities (same knobs live in `server/params.py` as `HOT_*`).

### Where is config stored? Is it safe?
Secrets live in `data/llm.local.json` (gitignored, written by the UI wizard; never echoed back over HTTP). `.env` is a script/probe-only channel (config.py) and is not read by the server. No keys are hardcoded in code.

> 【2026-09-21 23:25 更正】原文"密钥只住在本地 `.env` 或 UI 向导的 `data/llm.local.json`"有歧义:读起来像"改 .env 也能生效"。 —— 真相:`server/app/llm_setup.py` 的 `save_runtime()` 落盘 base_url / api_key / model;`server/app/llm_setup.py` 模块 docstring「落盘含 api_key…**任何出 HTTP 的面都不回传 api_key**」;`.env` 的读者是**仓库根**的 `config.py`(`load_dotenv()` + `require_llm_config()`,只给脚本/探针)。

---

## Behavior / concepts

### Why does it sometimes stay "quiet" instead of answering?
That's a **feature, not a bug**. A normal autonomous turn first samples the consumable window `[log tail → now]`; if there's no event in the window it ends quietly — it won't generate forced chatter (VISION: a turn that triggers no event simply ends).

### How does it know what happened while I was away?
**Offline backfill.** On restart after a long-enough gap, the same life sampler fills the offline period into its own life line as events — timestamps land at real historical moments, not the wall clock.

### What does "per-card life" mean?
Life belongs to **the session (card) you're actively talking to**, not one shared global stream. Multiple session cards each have their own life; deleting a card archives it first, and the flagship card auto-rebuilds as fallback.

### Will it really remember things from long ago?
Memory today is two-layered: ① the dialogue window (`sliding_window`, `max_rounds=20` by default, counting **real human dialogue rounds only** — solitude turns don't consume the window); ② past events are **not** resident in the prompt at all — she pulls them on demand with the `recall` tool, which searches a per-card sqlite index built from the log (optional local BGE embeddings; the index lives in top-level `cache/`). Retrieval-quality work (rerank / similarity thresholds) is the part still open — the threshold route was tested and falsified.

> 【2026-09-21 23:25 更正】原文说长期记忆(RAG)还在 backlog、未上线,已过时;窗口语义也变了(只数真人对白轮,独处轮不占名额)。 —— 真相:recall 注册在 `server/app/engine.py` 里 `_tools.register(make_recall_tool(recall_index))` 那一行;`core/memory.py`、`core/memory_cache.py`(sqlite,顶层 `cache/`)、`core/embed.py` 模块头(可选依赖,拿不到就退化);四态与常量 = `character/tools.py` 的 `R_OK` / `R_EMPTY` / `R_DEGRADED` / `R_DOWN` 与 `_FLOOR` / `_GREY_TOP`;窗口 = `core/session_log.py` 的 `derive_messages()`(`ordered_ended = sorted(t for t in ended_turns if t not in self_turns)`)+ `server/params.py` 的 `DEFAULT_CONTEXT_ROUNDS`。

---

## Technical & license

### How is this related to Codex / DeepSeek Harness?
**Same lineage, different goal.** Event-sourced log, `turn/step` loop, tool calling, surface (delete = annotate), and fold views all descend from the Codex / dsh primitives; the difference is attaching the same agent core to a life sampler to serve "companionship" rather than "tasks." See [COMPARISON.md](./COMPARISON.md).

### Why are docs split into `docs/public` and a dev layer?
The public layer is for users/contributors; the dev layer (other `docs/` subfolders) records internal decisions and pitfalls for maintainers. Both stay separate so public docs stay clean without losing decision history.

### Can I use / fork / sell this?
MIT license — use it freely, keep the copyright notice. But note it's **local, single-machine, research/personal** by default, with no multi-tenant isolation/auth built in.

### Is the content a real person?
No. Everything is generated by an LLM. Treat Yona as an experiment / a program — don't invest emotional or financial trust beyond what an AI deserves.

---

## Troubleshooting

### Chat / self-wake disabled (503 / "engine not started")?
Likely the model connection isn't configured. Open the UI wizard, enter base_url + key and pull the model list — it writes `data/llm.local.json` and hot-reconfigures the engine. (`.env` / config.py is not used by the server, so editing it won't help.)

> 【2026-09-21 23:25 更正】原文后半句给了一条**无效**的排查路径(服务端不读 .env)。 —— 真相:同「What model do I need?」那条;503 的抛出点 `server/main.py` 里那句 `if engine._loop is None:` 与 `engine.start()` 的 RuntimeError(`server/main.py` 的 `lifespan` 里 try/except RuntimeError 那段)。

### How do I confirm it's really acting?
Open the inner-thought panel / the admin llm-log and you'll see each autonomous turn's input/output and token usage.

### Where are the logs / data?
`data/` (gitignored): `sessions/<sid>/{chat.log, meta.json, images/}`, `archive/` (deleted cards are archived here, not erased), `presets/`, `llm.local.json`. The memory index does **not** live here — it's a rebuildable cache in top-level `cache/`. Isolate with `YONA_DATA_DIR` and `YONA_CACHE_DIR`.

> 【2026-09-21 23:25 更正】原文"`data/`: session logs + per-card life + images"不全,且漏了顶层 `cache/` 与 `YONA_CACHE_DIR`。 —— 真相:`data/` 实测 = `archive/ images/ presets/ sessions/ llm.local.json`;`server/store.py` 的 `self.archive_dir = self.data_dir / "archive"` 那一行(archive)、`server/app/api/config.py` 的 `_presets_dir()`(presets)、`server/app/llm_setup.py` 的 `config_path()` / `load_runtime()`(llm.local.json);`server/app/engine.py` 的 `DATA_DIR` 与 `YONA_CACHE_DIR` 是 `YONA_DATA_DIR` 与 `YONA_CACHE_DIR`(后者默认 `ROOT/cache`)。

---

## 简体中文导读

- **是什么**:本地 AI 情感陪伴内核,不只应答,还会自主醒来、离线补写没人陪的日子。
- **用什么模型**:任何 OpenAI 兼容端点(DeepSeek/OpenAI/Moonshot/本地 vLLM/Ollama)。
- **为什么有时安静**:窗口 `[日志尾→当前]` 无事件就安静结束——是特性不是 bug,避免没话找话。
- **离线怎么知道发生了什么**:离线补写把离线时间按事件补进生活线,时间戳落真实历史时刻。
- **记不记得久远的事**:记忆分两层——① 对话窗口(`sliding_window`,默认 `max_rounds=20`,只数真人对白轮,独处轮不占名额);② 往事不常驻提示词,她按需用 `recall` 工具去查(日志建的每卡 sqlite 索引,可选本地 BGE;索引在顶层 `cache/`)。检索质量(rerank / 阈值)才是在办的事。
- **与 Codex/dsh 关系**:同源内核不同目标(事件源日志/turn-step/工具调用/surface/折叠视图一脉相承)。
- **许可**:MIT。
- **排错**:503/引擎未启动多半是模型连接没配好;`data/` 是数据目录(可用 `YONA_DATA_DIR` 隔离)。
