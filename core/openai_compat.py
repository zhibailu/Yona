"""Yona 新内核 · OpenAI 兼容客户端(模型无关)

只认 OpenAI 风格的 chat/completions 协议。任何兼容端点都能用:
DeepSeek / OpenAI / Moonshot / 本地 vLLM / Ollama 等 —— 换 base_url 即可。
base_url、model、api_key 全部由调用方(配置)传入,代码里不写死任何厂商。
"""

from __future__ import annotations

import json
from typing import Any, Iterator

import requests

from .llm import AssistantOutput, ToolCall


def _parse_usage(u) -> dict | None:
    """把供应商原始 usage 归一化成互斥桶(对齐 dsh 的 TokenUsage 语义)。

    一次捕获、永久可用:所有消费方(token 计量/成本/截断率/补写判定)都读
    这个归一化结果,不再回头改本适配器。DeepSeek/OpenAI 两种拼写都认:
      - prompt_tokens 含缓存命中 → input_tokens = prompt − cache_hit(未缓存输入)
      - cached_tokens(prompt_tokens_details, OpenAI 兼容拼写)
        或 prompt_cache_hit_tokens(DeepSeek 直拼)
      - reasoning_tokens(completion_tokens_details 或顶层,细分 output)
    供应商不返回 usage(个别代理)→ 返回 None,调用方容错(启发式兜底)。
    """
    if not isinstance(u, dict):
        return None
    prompt = u.get("prompt_tokens") or 0
    completion = u.get("completion_tokens") or 0
    details = u.get("prompt_tokens_details") or {}
    cached = details.get("cached_tokens")
    if cached is None:
        cached = u.get("prompt_cache_hit_tokens")
    if cached is None:
        cached = 0
    cd = u.get("completion_tokens_details") or {}
    reasoning = cd.get("reasoning_tokens")
    if reasoning is None:
        reasoning = u.get("reasoning_tokens") or 0
    return {
        "input_tokens": max(0, prompt - cached),  # 未缓存输入(计费输入 = 三者之和)
        "cache_read_tokens": cached,  # 缓存命中读(DeepSeek 不报 cache_write,缺省)
        "output_tokens": completion,
        "reasoning_tokens": reasoning or 0,  # 细分,含在 output_tokens 里
        "total_tokens": u.get("total_tokens") or (prompt + completion),
    }


def _text_of(content) -> str:
    """内部 block 列表 -> 纯文本(用于 user/tool 消息和 assistant 的 content 字段)。"""
    if isinstance(content, str):
        return content
    parts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
    return "\n".join(parts)


def _to_wire(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """内部消息 -> OpenAI 线上格式。

    关键转换(对应 dsh 的 serialize):
    - assistant 的 tool-call 块 -> 消息上的独立 tool_calls 字段
    - reasoning 块丢弃(线上不支持输入推理;各家 reasoning 字段各异,不通用)
    - user/tool 的 content 压成字符串
    """
    wire: list[dict[str, Any]] = []
    for msg in messages:
        role = msg["role"]
        content = msg.get("content")
        if role == "system":
            wire.append({"role": "system", "content": _text_of(content)})
        elif role == "user":
            wire.append({"role": "user", "content": _text_of(content)})
        elif role == "assistant":
            blocks = content if isinstance(content, list) else []
            text = _text_of(content)
            tool_calls = [
                {
                    "id": b["id"],
                    "type": "function",
                    "function": {"name": b["name"], "arguments": b.get("arguments", "")},
                }
                for b in blocks
                if isinstance(b, dict) and b.get("type") == "tool-call"
            ]
            entry: dict[str, Any] = {"role": "assistant", "content": text}
            if tool_calls:
                entry["tool_calls"] = tool_calls
            wire.append(entry)
        elif role == "tool":
            wire.append(
                {
                    "role": "tool",
                    "tool_call_id": msg["tool_call_id"],
                    "content": _text_of(content),
                }
            )
        else:
            raise ValueError(f"未知 role: {role}")
    return wire


class OpenAICompatibleLLM:
    """OpenAI 风格 chat/completions 客户端,实现 LLM 协议。"""

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        temperature: float = 0.3,
        max_tokens: int = 1024,
        timeout: float = 60.0,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout

    def invoke(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        model: str | None = None,
    ) -> AssistantOutput:
        body: dict[str, Any] = {
            # model 可被单次调用覆盖(2026-09 同端点换模型 id;None = 实例默认)
            "model": self.model if model is None else model,
            "messages": _to_wire(messages),
            "temperature": self.temperature if temperature is None else temperature,
            "max_tokens": self.max_tokens if max_tokens is None else max_tokens,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"

        try:
            resp = requests.post(
                f"{self.base_url}/chat/completions",
                json=body,
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=self.timeout,
            )
            resp.raise_for_status()
        except requests.RequestException as exc:
            detail = ""
            if exc.response is not None:
                detail = exc.response.text
            raise RuntimeError(
                f"LLM API {exc.response.status_code if exc.response else 'error'}: {detail}"
            ) from exc

        data = resp.json()
        message = data["choices"][0]["message"]
        content = message.get("content") or ""
        # 部分厂商(如 DeepSeek reasoner)在 reasoning_content 里返回推理;
        # 没有就是空串,兼容各家。
        reasoning = message.get("reasoning_content") or ""

        tool_calls: list[ToolCall] = []
        for tc in message.get("tool_calls") or []:
            fn = tc.get("function", {})
            tool_calls.append(
                ToolCall(
                    id=tc.get("id", ""),
                    name=fn.get("name", ""),
                    arguments=fn.get("arguments", "") or "",
                )
            )

        return AssistantOutput(
            reasoning=reasoning,
            text=content,
            tool_calls=tool_calls,
            usage=_parse_usage(data.get("usage")),
            finish_reason=(data.get("choices") or [{}])[0].get("finish_reason"),
        )

    def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        model: str | None = None,
    ) -> Iterator[dict[str, Any]]:
        """流式:stream=True + SSE 解析,逐 chunk 产出。

        chunk 形状(见 core/assembler.py):
          {"kind":"text", "text":...}
          {"kind":"tool_call", "index", "id", "name", "arguments_delta"}
          {"kind":"finish", "reason"}
          {"kind":"usage", "usage": {...}}   —— 本次调用 token 用量(2026-09 加)

        用量获取:OpenAI 兼容 SSE 默认不送 usage,须请求体带
        `stream_options: {"include_usage": true}`;usage 随 finish chunk 或
        末尾独立 usage-only chunk(choices 空)送达。个别代理不支持该字段
        时只缺 usage 一条,正文流不受影响。
        """
        body: dict[str, Any] = {
            # model 可被单次调用覆盖(2026-09 同端点换模型 id;None = 实例默认)
            "model": self.model if model is None else model,
            "messages": _to_wire(messages),
            "temperature": self.temperature if temperature is None else temperature,
            "max_tokens": self.max_tokens if max_tokens is None else max_tokens,
            "stream": True,
            # 2026-09:要 usage 就必须显式要求(DeepSeek/OpenAI 同规)
            "stream_options": {"include_usage": True},
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"

        try:
            resp = requests.post(
                f"{self.base_url}/chat/completions",
                json=body,
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=self.timeout,
                stream=True,
            )
            resp.raise_for_status()
        except requests.RequestException as exc:
            detail = ""
            if exc.response is not None:
                detail = exc.response.text
            raise RuntimeError(
                f"LLM API {exc.response.status_code if exc.response else 'error'}: {detail}"
            ) from exc

        usage: dict | None = None
        try:
            for line in resp.iter_lines(decode_unicode=True):
                if not line:
                    continue
                if line.startswith("data:"):
                    line = line[5:].strip()
                if line == "[DONE]":
                    break
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                # usage:可能随 finish chunk 到达,也可能是末尾 usage-only chunk
                # (choices 空)。归一化一次,末尾统一带出(避免重复)。
                if data.get("usage"):
                    usage = _parse_usage(data["usage"])
                choice = (data.get("choices") or [{}])[0]
                delta = choice.get("delta") or {}
                text = delta.get("content")
                if text:
                    yield {"kind": "text", "text": text}
                for tc in delta.get("tool_calls") or []:
                    fn = tc.get("function") or {}
                    yield {
                        "kind": "tool_call",
                        "index": tc.get("index", 0),
                        "id": tc.get("id", ""),
                        "name": fn.get("name", ""),
                        "arguments_delta": fn.get("arguments", "") or "",
                    }
                finish = choice.get("finish_reason")
                if finish:
                    yield {"kind": "finish", "reason": finish}
        finally:
            resp.close()
        # 末尾独立 usage-only chunk 已存好,这里补发(放在流的最末尾)
        if usage is not None:
            yield {"kind": "usage", "usage": usage}
