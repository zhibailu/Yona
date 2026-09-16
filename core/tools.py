"""Yona 新内核 · 工具(Tool + 注册表)—— 工具三件套之一

**"工具该放哪"只有三种答案,由层决定**(2026-09-16 用户问过一次,记这儿):
  - 内核原语(Tool / ToolRegistry)      -> `core/tools.py`        (本文件)
  - 她的动作工具 + 委派工具文案          -> `character/tools.py`   (角色/内容层)
  - 给工人用的能力工具(网络/文件 IO)   -> `server/app/worker_tools.py` (产品层)
不建统一的 `tools/` 目录:那只能落在某一层里,必然要跨层 import,
而 core 的依赖边界是「不 import server/character」。等**同一层内**工具多到
一个文件放不下,再在该层的目录下开 `tools/` 子包 —— 不预建。
"""

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

    ⚠️ func 必须**线程安全**(2026-09-16):同一个 step 里被模型一起叫到的工具
    是**并发**跑的(core/loop.py 的 _execute_tools 走线程池)。func 不许共享
    可变状态、不许假设"别的工具已经跑完了"。工具之间的先后顺序没有保证;
    唯一有保证的是**结果落进日志的顺序 = 模型声明调用的顺序**。
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
