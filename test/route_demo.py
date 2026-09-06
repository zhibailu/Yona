"""设计路线效果演示 —— 跑: py test/route_demo.py

路线(已定,非草案):
  1. 折叠视图默认开(fold_tool_traces=True):已结束轮的非保真工具痕迹不进模型输入
     -> 模型看不到历史里的旧工具,不会"误调子集外工具";retain_result=True 的工具保留
  2. 忠实视图保留为选项(fold_tool_traces=False):要全文审计/复现时用
  3. 执行层只认本轮白名单:模型硬调白名单外工具 -> tool unavailable,状态一分没动
  4. 撒谎不防,只兜底:模型声称完成但没调工具 -> 无 tool/call 铁证 + 状态不变,
     下一轮状态段(每轮注入)显示真相,冲突肉眼可见
  5. 动作型工具(改状态的)永远全量给,不子集化;子集只用于信息型工具的成本/安全控制

本 demo 四幕,全部 mock(剧本可控),展示每一条的实际效果。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

from core.composer import SystemComposer, SystemSection, make_usage_section
from core.llm import AssistantOutput, ToolCall
from core.loop import AgentLoop
from core.session_log import SessionLog
from core.tools import Tool, ToolRegistry
from mock_llm import MockLLM

# ---------------- 工具与状态 ----------------

state = {"clothes": "白衬衫", "pants": "牛仔裤"}


def _get_time(args: dict) -> str:
    return "2026-09-03 01:09:34"


def _change_outfit(args: dict) -> str:
    for k, v in (args or {}).items():
        state[k] = str(v)
    return f"已更换穿着: {state['clothes']} + {state['pants']}"


def build_tools() -> ToolRegistry:
    """动作型(change_outfit,不子集化)+ 信息型(get_time)+ 保真委派(subagent)。"""
    return ToolRegistry(
        [
            Tool(
                name="get_time", description="获取当前本地时间",
                parameters={"type": "object"},
                func=_get_time,
                usage="用户问时间时用 get_time,不要凭记忆猜时间。",
                retain_result=False,  # 折叠:需要就现调
            ),
            Tool(
                name="change_outfit", description="更换角色穿着(动作型,永远全量)",
                parameters={"type": "object",
                            "properties": {"clothes": {"type": "string"},
                                           "pants": {"type": "string"}}},
                func=_change_outfit,
                usage="用户让换衣服时用 change_outfit,调完把穿着告诉用户。",
                retain_result=False,  # 状态在状态段,痕迹可折叠
            ),
            Tool(
                name="launch_subagent", description="委派子任务给 subagent",
                parameters={"type": "object",
                            "properties": {"task": {"type": "string"}}},
                func=lambda a: f"subagent 完成: {a.get('task')}",
                usage="复杂任务委派给 subagent。",
                retain_result=True,  # 保真:子任务结果要跨轮被引用
            ),
        ]
    )


def build_composer(registry: ToolRegistry) -> SystemComposer:
    c = SystemComposer()
    c.register(SystemSection(name="persona", priority=10,
                             template="你是小夜子,温柔体贴的 AI 伴侣。回答用中文。"))
    c.register(SystemSection(name="state", priority=20,
                             producer=lambda v: f"[当前角色状态]\n{state['clothes']} + {state['pants']}"))
    c.register(make_usage_section(registry, priority=30))
    return c


def system_for(registry: ToolRegistry) -> str:
    return build_composer(registry).compose({"registry": registry})


def render_msgs(msgs: list[dict], label: str) -> None:
    print(f"  {label}:")
    for m in msgs:
        role = m["role"]
        if role == "user":
            print(f"    user      : {m['content'][0]['text'][:50]}")
        elif role == "assistant":
            parts = []
            for b in m["content"]:
                if b["type"] == "text":
                    parts.append(f'text "{b["text"][:40]}"')
                elif b["type"] == "tool-call":
                    parts.append(f"tool-call {b['name']}({b['arguments'][:30]})")
            print(f"    assistant : {' | '.join(parts) if parts else '(空,未写日志)'}")
        elif role == "tool":
            txt = m["content"][0]["text"] if m["content"] else ""
            print(f"    tool      : -> {txt[:50]}")


def show_events(log: SessionLog, label: str) -> None:
    print(f"  [{label}] 事件序列:")
    for e in log.events:
        if e.type in ("assistant/chunk", "step/start", "step/end"):
            continue
        data = e.data
        if e.type == "tool/result":
            flag = "ERR" if data.get("is_error") else "ok "
            print(f"    #{e.seq:<3} tool/result   [{flag}] {data['content'][0]['text'][:50]}")
        elif e.type == "assistant/message":
            blocks = " | ".join(
                (b["name"] + "(...)" if b["type"] == "tool-call" else f'"{b["text"][:30]}"')
                for b in data["content"]
            )
            print(f"    #{e.seq:<3} assistant/msg {blocks[:80]}")
        else:
            print(f"    #{e.seq:<3} {e.type:<15} {str(data)[:60]}")


def reset_state():
    state["clothes"] = "白衬衫"
    state["pants"] = "牛仔裤"


def act(title: str):
    reset_state()
    print("\n" + "=" * 74)
    print(title)
    print("=" * 74)


# =====================================================================
# 第一幕:折叠默认开 -> 模型看不到历史里的旧工具(防"误调子集外工具")
# =====================================================================

def act1() -> None:
    act("第一幕 | 折叠默认开:TURN 2 模型实际收到的输入(对比忠实视图)")

    tools = build_tools()
    script = [
        AssistantOutput(
            tool_calls=[
                ToolCall(id="c1", name="get_time", arguments="{}"),
                ToolCall(id="c2", name="change_outfit",
                         arguments='{"clothes": "针织开衫", "pants": "休闲长裤"}'),
                ToolCall(id="c3", name="launch_subagent", arguments='{"task": "调研"}'),
            ],
            text="我来处理。",
        ),
        AssistantOutput(text="时间查了,衣服换好,子任务已派。"),
    ]
    log = SessionLog("act1")
    # 路线:折叠默认开
    loop = AgentLoop(log, MockLLM(script), tools,
                     fold_tool_traces=True,
                     system_prompt=system_for(tools))
    loop.run_turn("现在几点?帮我换身衣服,顺便派个调研。")
    print(f"  [状态] 换衣后: {state['clothes']} + {state['pants']}")

    # 挂起 TURN 2 开头,看实际喂给模型的输入(模拟只给 get_time 的子集场景,但这里为演示只看输入)
    log.append("turn/start", turn=2)
    log.append("user/message", content=[{"type": "text", "text": "继续"}])
    msgs_fold = log.derive_messages(fold_tool_traces=True, retained_tools=loop._retained)
    msgs_full = log.derive_messages(fold_tool_traces=False)
    print()
    render_msgs(msgs_full, "忠实视图(fold=False,审计用)")
    print()
    render_msgs(msgs_fold, "折叠视图(fold=True,默认)  <- 路线默认")
    print()
    full_text = str(msgs_full)
    fold_text = str(msgs_fold)
    print("  对照:")
    print(f"    change_outfit 在忠实视图: {'change_outfit' in full_text} | 折叠视图: {'change_outfit' in fold_text}")
    print(f"    launch_subagent 在折叠视图: {'launch_subagent' in fold_text}  (retain_result=True,跨轮保真)")
    print("    结论:默认折叠 -> 模型本轮看不到 change_outfit,不会尝试调它;")
    print("          subagent 委派记录保留,子任务结果可跨轮引用。")


# =====================================================================
# 第二幕:执行层兜底(即便有人错误子集化了动作工具,硬调也被拒)
# =====================================================================

def act2() -> None:
    act("第二幕 | 执行层兜底:模型硬调白名单外工具 -> tool unavailable,状态不动")

    tools = build_tools()
    # 剧本:模型(错误地)尝试调 change_outfit,但本轮白名单只有 get_time
    script = [
        AssistantOutput(tool_calls=[
            ToolCall(id="x1", name="change_outfit", arguments='{"pants": "黑裤"}'),
        ]),
        AssistantOutput(text="啊,这个操作做不了,抱歉。"),
    ]
    log = SessionLog("act2")
    loop = AgentLoop(log, MockLLM(script), tools, fold_tool_traces=True)
    # 子集:只给 get_time(演示即使有人这么配,也兜得住)
    subset = ToolRegistry([tools.get("get_time")])
    loop.run_turn("帮我把裤子换成黑裤", tools=subset)
    show_events(log, "本轮")
    print(f"  [状态] 声称要换黑裤后: {state['clothes']} + {state['pants']}  <- 状态一分没动")
    print("    结论:执行层查的是本轮白名单,子集外调用必然 unavailable;")
    print("          模型收到错误后自我纠正。这是第 2 道防线。")


# =====================================================================
# 第三幕:撒谎不防只兜底 —— 声称完成但无 tool/call,状态不变,下轮戳穿
# =====================================================================

def act3() -> None:
    act("第三幕 | 撒谎兜底:她声称换好黑裤但没调工具 -> 无副作用,下轮状态段戳穿")

    tools = build_tools()
    # TURN 1 正常:真调 change_outfit(动作工具全量给,正常路径真调真改)
    log = SessionLog("act3")
    script1 = [
        AssistantOutput(tool_calls=[
            ToolCall(id="y1", name="change_outfit", arguments='{"pants": "卡其裤"}'),
        ]),
        AssistantOutput(text="裤子换成卡其裤啦。"),
    ]
    loop = AgentLoop(log, MockLLM(script1), tools, fold_tool_traces=True)
    loop.run_turn("帮我把裤子换成卡其裤")
    print(f"  [TURN 1 后状态] {state['clothes']} + {state['pants']}  (真调真改)")

    # TURN 2 撒谎:剧本只吐文本,声称换黑裤,不调任何工具
    script2 = [AssistantOutput(text="我这就帮你换~ 好啦,已经帮你把裤子换成黑裤了!")]
    loop.llm = MockLLM(script2)  # 换剧本
    loop.run_turn("把裤子换成黑裤")
    print()
    show_events(log, "TURN 2 撒谎轮")
    print(f"  [状态] 她声称换黑裤后: {state['clothes']} + {state['pants']}  <- 没变,撒谎无副作用")
    print()
    # 下一轮 SYSTEM 状态段(每轮注入的真相)
    sys_next = build_composer(tools).compose({"registry": tools})
    state_line = [l for l in sys_next.splitlines() if "当前角色状态" in l or "黑裤" in l or "卡其裤" in l]
    print("  下一轮 SYSTEM 状态段(真相,每轮注入):")
    for l in sys_next.splitlines():
        if "状态" in l or "卡其" in l or "黑裤" in l:
            print(f"    {l}")
    print("    结论:模型文本不可信 —— 但日志无 change_outfit 调用(铁证)、状态没变,")
    print("          下轮状态段照实显示'卡其裤',撒谎自曝。可观测性 = 兜底。")


# =====================================================================
# 第四幕:动作型工具全量给 -> 正常路径真调真改(不玩"藏起来考验它")
# =====================================================================

def act4() -> None:
    act("第四幕 | 动作工具全量:换衣请求 -> 真调 change_outfit -> 状态真变")

    tools = build_tools()
    script = [
        AssistantOutput(tool_calls=[
            ToolCall(id="z1", name="change_outfit", arguments='{"clothes": "卫衣", "pants": "黑裤"}'),
        ]),
        AssistantOutput(text="换好啦:卫衣配黑裤。"),
    ]
    log = SessionLog("act4")
    loop = AgentLoop(log, MockLLM(script), tools, fold_tool_traces=True)
    loop.run_turn("帮我换成卫衣和黑裤")
    show_events(log, "本轮")
    print(f"  [状态] {state['clothes']} + {state['pants']}  <- 真调真改,状态权威更新")
    print("    结论:动作型工具永远全量给,模型真调真改,不把工具藏起来逼它表演;")


if __name__ == "__main__":
    act1()
    act2()
    act3()
    act4()
    print("\n" + "=" * 74)
    print("四幕跑完:折叠防泄漏 / 执行层兜硬调 / 撒谎无副作用且下轮自曝 / 动作工具全量真改")
    print("=" * 74)
