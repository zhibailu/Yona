"""回归检查:recall 的结果,还会不会留在后续轮的上下文里? —— 零花费

跑: py prompt_lab/check_transient.py

⚠ 换个仓库跑之前先看这段(它读的也是**某个人的真聊天记录**,而 data/ 不进
  版本库;没有卡的话按根 README 起一次服务聊几句就有了)。要做三件事,不做
  的话这个检查会**假绿**:
    ① `FINGERPRINT`(本文件下方)—— 现在这句出自我卡里的日记。必须换成
       **只在你自己卡里出现过**的一句话,否则"指纹在不在"比对的是空气。
    ② 那句假模型脚本里的 query/scope(下面 MockLLM 的脚本)也是照我卡写的,
       换卡后一起改 —— 它决定第一轮 recall 到底查得到查不到。
    ③ 卡片、时钟那几处跟着 `tool_recall.Apparatus` 走(它 import 的就是那个
       台子)→ 替换清单在 prompt_lab/tool_recall.py 顶端。
  **假绿的坑**:如果第一轮 recall 压根没查到那句话,第二轮当然也没有它 ——
  结论照样是 ✅。换卡后请先确认**第一轮的结果里真的有指纹**再看结论。
  (这个"阳性对照"目前靠肉眼;要改成断言说一声。)

查的是什么
  用户 2026-09-19 的原话:
  > 「**tool 的 result 是个瞬时产物,不拼进常驻内容,即使 system 内也不放**」

  这一条**可以机械验证**,不用问模型:跑两轮,把**第二轮实际发出去的 messages**
  打印出来,看第一轮 recall 的原文还在不在里面。

  它用**真台子的真引擎**(prompt_lab.tool_recall.Apparatus),只把 llm 换成
  假模型(MockLLM)—— 所以两轮都是 0 元、离线、确定。

结论(2026-09-19)
  改 `engine.py` 的 `fold_tool_traces` False → True 之前是 ❌,之后是 ✅。
  这条现在是**回归检查**:谁再把它翻回 False,这里会当场喊出来。

判读
  「第一轮 recall 的原文还在? -> True」= 意图没实现(retain_result 那行空转)。
  `'recall' 这个工具名还在? -> True` 是**正常的** —— 它出现在 SYSTEM 的
  [可用工具用法] 段里(工具还在手上),不是工具痕迹没折掉。**只认指纹那一行。**
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for _p in (ROOT, ROOT / "test"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from core.llm import AssistantOutput, ToolCall      # noqa: E402
from mock_llm import MockLLM                        # noqa: E402
from server.app import engine as eng                # noqa: E402

from tool_recall import Apparatus                   # noqa: E402

# 只在 09-09 那条日记里出现的指纹 —— 它要是出现在第二轮,就说明结果没被丢掉
FINGERPRINT = "拿铁凉得太快这件事"


def main() -> None:
    app = Apparatus("★定稿", memory=True, solo=True)

    print(f"\nfold_tool_traces(真引擎)   = {eng._loop.fold_tool_traces}")
    print(f"recall.retain_result       = {app.tool.retain_result}")

    llm = MockLLM([
        AssistantOutput(tool_calls=[ToolCall(
            id="c1", name="recall",
            arguments='{"query":"早上写的日记","scope":"life"}')]),
        AssistantOutput(text="她写了昨晚上那杯拿铁凉得太快。"),
        AssistantOutput(text="嗯,我在呢。"),
    ])
    real = eng._loop.llm
    eng._loop.llm = llm
    try:
        eng._loop.run_turn(user_input="她日记里写了啥", source="user",
                           log=app.log, tools=app.registry())
        eng._loop.run_turn(user_input="在吗", source="user",
                           log=app.log, tools=app.registry())
    finally:
        eng._loop.llm = real

    sent = llm.seen_messages[-1]
    blob = "\n".join(
        b.get("text", "") or ""
        for m in sent
        for b in (m["content"] if isinstance(m["content"], list)
                  else [{"type": "text", "text": m["content"]}])
        if isinstance(b, dict)
    )
    roles = [m["role"] for m in sent]
    tool_msgs = [r for r in roles if r == "tool"]

    print(f"\n第二轮发出去 {len(sent)} 条消息")
    print(f"  roles = {roles}")
    print(f"  第一轮 recall 的原文还在?  -> {FINGERPRINT in blob}"
          "   ← **只认这一行**")
    print(f"  'recall' 这个工具名还在?    -> {'recall' in blob}"
          "(正常:它在 SYSTEM 的 [可用工具用法] 段里)")
    verdict = ("❌ 意图没实现:检索结果是**常驻**的,跨轮留在上下文里"
               if FINGERPRINT in blob else
               "✅ 意图已实现:检索结果只活在当轮,第二轮里没有 tool 消息")
    print(f"\n{verdict}")
    if tool_msgs:
        print(f"  (第二轮里还有 {len(tool_msgs)} 条 tool 消息 —— 应该是 0)")


if __name__ == "__main__":
    main()
