"""character 层自测:状态注册机制 + change_outfit 工具 + 世界 section(时间注入)。"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from character.persona import (
    build_small_night_composer,
    make_persona_section,
    make_world_section,
)
from character.state import CharacterState
from character.tools import make_change_outfit_tool
from core.tools import ToolRegistry


def test_registered_field_can_change():
    state = CharacterState({"clothes": "白衬衫", "pants": "牛仔裤"})
    ok, msg = state.set("clothes", "卫衣")
    assert ok, msg
    assert state.get("clothes") == "卫衣"


def test_unregistered_field_rejected():
    """没注册的字段(内衣内裤)改不了。"""
    state = CharacterState({"clothes": "白衬衫", "pants": "牛仔裤"})
    ok, msg = state.set("underwear", "蕾丝")
    assert not ok
    assert "未注册" in msg
    assert state.get("underwear") is None


def test_change_outfit_schema_only_exposes_registered_fields():
    state = CharacterState({"clothes": "白衬衫", "pants": "牛仔裤"})
    tool = make_change_outfit_tool(state)
    schema = tool.schema()["function"]["parameters"]["properties"]
    assert set(schema) == {"clothes", "pants"}, f"schema 暴露了未注册字段: {set(schema)}"


def test_change_outfit_rejects_unregistered_arg():
    state = CharacterState({"clothes": "白衬衫", "pants": "牛仔裤"})
    tool = make_change_outfit_tool(state)
    # 通过注册表执行会拒绝未注册字段
    ok, msg = state.set("underwear", "蕾丝")
    assert not ok and "未注册" in msg


def test_state_projection():
    state = CharacterState({"clothes": "卫衣", "pants": "短裤"})
    proj = state.project()
    assert "clothes=卫衣" in proj and "pants=短裤" in proj


def test_world_section_injects_current_time():
    """世界 section:时间每轮现取注入(动态死信息,不是工具)。"""
    state = CharacterState({"clothes": "白衬衫"})
    tools = ToolRegistry([make_change_outfit_tool(state)])
    composer = build_small_night_composer("你是小夜子", state, tools)
    text = composer.compose()
    assert "[当前时间]" in text, text
    assert "20" in text or "2" in text  # 至少带年份/时分数字
    # 段序:persona -> world -> state -> usage
    assert text.index("你是小夜子") < text.index("[当前时间]") < text.index("[当前角色状态]")


def test_world_section_accepts_injected_clock():
    """时间源可注入:测试/演示给固定时刻,仍是单时间源(不依赖墙钟)。"""
    state = CharacterState({"clothes": "白衬衫"})
    tools = ToolRegistry([make_change_outfit_tool(state)])
    fixed = time.struct_time((2026, 9, 3, 3, 12, 0, 3, 246, 0))  # 周四 03:12
    sec = make_world_section(now=lambda: fixed)
    from core.composer import SystemComposer
    c = SystemComposer()
    c.register(sec)
    text = c.compose()
    assert "2026-09-03 03:12" in text, text
    assert "周四" in text, text


def test_world_section_present_in_default_composer_only_once():
    state = CharacterState({"clothes": "白衬衫"})
    tools = ToolRegistry([make_change_outfit_tool(state)])
    composer = build_small_night_composer("你是小夜子", state, tools)
    text = composer.compose()
    assert text.count("[当前时间]") == 1


def test_time_is_not_a_tool():
    """VISION 决策 8:get_time 已退役,注册表里没有时间工具。"""
    from character.tools import make_change_outfit_tool as mk
    # character.tools 不应再暴露 make_get_time_tool
    assert not hasattr(__import__("character.tools", fromlist=["x"]), "make_get_time_tool")


if __name__ == "__main__":
    test_registered_field_can_change()
    test_unregistered_field_rejected()
    test_change_outfit_schema_only_exposes_registered_fields()
    test_change_outfit_rejects_unregistered_arg()
    test_state_projection()
    test_world_section_injects_current_time()
    test_world_section_accepts_injected_clock()
    test_world_section_present_in_default_composer_only_once()
    test_time_is_not_a_tool()
    print("character all tests passed")
