<div align="center">

# Yona

**An emotional-companion agent kernel** — AI that isn't a reply machine, but a character who *lives* its own life.

[简体中文速览](#简体中文速览) · [Architecture](./docs/public/ARCHITECTURE.md) · [The Codex / DeepSeek-Harness lineage](./docs/public/COMPARISON.md) · [Roadmap](./docs/public/ROADMAP.md) · [FAQ](./docs/public/FAQ.md)

</div>

---

## What makes Yona different from a chatbot

Most "AI companion" products are, at heart, a **you-say / it-replies** dialogue shell. Yona is not designed that way:

- **An autonomous daily rhythm, not a timer.** While the process is alive, the character is alive. A cheap life gate (pure rules, **zero LLM**) decides whether "now is worth waking up"; only when it is does the model decide and act — and it only wakes when there is a real, consumable event in its time window. No event, no forced small-talk.
- **Offline backfill — it fills in the days you were away.** Come back after six hours and you won't get a cold "welcome back." Using the same life-sampling algorithm, it has already written, into its own life line, what it was doing while you were gone — as real events with a time and a duration.
- **One person across chat and solitude.** When you talk to it, that's a conversation. When it's alone or backfilling, that's **the same character** living its own life. The persona is written once; it never splits into different people depending on how it was triggered.

> **One-line positioning:** Yona is an experiment in pushing an agent from "tool" toward "companion." Its technical core is the same family as Codex / DeepSeek Harness — but the goal shifted from *completing tasks* to *sustaining a continuous life.*

---

## ✨ Highlights

- **Event-sourced session log (SessionLog)** — all state derives from an append-only event stream: replayable, recoverable, auditable. Deleting / editing doesn't erase data; it appends a *surface* annotation. The log always stays complete.
- **One unified agent loop** — streaming consumption + tool calling. `user` (real human) and `self` (autonomous) are two triggers on the **same kernel** and the **same tools**.
- **A life sampler (LifeSampler)** — a continuous probability model `rate(t) = K × shape(t)` that grows event start-times and duration budgets out of a real sleep/wake curve. It sleeps inside its sleep window and goes naturally quiet in sparse hours.
- **Per-session life (life lives on "the card")** — life belongs to the session you're actively talking to, not one shared global stream.
- **Observability by default** — transcript / inner-thought feed / per-call LLM input-output debug log (SSE live). You see not just *what it said* but *what it was thinking and which tool it called.*
- **Model-agnostic** — any OpenAI-compatible endpoint works: DeepSeek, OpenAI, Moonshot, local vLLM, Ollama. Swap `base_url` to switch vendor.
- **Local-first, zero-build web UI** — self-hosted on one machine; secrets stay in a local `.env`.

## 🚀 Quick start

```bash
# 1. clone / enter
cd yona-rewrite

# 2. configure (secrets only live in .env, never in git)
cp .env.example .env          # fill in LLM_API_KEY / LLM_BASE_URL / LLM_MODEL

# 3. install
pip install -r requirements.txt

# 4. run
py -m uvicorn server.main:app --port 8000
```

Open `http://127.0.0.1:8000`.

- On first launch, complete the **connection wizard** in the UI to point at your model endpoint and pull the model list (config is stored locally and can be hot-swapped without restart).
- To see it act quickly, use the demo mode: set `YONA_GATE_HOT=1` before starting — denser checks, shorter cooldown.
- Without a configured LLM, chat/self-wake is disabled, but the UI and structure remain browsable.

> Runtime deps: [`requirements.txt`](requirements.txt). More in the [`FAQ`](./docs/public/FAQ.md).

## 🏗️ Architecture at a glance

```
core/       Engine kernel (pure logic; never imports server/character)
  loop.py          responsive agent loop (streaming + tools, user/self dual-source)
  session_log.py   event-sourced session log + surface annotations + projection
  openai_compat    vendor-agnostic OpenAI-compatible client
server/     Product service layer (FastAPI thin router)
  app/engine.py    composition root + life runtime (heartbeat/backfill/per-card life)
  app/gate.py      heartbeat gate (pure rules: cooldown/time/random, zero LLM)
  rhythm.py        LifeSampler life-event sampler (rate = K × shape)
  params.py        single source of product params (each flagged decided/pending)
character/  Character layer (persona / state / tools)
static/     Local web UI (zero build)
test/       tests + probes + plotting scripts
docs/public/  public docs (this README points here)
docs/        dev-layer docs (decisions/ tasks/ pitfalls/ — for maintainers)
```

Layer design, the event model, and *how it lives*: **[ARCHITECTURE.md](./docs/public/ARCHITECTURE.md)**.

## 🔗 The Codex / DeepSeek-Harness lineage

If you've read the agent kernels of OpenAI Codex or DeepSeek Harness, Yona's core will feel familiar — **deliberately so**:

| Mechanism | Yona | Peer reference |
|---|---|---|
| append-only event log as single source of truth | `SessionLog` | dsh `session` model |
| loop with `turn/step` boundaries | `AgentLoop` | dsh `step()` / Codex agent loop |
| schema-driven tool calling | `ToolRegistry` | Codex / dsh tool use |
| delete/edit = annotate, never rewrite the log | `surface/shadow`, `replace/compact` | dsh `surface.ts`, Codex fork semantics |
| per-step LLM call & token observability | llm-log / agent-feed | agent telemetry |

**The difference is the goal.** Codex uses it to *write code*; Yona uses it to *live a life* — the same agent-harness core attached to a life sampler so it acts autonomously, with continuity, and within a time budget. A clean open-source case of "same engine, different purpose." Read the full breakdown: **[COMPARISON.md](./docs/public/COMPARISON.md)**.

## 🗺️ Roadmap

Short-term / near-term (kernel loop already closed): autonomous life loop ✅ · offline backfill ✅ · per-card life ✅ · observability ✅ · model-connection management ✅.
In the backlog (documented decisions, deliberately not prioritized): **long-term memory (RAG)** / **character preset packs** / **voice & senses** / **eval**.

See **[ROADMAP.md](./docs/public/ROADMAP.md)**.

## 🧰 Use cases

- **For companion / virtual-character builders** who want to study how a *continuous-life* AI character is implemented.
- **For agent-architecture learners** — the clearest reference for pointing a Codex/dsh-style core at a non-task goal.
- **Local-first AI character demos / portfolios.**

## ⚠️ Notes & caveats

- This is a **research / personal, local, single-machine** project — not a production multi-tenant SaaS. Isolation, auth, and long-term memory belong to the backlog, not the current kernel loop.
- **All content is AI-generated.** Everything the character says or does comes from a model and is not a real person; please don't over-invest emotionally or financially in an AI character.
- LLMs hallucinate and have safety boundaries. This project does no content moderation; follow your model vendor's usage policy.

## 📜 License

[MIT](./LICENSE) © zhibailu

---

## 简体中文速览

> 定位:一个 **AI 情感陪伴内核**——不是聊天机器人外皮,而是把"后台自主生命感"当一等公民的 agent 运行时。角色会在你看不到时按自己的生活节奏醒来、活动;你离线再回来,它已默默补完这段没人陪它的日子。

- **不是应答机**:低成本生活闸门(纯规则、零 LLM)判断"此刻值不值得醒";窗口内无事件就安静,不硬产话。
- **离线补写**:同一套生活采样算法,把你不在的时间按真实事件补回它自己的生活线。
- **同一个它**:陪聊/独处/补写是同一份人格,不因触发方式分裂。
- **技术内核与 Codex / DeepSeek Harness 同源**(事件源日志、turn/step 循环、工具调用、surface=删除不改日志),但服务目标从"完成任务"换成"过一段有连续感的生活"。

快速开始:配置 `.env` → `pip install -r requirements.txt` → `py -m uvicorn server.main:app --port 8000` → 打开 `http://127.0.0.1:8000`,首次在 UI 连接向导填模型端点。
