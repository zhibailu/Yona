# 文档总索引(docs/README)

> Yona 2.0 的分层文档地图:谁放哪、读的顺序、状态标记。
> 不再把所有东西挤进一两个根文件;每个话题有自己的一份,每份标状态。
> 阅读入口 = 本文件 → 相关子目录 → 目标代码模块头部注释。

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
├─ AI-GUARDRAILS.md            · AI 边界 + 矛盾登记 + 守则(开工先读)
├─ core/README.md  server/README.md  character/README.md  每目录自己的文档
├─ docs/README.md              · 本文件(地图/图例/真值排序)
├─ docs/STRUCTURE.md           · 结构身躯(布局/取舍/UI);有过时项见 pitfalls/HISTORY
├─ docs/decisions/
│  ├─ README.md                · 设计拍板(ADR 式)规则
│  ├─ VISION.md                · 愿景 + 技术路线(原 VISION.md)
│  ├─ DESIGN.md                · 设计决策与取舍(原 DESIGN.md)
│  └─ TIMELINE.md              · 拍板时间线(原 MAP 一/二节,拍板正典)
├─ docs/tasks/
│  ├─ README.md                · 任务/进度/开放项规则
│  ├─ PROGRESS.md              · 当前状态/进度(原 MAP 三节)
│  ├─ OPEN.md                  · 开放项/待拍(原 MAP 四节)
│  └─ RULES.md                 · 协作规则(原 MAP 五节)
├─ docs/pitfalls/
│  ├─ README.md                · 踩坑记录规则
│  └─ HISTORY.md               · 踩坑史 + STRUCTURE 过时项清单
└─ docs/protocols/
   ├─ README.md                · 模块协议规则
   └─ LIFE_BACKFILL.md         · 生活补写协议(原 LIFE_BACKFILL.md)
```

> 五个旧根文档(VISION/DESIGN/STRUCTURE/MAP/LIFE_BACKFILL)已迁移入上树,git 历史
> 完整保留。MAP 原来是"进度+拍板+开放项+规则"的杂物袋,已按 § 拆到
> decisions/TIMELINE 与 tasks/{PROGRESS,OPEN,RULES}。

## 真值排序(冲突时照这个)

1. `docs/decisions/TIMELINE.md`(拍板正典)+ `server/params.py`(✅/⏳ 参数权威)
2. `character/personas.py`(内容层文案)
3. 每目录 README + 代码头部注释
4. 其它文档(含 STRUCTURE,VISION/DESIGN 是决策源)

## 每个 .py 头注释的约定

- 模块头注释写"这个文件是什么、放什么";
- 涉及产品语义的参数/开关,标 ✅(已拍)或 ⏳(待拍),不写死结论;
- 长期模块文档进 `docs/protocols/<模块>.md`,模块头链过来。

## 写新文档的规则(防再乱)

1. 一个话题一份文件,别往已有杂物袋里续写;
2. 拍板记录:必须含 **日期 · 你的原话/要点 · ✅/⏳ · 影响**;
3. 我(AI)推断的范围标"(待确认)",不冒充拍板;
4. 发现旧文自相矛盾 → 进 `docs/pitfalls/HISTORY.md`,报用户,不默默二选一。
