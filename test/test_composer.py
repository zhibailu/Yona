"""SystemComposer 自测:段注册/优先级/启停 + 变量插值 + 工具用法段。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.composer import (
    SystemComposer,
    SystemSection,
    interpolate,
    make_usage_section,
)
from core.tools import Tool, ToolRegistry


def test_interpolate_replaces_known_keys():
    assert interpolate("你好,{owner}", {"owner": "主人"}) == "你好,主人"


def test_interpolate_keeps_unknown_keys():
    """模板引用了没提供的变量 -> 原样保留(可观测兜底,不炸)。"""
    assert interpolate("我是{ghost}", {}) == "我是{ghost}"


def test_interpolate_does_not_hurt_json_braces():
    """模板里的 JSON 花括号示例不能被误伤(str.format 会炸,这里不会)。"""
    tpl = '示例: {"type": "object", "owner": "{owner}"}'
    assert interpolate(tpl, {"owner": "主人"}) == '示例: {"type": "object", "owner": "主人"}'


def test_compose_orders_by_priority():
    c = SystemComposer()
    c.register(SystemSection(name="late", priority=30, template="后来者"))
    c.register(SystemSection(name="first", priority=10, template="先出场"))
    c.register(SystemSection(name="middle", priority=20, template="中间段"))
    assert c.compose() == "先出场\n\n中间段\n\n后来者"


def test_compose_skips_disabled_and_empty():
    c = SystemComposer()
    c.register(SystemSection(name="a", priority=10, template="A"))
    c.register(SystemSection(name="b", priority=20, template="", enabled=True))  # 空内容
    c.register(SystemSection(name="c", priority=30, template="C"))
    c.set_enabled("c", False)
    assert c.compose() == "A"


def test_compose_skips_section_when_producer_returns_none():
    """producer 返回 None/空 -> 该段不出现(如"没有可用的记忆"就不写段)。"""
    c = SystemComposer()
    c.register(SystemSection(name="persona", priority=10, template="我是小夜子"))
    c.register(
        SystemSection(
            name="memories", priority=20, producer=lambda values: None
        )
    )
    assert c.compose() == "我是小夜子"


def test_compose_variable_values_reach_template():
    """{变量} 插值:值源是任意 dict,state 只是其中一种。"""
    c = SystemComposer()
    c.register(SystemSection(name="persona", priority=10, template="你叫{owner}"))
    assert c.compose({"owner": "主人"}) == "你叫主人"


def test_producer_can_close_over_state():
    """producer 闭包拿状态(动态段),比模板更灵活。"""
    state = {"clothes": "卫衣"}

    def _state_text(values):
        return f"[状态] clothes={state['clothes']}"

    c = SystemComposer()
    c.register(SystemSection(name="state", priority=10, producer=_state_text))
    assert c.compose() == "[状态] clothes=卫衣"

    # 状态变了,compose 结果跟着变(不用重建 composer)
    state["clothes"] = "白衬衫"
    assert c.compose() == "[状态] clothes=白衬衫"


def test_register_duplicate_raises():
    c = SystemComposer()
    c.register(SystemSection(name="a", priority=10, template="A"))
    try:
        c.register(SystemSection(name="a", priority=20, template="A2"))
        assert False, "重复注册应抛错"
    except ValueError:
        pass


def test_unregister_removes_section():
    c = SystemComposer()
    c.register(SystemSection(name="a", priority=10, template="A"))
    assert c.unregister("a") is True
    assert c.unregister("a") is False  # 二次移除返回 False
    assert c.compose() == ""


def test_names_lists_enabled_in_order():
    c = SystemComposer()
    c.register(SystemSection(name="b", priority=20, template="B"))
    c.register(SystemSection(name="a", priority=10, template="A"))
    c.register(SystemSection(name="off", priority=30, template="X"))
    c.set_enabled("off", False)
    assert c.names() == ["a", "b"]


def test_make_usage_section_builds_usage_text():
    """工具用法散文从注册表拼成一个段;没 usage 的工具不出现。"""
    registry = ToolRegistry(
        [
            Tool(
                name="get_time",
                description="获取时间",
                parameters={"type": "object"},
                func=lambda a: "now",
                usage="用户问时间时用 get_time",
            ),
            Tool(
                name="no_usage",
                description="没有散文的工具",
                parameters={"type": "object"},
                func=lambda a: "x",
                usage="",
            ),
        ]
    )
    c = SystemComposer()
    c.register(SystemSection(name="persona", priority=10, template="我是小夜子"))
    c.register(make_usage_section(registry, priority=20))
    text = c.compose()
    assert text.startswith("我是小夜子")
    assert "- get_time: 用户问时间时用 get_time" in text
    assert "no_usage" not in text


def test_make_usage_section_omits_when_no_usages():
    registry = ToolRegistry(
        [Tool(name="x", description="x", parameters={"type": "object"}, func=lambda a: "")]
    )
    c = SystemComposer()
    c.register(make_usage_section(registry))
    assert c.compose() == ""


if __name__ == "__main__":
    test_interpolate_replaces_known_keys()
    test_interpolate_keeps_unknown_keys()
    test_interpolate_does_not_hurt_json_braces()
    test_compose_orders_by_priority()
    test_compose_skips_disabled_and_empty()
    test_compose_skips_section_when_producer_returns_none()
    test_compose_variable_values_reach_template()
    test_producer_can_close_over_state()
    test_register_duplicate_raises()
    test_unregister_removes_section()
    test_names_lists_enabled_in_order()
    test_make_usage_section_builds_usage_text()
    test_make_usage_section_omits_when_no_usages()
    print("composer all tests passed")
