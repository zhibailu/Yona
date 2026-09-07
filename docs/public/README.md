# Yona · Public Docs

> Docs for users and contributors. **Internal dev decisions / pitfalls live in the other `docs/` subfolders** (`decisions/`, `tasks/`, `pitfalls/`, …); this `docs/public/` layer is separate and clean to read outward.

[简体中文导读](#简体中文导读)

---

## Start here

- **[Home / README](../README.md)** — positioning, features, quick start.
- **[Architecture](./ARCHITECTURE.md)** — how it "lives": event-sourced log, agent loop, life sampler, time model, layering.
- **[The Codex / DeepSeek-Harness lineage](./COMPARISON.md)** — which mechanisms are shared and which differ.
- **[Roadmap](./ROADMAP.md)** — done / in progress / backlog / non-goals.
- **[FAQ](./FAQ.md)** — usage, concepts, license, troubleshooting.

## Contributing

- Feature requests / bugs → GitHub Issue.
- Read the code starting from the layering diagram in [`ARCHITECTURE.md`](./ARCHITECTURE.md).
- Before changing behavior, respect the layering discipline: content copy lives in `character/`, the single source of product params is `server/params.py`, and the pure core in `core/` never imports upper layers. Maintainer rules: repo dev-layer `docs/README.md`.

---

## 简体中文导读

这里是**公开文档**(使用者/贡献者向),与同目录下的开发层文档(decisions/tasks/pitfalls)分开。

- 定位与技术内核同源分析 → 根 [README](../README.md) 与 [COMPARISON.md](./COMPARISON.md)
- 机制/时间模型/分层 → [ARCHITECTURE.md](./ARCHITECTURE.md)
- 进度与开放项 → [ROADMAP.md](./ROADMAP.md)
- 使用/许可/排错 → [FAQ.md](./FAQ.md)
