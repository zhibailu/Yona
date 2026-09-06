"""AgentLoop 自测:干净单循环 + 工具执行 + 日志脚印。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.llm import AssistantOutput, ToolCall
from core.loop import AgentLoop
from core.session_log import SessionLog
from core.tools import Tool, ToolRegistry
from mock_llm import MockLLM


def weather_tool(args: dict) -> str:
    city = args.get("city", "unknown")
    return f"{city}: 晴天, 28 度"


def build_env(script: list[AssistantOutput]):
    log = SessionLog("test")
    tools = ToolRegistry(
        [
            Tool(
                name="web_search",
                description="联网搜索天气等信息",
                parameters={
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                },
                func=lambda args: "晴天, 28 度",
                usage="搜索时用 web_search,别用其他工具;query 要具体。",
            )
        ]
    )
    llm = MockLLM(script)
    loop = AgentLoop(log, llm, tools, system_prompt="You are a weather assistant.")
    return log, tools, llm, loop


def test_full_turn_with_tools():
    """模型先调工具,再给最终回答。"""
    log, tools, llm, loop = build_env(
        [
            AssistantOutput(
                reasoning="需要搜索",
                tool_calls=[ToolCall(id="call_001", name="web_search", arguments='{"query": "weather"}')],
            ),
            AssistantOutput(text="今天晴天, 28 度。"),
        ]
    )
    result = loop.run_turn("查一下今天的天气")

    assert result.reason == {"kind": "completed"}
    assert result.steps == 2

    # 日志脚印(滤掉 assistant/chunk):一轮 turn,两次 step
    types = [e.type for e in log.events if e.type != "assistant/chunk"]
    assert types == [
        "turn/start", "user/message",
        "step/start", "assistant/message", "tool/call", "tool/result", "step/end",
        "step/start", "assistant/message", "step/end",
        "turn/end",
    ], types

    # 流式证据:assistant/chunk 事件确实进了日志
    # step1: tool_call + finish;step2: text + finish -> 共 4 条
    chunks = [e for e in log.events if e.type == "assistant/chunk"]
    assert len(chunks) == 4, f"chunk 数不对: {len(chunks)}"
    assert chunks[0].data["chunk"]["kind"] == "tool_call"
    assert chunks[-1].data["chunk"]["kind"] == "finish"

    # 投影:第 2 次模型调用必须看到 tool 结果(回挂同一 id)
    second_call = llm.seen_messages[1]
    roles = [m["role"] for m in second_call]
    assert roles == ["system", "user", "assistant", "tool"], roles
    tool_msg = second_call[3]
    assert tool_msg["tool_call_id"] == "call_001"
    assert "晴天" in tool_msg["content"][0]["text"]


def test_quick_answer_no_tools():
    """模型直接回答,不调工具 -> 一步结束。"""
    log, tools, llm, loop = build_env([AssistantOutput(text="你好!")])
    result = loop.run_turn("你好")
    assert result.reason == {"kind": "completed"}
    assert result.steps == 1
    types = [e.type for e in log.events]
    assert "tool/call" not in types


def test_max_steps_guard():
    """模型死循环调工具 -> max_steps 兜底,reason=max-steps。"""
    log, tools, llm, loop = build_env(
        [AssistantOutput(tool_calls=[ToolCall(id="c", name="web_search", arguments="{}")])]
    )
    loop.max_steps = 3
    result = loop.run_turn("转圈")
    assert result.reason == {"kind": "max-steps"}
    assert result.steps == 3


def test_out_of_whitelist_tool_call_is_silently_dropped():
    """模型调用白名单外的工具 -> message 层静默剔除:不执行、无错误回喂。

    与折叠/mask 同哲学:不给 schema 就当它不存在,不把工具名泄露给模型。
    chunk 层留原始真相(可观测),但不进投影、不触发执行。
    """
    log, tools, llm, loop = build_env(
        [
            AssistantOutput(tool_calls=[ToolCall(id="c1", name="nope", arguments="{}")]),
            AssistantOutput(text="done"),
        ]
    )
    result = loop.run_turn("test")
    assert result.reason == {"kind": "completed"}

    # 没有 tool/call 也没有 tool/result(没执行、没错误回喂)
    assert not any(e.type == "tool/call" for e in log.events)
    assert not any(e.type == "tool/result" for e in log.events)
    # chunk 原始真相还在(可观测性)
    chunks = [e for e in log.events if e.type == "assistant/chunk"]
    assert any(e.data["chunk"].get("name") == "nope" for e in chunks)
    # assistant/message 里没有 nope 痕迹
    for e in log.events:
        if e.type == "assistant/message":
            assert "nope" not in str(e.data["content"])


class LengthCutLLM:
    """只吐一个 tool_call 就 finish=length(无任何文本)-> 消息应为空被丢弃。"""

    def stream(self, messages, tools=None):
        yield {"kind": "tool_call", "index": 0, "id": "c1", "name": "web_search", "arguments_delta": '{"q":"x"}'}
        yield {"kind": "finish", "reason": "length"}


def test_max_tokens_empty_message_not_logged():
    """max-tokens 截断且无文本 -> 空 assistant 消息不进日志,下一轮输入干净。"""
    log = SessionLog("test")
    tools = ToolRegistry(
        [Tool(name="web_search", description="搜", parameters={"type": "object"}, func=lambda a: "res")]
    )
    loop = AgentLoop(log, LengthCutLLM(), tools)
    result = loop.run_turn("继续")

    assert result.reason == {"kind": "max-tokens"}
    # 日志里没有空 assistant/message
    for e in log.events:
        if e.type == "assistant/message":
            assert e.data["content"], f"空消息被写进日志: #{e.seq}"
    # chunk 真相还在
    assert any(e.type == "assistant/chunk" for e in log.events)
    # 下一轮输入只有 user,没有空 assistant
    msgs = log.derive_messages()
    assert len(msgs) == 1 and msgs[0]["role"] == "user", msgs


def make_env_with_two_tools():
    """weather: 带 usage;secret: 每轮可被排除的工具。"""
    tools = ToolRegistry(
        [
            Tool(
                name="weather",
                description="查天气",
                parameters={"type": "object", "properties": {"city": {"type": "string"}}},
                func=lambda a: f"{a.get('city')} 晴",
                usage="查天气用 weather",
            ),
            Tool(
                name="secret",
                description="秘密操作",
                parameters={"type": "object"},
                func=lambda a: "secret done",
                usage="用户明确要求时用 secret",
            ),
        ]
    )
    log = SessionLog("test")
    llm = MockLLM(
        [
            AssistantOutput(tool_calls=[ToolCall(id="c1", name="secret", arguments="{}")]),
            AssistantOutput(text="done"),
        ]
    )
    return log, tools, llm


def test_callable_system_prompt_refreshes_per_step():
    """system_prompt 是 callable 时,每个 step 现场取 -> 状态变了自动新。"""
    log, tools, llm = make_env_with_two_tools()
    state = {"outfit": "旧"}

    def sys(registry):
        return f"当前穿着: {state['outfit']}"

    loop = AgentLoop(log, llm, tools, system_prompt=sys)
    # 第一步模型调 secret;第二步前系统把 outfit 改了 —— callable 让第二步看到新值
    state["outfit"] = "新"  # 模拟工具副作用发生在第一步之后
    loop.run_turn("来点操作")

    sys_msgs = [m["content"] for call in llm.seen_messages for m in call if m["role"] == "system"]
    assert len(sys_msgs) == 2, sys_msgs  # 两个 step 各取一次
    assert sys_msgs[1] == "当前穿着: 新", sys_msgs  # 第二 step 用 callable 现取


def test_callable_system_prompt_sees_active_registry():
    """system builder 收到本轮 registry -> SYSTEM 用法段与 schema 同批工具(子集一致性)。"""
    log, tools, llm = make_env_with_two_tools()
    seen: list[str] = []

    def sys(registry):
        seen.append(",".join(registry.names()))
        return "你是助手"

    loop = AgentLoop(log, llm, tools, system_prompt=sys)
    only_weather = ToolRegistry([tools.get("weather")])
    loop.run_turn("只查天气", tools=only_weather)

    assert all(names == "weather" for names in seen), seen  # 每 step 都只见 weather


def test_callable_system_prompt_with_full_registry():
    """不传 tools -> builder 拿到全量注册表。"""
    log, tools, llm = make_env_with_two_tools()
    seen: list[str] = []

    def sys(registry):
        seen.append(",".join(registry.names()))
        return "你是助手"

    loop = AgentLoop(log, llm, tools, system_prompt=sys)
    loop.run_turn("普通对话")
    assert all(names == "secret,weather" for names in seen), seen


def test_run_turn_tools_subset_limits_schema_and_execution():
    """run_turn(tools=子集):本轮只见子集 schema;调子集外的工具 -> 静默剔除。"""
    log, tools, llm = make_env_with_two_tools()
    loop = AgentLoop(log, llm, tools)
    only_weather = tools.get("weather")

    # 模型想调 secret(不在本轮子集里)
    result = loop.run_turn("操作", tools=[only_weather])

    # 每步给模型的 schema 只有 weather
    for seen in llm.seen_tools:
        names = [s["function"]["name"] for s in (seen or [])]
        assert names == ["weather"], names

    # secret 调用被静默剔除:不执行、无 tool/result、无错误回喂(不泄露工具名)
    assert result.reason == {"kind": "completed"}
    assert not any(e.type == "tool/result" for e in log.events)
    # 但 chunk 原始真相还在(可观测性)
    chunks = [e for e in log.events if e.type == "assistant/chunk"]
    assert any(e.data["chunk"].get("name") == "secret" for e in chunks)
    assert result.reason == {"kind": "completed"}


def test_run_turn_tools_none_uses_full_registry():
    """不传 tools -> 全量注册表,模型可调任意工具。"""
    log, tools, llm = make_env_with_two_tools()
    loop = AgentLoop(log, llm, tools)
    loop.run_turn("操作")

    names = [s["function"]["name"] for s in (llm.seen_tools[0] or [])]
    assert names == ["weather", "secret"], names  # schemas() 按注册顺序
    res = [e for e in log.events if e.type == "tool/result"][0]
    assert res.data["is_error"] is False


class _CaptureLLM:
    """记录每次 stream 收到的消息与温度/上限覆盖参数(2026-09 真接线用)。"""

    def __init__(self, chunks=None) -> None:
        self.chunks = list(chunks or [
            {"kind": "text", "text": "好。"},
            {"kind": "finish", "reason": "stop"},
        ])
        self.seen_messages: list = []
        self.last_kwargs: dict = {}

    def invoke(self, messages, tools=None, **kwargs):
        raise NotImplementedError

    def stream(self, messages, tools=None, temperature=None, max_tokens=None,
               model=None):
        self.seen_messages.append(messages)
        self.last_kwargs = {"temperature": temperature, "max_tokens": max_tokens,
                            "model": model}
        yield from self.chunks


def test_run_turn_optional_fields_forwarded_to_llm():
    """温度/输出上限/模型覆盖与 SYSTEM 覆盖(角色设定)真传到本轮 LLM 调用。"""
    log = SessionLog("t")
    llm = _CaptureLLM()
    loop = AgentLoop(log, llm, ToolRegistry([]),
                     system_prompt=lambda reg: "旗舰-builder")
    loop.run_turn("你好", temperature=0.5, max_tokens=123, model="deepseek-v4-pro",
                  system_prompt="自定义人格", max_rounds=0)

    assert llm.last_kwargs["temperature"] == 0.5
    assert llm.last_kwargs["max_tokens"] == 123
    assert llm.last_kwargs["model"] == "deepseek-v4-pro"
    # 覆盖串替换 builder 进 SYSTEM;max_rounds=0 -> 全量(没有窗口裁剪)
    assert llm.seen_messages[0][0] == {"role": "system", "content": "自定义人格"}

    # 不带字段 = 默认:不传覆盖参数(None),builder 照常
    log2 = SessionLog("t")
    llm2 = _CaptureLLM()
    loop2 = AgentLoop(log2, llm2, ToolRegistry([]),
                      system_prompt=lambda reg: "旗舰-builder")
    loop2.run_turn("再聊聊")
    assert llm2.last_kwargs == {"temperature": None, "max_tokens": None,
                                "model": None}
    assert llm2.seen_messages[0][0] == {"role": "system", "content": "旗舰-builder"}


def test_usage_and_finish_anchored_on_assistant_message():
    """usage chunk 归一化后锚到本条 assistant/message;finish reason 一并留痕。"""
    log = SessionLog("t")
    usage = {"input_tokens": 12, "cache_read_tokens": 0, "output_tokens": 5,
             "reasoning_tokens": 0, "total_tokens": 17}
    llm = _CaptureLLM([
        {"kind": "text", "text": "今天晴天。"},
        {"kind": "finish", "reason": "stop"},
        {"kind": "usage", "usage": usage},
    ])
    loop = AgentLoop(log, llm, ToolRegistry([]), system_prompt="你是助手")
    loop.run_turn("天气?")

    am = [e for e in log.events if e.type == "assistant/message"]
    assert len(am) == 1
    assert am[0].data["finish"] == "stop"
    assert am[0].data["usage"] == usage
    # chunk 原始层也留了 usage(真相可回放)
    assert any(e.data["chunk"].get("kind") == "usage" for e in log.events
               if e.type == "assistant/chunk")


def test_parse_usage_normalizes_deepseek_and_openai():
    """openai_compat._parse_usage:供应商原始 usage -> 互斥桶(上游一次捕获)。"""
    from core.openai_compat import _parse_usage
    # OpenAI 兼容拼写:prompt_tokens_details.cached_tokens + reasoning 细分
    u = _parse_usage({
        "prompt_tokens": 500, "completion_tokens": 120,
        "prompt_tokens_details": {"cached_tokens": 300},
        "completion_tokens_details": {"reasoning_tokens": 40},
    })
    assert u["input_tokens"] == 200  # 未缓存输入 = prompt - cache 命中
    assert u["cache_read_tokens"] == 300
    assert u["output_tokens"] == 120
    assert u["reasoning_tokens"] == 40
    # DeepSeek 直拼:prompt_cache_hit_tokens / 无 reasoning
    u2 = _parse_usage({"prompt_tokens": 500, "completion_tokens": 10,
                       "prompt_cache_hit_tokens": 500})
    assert u2["input_tokens"] == 0 and u2["cache_read_tokens"] == 500
    assert u2["reasoning_tokens"] == 0
    # 供应商不返回 usage -> None(调用方容错)
    assert _parse_usage(None) is None
    assert _parse_usage("garbage") is None


if __name__ == "__main__":
    test_full_turn_with_tools()
    test_quick_answer_no_tools()
    test_max_steps_guard()
    test_out_of_whitelist_tool_call_is_silently_dropped()
    test_max_tokens_empty_message_not_logged()
    test_callable_system_prompt_refreshes_per_step()
    test_callable_system_prompt_sees_active_registry()
    test_callable_system_prompt_with_full_registry()
    test_run_turn_tools_subset_limits_schema_and_execution()
    test_run_turn_tools_none_uses_full_registry()
    test_run_turn_optional_fields_forwarded_to_llm()
    test_usage_and_finish_anchored_on_assistant_message()
    test_parse_usage_normalizes_deepseek_and_openai()
    print("AgentLoop all tests passed")
