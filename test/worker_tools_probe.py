"""只读工具集效果演示 —— 跑: py test/lab_tools_probe.py [--subagent]

回答两个问题:
  1. 这四个工具**分别**能干什么   -> 默认模式:真网络 + 真本地,直接调,不花 LLM 钱
  2. **串起来**能干什么           -> --subagent:真模型拿这套工具跑多步任务,打印轨迹

四个工具(全部只读):
  web_search      上网搜 -> 标题/链接/摘要       "外面是什么情况"
  http_get        打开网址 -> 纯文本             "那一页到底写了什么"
  list_files      看目录里有什么                 "我这儿有什么可看的"
  read_text_file  读文件内容                     "那个文件里写了什么"

单个都普通,价值在串起来:搜 -> 打开 -> 读 -> 出结论。这就是"给子代理能力打分"的题面。

被测对象 = test/lab/tools.py(**实验台,不是产品,不进 core**)。
默认模式不打网络以外的任何东西;--subagent 才调真模型、才花钱。
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))

from server.app.worker_tools import make_read_only_tools

SEP = "─" * 66
BUDGET = 8192  # 实验输出预算(见 docs/protocols/SUBAGENT.md §4.3:4096 实测不够)


def rule(title: str) -> None:
    print()
    print(SEP)
    print(f"  {title}")
    print(SEP)


def _call(tools: list, name: str, args: dict) -> dict:
    tool = next(t for t in tools if t.name == name)
    started = time.time()
    try:
        payload = json.loads(tool.func(args))
    except Exception as exc:  # noqa: BLE001
        payload = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    payload["_elapsed"] = round(time.time() - started, 2)
    return payload


def _clip(text: str, n: int) -> str:
    text = (text or "").replace("\n", " | ")
    return text[:n] + ("…" if len(text) > n else "")


# ============================================================
# 模式 1:四个工具分别是什么
# ============================================================


def show_each(tools: list) -> None:
    rule("① web_search —— 上网搜,拿回 标题 / 链接 / 摘要")
    print('  调法: web_search({"query": "python urllib", "count": 3})')
    out = _call(tools, "web_search", {"query": "python urllib", "count": 3})
    print(f"  结果: ok={out.get('ok')} count={out.get('count')} 耗时={out['_elapsed']}s")
    for i, hit in enumerate(out.get("results") or [], 1):
        print(f"    {i}. {_clip(hit['title'], 60)}")
        print(f"       {hit['url']}")
        print(f"       摘要: {_clip(hit['snippet'], 70)}")
    if not out.get("results"):
        print(f"    (没拿到结果: {out.get('error')})")
    print("\n  ⚠️ 它只给摘要和链接,**不给正文** —— 要看正文得用 http_get。")

    rule("② http_get —— 把某个网址打开,读成纯文本")
    first = (out.get("results") or [{}])[0].get("url")
    print(f'  调法: http_get({{"url": "..."}})')
    if first:
        got = _call(tools, "http_get", {"url": first, "max_chars": 300})
        print(f"  打开: {first}")
        print(
            f"  结果: ok={got.get('ok')} status={got.get('status')} "
            f"chars={got.get('chars')} truncated={got.get('truncated')} 耗时={got['_elapsed']}s"
        )
        print(f"  读到的开头: {_clip(got.get('text') or got.get('error') or '', 160)}")
        print("\n  用途:这正是「搜到一条 -> 打开看内容」那一步。")

    rule("③ list_files —— 看本机某个目录里有什么")
    print('  调法: list_files({"pattern": "*.md", "max": 6})')
    listed = _call(tools, "list_files", {"pattern": "*.md", "max": 6})
    print(f"  结果: ok={listed.get('ok')} count={listed.get('count')} 耗时={listed['_elapsed']}s")
    for entry in listed.get("entries") or []:
        print(f"    {entry['path']}  ({entry['size']} 字节)")
    print("\n  用途:别猜路径 —— 先看看有什么,再决定读哪个。")

    rule("④ read_text_file —— 读某个文件的内容")
    print('  调法: read_text_file({"path": "README.md", "max_lines": 3})')
    read = _call(tools, "read_text_file", {"path": "README.md", "max_lines": 3})
    print(
        f"  结果: ok={read.get('ok')} total_lines={read.get('total_lines')} "
        f"truncated={read.get('truncated')} 耗时={read['_elapsed']}s"
    )
    for line in (read.get("text") or "").splitlines():
        print(f"    | {_clip(line, 70)}")
    print("\n  用途:文件很长时用 start_line/max_lines 分段读,不会一次塞爆上下文。")


# ============================================================
# 模式 2:串起来 —— 真模型 + 这套工具
# ============================================================


def show_subagent() -> None:
    from core.openai_compat import OpenAICompatibleLLM
    from core.subrun import SubRunSpec, execute
    from server.app.llm_setup import load_runtime

    cfg = load_runtime(ROOT / "data")
    if not cfg:
        print("\n[跳过] 没有可用的 data/llm.local.json(先在 UI 里配好连接)")
        return

    llm = OpenAICompatibleLLM(
        api_key=cfg["api_key"],
        base_url=cfg["base_url"],
        model=cfg["model"],
        timeout=180.0,
        max_tokens=BUDGET,
    )
    tools = make_read_only_tools(ROOT, timeout=20.0)

    tasks = [
        (
            "联网两步:搜 -> 打开 -> 总结",
            "先用 web_search 搜一个主题,挑一条最相关的链接用 http_get 打开,"
            "然后用 3 条要点总结那一页讲了什么。不要编造;打不开就说打不开。\n"
            "主题:DeepSeek Harness (dsh) 是什么",
        ),
        (
            "本地两步:找 -> 读 -> 回答",
            "用 list_files 找到仓库文档目录下有哪些 .md 文件,"
            "再用 read_text_file 读其中 docs/README.md 的前 40 行,"
            "然后一句话回答:它一共列了几个文档子目录,分别叫什么。",
        ),
    ]

    for title, task in tasks:
        rule(f"⑤ 串起来 —— {title}")
        print(f"  任务: {_clip(task, 100)}")
        print(f"  模型: {cfg['model']}   预算: {BUDGET}   工具: 4 个只读工具\n")
        started = time.time()
        rec = execute(
            SubRunSpec(task=task, system=_SUB_SYSTEM, label=title, tools=tools, max_steps=6),
            llm,
        )
        print(f"  子运行: status={rec.status} detail={rec.detail} steps={rec.steps} "
              f"耗时={rec.duration:.1f}s (墙钟 {time.time() - started:.1f}s)")
        print(f"  usage: {rec.usage}\n")

        print("  它自己走了哪几步(轨迹,主日志里看不到这些):")
        for event in rec.events:
            if event["type"] == "tool/call":
                args = json.loads(event["data"].get("arguments") or "{}")
                brief = {k: _clip(str(v), 46) for k, v in args.items()}
                print(f"    → 调 {event['data']['name']}  {json.dumps(brief, ensure_ascii=False)}")
            elif event["type"] == "tool/result":
                inner = (event["data"].get("content") or [{}])[0]
                text = str(inner.get("text", ""))
                try:
                    payload = json.loads(text)
                    note = f"ok={payload.get('ok')} count={payload.get('count')}"
                    if payload.get("url"):
                        note += f" status={payload.get('status')} chars={payload.get('chars')}"
                except json.JSONDecodeError:
                    note = _clip(text, 60)
                print(f"      ← 结果 {note}")
        print(f"\n  它的结论:\n    {_clip(rec.output, 400)}")


_SUB_SYSTEM = (
    "你是一次性的执行单元,不属于任何对话,也不是任何角色。"
    "只完成交给你的任务,直接给出结果;不寒暄、不反问、不解释你在做什么。"
    "需要外部信息就调工具,不要凭记忆编。"
)


def main() -> None:
    print("=" * 66)
    print("  只读工具集 —— 效果演示")
    print("=" * 66)
    print("  这四个工具 = 给她装的眼睛和手。在那之前她只有 change_outfit,")
    print("  只能改自己衣服:外面的事不知道,本机的东西看不到。")

    tools = make_read_only_tools(ROOT, timeout=20.0)
    show_each(tools)

    if "--subagent" in sys.argv:
        show_subagent()
    else:
        rule("串起来能干什么?")
        print("  加 --subagent 跑真模型:让它自己搜、自己打开、自己出结论,")
        print("  并把它的多步轨迹打出来给你看。")
        print("    例: py test/lab_tools_probe.py --subagent")
        print("  (会真调模型,花一点钱)")

    print()
    print("=" * 66)


if __name__ == "__main__":
    main()
