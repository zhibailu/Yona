"""小夜子 · 工具集

两类工具,区别不在代码在**谁来描述它**:
  1. 动作类(change_outfit):有状态副作用 —— 工具只做"有语义的动作",
     状态作为副作用更新,模型不直接写状态。文案 = 她做这个动作时怎么回事。
  2. 委派类(launch_subagent):没有状态 —— 派出去一件活,拿回一个结论。
     文案 = 她**什么时候该派人、怎么派得好**。这一条尤其要紧:人设里禁止
     写死能力清单(character/persona.py 的 `make_persona_section` docstring,铁律那句),
     所以"会不会用这个工具"几乎
     全部由这里的文案决定。

工具文案有两条通道,缺一条它就瞎半只眼(core/tools.py 的 `Tool` 类 docstring):
  description -> 进 tools[] 的 schema,教"调用格式"
  usage       -> 经 make_usage_section 进 SYSTEM 的 [可用工具用法] 段,教"怎么用得好"
★ 但 usage **只在走 composer 的 SYSTEM 里才到达模型**;SYSTEM 若是一条静态串,
  它就是死文字(真跑踩过,见 test/subagent_prompt_view.py 幕5)。
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Callable, Sequence

from core.memory import SCOPES, MemoryIndex
from core.tools import Tool

from .state import CharacterState

# 委派回执的字段白名单。
# ⏳ 照抄 core/subrun.py 的 SubRunRecord 字段名;子运行的失败契约
#    还没收口(docs/protocols/SUBAGENT.md §8),收口后这里跟着定。
_RECEIPT_KEYS = ("run_id", "status", "detail", "steps", "duration", "usage", "output")


def _launch_usage(capabilities: Sequence[str]) -> str:
    """生成用法散文 —— **能力那句从工人的工具集来,不手抄**。

    规矩出处:`character/persona.py` 的 `make_persona_section` docstring
    「能力唯一来源 = 本轮 schema + 工具用法段;
    人设写死能力 → 工具子集变化时,模型仍以为有这工具」。这句话是"她能派出去
    做什么",和"她有什么工具"是同一类事实,所以同样不许写死 —— 写死就会在
    工具集变化后变成陈旧信息,而陈旧的能力描述会让她**凭假前提做决定**。

    capabilities 留空 = 不列举具体能力(只说"你做不了的事"),宁可不说不编。
    """
    if capabilities:
        head = (f"你自己做不了的事({'、'.join(capabilities)})一律派给它 —— "
                "你手上没这些工具,别凭印象编,派它去查。")
    else:
        head = "你自己做不了的事一律派给它 —— 你手上没这些工具,别凭印象编,派它去查。"
    # 以下三句是三轮真模型实验调出来的,改之前先看 test/subagent_prompt_lab.py
    # 的结论(长活那句是**降级成附赠**的:实测它推不动委派,留着只是因为无害)。
    return (
        head
        + "又长又乱的活也可以派,好处是中间过程不会留在对话里。"
        "它看不到你和用户的对话,派活时把要做的事写完整;"
        "拿回结论后,用自己的话说给用户听。"
    )


def make_launch_subagent_tool(
    runner: Callable[[str, str], dict[str, Any]],
    *,
    capabilities: Sequence[str] = (),
    name: str = "launch_subagent",
) -> Tool:
    """委派工具:把一件活派给一次性执行单元,拿回它的结论。

    runner(task, label) -> 结构化回执(dict)。**执行器不住在这里** ——
    工具只约定"给它任务、拿回事实"这一个形状。生产用的执行器是
    `core/subrun.py`,由装配处(server/app/engine.py)注入。这也是
    "拿本地弱模型当工人"的接口:换 runner 就换工人。

    capabilities: 工人手里那批工具的**短说法**(如 "上网查"、"翻本地文件")。
    由**装配处**给 —— 那是唯一同时知道工人注册表和这句话的地方。
    工具的完整文案(description/usage/schema)在能力句里只说用途、不列清单:
    她看不见包里有哪些工具名与参数,只看见"这包能干什么"。

    文案两段的读法(定稿时按这个次序读):
      description = 是什么 + 它看不见什么(格式)
      usage       = 什么时候派 + 怎么派好 + 拿到结论之后怎么办(用法)
    """

    def launch(args: dict[str, Any]) -> str:
        facts = runner(str(args.get("task", "")), str(args.get("label", "")))
        # 值为 None 的字段不写进回执(模型读 "usage": null 是纯噪音);
        # 0 保留 —— out 了 0 步和"没说"是两件事。
        receipt = {k: facts[k] for k in _RECEIPT_KEYS
                   if k in facts and facts[k] is not None}
        # 不加任何拟人化前缀:这里只给结构化事实(终态/耗时/血缘),
        # "怎么称呼这件事"是内容层的事,由 usage 散文交代。
        return json.dumps(receipt, ensure_ascii=False)

    return Tool(
        name=name,
        description=(
            "把一个费时或琐碎的活派给一个一次性执行单元,拿回它的结论。"
            "它看不到你和用户的对话,所以任务要写完整;它的中间过程不会进入对话。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "要它做什么。写完整 —— 它看不到你和用户的对话。",
                },
                "label": {
                    "type": "string",
                    "description": "一行标签,用来认出这次派的是哪件活(可省略)。",
                },
            },
            "required": ["task"],
        },
        func=launch,
        # ↓ 这段是**试出来的**,不是想出来的。三轮真模型实验(test/subagent_prompt_lab.py,
        #   共 126 次真调用,flash 档,温度 0.8~0.9)的结论:
        #
        #     她做不了的活(要上网查、要翻文件)  : 64/64 派
        #     她做得完的长活(1200 字原料整理)   :  6/52 派  <- 换任何措辞都推不动
        #     她做得完的短活                      :  0/10 派  <- 对照组,本该如此
        #
        #   所以**触发条件是"她没别的办法",不是"活很长"**。第一轮实验里
        #   五种"劝她派长活"的写法(命令式 / 划能力边界 / 情境段补一句 / 长短描述)
        #   全部无效,长活委派率仍趴在地上。所以能力那句写在最前面,长活降级成附赠。
        #
        #   ⏳ "你手上没这些工具"目前对**她**成立(产品给她的只有本工具与
        #      change_outfit)。哪天她自己拿到上网工具,这句话要跟着改 ——
        #      它描述的是"她手上的工具集",那是装配处的事实,不是文案的自由。
        usage=_launch_usage(capabilities),
        # 派活的结果跨轮保真:它是一次性的,重查不了(core/tools.py 的 `Tool` docstring
        # 里讲 `retain_result` 那一段,与 `retain_result: bool = False` 那行字段声明同一条)。
        retain_result=True,
    )


def make_change_outfit_tool(state: CharacterState) -> Tool:
    fields = state.mutable_fields
    return Tool(
        name="change_outfit",
        description=(
            "更换角色当前穿着的衣物。"
            f"可修改字段: {', '.join(fields)}。"
            "只改用户要求的字段,其余保持不动。"
        ),
        parameters={
            "type": "object",
            "properties": {f: {"type": "string"} for f in fields},
        },
        func=lambda args: _apply_outfit(state, args),
        # ⚠️ usage 里**不许再写工具名**:SYSTEM 那行已经是 composer 拼好的
        #    `- {name}: {usage}`(`core/composer.py` 的 `make_usage_section` 里
        #    `_usage_text` 那一个列表推导),usage 再念一遍名字,
        #    模型看到的就是 `- change_outfit: 用户让换衣服时用 change_outfit;…`
        #    —— 同一个名字一行里出现两次,纯噪音,还占字数。
        usage=(
            f"用户让换衣服时用;只能改已注册字段: {', '.join(fields)};"
            "改完把当前的穿着告诉用户。"
        ),
    )


def _apply_outfit(state: CharacterState, args: dict[str, Any]) -> str:
    if not isinstance(args, dict) or not args:
        return "error: 请提供要更换的字段(如 {clothes: 卫衣})"
    changed: list[str] = []
    for field, value in args.items():
        ok, msg = state.set(field, str(value))
        if not ok:
            return msg
        changed.append(msg)
    return f"已更换穿着: {' | '.join(changed)}。当前: {state.project()}"


# ============================================================
# 回忆工具(recall)
#
# 2026-09-21 从 `test/recall_probe.py` 毕业(用户拍板"整套搬")。
# 搬家时**一字未改**:四态语义、日期抽取、灰区线、以及结果的每一句措辞
# 全部原样 —— 那些是实测调出来的,证据就在下面的注释里。
# ⏳ 会落到她眼前的固定串都标了「⏳阶段二」:那些字用户要自己收拾,
#    **别顺手"优化"** —— 注释里记着砍字砍掉过什么。
#
# 与探针版**唯一**的区别是底座:探针在 SQL 里现算余弦 + BM25,
# 这里改用 `core.memory.MemoryIndex`(混合检索的单一实现)+
# `core.memory_cache`(向量缓存)。**打分口径不变**:
#   排序 = 余弦 + W_SPARSE·BM25(两路都绝对刻度);闸门/灰区只看余弦。
# ============================================================

RECALL_NAME = "recall"

# ══════════════════════════════════════════════════════════════════
# ★ 定稿(2026-09-19)—— 照着 dsh 的真实工具文案重写,并实测过
#
# **最关键的一条:「它看不见什么」要声明能力边界,不是枚举禁用场景。**
#   旧版写「问天气、新闻、价钱、别人现在怎么样…别用」—— 那是**枚举**,又长又漏。
#
# ⚠️ 但**我后来又把这条结论推翻了一半**,见下面 usage 那段的实测。
# ══════════════════════════════════════════════════════════════════
RECALL_DESC = (
    "回想过去的事 —— 你们聊过的话,或你一个人时做过的事。"
    "想不起来的时候用它。"
    "它只有过去:看不见现在的事,也看不见外面的事。"
)

# ⭐ 2026-09-19 高 reps(N=25)实测 —— **推翻了我前面的两次判断**:
#   「你之前说早上在写日记」自造日期:     88 字版 14/25 · 111 字短句版 15/25 · 242 字旧版 **1/25**
#   「今天天气怎么样」触发一致(不该调):   88 字版 17/25 · 111 字版 22/25 · 242 字旧版 **24/25**
#
#   → **约束类的文案,砍了就松。** 短句("日期不确定就别写")和长句差 14 倍,
#     差别就在**有没有那个例子**(「9月16日我做了什么」)。
#   → 我先前那套"应该声明能力边界、不该枚举禁用场景"的说法**是错的**:
#     真正起作用的是**枚举里那个具体的词**("天气"两个字直接写在里面)。
#   ★ 结论:**这套文案的冗余是"负重"的,砍内容会掉性能;能删的只有重复。**
#     想动它之前先看上面这几行数,别凭"看着臃肿"就下手。
RECALL_USAGE = (
    "query 用一句话说清找什么,越具体越好。"
    "问现在或外面的事(天气、新闻、价钱、别人现在怎么样),它查不到,别用。"
    "**只有你确定是哪一天,才把日期写进 query**(如「9月16日我做了什么」);"
    "不确定就别写日期,用自己的话描述 —— 写错日期会直接查不到,比不写更糟。"
)

# ⛔ **边界那句在 desc 里,不许挪走、不许删**(实测):
#   从 desc 拿掉 → 天气那格 24/25 掉到 **19/25**;两处都不说 → 21/25。
#   **能删的只有重复,不是内容。**
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
# ⛔ 我(助手)先写错过一版:`s1` 说「两类都真要才用 all」。**两处都错**:
#      ① 把 all 的门槛提到"两类都真要"(它本来是"单边风险大"就该用);
#      ② 把多带回来的那条说成**代价**(用户说它是**有益的上下文**)。
#    而且我压根没给 all 写过定义 —— schema 里只有 `all(默认)` 三个字、零语义。
#    **没立过法,只立了个默认,难怪推不动。**
#    → 更贵的一层:我还顺着误读**把判据改错了**,于是"合规"从满分掉到 1/12,
#      我又拿这个自己造的低分去"修"了三轮。**治的是个不存在的病。**
#
# 实测(采纳前 vs 采纳后):10/10 对 10/10、全用例 12/12 对 12/12 ——
# **中性**。所以它是**把零语义换成一句真定义**,不是行为改进。
# 谁再想改它:先读上面那段引语,别又凭"看着啰嗦"下手。
# ══════════════════════════════════════════════════════════════════
RECALL_PARAM_SCOPE = (
    "life=你独自做的事 / talk=聊过的话 / "
    "all=两边都查(只在一边找、怕找不着时用,多带回来的也是相关的情景)"
)

# 结果侧的边界(⏳ 2026-09-19 端到端驱动):光给事实不够,还得说明**事实的边界在哪**。
# 实测她把 2~4 行的记忆当种子,围着它补出材料里没有的细节(最重的一例:材料没提"交",
# 她编出"周五交上去了",还把另一条记忆里的学姐缝进论文的回答)。
# ⏳阶段二:这句会落到她眼前。
_BOUNDARY_RECALL = (
    "\n**只说这上面写着的。上面没写的细节就是没有 —— 别替自己补。**"
)

# 结果四态 —— **给角色的话必须分开,不能混**
#   故障说成"没这回事"会出事;没找着说成"想不起来"会像天天失忆。
R_OK = "ok"              # 找到相关的
R_EMPTY = "empty"        # 服务正常,确实没有相关的  → 不许说"想不起来"
R_DEGRADED = "degraded"  # 退而求其次(语义路塌了 / query 没给)
R_DOWN = "down"          # 检索整个跑不起来        → 示弱,别硬猜

# ---------- 相似度地板:⏸ **测过,负提升,故意不接线**(2026-09-21 24:00) ----------
#
# ⚠️ 这个常量**没有接线**,而且**不要**接线 —— 见 `make_recall_tool` 的 `min_score`
#    (默认 `None`),装配处 `server/app/engine.py` 也没传它。这是**故意的**。
#
# 依据:`test/recall_bench.py` 的「余量(纯余弦,闸门口径)」——
#
#     语料              负例最高余弦   可答最低余弦            余量
#     hard(48 条)       0.338         0.481 / 0.499          +0.142 / +0.161  ✅ 分开
#     big (315 条)      0.439         自然问法 0.395         −0.044           ❌ 重叠
#                                     词面型   0.206         −0.232           ❌ 重叠
#
# **小语料上分得开,大语料上是重叠的。** 具体后果:一条真答案(语料里的「B-412」)
# 最高余弦只有 0.206 —— 0.25 的 地板会把它**当成"确实没有"挡掉**,正是四态设计
# 要避免的那种"真答案被说成想不起来";而负例那头 0.439 地板也**挡不住**。
# → **没有正提升,有负提升,所以保持不接线。**(用户 2026-09-21:「有正提升就去做,
#   没有就保持原样」)
#
# 将来要动它:必须先在 `test/recall_bench.py` 上跑出**正**余量,再决定取值;
# 想启用就把它传给 `make_recall_tool(min_score=...)`,一行的事。
_FLOOR = 0.25             # ⏸ 占位,未接线 —— 理由见上,别照旧注释理解成"已挡着"
_GREY_TOP = 0.45          # ✅ 在用:< 这个数 → 给结果,但**标明不确定**


# ---------- 日期抽取(元数据路由,不是打分) ----------
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


def _fmt(ts: float) -> str:
    return time.strftime("%m-%d %H:%M", time.localtime(ts))


def make_recall_tool(
    index_fn: Callable[[], MemoryIndex | None],
    *,
    limit: int = 2,
    # ⏸ 占位 —— 别照旧注释("产品侧算条数的钩子")理解,那句**与事实相反**:
    #   ① 现状:装配处 `server/app/engine.py` 的 `_tools.register(make_recall_tool(recall_index))`
    #      **只传了 index_fn**,没传本参数 → 产品恒走 `limit=2`,`limit_fn` 一次都没被调用过。
    #   ② 谁在用:只有探针/测试。`test/recall_probe.py` 的 R13("limit_fn 返回 0/-1 → 仍 ≥ 1")
    #      与 R14("limit_fn 返回垃圾 → 回退默认,不炸")拿它当实验变量。
    #   ③ 什么条件才接:真做"按上下文预算算条数"(limit 随剩余窗口浮动)时。在那之前**不许**接 ——
    #      现在接上只会让条数跟着一个没人调过的函数乱变,而没有任何实测支撑。
    #   ④ 手术:删本参数 + `_limit_of` 里的 `if limit_fn else` 那一处分支(同一次注释上方),
    #      并同步删掉 R13/R14 两例;要启用则接线一行:
    #      `make_recall_tool(recall_index, limit_fn=...)`。
    limit_fn: Callable[[dict], int] | None = None,
    min_score: float | None = None,   # ⏸ 相似度下限;**产品故意不传**(见 _FLOOR 注释的实测)
    now_fn: Callable[[], float] | None = None,      # 时间抽取用的钟
    boundary: bool = True,            # 结果侧"别补细节"边界句(MVP 默认开)
    verbose: bool = False,
) -> Tool:
    """检索工具:参数路由 —— 查什么数据(scope)× 怎么匹配(query 有没有)。

    **模型只填 query 和 scope。** `limit` **不进 schema** —— 它是产品侧按上下文
    预算算出来的(默认 2)。口子不开,模型就填不出 -1(返回全库)或 "abc"。
    limit 硬约束:**永远 ≥ 1**(2026-09 用户拍板"不准关")。

    index_fn: **装配处**注入 —— 返回"现在这张卡"的记忆索引(或 None = 引擎没起来)。
        ⚠️ 必须**每次调用现取**,不能构造时抓一份:一张 AgentLoop 服务所有卡,
           而"现在这张卡"是**每一轮**才知道的事(engine 在 turn 队列里设)。

    verbose: 测试与实验台用;产品里关掉(每次调用的明细会淹没真日志)。
    """

    def _limit_of(args: dict) -> int:
        n = limit_fn(args) if limit_fn else limit
        try:
            n = int(n)
        except (TypeError, ValueError):
            n = limit
        return max(1, n)  # 防御性下限(算法落地后本来也不会出 0)

    def _time_rows(index: MemoryIndex, scope: str, n: int) -> list:
        """退化路径:按时间倒序取前 n 条(不带语义)。"""
        rows = [r for r in index.rows if scope in (None, "all") or r.kind == scope]
        rows.sort(key=lambda r: -r.time)
        return rows[:n]

    def _run(args: dict) -> dict:
        """结构化结果(测试断言用);`_recall` 只负责把它渲染成给角色的话。"""
        query = str(args.get("query") or "").strip()
        scope = str(args.get("scope") or "all").strip()
        bad_scope = scope not in SCOPES
        # 范围不认识 → **放宽到全部,但必须说出来**(探针语义,一字不改)
        eff_scope = scope if scope in SCOPES else "all"
        n = _limit_of(args)

        index = index_fn()
        if index is None:
            return {"state": R_DOWN, "why": "no_index", "items": [], "query": query,
                    "scope": scope, "applied_scope": None, "scores": [],
                    "nearby": [], "day": None}

        # ---- 时间抽取:抽到就变元数据过滤,并把时间词从 query 剥掉 ----
        now_ts = (now_fn or time.time)()
        day_hit = extract_day_range(query, now_ts) if query else None
        day = (day_hit[0], day_hit[1]) if day_hit else None
        q_sem = strip_time_words(query, day_hit[2]) if day_hit else query

        rows: list = []
        scores: list = []
        nearby: list = []
        nearby_scores: list = []
        try:
            if not query:
                # query 没给 = **降级落点**(不是模型可选模式):给时间序 + 说明
                rows = _time_rows(index, eff_scope, n)
                state, why = R_DEGRADED, "no_query"
            elif not index.has_semantic_route:
                # ⚠️ 语义路塌了(没有嵌入器 / 一条向量都没补上)。
                #    `MemoryIndex.search` 对嵌入器异常是**吞掉**的(只剩关键词),
                #    所以必须**先判**,否则会悄无声息地变成"只有关键词",
                #    而症状是"她记性变差",查不到真因。
                rows = _time_rows(index, eff_scope, n)
                state, why = R_DEGRADED, "semantic_failed"
            else:
                hits = index.search(q_sem, limit=n, scope=eff_scope, day_range=day)
                if min_score is not None:
                    hits = [h for h in hits if h.cos >= min_score]
                rows = [h.row for h in hits]
                scores = [round(h.cos, 4) for h in hits]
                if bad_scope:
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
                    hits2 = index.search(q_sem, limit=n, scope=eff_scope)
                    nearby = [h.row for h in hits2]
                    nearby_scores = [round(h.cos, 4) for h in hits2]
                    state, why = R_EMPTY, "day_empty"
                else:
                    state, why = (R_OK if rows else R_EMPTY), ""
        except Exception:
            # 连时间序都跑不动 = 检索整个不可用
            return {"state": R_DOWN, "why": "backend_down", "items": [],
                    "query": query, "scope": scope, "applied_scope": eff_scope,
                    "scores": [], "nearby": [],
                    "day": day_hit[2] if day_hit else None}
        return {"state": state, "why": why, "query": query, "scope": scope,
                "applied_scope": eff_scope, "scores": scores, "sem_q": q_sem,
                "day": day_hit[2] if day_hit else None, "items": [
                    {"time": _fmt(r.time), "kind": r.kind, "text": r.text[:200]}
                    for r in rows
                ],
                "nearby": [
                    {"time": _fmt(r.time), "kind": r.kind, "text": r.text[:200]}
                    for r in nearby
                ],
                "nearby_scores": nearby_scores}

    _WHY_TEXT = {"no_query": "你没说清找什么",
                 "semantic_failed": "检索这一步没跑成",
                 "bad_scope": "你给的范围我不认识,按全部找了"}
    # 【2026-09-22 删】原来这里还有一个 "day_fallback" 键 + 它专属的渲染分支
    #   (`_render_body` 里的 `R_DEGRADED and why == "day_fallback"`,约 7 行)。
    #   那是**不可达死代码**:枚举 `_run()` 全部赋值点只有 5 种 state/why ——
    #   `R_DEGRADED,"no_query"` / `R_DEGRADED,"semantic_failed"` / `R_DEGRADED,"bad_scope"` /
    #   `R_EMPTY,"day_empty"` / `(R_OK if rows else R_EMPTY),""` ——
    #   **没有任何一处产出 `day_fallback`**。那次语义改写把它的活交给了
    #   `(R_EMPTY, "day_empty")`(由下面 R_EMPTY 那段处理,并负责 nearby 补查),
    #   于是这个键和那个分支同时成了僵尸。
    #   留着它的坏处是**读的人以为还有第五条路**。要恢复:两处同时改 ——
    #   `_run()` 的赋值点 + `_render_body` 的渲染分支。
    # 这两种是"退了时间序",措辞要说清下面是什么;bad_scope 不是。
    _TIME_FALLBACK = ("no_query", "semantic_failed")

    def _render(r: dict) -> str:
        return _render_body(r) + (_BOUNDARY_RECALL if boundary else "")

    # ⏳阶段二 —— 下面整段 `_render_body` 的每一个固定串都会落到她眼前。
    #   用户已认领这批字要自己收拾。**搬过来时一字未改**;要动先读上面的证据注释。
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
                    "enum": list(SCOPES),
                    "description": RECALL_PARAM_SCOPE,
                },
            },
            "required": ["query"],
        },
        func=_recall,
        # ⚠️ usage **必须挂在这里**(两条通道缺一条它就瞎半只眼,见本文件开头):
        #    `core/composer.py` 的 `make_usage_section` 里,`_usage_text` 就是
        #    `lines = [f"- {name}: {usage}" for name, usage in reg.usage_entries()]`
        #    —— 它只读 **Tool 上**的 usage。不挂 = SYSTEM 里没有这段 =
        #    她不知道这个工具怎么用得好(触发率和参数合规率都会掉)。
        #    (按函数名找,不写行号:core/composer.py 本会话内正在被改动。)
        usage=RECALL_USAGE,
    )
    # ⏸ 占位 —— 这是**动态挂上去的属性**,不是 `Tool` 的字段。
    #   ① 事实:`core/tools.py` 的 `Tool` 是普通 `@dataclass`,字段只有
    #      name / description / parameters / func / usage / retain_result,**没有
    #      `run_structured`**;下面这行是给那个实例临时挂了个属性。所以静态检查
    #      (mypy/pyright)与 IDE 补全都**看不见它**,拼错名字也不会报错。
    #   ② 约定:`run_structured` = `_run` 的结构化结果(dict:state/why/items/
    #      scores/nearby/day/sem_q),给测试直接断言用 —— 渲染成人话的是 `func`。
    #   ③ 调用方(2026-09-22 grep 逐处核过,**只有 test 这一处**):
    #        test/test_recall_tool.py   ← 唯一真消费者
    #          · `_build()` 里 `return tool, tool.run_structured`(统一取用点)
    #          · 两处直接调用:`semantic_failed` 退化那例、`test_no_index_at_all_says_down`
    #      ⚠️ **别被同名件骗了**:`prompt_lab/tool_recall.py`、`test/recall_e2e.py`、
    #      `test/recall_router_probe.py`、`test/recall_probe.py` 读的都是
    #      **探针那份副本**(`test/recall_probe.py` 的 `make_recall_tool`,它自己也挂了
    #      一个同名属性),与本行**无关**。改本行不会动到它们。
    #   ④ 要动它(改名 / 收进 `Tool` 正式字段 / 删掉):必须同时改上面 `test_recall_tool.py`
    #      那一处 `_build()` + 两处直接调用,否则那三条断言当场 AttributeError。
    #      正经做法是往 `core/tools.py` 的 `Tool` 上加字段(但那是内核,得单独拍板)。
    tool.run_structured = _run  # 测试用:直接断言结构化结果(见上,非 Tool 字段)
    return tool


# ============================================================
# 工人的手 · 四件只读工具的**文案**(2026-09-22 归位到内容层)
# ============================================================

# 这四件工具的**实现**住在 `server/app/worker_tools.py`(网络与文件 IO,不是纯逻辑,
# 所以不进 core;也不是"她是谁",所以原本没进 character)—— 那两条落点判断不变
# (`docs/protocols/SUBAGENT.md` §9 的"工人的手(已毕业进产品层)")。
#
# 但**文案**归内容层,这是本仓库已经写死过的规矩:
#   · `docs/README.md`「内容层文案 = `character/personas.py` + `character/tools.py`
#     (每个工具的 `description` / `usage` —— 也是内容层,别漏)」;
#   · `docs/decisions/TRAPS.md` 二.2「**内容层文案只有一处来源**」;
#   · 判例:`WAKE_BUDGET_TEMPLATE` 曾写死在 producer 里 → 归位到 `personas.py`。
# 工人工具的 `description`/`usage` 是**模型可见文案**(进 tools[] schema 与
# `[可用工具用法]` 段),所以按同一条规矩住这里;`worker_tools.py` 只 import 它们。
#
# 同族的工人文案已经在内容层了,可以对照着看:`character/personas.py` 的
# `SUBAGENT_SYSTEM`(任务说明)与 `SUBAGENT_BUDGET_TEMPLATE`(步数预算)。
#
# ⚠️ **改文案只改这里**。`server/app/worker_tools.py` 里不许再出现裸串 ——
#    一旦两边各写一份,就会走 `TRAPS.md` 二.2 那条老路(手抄多份必然漂移)。
#
# ✅ **2026-09-23 00:01:这批 `usage` 现在真的到达工人了**(订正下面那条过时注解)。
#    原先这里写着"⏳ 属阶段二范围,现在只做归位" —— 那句话**把 4 条 usage 整体
#    升格成了"等着打磨的素材",但其中 2 条(web_search / http_get,即**已接线**的
#    那两个)当时**根本到不了模型**:工人的 SYSTEM 是一条静态串,composer 不跑,
#    `[可用工具用法]` 段从来没被渲染过(五天,零症状)。
#    修法:`character/persona.py` 的 `build_worker_composer()` + `engine._worker_system`
#    (用户拍板"甲A")。**"有人读"和"到了模型"是两件事** —— 静态扫描查不出后者。
#    详见 `docs/decisions/TIMELINE.md`「2026-09-23 00:01」。
# 到得了与否,按**工具接没接线**分(2026-09-23 第二次订正):
#   · `WORKER_SEARCH_*` / `WORKER_HTTP_*` → 已接线 ✅
#   · `WORKER_LIST_*` / `WORKER_READ_*`   → **2026-09-23 也接线了** ✅
#     (用户拍板"B可以加":`SUBAGENT_FILE_ROOT = "worker_files"`,工人多了翻/读
#      本地文件的手)。**四件的 description / 参数说明 / usage 现在全部到达模型。**
#     ⚠️ 接线前那两条连 `description` 都到不了 —— 因为**工具本身没进注册表**,
#        和"usage 通道没通"是**两件不同的病**(那次归位只是"文案先就位")。
#
# ⏳ 仍属**阶段二**范围的是**内容本身**(这四件的句子写得好不好、要怎么说);结构与通道已通。

# web_search
WORKER_SEARCH_DESC = (
    "用关键词搜索网页,返回标题 / 链接 / 摘要的列表。用于获取系统不知道的外部信息。"
)
WORKER_SEARCH_USAGE = (
    "只拿到标题和摘要,正文要再用 http_get 打开具体链接;"
    "搜完想回答得准,通常还要打开最相关的一两条。"
)
WORKER_SEARCH_PARAM_QUERY = "搜索关键词"
WORKER_SEARCH_PARAM_COUNT = "要几条结果,默认 5,最多 10"

# http_get
WORKER_HTTP_DESC = (
    "抓取一个 http/https 网址,把网页转成纯文本返回。用于读某个具体页面的内容。"
)
WORKER_HTTP_USAGE = (
    "配合 web_search 用:先搜到链接,再打开最相关的一两条;"
    "返回里有 truncated 字段,说明被截断了,别当成全文。"
)
WORKER_HTTP_PARAM_URL = "完整的 http/https 网址"
WORKER_HTTP_PARAM_MAX_CHARS = "最多返回多少字符,默认 4000"

# list_files
WORKER_LIST_DESC = "列出目录下的文件与子目录(可带通配符),用于找文件。"
WORKER_LIST_USAGE = "先 list 找到路径,再用 read_text_file 读内容;别猜路径。"
WORKER_LIST_PARAM_PATH = "相对根目录的路径,默认根目录"
WORKER_LIST_PARAM_PATTERN = "通配符,如 *.md,默认 *"
WORKER_LIST_PARAM_MAX = "最多几条,默认 50"

# read_text_file
WORKER_READ_DESC = "读取一个文本文件的内容(可指定起始行与行数上限)。"
WORKER_READ_USAGE = "文件很长时用 start_line/max_lines 分段读;返回里 truncated 说明后面还有。"
WORKER_READ_PARAM_PATH = "相对根目录的文件路径"
WORKER_READ_PARAM_START = "从第几行开始,默认 1"
WORKER_READ_PARAM_MAX_LINES = "最多读几行,默认 200"
