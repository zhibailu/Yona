"""折叠视图(工具痕迹)验证 demo —— 跑: py test/mask_view_demo.py

场景:TURN 1 全量工具(看时间+换衣服),TURN 2 子集只给 get_time。
问题:TURN 1 的工具痕迹留在历史里,模型会看到 change_outfit 并尝试再调。

对比喂给模型的完整输入(SYSTEM + 历史)两种视图:
  忠实视图 = derive_messages()(默认,业界主流)
  折叠视图 = derive_messages(fold_tool_traces=True)

折叠规则(已实现于 core/session_log.py):
  - 日志原文不动,只改投影
  - 已结束轮次(turn/end 出现过的)工具痕迹折叠:
      assistant 消息剔 tool-call 块、tool/result 事件跳过
  - retain_result=True 的工具(如 launch_subagent)痕迹跨轮保留(保真)
  - 当前未结束轮完整保留(step 间还要拿结果当原料)
  - 信息兜底:穿着在 SYSTEM 状态段每轮注入;时间沉淀在她说出口的话里
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

from character.state import CharacterState
from core.composer import SystemComposer, SystemSection, make_usage_section
from core.llm import AssistantOutput, ToolCall
from core.loop import AgentLoop
from core.session_log import SessionLog
from core.tools import Tool, ToolRegistry
from mock_llm import MockLLM

# ---------------- 场景装备 ----------------

BASE_PERSONA = "你是小夜子,一个温柔体贴的 AI 伴侣。回答用中文,保持自然亲切。"

state = CharacterState({"clothes": "白衬衫", "pants": "牛仔裤"})


def _get_time(args: dict) -> str:
    return "2026-09-02 19:27:04"


def _change_outfit(args: dict) -> str:
    for k, v in args.items():
        state.set(k, str(v))
    return f"已更换穿着: {state.project()}"


def make_full_tools() -> ToolRegistry:
    return ToolRegistry(
        [
            Tool(
                name="get_time",
                description="获取当前本地时间",
                parameters={"type": "object"},
                func=_get_time,
                usage="用户问时间时用 get_time。",
                retain_result=False,  # 默认:折叠(需要就现调)
            ),
            Tool(
                name="change_outfit",
                description="更换角色穿着",
                parameters={
                    "type": "object",
                    "properties": {"clothes": {"type": "string"}, "pants": {"type": "string"}},
                },
                func=_change_outfit,
                usage="用户让换衣服时用 change_outfit。",
                retain_result=False,  # 状态进 state 段,痕迹可折叠
            ),
            Tool(
                name="launch_subagent",
                description="委派子任务给 subagent",
                parameters={"type": "object", "properties": {"task": {"type": "string"}}},
                func=lambda a: "subagent 完成: 调研报告已归档",
                usage="复杂任务委派给 subagent。",
                retain_result=True,  # 保真:子任务结果要跨轮被引用
            ),
        ]
    )


def build_composer() -> SystemComposer:
    c = SystemComposer()
    c.register(SystemSection(name="persona", priority=10, template=BASE_PERSONA))

    def _state_text(values: dict) -> str | None:
        text = state.project()
        return f"[当前角色状态]\n{text}" if text else None

    c.register(SystemSection(name="state", priority=20, producer=_state_text))
    c.register(make_usage_section(priority=30))
    return c


def render_msgs(msgs: list[dict]) -> str:
    lines = []
    for m in msgs:
        role = m["role"]
        if role == "user":
            txt = m["content"][0].get("text", "")
            lines.append(f'    {{"role": "user", "content": [text: {txt[:60]}]}}')
        elif role == "assistant":
            parts = []
            for b in m["content"]:
                if b["type"] == "text":
                    parts.append(f"text: {b['text'][:70]}")
                elif b["type"] == "tool-call":
                    parts.append(f"tool-call: {b['name']}({b['arguments'][:50]})")
            lines.append(f'    {{"role": "assistant", "content": [{" | ".join(parts)}]}}')
        elif role == "tool":
            txt = m["content"][0].get("text", "") if m["content"] else ""
            lines.append(f'    {{"role": "tool", "tool_call_id": "{m["tool_call_id"]}", "content": [text: {txt[:60]}]}}')
    return "\n".join(lines)


def main() -> None:
    full_tools = make_full_tools()

    # --- TURN 1:看时间 + 换衣服 + 委派 subagent(覆盖三种 retain 属性) ---
    script = [
        AssistantOutput(
            text="我来处理这三件事。",
            tool_calls=[
                ToolCall(id="tc1", name="get_time", arguments="{}"),
                ToolCall(
                    id="tc2",
                    name="change_outfit",
                    arguments='{"clothes": "针织开衫", "pants": "休闲长裤"}',
                ),
                ToolCall(id="tc3", name="launch_subagent", arguments='{"task": "调研"}' ),
            ],
        ),
        AssistantOutput(text="时间 19:27,换了针织开衫,子任务已委派。"),
    ]
    log = SessionLog("demo")
    loop = AgentLoop(log, MockLLM(script), full_tools)
    loop.run_turn("现在几点了?帮我换衣服,顺便委派个调研任务。")

    # --- TURN 2:还没真跑,只挂起用户输入 ---
    subset_tools = ToolRegistry([full_tools.get("get_time")])
    log.append("turn/start", turn=2)
    log.append(
        "user/message",
        content=[{"type": "text", "text": "现在几点?调研有结果了吗?顺便把裤子换成黑裤。"}],
    )

    composer = build_composer()
    sys_full = composer.compose({"registry": full_tools})
    sys_sub = composer.compose({"registry": subset_tools})
    hist_full = log.derive_messages()
    hist_fold = log.derive_messages(fold_tool_traces=True, retained_tools={"launch_subagent"})

    print("=" * 72)
    print("TURN 1 事件日志摘要(原始,一字未改)")
    print("=" * 72)
    for e in log.events:
        if e.type == "assistant/chunk":
            continue
        print(f"  #{e.seq:<3} {e.type:<20} {str(e.data)[:100]}")

    print()
    print("=" * 72)
    print("TURN 2 喂给模型的输入对比(SYSTEM 用子集 usage)")
    print("=" * 72)

    print("\n[SYSTEM - 同一份] composer.compose(子集 tools)")
    print("-" * 72)
    print(sys_sub)

    print("\n[历史 - 忠实视图] derive_messages()(默认,业界主流)")
    print("-" * 72)
    print(render_msgs(hist_full))

    print("\n[历史 - 折叠视图] derive_messages(fold_tool_traces=True)")
    print("-" * 72)
    print(render_msgs(hist_fold))

    print("\n" + "=" * 72)
    print("差异逐条对照")
    print("=" * 72)
    blob_full = sys_full + "".join(
        b.get("name", "") + b.get("text", "")
        for m in hist_full if m["role"] == "assistant"
        for b in m["content"]
    )
    blob_fold = sys_sub + "".join(
        b.get("name", "") + b.get("text", "")
        for m in hist_fold if m["role"] == "assistant"
        for b in m["content"]
    )
    print(f"  change_outfit 字样:  忠实 {('change_outfit' in blob_full)!s:>5} | 折叠 {('change_outfit' in blob_fold)!s:>5}")
    print(f"  get_time 字样:       忠实 {('get_time' in blob_full)!s:>5} | 折叠 {('get_time' in blob_fold)!s:>5}")
    print(f"  launch_subagent 字样: 忠实 {('launch_subagent' in blob_full)!s:>5} | 折叠 {('launch_subagent' in blob_fold)!s:>5}  <- retain_result=True,跨轮保留")
    print()
    print("  结论:")
    print("    - 折叠视图里,change_outfit / get_time 痕迹没了(模型不会尝试再调,需要就现调)")
    print("    - launch_subagent 痕迹保留:subagent 委派结果要跨轮被引用(保真)")
    print("    - 穿着在 SYSTEM 状态段,时间沉淀在她的话里 —— 信息不丢")


if __name__ == "__main__":
    main()
