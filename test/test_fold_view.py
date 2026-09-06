"""折叠视图(fold_tool_traces)自测:
- 已结束轮的工具痕迹折叠(assistant 剔 tool-call 块、tool/result 跳过)
- retain_result=True 的工具痕迹跨轮保留
- 当前未结束轮不折叠(step 间要拿结果)
- 默认(fold=False)行为不变,兼容忠实回放
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.session_log import SessionLog
from core.tools import Tool, ToolRegistry
from core.llm import AssistantOutput, ToolCall
from core.loop import AgentLoop
from mock_llm import MockLLM


def _mk_tool(name: str, retain: bool = False) -> Tool:
    return Tool(
        name=name,
        description=name,
        parameters={"type": "object"},
        func=lambda a: f"{name} done",
        usage=f"用 {name}",
        retain_result=retain,
    )


def _run_turn_with_tools(tool_names: list[str], retained: set[str] | None = None):
    """跑一轮:模型先调一批工具,再给最终文本。返回日志。"""
    registry = ToolRegistry([_mk_tool(n, n in (retained or set())) for n in tool_names])
    log = SessionLog("t")
    script = [
        AssistantOutput(
            tool_calls=[
                ToolCall(id=f"c{i}", name=n, arguments="{}")
                for i, n in enumerate(tool_names)
            ]
        ),
        AssistantOutput(text="都办好了。"),
    ]
    loop = AgentLoop(log, MockLLM(script), registry)
    loop.run_turn("都做")
    return log, registry


def test_fold_hides_closed_turn_tool_traces():
    """默认 retain=False:已结束轮里 tool-call 块与 tool/result 全折叠,只留文本。"""
    log, _ = _run_turn_with_tools(["get_time", "change_outfit"])
    # turn 2 挂起,让 turn1 成为"已结束轮"
    log.append("turn/start", turn=2)
    log.append("user/message", content=[{"type": "text", "text": "继续"}])

    msgs = log.derive_messages(fold_tool_traces=True)
    text = " ".join(b.get("text", "") for m in msgs if m["role"] == "assistant" for b in m["content"])
    names = " ".join(b.get("name", "") for m in msgs if m["role"] == "assistant" for b in m["content"])
    assert "get_time" not in names and "change_outfit" not in names
    assert "都办好了" in text  # 她的话还在
    assert all(m["role"] != "tool" for m in msgs)  # tool/result 全折叠


def _retained_names(registry: ToolRegistry) -> set[str]:
    return {n for n in registry.names() if registry.get(n).retain_result}


def test_fold_keeps_retained_tool_traces():
    """retain_result=True 的工具:tool-call 块 + tool/result 跨轮保留。"""
    log, registry = _run_turn_with_tools(
        ["web_search", "launch_subagent"], retained={"launch_subagent"}
    )
    log.append("turn/start", turn=2)
    log.append("user/message", content=[{"type": "text", "text": "继续"}])

    msgs = log.derive_messages(
        fold_tool_traces=True, retained_tools=_retained_names(registry)
    )
    names = " ".join(b.get("name", "") for m in msgs if m["role"] == "assistant" for b in m["content"])
    assert "web_search" not in names  # 非保真 -> 折叠
    assert "launch_subagent" in names  # 保真 -> 保留
    tools = [m for m in msgs if m["role"] == "tool"]
    assert len(tools) == 1 and tools[0]["tool_call_id"] == "c1"  # 只有 subagent 的结果留下


def test_agentloop_collects_retained_from_registry():
    """AgentLoop(fold_tool_traces=True) 自动收集 retain 工具,喂 derive。"""
    registry = ToolRegistry(
        [
            _mk_tool("web_search"),
            _mk_tool("launch_subagent", retain=True),
        ]
    )
    log = SessionLog("t")
    script = [
        AssistantOutput(
            tool_calls=[
                ToolCall(id="c0", name="web_search", arguments="{}"),
                ToolCall(id="c1", name="launch_subagent", arguments="{}"),
            ]
        ),
        AssistantOutput(text="好了"),
    ]
    loop = AgentLoop(log, MockLLM(script), registry, fold_tool_traces=True)
    loop.run_turn("做")
    assert loop._retained == {"launch_subagent"}

    log.append("turn/start", turn=2)
    log.append("user/message", content=[{"type": "text", "text": "继续"}])
    # 直接走 loop 的投影路径(等价于 _build_messages 去掉 system)
    msgs = log.derive_messages(
        fold_tool_traces=loop.fold_tool_traces, retained_tools=loop._retained
    )
    names = " ".join(
        b.get("name", "") for m in msgs if m["role"] == "assistant" for b in m["content"]
    )
    assert "web_search" not in names
    assert "launch_subagent" in names


def test_fold_keeps_current_turn_intact():
    """当前未结束的轮不折叠:同一轮里调完工具马上要结果。"""
    log, _ = _run_turn_with_tools(["get_time"])
    # 不开新轮:turn1 还没 turn/end 之前…… run_turn 已写 turn/end,所以直接模拟:
    # 造一个 turn2 进行中的状态(有 turn/start 但无 turn/end)
    log.append("turn/start", turn=2)
    log.append("user/message", content=[{"type": "text", "text": "现在几点?"}])
    log.append("step/start", turn=2, step=1)
    # 直接手动 append 当前轮的工具痕迹(模拟 step 中)
    log.append(
        "assistant/message",
        turn=2,
        step=1,
        content=[{"type": "tool-call", "id": "x1", "name": "get_time", "arguments": "{}"}],
    )
    log.append(
        "tool/result", turn=2, step=1, tool_call_id="x1",
        content=[{"type": "text", "text": "19:27"}],
    )

    msgs = log.derive_messages(fold_tool_traces=True)
    tool_msgs = [m for m in msgs if m["role"] == "tool"]
    assert len(tool_msgs) == 1  # turn2 的 tool 结果保留(step 间原料)


def test_default_is_faithful():
    """fold 默认 False:与旧行为一致,工具痕迹全保留。"""
    log, _ = _run_turn_with_tools(["get_time", "change_outfit"])
    log.append("turn/start", turn=2)
    log.append("user/message", content=[{"type": "text", "text": "继续"}])

    msgs = log.derive_messages()
    names = " ".join(b.get("name", "") for m in msgs if m["role"] == "assistant" for b in m["content"])
    assert "get_time" in names and "change_outfit" in names
    assert len([m for m in msgs if m["role"] == "tool"]) == 2


def test_fold_leaves_log_untouched():
    """折叠只改投影,日志事件原样。"""
    log, _ = _run_turn_with_tools(["get_time"])
    before = [e.type for e in log.events]
    log.append("turn/start", turn=2)
    log.append("user/message", content=[{"type": "text", "text": "继续"}])
    log.derive_messages(fold_tool_traces=True)
    after = [e.type for e in log.events]
    assert before + ["turn/start", "user/message"] == after


def test_self_placeholder_skipped_in_closed_turns():
    """自走轮占位 user 串:已结束轮不进历史;当前轮保留(触发点)。"""
    log = SessionLog("t")
    # turn1 = 自走轮,完整结束
    log.append("turn/start", turn=1, source="self")
    log.append("user/message", turn=1, source="self",
               content=[{"type": "text", "text": "【自动轮】占位串"}])
    log.append("assistant/message", turn=1, step=1,
               content=[{"type": "text", "text": "她独处的自语"}])
    log.append("turn/end", turn=1, reason={"kind": "completed"})
    # turn2 = 进行中(模拟 _build_messages 调用时)
    log.append("turn/start", turn=2, source="self")
    log.append("user/message", turn=2, source="self",
               content=[{"type": "text", "text": "【自动轮】占位串(当前轮)"}])

    msgs = log.derive_messages()
    roles = [m["role"] for m in msgs]
    texts = [m["content"][0]["text"] for m in msgs if m["role"] == "user"]
    # 只有当前轮(turn2)的占位 user;turn1 的占位被跳过
    assert roles == ["assistant", "user"], roles
    assert "占位串(当前轮)" in texts[0]
    assert "她独处的自语" in msgs[0]["content"][0]["text"]


def test_real_user_messages_never_skipped():
    """source=user 的消息永不跳过(回归)。"""
    log, _ = _run_turn_with_tools(["get_time"])
    log.append("turn/start", turn=2, source="user")
    log.append("user/message", turn=2, source="user",
               content=[{"type": "text", "text": "真用户的话"}])
    msgs = log.derive_messages()
    users = [m for m in msgs if m["role"] == "user"]
    assert any("真用户的话" in m["content"][0]["text"] for m in users)


if __name__ == "__main__":
    test_fold_hides_closed_turn_tool_traces()
    test_fold_keeps_retained_tool_traces()
    test_agentloop_collects_retained_from_registry()
    test_fold_keeps_current_turn_intact()
    test_default_is_faithful()
    test_fold_leaves_log_untouched()
    test_self_placeholder_skipped_in_closed_turns()
    test_real_user_messages_never_skipped()
    print("fold view all tests passed")
