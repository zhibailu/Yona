"""Yona 新内核 · 流式组装器(Assembler)

把流式 chunk(增量)安全地拼成消息块。

chunk 形状(内部统一,与厂商无关):
  {"kind": "text",         "text": "..."}                         文本增量
  {"kind": "tool_call",    "index": 0, "id": "...", "name": "...",
                           "arguments_delta": "..."}               工具参数增量(按 index 路由)
  {"kind": "finish",       "reason": "tool_calls"|"stop"|"length"|...}

安全规则(来自 dsh):
  - 多个工具调用靠 index 切分(顺序到达也不串)
  - max-tokens(length)截断时,丢弃 tool-call(截断的执行不安全)
  - 中断时只保留已流出的文本(不伪造工具)
"""

from __future__ import annotations

from typing import Any

# finish 原因:OpenAI 系。**只留 length 这一个常量,因为只有它参与分派。**
#
# 其它 finish 原因**不进代码判断**:这一轮有没有 tool-call,看的是**投影出来的块**
# (`Assembler.blocks()` 产出的 `{"type": "tool-call"}` 块,`core/loop.py` 的
# `_run_turn_locked()` 里 `tool_calls = [b for b in blocks if b.get("type") == "tool-call"]` 按块过滤),
# 不是 finish 串;`"stop"` 更是被"块里没有 tool-call"这一条自然涵盖。
# 唯一必须看 finish 的分支是**截断**(`core/loop.py` 的 `_run_turn_locked()` 里
# `if self._last_finish == FINISH_LENGTH:`)—— 那是唯一一个"块不可信、
# 必须改行为"的原因。
# ⚠️ 引用 loop.py 时**把符号也写上**:那一处行号飘过一次(加注释会整体下移),
#    只写行号的话下一个人会照着数到一个不相干的 `log.append` 上。
#
# 2026-09 清理:原来的 `FINISH_TOOL_CALLS = "tool_calls"` / `FINISH_STOP = "stop"`
# (原 :22-23)全仓零引用 —— 删掉它们不丢任何行为,留着只会让人以为分派看 finish。
FINISH_LENGTH = "length"


class Assembler:
    def __init__(self) -> None:
        self._text: list[str] = []              # 文本增量(OpenAI 系单条 content)
        self._tools: dict[int, dict[str, Any]] = {}  # index -> {id, name, arguments}
        self._tool_order: list[int] = []
        self.finish: str | None = None

    def push(self, chunk: dict[str, Any]) -> None:
        kind = chunk["kind"]
        if kind == "text":
            self._text.append(chunk["text"])
        elif kind == "tool_call":
            idx = chunk["index"]
            if idx not in self._tools:
                self._tools[idx] = {
                    "id": chunk.get("id", ""),
                    "name": chunk.get("name", ""),
                    "arguments": "",
                }
                self._tool_order.append(idx)
            # name 可能只在第一段出现,后到的 arguments_delta 只补参数
            if chunk.get("name"):
                self._tools[idx]["name"] = chunk["name"]
            self._tools[idx]["arguments"] += chunk.get("arguments_delta", "")
        elif kind == "finish":
            self.finish = chunk["reason"]

    def blocks(self) -> list[dict[str, Any]]:
        """安全投影:length 截断时丢弃 tool-call(执行不安全的就不执行)。"""
        blocks: list[dict[str, Any]] = []
        text = "".join(self._text)
        if text:
            blocks.append({"type": "text", "text": text})
        if self.finish != FINISH_LENGTH:
            for idx in self._tool_order:
                t = self._tools[idx]
                blocks.append(
                    {
                        "type": "tool-call",
                        "id": t["id"],
                        "name": t["name"],
                        "arguments": t["arguments"],
                    }
                )
        return blocks

    def interrupted_blocks(self) -> list[dict[str, Any]]:
        """中断时安全收尾:只留已流出的文本,工具调用一律丢弃。"""
        text = "".join(self._text)
        return [{"type": "text", "text": text}] if text.strip() else []
