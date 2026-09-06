"""观察:自走轮跑完后事件日志原始长什么样 —— 跑: py test/heartbeat_log_dump.py"""

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


def _fmt_epoch(ts):
    return time.strftime("%m-%d %H:%M", time.localtime(ts))


def main() -> None:
    api_key, base_url, model = require_llm_config()
    llm = OpenAICompatibleLLM(api_key=api_key, base_url=base_url, model=model)
    state = CharacterState({"clothes": "白衬衫", "pants": "牛仔裤"})

    def _change(args: dict) -> str:
        for k, v in (args or {}).items():
            state.set(k, str(v))
        return f"已换: {state.project()}"

    tools = ToolRegistry([
        Tool(name="change_outfit", description="更换身上穿的衣物",
             parameters={"type": "object",
                         "properties": {"clothes": {"type": "string"}, "pants": {"type": "string"}}},
             func=_change, usage="想换衣服时用 change_outfit。"),
    ])

    # --- 用户轮(周四 21:00) ---
    base = _epoch(2026, 9, 3, 21, 0)
    log = SessionLog("log_dump")
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

    # --- 两次自走轮(间隔 10s,然后 2 天) ---
    for label, when in [(10, base + 10), (2 * 86400, _epoch(2026, 9, 5, 21, 0))]:
        comp = SystemComposer()
        comp.register(make_persona_section(SELF_PERSONA))
        comp.register(make_world_section(lambda: time.localtime(when)))
        comp.register(make_state_section(state))
        comp.register(make_timeline_section(log, now_epoch=lambda: when))
        comp.register(make_usage_section(tools, priority=30))

        loop = AgentLoop(
            log, llm, tools,
            system_prompt=lambda reg, source, c=comp: c.compose({**VALUES, "registry": reg}),
            max_steps=3,
        )
        print(f"\n>>> 自走轮触发({label})")
        loop.run_turn(source="self")

    # --- dump 全日志(逐事件原始) ---
    print("\n" + "=" * 76)
    print("完整事件日志(seq / 类型 / 时间 / data)")
    print("=" * 76)
    for e in log.events:
        data = dict(e.data)
        if e.type == "assistant/message":
            data["content"] = [
                {"type": b["type"], **({"text": b["text"][:40] + "…" if len(b.get("text", "")) > 40 else b.get("text", "")} if b["type"] == "text" else {"name": b["name"], "arguments": b["arguments"][:40]})}
                for b in data["content"]
            ]
        elif e.type == "user/message":
            data["content"] = [{"type": "text", "text": data["content"][0]["text"][:50]}]
        elif e.type == "tool/result":
            data["content"] = [{"type": "text", "text": data["content"][0]["text"][:40]}]
        print(f"  #{e.seq:<3} {e.type:<18} {_fmt_epoch(e.time)}  {data}")

    # --- derive_messages 投影(下一次喂给模型的) ---
    print("\n" + "=" * 76)
    print("derive_messages() 投影 —— 下一次调用会看到的历史")
    print("=" * 76)
    for m in log.derive_messages():
        if m["role"] == "user":
            txt = m["content"][0]["text"]
            print(f"  user      : {txt[:60]}")
        elif m["role"] == "assistant":
            parts = []
            for b in m["content"]:
                if b["type"] == "text":
                    parts.append(f'"{b["text"][:40]}"')
                elif b["type"] == "tool-call":
                    parts.append(f"tool:{b['name']}")
            print(f"  assistant : {' | '.join(parts) if parts else '(空)'}")
        elif m["role"] == "tool":
            print(f"  tool      : -> {m['content'][0]['text'][:40]}")


if __name__ == "__main__":
    main()
