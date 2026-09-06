"""测试替身:假模型(只给单元测试用,不进 core 生产代码)。

为什么需要它:单元测试要测循环的停止/兜底/错误分支,
不能真去调 API(离线、确定、不花钱)。
"""

from __future__ import annotations

from typing import Any

from core.llm import AssistantOutput


class MockLLM:
    """按脚本顺序返回,并记录每次收到的消息。

    用法:
        MockLLM([
            AssistantOutput(reasoning="想", tool_calls=[ToolCall(...)]),
            AssistantOutput(text="最终回答"),
        ])
    """

    def __init__(self, script: list[AssistantOutput] | None = None) -> None:
        self._script = list(script or [])
        self._calls = 0
        self.seen_messages: list[list[dict[str, Any]]] = []
        self.seen_tools: list[list[dict[str, Any]] | None] = []

    def invoke(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> AssistantOutput:
        self.seen_messages.append(messages)
        self.seen_tools.append(tools)
        out = self._script[self._calls % len(self._script)] if self._script else AssistantOutput()
        self._calls += 1
        return out

    def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ):
        """把剧本输出转成 chunk 流(与真实客户端同形状)。"""
        self.seen_messages.append(messages)
        self.seen_tools.append(tools)
        out = self._script[self._calls % len(self._script)] if self._script else AssistantOutput()
        self._calls += 1
        if out.text:
            yield {"kind": "text", "text": out.text}
        for i, call in enumerate(out.tool_calls):
            yield {
                "kind": "tool_call",
                "index": i,
                "id": call.id,
                "name": call.name,
                "arguments_delta": call.arguments,
            }
        reason = "tool_calls" if out.tool_calls else "stop"
        yield {"kind": "finish", "reason": reason}

    @property
    def call_count(self) -> int:
        return self._calls
