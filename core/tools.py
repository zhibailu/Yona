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
        """所有带散文工具的 (name, usage) 对(SYSTEM 段落素材,**按名升序**)。

        2026-09 清理(等价重构):原来是 `for name in self.names()` 再
        `self.get(name)` —— 在**同一个字典上查了两遍**(`names()` 先排一次键,
        `get()` 再逐个取回),而这里要的顺序语义只是"按名排序"。
        改成直接迭代 `_tools.values()`(类内可以直接摸),再按名排序:
        结果逐项相同 —— `_tools` 就是以 `t.name` 为键的,所以 `t.name` 与
        `names()` 给出的那个键**恒等**,排序键与 `names()` 也是同一个。

        ⚠️ 顺序语义**一个字没改**:仍是**按名升序**。别"顺手"改成插入序 ——
        `names()` 是排序的,而 `schemas()` 是插入序的,两者**本来就不同**;
        本段的输出进 SYSTEM,换序会改模型看到的段落顺序(属模型可见变化)。
        要动顺序,得先想清 `schemas()` 那边为什么不跟着动。
        """
        return sorted(
            ((t.name, t.usage) for t in self._tools.values() if t.usage),
            key=lambda pair: pair[0],
        )

    def execute(self, name: str, args: dict[str, Any]) -> tuple[str, bool]:
        """执行工具,返回 (结果文本, 是否出错)。

        ⏸ **这两条错误串会作为 tool/result 回流给模型 —— 但它们是内核协议串,
        不是人格文案。别照"文案住内容层"去搬。**(2026-09 清理时标注。)

        ① 它们是**拼装出来的协议串**,不是"句子":
           - `f"tool unavailable: {name}"` —— 填工具名(`execute` 的入参);
           - `f"tool error: {exc}"` —— 填异常自身的文字(格式由异常类型决定)。
           没有可插值的模板,也没有"称呼/语气"可归内容层 ——
           内容层**没有对应模板**(去 `character/personas.py` /
           `character/tools.py` 也找不到它们,这是对的)。
           措辞本身也刻意是机器口吻:它出现在 tool 槽位,是给模型看的
           **失败事实**,不是她的话。
        ② 两条串的**流向要说清**(这是它们重要的原因):经
           `loop._execute_tools` → `log.append("tool/result", content=[{...text}])`
           → `derive_messages` 投影成 `role="tool"` 消息 → **下一 step 直接进模型上下文**。
           所以改一个字符就是**改模型可见文本**,要走评测,不能顺手改。
        ③ `"tool unavailable"` 这一条还有个**设计含义**:它只在"名字在白名单里、
           但执行期取不到"时出现。**白名单外的调用不会走到这里** ——
           `loop._stream_and_assemble` 在 message 层就静默剔除了(不执行、
           也不喂这个错误),理由是**不给 schema 就当它不存在**、
           别把工具名泄露给模型(见 `docs/decisions/DESIGN.md:39`)。
           别以为这条串是"白名单兜底提示"。
        ④ 要改措辞请**连同测试断言一起改**:
           - `test/test_loop.py` 的 `test_one_failing_tool_does_not_take_down_the_others` 钉着 `"tool error:"` 前缀
             (`.startswith("tool error:")`);
           - `"tool unavailable"` 的字符串本身没有单测断言,但
             `test/route_demo.py`(演示脚本)按它描述第 3 幕行为,改字要跟着改词;
           - `server/app/worker_tools.py` 的注释**照着这条串解释**工人工具的
             契约("抛出去会被 ToolRegistry 压成 `tool error: ...` 一行")。
        """
        tool = self._tools.get(name)
        if tool is None:
            return f"tool unavailable: {name}", True
        try:
            return str(tool.func(args or {})), False
        except Exception as exc:  # noqa: BLE001
            return f"tool error: {exc}", True
