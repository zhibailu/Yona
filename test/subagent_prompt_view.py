"""子代理提示词**视图** —— 跑: py test/subagent_prompt_view.py [--real]

这不是测试,是视图:不做断言、不评价好坏,只把值摆出来。

它 import 的是内容层与工厂**本身**(character/personas.py、character/tools.py、
character/persona.py),不另抄一份文案 —— 所以你在这里读到的就是模型读到的。
目的:一眼看懂"这段字最后落在哪一格",好提修改意见。

五幕:
  幕1 工具的两条通道:同一个 Tool 分别渲染成 tools[] 的 schema 与 SYSTEM 的用法段
  幕2 她这一轮的 SYSTEM(真 composer,按优先级逐段;陪聊轮)
  幕3 工人看到的那一侧:任务说明逐字 + 它能调什么 + 它的缺口(量出来的)
  幕4 一次派活的消息流:第1步给什么 -> 调工具 -> 回执长什么样 -> 第2步多带了什么
  幕5 对照:SYSTEM 是一条静态串时,usage 散文到不了模型(死文字)

--real 加跑幕6:真模型 + 新文案,看她到底派不派活(要花钱,几秒~几十秒)。

改文案的地方(改了这里立刻能看出差别):
  character/personas.py  的 SUBAGENT_SYSTEM       —— 工人是谁、什么纪律
  character/tools.py     的 make_launch_subagent_tool —— description / usage / 参数说明
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

from character import personas
from character.persona import build_small_night_composer
from character.state import CharacterState
from character.tools import make_launch_subagent_tool
from core.llm import AssistantOutput, ToolCall
from core.loop import AgentLoop
from core.session_log import SessionLog
from core.tools import ToolRegistry
from server.app.worker_tools import FetchResult, make_read_only_tools
from mock_llm import MockLLM

ROOT = Path(__file__).parent.parent
SEP = "═" * 72

DEMO_TASK = (
    "把下面这段乱七八糟的记录整理成一份结构化纪要:按事项分组,每条一行,"
    "标出时间与地点;该做的事单独列一个清单。不要复述原文,不要加评论,不要问问题。\n\n"
    "---\n昨天记的:早上铲猫砂;10点半约了牙医(城东);老王说周五评审改到下午两点、"
    "材料提前一天发他邮箱;牛奶没了鸡蛋还剩两个;周六9点到11点停水;下周二前把房租转给房东。"
)
DEMO_MATERIAL = """今天记的,乱七八糟的:
早上起来先给猫铲屎,然后想起来 10 点半约了牙医,在城东那家,出门前得把充电宝带上
9 点多的时候老王打电话说周五的评审会改到下午两点了,让我把材料提前一天发他邮箱
中午吃了楼下那家面,齁咸,下次不去了
下午想起来了,牛奶没了,鸡蛋还剩两个,酱油也快见底
3 点的时候物业群说周六上午停水,9 点到 11 点,让提前接水
晚上本来要跑步的,下雨了没去
还有个事,下周二之前要把房租转给房东,上个月忘了被念叨了一顿
书还差最后两章没看完,周五之前得还图书馆,不然要罚钱
"""
DEMO_USER_MSG = "我把今天记的一堆东西丢给你了,帮我理一下,我只要结果。\n\n" + DEMO_MATERIAL
DEMO_CONCLUSION = (
    "**要办的事**\n- 今天 10:30 牙医(城东那家)\n- 周五 14:00 评审会,材料提前一天发老王\n"
    "- 下周二前转房租\n\n**家里**\n- 牛奶没了,鸡蛋剩两个;周六 9:00-11:00 停水,提前接水"
)


def rule(title: str) -> None:
    print()
    print(SEP)
    print(f"  {title}")
    print(SEP)


def block(text: str, indent: str = "    ") -> None:
    for line in text.splitlines() or [""]:
        print(indent + line)


# ---------- 离线替身:视图只关心"文案落到哪",不关心抓到什么 ----------


class _NoNet:
    """不联网的取回替身(Opener)。"""

    def __call__(self, url: str, timeout: float) -> FetchResult:
        return FetchResult(status=200, url=url,
                           body="<html><body><p>样例正文</p></body></html>".encode(),
                           content_type="text/html; charset=utf-8")


class _NoSearch:
    """不联网的搜索替身(SearchProvider)。"""

    def search(self, query: str, count: int) -> list[dict[str, str]]:
        return [{"title": "样例结果", "url": "https://example.com/a", "snippet": "样例摘要"}]


def make_demo_runner():
    """给视图用的 runner:真跑一次**离线**子运行,回结构化事实。

    执行器住哪已经定了:core/subrun.py(2026-09-16 毕业)。这里用真的 execute。
    """
    from core.subrun import SubRunSpec, execute

    def run(task: str, label: str) -> dict:
        sub_llm = MockLLM([AssistantOutput(text=DEMO_CONCLUSION)])
        rec = execute(
            SubRunSpec(task=task, system=personas.SUBAGENT_SYSTEM, label=label, max_steps=2),
            sub_llm,
        )
        return {
            "run_id": rec.run_id,
            "status": rec.status,
            "detail": rec.detail,
            "steps": rec.steps,
            "duration": round(rec.duration, 2),
            "usage": rec.usage,
            "output": rec.output,
        }

    return run


def chat_composer(registry) -> str:
    """她这一轮的 SYSTEM —— 与 server/app/engine.py 的 chat 分支同形。"""
    composer = build_small_night_composer(
        personas.PERSONA,
        CharacterState({"clothes": "卫衣"}),
        registry,
        situation=personas.CHAT_SITUATION,
    )
    return composer.compose({**personas.VALUES, "registry": registry})


# ============================================================
# 幕
# ============================================================


def scene1(tool) -> None:
    rule("幕1 · 工具的两条通道:同一个 Tool,落在两个不同的格子里")
    print("  [通道A] description -> 进 tools[] 数组的 schema(模型靠它知道「是什么/格式」)")
    print()
    block(json.dumps(tool.schema(), ensure_ascii=False, indent=2))
    print()
    print("  [通道B] usage -> 进 SYSTEM 的 [可用工具用法] 段(模型靠它知道「什么时候用」)")
    print()
    block(tool.usage)
    print()
    print(f"  retain_result = {tool.retain_result}"
          "   <- 派活的结果跨轮保真,不会在后续轮的折叠视图里被折掉")


def scene2(registry) -> None:
    rule("幕2 · 她这一轮的 SYSTEM(陪聊轮;真 composer,按优先级分段)")
    composer = build_small_night_composer(
        personas.PERSONA, CharacterState({"clothes": "卫衣"}), registry,
        situation=personas.CHAT_SITUATION,
    )
    values = {**personas.VALUES, "registry": registry}
    for sec in composer.sections():
        text = (sec.render(values) or "").strip()
        print(f"  ┌─ [{sec.name}]  priority={sec.priority}")
        if not text:
            print("  │  (本段不出现)")
        else:
            block(text, indent="  │  ")
        print("  └─")


def scene3(worker_registry) -> None:
    rule("幕3 · 工人看到的那一侧(它和她是两套 SYSTEM,不共用)")
    print("  [它的 SYSTEM] —— 逐字。没有 persona、没有情境段、没有世界段")
    print()
    block(personas.SUBAGENT_SYSTEM)
    print()
    print(f"  [它的 tools[]] {len(worker_registry.names())} 件:")
    for schema in worker_registry.schemas():
        print(f"    - {schema['function']['name']}")

    # 缺口是量出来的,不是读出来的:跑一轮工人,看 usage 散文到没到它眼前
    llm = MockLLM([AssistantOutput(text="(工人的结论)")])
    AgentLoop(SessionLog("worker-view"), llm, worker_registry,
              system_prompt=personas.SUBAGENT_SYSTEM).run_turn("随便一件活", source="user")
    seen = str(llm.seen_messages[-1])
    print()
    print("  [缺口] 工人拿得到 schema,拿不拿得到用法散文?")
    for name, usage in worker_registry.usage_entries():
        arrived = "到了" if usage in seen else "没到(死文字)"
        print(f"    {name:<18} usage {arrived}")
    print("    -> 工人轮没有 composer,所以 SYSTEM 里没有 [可用工具用法] 段,"
          "也没有 [当前时间]。")
    print("       ⏳ 要不要给它,是 engine 装配处的决定(见 docs/protocols/TOOL_VISIBILITY.md)")


def scene4(registry, runner) -> None:
    rule("幕4 · 一次派活的消息流(离线 mock;每一步实际喂给模型的 messages)")
    tool = make_launch_subagent_tool(runner)
    solo = ToolRegistry([tool])

    parent_llm = MockLLM([
        AssistantOutput(text="你等我理一下", tool_calls=[
            ToolCall(id="call_1", name="launch_subagent",
                     arguments=json.dumps({"task": DEMO_TASK, "label": "整理今日记录"},
                                          ensure_ascii=False))]),
        AssistantOutput(text="理好了:要办的五件事我先说三件……"),
    ])
    log = SessionLog("session:view")
    loop = AgentLoop(log, parent_llm, solo,
                     system_prompt=lambda reg, source, lg=None: chat_composer(reg),
                     max_steps=4)
    user_msg = DEMO_USER_MSG
    loop.run_turn(user_msg, source="user")

    print(f"  用户说: {user_msg.splitlines()[0]}(后面跟了 {len(DEMO_MATERIAL)} 字的原料)")
    print()
    print(f"  她这一轮一共调了模型 {len(parent_llm.seen_messages)} 次 —— 逐次打印它收到的 messages:")
    for i, messages in enumerate(parent_llm.seen_messages, 1):
        print()
        print(f"  ── 第 {i} 次调用的 messages ──────────────────────────────")
        for msg in messages:
            role = msg.get("role")
            content = msg.get("content")
            if isinstance(content, str):
                head = content.splitlines()[0] if content else ""
                print(f"    role={role:<9} ({len(content)} 字) {head[:52]}")
                continue
            if role == "tool":
                text = "".join(b.get("text", "") for b in (content or [])
                               if b.get("type") == "text")
                print(f"    role={role:<9} tool_call_id={msg.get('tool_call_id')}")
                print(f"                       回执逐字: {text}")
                continue
            for blk in content or []:
                if blk.get("type") == "text":
                    print(f"    role={role:<9} text      {blk.get('text', '')[:52]}")
                elif blk.get("type") == "tool-call":
                    print(f"    role={role:<9} tool-call {blk.get('name')} "
                          f"id={blk.get('id')}")
                    print(f"                       参数逐字: {blk.get('arguments')[:120]}")
    print()
    print("  ── 主日志事件(非 chunk 层)────────────────────────────────")
    for event in log.events:
        if event.type == "assistant/chunk":
            continue
        note = ""
        if event.type == "tool/call":
            note = f"name={event.data.get('name')}"
        elif event.type == "tool/result":
            note = str((event.data.get("content") or [{}])[0].get("text", ""))[:60]
        print(f"    #{event.seq:<3} {event.type:<18} {note}")
    print()
    print("  ↑ 主日志里只有 call 与 result 两行;子运行自己的轨迹一条都没进来。")


def scene5(registry) -> None:
    rule("幕5 · 对照:SYSTEM 是一条静态串时,usage 是死文字")
    usage = registry.get("launch_subagent").usage
    static = "你是小夜子,遇到又长又杂的活优先派出去。"

    llm = MockLLM([AssistantOutput(text="我自己答了")])
    AgentLoop(SessionLog("static-view"), llm, registry,
              system_prompt=static).run_turn("帮我理一下", source="user")
    print(f"  静态串 SYSTEM  =>  usage 到模型眼前了吗: "
          f"{usage in str(llm.seen_messages[-1])}")

    llm2 = MockLLM([AssistantOutput(text="我自己答了")])
    AgentLoop(SessionLog("composer-view"), llm2, registry,
              system_prompt=lambda reg, source, lg=None: chat_composer(reg)
              ).run_turn("帮我理一下", source="user")
    print(f"  composer SYSTEM =>  usage 到模型眼前了吗: "
          f"{usage in str(llm2.seen_messages[-1])}")
    print()
    print("  ↑ 探针 test/subrun_probe.py 的 real_act2 用的是静态串 ——"
          " 所以上一次真跑,她只看到 description,没看到 usage。")
    print("    产品路径走 composer,两段都到。这不是文案问题,是装配问题。")


def scene6() -> None:
    rule("幕6 · 真模型 + **真上线路径**:engine 装配 → 她派活 → 真工人上网")
    from server.app import engine
    from server.app.llm_setup import load_runtime

    cfg = load_runtime(ROOT / "data")
    if not cfg:
        print("  没找到 data/llm.local.json,跳过(先在 UI 里把连接配好)")
        return
    model = cfg["model"]
    if "--pro" in sys.argv[1:]:
        strong = next((m for m in (cfg.get("models") or []) if "pro" in m), None)
        if not strong:
            print(f"  端点可用模型里没有 pro 档: {cfg.get('models')}")
            return
        model = strong
        cfg = dict(cfg, model=strong)

    # 走 **engine 自己的装配**,不是这里另搭一套:她这一轮用产品那份 _loop
    # (真 sys_by_source / 真工具注册表 / 真 llm),工人用 engine 的 _run_worker
    # (真 core/subrun.py + 真上网工具)。下面看到的每一步都是上线的那条路。
    engine._build_engine(dict(cfg))
    print(f"  端点 {cfg['base_url']}   模型 {model}")
    print(f"  她的工具注册表: {engine._tools.names()}")
    print(f"  工人的手: {engine._worker_tools.names()}"
          f"   能力句: {'、'.join(engine._worker_capabilities()) or '(空)'}")
    print("  ⚠ 工人的手是真联网的(web_search / http_get),这一幕会真发请求")

    log = SessionLog("session:real-view")
    # 用**她自己做不到**的活:t2(整理原料)她一步就答完了,派了纯亏 ——
    # 实验数据也是这么分的(见 test/subagent_prompt_lab.py)。这一幕要证的是
    # 全链路通不通,所以必须挑那条真会触发的路。
    task = "帮我查一下这周末杭州有哪些科幻片在上映,我想挑一部去看。"
    if "--notes" in sys.argv[1:]:
        task = DEMO_USER_MSG
    result = engine._loop.run_turn(task, source="user", log=log)
    calls = log.of_type("tool/call")
    print(f"\n  用户: {task.splitlines()[0]}")

    print(f"\n  [她看见了什么] SYSTEM 的 [可用工具用法] 段:")
    for name, text in engine.system_component_sections("chat", log):
        if name == "tool_usages":
            for line in text.splitlines():
                print(f"      {line[:150]}")
    print(f"  第 1 次调用带过去的 tools[]: {engine._tools.names()}")

    print(f"\n  steps={result.steps} reason={result.reason}  tool/call 次数 = {len(calls)}")
    for call in calls:
        print(f"    她派活时写的 task 前 160 字:\n      {call.data['arguments'][:160]}")
    if not calls:
        print("    -> 工具传过去了、她也看见了,但她选择自己干。")
    # 工人真跑出来的回执(主日志里那一行)
    for row in log.of_type("tool/result"):
        inner = (row.data.get("content") or [{}])[0]
        print(f"\n  [主日志里那一行 tool/result]\n    {str(inner.get('text', ''))[:400]}")
    finals = log.of_type("assistant/message")
    if finals:
        text = "".join(b.get("text", "") for b in (finals[-1].data.get("content") or [])
                       if b.get("type") == "text")
        print(f"\n  她最后的话:\n{text[:400]}")


# ============================================================


def main() -> None:
    # 用 **engine 真装配的那份能力表** —— 视图要摆的是上线的那段字,不是另抄一份
    from server.app.engine import _worker_capabilities

    caps = _worker_capabilities()
    registry = ToolRegistry([
        make_launch_subagent_tool(make_demo_runner(), capabilities=caps)])
    worker_registry = ToolRegistry(make_read_only_tools(
        ROOT, opener=_NoNet(), search_provider=_NoSearch()))

    print(SEP)
    print("  Yona · 子代理提示词视图(离线;只摆值,不评价)")
    print(SEP)
    print("  文案来源(直接 import,没另抄一份):")
    print("    character/personas.py :: SUBAGENT_SYSTEM           —— 工人的任务说明")
    print("    character/tools.py    :: make_launch_subagent_tool —— 工具的 description/usage")
    print(f"    能力句由 engine 的工人工具集生成 —— 当前: {'、'.join(caps) or '(空)'}")

    scene1(registry.get("launch_subagent"))
    scene2(registry)
    scene3(worker_registry)
    scene4(registry, make_demo_runner())
    scene5(registry)
    if "--real" in sys.argv[1:]:
        scene6()
    print()
    print(SEP)
    print("  真行为(她派不派活、工人干得好不好)看: py test/subrun_probe.py --real")
    print(SEP)


if __name__ == "__main__":
    main()
