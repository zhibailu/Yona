# character/ —— 旗舰角色层(目录自己的文档)

> 小夜子:是谁(文案)、段工厂、状态、工具。依赖 core;不 import server。
> **核心分工**:`personas.py`(内容层文案)与 `persona.py`(段工厂)分开;
> engine 把文案喂给工厂建 composer —— 文案在哪、怎么拼,职责各自清楚。

## 文件

| 文件 | 职责 |
|---|---|
| `personas.py` | **内容层文案**(用户主战场):PERSONA(她是谁)+ SITUATION(轮的处境)+ VALUES + 系统口吻(LIFE_EVENT_PREFIX / WAKE_BUDGET_TEMPLATE)。**改人设/台词只改这里** |
| `persona.py` | 段工厂:把文案拼成 SystemSection(persona/world/state/usage);不写死文案 |
| `state.py` | CharacterState:状态投影(`state.project()` 每轮注入 SYSTEM) |
| `tools.py` | 角色工具(change_outfit 等) |

## 本地拍板 / 边界

- **PERSONA = 唯一人设**,所有轮(陪聊/自走/补写)共用同一份;不许每轮重写身份
  (曾三份人格互相矛盾 → 已归位)。
- **情境是轮的属性,不是人设**:`CHAT_SITUATION`(陪聊)与 `SELF_SITUATION`(独处)。
  **补写轮没有自己的情境** —— 复用 `SELF_SITUATION`(补写 = 自走的离线回放)。
  想改"她独处时怎么说",只改 `SELF_SITUATION` 一处。
- 系统口吻也放这(`LIFE_EVENT_PREFIX`、`WAKE_BUDGET_TEMPLATE`):引擎只填数据,
  不写句子(曾把 [时间预算] 句子写死在 engine → 归位)。
- personas.py 是**用户在编辑的活文件**:AI 改内容前先看清楚别覆盖用户的未提交
  改写;提交按"用户的改动单独留给他"处理。

## 角色自身变量的工具(`change_outfit` 一族)

**用户 2026-09-21 的定义**:`change_outfit` = 对角色**自身变量**直接更改的**常驻**工具;
穿着、健康状况、世界信息、好感度系统、用户画像都在这一个工具内**按变量路由**。

**它为什么必须留在她手里**:归属轴(见 `docs/protocols/SUBAGENT.md` §3.3)—— 改自身变量的
工具只能是她自己的能力,工人无人格无记忆,拿不走。这条同时意味着:
**它内部的"路由"是按「改哪个变量」,不是按「该轮到哪个工具干活」** ——
前者变量全归它所有,没事;后者是跨工具耦合,**禁**。

**两条硬约束**(改这一族之前先看):

1. **凡进这个工具的变量,必须在某个 SYSTEM 段里有投影,否则跨轮不可见。**
   因为 `fold_tool_traces=True` + `retain_result=False`(`server/app/engine.py:777-787`),
   已结束轮的工具痕迹会被折掉。穿着之所以没事,只因为 `state.project()`
   (`state.py:42`)把它投影进了 `[当前角色状态]`。**好感度、健康同样必须有投影。**
2. **一个字段付两遍钱。** `project()` 拼的是**所有注册字段**、每轮重发进 SYSTEM;
   而 `mutable_fields` 又是 `change_outfit` 的属性名来源(`tools.py:133/143`)。
   字段一多,schema 变长 **+** 每轮 SYSTEM 变长,两处一起涨。

**⏳ 未拍(别当成设计)**:

- **健康 / 好感度是连续量**,而现在的形状是"模型直接给新值"。谁保证单调、
  谁保证不被一次对话洗掉 —— 那是**状态机**的事,不是文案的事。
- **用户画像**是长文:走 `dict[str, str]` 的 `好感度=72` 那条路会很难看,
  更像"另一张表 + 一段投影",不是一个标量字段。
- **世界信息**:它不是她的状态 —— 世界与角色的 owner 不同,要不要并进这一族 ⏳。

## 踩坑史(详见 docs/pitfalls/)

- 身份句曾多处复制且互相矛盾(21 岁 vs AI 伴侣、16 岁笔误)→ 教训:文案搬移必须逐字。
- 情境曾 SELF/BACKFILL 两份 → 漂移 → 删 BACKFILL。
