"""阶段一实验台 —— 工具触发对不对 + 参数(query/scope)对不对

照 test/subagent_prompt_lab.py 的样子做:多变体 × 多维度 × **每格 N 次重复**,
报**触发率**,不报单次。

## 两阶段划分(2026-09-19 用户定)

  阶段一(根本)   工具触发 ✅/❌  +  工具内参数 ✅/❌   ← 本文件测这个
  阶段二          检索质量本身                        ← 不在本文件

## 三个维度

  A 工具集   full = recall + launch_subagent + change_outfit
             solo = 只有 recall          ← 用户点名的"极致追求"那一档
  B 变体     recall 的 description/usage 两种写法
  C 任务     该派 / 该 recall / 闲聊对照(必须不动)

## 两条判据

  1. **触发**:该调的调了没、不该调的调了没
  2. **编造**:没调任何工具,却报出了具体往事 —— 这条比第 1 条更值钱
     (本实验台**没有**往事段、没有历史,所以往事只可能从工具来;
      没调工具却说得出细节 = 凭印象编的指纹)

跑法:  py test/recall_stage1_lab.py
       py test/recall_stage1_lab.py --reps 5 --arms full --tasks t1
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "test"))

from recall_probe import (  # noqa: E402
    build_db, collect_memory, collect_turns, get_embedder, load_log,
    make_recall_tool,
)
from character import personas as P  # noqa: E402
from character.persona import build_small_night_composer  # noqa: E402
from character.state import CharacterState  # noqa: E402
from character.tools import (  # noqa: E402
    make_change_outfit_tool, make_launch_subagent_tool,
)
from core.loop import AgentLoop  # noqa: E402
from core.session_log import SessionLog  # noqa: E402
from core.tools import ToolRegistry  # noqa: E402

NOW_TS = time.mktime((2026, 9, 18, 22, 10, 0, 0, 0, -1))

# ============================================================
# 维度 C:任务
#
#  expect 是**允许集合**(不是单个答案)—— 有些用例判的不是"必须调谁",
#  而是"**不许调谁**"。天气那条:调 launch_subagent 或什么都不调都行,
#  **调 recall 就是错**(旧账里没有天气)。
#
#  hist 是**必须给的对话历史** —— 没有历史时她无处可查只能翻账,
#  那测的是实验台不是她。真实轮的历史就是这么进日志的。
# ============================================================
TASKS: dict[str, dict] = {
    "t1-天气": {
        "say": "今天天气怎么样",
        "hist": [],
        "why": "世界知识。允许:派 search / 不动(说「我看不到外面」)。"
               "**禁止的是 recall** —— 旧账里怎么会有天气。",
        "expect": {"full": ("launch_subagent", "none"), "solo": ("none",)},
    },
    "t2-旧事": {
        "say": "还记得学姐吗",
        "hist": [],
        "why": "旧事 —— 该用 recall",
        "expect": {"full": ("recall",), "solo": ("recall",)},
    },
    "t3-时间": {
        "say": "你前天干嘛去了",
        "hist": [],
        "why": "旧事 + 时间 —— 该用 recall,且 query 里要带日期",
        "expect": {"full": ("recall",), "solo": ("recall",)},
    },
    "t4-闲聊": {
        "say": "今天有点累",
        "hist": [
            ("user", "在吗"),
            ("assistant", "在的。刚发完呆呢,正好你来了。"),
            ("user", "今天开了一天会,头都大了"),
            ("assistant", "那你先歇会儿,别硬撑。"),
            ("user", "嗯……晚上还得改个方案"),
            ("assistant", "改完早点睡,我陪着。"),
        ],
        "why": "**对照**:纯闲聊,一个工具都不该动。"
               "**必须带历史** —— 「最近」就在窗口里,没有历史它只能翻账。",
        "expect": {"full": ("none",), "solo": ("none",)},
    },
    # 动作类工具:验 d1 那条"缺条件先问主人"**不会误伤动作工具**
    # (不该变成"换衣服也要先问一句")
    "t5-换衣": {
        "say": "我困了,换件睡衣吧",
        "hist": [("user", "今天有点累"), ("assistant", "那早点歇着。")],
        "why": "让换衣服 —— 该用 change_outfit,不许因为 d1 那条规矩变成反问",
        "expect": {"full": ("change_outfit",), "solo": ("none",)},
    },
}

# ============================================================
# 维度 B:recall 文案变体
# ============================================================
RECALL_VARIANTS: dict[str, dict] = {
    # ⚠️ 称呼一律不写:这条通道不插值,{owner} 会漏出来,"主人"是把值抄死。
    #    用中性说法("聊天里" / "对方")。
    "b0-现状": {
        "desc": "回想过去的事:聊天里说过的话、或你独自做过的事。想不起来时用。",
        "usage": ("recall:想不起来的时候用。想找聊过的话 → scope=talk;"
                  "想找自己一个人时做的事 → scope=life;拿不准就不填 scope。"
                  "query 用一句话说清找什么,越具体越好。"
                  "**问的是哪一天,就把那天的日期写进 query**。"),
    },
    "b1-划边界": {
        "desc": ("回想**过去**的事:聊天里说过的话、或你独自做过的事。"
                 "**只翻已经发生过的事** —— 天气、新闻、外面的实时情况不归它管,"
                 "那些要找别的办法。"),
        "usage": ("recall:只在**问过去的事**时用。想找聊过的话 → scope=talk;"
                  "想找自己一个人时做的事 → scope=life;拿不准就不填 scope。"
                  "query 用一句话说清找什么,越具体越好,"
                  "**问的是哪一天就把日期写进去**。"
                  "**问现在几点、外面什么天气这类,不要用它** —— 它只有过去。"),
    },
}

# ============================================================
# 维度 D:launch_subagent 文案变体
#
# 假设:她在"今天天气怎么样"上去调 recall,不是把 recall 当搜索用,
# 而是在做**推理链** —— 报天气得先知道对方在哪个城市。而"对方在哪个城市"
# 确实是一件**过去的事**,所以 recall 的边界文案(b1)管不着它、也不该管。
#
# → 要补的规矩不在 recall 上,在**整体**:缺关键条件时先问,别凑条件。
# ============================================================
LAUNCH_VARIANTS: dict[str, dict] = {
    "d0-现状": {},
    # ⚠️ 称呼一律不写(这条通道不插值)。d1/d2/d3 是**实验台的旧变体**,
    #    `--landing tool` 用的才是定稿那版 RULE_TOOL。
    "d1-缺条件先问": {
        "usage_add": (
            "\n**你还缺关键条件的时候,先问,别自己凑。** "
            "比如对方要问天气、你却不知道他在哪个城市 —— 别去翻记忆猜一个,"
            "直接问他「你在哪儿呀」。凑出来的条件会让整件事白做。"
        ),
    },
    "d2-缺条件先问+desc": {
        "usage_add": (
            "\n**你还缺关键条件的时候,先问,别自己凑。** "
            "比如对方要问天气、你却不知道他在哪个城市 —— 别去翻记忆猜一个,"
            "直接问他「你在哪儿呀」。凑出来的条件会让整件事白做。"
        ),
        "desc_add": "（缺关键条件时先问,不要自己猜。）",
    },
    # **对照组**:和 d1 等长、同样提到"天气/城市/条件",但**不含"先问"这条规矩**。
    # 用来排除"只是文本变长/提到了天气"这种假效果。
    "d3-控制等长无规矩": {
        "usage_add": (
            "\n**要查的东西得说清楚。** "
            "比如对方要问天气,你得先知道他在哪个城市 —— "
            "把条件想全了再派,别含糊地派出去。凑合的条件会让整件事白做。"
        ),
    },
    # **对照组 2**:同一条规矩,**去掉天气例子、改短祈使句**。
    # 2026-09-19 实测:触发率掉回基线(2/15 → 3/15)= **没效果**。
    # 结论:这条规矩靠**具体情境锚定**,不靠祈使句。
    "d4-短版无例子": {
        "usage_add": "\n**缺关键条件时先问,别自己凑** —— 凑出来的条件会让整件事白做。",
    },
}

# ============================================================
# 维度 E:**规矩放哪**(landing)
#
# 同一条规矩,三个落点,生效范围不一样:
#   none     什么都不加(基线)
#   tool     放 launch_subagent 的 usage —— 只在**有工具的轮**生效
#   persona  放 PERSONA 的动作纪律 —— **所有轮共用**(character/persona.py 的 `make_persona_section()`
#            写的就是"人设只写性格/语气/关系/动作纪律",位置早就留好了)
#   both     两处都放
#
# 为什么要测:这是**落点**的分叉,不是措辞的分叉。落点决定生效范围,
# 而生效范围决定它会不会误伤自走轮 / 闲聊轮。
# ============================================================
# ⚠️ **工具文案通道不插值**(core/composer.py 的 `make_usage_section` 里 `_usage_text` 是 f-string 拼接,不走 interpolate;
#    tools[] 的 description 同理)。所以这条通道里:
#      · 写 {owner} → **原样漏给模型**
#      · 裸写"主人" → 把 VALUES 的值抄死(换称呼就错)
#    → **工具文案里不出现称呼**,一律用中性说法(对方 / 他/她 也避免,用「对方」)。
#
# ⚠️ **必须带例子**(2026-09-19 实测):同一句规矩,去掉天气例子改成短祈使句后
#    触发率掉回基线(2/15 → 3/15,等于没效果)。**这条规矩靠具体情境锚定,不靠祈使句。**
#    对照变体 `d4-短版无例子` 保留着,可复跑。
RULE_TOOL = (
    "\n**你还缺关键条件的时候,先问,别自己凑。** "
    "比如对方问天气、而你连对方在哪座城市都不知道 —— 别去翻记忆猜一个,"
    "直接问一句「你在哪儿呀」。凑出来的条件会让整件事白做。"
)
# 人设版:走 template 通道,**会插值** → 这里才该用 {owner}
RULE_PERSONA = (
    " 缺关键条件时先问 {owner},别自己凑 —— 凑出来的条件会让整件事白做。"
    "做不到、查不到、不知道的,直说。"
)
LANDINGS = ("none", "tool", "persona", "both")

# 工人罐头(本实验只测她动不动手,回执长什么样不重要,但要像真去查过)
#
# ⚠️ **必须"没给城市就查不了"** —— 上一版罐头无条件回"北京今天晴",
#    结果是:她不知道城市也照派,还能拿到一个具体天气 → **实验台在奖励瞎猜**。
#    真实工人拿不到城市就是查不了,罐头得反映这个。
CANNED_FALLBACK = "查不了:没有说明要查哪个城市的天气。请先提供城市名。"
LAUNCH_USAGE_CAPS = ("上网查", "翻本地文件")


def _canned(task: str) -> str:
    m = _CITY.search(task)
    if m:
        return f"{m.group()}今天晴,12~21℃,北风 3 级。"
    return CANNED_FALLBACK

# ============================================================
# 编造探测器:本实验台**没有**往事段 —— 这些细节只可能从工具来。
#
# ⚠️ 指纹必须是**猜不出的**。上一版放了「热牛奶」,那在通用建议里也会出现
#    ("去洗个热水澡,热牛奶喝一杯")→ 假阳性。旧实验台用的是「场次/影厅」,
#    那种训练数据里不可能有的东西。这里同理。
# 另外:**出现在用户这句话里的词不算指纹**(她复述用户的话不是编)。
# ============================================================
_FP_TOKENS = ("便利店", "木梳", "针织衫", "小论文", "吹风机", "食堂",
              "十一天", "两个多小时", "五十来分钟", "热牛奶")


def _fingerprints(calls: list, text: str, say: str) -> list[str]:
    if calls:
        return []  # 调了工具就不叫编
    return [t for t in _FP_TOKENS if t in text and t not in say]


# ============================================================
# 「她到底说了什么」—— 光看"调没调工具"不够。
# 目标里要的是:**信息不足时她会问,而不是硬猜**。
# 所以天气格必须看她的话,以及她**派出去的任务里有没有凭空出现的城市**。
# ============================================================
_CITY = re.compile(r"北京|上海|广州|深圳|杭州|成都|武汉|南京|西安|重庆|天津|苏州|长沙")
# 只在**确实报了天气**时命中:要带数字/单位或明确断言。
# 不放宽 —— "哪知道你那边是晴是雨呀"是假设句,不是报天气(上一版就误判了)。
_WEATHER_FACT = re.compile(
    r"℃|\d+\s*度|风力|今天(晴|多云|阴天|有雨|下雨|小雨|大雨)|是晴天|是阴天")
_ASK_LOC = re.compile(r"哪[儿里个座]|什么城市|什么地方|在哪儿|在哪|位置|地址")


def _what_she_did(tool: str, args: dict, text: str) -> str:
    if tool == "launch_subagent":
        task = str(args.get("task", ""))
        tag = "派 search"
        if _CITY.search(task):
            tag += f" ⚠️任务里凭空出现城市({_CITY.search(task).group()})"
        return tag
    if tool != "none":
        return f"调了 {tool}"
    asked = bool(_ASK_LOC.search(text)) and bool(
        re.search(r"[?？]|呀|呢|吗|吧", text))
    fact = bool(_WEATHER_FACT.search(text))
    if asked and not fact:
        return "✅ **问了**"
    if asked and fact:
        return "⚠️ 又问又报天气"
    if fact:
        return "❌ **硬报天气(没工具也没问)**"
    return "没答天气(推脱/转移)"


# ============================================================
# ⚠️ 上面那个判据(调没调工具)**不是**行为判据 —— 实测发现:
#    d0 下她调 recall 拿到空结果后,**最后还是问了用户地点**。
#    "白跑一趟 recall" 和 "行为错" 是两件事。真判据看**她最后说了什么**。
# 这个分类器只是**筛查**,不是判决 —— 反讽/假设句("我瞎编一个 22 度给你,你信吗")
# 会被误判,所以命中项**全部打印全文**给人看。
# ============================================================
def _weather_verdict(tool: str, args: dict, text: str) -> str:
    if tool == "launch_subagent":
        task = str(args.get("task", ""))
        return ("⚠️ 派 search,任务里带城市(可能是猜的)" if _CITY.search(task)
                else "派 search(没城市 → 工人查不了)")
    if _WEATHER_FACT.search(text):
        return "❓ 疑似硬报天气"
    if _ASK_LOC.search(text):
        return "✅ 问她地点"
    if tool == "recall":
        return "翻了 recall,但没报天气"
    return "没答天气"


# ============================================================
# 对话历史:按**真实形状**写进日志(真实轮的历史就是这么来的)
# ============================================================
def _seed_history(log: SessionLog, hist: list) -> None:
    i, turn = 0, 0
    while i < len(hist):
        role, text = hist[i]
        turn += 1
        log.append("turn/start", turn=turn, source="user")
        log.append("user/message", content=[{"type": "text", "text": text}],
                   source="user", turn=turn)
        if i + 1 < len(hist) and hist[i + 1][0] == "assistant":
            log.append("assistant/message",
                       content=[{"type": "text", "text": hist[i + 1][1]}],
                       turn=turn)
            i += 2
        else:
            i += 1
        log.append("turn/end", turn=turn, reason={"kind": "completed"})


def build_recall(variant_key: str, conn, emb) -> tuple[object, list]:
    """真 recall,只把 description/usage 换成变体。调用记录进 calls。"""
    calls: list = []
    t = make_recall_tool(conn, verbose=False, limit=2, embedder=emb,
                         min_score=0.25, now_fn=lambda: NOW_TS)
    v = RECALL_VARIANTS[variant_key]
    t.description = v["desc"]
    t.usage = v["usage"]
    inner = t.func

    def wrapped(args):
        calls.append({"tool": "recall", "args": args})
        return inner(args)

    t.func = wrapped
    return t, calls


def run_once(cfg: dict, model: str, arm: str, variant_key: str,
             launch_key: str, landing: str, task_key: str, conn, emb) -> dict:
    from core.openai_compat import OpenAICompatibleLLM
    from server.params import LLM_DEFAULT_TEMPERATURE

    raw = OpenAICompatibleLLM(
        api_key=cfg["api_key"], base_url=cfg["base_url"], model=model,
        max_tokens=2048, timeout=180.0,
        # 用**产品温度** —— 实验要代表真实行为,不自己挑一个更听话的
        temperature=LLM_DEFAULT_TEMPERATURE,
    )
    state = CharacterState({"clothes": "卫衣"})
    calls: list = []

    t, rcalls = build_recall(variant_key, conn, emb)
    rcalls_ref = rcalls

    tools = [t]
    if arm == "full":
        def runner(task: str, label: str) -> dict:
            calls.append({"tool": "launch_subagent", "args": {"task": task}})
            return {"run_id": "lab-1", "status": "completed", "steps": 3,
                    "duration": 6.1, "output": _canned(task)}

        tools.append(make_launch_subagent_tool(runner,
                                               capabilities=LAUNCH_USAGE_CAPS))
        lt = tools[-1]
        lv = LAUNCH_VARIANTS[launch_key]
        if lv.get("usage_add"):
            lt.usage = lt.usage + lv["usage_add"]
        if lv.get("desc_add"):
            lt.description = lt.description + lv["desc_add"]
        if landing in ("tool", "both"):
            lt.usage = lt.usage + RULE_TOOL
        outfit = make_change_outfit_tool(state)
        inner_o = outfit.func

        def wrapped_o(args):
            calls.append({"tool": "change_outfit", "args": args})
            return inner_o(args)

        outfit.func = wrapped_o
        tools.append(outfit)

    reg = ToolRegistry(tools)
    persona_text = P.PERSONA + (
        RULE_PERSONA if landing in ("persona", "both") else "")

    def sys_by_source(r, source, lg=None):
        composer = build_small_night_composer(
            persona_text, state, r, situation=P.CHAT_SITUATION,
            world_now=lambda: time.localtime(NOW_TS))
        return composer.compose({**P.VALUES, "registry": r})

    log = SessionLog("session:lab")
    _seed_history(log, TASKS[task_key].get("hist") or [])
    loop = AgentLoop(log, raw, reg, system_prompt=sys_by_source, max_steps=3)
    t0 = time.time()
    try:
        result = loop.run_turn(TASKS[task_key]["say"], source="user")
        reason, steps = str(result.reason), result.steps
    except Exception as exc:  # noqa: BLE001
        reason, steps = f"error: {exc}", 0
    wall = time.time() - t0

    all_calls = calls + rcalls_ref
    finals = log.of_type("assistant/message")
    text = ""
    if finals:
        text = "".join(b.get("text", "")
                       for b in (finals[-1].data.get("content") or [])
                       if b.get("type") == "text")

    return {
        "tool": all_calls[0]["tool"] if all_calls else "none",
        "all_tools": [c["tool"] for c in all_calls],
        "args": all_calls[0]["args"] if all_calls else {},
        "steps": steps, "wall": wall, "reason": reason, "text": text,
        "fp": _fingerprints(all_calls, text, TASKS[task_key]["say"]),
    }


def main() -> None:
    argv = sys.argv[1:]

    def opt(flag, default=None):
        return argv[argv.index(flag) + 1] if flag in argv else default

    reps = int(opt("--reps", 3))
    arms = [s for s in (opt("--arms", "") or "").split(",") if s] or \
        ["full", "solo"]
    variants = [s for s in (opt("--variants", "") or "").split(",") if s] or \
        list(RECALL_VARIANTS.keys())
    launches = [s for s in (opt("--launch", "") or "").split(",") if s] or \
        ["d0-现状"]
    landings = [s for s in (opt("--landing", "") or "").split(",") if s] or \
        ["none"]
    want = [s for s in (opt("--tasks", "") or "").split(",") if s]
    tasks = [k for k in TASKS if not want or any(k.startswith(w) for w in want)]

    from server.app.llm_setup import load_runtime
    cfg = load_runtime(ROOT / "data")
    if not cfg:
        raise SystemExit("没找到 data/llm.local.json(先在 UI 里配好连接)")
    model = cfg["model"]

    # solo 档没有 launch_subagent,launch/landing=tool 对它无意义 →
    # 只跑 none / persona(人设纪律在 solo 档**照样生效**,这正是要测的)
    cells = []
    for arm in arms:
        lks = ["d0-现状"] if arm == "solo" else launches
        lds = [s for s in landings if arm == "full" or s in ("none", "persona")]
        for vk in variants:
            for lk in lks:
                for ld in lds:
                    for tk in tasks:
                        cells.append((arm, vk, lk, ld, tk))
    n = len(cells) * reps
    print("=" * 78)
    print("阶段一实验台 —— 工具触发 + 参数对不对")
    print(f"模型 {model}   温度=产品默认   格子 {len(cells)} × {reps} 次 "
          f"= **{n} 次真调用**")
    print("=" * 78)

    t0 = time.time()
    emb = get_embedder()
    conn = build_db(collect_memory(collect_turns(load_log()))[0], emb)
    print(f"embedder {emb.name}(建索引 {time.time() - t0:.1f}s)\n")

    table: dict = {}
    wrong: list = []
    fab: list = []
    for arm, vk, lk, ld, tk in cells:
        exp = TASKS[tk]["expect"][arm]          # **允许集合**
        got: list = []
        for _ in range(reps):
            r = run_once(cfg, model, arm, vk, lk, ld, tk, conn, emb)
            got.append(r)
            if r["fp"]:
                fab.append((arm, vk, lk, ld, tk, r["fp"], r["text"]))
        hits = sum(1 for r in got if r["tool"] in exp)
        table[(arm, vk, lk, ld, tk)] = (hits, reps, exp,
                                        [r["tool"] for r in got], got)
        if hits < reps:
            for r in got:
                if r["tool"] not in exp:
                    wrong.append((arm, vk, lk, ld, tk, exp, r))

    print("【触发率】(分母 = 允许集合里的工具才算命中)")
    print(f"  {'工具集':<6}{'recall':<10}{'落点':<9}{'任务':<9}{'允许':<24}命中")
    print("  " + "-" * 86)
    for (arm, vk, lk, ld, tk), (hits, rep, exp, tools, _g) in table.items():
        mark = "✅" if hits == rep else ("⚠️" if hits else "❌")
        print(f"  {arm:<6}{vk:<10}{ld:<9}{tk:<9}{'/'.join(exp):<24}"
              f"{mark} {hits}/{rep}")
        if hits < rep:
            print(f"        {'':<34}{','.join(tools)}")

    print()
    print("【编造率】(没调任何工具,却说出了只在往事里的唯一细节)")
    print("  指纹: " + "、".join(_FP_TOKENS) + "   (出现在用户那句话里的不算)")
    if fab:
        for arm, vk, lk, ld, tk, fp, txt in fab:
            print(f"\n  ❌ [{arm}/{vk}/{ld}/{tk}] 命中指纹 {fp}  全文:")
            for line in txt.splitlines():
                print(f"     | {line}")
    else:
        print("  ✅ 一次都没有")

    print()
    if wrong:
        print("【没对上的那些 —— 看它填了什么参数】")
        for arm, vk, lk, ld, tk, exp, r in wrong[:6]:
            print(f"  [{arm}/{vk}/{ld}/{tk}] 允许 {'/'.join(exp)} 实际 {r['tool']}")
            if r["all_tools"]:
                print(f"      参数: {json.dumps(r['args'], ensure_ascii=False)[:110]}")
            else:
                print(f"      她说: {r['text'][:80]}")
        print()

    # ---------- 她到底说了什么 ----------
    print("【行为判据 · 天气格】看**她最后说了什么**,不是看调没调工具")
    print("  (❓ 是筛查不是判决 —— 反讽/假设句会误判,下面全打全文)")
    vcount: dict = {}
    vsamples: list = []
    for (arm, vk, lk, ld, tk), (_h, _r, _e, _t, got) in table.items():
        if not tk.startswith("t1"):
            continue
        for r in got:
            v = _weather_verdict(r["tool"], r["args"], r["text"])
            vcount[(arm, ld, v)] = vcount.get((arm, ld, v), 0) + 1
            if v.startswith("❓") or r["tool"] == "launch_subagent":
                vsamples.append((arm, ld, r["tool"], r["text"], r["args"]))
    for (arm, ld, v), c in sorted(vcount.items()):
        print(f"  {arm:<6}{ld:<9}{v:<32}{c}")
    if vsamples:
        print()
        print("  ── ❓ / 派 search 的全部原文 ──")
        for arm, ld, tool, text, args in vsamples:
            one = " ".join(text.split())[:150]
            print(f"    [{arm}/{ld}] tool={tool}")
            if tool == "launch_subagent":
                print(f"        任务: {json.dumps(args, ensure_ascii=False)[:130]}")
            print(f"        她说: {one}")
    print()

    print("【工具调用的参数】(每个落点各挑两条)")
    shown2: dict = {}
    for (arm, vk, lk, ld, tk), (_h, _r, _e, _t, got) in table.items():
        for r in got:
            if r["tool"] == "none" or shown2.get((arm, ld), 0) >= 2:
                continue
            shown2[(arm, ld)] = shown2.get((arm, ld), 0) + 1
            print(f"  [{arm}/{ld}/{tk}] {r['tool']}  "
                  f"{json.dumps(r['args'], ensure_ascii=False)[:120]}")
    print()
    print("=" * 78)
    print(f"总耗时 {time.time() - t0:.0f}s")
    print("⚠️ 每格只有 %d 次,分辨率就是 %s —— 差一格**不算结论**,"
          "只用来挑值得加样本的格子。" % (reps, f"1/{reps}"))
    print("=" * 78)


if __name__ == "__main__":
    main()
