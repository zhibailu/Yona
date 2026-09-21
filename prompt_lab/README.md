# prompt_lab —— 提示词实验台

**这是你自己上手玩的地方。** 不是测试套件,不是批量仪器 —— 那两样在 `test/`。

一眼看懂的分工:

| 你想干什么 | 去哪 |
|---|---|
| **自己动手玩,当场看效果** | **`prompt_lab/`(这里)** |
| 要**数字**,要下结论(触发率 / 自造日期率) | `test/recall_router_probe.py` |
| 跑一整轮(陪聊 / 自走 / 补写 + 拨时钟) | `turn_lab.py`(项目根) |
| 看端到端 + 判据自查 | `test/recall_e2e.py` |
| 文案与工具实现的**单一来源** | `test/recall_probe.py` |

⚠ **台上的观感 ≠ 结论。** 一次只跑一个样本,模型本来就有随机性。
台上看到"她这次没调"**不能**拿去当依据 —— 要数字就去 router 探针跑 reps。
这条是踩出来的:早期把接口 503 当成"她没调",又把分块跑的模型漂移当成变体差异,
两个假象各自骗了我一整轮(`docs/decisions/TRAPS.md` §一.9 / §一.11)。

---

## 分类

台子按**被试对象**分类,文件名 = `<类>_<对象>.py`:

| 类 | 前缀 | 说明 | 现在有 |
|---|---|---|---|
| 工具文案 | `tool_` | 一件工具的 `description` / `usage` / schema | `tool_recall.py` |
| 人设情境 | `persona_` | `PERSONAS.PERSONA` / 各轮 `SITUATION` | (还没做) |
| 轮次编排 | `loop_` | 送进一轮的段序 / 前缀 / 时间戳 | (还没做 —— 那类现在住 `turn_lab.py`) |

加新台子照这个走:一个文件、一个菜单、能换变体、能零花费预览。

---

## `tool_recall.py` —— 回忆工具的文案台

```powershell
py prompt_lab\tool_recall.py                 # 交互,菜单里敲 1~8 有预设题
py prompt_lab\tool_recall.py --solo          # 隔离档:本轮只开放 recall
py prompt_lab\tool_recall.py --view full     # 全展开(再带上 SYSTEM 与每次调用输入)
py prompt_lab\tool_recall.py --view quiet    # 只剩她的话 + 一行小结
py prompt_lab\tool_recall.py --preview       # 只看 SYSTEM,不调模型,零花费
py prompt_lab\tool_recall.py --list          # 列出全部文案变体
```

### 三档视图(菜单 `x` 循环)

| 档 | 给你什么 | 什么时候用 |
|---|---|---|
| `quiet` | 她的话 + 一行小结 | 只想快速过一遍 |
| **`tools`(默认)** | 再加 **⚙ 她产出的工具参数** + **↳ 工具返回原文(整段,不截断)** | 日常看效果 |
| `full` | 再加 SYSTEM 段拆分 + 每次 LLM 调用的输入 + 工具 SQL/命中分数 | 要查"她到底看见了什么" |

默认档长这样(真跑出来的):

```
你: 你还记得前天中午吃的什么吗

  ⚙ [1] recall 参数 → {"query": "9月16日中午我吃了什么", "scope": "life"}

  ⚙ [2] recall 参数 → {"query": "前天中午吃饭的事", "scope": "all"}

  ↳ 工具返回 [1]:
     [回忆] 「9月16日」这段时间,**你这边没留下什么记录**。
     这不是「你那天什么都没做」—— 只是没记下来。**别硬编那天做了什么**……
     另外,**前后这几天**你有下面这些 —— **不是那天的**,别当成那天的说……
     09-09 11:50 （单手撑着下巴，另一只手划着手机屏幕）……
     **只说这上面写着的。上面没写的细节就是没有 —— 别替自己补。**

  ↳ 工具返回 [2]:
     ……

  她: ……前天。我翻了翻，没翻着。那天中午吃了什么，我这边真的一点记录都没有……

  ── 她调了 recall ×2 ──
     query='9月16日中午我吃了什么'  scope='life'  (1/2)
     → empty (day_empty) · 命中 0 条 · 另附相邻 2 条(结果里明说不是那天的)
     日期「9月16日」(你问句里就有,不算自造)
     query='前天中午吃饭的事'  scope='all'  (2/2)
     → empty (day_empty) · 命中 0 条 · 另附相邻 2 条(结果里明说不是那天的)
     日期「前天」(你问句里就有,不算自造)
```

`[1]` `[2]` 是编号:她**一步里能同时声明好几个调用**(引擎并发跑,
先落 N 条 tool/call 再落 N 条 tool/result,`core/loop.py:393-414`),
不编号就看不出哪个返回对应哪个调用。

### 它接在哪

**接在真实引擎上,不是平行复刻**(与 `turn_lab.py` 同一条原则):

1. `eng._tools.register(recall)` —— 必须在 `eng._build_engine()` **之前**
   (AgentLoop 只在构造时快照 `retain_result`,`core/loop.py:84-87`)。
2. `eng._build_engine(cfg)` —— 真 composer / 真 loop / 真 llm,配置读 `data/llm.local.json`。
3. 往事段 `register` 进**引擎已经建好的** `eng._composers`,不另建 composer ——
   因为 `sys_by_source` 闭包捕获的就是那几个对象,所以台上打印的和实际发出去的
   一定是同一批段。

产品里目前**没有** recall 工具和往事段(`character/tools.py` 只有 `change_outfit`
与 `launch_subagent`),这两件是本台从 `test/recall_probe.py` 取来临时接上的。
**本台不改产品代码**,只往运行中的引擎里插。

### 换变体 = 只改两行

变体切换只改工具的 `description` 与 `usage`,别的一律不动 —— 所以两次跑出来的
差异就是那两行文案的差异。全部变体来自 `test/recall_probe.py` 的 `RECALL_VARIANTS`
(**不在这里抄一份**,抄了就会漂移)。

### 菜单

```
1..8    预设问题(每条括号里写着这条在看什么)
t       自己说一句
v       换文案变体
d       拨「现在」(虚拟钟,三处一起拨)
m       往事段开关
l       隔离档开关(摘掉/装回 launch_subagent)
x       细节开关(安静 ↔ 全展开)
p       看 SYSTEM(零花费,完整)
c       清空对话(记忆库不动)
q       退出
```

### 往事段(**挂**)与 tool result(**瞬时**)—— 两条通道,别混

用户的说法(2026-09-19):

> 如果注入的话就是 **system 内的一个位置**;但 **tool 的 result 是个瞬时产物**,
> 不拼进常驻内容,**即使 system 内也不放**。

| | 在哪 | 状态 |
|---|---|---|
| **往事段**(零件③) | SYSTEM 里的一段(`SystemSection`) | ✅ 就是"注入 → SYSTEM 一个位置"。默认挂着,`m` 或 `--no-memory` 关掉做对照 |
| **recall 的结果** | 只在当轮的工具消息里 | ✅ 已修好,见下 |

#### ✅ 检索结果的瞬时效(2026-09-19 修)

跑 `py prompt_lab\check_transient.py`(0 花费,用真引擎 + 假模型跑两轮):

```
fold_tool_traces(真引擎)   = True       ← 原来是 False,2026-09-19 改的
recall.retain_result       = False
第二轮 roles = ['system','user','assistant','user']    ← 没有 tool 消息了
第一轮 recall 的原文还在?  -> False
✅ 意图已实现:检索结果只活在当轮
```

改的是 `server/app/engine.py:777` 的 `fold_tool_traces`(`False` → `True`)。
折叠**只改投影,日志原文一个字不动**。谁保住痕迹由工具自己声明:

| 工具 | `retain_result` | 折叠后 |
|---|---|---|
| `launch_subagent` | True | 痕迹跨轮保留(一次性委派,重查不了) |
| `change_outfit` | False | 折叠(穿着在 `[当前角色状态]` 段里) |
| `recall` | False | **折叠 —— 就是那条"瞬时产物"** |

> ⚠ 顺带:`test/recall_e2e.py:257` 注释写着"recall 的 retain_result 默认 False,
> 所以上一题的检索结果**不会**留在历史里" —— 那句话在 `fold_tool_traces=False`
> 的时代**是错的**(改之前,`retain_result` 整行是空转的)。现在它对了。

### 三个刻度是一起拨的

菜单 `d` 拨「现在」时,**三处同时改**,不留两套刻度:

- `eng._clock_override["ts"]` → SYSTEM 里的 `[当前时间]`
- `log.set_time_cursor(ts)` → 真人消息进上下文带的 `[HH:MM]` 戳
- 工具的 `now_fn` → 日期抽取("前天"算成哪一天)

只拨第一处会出鬼影:实测 SYSTEM 写着 `2026-09-18 22:10`、消息戳却写真实墙钟
`09-20 18:23` —— 她会以为主人从两天后发来的。

### 花费

默认是**真产品全量工具**。问「今天天气怎么样」时她**规规矩矩不调 recall**,
但会派 `launch_subagent` 的联网工人 —— 实测那一次 **40 秒 / 18.5k token**。
不是文案错(产品本来就这么办),但你要测回忆就开 `--solo`(或敲 `l`),
本轮只开放 `recall` + `change_outfit`,`--preview` 则完全不调模型。

---

## 改文案之前先读

`test/recall_probe.py` 的 `RECALL_DESC` / `RECALL_USAGE` 上方有实测数据。要点:

**这套文案的冗余是负重的 —— 砍内容会掉性能,能删的只有重复。**

- 242 字旧版砍到 88 字:该调的格子一格没掉,但**自造日期从 1~2/25 涨到 14~17/25**。
- 能删的只有三处**真重复**(desc 里已有的 opener、schema 里一模一样的 scope 说明、
  usage 里的重复标签)。197 字定稿就是这么来的。
- **`description` 里那句边界不许挪走**:从 desc 拿掉 → 天气那格 24/25 掉到 19/25。

别凭"看着臃肿"就下手。先跑 `test/recall_router_probe.py` 拿数。
