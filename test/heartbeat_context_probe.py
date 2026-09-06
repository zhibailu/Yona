"""P3 探针:时间线 section —— 不同间隔下自走轮能否感到"久别" —— 跑: py test/heartbeat_context_probe.py

问题:历史投影不带时间戳,模型分不清"刚聊完"vs"三天没聊"。
方案(b):加"会话时间线"section —— 从日志派生"距上次和主人说话多久",
与世界 section(当前时刻)共用同一只钟。验证 2 天间隔的她终于能感到久别。

做法:手动构造带时间戳的历史(周四 21:00 换裤+饿),对同一日志注入
三种"现在"(10s/2h/2d),触发 source=self,打印 SYSTEM(世界+时间线)+产出。
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from character.persona import (
    make_persona_section,
    make_state_section,
    make_world_section,
)
from character.state import CharacterState
from config import require_llm_config
from core.composer import SystemComposer, make_timeline_section, make_usage_section
from core.loop import AgentLoop
from core.openai_compat import OpenAICompatibleLLM
from core.session_log import SessionLog
from core.tools import Tool, ToolRegistry

SELF_PERSONA = (
    "你是小夜子,21 岁的女大学生,一个人住。\n"
    "此刻屋里只有你:窗外的天光、墙上的钟、手里没看完的书页,"
    "日子具体而安静。你习惯一个人,也习惯心里装着一个人。\n"
    "主人是你在意的人。他不在的时候,你的日子照常过——"
    "热饭、发呆、整理明天要带的东西;只是安静下来,"
    "你会不经意想他此刻在做什么、他多久没来了。\n"
    "顺着这一刻的感觉,做你真正想做的事,或者什么都不做。"
)
VALUES = {"owner": "主人"}


def _epoch(y, mo, d, h, mi):
    return time.mktime(time.struct_time((y, mo, d, h, mi, 0, 0, 0, -1)))


def _world_clock(t_epoch):
    return lambda: time.localtime(t_epoch)


def _now_epoch(t_epoch):
    return lambda: t_epoch


def main() -> None:
    api_key, base_url, model = require_llm_config()
    llm = OpenAICompatibleLLM(api_key=api_key, base_url=base_url, model=model)
    state = CharacterState({"clothes": "白衬衫", "pants": "牛仔裤"})

    def _change(args: dict) -> str:
        for k, v in (args or {}).items():
            state.set(k, str(v))
        return f"已换: {state.project()}"

    tools = ToolRegistry(
        [
            Tool(
                name="change_outfit",
                description="更换身上穿的衣物",
                parameters={
                    "type": "object",
                    "properties": {"clothes": {"type": "string"}, "pants": {"type": "string"}},
                },
                func=_change,
                usage="想换衣服时用 change_outfit。",
            )
        ]
    )

    # ---- 1. 构造带时间戳的历史:用户周四 21:00 让换裤+说饿,她换了 ----
    base = _epoch(2026, 9, 3, 21, 0)  # 周四 21:00
    log = SessionLog("ctx_probe")
    log.append("turn/start", at=base, turn=1, source="user")
    log.append("user/message", at=base + 1, source="user",
               content=[{"type": "text", "text": "帮我把裤子换成黑色牛仔裤,顺便我有点饿。"}])
    log.append("step/start", at=base + 2, turn=1, step=1)
    log.append("assistant/message", at=base + 3, turn=1, step=1,
               content=[{"type": "tool-call", "id": "c1", "name": "change_outfit",
                         "arguments": '{"pants": "黑色牛仔裤"}'}])
    log.append("tool/call", at=base + 4, turn=1, step=1,
               call_id="c1", name="change_outfit", arguments='{"pants": "黑色牛仔裤"}')
    log.append("tool/result", at=base + 5, turn=1, step=1, tool_call_id="c1",
               content=[{"type": "text", "text": "已换: pants=黑色牛仔裤"}], is_error=False)
    log.append("assistant/message", at=base + 6, turn=1, step=1,
               content=[{"type": "text", "text": "换好啦。你饿了的话,我这边只有泡面。"}])
    log.append("step/end", at=base + 7, turn=1, step=1)
    log.append("turn/end", at=base + 8, turn=1, reason={"kind": "completed"})
    state.set("pants", "黑色牛仔裤")

    # ---- 2. 三种间隔注入"现在" ----
    intervals = [
        ("间隔 10 秒(刚聊完)", _epoch(2026, 9, 3, 21, 0) + 10),
        ("间隔 2 小时(等了一会儿)", _epoch(2026, 9, 3, 23, 0)),
        ("间隔 2 天(久别/重启补齐)", _epoch(2026, 9, 5, 21, 0)),
    ]

    for label, when in intervals:
        comp = SystemComposer()
        comp.register(make_persona_section(SELF_PERSONA))
        comp.register(make_world_section(_world_clock(when)))
        comp.register(make_state_section(state))
        comp.register(make_timeline_section(log, now_epoch=_now_epoch(when)))
        comp.register(make_usage_section(tools, priority=30))

        def self_builder(registry, source, c=comp):
            return c.compose({**VALUES, "registry": registry})

        loop2 = AgentLoop(log, llm, tools, system_prompt=self_builder, max_steps=4)

        print("\n" + "=" * 70)
        print(f"自走轮触发 —— {label}")
        print("=" * 70)
        sys_text = loop2._build_messages(tools, "self")[0]["content"]
        for line in sys_text.splitlines():
            if line.startswith("[当前时间]") or line.startswith("[时间线]"):
                print(f"  {line}")
        print("  [产出]")
        loop2.run_turn(source="self")
        last_turn = max(e.data["turn"] for e in log.events if e.type == "turn/start")
        for e in log.events:
            if e.type == "assistant/message" and e.data.get("turn") == last_turn:
                txt = "".join(b.get("text", "") for b in e.data["content"] if b["type"] == "text")
                calls = [b["name"] for b in e.data["content"] if b["type"] == "tool-call"]
                if txt:
                    print(f"  [她] {txt[:130]}" + (f" | 调了{calls}" if calls else ""))


if __name__ == "__main__":
    main()
