"""小夜子 · 人设上下文装配(段工厂)

把"人设长什么样"变成一组 SystemSection,交给内核 SystemComposer 拼装:
  1. persona:   静态人设文本(模板,可含 {变量})—— **唯一常驻段**
  2. situation: 轮次情境段(可选:陪聊/自走/补写)—— 轮的属性,不重述身份
                (2026-09 拍板修正:人设 ≠ 轮的属性,见 character/personas.py)
  3. world:     世界基础信息(时间等动态死信息,每轮现取注入 —— VISION 决策 8)
  4. state:     当前角色状态(动态 producer,闭包 state —— 状态变了 compose 自动新)
  5. tool usage:工具用法散文(由 make_usage_section 提供,内核通用)

人格 = prompt 段落 + 状态投影,不是状态机代码。

本文件有**两个装配工厂**,差异只在**段清单**、机制是同一套:
  - `build_small_night_composer()` —— 她的三类轮(陪聊 / 自走 / 补写);
  - `build_worker_composer()`       —— 工人(一次性执行单元);见该函数 docstring。
"""

from __future__ import annotations

import time
from typing import Callable

from core.composer import SystemComposer, SystemSection, make_usage_section

from .state import CharacterState

# 小夜子默认段优先级
_PERSONA_PRIORITY = 10
# 轮次情境:紧跟人设之后、世界/状态之前。原先这里是个裸 `12`(本文件其余段全用常量),
# 命名是为了让"位置在 persona 与 world 之间"这件事在常量表里看得见,不是别处共享的值。
_SITUATION_PRIORITY = 12
_WORLD_PRIORITY = 15
_STATE_PRIORITY = 20
_USAGE_PRIORITY = 30

# 工人(子运行)的任务说明段:占**人设段那一格**(10)。工人的段清单里没有 persona,
# 任务说明就是它的"最前面那一段" —— 沿用同一个数字,是让"同一个位置、不同清单"
# 在常量表里看得见,不是与 persona 共享值。
_WORKER_TASK_PRIORITY = 10

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

    # 参数名带下划线:本段内容来自闭包 `clock`,**用不到 values**。
    # 但签名不能省 —— Producer 协议要求就是 `Callable[[dict], str | None]`
    # (core/composer.py 的 `Producer` 类型别名),`SystemSection.render` 固定位置传一个 values
    # (core/composer.py 的 `SystemSection.render()`)。省掉参数 = 调用时 TypeError。
    def _world_text(_values: dict) -> str:
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

    # 同 `_world_text`:内容来自闭包 `state`,**用不到 values**;
    # 参数留着只为满足 Producer 协议(core/composer.py 的 `Producer` / `SystemSection.render()`)。
    def _project(_values: dict) -> str | None:
        text = state.project()
        if not text:
            return None  # 无字段时不写这段
        return f"[当前角色状态]\n{text}"

    return SystemSection(
        name="state",
        priority=_STATE_PRIORITY,
        producer=_project,
    )


def make_situation_section(text: str) -> SystemSection:
    """轮次情境段:这一轮是什么处境(陪聊/自走/补写),紧跟人设段之后。

    2026-09 拍板修正:情境 ≠ 人设。人设常驻(PERSONA 一段),轮只附加
    自己的情境段 —— 所以这里**不许重述身份**,只写"此刻如何"。
    文本同样支持 {owner} 之类插值(复用 VALUES)。
    """
    return SystemSection(
        name="situation",
        priority=_SITUATION_PRIORITY,
        template=text,
    )


def build_small_night_composer(
    persona: str,
    state: CharacterState,
    registry,
    situation: str | None = None,
    extra_sections: list[SystemSection] | None = None,
    world_now=None,
) -> SystemComposer:
    """小夜子默认 SYSTEM 装配:常驻人设 + 世界 + 状态 + 工具用法(+ 可选段)。

    persona: 唯一人设文本(PERSONA) —— 三种轮共用同一份,这里只传一份。
    situation: 轮次情境文本(自走/补写轮传,陪聊可不传),作为独立情境段
        拼在人设之后;None = 本段不出现。
    extra_sections: 后续能力(RAG 记忆等)从这里挂进来,VISION 的接回点。
    world_now: 世界 section 的时间源(测试/演示注入固定时刻,默认系统时钟)。
    """
    composer = SystemComposer()
    composer.register(make_persona_section(persona))
    if situation and situation.strip():
        composer.register(make_situation_section(situation))
    composer.register(make_world_section(world_now))
    composer.register(make_state_section(state))
    composer.register(make_usage_section(registry, priority=_USAGE_PRIORITY))
    for sec in extra_sections or []:
        composer.register(sec)
    return composer


def build_worker_composer(task_system: str) -> SystemComposer:
    """工人(一次性执行单元)的 SYSTEM 装配 —— **同一套机制,另一份段清单**。

    为什么需要它(而不是直接用静态串):`server/app/engine.py` 原先把
    `SUBAGENT_SYSTEM + [步数预算]` 拼成**一条字符串**递给 `SubRunSpec.system`,
    而 `core/loop.py` 的 `_build_messages()` 里 `if system_prompt is not None:`
    是**替换**不是叠加 —— 于是 **composer 整条不跑**。后果:工人五天拿不到
    `[可用工具用法]`,而且**零症状**(`description` 走 `tools[]` 数组,与 SYSTEM
    无关,所以它用起工具来"有名字有参数",看着像模像样)。取证与裁决见
    `docs/decisions/TIMELINE.md`「2026-09-23 00:01」;段清单见
    `docs/protocols/SUBAGENT.md` §4.2。

    **段清单(只有两段,别照抄主循环那份)**:

      1. 任务说明 —— 静态,`producer` **原样输出**,不走插值;
      2. 工具用法 —— 跟随**本轮注册表**渲染(这是它能自动跟上白名单的原因)。

    **不给的段**:persona / 情境 / 世界 / 状态 / 时间线 —— 前四者的判据在
    §4.2(世界段 2026-09-23 已拍:噪音太大,不进),时间线对工人无意义。

    ⚠️ **任务说明必须走 `producer`(原样)而不是 `template`(插值)**:它内部可能
    出现 `{` 之类的字面量(`SUBAGENT_BUDGET_TEMPLATE` 的 `{steps}` 是**上游
    已经填好**才传进来的,不是留给这里插的)。`template` 通道会对整段做
    `interpolate`,一旦有未登记的 `{变量}` 就会漏进 SYSTEM —— 这就是
    `core/composer.py` 里 `producer` 与 `template` 两条通道的区别,踩过的坑见
    `docs/decisions/TRAPS.md` 二.2 那一族(称呼被写死在同一机理上)。

    工具用法段传 `registry=None` 是**有意**的:`make_usage_section` 的 producer
    优先取 `values["registry"]`(= 本轮真正开放的那份白名单),没给才退回闭包值。
    这样白名单一变,用法段**自动跟着变**,不需要谁记得同步。
    """
    composer = SystemComposer()
    composer.register(
        SystemSection(
            name="worker_task",
            priority=_WORKER_TASK_PRIORITY,
            producer=lambda _values: task_system.strip() or None,
        )
    )
    composer.register(make_usage_section(priority=_USAGE_PRIORITY))
    return composer
