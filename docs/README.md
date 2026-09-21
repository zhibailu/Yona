# 文档总索引(docs/README)

> Yona 2.0 的分层文档地图:谁放哪、读的顺序、状态标记。
> 不再把所有东西挤进一两个根文件;每个话题有自己的一份,每份标状态。
> 阅读入口 = 本文件 → 相关子目录 → 目标代码模块头部注释。
> **最近核对 2026-09-21 23:40。**

---

## 状态图例(全文统一)

| 标记 | 含义 | 用法 |
|---|---|---|
| ✅ | 用户拍板(带日期) | 事实,可依赖 |
| ⏳ | 待拍(未定) | **禁止给默认 / 禁止当已定使用** |
| ⏸ | 延后 / 冻结 | 暂不做,留入口或注释 |
| ❌ | 否决(弃稿) | 别再用 |
| 🔧 | 开发/演示参数,非产品语义 | params.py 专用 |

## 文档分层(最终形态,2026-09 重排完成)

```
yona-rewrite/
├─ AI-GUARDRAILS.md            · AI 边界 + 矛盾登记 + 守则 + 文档守则(开工先读)
├─ core/README.md  server/README.md  character/README.md  prompt_lab/README.md
├─ turn_lab.py                 · 跑一整轮的实验台(陪聊/自走/补写回放 + 拨时钟)
├─ docs/README.md              · 本文件(地图/图例/真值排序/写文档的规则)
├─ docs/STRUCTURE.md           · 结构身躯(布局/取舍/UI);过时项见 pitfalls/HISTORY §五
├─ docs/decisions/
│  ├─ README.md                · 设计拍板(ADR 式)规则 + **本目录效力排序表**
│  ├─ TIMELINE.md              · 拍板时间线(拍板正典;**文首有目录**)
│  ├─ VISION.md                · 愿景 + 技术路线
│  ├─ DESIGN.md                · 设计决策与取舍
│  ├─ RECALL_MVP.md            · 记忆检索 MVP 的**实验记录/基线**(不是拍板)
│  └─ TRAPS.md                 · 实验台上的坑(含对实验结论的复核)
├─ docs/tasks/
│  ├─ README.md                · 任务/进度/开放项规则
│  ├─ PROGRESS.md              · 当前状态/进度 + 分阶段落地史
│  ├─ OPEN.md                  · 开放项/待拍(参数级 ⏳ 的汇总入口)
│  └─ RULES.md                 · 协作规则
├─ docs/pitfalls/
│  ├─ README.md                · 踩坑记录规则
│  └─ HISTORY.md               · 踩坑史 + STRUCTURE 过时项清单(§五)
├─ docs/protocols/
│  ├─ README.md                · 模块协议规则
│  ├─ LIFE_BACKFILL.md         · 生活补写协议
│  ├─ SUBAGENT.md              · 子代理协议(工人身份的 loop 复用)
│  └─ TOOL_VISIBILITY.md       · 工具可见性协议(谁在什么轮次看得见哪些工具)
└─ docs/public/                · **对外文档**(给使用者/贡献者看的英文层)
   ├─ README.md  ARCHITECTURE.md  COMPARISON.md  FAQ.md  ROADMAP.md
```

> 【2026-09-21 23:40 补登记】`docs/public/`(5 份)、`decisions/RECALL_MVP.md`、
> `decisions/TRAPS.md`、`prompt_lab/` **以前没在这张地图里** —— 按本页自己的规则
> "新目录/文件不登记就等于不存在",已补上。
> 五个旧根文档(VISION/DESIGN/STRUCTURE/MAP/LIFE_BACKFILL)已迁移入上树,git 历史
> 完整保留。MAP 原来是"进度+拍板+开放项+规则"的杂物袋,已按 § 拆到
> decisions/TIMELINE 与 tasks/{PROGRESS,OPEN,RULES}。

## 真值排序(冲突时照这个)

1. `docs/decisions/TIMELINE.md`(拍板正典)+ `server/params.py`(✅/⏳ 参数权威)
2. `character/personas.py`(内容层文案,**系统口吻区**)+ `character/tools.py`
   (每个工具的 `description` / `usage` —— 也是内容层,别漏)
3. 每目录 README + 代码头部注释
4. 其它文档(含 STRUCTURE;VISION/DESIGN 是决策源)

> **代码 > 文档。** 文档说 A、代码是 B → **先报用户**,再判断哪边是笔误。
> `docs/public/` 是**对外复述层**,是上面前三层的下游 —— 它和 dev 层冲突时,
> **以 dev 层(1~3)为准**,并回头修 public。
> `decisions/RECALL_MVP.md` / `TRAPS.md` 是**实验记录,不是拍板**;引用它们必须标
> **批次与 reps**,并注明是实测值还是拍板值(效力排序见 `decisions/README.md`)。

## 每个 .py 头注释的约定

- 模块头注释写"这个文件是什么、放什么";
- 涉及产品语义的参数/开关,标 ✅(已拍)或 ⏳(待拍),不写死结论;
- 长期模块文档进 `docs/protocols/<模块>.md`,模块头链过来。

## 写新文档的规则(防再乱)

> **完整版见 skill `decision-doc-maintenance` 与 `AI-GUARDRAILS.md §五`。**
> 【2026-09-21 23:40 补】下面第 2 条以前只要求"带日期"—— **不够**。同一天可能拍板
> 两次并互相推翻(2026-09-21 就发生了:"往事折成 SYSTEM 一段"上午定、下午推翻),
> **只有日期时两条记录排不出序**,而"以更晚的那条为准"正靠排序生效。

1. 一个话题一份文件,别往已有杂物袋里续写;新文件要在本页的目录树里**登记**。
2. **每条记录带 `YYYY-MM-DD HH:MM`(精确到分钟)** · 你的原话/要点 · ✅/⏳ ·
   影响。状态词**四档分开写**:`⏳ 未拍` / `✅ 已拍未落地` / `✅ 已落地` / `❌ 已否决`
   —— 把"已拍未落地"写成"未决",下一个人还会再去讨论一遍。
3. 我(AI)推断的范围标"(待确认)",不冒充拍板。
4. **新拍板时当场回旧节标作废**(按话题全库扫,不是只标自己记得的那几句);
   发现旧文自相矛盾 → **立刻报用户**,默认**以更晚的那条为准**,
   同时进 `docs/pitfalls/HISTORY.md` —— 不默默二选一。
5. 引用任何源码取值(枚举 / 字段名 / 常量 / 函数签名 / 行号)前**先 grep 到定义**;
   行号会漂,引用时顺手核一遍。
6. 单个文件超过约 800 行就该拆;一个目录超过约 8 个文件说明分类粒度不对。
