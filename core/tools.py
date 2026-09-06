"""Yona 新内核 · 工具(Tool + 注册表)—— 工具三件套之一"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class Tool:
    """一个工具 = schema(名/描述/参数) + 核心逻辑(func) + 用法散文(usage)。

    schema 和 usage 是给模型的两种信息:
    - schema 进 tools[] 数组,模型用它做调用格式
    - usage 进 SYSTEM 段落,教模型"怎么用得好"

    retain_result: 工具自己声明"我的结果要不要跨轮保真"。
    True = 已结束轮次里也保留本工具的痕迹(适合 subagent 委派、不可重查的查询);
    False(默认)= 已结束轮次里折叠本工具的痕迹(干净视图,需要就现调)。
    """

    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema
    func: Callable[[dict[str, Any]], Any]
    usage: str = ""  # 可选的 SYSTEM 用法散文
    retain_result: bool = False

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolRegistry:
    """工具注册表:注册、列出 schema、按名执行。"""

    def __init__(self, tools: list[Tool] | None = None) -> None:
        self._tools: dict[str, Tool] = {t.name: t for t in (tools or [])}

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"tool '{tool.name}' 已注册")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return sorted(self._tools)

    def schemas(self) -> list[dict[str, Any]]:
        return [t.schema() for t in self._tools.values()]

    def usage_entries(self) -> list[tuple[str, str]]:
        """所有带散文工具的 (name, usage) 对(SYSTEM 段落素材,按名排序)。"""
        return [
            (name, tool.usage)
            for name in self.names()
            if (tool := self.get(name)) is not None and tool.usage
        ]

    def execute(self, name: str, args: dict[str, Any]) -> tuple[str, bool]:
        """执行工具,返回 (结果文本, 是否出错)。"""
        tool = self._tools.get(name)
        if tool is None:
            return f"tool unavailable: {name}", True
        try:
            return str(tool.func(args or {})), False
        except Exception as exc:  # noqa: BLE001
            return f"tool error: {exc}", True
