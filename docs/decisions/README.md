# docs/decisions —— 设计拍板(ADR 式)

> 放"用户拍过的设计决定",一条一个文件或表格一条,含:**日期 · 你的原话/要点 ·
> ✅/⏳ · 影响范围**。
>
> **拍板正典 = `docs/decisions/TIMELINE.md`**(从旧 MAP 一/二节拆出)。
> VISION.md(愿景路线)/ DESIGN.md(设计决策与取舍)是更早的决策源。
> 新拍板:直接在 TIMELINE 追加,或独立成文放本目录。
>
> **本目录层级与效力**
>
> | 文件 | 地位 | 冲突时的效力 |
> |---|---|---|
> | TIMELINE.md | 拍板正典 | **最高** |
> | DESIGN.md / VISION.md | 设计展开层 | 低于 TIMELINE |
> | RECALL_MVP.md | **实验记录 / 基线**(不是拍板) | 与代码或 TIMELINE 冲突时,一律**以 TIMELINE + 代码为准** |
> | TRAPS.md | 踩坑史(含对实验结论的复核) | **复核结论优先于被复核的旧结论** |
>
> 【2026-09-21 23:30 补】原页只登记了 3 个文件,把 `RECALL_MVP.md` / `TRAPS.md` 漏在层级之外 —— 真相:本目录 = DESIGN.md / README.md / RECALL_MVP.md / TIMELINE.md / TRAPS.md / VISION.md;这两个文件正在跟 TIMELINE 及**彼此**矛盾(例:`RECALL_MVP.md` 说"地板 0.25 挡着",代码里其实没接线;`RECALL_MVP.md` 说洞 A "0/35 已补",`TRAPS.md` 判定那个 0 是**空数**),读者找不到裁决依据。
>
> 规则:
> - 只记用户**原话/明确拍过的**;AI 推断的标"(待确认)"。(实验 / 跑批得出的结论另立 `RECALL_MVP.md` / `TRAPS.md` 这类文件,**不算拍板**;引用时必须标**批次与 reps**,并注明是**实测值**还是**拍板值**。本目录的效力排序见上表。)
> 【2026-09-21 23:30 更正】原规则与本目录文件内容不符(`RECALL_MVP.md` / `TRAPS.md` 的主体是 AI 跑实验得出的结论,既非用户原话、也没标"(待确认)"),于是那些实验数字被当拍板值引用 —— 真相:`RECALL_MVP.md:3-6` 自称"这份文档是给你通读和批注用的,不是历史记录"。
> - 与代码行为的偏差(文档写 A 代码做 B)= bug,进 docs/pitfalls/,不在这打补丁。
