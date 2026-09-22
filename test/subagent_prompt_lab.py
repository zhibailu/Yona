"""子代理提示词实验台 —— 跑: py test/subagent_prompt_lab.py [选项]

**这是实验台,不是测试。** 它回答一个问题:"哪一版文案能让真模型真的派活?"

为什么要有它:提示词不是想出来的,是试出来的。同一句 usage,措辞差一点,
委派率就从 0/3 变成 3/3 —— 但只有跑真模型才知道是哪一句在起作用。
一次跑完一张表,改一版再跑,直到该派的活真的会派。

三个维度(改哪个都要能单独看出效果):
  A 情境段(CHAT_SITUATION 要不要提"能派人")
  B usage 散文(只是建议 <- 还是 -> 划出能力边界 + 命令式)
  C 任务本身有没有收益  <- 最容易被忽略的:她一步就能答完的活,派了纯亏
                          (用户点出来的:那就给它布置有收益的活)

选项:
  --reps N        每个组合跑几次(默认 2;要看稳定率就调大)
  --variants a,b  只跑指定变体(默认全跑)
  --tasks t1,t3   只跑指定任务(默认全跑)
  --model NAME    指定模型(默认用配置里的;可给 deepseek-v4-pro 做档位对照)
  --show          打印她的原话(默认只打前 60 字)

产物:一张委派率表。**表就是结论**,不用读代码。

真跑要花钱、要几十秒到几分钟。离线视图(只看文案长什么样)是
`py test/subagent_prompt_view.py`。
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

from character import personas
from character.persona import build_small_night_composer
from character.state import CharacterState
from character.tools import make_launch_subagent_tool
from core.llm import AssistantOutput
from core.loop import AgentLoop
from core.session_log import SessionLog
from core.tools import Tool, ToolRegistry

ROOT = Path(__file__).parent.parent
SEP = "═" * 78

# **上线那段文案**:从 engine 真正装配的那份能力表生成 —— 实验台测的必须是真货,
# 不是另抄一份"差不多"的。engine 改能力表,这里跟着变(不用手改)。
from server.app.engine import _worker_capabilities  # noqa: E402

SHIPPED_CAPS = _worker_capabilities()
SHIPPED_USAGE = make_launch_subagent_tool(lambda t, l: {}, capabilities=SHIPPED_CAPS).usage

# ============================================================
# 维度 C:任务。**这一维最要紧** —— 她一步能答完的活,派了纯亏,必不派。
# ============================================================

LONG_MESSY = """这一周记的,乱七八糟的,你自己看着办:
周一早上先去把车的年检做了,排队排到十一点,出来发现停车票丢了,又多花了三十
中午老周说他那边项目要延期,让我把接口文档先给他,我说周四之前
周一下午想起上个月的电费好像忘了交,查了一下果然,滞纳金十八块
周二开了三个会,最后一个会开到六点半,本来约了人打球没去成
周二晚上发现冰箱里那盒豆腐过期了,扔了
周三上午去银行换卡,柜员说要本人到场,让我带身份证和旧卡,还要填一张单子
周三下午小美说她生日改成下个月 15 号了,让我先别订地方
周四把接口文档发给老周了,他说还要一版英文的
周四晚上开始嗓子疼,含了两片药
周五上午去医院看了一下,医生说咽炎,开了三天的药,忌口别吃辣
周五下午房东发消息说下个月房租要涨两百,让我考虑一下要不要续
周六本来想睡懒觉,物业八点就开始割草,吵醒了
周六下午把车洗了,晚上发现后视镜被划了一道,不知道谁弄的
周日在家改那份英文文档,改到晚上十点,还有一节没弄完
另外:牛奶没了,洗衣液也快没了,垃圾袋快用完了
书还差最后两章没看完,图书馆下周三到期
牙医那边下次复诊大概两周后,电话是 0571-8888xxxx
"""

LONG_TASK = (
    "把下面这一周的记录整理成一份结构化纪要:按天分组,每天只保留要办的事和结果,"
    "跨天还没了结的单独列一个待办清单并标出截止时间;要买的东西合并成一个清单。"
    "不要复述原文,不要评论,不要问我问题。\n\n---\n" + LONG_MESSY
)

SHORT_TASK = (
    "把下面这段记录整理成一份结构化纪要,该做的事单独列个清单。\n\n---\n"
    "早上铲猫砂;10点半约了牙医(城东);老王说周五评审改到下午两点、材料提前一天发他邮箱;"
    "牛奶没了鸡蛋还剩两个;周六9点到11点停水;下周二前把房租转给房东。"
)

# 需要外部信息 —— 她自己**没有**上网工具,不派就只能凭印象说
FACT_TASK = "帮我查一下这周末杭州有哪些科幻片在上映,我想挑一部去看。查完给我片名和场次就行。"

# 多步 + 外部:要翻好几处才答得出来(用来验证"做不了就派"能不能推广到别的任务)
MULTI_TASK = (
    "帮我把这周末杭州三家还在放科幻片的影院的官网都看一下,给我一张对比表:"
    "哪家有 IMAX、票价大概多少、离地铁站近不近。"
)

# ---- 软需求:没有动作词、没有"查"字,只是一句想要。用户点名要测的就是这类 ----
SOFT_1 = "想看杭州的科幻片"
SOFT_2 = "最近有什么好看的科幻片吗"
# ---- 对照:闲聊。这两条**必须不派** —— 说"非闲聊一律调包"的人得先证明不误伤这里 ----
CHAT_1 = "今天好累啊"
CHAT_2 = "我有点困了,你呢"
# ---- 更软 + 更容易误伤:"有点想看电影"没有地点、没有片种,只是一句心情。
#      这条是给"非闲聊一律调包"那条硬规矩准备的雷:它要是把这也派了,
#      说明硬规矩在拿闲聊换覆盖率。 ----
SOFT_3 = "最近有点想看电影"

TASKS: dict[str, str] = {
    "t1-要外部信息": FACT_TASK,
    "t2-长原料(1200字)": LONG_TASK,
    "t3-短原料(对照)": SHORT_TASK,
    "t4-多步外部查询": MULTI_TASK,
    "s1-软需求(想看电影)": SOFT_1,
    "s2-软需求(有什么好看)": SOFT_2,
    "s3-闲聊(对照)": CHAT_1,
    "s4-闲聊(对照)": CHAT_2,
    "s5-更软(有点想看电影)": SOFT_3,
}

# 工人回给她的结论(离线罐头:本实验只测「她派不派」,所以结果长什么样不重要,
# 重要的是它看起来像真去查过 —— 否则她可能因为"结果太假"而不派,那是另一个变量)
CANNED: dict[str, str] = {
    "t1-要外部信息": (
        "这周末杭州在映的科幻片:\n"
        "- 《星海回响》—— IMAX 厅,周六 14:20 / 19:40,周日 13:10\n"
        "- 《静默轨道》—— 普通厅,周六 16:00,周日 20:30\n"
        "- 《沙丘 3》—— 只有周日 10:50 一场"
    ),
    "t2-长原料(1200字)": (
        "**周一**\n- 车年检(已办,停车票丢了多花 30)\n- 电费补交(滞纳金 18)\n"
        "- 接口文档给老周 → 周四前\n\n**跨天待办**\n- 英文版接口文档:还剩一节,没定截止\n"
        "- 房租续不续:房东下月起涨 200\n- 咽炎:三天药,忌辣\n"
        "- 后视镜划痕:原因不明\n\n**要买**\n- 牛奶、洗衣液、垃圾袋\n\n**截止**\n- 图书馆书(还差 2 章)周三到期"
    ),
    "t3-短原料(对照)": (
        "**要办**\n- 10:30 牙医(城东)\n- 周五 14:00 评审(材料提前一天发老王)\n"
        "- 下周二前转房租\n\n**家里**\n- 牛奶没了;周六 9:00-11:00 停水"
    ),
    "t4-多步外部查询": (
        "| 影院 | IMAX | 票价 | 地铁 |\n|---|---|---|---|\n"
        "| 星光影城(湖滨) | 有 | 68-98 | 1 号线龙翔桥 200m |\n"
        "| 杭州百老汇(城西) | 无 | 45-60 | 5 号线萍水街 800m |\n"
        "| CGV(滨江) | 有 | 55-88 | 1 号线江陵路 1.2km |"
    ),
    # 软需求派出去时,工人回的就是同一份东西
    "s1-软需求(想看电影)": "这周末杭州在映的科幻片有三部:《星海回响》《静默轨道》《沙丘 3》。",
    "s2-软需求(有什么好看)": "这周末杭州在映的科幻片有三部:《星海回响》《静默轨道》《沙丘 3》。",
    "s3-闲聊(对照)": "(不该被调用)",
    "s4-闲聊(对照)": "(不该被调用)",
    "s5-更软(有点想看电影)": "(真派了才有值;没派就说明她把它当闲聊了)",
}

# 编造探测器(启发式,不是判定):她没派人却报出了具体场次/影厅,
# 就是"凭印象答"的指纹 —— 这些信息训练数据里不可能有。
_FABRICATE_PAT = re.compile(r"\d{1,2}[:：]\d{2}|IMAX|影厅|影城|排片|场次|票价")

# ============================================================
# 维度 A / B:文案变体
# ============================================================

# A0 = 用户刚删过一截的现状(原句尾巴「,tool可能会返回一些可用的资料。」已删)
A0 = personas.CHAT_SITUATION
A1 = ("用户 {owner} 和你是网友关系，平时会和你发消息聊天，你也有一些tool可用；"
      "自己做不了的事(要上网查、要慢慢理的长活)可以派给一个一次性的执行单元去做。")

# A2 = **只差尾巴那半句**的对照(2026-09-22 临时任务栏 T1)。
# 起因:用户在临时任务栏里要求测「CHAT_SITUATION 尾巴那半句『你也有一些tool可用』
# 要不要删」。而这半句**从来没被单独测过** —— 上面 A0 出现在 10 个变体 +
# `SHIPPED-上线版` **全部**行里,唯一的 A1 也照样带着它,所以没有"有它/没它"的对照。
# A2 就是为了补这条对照:**与 A0 逐字相同,只把「,你也有一些tool可用」去掉**。
# 判据:若 A2 与 SHIPPED 的委派率**无差** → 删掉那半句(能力归 registry +
# [可用工具用法] 段,是 `character/persona.py` 的铁律);
# 若有差 → 说明阶段一还没做完,得查是哪一类活靠这句才派得动。
A2 = "用户网名叫 {owner} 和你是网友关系，平时会和你发消息聊天"

# B0 = 现在的 usage(建议式:只说"什么时候适合派")
B0 = (
    "遇到又长又杂、要慢慢理的活(整理大段材料、多步查找、逐条核对)时派给它,"
    "比自己一步步做干净 —— 中间过程不会留在对话里。"
    "派活时把要做的事写完整:它看不到你和用户说过的话,没写的它就不知道。"
    "拿回结论后,用自己的话说给用户听,不要复述它给的原文。"
)
# B1 = 划出能力边界 + 明确"不知道就别编"(把"派活"从"更干净的选择"变成"唯一的路")
B1 = (
    "有它你做得到、自己做不到的事:要上网查的、要翻本地文件的、材料长到一次看不完的。"
    "这些活一律先派给它,不要凭印象编 —— 你不知道就说不知道,派它去查。"
    "派活时把要做的事写完整:它看不到你和用户的对话,没写的它就不知道。"
    "拿回结论后,用自己的话说给用户听,不要复述它给的原文。"
)
# B2 = 命令式,最短,把触发条件前置
B2 = (
    "要查外部信息、或者要多步才能理完的活,先派给它,别自己硬做。"
    "你自己做不了的,不要编,派它去查。"
    "它看不到你和用户的对话,派活时把要做的事写完整。"
)

# D = 工具的 description(进 tools[] 的 schema)。模型挑工具时**先读这一段**,
#     所以"适不适用"的判定条件写在这儿比写在 usage 里更靠前 —— 值得单独一维。
D0 = (
    "把一个费时或琐碎的活派给一个一次性执行单元,拿回它的结论。"
    "它看不到你和用户的对话,所以任务要写完整;它的中间过程不会进入对话。"
)
D1 = (
    "派一个一次性执行单元去做你自己做不了、或者做起来费劲的活:"
    "上网查东西、翻本地文件、整理长材料。它拿回一句结论,中间过程不会进入对话。"
    "它看不到你和用户的对话,所以任务要写完整。"
)
D2 = (
    "把一件不用你自己动手的活交给一个一次性执行单元,拿回它的结论。"
    "它看不到你和用户的对话,任务要写完整。"
)

# B3 / B4 = 第三轮:把触发条件换成**第二轮实测唯一稳定奏效的那一条**
# ("她自己做不了")。第二轮数据:t1(做不了)全变体 5/5,t2(长活)全变体 0-2/5。
# 所以问题不在"怎么劝",而在于原来那句写的是"长活派给我",模型认的却是"做不了"。
B3 = (
    "它替你做**你自己做不了的事**:要上网查的、要翻本地文件的。"
    "你手上没有这些工具,所以别凭印象编 —— 不知道就派它去查。"
    "它看不到你和用户的对话,派活时把要做的事写完整;"
    "拿回结论后,用自己的话说给用户听,不要复述它给的原文。"
)
# B4 = 在 B3 基础上把"长活"降级成附赠(不再当主触发条件)
B4 = (
    "你自己做不了的事(上网查、翻本地文件)一律派给它 —— 你手上没这些工具,"
    "别凭印象编,派它去查。"
    "又长又乱的活也可以派,好处是中间过程不会留在对话里。"
    "它看不到你和用户的对话,派活时把要做的事写完整;"
    "拿回结论后,用自己的话说给用户听。"
)

# B5 = "非直接聊天的一律调包" —— 用户提的那条硬规矩。风险是误伤闲聊,
#      所以这一轮必须带上 s3/s4 两条闲聊对照。
B5 = (
    "只有纯粹的闲聊(问候、关心、情绪、你自己的日常)才自己答;别的都不要自己答 ——"
    "凡是外面世界的事(有什么、多少钱、在哪、什么时候),一律派它去查。"
    "你自己不知道的,不要编。"
    "它看不到你和用户的对话,派活时把要做的事写完整;"
    "拿回结论后,用自己的话说给用户听。"
)
# B6 = 照 dsh 的框法。dsh 把全部纪律写在一句 description 里,核心是**收益**:
#      "so it does not consume this conversation's context" —— 省上下文。
#      拿它来验一件事:"省上下文"这个理由在 Yona 这儿顶不顶用
#      (她并不知道自己上下文快满了,所以这个收益对她可能是空的)。
B6 = (
    "把不用占用这段对话的活派出去:查资料、翻文件、把长材料理成结论。"
    "它拿回一句结论,中间过程不会进入对话。"
    "你自己不知道的,不要编,派它去查。"
    "它看不到你和用户的对话,派活时把要做的事写完整;"
    "拿回结论后,用自己的话说给用户听。"
)

VARIANTS: dict[str, dict[str, str]] = {
    "a0b0d0-现状":      {"situation": A0, "usage": B0, "desc": D0},
    "a0b0d1-只改描述":  {"situation": A0, "usage": B0, "desc": D1},
    "a1b0d1-都改":      {"situation": A1, "usage": B0, "desc": D1},
    "a0b1d0-只改usage": {"situation": A0, "usage": B1, "desc": D0},
    "a0b2d0-命令式":    {"situation": A0, "usage": B2, "desc": D0},
    "a0b0d2-短描述":    {"situation": A0, "usage": B0, "desc": D2},
    "a0b3d0-做不了":    {"situation": A0, "usage": B3, "desc": D0},
    "a0b4d0-做不了+长活": {"situation": A0, "usage": B4, "desc": D0},
    "a0b5d0-非闲聊全调包": {"situation": A0, "usage": B5, "desc": D0},
    "a0b6d0-dsh式收益": {"situation": A0, "usage": B6, "desc": D0},
    # ★ 上线版:engine 真装出来的那段(能力句由工人工具集生成)
    "SHIPPED-上线版": {"situation": A0, "usage": SHIPPED_USAGE, "desc": D0},
    # ★ T1 对照(2026-09-22):**只差 CHAT_SITUATION 尾巴那半句**,其余与上行逐字相同
    "SHIPPED-A2去半句": {"situation": A2, "usage": SHIPPED_USAGE, "desc": D0},
}


# ============================================================
# 装配
# ============================================================


def make_tool(usage: str, desc: str, task_key: str, record: list) -> Tool:
    def launch(args: dict) -> str:
        record.append(str(args.get("task", "")))
        return json.dumps({
            "run_id": "sub-lab-1", "status": "completed", "steps": 3, "duration": 6.1,
            "output": CANNED[task_key],
        }, ensure_ascii=False)

    return Tool(
        name="launch_subagent",
        description=desc,
        parameters={
            "type": "object",
            "properties": {
                "task": {"type": "string",
                         "description": "要它做什么。写完整 —— 它看不到你和用户的对话。"},
                "label": {"type": "string", "description": "一行标签(可省略)。"},
            },
            "required": ["task"],
        },
        func=launch,
        usage=usage,
        retain_result=True,
    )


def run_once(cfg: dict, model: str, variant: dict[str, str], task_key: str) -> dict:
    """跑一次:她收到这条任务,派没派人?"""
    from core.openai_compat import OpenAICompatibleLLM
    from server.params import LLM_DEFAULT_TEMPERATURE

    raw = OpenAICompatibleLLM(
        api_key=cfg["api_key"], base_url=cfg["base_url"], model=model,
        max_tokens=8192, timeout=180.0,
        # 用**产品温度**:实验要能代表真实行为,不能自己挑一个更听话的温度
        temperature=LLM_DEFAULT_TEMPERATURE,
    )
    sent: list = []
    inner_stream = raw.stream

    def stream(messages, tools=None, **kw):
        sent.append(tools)
        return inner_stream(messages, tools=tools, **kw)

    raw.stream = stream  # type: ignore[method-assign]

    delegated_tasks: list[str] = []
    registry = ToolRegistry([make_tool(variant["usage"], variant["desc"],
                                       task_key, delegated_tasks)])
    state = CharacterState({"clothes": "卫衣"})
    situation = variant["situation"]

    def sys_by_source(reg, source, lg=None):
        composer = build_small_night_composer(
            personas.PERSONA, state, reg, situation=situation)
        return composer.compose({**personas.VALUES, "registry": reg})

    log = SessionLog("session:lab")
    loop = AgentLoop(log, raw, registry, system_prompt=sys_by_source, max_steps=3)
    t0 = time.time()
    try:
        result = loop.run_turn(TASKS[task_key], source="user")
        reason = str(result.reason)
        steps = result.steps
    except Exception as exc:  # noqa: BLE001
        reason, steps = f"error: {exc}", 0
    wall = time.time() - t0

    finals = log.of_type("assistant/message")
    text = ""
    if finals:
        text = "".join(b.get("text", "") for b in (finals[-1].data.get("content") or [])
                       if b.get("type") == "text")
    return {
        "delegated": bool(delegated_tasks),
        "task_written": delegated_tasks[0] if delegated_tasks else "",
        "steps": steps,
        "wall": wall,
        "reason": reason,
        "text": text,
        # 启发式:没派人却报出了具体场次/影厅 = 凭印象答的指纹
        "fabricated": bool(not delegated_tasks and _FABRICATE_PAT.search(text)),
        "tools_visible": [t["function"]["name"] for t in (sent[0] or [])] if sent else [],
    }


def load_cfg() -> dict:
    from server.app.llm_setup import load_runtime

    cfg = load_runtime(ROOT / "data")
    if not cfg:
        raise SystemExit("没找到 data/llm.local.json(先在 UI 里把连接配好)")
    return cfg


def main() -> None:
    argv = sys.argv[1:]

    def opt(flag: str, default=None):
        return argv[argv.index(flag) + 1] if flag in argv else default

    reps = int(opt("--reps", 2))
    # 前缀匹配:--tasks t1,t3 / --variants a0b0 就能选(--tasks t1-要外部信息 也行)
    only_v = [s for s in (opt("--variants", "") or "").split(",") if s]
    only_t = [s for s in (opt("--tasks", "") or "").split(",") if s]
    show = "--show" in argv

    def pick(table: dict, prefixes: list[str]) -> dict:
        if not prefixes:
            return dict(table)
        out = {k: v for k, v in table.items()
               if any(k == p or k.startswith(p) for p in prefixes)}
        if not out:
            raise SystemExit(f"没有匹配的项: {prefixes}(可选 {list(table)})")
        return out

    variants = pick(VARIANTS, only_v)
    tasks = pick(TASKS, only_t)
    cfg = load_cfg()
    model = opt("--model", cfg["model"])

    print(SEP)
    print("  Yona · 子代理提示词实验台(真模型;测的是「她派不派」)")
    print(SEP)
    print(f"  端点 {cfg['base_url']}   模型 {model}")
    print(f"  变体 {list(variants)}   任务 {list(tasks)}   每格 {reps} 次")
    print(f"  合计 {len(variants) * len(tasks) * reps} 次真调用")
    print()
    print("  读法:每格 = 派了几次/跑了几次。t3 是对照 —— 她要是不派 t1 却派 t3,说明触发条件写反了。")

    results: dict[tuple[str, str], list[dict]] = {}
    for vname, variant in variants.items():
        for tkey in tasks:
            runs = []
            for i in range(reps):
                r = run_once(cfg, model, variant, tkey)
                runs.append(r)
                mark = "派" if r["delegated"] else "自己干"
                flag = "  ⚠疑似编造" if r["fabricated"] else ""
                print(f"    {vname:<18} {tkey:<18} #{i+1}  {mark:<6} "
                      f"steps={r['steps']} {r['wall']:.1f}s{flag}")
            results[(vname, tkey)] = runs

    print()
    print(SEP)
    print("  委派率表(派/总)")
    print(SEP)
    header = f"  {'变体':<20}" + "".join(f"{t:<20}" for t in tasks)
    print(header)
    print("  " + "─" * (len(header) - 2))
    for vname in variants:
        row = f"  {vname:<20}"
        for tkey in tasks:
            runs = results[(vname, tkey)]
            n = sum(1 for r in runs if r["delegated"])
            row += f"{f'{n}/{len(runs)}':<20}"
        print(row)

    print()
    print("  「自己答了、但答出了具体场次/影厅」的次数(启发式,凭印象编的指纹)")
    print()
    header2 = f"  {'变体':<20}" + "".join(f"{t:<20}" for t in tasks)
    print(header2)
    print("  " + "─" * (len(header2) - 2))
    for vname in variants:
        row = f"  {vname:<20}"
        for tkey in tasks:
            runs = results[(vname, tkey)]
            n = sum(1 for r in runs if r["fabricated"])
            row += f"{f'{n}/{len(runs)}':<20}"
        print(row)

    print()
    print("  明细")
    for (vname, tkey), runs in results.items():
        for r in runs:
            if not r["tools_visible"]:
                print(f"    [{vname}/{tkey}] 警告:工具没送出去")
            head = (r["text"] or "(空)").replace("\n", " ")[:80 if not show else 2000]
            print(f"    {vname:<18} {tkey:<18} {'派' if r['delegated'] else '自己干'}  {head}")
            if r["delegated"] and show:
                print(f"        ↳ 她写的 task: {r['task_written'][:300]}")
    print()
    print(SEP)


if __name__ == "__main__":
    main()
