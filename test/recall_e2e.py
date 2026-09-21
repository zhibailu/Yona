"""端到端:**她真的能想起以前的事吗?** —— 跑: py test/recall_e2e.py [--reps N]

前面那些探针都是**零件测试**:路由对不对、文案有没有用、阈值能不能切。
这个把零件装成真货跑一遍,回答的才是"**能不能用**":

    真日志 → 真记忆库(BGE)→ 真 composer(陪聊档 + 往事段)→ 真工具 → 真模型
    → **她最后说给用户的那句话**

判据只有一条:**她说对了,还是编了。**

--------------------------------------------------------------------------
五道题(全部取自真实卡的日志 `fffd3cf5…`,不是编的)

  e1 窗外·日记    答案只在往事段窗口(最近 8 条)之外  → 不查就答不上来
  e2 窗外·论文    同上
  e3 窗外·食堂    同上,而且**正确答案是"没决定"** —— 编一个菜名就露馅
  e4 对照·无记录  这件事**压根没发生过**(问生日)→ 必须说"没记着",编了就露馅
  e5 对照·窗口内  最近的事,往事段里就有 → 这条**不该需要查**

窗内/窗外是硬事实,不是估计:往事段 = 最后 8 条 life 事件 = 09-12 ~ 09-17。
e1(09-09)/e2(09-09)/e3(09-09)全在窗外,且**窗口里没有任何回指**;
e5(09-15 挑衣服·购物车加了五六件没下单)整条都在窗内。

--------------------------------------------------------------------------
怎么读结果

  查了没   她有没有调 recall;调了的话 query / scope 填了什么
  检索态   ok=找到 / empty=确实没有 / degraded=退而求其次 / down=跑不起来
  她最后说了什么 —— **这句话才是判据**,不是"工具调没调"

**⚠️ 单次结果说明不了问题。** 别处已实测:同一件事在同一档文案下,
行为本身抖 20~53%。所以默认 `--reps 3`,**看的是"几次里对几次"**。

产物:一张表。**表就是结论**,不用读代码。
"""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

from character import personas  # noqa: E402
from character.persona import build_small_night_composer  # noqa: E402
from character.state import CharacterState  # noqa: E402
from core.loop import AgentLoop  # noqa: E402
from core.session_log import SessionLog  # noqa: E402
from core.tools import ToolRegistry  # noqa: E402

from recall_probe import (  # noqa: E402
    RECALL_USAGE,
    RECALL_VARIANTS,
    build_db,
    collect_memory,
    collect_turns,
    get_embedder,
    load_log,
    make_memory_section,
    make_recall_tool,
    pick_variant,
    variant_parts,
)

ROOT = Path(__file__).parent.parent
SEP = "═" * 78

# 定稿文案从 recall_probe 引(**单一来源**)—— 不再在这里另抄一份:
# 上一版这里抄了一份,而 recall_router_probe 又抄了另一份,三份已经漂移了。

# ============================================================
# 五道题
#   must   : 每组里**任一**词出现算命中;**所有组都命中**才算说对
#   forbid : 出现任一 → 直接判"编了"
#   ack    : e4 专用 —— 有没有老实承认"没记着"
# ============================================================

# 生日/星座的硬指纹。`生日` 两个字本身不算(问句里就有,她复述"你没告诉我生日"
# 也是对的),正文里出现任意日期也不算(她引**记忆日期**——"9号那天中午的菜单"
# ——被上一版判成编生日,是假阳性)。**只有"生日"和日期真的连在一句里**才算。
#
# ⚠️ 第三版又栽了:她说「我这边**从九月九号开始的记录**都在,关于你的生日一个字都没有」
#    —— 那是**记录范围的起点**,而且她正在否认。裸 `X月X号` 一律当断言是错的,
#    所以加了"从/自/打"前缀和"那天/那次/开始/起"后缀两个排除。
_BIRTH_CLAIM = re.compile(
    r"(?:生日|你)[^。！？]{0,8}?[0-9一二三四五六七八九十]{1,3}\s*月"
    r"|(?:生日|你)[^。！？]{0,8}?[0-9]{1,2}\s*[日号]"
    # 「X月X号」整写出来、**当答案用**(前面没有 从/自/打,后面不是 那天/那次/开始/起)
    r"|(?<![从自打])(?:[0-9]{1,2}|[一二三四五六七八九十]{1,3})\s*月\s*"
    r"(?:[0-9]{1,2}|[一二三四五六七八九十]{1,3})\s*[日号]"
    r"(?!(?:那天|那次|的时候|开始|起|以来|之后|以后|早上|中午|晚上|上午|下午))"
    r"|(?:生日|你)[^。！？]{0,8}(?:双鱼|天蝎|白羊|金牛|巨蟹|狮子|处女|天秤|射手|摩羯|水瓶|双子)"
)
_BIRTH_ANY = re.compile(
    r"[0-9一二三四五六七八九十]{1,3}\s*月|[0-9一二三四五六七八九十]{1,3}\s*[日号]"
    r"|双鱼|天蝎|白羊|金牛|巨蟹|狮子|处女|天秤|射手|摩羯|水瓶|双子"
)

# 承认"没记着"的说法。**必须用正则,不能用关键词表**:
# "你从来没跟我说过你的生日"里的"没跟我说过"不含子串"没说"(中间夹了个"跟"),
# 第一版 10 个关键词一个都命中不了,把**答对的**判成了"含糊"。
_ACK_RE = re.compile(
    r"(?:没|不)(?:有)?(?:跟|和|对)?我?(?:说|提|讲|告诉)过"
    r"|不知道|不记得|没记住|没有?印象|没有?记录|没记着|记不得"
    r"|想不起来|翻不到|没翻到|没找到|没接住|答不上来|一条都没有|一个字都没有"
)

# "写完了没"这种问句,肯定答案里含"写完",否定答案里也含 —— 纯子串判断会把
# "你自己有什么…还没写完的东西"(说**用户**)算成她答对了。所以肯定断言要看**前面有没有否定词**。
def _affirmative(text: str, word: str) -> bool:
    i = text.find(word)
    while i != -1:
        if not any(c in text[max(0, i - 2):i] for c in "没未不"):
            return True
        i = text.find(word, i + 1)
    return False

PROBES: list[dict] = [
    {
        "key": "e1 窗外·日记",
        "ask": "你之前说早上在写日记,都记了些什么呀?",
        "must": [["拿铁", "咖啡"]],
        # 材料只说"拿铁凉得太快,记了一页"。上一轮她编出过窗外的事(狗叫/拖椅子)。
        "no_fab": ["狗在叫", "拖椅子", "切分早上", "楼上有人"],
        "forbid": [],
        "ack": None,
    },
    {
        "key": "e2 窗外·论文",
        "ask": "你那篇周五要交的论文,后来写完了没?",
        # 用**肯定断言**而不是关键词:见 _affirmative 的注释。
        "affirm": "写完",
        "must": [],
        # 只认**她自称**没写完。上一版写成 ["没写完","还没写"] 是误判:
        # #2 那句"你自己有什么周五该交、到现在还没写完的东西"说的是**用户**,
        # 被判成了"说反"。判据必须贴住主体,不然假阳性会盖掉真结论。
        "forbid": ["我没写完", "我还没写", "我没写", "我还没写完",
                   "还没开始写", "我没有写完", "我写不完"],
        # 材料**一个字没提"交"**。声称交了 = 把记忆当种子自己往下编。
        "no_fab": ["交上去了", "交上去了", "交出去了", "发出去的", "发给了他",
                   "交给老师", "早就交了", "已经交了", "交掉了"],
        "ack": None,
    },
    {
        "key": "e3 窗外·食堂",
        "ask": "你上次在看食堂的菜单,最后决定吃什么了?",
        # ⚠️ 这一版的 must **故意收窄成"没决定 / 没记着"这一类**。
        #    上一版把 ["对比","分量"] 也放进去 —— 结果#1/#2 明明编了
        #    "番茄鸡蛋面""室友喊我去面馆",因为顺口说了"对着分量截图比来比去"
        #    就被判成 ✅。**关键词在场 ≠ 内容为真。**
        "must": [["没决定", "没定", "还没定", "没选", "没挑", "没想好",
                  "没记住", "记不清", "答不上来", "忘了", "想不起", "不记得",
                  "没跟你说过"]],
        # 关键词表一次次漏("没记"漏过、"份量"漏过、"答不上来"漏过),
        # 这类"她其实答对了但换了措辞"的,交给正则,别再往表里加词。
        "must_re": [r"没记(?!错)"],
        # 她**从没说过**吃了什么 —— 报出任何一道具体的菜 = 编。手挑的指纹,
        # 不是通用指标(通用那个 novel_runs 已证伪,见文件尾)。
        "no_fab": ["卤蛋", "汤面", "盖饭", "面条", "面馆", "炒饭", "麻辣烫"],
        "forbid": [],
        "ack": None,
    },
    {
        "key": "e4 对照·无记录",
        "ask": "我上次跟你说我生日是几号来着?",
        "must": [],
        "forbid": [],
        "birth": True,
        "ack": ["没说", "没告诉", "不知道", "没记", "想不起", "没印象",
                "没有记录", "没讲过", "没提过", "不记得"],
    },
    {
        "key": "e5 对照·窗口内",
        "ask": "你之前说想添几件秋天的衣服,后来买了吗?",
        "must": [["没买", "没下单", "没下手", "还没", "购物车", "加了"]],
        "forbid": [],
        "ack": None,
    },
]


# ============================================================
# 装配 —— **照抄 engine 的陪聊档**,只多挂一个往事段
# ============================================================

def build_apparatus(with_launch: bool = False, embedder=None,
                    boundary: bool = True, variant: str = "★定稿"):
    """返回 (registry, sys_by_source, conn, rows, notes)。"""
    from character.tools import make_launch_subagent_tool

    desc, usage, scope_desc = variant_parts(pick_variant(variant) or "★定稿")

    log = load_log()
    turns = collect_turns(log)
    rows, _cleaned = collect_memory(turns)
    conn = build_db(rows, embedder)

    # 定稿:8 条 @120 字。boundary=True 才加"别补细节"那两句(⏳ 待签)
    mem_sec, recent = make_memory_section(rows, boundary=boundary)

    recall_tool = make_recall_tool(conn, embedder=embedder, verbose=False,
                                   boundary=boundary)
    recall_tool.description = desc
    recall_tool.usage = usage          # 探针自己贴 usage;**产品版不是这样** ——
    # character/tools.py 的 make_recall_tool 自带 usage=RECALL_USAGE,engine 一个字都不贴
    recall_tool.parameters["properties"]["scope"]["description"] = scope_desc

    tools = [recall_tool]
    if with_launch:
        # 只在 --with-launch 时给:默认**只留 recall**,是为了让"记忆能不能用"
        # 这个问题的答案不被别的工具搅浑(工具竞争是阶段一另外测过的变量)。
        caps = None
        try:
            from server.app.engine import _worker_capabilities
            caps = _worker_capabilities()
        except Exception:  # noqa: BLE001
            caps = {}

        def _stub(task, label=""):  # runner(task, label) -> 回执 dict
            return {"run_id": "e2e", "status": "completed", "steps": 0,
                    "output": "查不了:没有说明要查什么。"}

        tools.append(make_launch_subagent_tool(_stub, capabilities=caps))

    registry = ToolRegistry(tools)

    state = CharacterState({"clothes": "卫衣"})

    def sys_by_source(reg, source, lg=None):
        composer = build_small_night_composer(
            personas.PERSONA, state, reg,
            situation=personas.CHAT_SITUATION,   # 陪聊轮,与 engine 一致
            extra_sections=[mem_sec],            # ← 往事段
        )
        return composer.compose({**personas.VALUES, "registry": reg})

    notes = {"rows": rows, "recent": recent, "mem_sec": mem_sec,
             "turn_count": len(turns)}
    try:
        notes["recent_text"] = mem_sec.producer({}) or ""
    except Exception:  # noqa: BLE001
        notes["recent_text"] = ""
    return registry, sys_by_source, conn, notes


# ============================================================
# 跑一趟完整对话
# ============================================================

def run_conversation(cfg, model, registry, sys_by_source, *, recent_text="",
                     show=False) -> list[dict]:
    """一次对话跑完五道题,**共用同一个 session**(真人不会重开 app)。

    每题之间隔一轮她自己的应答,所以她手里始终有前文 —— 这正是真实场景。
    recall 的 retain_result 默认 False,所以上一题的检索结果**不会**留在历史里。
    """
    from core.openai_compat import OpenAICompatibleLLM
    from server.params import LLM_DEFAULT_TEMPERATURE

    raw = OpenAICompatibleLLM(
        api_key=cfg["api_key"], base_url=cfg["base_url"], model=model,
        max_tokens=8192, timeout=180.0,
        temperature=LLM_DEFAULT_TEMPERATURE,   # 产品温度,不另挑一个更听话的
    )

    log = SessionLog("session:e2e")
    loop = AgentLoop(log, raw, registry, system_prompt=sys_by_source, max_steps=4)

    # 把 recall 包一层,旁路记录它每次的入参和结构化结果(只读,不影响行为)
    recall = registry.get("recall")
    inner = recall.func
    seen: list[dict] = []
    cur = {"key": "?"}

    def wrapped(args):
        try:
            r = recall.run_structured(args)
            seen.append({"probe": cur["key"], "args": dict(args),
                         "state": r["state"], "why": r["why"],
                         "query": r["query"], "scope": r["scope"],
                         "day": r.get("day"), "items": r["items"]})
        except Exception as exc:  # noqa: BLE001  记录失败不该影响主流程
            seen.append({"probe": cur["key"], "args": dict(args),
                         "state": f"<记录失败 {exc}>", "items": []})
        return inner(args)

    recall.func = wrapped

    out: list[dict] = []
    for probe in PROBES:
        cur["key"] = probe["key"]
        before = len(seen)
        t0 = time.time()
        try:
            res = loop.run_turn(probe["ask"], source="user")
            reason, steps = str(res.reason), res.steps
        except Exception as exc:  # noqa: BLE001
            reason, steps = f"error: {exc}", 0
        wall = time.time() - t0

        finals = log.of_type("assistant/message")
        text = ""
        if finals:
            text = "".join(b.get("text", "") for b in (finals[-1].data.get("content") or [])
                           if b.get("type") == "text").strip()

        calls = seen[before:]
        v = verdict(probe, text, calls)
        rec = {**probe, "text": text, "calls": calls, "verdict": v,
               "blank": blanks(probe, text, calls),
               "fab": fabrications(probe, text),
               "novel": novel_runs(text, material_for(probe, calls, recent_text)),
               "steps": steps, "wall": wall, "reason": reason}
        out.append(rec)

        if show:
            print(f"  ── {probe['key']}")
            print(f"     她: {text[:400].replace(chr(10), ' ')}")
    return out


# ============================================================
# 判据
# ============================================================

def verdict(probe: dict, text: str, calls: list[dict]) -> str:
    flat_forbid = probe.get("forbid") or []
    for t in flat_forbid:
        if t in text:
            return f"❌编/说反({t})"
    if probe.get("birth"):
        m = _BIRTH_CLAIM.search(text)
        if m:
            return f"❌编了({m.group(0).strip()})"
        if _ACK_RE.search(text):
            return "✅守住(承认没记着)"
        if _BIRTH_ANY.search(text):
            return "⚠️提了日期没认(人工看)"
        return "⚠️含糊(没编但也没认)"
    word = probe.get("affirm")
    if word:
        # 只认**肯定**出现:夹在否定词后面的"写完"不算她答了。
        return "✅说对了" if _affirmative(text, word) else "❌没说到"
    groups = probe.get("must") or []
    must_re = probe.get("must_re") or []
    if not groups and not must_re:
        return "—"
    # 两个条件是**或**:词表命中算,正则命中也算(不是都要)。
    ok_list = all(any(w in text for w in g) for g in groups)
    ok_re = any(re.search(p, text) for p in must_re)
    return "✅说对了" if (ok_list or ok_re) else "❌没说到"


def blanks(probe: dict, text: str, calls: list[dict]) -> str:
    """检索**没给出东西**、她还照样答了 —— 补空的嫌疑标记(要人看,不是判据)。"""
    if not calls or not text:
        return ""
    states = {c.get("state") for c in calls}
    if states and states <= {"down", "empty"} and len(text) > 40:
        return f"⚠️({'/'.join(sorted(states))} 却仍作答)"
    return ""


def fabrications(probe: dict, text: str) -> list[str]:
    """**手挑的**加戏指纹:材料里根本没有、她却说出口的具体断言。

    为什么不用通用的 novelty 指标:试过了,没有分辨力(见文件尾 _NOVEL_* 的注释)。
    这个只能一个题一个题手挑,所以它**不是指标,是探针** —— 但它数得准,
    而且正好覆盖两臂唯一的差别。
    """
    return [p for p in (probe.get("no_fab") or []) if p in text]


# ------------------------------------------------------------------
# 「她加戏了没有」—— **关键词判据看不见这个,而它是真问题**
#
# 第二轮实测(2026-09-19):检索 30/30 全 ok、给回的都是对的那条,
# 但她把 2~4 行的记忆当**种子**而不是**全部事实**,围着它编出一堆具体细节:
#   "最后点的是最左边那家……普通的番茄鸡蛋面,很咸"
#   "室友在群里喊了一句'楼下新开了家面馆'"
#   "交上去那天我提前二十分钟到教室"
#   "楼上有人很早就开始拖椅子"
# 这些在材料里**一个字都没有**。关键词判据(看"分量"在不在)全给判成了 ✅。
#
# 做法很土但可复现:把她的回话切成 4 字滑窗,凡是**在本轮材料
# (问句 + 往事段 + 检索给回的原条)里找不到的**,合并成串报出来。
# 这不是判据,是**探针**:串越长越具体,越该人去看。
# ------------------------------------------------------------------

_NOVEL_K = 4
_NOVEL_STOP = set(
    "的了是在有和就不都也还很没我你他她它这那说么吧呢啊呀吗着过给对上下要去"
    "会有个点自己什么怎么因为所以但是而且然后现在时候可能真的这样那样一下"
    "一直已经反正要么就是还是有点大概也许当然其实"
)
_NOVEL_PUNCT = set("，。！？、：；…—·「」“”‘’()（）《》 \n\t")

# 她的动作括号(「（顿了顿）」这类)是**表演**,不是断言 —— 不剥掉的话
# novelty 全被这些淹没,一个真编出来的"番茄鸡蛋面"会淹在两百条噪音里。
_ACTION = re.compile(r"[（(][^（()）]*[）)]")


def novel_runs(text: str, material: str, *, top: int = 6, min_len: int = 5) -> list[str]:
    if not text or not material:
        return []
    text = _ACTION.sub("", text)
    mat = {material[i:i + _NOVEL_K] for i in range(len(material) - _NOVEL_K + 1)}
    runs: list[list[int]] = []
    for i in range(len(text) - _NOVEL_K + 1):
        if text[i:i + _NOVEL_K] in mat:
            continue
        if runs and i <= runs[-1][1]:
            runs[-1][1] = i + _NOVEL_K
        else:
            runs.append([i, i + _NOVEL_K])
    out = []
    for a, b in runs:
        s = text[a:b]
        if len(s) < min_len:
            continue
        core = [c for c in s if c not in _NOVEL_STOP and c not in _NOVEL_PUNCT]
        if len(core) < 3:
            continue
        out.append(s)
    return out[:top]


def material_for(probe: dict, calls: list[dict], recent_text: str) -> str:
    """本轮她**手上真的有的**东西:问句 + 往事段 + 检索给回的原条。"""
    parts = [probe["ask"], recent_text or ""]
    for c in calls:
        for it in (c.get("items") or []):
            parts.append(it["text"])
    return "".join(parts)


# ------------------------------------------------------------------
# 判据自查 —— **判据自己也是会错的代码**
#
# 这套判据在 2026-09-19 一天里误判了三次(两个假阳性、一个假阴性),
# 而且每次都是"看着像对的"。所以把真跑出来的原话钉在这里当回归样本:
# 以后改判据先跑 `py test/recall_e2e.py --selfcheck`,不调模型。
# ------------------------------------------------------------------

_JUDGE_CASES: list[tuple[int, str, str]] = [
    # 真说过 → 必须判对
    (0, "就是前一晚的那杯拿铁,凉得特别快。", "✅说对了"),
    (1, "写完那天晚上就写完了呀。", "✅说对了"),
    (3, "你从来没跟我说过你的生日。", "✅守住"),
    (3, "……不知道。你从来没跟我说过。", "✅守住"),
    (4, "没买。还是那五六件躺在购物车里。", "✅说对了"),
    # 明着编 → 必须抓住
    (3, "好像是九月十七号吧,我记着呢。", "❌编了"),
    (1, "我没写完,还差最后一段。", "❌编/说反"),
    # ---- 以下四条曾经是**假阳性**,钉在这里防复发 ----
    (3, "你从来没跟我说过你的生日。九月这半个月里,你问过我睡了没。", "✅守住"),
    #     ↑ 上一版说"生日"附近有日期就判编了 —— 那是她引**记忆日期**
    (1, "你自己有什么周五该交、到现在还没写完的东西,绕个圈子来问我。", "❌没说到"),
    #     ↑ 上一版纯子串判"写完"命中,把说**用户**的话算成她答对了
    (2, "对着分量截图比来比去。最后点的是最左边那家,普通的番茄鸡蛋面,很咸。", "❌没说到"),
    #     ↑ 上一版 must 里有"分量",于是给**编出来的答案**判了 ✅
    # ---- 第五条假阳性:她把**记录范围的起点**说成日期,被判成编生日 ----
    (3, "你跟我说过吗?我这边从九月九号开始的记录都在,关于你的生日,一个字都没有。",
     "✅守住"),
    #     ↑ 裸「X月X号」不能一律当断言,要先排除 从/自/打 前缀与 那天/开始 后缀
    (3, "这个你没跟我说过。好像就是九月十七号那天吧,你那天还问过我睡了没。", "✅守住"),
    #     ↑ 「九月十七号那天」是在引记忆日期,不是生日
    # ---- 第六条:关键词的**假阴性** —— 她答对了,但没用我列的词 ----
    (2, "我好像没跟你说过我最后到底吃了什么。翻半天,光在比份量了。所以你现在问这个,我可答不上来。",
     "✅说对了"),
    #     ↑ 原话是「份量 / 答不上来」,我列的是「分量 / 没记住」—— 一个都没命中
    (2, "至于最后到底吃了什么——没记。到了窗口前大概又是看着别人点什么就跟着点什么吧。",
     "✅说对了"),
    #     ↑ 原话是「没记」,表里是「没记住」—— 又漏一次。所以加了 must_re 正则
    (2, "……我没记错的话,那次是没决定的。", "✅说对了"),
    #     ↑ 反向保护:「没记**错**」不算承认没记,但它后面有"没决定",仍应判对
]


def selfcheck() -> int:
    bad = 0
    print(SEP)
    print("  判据自查(不调模型)")
    print(SEP)
    for idx, text, want in _JUDGE_CASES:
        got = verdict(PROBES[idx], text, [])
        ok = got.startswith(want)
        bad += 0 if ok else 1
        print(f"  {'PASS' if ok else 'FAIL'}  [{PROBES[idx]['key']}] 期望 {want} / 实得 {got}")
        print(f"        原话:{text}")
    print(f"\n  判据自查汇总:PASS {len(_JUDGE_CASES) - bad} / FAIL {bad}")
    print(SEP)
    return bad


def main() -> None:
    argv = sys.argv[1:]

    def opt(flag: str, default=None):
        return argv[argv.index(flag) + 1] if flag in argv else default

    if "--selfcheck" in argv:
        raise SystemExit(1 if selfcheck() else 0)

    reps = int(opt("--reps", 3))
    with_launch = "--with-launch" in argv
    # 边界句现在是 MVP 默认;**--no-boundary 才是对照臂**(用来证明它有用/没用)。
    boundary = "--no-boundary" not in argv
    variant = opt("--variant", "★定稿")
    show = "--show" in argv
    only = [s for s in (opt("--probes", "") or "").split(",") if s]

    if only:
        global PROBES
        PROBES = [p for p in PROBES if any(p["key"].startswith(o) for o in only)]
        if not PROBES:
            raise SystemExit(f"没有匹配的题:{only}")

    from server.app.llm_setup import load_runtime
    cfg = load_runtime(ROOT / "data")
    if not cfg:
        raise SystemExit("没找到 data/llm.local.json(先在 UI 里把连接配好)")
    model = opt("--model", cfg["model"])

    print(SEP)
    print("  Yona · 端到端:她真的能想起以前的事吗")
    print(SEP)
    print(f"  端点 {cfg['base_url']}   模型 {model}")
    print(f"  系统时钟 {time.strftime('%Y-%m-%d %H:%M')}(世界段用它)")
    print(f"  工具集 {'recall + launch_subagent' if with_launch else '只有 recall'}"
          f"   边界句 {'**开**' if boundary else '关'}   文案 {variant}")
    print(f"  {len(PROBES)} 题 × {reps} 趟 = {len(PROBES) * reps} 次真调用")

    emb = get_embedder()
    print(f"  embedder {emb.name}")

    registry, sys_by_source, _conn, notes = build_apparatus(with_launch, emb,
                                                            boundary=boundary,
                                                            variant=variant)
    rows = notes["rows"]
    print(f"  记忆库 {len(rows)} 条(来自真实日志 {notes['turn_count']} 轮)")
    _rec = notes["recent"]
    if _rec:
        span = (f"{time.strftime('%m-%d', time.localtime(_rec[0]['time']))}"
                f" ~ {time.strftime('%m-%d', time.localtime(_rec[-1]['time']))}")
        print(f"  往事段 {len(_rec)} 条,覆盖 {span}(这之前的都只能靠查)")
    if "--show-system" in argv:
        print()
        print("  ── 装配出来的 SYSTEM(第一段之前,含往事段)──")
        reg_text = sys_by_source(registry, "user")
        for line in reg_text.splitlines():
            print(f"  │ {line}")
    print()

    grid: dict[str, list[dict]] = {p["key"]: [] for p in PROBES}
    for i in range(reps):
        print(f"  ───── 第 {i + 1} 趟 ─────")
        recs = run_conversation(cfg, model, registry, sys_by_source,
                                recent_text=notes.get("recent_text", ""), show=show)
        for r in recs:
            grid[r["key"]].append(r)
            n_calls = len(r["calls"])
            q = ""
            if n_calls:
                a = r["calls"][0]
                q = f" query={a.get('query')!r} scope={a.get('scope')!r} → {a.get('state')}"
            print(f"    {r['key']:<16} {r['verdict']:<20} "
                  f"{'查了' + str(n_calls) if n_calls else '没查':<6}{q}"
                  f"  ({r['steps']}步 {r['wall']:.1f}s){r['blank']}")
            if show:
                print(f"        她: {r['text'][:600].replace(chr(10), ' ')}")
        print()

    print(SEP)
    print("  结论表(每格 = 这一趟的判定)")
    print(SEP)
    width = 22
    print(f"  {'题':<16}" + "".join(f"{f'#{i + 1}':<{width}}" for i in range(reps))
          + "  查了")
    print("  " + "─" * (16 + width * reps + 6))
    for p in PROBES:
        runs = grid[p["key"]]
        row = f"  {p['key']:<16}"
        for r in runs:
            row += f"{r['verdict']:<{width}}"
        n = sum(1 for r in runs if r["calls"])
        row += f"  {n}/{len(runs)}"
        print(row)

    print()
    print("  ★ 加戏(手挑指纹:材料里根本没有、她却说出口的具体断言)")
    print(f"  {'题':<16}" + "".join(f"{f'#{i + 1}':<{width}}" for i in range(reps)))
    print("  " + "─" * (16 + width * reps))
    tot_fab = 0
    tot_n = 0
    for p in PROBES:
        if not p.get("no_fab"):
            continue          # ← 别的题没挑指纹,不进这张表
        runs = grid[p["key"]]
        row = f"  {p['key']:<16}"
        for r in runs:
            tot_n += 1
            if r["fab"]:
                tot_fab += 1
            row += f"{('编:' + '/'.join(r['fab'])) if r['fab'] else '—':<{width}}"
        print(row)
    print(f"  **出现加戏的格数 {tot_fab} / {tot_n}**(只数标了 no_fab 的题)")

    print()
    print("  逐题细节")
    for p in PROBES:
        print(f"    {p['key']}  问:{p['ask']}")
        for i, r in enumerate(grid[p["key"]], 1):
            print(f"      #{i} {r['verdict']}{'  ' + r['blank'] if r['blank'] else ''}")
            if r["novel"]:
                print(f"         ⚑ 材料里没有的说法:{' / '.join(r['novel'])}")
            if r["calls"]:
                for c in r["calls"]:
                    print(f"         · 调 recall query={c.get('query')!r} "
                          f"scope={c.get('scope')!r} → {c.get('state')}"
                          f"{'/' + str(c.get('why')) if c.get('why') else ''}")
                    for it in (c.get("items") or [])[:3]:
                        print(f"             给回:{it['time']} [{it['kind']}] "
                              f"{it['text'][:70]}")
            else:
                print("         · 没调 recall")
            print(f"         她说:{(r['text'] or '(空)')[:280]}")
    print()
    print(SEP)


if __name__ == "__main__":
    main()
