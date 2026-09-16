"""子运行(SubRun)探针 —— 跑: py test/subrun_probe.py [--real] [--budget N] [--acts R1,R3]

两种模式:
  (默认)     mock —— 离线、秒级、可重复。只证明"管线通",不证明"行为对"。
  --real     真模型 —— 用 data/llm.local.json 的连接,真调 API,打印真实
             steps / 耗时 / token / 结论。**证明行为,要花一点钱。**
             --budget N   实验输出预算(默认 8192)。**不是产品值** ——
                          产品的 4096 是拍给聊天轮的,子运行不借。
             --acts R1,R3 只跑指定幕(省钱的调试用)

真模型模式四幕:
  R1 单跑:长原料 -> 一句结论(隔离到底省下了什么)
  R2 端到端:真模型**自己决定**要不要派活 —— 调没调,如实打印
  R3 两档对比:同一个任务 flash vs pro(弱模型验收的第一个实证)
  R4 失败的样子:输出被截断 -> 老实记 failed,不假装成功

被测对象是 core/subrun.py + test/lab/scheduler.py(执行器 2026-09-16 已毕业进 core;
队列仍未收口,暂住实验台)。
设计要点:
  - 子运行**不带人格**:任务级 SYSTEM 由内容层给,lab 里没有一个字的文案;
  - 子运行**不进主日志**:主日志只留 tool/call + tool/result(带 run_id),轨迹落 run store;
  - 阻塞式**不需要新的注入机制**:execute -> 追加 tool/result -> 下一步 derive_messages 带上。
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

from core.llm import AssistantOutput, ToolCall
from core.loop import AgentLoop
from core.session_log import SessionLog
from core.tools import Tool, ToolRegistry
from lab.scheduler import RunQueue
from core.subrun import SubRunSpec, SubRunStore, execute
from mock_llm import MockLLM

ROOT = Path(__file__).parent.parent
RUNS_DIR = Path(tempfile.gettempdir()) / "yona-subrun-probe"
STORE = SubRunStore(RUNS_DIR)
SEP = "─" * 66

# 实验预算(可 --budget 覆盖)。**不是产品值** —— 产品 4096 是拍给聊天轮的。
# 实测:同一任务在 4096 下 reasoning 100% 吃满、正文 0 token、结算成 failed。
_ACTIVE_BUDGET = 8192

# ---------------- 内容层文案(探针自带;core 里一个字都没有) ----------------

SUB_SYSTEM = (
    "你是一次性的执行单元,不属于任何对话,也不是任何角色。"
    "只完成交给你的任务,直接给出结果;不寒暄、不反问、不解释你在做什么。"
)
PARENT_SYSTEM = (
    "你是小夜子,一个和用户很熟的陪伴型助手,说话自然、简短,不说客套话。\n"
    "有一个一次性的执行单元可以调(launch_subagent):它只回一句结论,"
    "中间过程不会进入你们的对话。遇到又长又杂、需要慢慢理的活,优先派给它。"
)

# 一段真实的"乱七八糟的原料":真模型模式用它当长输入
MESSY_NOTES = """今天记的,乱七八糟的:
早上起来先给猫铲屎,然后想起来 10 点半约了牙医,在城东那家,出门前得把充电宝带上
9 点多的时候老王打电话说周五的评审会改到下午两点了,让我把材料提前一天发他邮箱
中午吃了楼下那家面,齁咸,下次不去了
下午想起来了,牛奶没了,鸡蛋还剩两个,酱油也快见底
3 点的时候物业群说周六上午停水,9 点到 11 点,让提前接水
晚上本来要跑步的,下雨了没去
还有个事,下周二之前要把房租转给房东,上个月忘了被念叨了一顿
小美说她生日是下个月 8 号,想约大家一起吃个饭,让我先看看地方
对了,牙医那家的电话是 0571-8888xxxx,下次复诊大概是两周后
书还差最后两章没看完,周五之前得还图书馆,不然要罚钱
"""


def rule(title: str) -> None:
    print()
    print(SEP)
    print(f"  {title}")
    print(SEP)


def mask(key: str) -> str:
    return f"{key[:6]}...{len(key)}chars" if key else "(空)"


def render_messages(messages: list[dict]) -> None:
    """把喂给模型的 messages 压成人能看的样子(形状对齐 derive_messages)。"""
    for msg in messages:
        role = msg.get("role")
        content = msg.get("content")
        if isinstance(content, str):
            print(f"    role={role:<10} {content[:74]}")
            continue
        if role == "tool":
            text = "".join(
                b.get("text", "") for b in (content or []) if b.get("type") == "text"
            )
            print(
                f"    role={role:<10} tool_call_id={msg.get('tool_call_id')}  {text[:58]}"
            )
            continue
        for block in content or []:
            kind = block.get("type")
            if kind == "text":
                print(f"    role={role:<10} text       {block.get('text', '')[:66]}")
            elif kind == "tool-call":
                args = block.get("arguments", "")
                print(
                    f"    role={role:<10} tool-call  {block.get('name')} "
                    f"id={block.get('id')} {args[:40]}"
                )
            else:
                print(f"    role={role:<10} {kind}")


def show_record(rec, indent: str = "  ") -> None:
    print(f"{indent}run_id   {rec.run_id}")
    print(f"{indent}status   {rec.status}  (detail={rec.detail})")
    print(f"{indent}steps    {rec.steps}")
    print(f"{indent}duration {rec.duration:.2f}s")
    print(f"{indent}usage    {rec.usage}")
    print(f"{indent}output   {rec.output}")


def make_launch_tool(sub_llm, container: list, *, max_steps: int = 4) -> Tool:
    """工具壳:派活 -> 阻塞等结果 -> 回一段结构化结果(带血缘 run_id)。

    注意这里**不加任何拟人化前缀** —— 内核只给结构化事实(来源/终态/耗时/血缘),
    怎么称呼这件事由内容层决定(本探针里就是 PARENT_SYSTEM 那句话)。
    """

    def launch_subagent(args: dict) -> str:
        spec = SubRunSpec(
            task=str(args.get("task", "")),
            system=SUB_SYSTEM,
            label=str(args.get("label", "")),
            parent="session:demo",
            max_steps=max_steps,
        )
        rec = execute(spec, sub_llm, store=STORE)
        container.append(rec)
        return json.dumps(
            {
                "run_id": rec.run_id,
                "status": rec.status,
                "detail": rec.detail,
                "steps": rec.steps,
                "duration": round(rec.duration, 2),
                "usage": rec.usage,
                "output": rec.output,
            },
            ensure_ascii=False,
        )

    return Tool(
        name="launch_subagent",
        description=(
            "把一件不便在当下对话里做的活(长文本整理、多步排查、大量检索)派给"
            "一个一次性执行单元,拿回它的结论。中间过程不会进入你们的对话。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "要它做什么(写全,它看不到你们的对话)"},
                "label": {"type": "string", "description": "一行标签"},
            },
            "required": ["task"],
        },
        func=launch_subagent,
        usage=(
            "用户丢来一大段原料、而你不希望那些中间过程留在对话里时用它;"
            "把要做的事写完整 —— 它看不到你和用户的对话。"
        ),
        retain_result=True,
    )


# ============================================================
# 真模型模式
# ============================================================


def real_llm(cfg: dict, model: str | None = None, **kw):
    """真模型客户端。

    max_tokens **显式给,不借产品值**:core 的 OpenAICompatibleLLM 默认只有 1024,
    而推理模型的 reasoning_tokens 计入 output —— 1024 会被"想"光,正文 0 token
    (实测踩过)。产品值 server/params.py 的 4096 是拍给**聊天轮**的,子运行
    是另一种成本结构的工作,直接沿用就是"把沿用值当已定"(OPEN.md 明令禁止)。
    所以这里用实验预算 _ACTIVE_BUDGET,可 --budget 覆盖,并在页首打印。
    """
    from core.openai_compat import OpenAICompatibleLLM

    kw.setdefault("max_tokens", _ACTIVE_BUDGET)
    return OpenAICompatibleLLM(
        api_key=cfg["api_key"],
        base_url=cfg["base_url"],
        model=model or cfg["model"],
        timeout=180.0,
        **kw,
    )


def load_cfg() -> dict:
    from server.app.llm_setup import load_runtime

    cfg = load_runtime(ROOT / "data")
    if not cfg:
        raise SystemExit("没找到可用的 data/llm.local.json(先在 UI 里把连接配好)")
    return cfg


TIDY_TASK = (
    "把下面这段乱七八糟的记录整理成一份结构化纪要:按事项分组,每条一行,"
    "标出时间与地点;该做的事单独列一个清单。不要复述原文,不要加评论,不要问问题。\n\n"
    "---\n" + MESSY_NOTES
)


def real_act1(cfg: dict) -> tuple:
    rule("R1 · 单跑:真模型 + 长原料 -> 一句结论(隔离到底省下了什么)")
    model = cfg["model"]
    llm = real_llm(cfg)
    print(f"  模型 {model} / 原料 {len(MESSY_NOTES)} 字")
    spec = SubRunSpec(task=TIDY_TASK, system=SUB_SYSTEM, label="整理今日记录",
                      parent="session:demo", max_steps=3)
    t0 = time.time()
    rec = execute(spec, llm, store=STORE)
    print(f"  实跑耗时(含网络) {time.time() - t0:.2f}s\n")
    show_record(rec)
    print(f"\n  子运行自己的日志({len(rec.events)} 条事件):")
    for event in rec.events:
        if event["type"] in ("assistant/message", "tool/call", "tool/result", "turn/end"):
            print(f"    #{event['seq']:<3} {event['type']:<18} "
                  f"{str(event['data'].get('content') or event['data'].get('reason') or '')[:60]}")
    return rec, model


def real_act3(cfg: dict, base: tuple) -> None:
    rule("R3 · 两档对比:同一个任务,弱档 vs 强档(弱模型验收的第一个实证)")
    flash_rec, flash_model = base
    strong = next(
        (m for m in (cfg.get("models") or []) if m != flash_model and "pro" in m), None
    )
    if not strong:
        print(f"  端点可用模型里没有另一个 pro 档: {cfg.get('models')}")
        return
    print(f"  弱档 {flash_model}  vs  强档 {strong}\n")
    llm = real_llm(cfg, model=strong)
    spec = SubRunSpec(task=TIDY_TASK, system=SUB_SYSTEM, label="整理今日记录",
                      parent="session:demo", max_steps=3)
    t0 = time.time()
    pro_rec = execute(spec, llm, store=STORE)
    wall = time.time() - t0

    def tok(rec) -> tuple[str, str]:
        """input_tokens 是**未缓存**输入(适配器:计费输入 = 未缓存 + 缓存读),
        所以真实输入 = input_tokens + cache_read_tokens。别把它当全部。"""
        u = rec.usage or {}
        inp = (u.get("input_tokens") or 0) + (u.get("cache_read_tokens") or 0)
        out = u.get("output_tokens")
        reasoning = u.get("reasoning_tokens") or 0
        pct = f"{reasoning / out:.0%}" if out else "-"
        return (
            f"{inp}(缓存{u.get('cache_read_tokens') or 0})",
            f"{out}(推理{reasoning}={pct})",
        )

    print(f"  {'档位':<24}{'status':<11}{'steps':<7}{'耗时':<9}{'输入':<16}输出")
    for rec, model, note in (
        (flash_rec, flash_model, ""),
        (pro_rec, strong, "  <- 撞 4096 上限" if not pro_rec.ok else ""),
    ):
        i, o = tok(rec)
        print(f"  {model:<24}{rec.status:<11}{rec.steps:<7}"
              f"{rec.duration:<9.2f}{i:<16}{o}{note}")
    print(f"\n  --- 弱档输出({flash_model}) ---\n{flash_rec.output}\n")
    print(f"  --- 强档输出({strong}) ---\n{pro_rec.output or '(空:撞了输出上限,正文 0 token)'}\n")
    print("  验收:上面两段你自己判断哪段能直接用 —— 这就是「弱模型要不要配验收」的第一步证据。")
    print("  另:强档若撞上限,说明 4096 对「多推理」的档位不够 —— 这本身就是结论。")


def real_act2(cfg: dict) -> None:
    rule("R2 · 端到端:真模型自己决定要不要派活(调没调,如实打印)")
    sub_llm = real_llm(cfg)
    ran: list = []
    tools = ToolRegistry([make_launch_tool(sub_llm, ran, max_steps=3)])
    parent_log = SessionLog("session:real-demo")
    parent_llm = real_llm(cfg, temperature=0.8)
    loop = AgentLoop(parent_log, parent_llm, tools, system_prompt=PARENT_SYSTEM, max_steps=4)
    user_msg = "我把今天记的一堆东西丢给你了,你帮我理一下,我只要结果。\n\n" + MESSY_NOTES
    t0 = time.time()
    result = loop.run_turn(user_msg, source="user")
    print(f"  她的这一轮: steps={result.steps} reason={result.reason} "
          f"墙钟 {time.time() - t0:.2f}s")
    calls = parent_log.of_type("tool/call")
    print(f"\n  [她调工具了吗] tool/call 次数 = {len(calls)}")
    for c in calls:
        print(f"    name={c.data['name']}")
        print(f"    args={c.data['arguments'][:120]}")
    if not calls:
        print("    -> 她没派活,自己答了。(这是真实结果,不是失败 —— 但是个值得看的信号)")
    print(f"\n  主日志({len(parent_log.events)} 条事件):")
    for event in parent_log.events:
        note = ""
        if event.type == "tool/result":
            inner = (event.data.get("content") or [{}])[0]
            note = str(inner.get("text", ""))[:64]
        elif event.type == "tool/call":
            note = event.data.get("name", "")
        elif event.type == "assistant/message":
            blocks = event.data.get("content") or []
            note = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")[:64]
        print(f"    #{event.seq:<3} {event.type:<18} {note}")

    if ran:
        rec = ran[0]
        print(f"\n  子运行真实结算:")
        show_record(rec, indent="    ")
    print(f"\n  [她最后的话]")
    finals = parent_log.of_type("assistant/message")
    if finals:
        blocks = finals[-1].data.get("content") or []
        print("    " + "".join(b.get("text", "") for b in blocks if b.get("type") == "text")[:400])


def real_act4(cfg: dict) -> None:
    rule("R4 · 失败的样子:输出被截断 -> 老实记 failed,不假装成功")
    llm = real_llm(cfg, max_tokens=32)  # 故意把预算掐死(覆盖产品的 4096)
    spec = SubRunSpec(
        task="写一份 300 字以上的详细说明,介绍如何整理一天的零散记录。",
        system=SUB_SYSTEM,
        label="会被截断的活",
        max_steps=2,
    )
    rec = execute(spec, llm, store=STORE)
    show_record(rec)
    print("\n  ↑ 撞了输出上限就是没干完 —— 记 failed + max-tokens,不记 completed。")
    print("    注意 usage 仍然有值:失败轮也要进成本账(chunk 层的用量被捞回来了)。")


def run_real(acts: set[str] | None = None) -> None:
    def wanted(key: str) -> bool:
        return acts is None or key in acts

    cfg = load_cfg()
    print("=" * 66)
    print("  Yona · 子运行探针 —— 真模型模式")
    print("=" * 66)
    print(f"  端点 {cfg['base_url']}   默认模型 {cfg['model']}")
    print(f"  key  {mask(cfg.get('api_key', ''))}")
    print(f"  可用模型 {cfg.get('models')}")
    print(f"  实验输出预算 {_ACTIVE_BUDGET} tok(--budget 可改)")
    print("              不是产品值:server/params.py 的 4096 是拍给聊天轮的;")
    print("              推理模型的 reasoning_tokens 计入 output,借聊天预算 =")
    print("              把沿用值当已定(OPEN.md 禁止)。这里要测的是'多少才够'。")
    base = real_act1(cfg) if wanted("R1") else None
    if wanted("R2"):
        real_act2(cfg)
    if wanted("R3") and base is not None:
        real_act3(cfg, base)
    if wanted("R4"):
        real_act4(cfg)
    print()
    print(f"  run store(每次子运行的完整轨迹): {RUNS_DIR}")
    print("=" * 66)


# ============================================================
# mock 模式(离线回归;证明管线通,不证明行为对)
# ============================================================


class SlowLLM:
    def __init__(self, delay: float, text: str) -> None:
        self.delay = delay
        self.text = text

    def stream(self, messages, tools=None, **kwargs):
        time.sleep(self.delay)
        yield {"kind": "text", "text": self.text}
        yield {"kind": "finish", "reason": "stop"}

    def invoke(self, messages, tools=None, **kwargs):  # pragma: no cover
        time.sleep(self.delay)
        return AssistantOutput(text=self.text)


def mock_act1() -> None:
    rule("幕 1 · 端到端:一次 launch_subagent 是怎么派出去、怎么回来的")

    sub_llm = MockLLM([AssistantOutput(text="《沙丘 2》《降临》最近口碑最好。")])
    parent_llm = MockLLM([
        AssistantOutput(
            text="你等我搜搜……",
            tool_calls=[ToolCall(id="call_1", name="launch_subagent",
                                 arguments=json.dumps({"task": "查最近口碑好的科幻片",
                                                       "label": "找科幻片"},
                                                      ensure_ascii=False))],
        ),
        AssistantOutput(text="查到啦:《沙丘 2》《降临》口碑最好。"),
    ])
    ran: list = []
    tools = ToolRegistry([make_launch_tool(sub_llm, ran)])
    parent_log = SessionLog("session:demo")
    loop = AgentLoop(parent_log, parent_llm, tools, system_prompt=PARENT_SYSTEM, max_steps=4)
    result = loop.run_turn("帮我查一下最近有什么好看的科幻片", source="user")

    print(f"\n[她的这一轮] steps={result.steps} reason={result.reason}")
    print("\n[父日志 —— 主日志里只有这些,子运行一个事件都没进来]")
    for event in parent_log.events:
        note = ""
        if event.type == "tool/call":
            note = f"name={event.data.get('name')} args={event.data.get('arguments', '')[:44]}"
        elif event.type == "tool/result":
            inner = (event.data.get("content") or [{}])[0]
            note = str(inner.get("text", ""))[:52]
        elif event.type == "assistant/message":
            blocks = event.data.get("content") or []
            note = " ".join(f"[{b.get('type')}]" for b in blocks)
        print(f"  #{event.seq:<3} {event.type:<17} {note}")

    print("\n[注入证据] 她的第 2 步真正喂给模型的 messages:")
    render_messages(parent_llm.seen_messages[-1])
    print("\n  ↑ 子运行的结论就是这样进来的:一条普通 tool 消息。")
    if ran:
        print()
        show_record(ran[0])


def mock_act2() -> None:
    rule("幕 2 · 队列编排:2 个 worker / 5 件活 / 1 件排队时取消")
    timeline: list[str] = []

    def on_change(job) -> None:
        timeline.append(f"  +{time.time() - t0:6.2f}s  {job.run_id[-6:]:>7}  "
                        f"{job.status:<10} {job.label}")

    def runner(spec: SubRunSpec, run_id: str):
        return execute(spec, SlowLLM(0.30, f"（结论）{spec.task} -> 已处理"),
                       run_id=run_id, store=STORE)

    queue = RunQueue(runner, workers=2, on_change=on_change)
    t0 = time.time()
    ids = []
    for i, name in enumerate(["找科幻片", "整理笔记", "翻译摘要", "查天气", "写提纲"], 1):
        rid = queue.submit(SubRunSpec(task=name, system=SUB_SYSTEM, label=name))
        ids.append(rid)
        print(f"  submit #{i} {rid}  {name}")
    print(f"\n排队中取消 {ids[3]} -> {queue.cancel(ids[3])}")
    queue.wait_all(timeout=10)
    print("\n状态流转:")
    for line in timeline:
        print(line)
    print(f"\n队列总览: {queue.status()}")
    queue.stop()


def mock_act3() -> None:
    rule("幕 3 · 隔离与血缘:主日志干净,轨迹可追")
    ids = sorted(STORE.list_ids(), key=lambda r: STORE.path(r).stat().st_mtime)
    print(f"run store: {RUNS_DIR}   共 {len(ids)} 次子运行")
    if not ids:
        return
    rec = STORE.load(ids[-1])
    print(f"\n抽最新一次复盘 run_id={rec.run_id}")
    show_record(rec)
    print(f"\n  轨迹({len(rec.events)} 条事件,全在 store 里,主日志一条都没有):")
    for event in rec.events:
        print(f"    #{event['seq']:<3} {event['type']}")


def run_mock() -> None:
    print("=" * 66)
    print("  Yona · 子运行(SubRun)探针 —— mock 模式")
    print("=" * 66)
    print("  [注意] 全部离线脚本模型,不调真模型 —— 只证明管线通,不证明行为对。")
    print("         要看真行为: py test/subrun_probe.py --real")
    mock_act1()
    mock_act2()
    mock_act3()
    print()
    print("=" * 66)


def main() -> None:
    global _ACTIVE_BUDGET
    argv = sys.argv[1:]
    if RUNS_DIR.exists():
        for old in RUNS_DIR.glob("*.jsonl"):
            old.unlink()
    if "--real" not in argv:
        if "--budget" in argv:
            print("[warn] --budget 只对 --real 有意义")
        run_mock()
        return
    if "--budget" in argv:
        _ACTIVE_BUDGET = int(argv[argv.index("--budget") + 1])
    acts = None
    if "--acts" in argv:
        acts = {a.strip().upper() for a in argv[argv.index("--acts") + 1].split(",")}
    run_real(acts)


if __name__ == "__main__":
    main()
