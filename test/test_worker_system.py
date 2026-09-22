"""工人(子运行)的 **SYSTEM 段清单**自测。

**这个文件是为一次真实事故立的守卫(2026-09-23)。**
工人的 SYSTEM 曾经是一条**静态串**(`SubRunSpec.system` 只声明了 `str`),而
`core/loop.py` 的 `_build_messages()` 里 `if system_prompt is not None:` 是**替换**
不是叠加 —— 于是 composer 整条不跑,工人**五天拿不到 `[可用工具用法]`**,
而且**零症状**:`description` 走 `tools[]` 数组(与 SYSTEM 无关),所以它用起工具来
"有名字、有参数、有说明",看着像模像样;缺的只是"搜完要接 `http_get`"这种跨工具用法,
而那只像"它不够聪明"。取证与裁决:`docs/decisions/TIMELINE.md`「2026-09-23 00:01」。

所以本文件钉的不是"文案好不好",而是**段清单这个结构**:

  ① 工具用法段**必须到**工人手上,且**跟随本轮注册表**(这是静态串做不到的性质);
  ② persona / 世界 / 状态 / 时间线**必须不到**(隔离 + 2026-09-23 已拍);
  ③ 任务说明走**原样输出**(`producer`),不走插值(`template`)—— 差 `{` 就会漏变量;
  ④ 空注册表时用法段**自动消失**(不要留一个空标题);
  ⑤ `SubRunSpec.system` 真的能吃 **builder**(覆盖 `core/` 那一侧的类型收窄);
  ⑥ engine 产品装配**真的接的是 builder**(不是又退回拼串)。

全离线(假模型),确定性、不花钱。
跑: py test/test_worker_system.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

from character import personas as P
from character.persona import build_worker_composer
from core.llm import AssistantOutput
from core.subrun import SubRunSpec, execute
from core.tools import Tool, ToolRegistry
from mock_llm import MockLLM

TASK_SYSTEM = "你是一次性的执行单元:只做交给你的那件事,直接把结果交出来。"


def _tool(name: str, usage: str) -> Tool:
    """造一个带 usage 的假工具(只关心 usage 通道,func 是摆设)。"""
    return Tool(
        name=name,
        description=f"{name} 的说明",
        parameters={"type": "object", "properties": {}},
        func=lambda args: "ok",
        usage=usage,
    )


def _sys(registry, task_system: str = TASK_SYSTEM) -> str:
    """按 engine 的同形状渲染一次(三参 builder)。"""
    composer = build_worker_composer(task_system)
    def builder(reg, source=None, log=None):
        return composer.compose({"registry": reg})
    return builder(registry)


# ---------- ① 工具用法段必须到,且跟随注册表 ----------


def test_tool_usages_reach_the_worker() -> None:
    """**这条就是那五天里缺的东西。**"""
    reg = ToolRegistry([
        _tool("web_search", "只拿到标题和摘要,正文要再用 http_get 打开。"),
        _tool("http_get", "配合 web_search 用;返回里有 truncated 就说明被截断了。"),
    ])
    text = _sys(reg)
    assert "[可用工具用法]" in text
    assert "只拿到标题和摘要" in text
    assert "配合 web_search 用" in text


def test_usage_section_follows_the_registry() -> None:
    """**静态串做不到的那条性质**:白名单换了,用法段跟着换。

    这是本文件最值钱的一条 —— 它一红,就说明有人把装配退回成拼死的字符串了。
    """
    a = _sys(ToolRegistry([_tool("alpha", "A 的用法句。")]))
    b = _sys(ToolRegistry([_tool("alpha", "A 的用法句。"), _tool("beta", "B 的用法句。")]))

    assert "A 的用法句。" in a
    assert "B 的用法句。" not in a        # 没注册就不该出现
    assert "B 的用法句。" in b            # 注册了就自动来
    assert a != b


def test_usage_section_disappears_when_no_tool_has_usage() -> None:
    """空注册表(探针路径 `store=None`、无工具的轮)→ 整段消失,不留空标题。"""
    text = _sys(ToolRegistry([]))
    assert "[可用工具用法]" not in text
    assert text.strip() == TASK_SYSTEM.strip()

    # 工具有、但都没写 usage → 同样不留空标题(段是"有一条才出现")
    text2 = _sys(ToolRegistry([Tool(name="x", description="d",
                                    parameters={"type": "object", "properties": {}},
                                    func=lambda a: "ok")]))
    assert "[可用工具用法]" not in text2


# ---------- ② 不该给的段:一个都不许到 ----------


def test_worker_system_has_no_persona_world_state_or_timeline() -> None:
    """隔离是**结构性**的:工人那台 composer 里根本没有那几个段。

    ⚠️ 与 `test_subagent_wiring.py` 的 `test_persona_leaks_unless_the_subrun_overrides_system`
    是**两件事**:那条测"别把**父的** builder 递给工人"(覆盖路径);
    这条测"工人**自己那台** composer 里就没有 persona"(装配路径)。
    两条同时成立 = 从哪个方向都漏不出人设。
    """
    reg = ToolRegistry([_tool("web_search", "搜。")])
    text = _sys(reg)

    # persona:取人设原文里一段**不含 `{变量}`** 的前缀来判
    assert P.PERSONA[:12] not in text, "人设漏进工人 SYSTEM 了"
    assert "小夜子" not in text, "人设的身份词漏进工人 SYSTEM 了"
    # 世界段 / 状态段 / 时间线的**模型可见标记**(它们在各自的 section 里是字面量)
    assert "[当前时间]" not in text, "世界段漏了(2026-09-23 已拍:噪音太大,不进)"
    assert "[时间线]" not in text
    assert "[状态]" not in text
    # 陪聊情境
    assert "和你是网友关系" not in text

    # 反过来:该在的必须在(免得"隔离"是靠整个 SYSTEM 空掉实现的)
    assert TASK_SYSTEM in text


# ---------- ③ 任务说明是原样输出,不是模板 ----------


def test_task_system_is_verbatim_not_interpolated() -> None:
    """任务说明走 `producer`(原样),不走 `template`(插值)。

    为什么单列一条:`template` 通道会对整段做 `interpolate`,里面有未登记的
    `{...}` 就会**漏进 SYSTEM**(`docs/decisions/TRAPS.md` 二.2 那一族的机理,
    称呼被写死就是同一类)。`SUBAGENT_BUDGET_TEMPLATE` 的 `{steps}` 是**上游已经
    填好**才传进来的,不是留给这里插的 —— 传进来时若还残留 `{`,必须原样保留。
    """
    raw = "任务说明:{steps} 这个词上游已填;{owner} 这个若没值就应原样留着。"
    text = _sys(ToolRegistry([]), task_system=raw)
    assert "{steps}" in text
    assert "{owner}" in text


# ---------- ④ core/ 那一侧:SubRunSpec 真的吃 builder ----------


def test_subrun_spec_accepts_a_builder_and_the_model_gets_it() -> None:
    """端到端接缝:**builder 传进 `SubRunSpec.system`,模型真的收到渲染结果。**

    这条覆盖 `core/subrun.py` 的类型收窄(`system: str` → `str | Callable`)——
    收窄回去,这里必红(`_build_messages` 拿到 callable 会原样当字符串用)。
    """
    composer = build_worker_composer(TASK_SYSTEM)
    def builder(reg, source=None, log=None):
        return composer.compose({"registry": reg})

    llm = MockLLM([AssistantOutput(text="好了")])
    rec = execute(
        SubRunSpec(
            task="查点东西",
            system=builder,
            tools=[_tool("web_search", "只拿到标题和摘要。")],
        ),
        llm,
    )
    assert rec.status == "completed"

    system_text = llm.seen_messages[0][0]["content"]
    assert "[可用工具用法]" in system_text
    assert "只拿到标题和摘要。" in system_text
    assert P.PERSONA[:12] not in system_text

    # 静态串那条路**照旧能用**(探针、实验台还在用它,别为了修这条把那条拆了)
    llm2 = MockLLM([AssistantOutput(text="好了")])
    execute(SubRunSpec(task="t", system="你就是个执行单元。"), llm2)
    assert llm2.seen_messages[0][0]["content"] == "你就是个执行单元。"


# ---------- ⑤ 产品装配处:engine 接的必须是 builder ----------


def test_engine_wires_the_worker_builder_not_a_string() -> None:
    """产品那一侧的守卫 —— **这条一红,就是有人把 `system=` 退回成拼串了。**

    真实现:`server/app/engine.py` 的 `_worker_system`(三参 builder)+ `_worker_composer`。
    """
    from core.loop import _builder_arity
    import server.app.engine as eng

    assert callable(eng._worker_system), "engine 又把工人的 system 写回成静态串了"
    assert _builder_arity(eng._worker_system) == 3, "builder 该是三参(拿得到本轮 registry)"

    text = eng._worker_system(eng._worker_tools)
    entries = eng._worker_tools.usage_entries()
    assert entries, "工人的白名单里一个带 usage 的工具都没有 —— 接线断了"
    for name, usage in entries:
        assert usage in text, f"{name} 的 usage 没到工人手上"

    # 任务说明与步数预算**都还在**(修这条不能把原来对的东西弄丢)
    assert P.SUBAGENT_SYSTEM.split("。")[0] in text
    assert str(eng.SUBAGENT_MAX_STEPS) in text


def test_run_worker_actually_hands_the_builder_to_subrun() -> None:
    """**产品装配处的真守卫** —— 从 `engine._run_worker` 进去,看模型收到什么。

    为什么必须单列这一条(而不是只测 `_worker_system` 这个函数):
    上面那条只证明**那个函数存在且渲染正确**,它证明不了 `_run_worker` 真的把它
    **传下去了** —— 有人把 `system=` 改回"事先拼好的串",上面那条**照样绿**,
    而这个文件本该拦住的事故会原样复现。所以这里走**真实产品路径**:
    `_run_worker` → `SubRunSpec` → `execute()` → `AgentLoop` → 模型。
    (用 MockLLM 顶掉连接:`server/app/engine.py` 的 `_worker_llm["llm"]` 是晚绑定的,
    注入即可;`_sid_now()` 在测试里恒 None → 不落盘。离线、确定性、不花钱。)
    """
    import server.app.engine as eng

    fake = MockLLM([AssistantOutput(text="结论:查到了。")])
    saved = eng._worker_llm.get("llm")
    eng._worker_llm["llm"] = fake
    try:
        rec = eng._run_worker("随便一件活", "测试用标签")
    finally:
        eng._worker_llm["llm"] = saved

    assert rec["status"] == "completed", rec
    system_text = fake.seen_messages[0][0]["content"]

    assert "[可用工具用法]" in system_text, (
        "工人拿到的 SYSTEM 里没有 [可用工具用法] —— `_run_worker` 大概又把 system= "
        "写回成拼好的字符串了(那正是 2026-09-23 修掉的事故:composer 不跑、零症状)"
    )
    for name, usage in eng._worker_tools.usage_entries():
        assert usage in system_text, f"{name} 的 usage 没到工人手上"

    # 该给的在、不该给的都不在(一次把段清单钉住)
    assert "你是一次性的执行单元" in system_text
    assert str(eng.SUBAGENT_MAX_STEPS) in system_text
    assert P.PERSONA[:12] not in system_text
    assert "[当前时间]" not in system_text


if __name__ == "__main__":
    started = time.time()
    test_tool_usages_reach_the_worker()
    test_usage_section_follows_the_registry()
    test_usage_section_disappears_when_no_tool_has_usage()
    test_worker_system_has_no_persona_world_state_or_timeline()
    test_task_system_is_verbatim_not_interpolated()
    test_subrun_spec_accepts_a_builder_and_the_model_gets_it()
    test_engine_wires_the_worker_builder_not_a_string()
    test_run_worker_actually_hands_the_builder_to_subrun()
    print(f"worker system all tests passed ({time.time() - started:.2f}s)")
