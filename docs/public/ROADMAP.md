# Roadmap

> Public roadmap. Status: ✅ done · 🔶 in progress · ⏳ backlog (documented decision, deliberately not prioritized).
> A local single-machine project — the priority is making the **autonomous-companion kernel loop** solid, not piling on features.

---

## 1. Kernel loop already closed (✅)

| Capability | Status | Notes |
|---|---|---|
| Unified agent loop | ✅ | `AgentLoop`: streaming + tool calling, `user`/`self` dual triggers |
| Event-sourced session log | ✅ | append-only + surface annotations, replay/recover/audit |
| Autonomous life loop | ✅ | heartbeat wake → life gate → only then let the model decide |
| Life sampler | ✅ | `LifeSampler`, `rate = K × shape`, grows events out of a daily curve |
| Offline-life backfill | ✅ | gap over threshold → fill the days back into the card |
| Per-card life | ✅ | life lives on the session you're talking to, not one global stream |
| surface governance | ✅ | delete/edit = annotation, never log rewrite, incl. tool-pair validation |
| Observability | ✅ | transcript / inner-thought feed / llm-log (SSE live) / foldable tool traces |
| Model-connection management | ✅ | UI wizard pulls models, hot-swap without restart; OpenAI-compatible endpoints |
| Multi-session + per-session snapshot | ✅ | temperature / context rounds / model / persona override per session |

## 2. In progress / polish (🔶)

- **Vertical-scenario tuning**: how solitude vs. backfill situation copy reads, and the autonomous-turn SYSTEM layout — to be validated with real models across times/situations, not decided on paper.
- **Final sign-off on the life-sampling shape curve** (the evening-peak version is the current candidate).
- Making the inner-thought panel / llm-log observability more convenient in the UI.

## 3. Backlog (⏳ deliberately not prioritized)

All have documented decisions, but **no old-glue porting** and they do not compete with closing the kernel loop:

- **RAG long-term memory**: vector recall → rerank, so it remembers further back than the last-N-rounds context.
- **Character preset packs** (agent-presets): change characters without changing code. cf. dsh agent-presets.
- **Voice & senses**: ASR / TTS / vision.
- **Eval**: an automated system for measuring "companionship quality."

> For each item's tradeoff and "why not now," see the dev-layer `docs/decisions/` and `docs/STRUCTURE.md` (maintainer docs).

## 4. Non-goals (explicitly out of scope, or left for the future)

- **Multi-tenant SaaS**: local-first single machine; isolation/auth wait for the "publish to others" stage.
- **Content moderation**: research/personal project; follow the model vendor's policy.
- **Big-bang feature pile**: no attempt to ship RAG + voice + presets at once — that was precisely the old project's disease.

---

## 简体中文导读

公开版路线。**已实现内核闭环**:统一 agent 循环、事件源会话日志、自主生活循环、LifeSampler、离线补写、每卡 life、surface 治理、可观测性、模型连接管理、多会话快照——全部 ✅。

**待启动轨道(刻意不优先,有决策记录但不移植旧胶水)**:RAG 长期记忆、角色预设包(不改代码换角色)、语音感官(ASR/TTS/vision)、评测 eval。

**非目标**:多租户 SaaS、内容审查、大而全。单机本地、研究/个人向优先把"自主陪伴"这条内核闭环做扎实。
