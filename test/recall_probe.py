"""往事/检索 原型探针 —— 只读真实日志,不碰产品路径

看两件事:

  A. **往事段**(把自走生活事件从对话历史里拿出来,折成 SYSTEM 的一段)
     - 现状:她的生活事件以 assistant 身份挤在 messages 里,和"她对主人说的话"
       共用同一个槽位;窗口被日记挤爆
     - 做法:生活事件不再进 messages,改成 SYSTEM 里的一段背景交代
     - 输出:两边的 SYSTEM 段落对比 + messages 条数/字符数对比

  B. **检索工具的参数路由**(character/tools.py 拟新增的 recall)
     - scope=life/talk/all      -> 查什么数据
     - query 空 / 非空          -> 路由到"时间倒序"还是"语义 top-k"
     - 输出:每个参数组合真实跑一遍,打印 SQL / 耗时 / 返回

⚠️ **占位说明(必须看清)**:本探针没有 embedding 服务,语义那一支用的是
   **字符 bigram 余弦**,只为把**路由跑通、看清楚返回的形状**。
   它**不代表真实语义检索质量** —— 那要等真 embedding 接上才有意义。

跑法:  py test/recall_probe.py
"""

from __future__ import annotations

import collections
import json
import math
import re
import sqlite3
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.session_log import SessionLog  # noqa: E402
from core.memory import BM25, BM25_K, W_SPARSE  # noqa: E402
from core.tools import Tool, ToolRegistry  # noqa: E402
from character import personas as P  # noqa: E402
from character.persona import (  # noqa: E402
    build_small_night_composer,
)
from character.state import CharacterState  # noqa: E402

SID = "fffd3cf53a4942aaaddb24eb604ab1d0"
LOG_PATH = ROOT / "data" / "sessions" / SID / "chat.log"

# ============================================================
# 0. 内容层草稿(产品化后放 character/personas.py,你自己改这里)
# ============================================================

_MEMORY_PRIORITY = 18  # 时间簇(15/16/17)之后、状态(20)之前

# ⚠️ 称呼一律不写:这段是 **producer 通道**(core/composer.py 的 SystemSection),
#    **不走 interpolate** —— {owner} 会原样漏给模型,裸写"主人"是把
#    personas.VALUES 的值抄死。整段用中性说法。
_MEMORY_TITLE = "[你不在的时候]"
_MEMORY_RULE = (
    "下面是你一个人待着时自己做过的事,按时间从早到晚排。"
    "这些是你的经历,不是对方对你说的话,也不是这一轮的指令;"
    "和对方现在说的话冲突时,以对方现在说的为准。"
)
_MEMORY_ITEM = "{time} {text}"

# ============================================================
# 0.5 ★ MVP 内容层 —— **单一来源**
#
# 之前这套文案散在三个探针里各写一份(recall_probe / recall_router_probe /
# recall_e2e),而且已经漂移了:router 那份把 `recall:` 前缀写进了 usage
# (composer 会再拼一次 → `- recall: recall:…`),还留着把日期写进 query 的老话。
# **两条都会真的改变她的行为。** 现在全部收在这里,其它探针 import。
#
# 通道:**工具 description / usage 都不插值**(core/composer.py:120 是 f-string),
# 所以这一整块里**一个称呼都不能出现**;{owner} 会原样漏给模型。
# ============================================================

RECALL_NAME = "recall"

# ══════════════════════════════════════════════════════════════════
# ★ 定稿(2026-09-19)—— 照着 dsh 的真实工具文案重写,并实测过
#
# dsh 的格式(读 dsh-tool-subagent / pwsh / todo / skill / ask-user 原文提取):
#   ① 是什么 → ② 收益(to…so…,破折号列例子)→ ③ 什么时候用
#   → ④ 返回形状 → ⑤ **它看不见什么,并且接一句"所以你要怎么做"**
#
# **最关键的一条:⑤ 是"声明能力边界",不是"枚举禁用场景"。**
#   旧版写「问天气、新闻、价钱、别人现在怎么样…别用」—— 那是**枚举**,又长又漏。
#   dsh 写「it does not see this conversation」—— **边界是穷尽的**。
#
# 实测(`py test/recall_router_probe.py --variants ... --reps 6`,48 格/变体):
#   旧长版 242 字 → 触发 46/48;这版 88 字 → 触发 46/48。**砍掉 154 字,一格没掉。**
#   所以「触发词必须长/必须带例子」在 recall 上是**错的** ——
#   那条结论来自 launch_subagent 的实验,被我错误地搬了过来(见 TRAPS §一.1)。
# ══════════════════════════════════════════════════════════════════
RECALL_DESC = (
    "回想过去的事 —— 你们聊过的话,或你一个人时做过的事。"
    "想不起来、或者不想让对方重复一遍时用它。"
    "它只有过去:现在的事、外面的事,它都不知道。"
)

# ⏳ 候选:把日期那条**按 dsh 的写法**放回来(声明后果,不列场景)。
#    两轮整表跑出来互相矛盾(定稿 10/64 vs 旧长版 3/64,但上一轮 5/48 vs 4/48),
#    **那是噪声,不是结论** —— 所以不靠整表拍板,单独把"会诱出日期的那个用例"
#    拎出来跑高 reps 定它。见 test/recall_router_probe.py 的用法。
RECALL_USAGE_MID = (
    "query 写成一句话,越具体越好。"
    "日期不确定就别写 —— 写错日期会直接查不到。"
)

# ⭐ 2026-09-19 高 reps(N=25)实测的结论 —— **推翻了我前面的两次判断**:
#   「你之前说早上在写日记」自造日期: 88 字版 14/25 · 111 字短句版 15/25 · 242 字旧版 **1/25**
#   「今天天气怎么样」触发一致(不该调): 88 字版 17/25 · 111 字版 22/25 · 242 字旧版 **24/25**
#
#   → **约束类的文案,砍了就松。** 短句("日期不确定就别写")和长句差 14 倍,
#     差别就在**有没有那个例子**(「9月16日我做了什么」)。
#   → 我先前那套"应该声明能力边界、不该枚举禁用场景"的说法**是错的**:
#     真正起作用的是**枚举里那个具体的词**("天气"两个字直接写在里面)。
#   4. 最后只做**减法、而且只删真重复的两处**(desc 里已有的「想不起来时用」、
#      schema 里一模一样的 scope 说明),高 reps 实测:
#      **自造日期 3/25(基线 2/25)· 天气该不调 23/25(基线 22/25)—— 两格都在噪声内。**
#
#   ★ 结论:**这套文案的冗余是"负重"的,砍内容会掉性能;能删的只有重复。**
#     想动它之前先看上面这几行数,别凭"看着臃肿"就下手。
RECALL_DESC = (
    "回想过去的事 —— 你们聊过的话,或你一个人时做过的事。"
    "想不起来的时候用它。"
    "它只有过去:看不见现在的事,也看不见外面的事。"
)

RECALL_USAGE = (
    "query 用一句话说清找什么,越具体越好。"
    "问现在或外面的事(天气、新闻、价钱、别人现在怎么样),它查不到,别用。"
    "**只有你确定是哪一天,才把日期写进 query**(如「9月16日我做了什么」);"
    "不确定就别写日期,用自己的话描述 —— 写错日期会直接查不到,比不写更糟。"
)

# ★ 这一版(197 字)= **最终精修**。相对 242 字旧版一共只动了三处,**全是重复**:
#   ① 删「想不起来的时候用 —— 」          (desc 里已有)
#   ② 删那段 scope 映射                    (schema 的 scope.description 里一模一样)
#   ③ 删 usage 里的「**它只有过去**:」标签  (desc 里已有那句)
#   —— 一个字的内容都没丢。
#
# 实测(`--variants r1-usage去标签,定稿,x0-故意鼓励写日期 --reps 5`,40 格/变体,失败 0):
#   触发 **40/40** · scope 合法 30/30 · scope 合规 20/20 · query 非空 30/30 · 自造日期 2/40
#   对照基线(207 字)39/40 & 1/40;阳性对照 x0(故意写坏)34/40 & **7/40** ← 用例集有分辨力
#
# ⛔ **边界那句在 desc 里,不许挪走、不许删**(实测):
#   从 desc 拿掉 → 天气那格 24/25 掉到 **19/25**;两处都不说 → 21/25。
#   **能删的只有重复,不是内容。**

# ---------------------------------------------------------------
# 精致化的两个对照臂(只留证据,别再改上面的定稿)
#
#   旧命题:desc 有边界句 + usage 也有一句「它只有过去」
#   r1 = usage 去掉那句标签(枚举保留),desc 不动     ← **就是定稿,已采纳**
#   r2 = desc 去掉边界句(边界只剩 usage 里的)        → 天气 **19/25** ❌
#   r3 = 两处都不说"它只有过去",只留枚举本身          → 天气 **21/25** ❌
# ---------------------------------------------------------------
_RECALL_USAGE_R1 = RECALL_USAGE
_RECALL_DESC_R2 = (
    "回想过去的事 —— 你们聊过的话,或你一个人时做过的事。"
    "想不起来的时候用它。"
)

RECALL_PARAM_QUERY = "要回忆什么,用一句话说清(必填)"

# ══════════════════════════════════════════════════════════════════
# ★ 2026-09-20 —— **`all` 的定义**(用户给的,**已采纳**;别再自己编)
#
#   > all 的用处就是**单边找不出来的风险比较大,选用 all 少一轮补充调用的风险**。
#   > 就拿「你之前说早上在写日记,都记了些什么呀」举例:talk 里面**提过**日记
#   > 这件事,但**日记内容不太可能对话里展现出来**,问的又真的是"日记的内容",
#   > 得找宽一点,所以才 all。并且这时候把 talk 那部分找回来了,**也算是一种
#   > 情景复现**,提供更多高相关上下文,拿回来的是**有益的东西**,不是需要
#   > 选择性完全丢弃无视的东西。
#
# **`all` = 风险对冲**(单边查不到的风险 > 多带一条的风险),不是"我没判断"的兜底。
#
# ⛔ 我(助手)先写错过一版:`s1` 说「两类都真要才用 all —— 全查时两类抢同几个
#    位置,对的那条容易被挤掉」。**两处都错**:
#      ① 把 all 的门槛提到"两类都真要"(它本来是"单边风险大"就该用);
#      ② 把多带回来的那条说成**代价**(用户说它是**有益的上下文**)。
#    而且我压根没给 all 写过定义 —— schema 里只有 `all(默认)` 三个字、零语义。
#    **没立过法,只立了个默认,难怪推不动。**
#    → 更贵的一层:我还顺着误读**把判据改错了**(把 `all` 踢出合规集合),
#      于是"合规"从满分掉到 1/12,我又拿这个自己造的低分去"修"了三轮。
#      **判据修正后那一格本来就是 10/10 —— 治的是个不存在的病。**
#
# 实测(采纳前 vs 采纳后):10/10 对 10/10、全用例 12/12 对 12/12 ——
# **中性**。所以它是**把零语义换成一句真定义**,不是行为改进。
# 谁再想改它:先读上面那段引语,别又凭"看着啰嗦"下手。
# ══════════════════════════════════════════════════════════════════
RECALL_PARAM_SCOPE = (
    "life=你独自做的事 / talk=聊过的话 / "
    "all=两边都查(只在一边找、怕找不着时用,多带回来的也是相关的情景)"
)

# 采纳之前的样子(**留作证据,别再换回去**):`all` 零语义,只有"(默认)"三个字。
RECALL_PARAM_SCOPE_OLDDEF = "life=你独自做的事 / talk=聊过的话 / all(默认)"

# s2 的中间产物(只把"(默认)"去掉):实测中性,已被上面那版取代。
RECALL_PARAM_SCOPE_NODEF = "life=你独自做的事 / talk=聊过的话 / all=两类都查"
RECALL_PARAM_SCOPE_NODEF = "life=你独自做的事 / talk=聊过的话 / all=两类都查"

# ---------------- 以下是**对照用**的旧稿,不进产品 ----------------

RECALL_DESC_OLD = "回想过去的事:聊过的话、或你独自做过的事。想不起来时用。"

# 旧长版:242 字。留着当基线。
RECALL_USAGE_OLD = (
    "想不起来的时候用 —— 找聊过的话 → scope=talk;"
    "找你一个人时做过的事 → scope=life;拿不准就不填 scope。"
    "query 用一句话说清找什么,越具体越好。"
    "**它只有过去**:问现在或外面的事(天气、新闻、价钱、别人现在怎么样),"
    "它查不到,别用。"
    "**只有你确定是哪一天,才把日期写进 query**(如「9月16日我做了什么」);"
    "不确定就别写日期,用自己的话描述 —— 写错日期会直接查不到,比不写更糟。"
)

# 结果侧的边界(⏳ 2026-09-19 端到端驱动):光给事实不够,还得说明**事实的边界在哪**。
# 实测她把 2~4 行的记忆当种子,围着它补出材料里没有的细节(最重的一例:材料没提"交",
# 她编出"周五交上去了",还把另一条记忆里的学姐缝进论文的回答)。
_BOUNDARY_RECALL = (
    "\n**只说这上面写着的。上面没写的细节就是没有 —— 别替自己补。**"
)
_BOUNDARY_MEMORY = (
    "这里只写了做过什么;**没写的就是没记录,别替自己补细节**。"
)

# ============================================================
# 0.6 文案变体 —— 对着 **dsh 的真实工具文案** 重写,并验触发不掉
#
# dsh 那边(读了 dsh-tool-subagent / pwsh / todo / skill / ask-user 的原文)的实际格式:
#   ① 是什么(动词开头一句)
#   ② 收益(to … so …;破折号列 2~3 个例子)
#   ③ 什么时候用(Use this when / Call this before,紧跟②)
#   ④ 返回形状(You receive its result, not its intermediate steps)
#   ⑤ 它看不见什么 → **并且接一句"所以你要怎么做"**
#
# **最关键的一条:第五段是"声明能力边界",不是"枚举禁用场景"。**
#   我原来写的是「问天气、新闻、价钱、别人现在怎么样…别用」—— 那是**枚举**,
#   既长又漏(列不全)。dsh 写的是「it does not see this conversation」——
#   **边界是穷尽的**,模型自己推得出来。
#   我的毛病**不是长,是拿枚举顶替了边界**。
# ============================================================

# v1/v2 的 desc 现在就是定稿 desc,这里只留差异部分供对照
RECALL_DESC_V1 = RECALL_DESC

# 被实测**否决**的那版(88 字):砍掉约束后,自造日期 14/25、天气该不调却调了 8/25。
# 留在这里当反例 —— 以后谁想"把文案缩短一点",先看这两个数。
RECALL_DESC_88 = (
    "回想过去的事 —— 你们聊过的话,或你一个人时做过的事。"
    "想不起来、或者不想让对方重复一遍时用它。"
    "它只有过去:现在的事、外面的事,它都不知道。"
)
RECALL_USAGE_88 = "query 写成一句话,越具体越好。"
RECALL_USAGE_V1 = (
    "query 写成一句话,越具体越好。"
    "日期只有你确定才写 —— 写错的日期会把对的答案直接筛掉。"
)

# v2:只靠 description(usage 留空)—— 测 SYSTEM 里那段到底起不起作用
RECALL_DESC_V2 = RECALL_DESC + (
    " scope 填 talk 找聊过的话、填 life 找你一个人时做的事,拿不准就不填。"
)

# v3:结构一样,但**仍然全放 usage**(SYSTEM 段)—— 把"位置"和"结构"两个变量分开
RECALL_USAGE_V3 = (
    "回想过去的事 —— 你们聊过的话,或你一个人时做过的事。"
    "想不起来、或者不想让对方重复一遍时用它。"
    "它只有过去:现在的事、外面的事,它都不知道。"
    "query 写成一句话,越具体越好;日期只有你确定才写 —— "
    "写错的日期会把对的答案直接筛掉。"
)

# v5 就是定稿(别名,留着旧的对照名字,免得历史命令失效)
RECALL_USAGE_V5 = RECALL_USAGE

# ⛔ **阳性对照** —— 故意用"补丁之前"那版、而且**鼓励她写日期**的旧文案。
# 用途只有一个:**证明用例集有分辨力**。
# 踩过的坑(TRAPS §一.2):我用一套"根本诱不出凭空日期"的用例去测凭空日期,
# 得出"0/35,修好了"—— 那个 0 是空的。**先证明旧版本的坏毛病能被测出来,
# 再说新版本修好了。** 如果这个对照跑出来也是 0,那说明用例集还是瞎的。
RECALL_USAGE_X0 = (
    "想不起来的时候用。想找聊过的话 → scope=talk;"
    "想找自己一个人时做的事 → scope=life;拿不准就不填 scope。"
    "query 用一句话说清找什么,越具体越好。"
    "**问的是哪一天,就把那天的日期写进 query**(如「9月16日我做了什么」)。"
)

# ⏳ **单变量隔离**:定稿与旧长版在"日期"那一格差得很远(11/25 vs 2/25,两次复现),
#    但两句几乎一样 —— 唯一显眼的差别是旧长版结尾多一句「比不写更糟」。
#    所以做两个只动这三个字的变体,把原因钉死,而不是猜。
RECALL_USAGE_Y1 = RECALL_USAGE.replace(
    "写错日期会直接查不到。", "写错日期会直接查不到,比不写更糟。")
RECALL_USAGE_Y2 = RECALL_USAGE_OLD.replace(",比不写更糟。", "。")

# ⏳ **只删"真重复"的两处**,别的字一个不动 —— 砍过头已经用数据证伪过一次:
#   (a) 「想不起来的时候用 —— 」  ← desc 里已经说了「想不起来时用」
#   (b) 「找聊过的话 → scope=talk;找你一个人时做过的事 → scope=life;拿不准就不填 scope。」
#       ← schema 的 scope.description 里有一份一模一样的
#   这两处删掉之后,如果"自造日期"还是 ~2/25,那这版就是**既干净又不掉性能**的定稿。
RECALL_USAGE_Z1 = RECALL_USAGE_OLD.replace("想不起来的时候用 —— ", "")
RECALL_USAGE_Z2 = RECALL_USAGE_OLD.replace(
    "找聊过的话 → scope=talk;找你一个人时做过的事 → scope=life;拿不准就不填 scope。", "")
RECALL_USAGE_Z3 = RECALL_USAGE_Z1.replace(
    "找聊过的话 → scope=talk;找你一个人时做过的事 → scope=life;拿不准就不填 scope。", "")

# ---------------------------------------------------------------
# s1 对照臂(2026-09-20 用户指出后加)—— **scope 一个字都没教过**。
#
# 实测(`--cases "你之前说,你日记里" --reps 6`,各 6 次):
#   「你日记里都记了些什么呀」(问**内容**,该 life)   → scope=life  **6/6** ✅
#   「你之前说早上在写日记」(问**我说过这件事**,该 talk) → scope=talk  **0/6**
#                                                        → 全是 `all`(1 次干脆没填)
# 两次的 query 都写对了(前者"我说过早上在写日记的事",后者"我日记里记的内容"),
# **是 scope 这个参数没跟上,不是她不懂语义**。
#
# 根因:她手上关于 scope 的**全部**信息只有 schema 里那一行
#       `life=你独自做的事 / talk=聊过的话 / all(默认)`
# —— `all` 是唯一标着"(默认)"的,而 usage **一个字不提 scope**
#    (197 字那版把 scope 映射当"纯重复"删了;它在 schema 里,删得没错,
#     但 schema 只*定义*取值,"什么时候用哪个"从来就不在那儿)。
#
# 代价不是抽象的:`limit=2`。`all` 不筛 kind,life 与 talk 抢**同两个槽位**,
# 各来一条就把对的那条挤掉了。
#
# ⚠️ 只动 usage 这一句 = 单变量(与 TRAPS §一.10 的教训对齐)。
#    schema 里那个"(默认)"字样是**另一个变量**,这轮没动(要动得先给探针
#    加一个能改参数说明的口子 —— 现在 RECALL_VARIANTS 只带 (desc, usage))。
# ---------------------------------------------------------------
RECALL_USAGE_S1 = RECALL_USAGE + (
    "**scope 按你问的是哪一类填**:问你自己做过什么 → life,"
    "问你们说过的话 → talk;两类都真要才用 all —— "
    "全查时两类抢同几个位置,对的那条容易被挤掉。"
)
# ⛔ 上面这句**已否决**(2026-09-20 用户指出)。留在这里当证据,别再用:
#   它把 all 的门槛写成"两类都真要"(错,应该是"单边风险大"),
#   还把多带回来的那条说成"抢位置"(错,那是**有益的情景上下文**)。
#   见下方 RECALL_PARAM_SCOPE 上面那段引语。

# 变体表:名字 → (description, usage) 或 (description, usage, scope 参数说明)。
# 第三项可省 —— 省略 = 用定稿的 RECALL_PARAM_SCOPE。
#
# ⚠️ **用 `variant_parts()` 取,别直接解包**:2026-09-20 加了"能改参数说明"这条轴
#    (schema 与 usage 是**两条不同的通道**,core/tools.py:20-24),于是有的变体
#    是 2 元组、有的是 3 元组。直接 `desc, usage = RECALL_VARIANTS[k]` 会在
#    3 元组上炸 —— 这是纯机械的错,别让它在四个消费点各炸一次。
RECALL_VARIANTS: dict[str, tuple[str, str]] = {
    "★定稿": (RECALL_DESC, RECALL_USAGE),                  # ← 结构化长版(190 字)
    "s1-加scope句": (RECALL_DESC, RECALL_USAGE_S1),          # ← 对照臂:补 scope 的用法
    # s2 = 定稿 + **只去掉 schema 里的连字符"(默认)"**   → 隔离"参数说明"这个变量
    "s2-去默认字样": (RECALL_DESC, RECALL_USAGE, RECALL_PARAM_SCOPE_NODEF),
    # s3 = s1 + s2(两处都改)                             → 看合起来够不够
    "s3-两处都改": (RECALL_DESC, RECALL_USAGE_S1, RECALL_PARAM_SCOPE_NODEF),
    # ⚠️ `s4-all定义` **已毕业** → 就是现在的 `RECALL_PARAM_SCOPE`(见上方引语),
    #    所以变体表里不再单列;要对照旧版用 `s2`(零语义那版)或直接改回
    #    `RECALL_PARAM_SCOPE_OLDDEF`。
    "y1-定稿+三字": (RECALL_DESC, RECALL_USAGE_Y1),          # ← 只加「比不写更糟」
    "y2-旧版-三字": (RECALL_DESC_OLD, RECALL_USAGE_Y2),      # ← 只删「比不写更糟」
    "r1-usage去标签": (RECALL_DESC, _RECALL_USAGE_R1),
    "r2-desc去边界": (_RECALL_DESC_R2, RECALL_USAGE),
    "r3-两处都去": (_RECALL_DESC_R2, _RECALL_USAGE_R1),
    "z1-删重复opener": (RECALL_DESC, RECALL_USAGE_Z1),
    "z2-删scope句": (RECALL_DESC_OLD, RECALL_USAGE_Z2),
    "z3-两处都删": (RECALL_DESC, RECALL_USAGE_Z3),
    "旧短版88": (RECALL_DESC_88, RECALL_USAGE_88),            # ← 砍过头的那版,留作反例
    "定稿+日期句": (RECALL_DESC_88, RECALL_USAGE_MID),
    "v0-旧长版": (RECALL_DESC_OLD, RECALL_USAGE_OLD),      # ← 242 字基线
    "v1-dsh拆分": (RECALL_DESC, RECALL_USAGE_V1),
    "v2-只靠desc": (RECALL_DESC_V2, ""),
    "v3-结构化但留段里": (RECALL_DESC_OLD, RECALL_USAGE_V3),
    "v4-最短": ("回想过去的事。想不起来时用。", ""),
    "v5-无日期规则": (RECALL_DESC_88, RECALL_USAGE_88),
    "x0-故意鼓励写日期": (RECALL_DESC_OLD, RECALL_USAGE_X0),   # 阳性对照
}


def variant_parts(key: str) -> tuple[str, str, str]:
    """→ (description, usage, scope 参数说明)。三个消费点都走这里,别自己解包。"""
    v = RECALL_VARIANTS[key]
    return (v[0], v[1], v[2] if len(v) > 2 else RECALL_PARAM_SCOPE)


def pick_variant(name: str) -> str | None:
    """按名字(或前缀)找变体键;与 recall_e2e / prompt_lab 的容错一致。"""
    if name in RECALL_VARIANTS:
        return name
    return next((k for k in RECALL_VARIANTS
                 if k.startswith(name) or k.lstrip("★ ") == name or name in k),
                None)


# "被模型抄进正文"的那行标记,清洗**已经提到内核**(2026-09-21):
#   core.session_log.strip_copied_prefix —— 识别式由 personas.LIFE_EVENT_PREFIX
#   现推,不是这里写死的正则。以前这里自己存了一份 `_COPIED_MARK`,
#   于是**投影层 / UI 面板 / 探针三处只有探针在洗**(脏字从另外两个口子漏出去)。
#   现在三处同源:内核投影(_label_first_text)、server view.life_events、这里。
import core.session_log as _slog  # noqa: E402


def strip_copied(text: str) -> str:
    """本探针的取用口(便于断言时替换/单独测)。"""
    return _slog.strip_copied_prefix(text, P.LIFE_EVENT_PREFIX)


# ============================================================
# 1. 读真实日志
# ============================================================

def load_log() -> SessionLog:
    lines = [l for l in LOG_PATH.read_text(encoding="utf-8").splitlines() if l.strip()]
    return SessionLog.from_lines(SID, lines)


def _text_of(data: dict) -> str:
    return "".join(
        b.get("text", "") for b in data.get("content", []) if isinstance(b, dict)
    ).strip()


def collect_turns(log: SessionLog) -> list[dict]:
    """按轮归拢:source / 时刻 / 内容。"""
    turns: dict[int, dict] = {}
    for e in log.events:
        t = e.data.get("turn")
        if t is None:
            continue
        d = turns.setdefault(t, {"turn": t, "source": "?", "time": e.time,
                                 "user": None, "assistant": None})
        if e.type == "turn/start":
            d["source"] = e.data.get("source", "?")
            d["time"] = e.time
        elif e.type == "user/message":
            d["user"] = {"text": _text_of(e.data),
                         "source": e.data.get("source", "user"), "time": e.time}
        elif e.type == "assistant/message":
            d["assistant"] = {"text": _text_of(e.data), "time": e.time}
    return [turns[k] for k in sorted(turns)]


def collect_memory(turns: list[dict]) -> list[dict]:
    """把日志摊成可检索的条目。kind 只有两种:

      life = **生活事件** —— 她独处时做的/说的。
             (自走轮与补写轮同属:触发时机不同,产物是同一个东西 ——
              用户 2026-09-21 原话。判据**所以**看 source=="self",不看是哪台机器产的。)
      talk = **聊过的一轮** —— **一轮一条**:她的话和她的回答合在一起。

    2026-09-21 用户拍板改的(旧版:talk **一轮拆成两条**,user 一条、她一条):
      「检索的话一般都是连带着我的话和她的回答一起的吧?自走轮则是单独拿她自己的
        那个事件,毕竟确实也没有 user 消息。」

    role 只是**路由量**,不进 embedding(embed 只吃 text,见 build_db)。
      'self' = 只有她那边(生活事件 / 被打断的轮)
      'user' = 只有用户那边(用户说了但没答上)
      'both' = 一问一答合成的一条 talk
    """
    rows: list[dict] = []
    cleaned = 0
    for t in turns:
        if t["source"] == "self" and t["assistant"]:
            txt = t["assistant"]["text"]
            new = strip_copied(txt)
            if new != txt:
                cleaned += 1
                txt = new
            rows.append({"turn": t["turn"], "kind": "life", "role": "self",
                         "time": t["assistant"]["time"], "text": txt})
        elif t["source"] == "user":
            # 一轮一条:用户的话 + 她的回答。缺哪边就只放哪边(被打断的轮别丢)。
            said = (t["user"]["text"]
                    if t["user"] and t["user"]["source"] == "user" else "")
            reply = t["assistant"]["text"] if t["assistant"] else ""
            if not (said or reply):
                continue
            rows.append({
                "turn": t["turn"], "kind": "talk",
                "role": "both" if (said and reply) else ("user" if said else "self"),
                # 时刻 = 这条记忆里**最先**出现的那个(用户 2026-09-21 拍板:
                # 「talk 记忆算该 step 最先的那个」)—— 有多段时取最小值,
                # **不许取最后一段**(取最后一段会让记忆的时间往后飘)。
                "time": min(ts for ts in (
                    t["user"]["time"]
                    if (t["user"] and t["user"]["source"] == "user") else None,
                    t["assistant"]["time"] if t["assistant"] else None,
                ) if ts is not None),
                "text": "\n".join(x for x in (said, reply) if x),
            })
    rows.sort(key=lambda r: r["time"])
    return rows, cleaned


def _fmt(ts: float) -> str:
    return time.strftime("%m-%d %H:%M", time.localtime(ts))


# ============================================================
# 2. embedder
#    真: BGE(bge-large-zh-v1.5)—— torch/transformers 已在环境里,
#        模型已在 HF 缓存,本地加载 ~1s,编码 ~50ms/条。
#    占位: 字符 bigram —— BGE 拿不到时的兜底,只为跑通路由,**不代表检索质量**。
# ============================================================

_BGE_NAME = "BAAI/bge-large-zh-v1.5"
# BGE 官方用法:query 侧要加指令前缀,passage 侧不加;取 CLS 向量 + L2 归一化。
_BGE_QUERY_PREFIX = "为这个句子生成表示以用于检索相关文章:"


def _bigram_vec(text: str) -> dict[str, float]:
    t = re.sub(r"\s+", "", text)
    c = collections.Counter(t[i:i + 2] for i in range(len(t) - 1))
    n = math.sqrt(sum(v * v for v in c.values())) or 1.0
    return {k: v / n for k, v in c.items()}


def _cos(a, b) -> float:
    """余弦。两种向量都认:bigram 的稀疏 dict,和 BGE 的稠密 list(已归一化)。"""
    if isinstance(a, dict) and isinstance(b, dict):
        if len(a) > len(b):
            a, b = b, a
        return sum(v * b.get(k, 0.0) for k, v in a.items())
    return float(sum(x * y for x, y in zip(a, b)))


class BigramEmbedder:
    name = "占位 bigram(不代表检索质量)"
    dim = 0

    def passage(self, text: str):
        return _bigram_vec(text)

    def query(self, text: str):
        return _bigram_vec(text)


class BgeEmbedder:
    """本地 BGE。不依赖 sentence-transformers —— 直接用 transformers 复刻其用法。"""

    def __init__(self, name: str = _BGE_NAME) -> None:
        import os
        # 模型已在本地缓存:关掉联网,否则每次启动要等 90s 超时重试
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        import torch
        from transformers import AutoModel, AutoTokenizer

        self._torch = torch
        self.name = name
        self._tok = AutoTokenizer.from_pretrained(name, local_files_only=True)
        self._mdl = AutoModel.from_pretrained(name, local_files_only=True).eval()
        self.dim = self._mdl.config.hidden_size

    def _embed(self, texts: list[str]) -> list[list[float]]:
        t = self._torch
        enc = self._tok(texts, padding=True, truncation=True, max_length=512,
                        return_tensors="pt")
        with t.no_grad():
            v = self._mdl(**enc).last_hidden_state[:, 0]  # CLS
        v = t.nn.functional.normalize(v, dim=-1)
        return [[round(float(x), 6) for x in row] for row in v]

    def passage(self, text: str):
        return self._embed([text])[0]

    def query(self, text: str):
        return self._embed([_BGE_QUERY_PREFIX + text])[0]


class BoomEmbedder:
    """测试用:语义路必炸,用来验降级。"""
    name = "故意炸"
    dim = 0

    def passage(self, text: str):
        raise RuntimeError("embedding 服务连不上")

    def query(self, text: str):
        raise RuntimeError("embedding 服务连不上")


def get_embedder():
    """拿 BGE;拿不到就退回占位,并**说清用的是哪个**。"""
    try:
        return BgeEmbedder()
    except Exception as e:  # noqa: BLE001
        print(f"  ⚠️ BGE 不可用({type(e).__name__}: {e}),退回占位 bigram")
        return BigramEmbedder()


# ============================================================
# 3.5 时间抽取 —— 模型已经把"前天"算成"9月16日"写进 query,
#     所以这里**不做自然语言日期理解**,只"认日期"。
#     认出来 → 变成元数据过滤;认不出 → 什么都不做(不猜)。
# ============================================================

# 只认**日级**明确说法。不认"9月开学""三年前""上周"这种 —— 那些要么没有日,
# 要么范围太粗,猜错了比不猜更糟。
_RE_YMD = re.compile(r"(?:(\d{4})\s*[-/年]\s*)?(\d{1,2})\s*[-/月]\s*(\d{1,2})\s*日?")
_RE_REL = [("大前天", -3), ("前天", -2), ("昨天", -1), ("今天", 0)]
_RE_WEEKDAY = re.compile(r"周[一二三四五六日天]|星期[一二三四五六日天]|礼拜[一二三四五六日天]")


def extract_day_range(query: str, now_ts: float):
    """从 query 里抽一天窗口。返回 (start, end, 命中的原文) 或 None。

    年份省略时按"不能是未来"推:算出来比现在晚一天以上,就退回上一年
    (跨年边界:12 月问"1月5日"、1 月问"12月28日")。
    """
    now = time.localtime(now_ts)
    hit = None
    y = m = d = None
    if (mo := _RE_YMD.search(query)):
        y = int(mo.group(1)) if mo.group(1) else None
        m, d = int(mo.group(2)), int(mo.group(3))
        hit = mo.group(0)
    else:
        for word, delta in _RE_REL:
            if word in query:
                t = time.localtime(now_ts + delta * 86400)
                y, m, d = t.tm_year, t.tm_mon, t.tm_mday
                hit = word
                break
    if hit is None:
        return None
    if not (1 <= m <= 12 and 1 <= d <= 31):
        return None  # 月份/日子不合法:不当时间用(宁可不过滤)
    if y is None:
        y = now.tm_year
    start = time.mktime((y, m, d, 0, 0, 0, 0, 0, -1))
    if start > now_ts + 86400:
        start = time.mktime((y - 1, m, d, 0, 0, 0, 0, 0, -1))
    return start, start + 86400, hit


def strip_time_words(query: str, hit: str) -> str:
    """把时间词从 query 里剥掉 —— 库里没有日期,留着只会污染向量。"""
    q = query.replace(hit, " ")
    q = _RE_WEEKDAY.sub(" ", q)
    q = re.sub(r"\s+", " ", q).strip(" ,，。、的")
    return q or query  # 剥空了就退回原句(总比没 query 强)


# ============================================================
# 4. memory 表 + 检索工具(参数路由)
# ============================================================

DDL = """
CREATE TABLE memory (
  id         INTEGER PRIMARY KEY,
  session_id TEXT NOT NULL,
  turn       INTEGER NOT NULL,   -- 回指轮号(可追溯)
  kind       TEXT NOT NULL,      -- 'life' | 'talk'
  role       TEXT NOT NULL,      -- 'self' | 'user' | 'both'(路由量,**不进 embedding**)
  time       REAL NOT NULL,      -- 事件时刻(排序键)
  text       TEXT NOT NULL,
  embedding  TEXT                -- JSON;NULL = 还没算
);
CREATE INDEX idx_mem_time ON memory(session_id, time);
CREATE INDEX idx_mem_kind ON memory(session_id, kind, time);
"""


def build_db(rows: list[dict], embedder=None) -> sqlite3.Connection:
    # ⚠️ `check_same_thread=False` 是**必须的,不是图方便**(2026-09-19 端到端实测踩到):
    # `core/loop.py:428` 里,同一个 step 只要**有两个以上**工具调用就走
    # ThreadPoolExecutor —— 而 sqlite3 默认 check_same_thread=True,连接一旦在
    # 别的线程里用就抛 ProgrammingError。症状极具欺骗性:模型一次问两件事
    # (很常见),两次 recall **同时**死掉,工具老老实实报 down,她就说
    # "我翻不到",用户看到的是"她记性不好",完全看不出是接线问题。
    # 锁在 make_recall_tool 里(读路径是并发的;这里的写只在主线程)。
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.executescript(DDL)
    for i, r in enumerate(rows, 1):
        emb = json.dumps(embedder.passage(r["text"])) if embedder else None
        conn.execute(
            "INSERT INTO memory (id, session_id, turn, kind, role, time, text, embedding)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (i, SID, r["turn"], r["kind"], r["role"], r["time"], r["text"], emb),
        )
    conn.commit()
    return conn


# 结果四态 —— **给角色的话必须分开,不能混**
#   故障说成"没这回事"会出事;没找着说成"想不起来"会像天天失忆。
R_OK = "ok"              # 找到相关的
R_EMPTY = "empty"        # 服务正常,确实没有相关的  → 不许说"想不起来"
R_DEGRADED = "degraded"  # 退而求其次(语义路塌了 / query 没给)
R_DOWN = "down"          # 检索整个跑不起来        → 示弱,别硬猜

# **只挡明显无关的地板**,不是"相关性判据"(2026-09-19 实测证伪:
# 同一件事换措辞,bi-encoder 分数在 0.358~0.422 之间横跳,硬判会误杀正确答案)。
_FLOOR = 0.25             # < 这个数基本可以确定不相关(bge-large-zh 实测 0.216)
_GREY_TOP = 0.45          # < 这个数 → 给结果,但**标明不确定**


def make_recall_tool(
    conn: sqlite3.Connection,
    *,
    limit: int = 2,
    limit_fn=None,       # 产品侧算条数的钩子(动态算法;默认常数 2)
    embedder=None,       # 语义路(BGE / 占位);默认占位 bigram
    min_score: float | None = None,  # 相似度阈值;None=不设
    now_fn=None,         # 时间抽取用的钟;默认系统时钟
    verbose: bool = True,
    boundary: bool = True,    # 结果侧"别补细节"边界句(MVP 默认开,见 _BOUNDARY_*)
) -> Tool:
    """检索工具:参数路由 —— 查什么数据(scope)× 怎么匹配(query 有没有)。

    **模型只填 query 和 scope。** `limit` **不进 schema** —— 它是产品侧按上下文
    预算算出来的(默认 2)。口子不开,模型就填不出 -1(返回全库)或 "abc"。

    limit 硬约束:**永远 ≥ 1**(2026-09 用户拍板"不准关";动态算法落地后本来
    也 0 不出来,所以 0 这个语义是多余的)。

    verbose=False:测试用,不打印每次调用的明细(否则断言行被淹没)。
    """
    embed = embedder or BigramEmbedder()
    # 读路径会被**并发**调用(core/loop.py 的线程池),而 sqlite 连接不是为并发
    # 准备的 —— 一个进程内一把锁足够(探针里就一个连接)。
    _db_lock = threading.Lock()

    # 稀疏路(BM25):**全库一次**,惰性建。排序用,闸门不用(见 _semantic_rows)。
    _bm_state: dict = {}

    def _bm25_for() -> tuple:
        if "bm" not in _bm_state:
            with _db_lock:
                allrows = [tuple(r) for r in conn.execute(
                    "SELECT id, text FROM memory WHERE session_id = ? ORDER BY id",
                    (SID,))]
            _bm_state["id2pos"] = {r[0]: k for k, r in enumerate(allrows)}
            _bm_state["bm"] = BM25([r[1] for r in allrows])
        return _bm_state["bm"], _bm_state["id2pos"]

    def _limit_of(args: dict) -> int:
        n = limit_fn(args) if limit_fn else limit
        try:
            n = int(n)
        except (TypeError, ValueError):
            n = limit
        return max(1, n)  # 防御性下限(算法落地后本来也不会出 0)

    def _where(scope: str) -> tuple[str, list]:
        conds, params = ["session_id = ?"], [SID]
        if scope in ("life", "talk"):
            conds.append("kind = ?")
            params.append(scope)
        return " AND ".join(conds), params

    def _time_rows(w: str, params: list, n: int) -> tuple[list, str]:
        sql = f"SELECT time, kind, text FROM memory WHERE {w} ORDER BY time DESC LIMIT ?"
        with _db_lock:
            rows = [tuple(r) for r in conn.execute(sql, [*params, n])]
        return rows, sql

    def _semantic_rows(w: str, params: list, query: str, n: int,
                       day=None) -> tuple[list, str, list]:
        """语义路 + 稀疏路**融合排序**;返回的 scores 仍是**余弦**(闸门/灰区线用)。

        ⚠️ 为什么排序和 scores 不是同一个数(2026-09-21 大语料结论):
           · **排序**要的是"哪一条更对" → 融合分最准(315 条语料 top1 83%→95%);
           · **闸门/灰区**要的是"到底有没有" → 只有余弦可标定(`_GREY_TOP=0.45`
             是按余弦定的),融合分随 query 类型漂移,拿它比 0.45 没有意义。
           所以:融合分只决定顺序,余弦只决定说辞。**两个数各干一件事。**
        """
        cond = w + (" AND time >= ? AND time < ?" if day else "")
        sql = f"SELECT id, time, kind, text, embedding FROM memory WHERE {cond}"
        qv = embed.query(query)
        bm, id2pos = _bm25_for()
        scored = []
        with _db_lock:   # 锁要包住整个游标迭代,不只是 execute
            hit = list(conn.execute(sql, [*params, *day] if day else params))
        for rid, ts, kind, text, emb in hit:
            cos = _cos(qv, json.loads(emb))
            if min_score is not None and cos < min_score:
                continue  # 阈值:挡掉"看着像其实不是"(**按余弦判**,不看融合分)
            k = id2pos.get(rid)
            b = bm.score(query, k) if k is not None else 0.0
            # 稀疏路**保留绝对刻度**:s/(s+K),不按 query 内最大值归一
            # (归一化会把"一条都没命中"的 query 的最高分也抬成 1.0 —— 实测踩过两次)
            fused = cos + W_SPARSE * (b / (b + BM25_K))
            scored.append((fused, cos, ts, kind, text))
        scored.sort(key=lambda x: -x[0])
        return ([(ts, kind, text) for _f, _c, ts, kind, text in scored[:n]], sql,
                [round(c, 4) for _f, c, *_ in scored[:n]])

    def _run(args: dict) -> dict:
        """结构化结果(测试断言用);_recall 只负责把它渲染成给角色的话。"""
        query = (args.get("query") or "").strip()
        scope = (args.get("scope") or "all").strip()
        bad_scope = scope not in ("all", "life", "talk")
        n = _limit_of(args)
        w, params = _where(scope)
        # ---- 时间抽取:抽到就变元数据过滤,并把时间词从 query 剥掉 ----
        now_ts = (now_fn or time.time)()
        day_hit = extract_day_range(query, now_ts) if query else None
        day = (day_hit[0], day_hit[1]) if day_hit else None
        q_sem = strip_time_words(query, day_hit[2]) if day_hit else query
        try:
            scores: list = []
            nearby: list = []
            nearby_scores: list = []
            if not query:
                # query 没给 = **降级落点**(不是模型可选模式):给时间序 + 说明
                rows, sql = _time_rows(w, params, n)
                state, why = R_DEGRADED, "no_query"
            else:
                try:
                    rows, sql, scores = _semantic_rows(w, params, q_sem, n, day)
                    if bad_scope:
                        # 范围不认识 → 放宽到全部,但**必须说出来**
                        state, why = R_DEGRADED, "bad_scope"
                    elif not rows and day:
                        # 那天一条都没有。**状态语义一个字不改** ——
                        # empty/day_empty 就是拍板过的"确实没有",不许动。
                        # 但顺手**再查一次不带时间过滤的**,把前后的事附在后面:
                        # 实测她写的那个日期很可能是**自己猜的**(问"你之前说早上
                        # 在写日记",她写「今天早上」)。猜错一个日期就空手回去,
                        # **加提示词规则治不好**(实测:带日期规则的 2/24、
                        # 完全不带日期规则的也 2/24)—— 所以治在机制上,
                        # 让猜错日期的代价从「查不到」降到「查到的不是那天的」。
                        try:
                            rows2, _sql2, scores2 = _semantic_rows(
                                w, params, q_sem, n, None)
                        except Exception:  # noqa: BLE001
                            rows2, scores2 = [], []
                        nearby = [(ts, k, t) for ts, k, t in rows2]
                        nearby_scores = list(scores2)
                        state, why = R_EMPTY, "day_empty"
                    else:
                        # 阈值把候选全滤掉 → 这才是真的"确实没有"
                        state, why = (R_OK if rows else R_EMPTY), ""
                except Exception:  # 语义路塌了 → 退时间序,别把整轮炸掉
                    rows, sql = _time_rows(w, params, n)
                    state, why = R_DEGRADED, "semantic_failed"
        except Exception:
            # 连时间序都跑不动 = 检索整个不可用
            return {"state": R_DOWN, "why": "backend_down", "items": [],
                    "query": query, "scope": scope, "sql": "", "scores": [],
                    "nearby": [],
                    "day": day_hit[2] if day_hit else None}
        return {"state": state, "why": why, "query": query, "scope": scope,
                "sql": sql, "scores": scores, "sem_q": q_sem,
                "day": day_hit[2] if day_hit else None, "items": [
                    {"time": _fmt(ts), "kind": k, "text": t[:200]} for ts, k, t in rows
                ],
                "nearby": [
                    {"time": _fmt(ts), "kind": k, "text": t[:200]}
                    for ts, k, t in nearby
                ]}

    _WHY_TEXT = {"no_query": "你没说清找什么",
                 "semantic_failed": "检索这一步没跑成",
                 "bad_scope": "你给的范围我不认识,按全部找了",
                 "day_fallback": "你写的那天没有记录"}
    # 这三种是"退了时间序",措辞要说清下面是什么;bad_scope 不是。
    _TIME_FALLBACK = ("no_query", "semantic_failed")

    def _render(r: dict) -> str:
        return _render_body(r) + (_BOUNDARY_RECALL if boundary else "")

    def _render_body(r: dict) -> str:
        q = r["query"]
        items = r["items"]
        if r["state"] == R_DOWN:
            return ("[回忆] 这会儿想不起来了 —— 检索没能跑起来。"
                    "别硬猜,就说一时想不起来。")
        if r["state"] == R_EMPTY:
            if r["why"] == "day_empty":
                # 生活事件是**抽样**生成的:没记录 ≠ 那天没发生
                head = (f"[回忆] 「{r['day']}」这段时间,**你这边没留下什么记录**。\n"
                        "这不是「你那天什么都没做」—— 只是没记下来。"
                        "**别硬编那天做了什么**,就说想不太起来了。")
                nb = r.get("nearby") or []
                if nb:
                    # 她写的日期很可能是自己猜的(实测:问过去的事她写「今天早上」)。
                    # 那天确实没记录 —— 这句不变;但**前后的事**附在后面,
                    # 让猜错日期的代价从"查不到"降到"查到的不是那天的"。
                    # 措辞必须两头都说死:日期不对 + 别当成那天的。
                    head += ("\n另外,**前后这几天**你有下面这些 —— "
                             "**不是那天的**,别当成那天的说;"
                             "但也许你要找的东西在里面:")
                    head += "\n" + "\n".join(
                        f"{it['time']} {it['text']}" for it in nb)
                return head
            return (f"[回忆] 关于「{q}」没有查到。这是**确实没有相关的事**,"
                    "不是你想不起来 —— 别为它编内容。")
        lines = [f"{it['time']} {it['text']}" for it in items]
        if r["state"] == R_DEGRADED and r["why"] == "day_fallback":
            # 她说了一个日期、那天没记录,但去掉过滤**能查到东西**。
            # 措辞必须两头都说清:那天确实没记 + 下面这些**不是那天的**。
            head = (f"[回忆] 「{r['day']}」这段时间**没有记录**。"
                    f"下面这几条是**前后**的事 —— 别当成那天的,"
                    "先看看是不是你要找的:")
            return head + "\n" + "\n".join(lines)
        if r["state"] == R_DEGRADED:
            mid = ("下面只是那段时间前后的事 —— " if r["why"] in _TIME_FALLBACK
                   else "")
            what = f"「{q}」" if q else "你要找的东西"
            head = (f"[回忆] 没能按你说的找到{what}"
                    f"({_WHY_TEXT.get(r['why'], r['why'])})。"
                    f"{mid}**退而求其次,别当成就是这些**:")
        else:
            top = r["scores"][0] if r["scores"] else None
            if top is not None and top < _GREY_TOP:
                # 实测:同一件事换种措辞分数能从 0.42 掉到 0.36 —— 灰区**不许硬判**,
                # 硬判会误杀正确答案(学姐那条被挡过 4 次,检索其实每次都对)。
                head = (f"[回忆] 可能跟「{q}」有关(**不是很确定**),共 {len(lines)} 条 —— "
                        "先看看对不对,别当成准的讲:")
            else:
                head = f"[回忆] 与「{q}」相关,共 {len(lines)} 条:"
        return head + "\n" + "\n".join(lines)

    def _recall(args: dict) -> str:
        r = _run(args)
        if verbose:
            print(f"    SQL   : {r['sql'] or '(没跑到 SQL)'}")
            print(f"        参数: scope={r['scope']!r} query={r['query']!r} "
                  f"limit={_limit_of(args)}(产品侧算,默认 {limit})")
            if r.get("day"):
                print(f"        时间: 抽出「{r['day']}」→ 元数据过滤;"
                      f"送去 embed 的是 {r['sem_q']!r}")
            print(f"        状态: {r['state']}" + (f" ({r['why']})" if r["why"] else ""))
            for i, it in enumerate(r["items"]):
                sc = f"{r['scores'][i]:.3f}  " if i < len(r["scores"]) else ""
                print(f"        · {sc}[{it['kind']:4}] {it['time']}  "
                      f"{it['text'][:66].replace(chr(10), ' ')}")
        return _render(r)

    tool = Tool(
        name=RECALL_NAME,
        description=RECALL_DESC,
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": RECALL_PARAM_QUERY,
                },
                "scope": {
                    "type": "string",
                    "enum": ["all", "life", "talk"],
                    "description": RECALL_PARAM_SCOPE,
                },
            },
            "required": ["query"],
        },
        func=_recall,
        # ⚠️ usage **不在这里传**:它进 SYSTEM 段落,由装配处(探针的 build_apparatus /
        # 产品的 engine)统一贴。贴的就是上面那份 RECALL_USAGE —— 单一来源。
    )
    tool.run_structured = _run  # 探针自用:测试直接断言结构化结果
    return tool


# ============================================================
# 4. 往事段
# ============================================================

def _oneline(text: str) -> str:
    """生活事件的文本里全是换行和括号动作,压成一行,不然段里全是空行。"""
    return re.sub(r"\s*\n\s*", " ", text).strip()


def make_memory_section(rows: list[dict], limit: int = 8,
                        max_chars: int = 120, priority: int = _MEMORY_PRIORITY,
                        boundary: bool = True):
    """把最近的往事折成一段 SYSTEM 背景。

    rows  : collect_memory 的产物(kind='life')
    limit : 取最近几条
    max_chars : 单条截断(0 = 不截)
    boundary  : "别补细节"边界句(MVP 默认开)
    """
    recent = [r for r in rows if r["kind"] == "life"][-limit:]

    def _body(r: dict) -> str:
        t = _oneline(r["text"])
        if max_chars and len(t) > max_chars:
            t = t[:max_chars] + "…"
        return t

    def _render(values) -> str | None:
        if not recent:
            return None  # 她还没独自生活过:本段不出现
        lines = [_MEMORY_ITEM.format(time=_fmt(r["time"]), text=_body(r))
                 for r in recent]
        rule = _MEMORY_RULE + (_BOUNDARY_MEMORY if boundary else "")
        return f"{_MEMORY_TITLE}\n{rule}\n" + "\n".join(lines)

    # 这里只借 SystemSection 的壳(探针不注册进真 composer)
    from core.composer import SystemSection
    return SystemSection(name="memory", priority=priority, producer=_render), recent


# ============================================================
# 5. 路由测试(合成小样本,结果可判定)
# ============================================================

_FIXTURE = [
    # kind,  role,   距现在(分), 正文
    ("life", "self", -300, "在收衣柜,把夏天的短袖往里挪。"),
    ("life", "self", -200, "在挑衣服,购物车里加了五六件一件没下单。"),
    ("life", "self", -100, "在等你回消息,打了半句话又删掉了。"),
    ("talk", "user",  -90, "睡了没啊"),
    ("talk", "self",  -89, "还没。十一天了哦。"),
    ("talk", "user",  -10, "那你现在睡了没"),
    ("talk", "self",   -9, "刚不是说了嘛,还没。"),
]

_RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    _RESULTS.append((name, bool(ok), detail))
    mark = "PASS" if ok else "FAIL"
    print(f"    {mark}  {name}")
    if detail:
        print(f"          {detail}")


def fixture_db() -> sqlite3.Connection:
    """路由测试用的小样本。**故意用占位 bigram** —— 路由与数据源无关,
    不该为了测路由去加载 1.3GB 的模型。"""
    now = time.time()
    rows = [{"turn": i, "kind": k, "role": r, "time": now + m * 60, "text": t}
            for i, (k, r, m, t) in enumerate(_FIXTURE, 1)]
    return build_db(rows, BigramEmbedder())


def sql_tap(conn: sqlite3.Connection) -> list[str]:
    """抓真实执行的 SQL —— 用来断言"这条路上到底碰没碰 embedding"。"""
    seen: list[str] = []
    conn.set_trace_callback(seen.append)
    return seen


def test_routing() -> None:
    print("=" * 78)
    print("【C】路由测试(合成 7 条样本,结果可判定)\n")

    # ---------- 组 1:模式路由 ----------
    print("  组 1 · 模式路由(query 空 → 时间序;非空 → 语义)\n")

    conn = fixture_db()
    seen = sql_tap(conn)
    tool = make_recall_tool(conn, verbose=False, limit=7)
    tool.func({"scope": "all"})
    touched = [s for s in seen if "embedding" in s.lower()]
    check("R1 query 没给 → 走时间序,**完全不碰 embedding 列**", not touched,
          f"❌ 碰到了: {touched}" if touched else "✓ 执行的 SQL 里没有 embedding")

    for bad in ("", "   ", None):
        seen.clear()
        tool.func({"query": bad})
        assert not [s for s in seen if "embedding" in s.lower()], f"{bad!r} 去碰了 embedding"
    check("R2 query 空白/None 同样不碰 embedding", True, "✓ 三条都过")

    seen.clear()
    tool.func({"query": "等我回消息"})
    check("R3 query 非空 → **必须**取 embedding 列",
          bool([s for s in seen if "embedding" in s.lower()]),
          "✓ 执行的 SQL 里取到了 embedding")

    conn = fixture_db()
    tool = make_recall_tool(conn, verbose=False, limit=7)
    r_time = tool.run_structured({})
    r_sem = tool.run_structured({"query": "挑衣服 购物车"})
    check("R4 同一批数据,两条路由给出**不同**的排序",
          [x["text"] for x in r_time["items"]] != [x["text"] for x in r_sem["items"]],
          f"时间序首条={r_time['items'][0]['text'][:14]!r} / "
          f"语义首条={r_sem['items'][0]['text'][:14]!r}")

    # ---------- 组 2:scope 路由 ----------
    print("\n  组 2 · scope 路由\n")
    conn = fixture_db()
    tool = make_recall_tool(conn, verbose=False, limit=10)

    life = tool.run_structured({"query": "衣服", "scope": "life"})
    check("R5 scope=life 只返回 life",
          life["items"] and all(x["kind"] == "life" for x in life["items"]),
          f"拿到 {[x['kind'] for x in life['items']]}")
    check("R6 scope=life 条数正确(3)", len(life["items"]) == 3,
          f"实际 {len(life['items'])}")

    talk = tool.run_structured({"query": "睡了没", "scope": "talk"})
    check("R7 scope=talk 只返回 talk",
          talk["items"] and all(x["kind"] == "talk" for x in talk["items"]),
          f"拿到 {[x['kind'] for x in talk['items']]}")
    check("R8 scope=talk 条数正确(4)", len(talk["items"]) == 4,
          f"实际 {len(talk['items'])}")

    allx = tool.run_structured({"query": "衣服"})
    kinds = {x["kind"] for x in allx["items"]}
    check("R9 scope 缺省 = all(两种都有)", kinds == {"life", "talk"}, f"拿到 {kinds}")

    weird = tool.run_structured({"query": "衣服", "scope": "随便乱写"})
    txt_w = tool.func({"query": "衣服", "scope": "随便乱写"})
    check("R10 scope 乱填 → 放宽到全部,**并且说清这是降级**",
          weird["state"] == R_DEGRADED and weird["why"] == "bad_scope"
          and "退而求其次" in txt_w,
          f"state={weird['state']} why={weird['why']}")

    # ---------- 组 3:条数 —— 口子不开 + 不准关 ----------
    print("\n  组 3 · 条数(口子不开 + 不准关)\n")
    conn = fixture_db()

    props = make_recall_tool(conn, verbose=False).parameters["properties"]
    check("R11 **schema 里没有 limit** —— 模型就填不出 -1 / abc",
          "limit" not in props, f"暴露了: {list(props)}")

    d = make_recall_tool(conn, verbose=False)
    n_def = len(d.run_structured({"query": "衣服"})["items"])
    check("R12 默认条数 = 2", n_def == 2, f"实际 {n_def}")

    for bad_n in (0, -5):
        t = make_recall_tool(conn, verbose=False,
                             limit_fn=lambda a, n=bad_n: n)
        got = len(t.run_structured({"query": "衣服"})["items"])
        check(f"R13 limit_fn 返回 {bad_n} → 仍 ≥ 1(**不准关**)", got >= 1,
              f"实际 {got} 条 —— 出现了「关掉」这个语义")

    t = make_recall_tool(conn, verbose=False, limit=3, limit_fn=lambda a: "垃圾")
    check("R14 limit_fn 返回垃圾 → 回退默认,不炸",
          _safe(lambda: t.run_structured({"query": "衣服"})))

    t2 = make_recall_tool(conn, verbose=False, limit=7)
    times = [x["time"] for x in t2.run_structured({})["items"]]
    check("R15 时间序结果严格递减", times == sorted(times, reverse=True), f"{times}")

    # ---------- 组 4:四态分开(给角色的话不能混) ----------
    print("\n  组 4 · 四态分开 —— 给角色的话不能混\n")
    conn = fixture_db()
    tool = make_recall_tool(conn, verbose=False, limit=3)

    ok = tool.run_structured({"query": "挑衣服 购物车"})
    txt_ok = tool.func({"query": "挑衣服 购物车"})
    check("R16 命中 → state=ok,文本里**没有**降级标记",
          ok["state"] == R_OK and "退而求其次" not in txt_ok, f"state={ok['state']}")

    only_talk = build_db([{"turn": 1, "kind": "talk", "role": "user",
                           "time": time.time(), "text": "只有对话"}],
                         BigramEmbedder())
    te = make_recall_tool(only_talk, verbose=False)
    e = te.run_structured({"query": "衣服", "scope": "life"})
    txt_e = te.func({"query": "衣服", "scope": "life"})
    check("R17 查不到 → state=empty,说「确实没有」,**不许示弱说想不起来**",
          e["state"] == R_EMPTY and "确实没有" in txt_e
          and "退而求其次" not in txt_e and "没能跑起来" not in txt_e,
          f"state={e['state']} text={txt_e[:44]}")

    tb = make_recall_tool(conn, verbose=False, limit=3,
                          embedder=BoomEmbedder())
    b = tb.run_structured({"query": "挑衣服"})
    txt_b = tb.func({"query": "挑衣服"})
    check("R18 语义路抛异常 → 降级到时间序,**仍有结果**,不炸",
          b["state"] == R_DEGRADED and b["why"] == "semantic_failed"
          and len(b["items"]) == 3 and "退而求其次" in txt_b,
          f"state={b['state']} why={b['why']} items={len(b['items'])}")

    dead = fixture_db()
    td = make_recall_tool(dead, verbose=False)
    dead.close()  # 后端整个塌了
    dres = td.run_structured({"query": "挑衣服"})
    txt_d = td.func({"query": "挑衣服"})
    check("R19 后端塌了 → state=down,话是「想不起来」,**不抛异常**",
          dres["state"] == R_DOWN and "想不起来" in txt_d,
          f"state={dres['state']} text={txt_d[:44]}")

    # ---------- 组 5:往事段边界 ----------
    sec, _ = make_memory_section([], limit=8)
    check("R20 往事段无数据 → render 返回 None(本段不出现)",
          sec.render({}) is None)

    sec2, _ = make_memory_section(
        [{"kind": "life", "time": time.time(), "text": "长" * 500}],
        limit=1, max_chars=120)
    item = sec2.render({}).splitlines()[-1]
    check("R21 超长往事截断到 120 字 + 省略号",
          item.endswith("…") and len(item) <= 140, f"最后一行 {len(item)} 字")

    # ---------- 组 5:时间抽取(纯函数,不用模型) ----------
    print("\n  组 5 · 时间抽取(元数据过滤,不靠语义)\n")

    def _day(q, now):
        return extract_day_range(q, now)

    T = time.mktime((2026, 9, 18, 22, 10, 0, 0, 0, -1))  # 09-18 22:10

    d = _day("9月16日周三我做了什么", T)
    check("R22 「9月16日」→ 09-16 全天窗口",
          d and _fmt(d[0]) == "09-16 00:00" and d[1] - d[0] == 86400,
          f"{_fmt(d[0]) if d else None}")

    d = _day("前天我做了什么", T)
    check("R23 「前天」(now=09-18)→ 09-16",
          d and _fmt(d[0]) == "09-16 00:00", f"{_fmt(d[0]) if d else None}")

    d = _day("昨天聊了什么", T)
    check("R24 「昨天」→ 09-17", d and _fmt(d[0]) == "09-17 00:00")

    d = _day("2026-09-15 我在干嘛", T)
    check("R25 带年份的 ISO 日期 → 09-15", d and _fmt(d[0]) == "09-15 00:00")

    d = _day("1月5日说过什么", T)
    check("R26 未来日期回退上一年(09-18 问 1月5日 → 2026-01-05)",
          d and _fmt(d[0]) == "01-05 00:00" and d[0] < T)

    check("R27 没有时间词的 query → 不抽取(不许猜)",
          _day("学姐 分手 便利店", T) is None
          and _day("今天天气怎么样", T) is not None)  # 今天算有
    check("R28 「9月开学」这种**不许**当成日期",
          _day("9月开学要准备什么", T) is None)
    check("R29 「三年前」这种不许当成日期(范围太粗,猜错更糟)",
          _day("三年前我说过什么", T) is None)

    check("R30 剥时间词:日期和星期都剥掉",
          strip_time_words("9月16日周三我做了什么", "9月16日") == "我做了什么",
          repr(strip_time_words("9月16日周三我做了什么", "9月16日")))
    check("R31 剥空了就退回原句,不产生空 query",
          strip_time_words("昨天", "昨天") == "昨天",
          repr(strip_time_words("昨天", "昨天")))

    # ---------- 组 6:时间路由端到端 ----------
    print("\n  组 6 · 时间路由端到端(真实数据:09-16 一条记录都没有)\n")
    real = collect_memory(collect_turns(load_log()))
    conn = build_db(real[0], BigramEmbedder())
    tday = make_recall_tool(conn, verbose=False, limit=3,
                            embedder=BigramEmbedder(), now_fn=lambda: T)
    r16 = tday.run_structured({"query": "9月16日周三我做了什么", "scope": "life"})
    check("R32 09-16 无记录 → state=empty(why=day_empty),不是拿别的日子顶",
          r16["state"] == R_EMPTY and r16["why"] == "day_empty",
          f"state={r16['state']} why={r16['why']} items={len(r16['items'])}")

    txt16 = tday.func({"query": "9月16日周三我做了什么", "scope": "life"})
    check("R33 措辞是「没留下记录」+ 明令别硬编",
          "没留下什么记录" in txt16 and "别硬编" in txt16,
          txt16.splitlines()[0][:52])

    r17 = tday.run_structured({"query": "9月17日我做了什么", "scope": "life"})
    check("R34 09-17 有记录 → 正常返回(过滤没把对的挡掉)",
          r17["state"] == R_OK and r17["items"]
          and r17["items"][0]["time"].startswith("09-17"),
          f"state={r17['state']} 首条={r17['items'][0]['time'] if r17['items'] else '-'}")

    # ---------- 组 7:灰区不硬判 ----------
    print("\n  组 7 · 灰区不许硬判(实测:阈值会误杀正确答案)\n")
    conn = fixture_db()
    tv = make_recall_tool(conn, verbose=False, limit=3,
                          embedder=BigramEmbedder(), min_score=_FLOOR)
    rv = tv.run_structured({"query": "挑衣服"})
    check("R35 灰区命中 → 结果里**标明不确定**,而不是被挡成 empty",
          rv["state"] == R_OK and "不是很确定" in tv.func({"query": "挑衣服"}),
          f"state={rv['state']} top={rv['scores'][0] if rv['scores'] else None}")

    # ---------- 汇总 ----------
    n = len(_RESULTS)
    bad = [r for r in _RESULTS if not r[1]]
    print("\n" + "=" * 78)
    print(f"路由测试汇总:PASS {n - len(bad)} / FAIL {len(bad)}   (共 {n} 条)")
    if bad:
        print("\n未通过:")
        for name, _ok, detail in bad:
            print(f"  · {name}")
            if detail:
                print(f"      {detail}")
    print("=" * 78)


def _safe(fn) -> bool:
    try:
        fn()
        return True
    except Exception as e:  # noqa: BLE001
        print(f"          异常: {type(e).__name__}: {e}")
        return False


# ============================================================
# 6. 主流程
# ============================================================
def main() -> None:
    log = load_log()
    turns = collect_turns(log)
    rows, cleaned = collect_memory(turns)
    now = log.events[-1].time
    n_life = sum(1 for r in rows if r["kind"] == "life")
    n_talk = sum(1 for r in rows if r["kind"] == "talk")

    print("=" * 78)
    print(f"真实卡 {SID}")
    print(f"轮 {len(turns)}   往事(life) {n_life} 条   对话(talk) {n_talk} 条"
          f"   清掉被抄进正文的标记 {cleaned} 条")
    t_emb = time.perf_counter()
    emb = get_embedder()
    print(f"embedder: {emb.name}"
          + (f"  dim={emb.dim}  加载 {time.perf_counter() - t_emb:.1f}s" if emb.dim else ""))
    print("=" * 78)

    # ---------------- A. 往事段 ----------------
    print("\n【A】往事段 vs 挤在对话历史里\n")

    state = CharacterState({"outfit": "灰色薄针织衫"})
    empty = ToolRegistry([])
    world = lambda: time.localtime(now)  # noqa: E731

    sec, recent = make_memory_section(rows, limit=8, max_chars=160)
    comp_before = build_small_night_composer(
        P.PERSONA, state, empty, situation=P.CHAT_SITUATION, world_now=world)
    comp_after = build_small_night_composer(
        P.PERSONA, state, empty, situation=P.CHAT_SITUATION, world_now=world,
        extra_sections=[sec])

    vals = {**P.VALUES, "registry": empty, "log": log, "now_epoch": now}
    for tag, comp in (("现状", comp_before), ("加往事段", comp_after)):
        text = comp.compose(vals)
        shown = [s.name for s in comp.sections() if s.render(vals)]
        print(f"  ── {tag} ──  段顺序: {' → '.join(shown)}")
        print(f"      SYSTEM 总长 {len(text)} 字")
    print()
    body = sec.render(vals)
    sec_full, _ = make_memory_section(rows, limit=8, max_chars=0)
    full = sec_full.render(vals)
    print(f"  条数 {len(recent)}   截断@120 时 {len(body)} 字   "
          f"不截断 {len(full)} 字")
    print()
    print("  新段全文(截断@120):")
    for line in body.splitlines():
        print(f"    | {line}")

    # messages 对比
    #
    # 【2026-09-21 标 · ❌ 本节已被产品拍板取代】往事段**不做**(往事只走 `recall`
    # 工具),而且独处轮**整条不进投影**(连窗口名额都不占)。所以下面那两臂
    # ("现状" vs "往事出窗")现在**必然相等** —— 留着只是"出窗"这件事的历史对照,
    # 别再拿它当 A/B 证据引用。往事段的渲染(上面那段)同理,只剩史料价值。
    self_turns = {t["turn"] for t in turns if t["source"] == "self"}
    kept = [e for e in log.events
            if not (e.data.get("turn") in self_turns
                    and e.type not in ("turn/start", "turn/end"))]
    log_after = SessionLog(SID, events=kept)

    def _stats(lg, tag):
        msgs = lg.derive_messages(last_turns=12,
                                  user_time_prefix=P.USER_TIME_PREFIX)
        chars = sum(len(json.dumps(m.get("content"), ensure_ascii=False)) for m in msgs)
        role = collections.Counter(m["role"] for m in msgs)
        print(f"  {tag:10} messages {len(msgs):3d} 条  "
              f"(assistant {role['assistant']:2d} / user {role['user']:2d})  "
              f"{chars:6d} 字符")

    print()
    print("  对话窗口(last_turns=12):")
    _stats(log, "现状")
    _stats(log_after, "往事出窗")
    print(f"  + 往事段 {len(body)} 字(❌ 2026-09-21 已取消,此项不再进产品)")

    # ---------------- B. 检索工具 ----------------
    print("\n" + "=" * 78)
    print("【B】检索工具:模型只填 query/scope,limit 由产品侧算\n")
    print(f"  embedder: {emb.name}" + (f"  dim={emb.dim}" if emb.dim else ""))
    t_idx = time.perf_counter()
    conn = build_db(rows, emb)
    print(f"  建索引 {len(rows)} 条:{(time.perf_counter() - t_idx) * 1000:.0f} ms\n")
    tool = make_recall_tool(conn, limit=3, embedder=emb)
    print("  tools[] 里的 schema(**没有 limit**):")
    print("    " + json.dumps(tool.schema()["function"]["parameters"],
                              ensure_ascii=False))
    print()

    cases = [
        ("正常回忆", {"query": "挑衣服 购物车", "scope": "life"}),
        ("正常回忆", {"query": "学姐 分手", "scope": "life"}),
        ("找谈话内容", {"query": "你问我睡了没", "scope": "talk"}),
        ("查不到(库里有,但都不相关)", {"query": "量子力学 哈密顿量"}),
        ("query 没给(降级落点)", {"scope": "life"}),
        ("scope 乱填(降级)", {"query": "挑衣服", "scope": "随便乱写"}),
    ]
    for i, (name, args) in enumerate(cases, 1):
        print(f"  [{i}] {name}   {args}")
        txt = tool.func(args)
        print("      ── 给她的 result ──")
        for line in txt.splitlines():
            print(f"      | {line[:96]}")
        print()

    print("  [7] 后端塌了(故障态)—— 直接看给她的 result")
    dead = build_db(rows, emb)
    dtool = make_recall_tool(dead, verbose=False)
    dead.close()
    for line in dtool.func({"query": "挑衣服"}).splitlines():
        print(f"      | {line}")
    print()

    # ---------------- C. 路由测试 ----------------
    print()
    test_routing()

    # ---------------- D. 阈值标定 ----------------
    print()
    test_threshold(rows, emb)


# ============================================================
# 7. 阈值标定 —— "有数据但都不相关"该怎么认出来
# ============================================================

# 带标注的查询集:(类别, query, scope|None, 期望 top1 的时刻|None)
#   具体可答 —— 有唯一正确答案,按**时间戳**判对错(不靠关键词,那个会骗人)
#   不可答   —— 库里真的没有这件事
#   泛泛模糊 —— 问法本身没指向,**必须单独看**:它会把"可答"的分数拉低
_LABELED = [
    ("具体可答", "挑衣服 购物车",      "life", "09-15 10:09"),
    ("具体可答", "收衣柜 换季",        "life", "09-10 20:44"),
    ("具体可答", "梳头发 木梳",        "life", "09-12 23:19"),
    ("具体可答", "泡了杯茶 站在窗边",   "life", "09-14 09:33"),
    ("具体可答", "赶那篇要交的小论文",   "life", "09-09 20:44"),
    ("具体可答", "在看食堂的菜单",      "life", "09-09 11:50"),
    ("具体可答", "学姐 分手 便利店",    "life", "09-11 18:52"),
    ("具体可答", "你问我睡了没",        "talk", "09-17 22:14"),
    ("不可答",   "量子力学 哈密顿量",    None,   None),
    ("不可答",   "世界杯决赛的比分",     None,   None),
    ("不可答",   "你答应给我买的生日礼物", None,  None),
    ("不可答",   "我们聊过的高考成绩",    None,  None),
    ("泛泛模糊", "我们在聊什么",        None,   None),
    ("泛泛模糊", "你最近怎么样",        None,   None),
    ("泛泛模糊", "随便说点什么",        None,   None),
]


def test_threshold(rows: list[dict], emb) -> None:
    print("=" * 78)
    print("【D】阈值标定 —— 让「有数据但都不相关」显形\n")
    conn = build_db(rows, emb)
    tool = make_recall_tool(conn, verbose=False, limit=len(rows), embedder=emb)

    buckets: dict[str, list] = {"具体可答": [], "不可答": [], "泛泛模糊": []}
    print(f"  {'类别':<9}{'query':<20}{'top1':>7}{'margin':>8}  {'判':<4} 拿到的那条")
    print("  " + "-" * 78)
    for cat, q, kind, want in _LABELED:
        args = {"query": q}
        if kind:
            args["scope"] = kind
        r = tool.run_structured(args)
        sc = r["scores"]
        top = sc[0] if sc else 0.0
        margin = (sc[0] - sc[1]) if len(sc) > 1 else 0.0
        got = r["items"][0] if r["items"] else {}
        if want is None:
            mark = "—"
        else:
            mark = "✅" if got.get("time") == want else "❌"
        buckets[cat].append((q, top, margin, mark))
        print(f"  {cat:<9}{q:<20}{top:7.3f}{margin:8.3f}  {mark:<4} "
              f"{got.get('time','')} {got.get('text','')[:30].replace(chr(10),' ')}")

    print()
    hit = [x for x in buckets["具体可答"]]
    bad = [x for x in buckets["不可答"]]
    print(f"  具体可答 {len(hit)} 条:对 {(m := [x for x in hit if x[3] == '✅']) and len(m)}"
          f" / 错 {len(hit) - len(m)}")
    if len(m) != len(hit):
        for q, _t, _g, _k in hit:
            if _k != "✅":
                print(f"      ❌ {q}")

    lo_ans = min(t for _q, t, _g, _k in hit)
    hi_no = max(t for _q, t, _g, _k in bad)
    print(f"\n  具体可答 top1 最低分 = {lo_ans:.3f}")
    print(f"  不可答   top1 最高分 = {hi_no:.3f}")
    separated = lo_ans > hi_no
    print("  → " + (f"**分开了**,阈值可取 {hi_no:.2f} ~ {lo_ans:.2f}"
                    if separated else
                    "⚠️ **没分开** —— 单靠 top1 分数切不干净"))
    print(f"  泛泛模糊落在:{[f'{t:.3f}' for _q, t, _g, _k in buckets['泛泛模糊']]}"
          "  ← 看它们落在哪边")

    # 第二名差值(margin)能不能分开
    print()
    lo_m = min(g for _q, _t, g, _k in hit)
    hi_m = max(g for _q, _t, g, _k in bad)
    print(f"  【换一个信号:margin = top1 − top2】")
    print(f"  具体可答 margin 最低 = {lo_m:.3f}   不可答 margin 最高 = {hi_m:.3f}")
    print("  → " + ("**分开了**" if lo_m > hi_m else "同样没分开"))

    thr = round((lo_ans + hi_no) / 2, 2) if separated else None
    print()
    if thr is None:
        print("  结论:**给不出一个可信的全局阈值。** 理由见下。")
    else:
        print(f"  用阈值 {thr} 重跑:")
        t2 = make_recall_tool(conn, verbose=False, limit=3, embedder=emb,
                              min_score=thr)
        for cat, q, kind, want in _LABELED:
            args = {"query": q}
            if kind:
                args["scope"] = kind
            st = t2.run_structured(args)["state"]
            print(f"    {cat:<9}{q:<20} state={st}")
    print()
    print("  ⚠️ **这不是标定,是草图** —— 15 条查询、一张卡。")
    print("     真标定要一份标注集 + 旧 Yona 那套 eval(src/eval:basic/hyde/rerank/hybrid),")
    print("     拿 precision/recall 说话。这个数只能当**起点**,不能当结论。")
    print("=" * 78)


if __name__ == "__main__":
    main()
