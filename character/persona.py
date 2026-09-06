"""小夜子 · 人设上下文装配(段工厂)

把"人设长什么样"变成一组 SystemSection,交给内核 SystemComposer 拼装:
  1. persona:   静态人设文本(模板,可含 {变量})
  2. world:     世界基础信息(时间等动态死信息,每轮现取注入 —— VISION 决策 8)
  3. state:     当前角色状态(动态 producer,闭包 state —— 状态变了 compose 自动新)
  4. tool usage:工具用法散文(由 make_usage_section 提供,内核通用)

人格 = prompt 段落 + 状态投影,不是状态机代码。
"""

from __future__ import annotations

import time
from typing import Callable

from core.composer import SystemComposer, SystemSection, make_usage_section

from .state import CharacterState

# 小夜子默认段优先级
_PERSONA_PRIORITY = 10
_WORLD_PRIORITY = 15
_STATE_PRIORITY = 20
_USAGE_PRIORITY = 30

# 星期名(中文,按 time.localtime 的 tm_wday 0=周一)
_WEEKDAYS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


def make_world_section(
    now: Callable[[], time.struct_time] | None = None,
) -> SystemSection:
    """世界基础信息段:时间等"动态死信息"每轮现取注入。

    VISION 决策 8:系统本地能知道的,注入;不知道的,才配工具。
    时间不是工具(get_time 退役)——模型每轮都看得见当前时刻,不需要调工具查。

    now: 时间源,默认系统时钟;测试/演示可注入固定时刻(仍是单时间源)。
    """
    clock = now or time.localtime

    def _world_text(values: dict) -> str:
        t = clock()
        return (
            f"[当前时间] {time.strftime('%Y-%m-%d %H:%M', t)} "
            f"{_WEEKDAYS[t.tm_wday]}"
        )

    return SystemSection(
        name="world",
        priority=_WORLD_PRIORITY,
        producer=_world_text,
    )


def make_persona_section(base: str) -> SystemSection:
    """静态人设段:直接放模板文本(支持 {owner} 之类插值)。

    铁律:人设里**不要写死能力清单**("你会看时间、换衣服等")。
    能力唯一来源 = 本轮 schema + 工具用法段(它们跟随 registry)。
    人设写死能力 -> 工具子集变化时,模型仍以为有这工具,会撒谎/表演调用。
    人设只写:性格、语气、关系、动作纪律("没调工具就没做,做不到直说")。
    """
    return SystemSection(
        name="persona",
        priority=_PERSONA_PRIORITY,
        template=base,
    )


def make_state_section(state: CharacterState) -> SystemSection:
    """状态投影段:闭包 state,每次 compose 现取 -> 状态变了不用手动重投影。"""

    def _project(values: dict) -> str | None:
        text = state.project()
        if not text:
            return None  # 无字段时不写这段
        return f"[当前角色状态]\n{text}"

    return SystemSection(
        name="state",
        priority=_STATE_PRIORITY,
        producer=_project,
    )


def build_small_night_composer(
    base: str,
    state: CharacterState,
    registry,
    extra_sections: list[SystemSection] | None = None,
    world_now=None,
) -> SystemComposer:
    """小夜子默认 SYSTEM 装配:人设 + 世界 + 状态 + 工具用法(+ 可选扩展段)。

    extra_sections: 后续能力(RAG 记忆等)从这里挂进来,VISION 的接回点。
    world_now: 世界 section 的时间源(测试/演示注入固定时刻,默认系统时钟)。
    """
    composer = SystemComposer()
    composer.register(make_persona_section(base))
    composer.register(make_world_section(world_now))
    composer.register(make_state_section(state))
    composer.register(make_usage_section(registry, priority=_USAGE_PRIORITY))
    for sec in extra_sections or []:
        composer.register(sec)
    return composer
