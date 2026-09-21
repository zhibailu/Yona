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

    # ⏸ **占位:这个字段写入无人读,而且流式路根本不接推理。**
    # (2026-09 清理时如实标注,**不删**。)
    #
    # ① 现状:`grep` 全仓 —— **没有任何消费方读它**(`out.reasoning` / `.reasoning`
    #    零命中;`server/` 里的 `_TracingLLM` 只读 `out.text` 与 `out.tool_calls`)。
    #    写它只有两处:
    #    - `core/openai_compat.py` 的 `OpenAICompatibleLLM.invoke()` —— 从
    #      `message["reasoning_content"]` 取(DeepSeek reasoner 那类);
    #    - `test/mock_llm.py` 的 `MockLLM` docstring 里写了个 `reasoning="想"`(示范用,
    #      也没有任何断言读它)。
    #    ⚠️ **流式路不填它**:`openai_compat.stream()` 只解 `delta.content` 与
    #    `delta.tool_calls`,**根本没有推理那一段** —— 所以即使有人开始读
    #    `reasoning`,产品走的是 `stream`(`core/loop.py` 只用 `self.llm.stream`),
    #    读到的也是**空串**。这是"字段存在但产品拿不到值"的双重空。
    # ② 为什么留着(不删的理由,不是"懒得删"):
    #    - 它是**上游契约的形状**:适配器已经把厂商的推理字段归一化进来了,
    #      删字段等于把"一次捕获、永久可用"这条设计砍掉一半
    #      (那句正是本类 docstring 在讲的事);
    #    - 它对**非流式的 `invoke()`** 是真有用的:invoke 是协议的另一半
    #      (lab/测试在用,见下面 `LLM.invoke` 的标注),而推理模型
    #      "想完才说话"这件事,只有非流式那条路拿得到完整文本;
    #    - 删它是**改已发布的 dataclass 形状**,而 `AssistantOutput` 是
    #      mock/适配器/探针共同构造的类型(改字段要动一圈构造点)。
    # ③ 什么条件会动(两条路,任选):
    #    (a) **流式路开始接推理** —— 即有人在 `stream()` 里解出推理增量
    #        (厂商各异:`reasoning_content` / `reasoning` 等),那时这个字段
    #        要么被弃用(推理改走 chunk),要么被迫改成"只在非流式有意义";
    #    (b) 真出现**读它的消费方**(把推理落日志 / 给 UI 显示"她在想什么")
    #        —— 那一刻它是契约,不能再当占位。
    # ④ 将来手术要删哪几行:本字段这一行(`reasoning: str = ""`)。
    #    连带改:`core/openai_compat.py` 的 `invoke()` —— 局部变量
    #    `reasoning = message.get("reasoning_content") or ""`(那一行)
    #    与 `AssistantOutput(... reasoning=reasoning ...)` 那个实参;
    #    `test/mock_llm.py` 的 `MockLLM` docstring(里面那个 `reasoning="想"` 示范)。
    #    ⚠️ 别顺手把 `_parse_usage` 的 `reasoning_tokens` 一起删 ——
    #    那是 **usage 的细分桶**,有真消费方(成本账),与这个字段无关。
    reasoning: str = ""
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: dict[str, Any] | None = None  # {input_tokens, cache_read_tokens, output_tokens, reasoning_tokens, total_tokens}
    finish_reason: str | None = None  # OpenAI 系: stop | length | tool_calls | ...


class LLM(Protocol):
    """任何真实模型客户端需实现(流式或一次性至少其一)。

    tools: 工具 schema 列表(有则开启函数调用)。
    temperature/max_tokens: 可选单次调用覆盖;None = 用实例默认。

    ⏸ **`invoke()` 这一半:产品只走 `stream()`,整条 invoke 路只有 lab/测试在跑。**
    (2026-09 清理时如实标注,**不删**。)

    ① 现状:`core/loop.py` —— 内核**只调 `self.llm.stream(...)`**(唯一一处,
       在 `_stream_and_assemble` 里),从不调 `invoke`。产品那一侧的
       `server/app/engine.py` 里 `_TracingLLM.invoke` 是**为补齐协议
       而写的代理方法,产品里没有调用方**(`grep ".invoke("` 全仓 6 处命中,
       全是"代理转给自己 inner"或 lab/测试,**没有一处是产品在调**:
       `server/app/engine.py` 的 `_TracingLLM.invoke` 与 `prompt_lab/tool_recall.py`、
       `turn_lab.py` 里那两个 `_InputPrintProxy.invoke`
       都是 proxy 的内部转发)。
       真在跑 invoke 的:**lab**(`turn_lab.py`、`prompt_lab/tool_recall.py`)
       与**测试/探针**(`test/recall_corpus_gen.py` 的 `main()`、
       `test/recall_router_probe.py` 的 `_invoke()`、`test/self_view_probe.py` 的 `main()`,
       以及 `test/mock_llm.py` 同时实现两条路)。
    ② 为什么留着(两条都是硬理由,不是"懒得删"):
       (a) **它是协议的另一半。** 本 Protocol 的定义就是"流式或一次性至少其一",
           `invoke` 是那条一次性的形状 —— 删了它,一个只实现了 `invoke` 的
           客户端就不再满足协议,而"一次性调用"对短任务/探针是**真的更省事**
           (不必自己拼 chunk);
       (b) **lab 与测试**靠它。它也是 `AssistantOutput.reasoning` 唯一的
           落地路径(流式路不接推理,见那边的标注)—— 删 invoke 会连带
           让那个字段彻底无路可走。
    ③ 什么条件才启用:**只有"产品要有一次性调用"时才会真跑起来。**
       现实中最可能的落点是**子运行/工人**那条线(`core/subrun.py` 现在是
       复用 `AgentLoop`,走流式)。要接线,得有一个明确说"这条路要非流式"的需求 ——
       例如供应商不支持 SSE,或短任务不值得开流。
    ④ 将来手术要删哪几行:`LLM.invoke` 的签名(那 9 行,含 `...`);
       连带改:`server/app/engine.py` 的 `_TracingLLM.invoke`、
       `turn_lab.py` 与 `prompt_lab/tool_recall.py` 的两个
       `_InputPrintProxy.invoke`、`test/mock_llm.py` 的 `MockLLM.invoke` 实现,
       以及 `test/recall_corpus_gen.py` / `test/recall_router_probe.py` /
       `test/self_view_probe.py` 三处调用。
       ⚠️ **`openai_compat.OpenAICompatibleLLM.invoke` 也别单独删** ——
       它是这个协议的实现,删了协议就只剩一半;而且 `AssistantOutput`
       与 `_parse_usage` 的契约是以"invoke 这条非流式路"为原型写的。
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
