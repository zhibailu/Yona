# 协作规则(防跑偏)

> 来自旧 `MAP.md` 五节。AI 侧边界与矛盾登记见根目录 AI-GUARDRAILS.md。
> `MAP.md` **已不存在**(2026-09 拆成 `decisions/TIMELINE.md` + `tasks/{PROGRESS,OPEN,RULES}`)。
> 【2026-09-21 23:35 更正】

## 规则(防跑偏,2026-09 更新)

1. 每个新功能先写测试,能跑才叫完成。
2. 新代码只进 `yona-rewrite`,不碰旧 `Yona`。
3. 每完成一块,**更新 `docs/tasks/PROGRESS.md` 与 `docs/STRUCTURE.md`**,并把改动说清楚。
   【2026-09-21 23:35 更正】原文写"更新本 MAP" —— MAP 已不存在,进度是 PROGRESS.md。
4. 冻结区(sensory/objects/actions)不进内核,除非用户明确要。
5. **参数与形态不替我拍(血的教训,2026-09 加)**:执行只落"用户拍过"的值;
   未拍的一律 ⏳ 停在 params.py / 文档,不许我给默认、不许我把"沿用值"
   写成"已定"。拍板后才写 ✅ 并记日期。
6. **搬移必须逐字**:换文件/换形态时不得手打重写(曾把 persona 打成
   "16 岁女孩");用脚本移动 + 原文核对。
7. **文档记录带 `YYYY-MM-DD HH:MM`**(精确到分钟),`⏳ 未拍` / `✅ 已拍未落地` /
   `✅ 已落地` / `❌ 已否决` 四档状态分开写;新拍板时当场回旧节标作废。
   完整版见 skill `decision-doc-maintenance` 与 `AI-GUARDRAILS.md §五`。
   【2026-09-21 23:35 加】
