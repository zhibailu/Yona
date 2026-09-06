"""Yona 新内核 · LLM 协议与数据类型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class ToolCall:
    """模型发出的一次工具调用请求。"""

    id: str
    name: str
    arguments: str  # JSON 字符串


@dataclass
class AssistantOutput:
    """一次模型调用的输出:推理 + 文本 + 工具调用 + 元信息。

    usage/finish_reason 由适配器(openai_compat)从**原始响应归一化**后带回,
    是"一次捕获、永久可用"的上游契约(见 openai_compat._parse_usage):
    以后任何消费方(token 计量/成本/截断率)都从这里取,不再回头改适配器。
    """

    reasoning: str = ""
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: dict[str, Any] | None = None  # {input_tokens, cache_read_tokens, output_tokens, reasoning_tokens, total_tokens}
    finish_reason: str | None = None  # OpenAI 系: stop | length | tool_calls | ...


class LLM(Protocol):
    """任何真实模型客户端需实现(流式或一次性至少其一)。

    tools: 工具 schema 列表(有则开启函数调用)。
    temperature/max_tokens: 可选单次调用覆盖;None = 用实例默认。
    """

    def invoke(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        model: str | None = None,
    ) -> AssistantOutput: ...

    def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        model: str | None = None,
    ):  # -> Iterator[chunk dict]
        """流式:逐 chunk 产出(形状见 core/assembler.py)。

        流内新增 chunk kind:
          {"kind": "usage", "usage": {...}}  —— 本次调用的 token 用量
          (供应商 SSE 需 stream_options.include_usage 才会送达;不支持的
          供应商缺这条,不影响正文流)。
        """
