# character/ —— 旗舰角色层(目录自己的文档)

> 小夜子:是谁(文案)、段工厂、状态、工具。依赖 core;不 import server。
> **核心分工**:`personas.py`(内容层文案)与 `persona.py`(段工厂)分开;
> engine 把文案喂给工厂建 composer —— 文案在哪、怎么拼,职责各自清楚。

## 文件

| 文件 | 职责 |
|---|---|
| `personas.py` | **内容层文案**(用户主战场):PERSONA(她是谁)+ SITUATION(轮的处境)+ VALUES + 系统口吻(SELF_TALK_PREFIX / WAKE_BUDGET_TEMPLATE)。**改人设/台词只改这里** |
| `persona.py` | 段工厂:把文案拼成 SystemSection(persona/world/state/usage);不写死文案 |
| `state.py` | CharacterState:状态投影(`state.project()` 每轮注入 SYSTEM) |
| `tools.py` | 角色工具(change_outfit 等) |

## 本地拍板 / 边界

- **PERSONA = 唯一人设**,所有轮(陪聊/自走/补写)共用同一份;不许每轮重写身份
  (曾三份人格互相矛盾 → 已归位)。
- **情境是轮的属性,不是人设**:`CHAT_SITUATION`(陪聊)与 `SELF_SITUATION`(独处)。
  **补写轮没有自己的情境** —— 复用 `SELF_SITUATION`(补写 = 自走的离线回放)。
  想改"她独处时怎么说",只改 `SELF_SITUATION` 一处。
- 系统口吻也放这(`SELF_TALK_PREFIX`、`WAKE_BUDGET_TEMPLATE`):引擎只填数据,
  不写句子(曾把 [时间预算] 句子写死在 engine → 归位)。
- personas.py 是**用户在编辑的活文件**:AI 改内容前先看清楚别覆盖用户的未提交
  改写;提交按"用户的改动单独留给他"处理。

## 踩坑史(详见 docs/pitfalls/)

- 身份句曾多处复制且互相矛盾(21 岁 vs AI 伴侣、16 岁笔误)→ 教训:文案搬移必须逐字。
- 情境曾 SELF/BACKFILL 两份 → 漂移 → 删 BACKFILL。
