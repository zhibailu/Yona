# Architecture

> How Yona works — the **agent kernel + life runtime** underneath it.
> For the technical reader; to run it quickly see the root [README](../README.md).

---

## 0. In one line

Yona = **a general agent loop** (event-sourced session + streaming tool calling) **+ a life sampler** (decides *when* the character wakes and with what life budget) **+ a character layer** (persona / state / tools).

The three are cleanly layered: `core/` knows nothing about "life"; `server/` composes them into the process's one instance.

---

## 1. Layering at a glance

```
┌─────────────────────────────────────────────────────────────┐
│ static/  local web UI (zero build)                          │
├─────────────────────────────────────────────────────────────┤
│ server/  product service layer (FastAPI thin router)        │
│   main.py          HTTP contract: session/chat/govern/obs/connect/pulse │
│   app/engine.py    composition root + life runtime (singleton, start/stop)│
│   app/gate.py      heartbeat gate: pure rules (cooldown/time/random), 0 LLM│
│   rhythm.py        LifeSampler life-event sampler            │
│   params.py        single source of product params (decided/pending flags) │
│   app/api/config.py  presets CRUD + model discovery         │
├─────────────────────────────────────────────────────────────┤
│ character/  character layer (depends on core)                │
│   personas.py      persona copy (who it is / situation) — content layer    │
│   persona.py       section factory: assemble copy into SYSTEM sections     │
│   state.py/tools.py    character state + tools               │
├─────────────────────────────────────────────────────────────┤
│ core/  engine kernel (pure logic; never imports server/character) │
│   loop.py          AgentLoop: responsive single loop (stream + tools) │
│   session_log.py   SessionLog: event-sourced log + surface annotation + projection │
│   openai_compat    vendor-agnostic OpenAI-compatible client  │
│   assembler.py     safe streaming-block accumulation → projection │
│   llm.py/tools.py  abstractions                              │
│   composer.py      SystemSection assembly (content layer → SYSTEM) │
│   memory.py / memory_cache.py / embed.py  recall's index + optional BGE │
│   subrun.py        worker sub-run (delegated tool)           │
└─────────────────────────────────────────────────────────────┘
```

> 【2026-09-21 23:25 更正】上图漏了一整级(composer)和整个记忆层。 —— 真相:`core/composer.py`(182 行,`SystemSection`,即 `server/app/engine.py` 里 `from core.composer import SystemSection, make_timeline_section` 那一行导入)、`core/memory.py`(300)、`core/memory_cache.py`(389)、`core/embed.py`(175)、`core/subrun.py`(335)、`server/app/api/config.py`(预设 CRUD + 模型发现,`server/main.py` 里 `from .app.api import chat, config, media, view` 那一行)都存在。

---

## 2. The data spine: event-sourced session log (SessionLog)

**Core conviction: all state comes from one append-only event log.** There is no separate "conversation table," no scattered mutable in-memory state.

- `append(type, **data)` — appends an immutable, `seq`-numbered event.
- Events are organized on `turn/step` boundaries: `turn/start`, `user/message`, `assistant/message`, `tool/call`, `tool/result`, `step/start`, `step/end`, `turn/end`, plus annotation events like `surface/shadow`.
- `derive_messages()` — **projects** the log into the `messages` fed to the model. Projection ≠ the log itself.
- `replay_from(seq)` — replay from any point. ⚠️ **Not what production uses for recovery**: resumption/recovery goes through a **full re-read** of the log (`SessionLog.from_lines` / `server/store.py` 的 `_load_log`), never an incremental replay — `replay_from` is called **only by tests** today, and the 2026-09 cleanup left a placeholder note on it in `core/session_log.py`。

> 【2026-09-21 23:45 更正】本节原写 `replay_from(seq)` — replay from any point (**crash recovery / resumption**),
> 把"生产会用增量回放做恢复"写成了事实 —— **不成立**(见上一行)。占位标注原文在 `core/session_log.py`:
> 「⏸ **占位:docstring 里那句"用于崩溃恢复/续跑"是承诺,生产没接这个口子**」;真正在重读整份日志的是
> `server/store.py` 的 `_load_log`。

### Why append-only is right

1. **The truth is always complete.** What it said and which tools it called are never deleted. Deleting/editing isn't destroying history; it's saying "skip this segment" at the view layer.
2. **Auditable.** Replay any moment to rebuild the full state at that time.
3. **Projection is where mutability lives.** Make the model "forget" a segment, fold tool traces, or compact a middle segment — all in projection. The log stays untouched and safe.

### surface: delete = annotate, never delete

UI delete / undo / edit all collapse into one primitive: **`surface/shadow {start, end, reason}`** — tagging an already-happened range as "not needed for now." Projection skips the shadowed segment; the log keeps it. Any historical segment can be:

- `current` — visible to the model now
- `shadowed` — shadowed, kept in the log
- `log-only` — pure annotation, never on the surface

Plus `replace` (compact): shadow a range **and** top it with a summary, `anchor`ed back to the start of the shadowed range — so compressing an old middle segment doesn't reorder the conversation.

> This is the same family as Codex / dsh (see VISION §9: modeled on dsh `surface.ts`, Codex fork). Mechanics: [COMPARISON.md](./COMPARISON.md).

---

## 3. The brain: AgentLoop (one unified agent loop)

`AgentLoop.run_turn(...)` is a clean single loop:

```
while step < max_steps:
    messages = derive_messages(log)              # system + history
    blocks   = stream_and_assemble(messages)     # stream chunks → safe accumulation
    tool-call present? → run tool, write result back to log, loop again
    none (finish=stop)? → that's the answer, turn ends
```

**Two triggers, one kernel** (`source` param):
- `source="user"` — a real human message.
- `source="self"` — autonomous wake; no human message, so the `user` slot gets a protocol placeholder string telling the model "nobody is talking to you this turn" so it isn't mistaken for an instruction.

**Only one turn at a time** (internal lock): a character does one thing at a time. If a user message arrives while a background turn runs, "it's busy" is a feature, not a bug. Since 2026-09-17 all four turn sources go through one queue (`_submit_turn`, single `yona-turn` worker) — never concurrent. Priority: a card still being backfilled keeps its slot ahead of a user message sent to it; otherwise `user` > `self`. The loop keeps its own turn lock as a second line of defence.

> 【2026-09-21 23:25 更正】原文只写"internal lock",机制已变成"队列 + 单 worker + 优先级插队",而优先级规则是明确拍板的产品语义。 —— 真相:`server/app/engine.py` 的 `_submit_turn`(四来源统一入口);`_QUEUE_USER = 0` / `_QUEUE_SELF = 1`;`server/app/engine.py` 的 `user_turn_priority()`(该卡还在补写 → user 排在补写之后);`core/loop.py` 的 `AgentLoop` 里 `self._turn_lock` 的 turn 锁仍在(单个 loop 内互斥那层没错)。

**System is assembled per step**: persona/state/world/tool-usage are injected by a builder at the composition layer, able to inspect `(registry, source, log)` to detect backfill vs. normal turns and switch the time source.

---

## 4. The time model: one time source + a relative timeline

Yona cares about "time feeling" — that's the technical source of the companionship feeling:

- **World section = absolute time** (clock; dynamic, static-ish info). Refreshed each turn, always visible to the model — no need to "call a tool to check time."
- **Timeline section = relative time** (how long since the last real human interaction), derived from the log's `Event.time`, not extra state. This lets the model tell "just chatted" from "long apart."
- **Single-time-source principle**: the moment comes only from the world section. The clock *may* be shifted by the lab/demo (`_clock_override`); backfill turns instead walk the historical cursor (`_backfill_clock`). Product paths never override — they always use the real wall clock.

Two kinds of turn:
- **Chat turns**: current time only, no timeline (while chatting, "how long since we last chatted" is noise).
- **Solitude / backfill turns**: mount the timeline + time budget — it needs to know how long it was alone and what it can do in that window.

---

## 5. How it "lives": the autonomous life loop

VISION decision 2: **autonomy = a background life loop, not a timer.**

### 5.1 Waking up (heartbeat)

```
heartbeat thread (judged every `interval`, jittered)
  → ServerGate gate: pure-rules decision "is now worth waking?"
      hit probability = SELF_WAKES_PER_DAY × shape(time) × Δt
      deep night (sleep shape = 0) → not waking; just self-woke (cooldown) → not waking
  → worth it → LifeLoop.run_turn(source="self")
```

The gate is **pure rules, zero LLM** — it never burns an LLM call at a time it shouldn't wake.

### 5.2 Time budget = a real event, not a random number

When a normal autonomous turn (heartbeat/pulse) wakes, it samples the **consumable window** `[last interaction time (log tail) → current real time]` with one `LifeSampler` pass:

- A hit event → this turn's budget = that event's duration.
- **Safety clamp**: `start + budget ≤ current time`. An overlong budget is truncated to `current − start` (it can't do more than the elapsed time).
- **No hit → no event this turn → ends quietly**: no LLM call, no log write, no cooldown (the heartbeat keeps waiting from the same anchor).

What the model sees as `[time budget]` is the truncated value, phrased as "this window is about X; do one thing, don't start something you can't finish." (Copy lives in the content layer.)

### 5.3 Offline backfill: fill in the days

When the process restarts after being off long enough (gap > threshold), **every card with history** gets an offline-life backfill — one card at a time, ordered by offline gap so the card you're on is filled first:

```
set the log's time cursor
  → LifeSampler(offline start → boot time).sample()     # dice per grid point
  → each hit event: run one source="self" replay turn
      world time = historical cursor (the model sees "then")
      one thing per turn; note hints "this was ~X, you had ~Y gap"
  → events land in that card's own chat.log (the record of its time away)
```

Key: **backfill = the replay of an autonomous turn**, not a second persona and not a second AgentLoop. Same card, same loop — only the **time is jumping**. Backfill gets no live tools (those days shouldn't "check the weather now").

> 【2026-09-21 23:25 更正】原文只讲"这张卡"补写,漏了 2026-09-17 的拍板:补写对象是**所有有历史的卡**。 —— 真相:`server/app/engine.py` 的 `_maybe_backfill_life` docstring「启动时遍历**所有有历史的卡**,按**离线间隔升序**(= `updated_at` 降序)一张一张来 —— 当前卡天然第一」;`server/store.py` 的 `def life_backfill_order()`;阈值 = `params.WAKE_AFTER_GAP_SECONDS`(30 min,`server/params.py` 的 `WAKE_AFTER_GAP_SECONDS`)。

### 5.4 Per-card life: life belongs to "that card"

There is no longer an anonymous global life stream. Life belongs to **the card you're actively talking to**: a heartbeat wake means that card is awake; its autonomous events are written into its own log (invisible in chat view — the inner-thought panel reads them straight from the log, and the model only sees them when she calls the `recall` tool; life events no longer sit in the prompt at all). Deleting a card archives it first; the flagship card falls back and auto-rebuilds.

> 【2026-09-21 23:25 更正】原文说生活事件"model context 也看得见",已被 2026-09-21 拍板推翻。 —— 真相:`core/session_log.py` 的 `derive_messages()`(「生活事件不进投影(2026-09-21 用户拍板)」,已结束的**独处轮**的 assistant 消息**整条跳过**);`server/app/engine.py` 里那句注释「⚠️ 这里**没有** life_event_prefix(2026-09-21 拆除):生活事件整条不进投影,取用路径只剩 `recall` 工具」;聊天视图隐藏 = `server/store.py` 的 `_messages_view()`;取用 = `character/tools.py` 的 `make_recall_tool`(`server/app/engine.py` 里 `_tools.register(make_recall_tool(recall_index))` 那一行注册)。

---

## 6. Content layer vs. mechanism layer: copy strictly separated from engine

One of the project's strongest layering disciplines:

- **`character/personas.py` = content layer** — who it is (PERSONA), what situation this turn is (CHAT/SELF situation), system voice, and how the `[time budget]` sentence is phrased. **Copy lives in the character layer, and only there:** `personas.py` (persona, situations, SYSTEM templates such as `[time budget]`) and `tools.py` (each tool's `description` / `usage`). The engine only composes.

> 【2026-09-21 23:25 更正】原文"Want to change copy? Change only this file"不成立:工具侧文案住在 `tools.py`。 —— 真相:`character/tools.py` 的 `RECALL_DESC`、`RECALL_USAGE`、`RECALL_PARAM_QUERY`、`RECALL_PARAM_SCOPE`、`_launch_usage`,经 `core/composer.py` 的 `make_usage_section()` 渲染进 SYSTEM;`personas.py` 侧只有 `PERSONA` / `CHAT_SITUATION` / `SELF_SITUATION` / `WAKE_BUDGET_TEMPLATE` / `USER_TIME_PREFIX` / `LIFE_EVENT_PREFIX`。

- **Engine only composes.** engine feeds copy to the section factory to build SYSTEM. A system-voiced sentence appearing in engine code = a boundary violation (it happened once; already fixed).
- **Persona ≠ a property of the turn.** Chat / solitude / backfill are the *same character* sharing one PERSONA; each turn only adds its situation section. Never rewrite identity per trigger.

To change characters without touching code, a character-preset pack (cf. dsh agent-presets) is a planned future item.

---

## 7. Observability

Vision: observability first, not the front-end. You can watch the agent "think" live:

- **Transcript / inner-thought feed (life-events)**: projected from the log — the character's own words and inner monologue from autonomous turns (timestamped, never pretending to be "said to the user").
  > ⛔ 【2026-09-22 10:45 更正】上面这条把 life-events 当成对外观测面在写,**这层分量是擅自加的** ——
  > 该面板的接法与投影全是 baseline(`2903b2a`)搬来的旧 UI 配套,不是 rewrite 设计出来的产品面。
  > **用户 2026-09-22 10:45 判定内心活动(life-events)与桌面(workspace)两个面板不留**,
  > 现状**标注待砍、不摘**(本次判定只点名这两个面板)。详见 `server/app/api/view.py` 的模块头。
- **llm-log panel (admin)**: every real LLM call's input/output/token usage, ring buffer + live SSE. All real calls go through one choke point (`_TracingLLM` wrapper); no scattered instrumentation.
- Tool calls are fully traced in the event log; projection can fold them (tool trace = view, not log).

---

## 8. Status & next steps

- Done: autonomous loop, offline backfill, per-card life, surface governance, observability, hot model-connection management, multi-session, long-term memory recall (the `recall` tool over a per-card sqlite index built from the log, optional local BGE embeddings; the index lives in top-level `cache/`).
- Backlog (documented decisions, deliberately not prioritized): rerank / retrieval-quality work (the similarity-threshold route was tested and falsified), character preset packs, voice & senses, eval.
- Details: [ROADMAP.md](./ROADMAP.md).

> 【2026-09-21 23:25 更正】原文把 long-term memory 留在 Backlog,已过时 —— 它已上线;真正被**证伪而未采用**的是阈值 / rerank 那条。 —— 真相:recall 已进产品(同 §5.4,`server/app/engine.py` 里 `_tools.register(make_recall_tool(recall_index))` 那一行注册);阈值 / rerank 被证伪见 `docs/decisions/TIMELINE.md` 的「2026-09-19 · 往事段 + recall 工具」§二「⚠️ 阈值那条路**已被实测证伪**」。

---

## 简体中文导读

Yona 是 **agent 内核 + 生活运行时** 的组合:通用 agent 循环(事件源会话 + 流式工具调用)+ 生活采样器(决定角色何时醒、带多大生活预算)+ 角色层。

- **数据脊柱**:append-only 事件日志是唯一真相,可回放/恢复/审计。删除/编辑 = 追加 `surface/shadow` 注解,**永不改日志**。
- **一个循环,两个触发源**:`user`(真人)/`self`(自走)共用同一内核与工具;同一时刻只跑一轮。
- **时间感**:世界段 = 绝对时间;时间线段 = 相对时间(距上次真人互动多久);单时间源,产品路径永远真实墙钟。
- **怎么活**:心跳 → 纯规则闸门(零 LLM)→ 值得才让模型决策;预算 = 对 `[日志尾→当前]` 跑 LifeSampler 的真实事件(有兜底截断,无事件就安静);离线补写 = 同一 loop 的回放,只是时间在跳;**每卡 life** = 生活属于你在聊的那张卡。
- **分层纪律**:人格文案在 `character/`(内容层),引擎只装配;核心纯逻辑不 import 上层。
