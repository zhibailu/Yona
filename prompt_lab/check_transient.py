"""回归检查:recall 的结果,还会不会留在后续轮的上下文里? —— 零花费

跑: py prompt_lab/check_transient.py

⚠ 换个仓库跑之前先看这段(它读的也是**某个人的真聊天记录**,而 data/ 不进
  版本库;没有卡的话按根 README 起一次服务聊几句就有了)。要做三件事:
    ① `FINGERPRINT`(本文件下方)—— 现在这句出自我卡里的日记。必须换成
       **只在你自己卡里出现过**的一句话,否则"指纹在不在"比对的是空气。
    ② 那句假模型脚本里的 query/scope(下面 MockLLM 的脚本)也是照我卡写的,
       换卡后一起改 —— 它决定第一轮 recall 到底查得到查不到。
    ③ 卡片、时钟那几处跟着 `tool_recall.Apparatus` 走(它 import 的就是那个
       台子)→ 替换清单在 prompt_lab/tool_recall.py 顶端。

⛔ **本检查曾经是"假绿",根因不是指纹选得不好,是接线断了**(2026-09-22 查明并修):
  旧版唯一的判据是"第二轮的 blob 里还有没有第一轮的指纹",而**第一轮的结果里
   恒没有指纹**(它恒是"检索没能跑起来"那句,不是空串)——
   于是 `FINGERPRINT in blob` 恒为 `False`,**恒打印 ✅**。
   也就是说 `fold_tool_traces` 哪天被翻回 `False`,它也不会喊。
   第一轮为什么恒空:`prompt_lab/tool_recall.py` 的 `Apparatus` 里那句
   `if "recall" not in eng._tools.names()` **恒为假**(产品那件 recall 在
   `server/app/engine.py` **导入时**就注册好了),所以台子自己那件连着真卡片
   sqlite / 真嵌入器的工具**永远进不了注册表**;而实际跑的是产品那件,
   它的检索口 `recall_index()` 依赖只有 turn worker 才设的 `_recall_sid`,
   台子直接调 `eng._loop.run_turn` 绕过了 worker → `_recall_sid` 恒 `None`
   → 工具**恒返回** "检索没能跑起来"。完整证据链写在
   `prompt_lab/tool_recall.py` 的 `Apparatus.__init__` 那段标注里。
   **那一处没修好之前,本文件会直接 `raise` 而不是给结论** —— 这是有意的。

✅ 现在的判据是**阳性对照 + 两条断言**(不再靠肉眼,也不允许"没查到也算通过"):
     (甲) 第一轮跑完后,**断言**指纹真的出现在第一轮的 `tool/result` 原文里;
          拿不到就 `raise`(**不给结论**)—— 因为"第一轮没查到"会让第二轮
          当然也没有它,那种 ✅ 是假的。
     (乙) 再**断言**第二轮的 roles 里**没有** `tool`。
   两条都过才打印 ✅;**任何一条不成立都不出 ✅**。

查的是什么
  用户 2026-09-19 的原话:
  > 「**tool 的 result 是个瞬时产物,不拼进常驻内容,即使 system 内也不放**」

  这一条**可以机械验证**,不用问模型:跑两轮,把**第二轮实际发出去的 messages**
  打印出来,看第一轮 recall 的原文还在不在里面。

  它用**真台子的真引擎**(prompt_lab.tool_recall.Apparatus),只把 llm 换成
  假模型(MockLLM)—— 所以两轮都是 0 元、离线、确定。

结论(2026-09-19,2026-09-22 加固)
  改 `engine.py` 的 `fold_tool_traces` False → True 之前是 ❌,之后是 ✅。
  这条现在是**回归检查**:谁再把它翻回 False,这里会当场喊出来。
  ⚠ 但它只在**仪器接线完好**时才有意义 —— 接线断着时它 `raise`(见文件头 ⛔)。

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


def _tool_result_text(log) -> str:
    """把日志里全部 `tool/result` 事件的原文拼成一段(阳性对照用)。

    形状与两个台子的 `_blocks_text` 同源,但这里读的是**日志事件**的 content
    而不是 messages 的 content —— 而且故意**不做**那种"两份逐字重复"的复制,
    它只服务本文件这一处判据。
    """
    out: list[str] = []
    for e in log.events:
        if e.type != "tool/result":
            continue
        content = e.data.get("content")
        if isinstance(content, str):
            out.append(content)
        elif isinstance(content, list):
            out.append("\n".join(
                b.get("text", "") for b in content
                if isinstance(b, dict) and b.get("type") == "text"))
    return "\n".join(out)


def main() -> None:
    app = Apparatus("★定稿", memory=True, solo=True)

    print(f"\nfold_tool_traces(真引擎)   = {eng._loop.fold_tool_traces}")
    # ⚠ 这一行打的是**台子自己那件**工具的声明;而按 tool_recall.py 的标注,
    #    台子那件其实**没进注册表**(产品版顶了它的位),真正在跑的是产品那件。
    #    两者 `retain_result` 眼下同为 False,所以这个读数暂时不影响判读 ——
    #    但别把它当成"跑起来的那一件"的证据。
    print(f"recall.retain_result       = {app.tool.retain_result}"
          "   (台子那件;真正在跑的是产品版,见 tool_recall.py 的标注)")

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

    # ---------- (甲) 阳性对照:第一轮的结果里**必须真有**指纹 ----------
    # 没有它,下面的"第二轮没有指纹"是废话 —— 第一轮本来就没查到,
    # 第二轮当然也没有,那种 ✅ 是假绿(本文件 2026-09-22 之前就是这样)。
    first = _tool_result_text(app.log)
    if FINGERPRINT not in first:
        raise AssertionError(
            "阳性对照失败:第一轮 recall 的 tool/result 原文里**没有**指纹 "
            f"{FINGERPRINT!r} → 本轮**不给结论**(这个检查只有第一轮真查到了"
            "才有意义)。\n"
            "  最常见的原因不是指纹选得不好,而是**仪器接线断了**:\n"
            "  `prompt_lab/tool_recall.py` 的 Apparatus 里那句\n"
            "  `if \"recall\" not in eng._tools.names()` 恒为假(产品那件在\n"
            "  engine 导入时就注册好了),而真正在跑的产品 recall 又因为\n"
            "  `_recall_sid` 恒 None(台子绕过了 turn worker)而恒返回\n"
            "  \"检索没能跑起来\"。完整证据链见那个文件的 Apparatus 标注。\n"
            f"  第一轮 tool/result 原文(共 {len(first)} 字):\n"
            f"  ────────\n{first[:1200]}\n  ────────")

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

    print(f"\n第一轮 tool/result 原文里有指纹? -> {FINGERPRINT in first}"
          "   (阳性对照,已过)")
    print(f"第二轮发出去 {len(sent)} 条消息")
    print(f"  roles = {roles}")
    print(f"  第一轮 recall 的原文还在?  -> {FINGERPRINT in blob}"
          "   ← **只认这一行**")
    print(f"  'recall' 这个工具名还在?    -> {'recall' in blob}"
          "(正常:它在 SYSTEM 的 [可用工具用法] 段里)")

    # ---------- (乙) 第二轮的角色里不许有 tool ----------
    if tool_msgs:
        print(f"\n❌ 意图没实现:第二轮里还有 {len(tool_msgs)} 条 tool 消息"
              "(应该是 0)—— 检索结果**常驻**在上下文里了。")
        return
    if FINGERPRINT in blob:
        print("\n❌ 意图没实现(retain_result 那行空转):"
              "第二轮里已经没有 tool 消息,但第一轮 recall 的原文**还在**。")
        return
    print("\n✅ 两条都成立:第一轮确实查到了指纹(阳性对照),"
          "且第二轮里既没有 tool 消息、也没有第一轮 recall 的原文 —— "
          "检索结果只活在当轮。")


if __name__ == "__main__":
    main()
