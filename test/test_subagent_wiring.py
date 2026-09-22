"""子代理**接线**自测 —— 子运行插进产品装配时,哪几件事必须先定、为什么。

前置事实(已在 core/subrun.py 里成立):它是能跑的执行器,test_subrun.py 已证明
"隔离 / 不带人格 / 会死 / 血缘"四条行为。**本文件不测它。**
本文件测的是**另一件事**:它能跑 ≠ 它接得上。接缝在产品装配处,只有四件事:

  ① 在工具体里再跑一轮,为什么必须换 AgentLoop 实例?          -> 锁(硬约束)
  ② 复用装配时,SYSTEM 能不能一起复用?                        -> 不能,人格会漏
                                             (工人用的是**自己那台** composer:
                                              `character/persona.py` 的
                                              `build_worker_composer`;段清单的守卫在
                                              `test/test_worker_system.py`)
  ③ 工人轮在日志里认不认得出来?                                -> 现在认不出来
  ④ 工具注册进产品那份 module 级 registry 会怎样?              -> 重跑就炸
  ⑤ retain_result 什么时候被收集?                             -> 构造时快照

全离线(脚本模型),确定性、可重复 —— 证明的是**结构**,不是行为。
真行为看 `py test/subrun_probe.py --real --acts R2`。

跑: py test/test_subagent_wiring.py
"""

from __future__ import annotations

import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

from core.llm import AssistantOutput, ToolCall
from core.loop import AgentLoop
from core.session_log import SessionLog
from core.tools import Tool, ToolRegistry
from core.subrun import SubRunSpec, SubRunStore, execute
from mock_llm import MockLLM

# ---------- 内容层文案(测试自带;core/ 与 character/ 里一个字都没有) ----------

SUB_SYSTEM = "你是一次性执行单元:只完成任务、直接给结论,不寒暄、不反问。"
PARENT_SYSTEM = "你是小夜子,一个和用户很熟的陪伴型助手,说话自然、简短。"
PERSONA_TEXT = "【人设】小夜子:说话自然、简短,不说客套话。"  # 模拟 character/personas.PERSONA
CHAT_SITUATION = "【情境】主人正在跟你说话。"


def make_product_builder(hits: list) -> object:
    """模拟 server/app/engine.py 的 sys_by_source 形状(三参 builder,按 source 选情境)。

    真实实现见 server/app/engine.py 的 `_build_engine()` 里那个 `sys_by_source(...)` builder。**它唯一的缺陷是:认不出"工人轮"** ——
    source 不是 "self" 就一律当陪聊轮,发人格。这不是 bug,
    因为它当初只为"同一个她的三种轮"(陪聊/自走/补写)写的。
    hits 记录它被谁问过,用来证明覆盖路径真的绕开了它。
    """

    def sys_by_source(registry, source, log=None) -> str:
        hits.append(source)
        situation = "【情境】你一个人待着。" if source == "self" else CHAT_SITUATION
        return f"{PERSONA_TEXT}\n{situation}"

    return sys_by_source


def make_launch_tool(func) -> Tool:
    """派活的工具壳(字段与 subrun_probe 的同形,便于两边对照)。"""
    return Tool(
        name="launch_subagent",
        description="把一个不便在当下对话里做的活派给一次性执行单元,拿回它的结论。",
        parameters={
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "要它做什么"},
                "label": {"type": "string", "description": "一行标签"},
            },
            "required": ["task"],
        },
        func=func,
        # core/tools.py 的 `Tool.retain_result` 字段:「True = 已结束轮次里也保留本工具的痕迹
        # (适合 subagent 委派、不可重查的查询)」—— 血缘必须在视图里活下来。
        retain_result=True,
    )


def _store(name: str) -> SubRunStore:
    """临时 run store —— 系统 temp 下,不往仓库里写任何东西。"""
    path = Path(tempfile.gettempdir()) / "yona-subagent-wiring" / name
    if path.exists():
        for old in path.glob("*.jsonl"):
            old.unlink()
    path.mkdir(parents=True, exist_ok=True)
    return SubRunStore(path)


# ---------- ① 换实例:这条路通 ----------


def test_fresh_instance_inside_a_tool_works() -> None:
    """工具体里跑子运行 = 换一个新 AgentLoop 实例 —— 通,父那一轮照常收尾。

    "复用装配"的准确含义:复用 **llm / 工具 / 执行器**,
    **不复用那个实例**。所以父这一轮不该因为派了活就变慢或变脏。
    """
    store = _store("fresh")
    sub_llm = MockLLM([AssistantOutput(text="结论:三条要点")])
    parent_llm = MockLLM([
        AssistantOutput(text="你等我理一下", tool_calls=[
            ToolCall(id="c1", name="launch_subagent",
                     arguments='{"task":"理一下今天的记录","label":"整理"}')]),
        AssistantOutput(text="理好了:三条要点"),
    ])
    ran: list = []

    def launch(args: dict) -> str:
        rec = execute(
            SubRunSpec(task=str(args.get("task", "")), system=SUB_SYSTEM,
                       label=str(args.get("label", "")), parent="session:1", max_steps=2),
            sub_llm, run_id="sub-fresh-1", store=store,
        )
        ran.append(rec)
        return f"run_id={rec.run_id} status={rec.status} output={rec.output}"

    tools = ToolRegistry([make_launch_tool(launch)])
    parent_log = SessionLog("session:1")
    parent = AgentLoop(parent_log, parent_llm, tools,
                       system_prompt=PARENT_SYSTEM, max_steps=4)
    result = parent.run_turn("帮我理一下今天记的东西", source="user")

    assert result.reason["kind"] == "completed", result.reason
    assert ran and ran[0].status == "completed"
    assert ran[0].output == "结论:三条要点"

    # 父日志的完整事件序列:一趟普通工具轮。子运行自己的事件一条都没进来。
    # (只看非 chunk 事件 —— chunk 是原始真相层,数量随流式细节变。)
    assert [e.type for e in parent_log.events
            if e.type != "assistant/chunk"] == [
        "turn/start", "user/message",
        "step/start", "assistant/message", "tool/call", "tool/result", "step/end",
        "step/start", "assistant/message", "step/end",
        "turn/end",
    ]
    # 血缘:父这边只有**一行** tool/result,顺着 run_id 能追到 store
    lineage = parent_log.of_type("tool/result")[0].data["content"][0]["text"]
    assert "sub-fresh-1" in lineage
    assert store.load("sub-fresh-1") is not None


# ---------- ② 同实例:这条路是死的 ----------


def test_same_instance_inside_a_tool_deadlocks() -> None:
    """反例:同一个 loop 实例在工具里再跑一轮 = 永久卡死。

    这不是洁癖,是硬约束:
      core/loop.py  AgentLoop.__init__:  self._turn_lock = threading.Lock()   # 不可重入
      core/loop.py  run_turn():          with self._turn_lock: ...            # 整个 turn 都在锁里
    工具执行发生在 run_turn 的 with 块内,所以"父的工具体里调父自己的
    run_turn"在类型上就是死的 —— 只有换实例一条路。

    观察方式:重入那一轮放在另一个线程里跑,1 秒后看它是否还堵在锁上。
    (它此后会一直被堵着;daemon 线程,进程退出即消失。)
    """
    state: dict = {}
    holder: dict = {}

    def probe(args: dict) -> str:
        if state.get("spawned"):
            # 只派一次:否则被释放后的重入轮会再派一次,无限套娃
            return "已在锁上,不重复派"

        def reenter() -> None:
            holder["loop"].run_turn("在工具体里再跑一轮", source="user")
            state["returned"] = True

        state["spawned"] = True
        thread = threading.Thread(target=reenter, daemon=True)
        thread.start()
        thread.join(timeout=1.0)
        # 快照必须在这里取:父轮一结束锁就释放,重入轮随后会真的跑起来并改 state
        state["snapshot"] = {"alive_after_1s": thread.is_alive(),
                             "returned": "returned" in state}
        return "工具体没等到锁"

    tools = ToolRegistry([Tool(name="probe", description="探针",
                               parameters={"type": "object"}, func=probe)])
    llm = MockLLM([
        AssistantOutput(tool_calls=[ToolCall(id="c1", name="probe", arguments="{}")]),
        AssistantOutput(text="结束"),
    ])
    loop = AgentLoop(SessionLog("session:lock"), llm, tools,
                     system_prompt="父 SYSTEM", max_steps=3)
    holder["loop"] = loop
    result = loop.run_turn("派个活", source="user")

    assert result.reason["kind"] == "completed"  # 父这一轮自己没被卡住
    assert state["snapshot"] == {"alive_after_1s": True, "returned": False}


# ---------- ③ SYSTEM 不能一起复用 ----------


def test_persona_leaks_unless_the_subrun_overrides_system() -> None:
    """复用"装配"有一条红线:**llm 和工具可以共用,SYSTEM 必须换掉。**

    把子运行建成 AgentLoop(sub_log, llm, _tools, system_prompt=sys_by_source)
    是个很顺手的写法(毕竟 sys_by_source 就是"这具装配的 SYSTEM")——
    但子运行会因此**穿着小夜子的人设去干活**,而它本该是个匿名执行单元。

    红线是**"别把父的装配给它"**,不是**"它不能有装配"**。这两句看着像,差得远:
    后者会让工人退化成一条静态串,而那正是 2026-09-23 修掉的事故(工人五天拿不到
    `[可用工具用法]`,且零症状 —— 见 `docs/decisions/TIMELINE.md`「2026-09-23 00:01」)。

    本函数测的是**覆盖路径**:每轮显式传 `system_prompt=` 任务书
    (`core/loop.py` 的 `_build_messages()` 里 `if system_prompt is not None:` 那条),
    **父 builder 不被问**。
    ⚠️ 产品现在走的**不是**这条路 —— 工人有**自己那台** composer
    (`character/persona.py` 的 `build_worker_composer`,经 `engine._worker_system` 传进
    `SubRunSpec.system`)。那条装配路径的守卫在 `test/test_worker_system.py`,
    两条同时成立 = 从哪个方向都漏不出人设。
    """
    hits: list = []
    builder = make_product_builder(hits)
    task = "把这段记录整理成三条要点"

    # (a) 反例:子运行复用产品 builder
    naive_llm = MockLLM([AssistantOutput(text="好")])
    AgentLoop(SessionLog("subrun:naive"), naive_llm, ToolRegistry([]),
              system_prompt=builder).run_turn(task, source="user")
    naive_system = naive_llm.seen_messages[0][0]["content"]
    assert naive_system.startswith(PERSONA_TEXT)   # 人格漏进来了
    assert hits == ["user"]

    # (b) 正解:每轮覆盖 SYSTEM
    good_llm = MockLLM([AssistantOutput(text="好")])
    AgentLoop(SessionLog("subrun:good"), good_llm, ToolRegistry([]),
              system_prompt=builder).run_turn(task, source="user",
                                              system_prompt=SUB_SYSTEM)
    assert good_llm.seen_messages[0][0]["content"] == SUB_SYSTEM
    assert PERSONA_TEXT not in good_llm.seen_messages[0][0]["content"]
    assert hits == ["user"]   # builder 没被 (b) 问过 —— 覆盖真的绕开了它


# ---------- ④ 工人轮在日志里认不出来(现状) ----------


def test_subrun_turn_is_indistinguishable_from_a_chat_turn() -> None:
    """现状记录:子运行的 turn/start 记的是 source="user"(core/subrun.py 的 `execute()` 里
    `loop.run_turn(spec.task, source="user", ...)` 那行)。

    意思是**主日志里"工人轮"和"真人聊天轮"长得一模一样** ——
    与 TOOL_VISIBILITY.md §3 那个"自走与补写撞在 source='self' 上"是同一类问题,
    只是换了一层。这里如实钉住现状,不正名:改成什么键由 L2 判定键一起拍。
    """
    store = _store("source")
    rec = execute(SubRunSpec(task="干活", system=SUB_SYSTEM),
                  MockLLM([AssistantOutput(text="好了")]), store=store)
    starts = [e for e in rec.events if e["type"] == "turn/start"]
    assert len(starts) == 1
    assert starts[0]["data"]["source"] == "user"


# ---------- ⑤ 注册进产品那份 registry ----------


def test_registering_into_the_persistent_registry_breaks_on_rebuild() -> None:
    """产品那份 registry 是 **module 级单例**(engine.py 的 `_tools = ToolRegistry(...)`),而 _build_engine 会重跑
    (换连接 / 重载)。所以"在 _build_engine 里 register 一次"是错的:
    第二次就 ValueError,而且它挂在**换连接**这条路上 —— 用户看不见的炸法。

    能做的最小安全形状:工具只注册一次,llm 靠**可变句柄晚绑定**,换连接时改句柄。
    (产品最终怎么落是另一回事 —— 这里只证明约束真实存在。)
    """
    registry = ToolRegistry([])

    def naive_build(llm: str) -> str:
        registry.register(make_launch_tool(lambda args, l=llm: f"用了 {l}"))
        return llm

    naive_build("llm-A")
    failure = ""
    try:
        naive_build("llm-B")
    except ValueError as exc:
        failure = str(exc)
    assert "已注册" in failure, failure   # core/tools.py 的 ToolRegistry.register()

    # 安全形状:注册一次,句柄晚绑定
    safe = ToolRegistry([])
    runtime: dict = {"llm": None}
    safe.register(make_launch_tool(lambda args: f"用了 {runtime['llm']}"))
    runtime["llm"] = "llm-A"
    assert safe.execute("launch_subagent", {})[0] == "用了 llm-A"
    runtime["llm"] = "llm-B"          # 换连接:只改句柄
    assert safe.execute("launch_subagent", {})[0] == "用了 llm-B"


# ---------- ⑥ retain 的收集时机 ----------


def test_retain_is_snapshotted_when_the_loop_is_built() -> None:
    """retain_result 的收集发生在 **AgentLoop 构造时**(core/loop.py 的 `AgentLoop.__init__` 里 `self._retained` 快照),之后不刷新。

    效果:晚注册进 registry 的 launch_subagent 不在 _retained 里,
    它的 tool/result 会在后续轮的折叠视图里被折掉 —— 血缘从**视图**消失
    (日志原文还在,折叠是视图不是日志)。
    所以"注册工具"必须发生在"建 loop"之前。
    """
    early = ToolRegistry([make_launch_tool(lambda args: "ok")])
    loop_early = AgentLoop(SessionLog("early"), MockLLM([]), early)
    assert "launch_subagent" in loop_early._retained

    late = ToolRegistry([])
    loop_late = AgentLoop(SessionLog("late"), MockLLM([]), late)
    late.register(make_launch_tool(lambda args: "ok"))
    assert loop_late.tools.names() == ["launch_subagent"]      # registry 里有了
    assert "launch_subagent" not in loop_late._retained        # 但快照里没有


if __name__ == "__main__":
    started = time.time()
    test_fresh_instance_inside_a_tool_works()
    test_same_instance_inside_a_tool_deadlocks()
    test_persona_leaks_unless_the_subrun_overrides_system()
    test_subrun_turn_is_indistinguishable_from_a_chat_turn()
    test_registering_into_the_persistent_registry_breaks_on_rebuild()
    test_retain_is_snapshotted_when_the_loop_is_built()
    print(f"subagent wiring all tests passed ({time.time() - started:.2f}s)")
