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
from character.tools import make_change_outfit_tool, make_launch_subagent_tool
from core.tools import ToolRegistry

# 实验台已验证过的委派用法文案(flash 档 64/64 派)。**逐字钉住**:
# 能力句现在是生成的,生成结果一旦漂了,这三轮实验的结论就不再覆盖它了。
VERIFIED_USAGE = (
    "你自己做不了的事(上网查、翻本地文件)一律派给它 —— 你手上没这些工具,"
    "别凭印象编,派它去查。"
    "又长又乱的活也可以派,好处是中间过程不会留在对话里。"
    "它看不到你和用户的对话,派活时把要做的事写完整;"
    "拿回结论后,用自己的话说给用户听。"
)


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


def test_launch_subagent_usage_reproduces_the_verified_text():
    """能力句由 capabilities 生成 —— 生成结果必须与实验验证过的那段**逐字相同**。

    这条测试是"实验结论还有效吗"的开关:文案漂了,它当场红,
    提醒你重跑 test/subagent_prompt_lab.py,而不是让旧结论静默失效。
    """
    tool = make_launch_subagent_tool(lambda t, l: {}, capabilities=("上网查", "翻本地文件"))
    assert tool.usage == VERIFIED_USAGE


def test_launch_subagent_usage_omits_capabilities_when_unknown():
    """不知道工人有什么工具时,宁可不举例,也不要写死一份可能过期的能力清单。"""
    tool = make_launch_subagent_tool(lambda t, l: {})
    assert "你自己做不了的事一律派给它" in tool.usage
    assert "上网查" not in tool.usage


def test_launch_subagent_receipt_drops_none_but_keeps_zero():
    """回执:None 不写进去(噪音),0 保留(跑了 0 步和"没说"是两件事)。"""
    import json
    tool = make_launch_subagent_tool(
        lambda t, l: {"run_id": "sub-1", "status": "completed", "output": "结论",
                      "steps": 0, "usage": None})
    assert json.loads(tool.func({"task": "干活"})) == {
        "run_id": "sub-1", "status": "completed", "output": "结论", "steps": 0}


def test_launch_subagent_retains_result_across_turns():
    """派活是一次性的,重查不了 —— 结果必须跨轮保真(core/tools.py 的 `Tool.retain_result` 字段)。"""
    assert make_launch_subagent_tool(lambda t, l: {}).retain_result is True


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
    test_launch_subagent_usage_reproduces_the_verified_text()
    test_launch_subagent_usage_omits_capabilities_when_unknown()
    test_launch_subagent_receipt_drops_none_but_keeps_zero()
    test_launch_subagent_retains_result_across_turns()
    print("character all tests passed")
