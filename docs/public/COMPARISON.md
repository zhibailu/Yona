# The Codex / DeepSeek-Harness lineage (COMPARISON)

> Yona's core is **not another chat demo** — it is a real agent kernel whose primitives are explicitly modeled on those of OpenAI Codex and DeepSeek Harness. This page explains **what is shared, what differs, and why it's worth reading side-by-side.**

---

## In one line

**The same "agent harness" engine — different purpose.** Codex uses it to write code, dsh to orchestrate agents; Yona wires it to a *life sampler* so a character can act autonomously, continuously, and within a time budget — in other words, to *live*.

---

## 1. Shared mechanisms, side by side

| Mechanism | Yona | Codex / DeepSeek Harness reference |
|---|---|---|
| **append-only event log = single source of truth** | `SessionLog` (`core/session_log.py`) | dsh session event model |
| **loop with `turn/step` boundaries** | `AgentLoop` (`core/loop.py`) | dsh `step()` / Codex agent loop |
| **safe streaming accumulation** | `Assembler` | dsh streaming block accumulation |
| **schema-driven tool calling** | `ToolRegistry`, schemas injected per step | Codex / dsh tool use |
| **delete/edit = annotate, never rewrite the log** | `surface/shadow`, `replace/compact` | dsh `surface.ts`, Codex fork semantics |
| **fold = a view, not the log** | `fold_tool_traces` projection switch | the same family as agent context compression |
| **per-step token-usage anchoring** | usage anchored to `(turn, step)` | dsh's same usage anchor |
| **per-turn dynamic system assembly** | builder `(registry, source, log)` | agent prompt assembly |

> These are not "look-alike" coincidences — the design docs explicitly say "modeled on dsh session / `surface.ts` / Codex fork." It's a real, deliberate architectural alignment.

---

## 2. The one worth unpacking: surface (delete = annotate)

A chat product's "delete/undo/edit" is a natural trap: there's no database to delete from, and deleting carelessly can leave the model context with an orphaned tool message (a 400).

Yona collapses every "view operation" into **one first-class primitive**:

```
shadow(start_seq, end_seq, reason)     # shadow a range: log keeps it, projection skips it
replace(start_seq, end_seq, content)   # = shadow + a summary topper, anchored back (compact)
```

- Validation enforces **tool-pair integrity**: if the shadowed range contains an assistant's tool-call, its `tool/result` must be in the same range — otherwise projection would leave an orphan ("called a tool, no result"), which is invalid to feed a model.
- Three state paths: `current` / `shadowed` / `log-only`.

The industry peers:
- **dsh**'s `surface.ts` offers `surfaceOp(append / replace range)`, `shadowedSeqs`, `sourceEventSeqs` (lineage).
- **Codex**'s Esc-Esc fork, **Claude Code**'s checkpoint /rewind are the same family.

Yona's tradeoff: the log never changes; shadowing happens only at the "view fed to model/UI" layer; auditing = one more annotation event in the log. This is VISION's **convergence principle**: external needs must reduce to a first-class kernel primitive, or the requirement isn't thought through.

---

## 3. The difference: wiring the engine to "a life"

Where Yona genuinely parts ways with Codex/dsh is the **upper goal** — which is also its innovation:

| Dimension | Codex / dsh | Yona |
|---|---|---|
| Goal | complete a task (code / orchestrate) | sustain a **continuous life** (companion) |
| Trigger | user issues an instruction | autonomous heartbeat + user chat (dual) |
| When idle | waits | **offline backfill**: fills the empty time back in as events |
| Time sense | this one call | single time source + relative timeline + **time budget** |
| Identity | a tool / an agent | **one fixed, unique "person"** (persona written once) |
| Evaluation | task success | time-continuity / not going cold / not over-committing |

Concrete mechanism differences:

- **Time budget = a real sampled event, not a random number.** A normal autonomous turn samples the consumable window `[log tail → now]` with the same LifeSampler; `start + budget ≤ now`; no event → quiet. When it wakes and for how long is data-driven, not arbitrary.
- **One event = one thing.** The prompt stresses "this window is about X — do one thing, don't start something you can't finish." Anti-nonsense-chatter by construction.
- **A sleep window.** In the `shape(t)` daily curve, deep night is 0 — it's sleeping and doesn't judge. It lives like a person, not a 24/7 on-call machine.

---

## 4. Why developers should read this side-by-side

1. **A clean case of "same agent kernel, different service target."** To understand how Codex/dsh's primitives (session/surface/fold/step) land and decouple cleanly, every layer maps back.
2. **It demonstrates how "time" is modeled into an agent.** Most agents have no concept of a time budget or daily rhythm — Yona makes sleep schedule and offline backfill first-class.
3. **The layering discipline is instructive.** Content (copy) and mechanism (engine) are strictly separated; the core kernel doesn't import upper layers. A companionship project can still be as testable / replayable / auditable as an engineered agent.

---

## 5. Related docs

- Layering details → [ARCHITECTURE.md](./ARCHITECTURE.md)
- Underlying technical tradeoffs (internal) → repo `docs/decisions/VISION.md` (dev-facing)
- Questions → open a GitHub Issue.

---

## 简体中文导读

Yona 的"近似内核"不是巧合:设计文档明确写着对标 dsh session / `surface.ts` / Codex fork。事件源日志、turn/step 循环、工具调用、`surface`(删除=注解不改日志)、折叠视图、token 用量锚定——这些原语与 Codex / DeepSeek Harness 一脉相承。

**差异在目标**:Codex 用它写代码,Yona 把它接到生活采样器上,让它自主、有连续感、有时间预算地**过日子**。最值得对照的是 `surface`:聊天产品的"删除/撤回/编辑"没有数据库可删,Yona 收敛成 `surface/shadow` / `replace(compact)` 一个一等公民原语,日志零改动、遮蔽只在投影层、审计=多一条注解——这是把 Codex/dsh 的同一思路搬到情感陪伴场景的干净样例。
