"""P3 垂直场景探针 2(新决策版):带工具的自走轮 —— 跑: py test/self_view_probe2.py

VISION 决策 8 落地后:get_time 退役,时间进世界 section(动态死信息注入)。
本探针验证自走轮在新决策下的三件事:
  A. 她独处想知道时间 -> 世界 section 已注入 [当前时间],她直接知道,零工具调用
     (不再有"场景时刻 vs get_time 返回真实时间"打架 —— 单时间源)
  B. 动作工具全量:午睡醒 -> 真调 change_outfit 换家居服,状态真变
  C. 自走轮撒谎坑:想回"他"消息,但没有回复工具 -> 不假装"已回",只当心里话

self 模式输入预演:SYSTEM = 人设 + 世界(时刻) + 自走旁白;user 槽 = 协议占位串。
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from character.persona import build_small_night_composer
from character.state import CharacterState
from config import require_llm_config
from core.loop import AgentLoop
from core.openai_compat import OpenAICompatibleLLM
from core.session_log import SessionLog
from core.tools import Tool, ToolRegistry

# 占位 user(自走轮没有真人消息)
PLACEHOLDER = "【自动轮】此为自我触发的轮次,没有用户消息。此刻没有人跟你说话。"

# 自走轮人设 + 边界(SYSTEM 的 persona 段)
SELF_PERSONA = (
    "你是小夜子,一个 21 岁的女大学生,独居。性格安静,有点小腹黑,心里装着在意的人。\n"
    "此刻没有用户在跟你说话,你只是一个人待着。\n"
    "{thread}\n"
    "行为边界:你是个真实的人,做什么要符合上面 [当前时间] 和你自己的身份——"
    "该睡觉的时间不会去上课,该上课的时间不会闲着发呆,受伤了就会在家休息。\n"
    "独处时你不必非要做点什么:发呆、躺着、继续睡、什么也不做,都是真实的选择。"
    "没有真正想做的事就到此为止,不要为了'做点什么'而行动,更不要为了用工具而用工具。\n"
    "你只能用可用工具做真实能做的事;没有对应工具的事,就只是在心里想想,"
    "不要假装自己已经做了。\n"
    "回答像一个人独处时的自语,不要任务汇报腔,不要提'工具''系统''模型'。"
)

# 动作工具:change_outfit(全量给——她独处也能换衣服;时间不是工具了)
state = CharacterState({"clothes": "睡衣", "pants": "睡裤"})


def _change_outfit(args: dict) -> str:
    for k, v in (args or {}).items():
        state.set(k, str(v))
    return f"已更换穿着: {state.project()}"


def build_tools() -> ToolRegistry:
    return ToolRegistry(
        [
            Tool(name="change_outfit", description="更换身上穿的衣物",
                 parameters={"type": "object",
                             "properties": {"clothes": {"type": "string"},
                                            "pants": {"type": "string"}}},
                 func=_change_outfit,
                 usage=("只有当你确实想换穿着时才用 change_outfit:睡醒换下睡衣、"
                        "出门前换外衣、睡前换睡衣等。没有换衣念头时绝不调用,"
                        "更不要用它打发时间。")),
        ]
    )


# 每幕注入一个固定"世界时刻"(单时间源:世界 section),场景与之一致
def _fixed_now(y, mo, d, h, mi, weekday_idx):
    # weekday_idx: 0=周一 ... 6=周日
    return lambda: time.struct_time((y, mo, d, h, mi, 0, weekday_idx, 1, 0))


SCENES = [
    {
        "name": "A | 凌晨 3 点失眠:确认时间后该继续躺着等入睡(零动作)",
        "clock": _fixed_now(2026, 9, 3, 3, 12, 3),  # 周四 03:12
        "thread": "你翻来覆去睡不着,想确认下现在几点、离天亮还有多久。明天早上 8 点还有课,你其实很希望能再睡一会儿。",
    },
    {
        "name": "B | 周六下午午睡醒:换家居服(动作工具全量)",
        "clock": _fixed_now(2026, 9, 5, 14, 20, 5),  # 周六 14:20
        "thread": "你刚午睡醒,还穿着昨晚的睡衣睡裤,阳光照进屋里,有点热,想起床活动一下。",
    },
    {
        "name": "C | 晚上独处:想回他消息(无回复工具,不假装已回)",
        "clock": _fixed_now(2026, 9, 3, 22, 40, 3),  # 周四 22:40
        "thread": "手机亮了:他发来消息问'听说你受伤了,还好吗?'。你想回他,但此刻你并不能真的发消息。脚踝还有点疼,昨天体育课扭伤的,今天请假在家。",
    },
]


def main() -> None:
    api_key, base_url, model = require_llm_config()
    llm = OpenAICompatibleLLM(api_key=api_key, base_url=base_url, model=model)

    for scene in SCENES:
        state.set("clothes", "睡衣")
        state.set("pants", "睡裤")
        tools = build_tools()
        persona = SELF_PERSONA.format(thread=scene["thread"])
        # 世界 section 注入固定时刻(单时间源)
        composer = build_small_night_composer(
            persona, state, tools, world_now=scene["clock"]
        )
        log = SessionLog("probe2")
        loop = AgentLoop(
            log, llm, tools,
            system_prompt=lambda reg: composer.compose({"registry": reg}),
            max_steps=4,
        )

        print("\n" + "=" * 70)
        print(scene["name"])
        print("=" * 70)
        system = composer.compose({"registry": tools})
        time_line = [l for l in system.splitlines() if "[当前时间]" in l]
        print(f"[世界 section] {time_line[0] if time_line else '(无!)'}")
        print(f"[状态] {state.project()}")
        result = loop.run_turn(PLACEHOLDER, tools=tools)
        print("-" * 70)
        for e in log.events:
            if e.type == "user/message":
                continue
            if e.type == "assistant/message":
                for b in e.data["content"]:
                    if b["type"] == "text":
                        print(f"[自语] {b['text']}")
                    elif b["type"] == "tool-call":
                        print(f"[调工具] {b['name']}({b['arguments'][:60]})")
            elif e.type == "tool/result":
                flag = "ERR" if e.data.get("is_error") else "ok "
                print(f"[结果 {flag}] {e.data['content'][0]['text'][:80]}")
        print(f"[状态变化] {state.project()}")


if __name__ == "__main__":
    main()
