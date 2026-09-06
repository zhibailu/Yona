"""重启补齐(醒来轮)自测:self_note 只对当轮可见,已结束轮投影自动跳过。

醒来轮 = 一次 source="self" 的自走,user 槽换成"刚醒"的情境说明(self_note)。
关键语义:说明是占位不是历史 —— 轮一结束,投影里就该消失,
否则每轮自走都带着"你刚醒,睡了 X 小时"的开场,污染她之后的生活流。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

from core.llm import AssistantOutput
from core.loop import AgentLoop
from core.session_log import SessionLog
from core.tools import ToolRegistry
from mock_llm import MockLLM


def _build_env():
    log = SessionLog("wake")
    tools = ToolRegistry([])
    llm = MockLLM([AssistantOutput(text="唔……怎么睡了这么久。")])
    loop = AgentLoop(log, llm, tools)
    return log, llm, loop


def _user_texts(log: SessionLog) -> list[str]:
    """投影里 user 槽的文本(应该只有当轮可见的占位/说明)。"""
    return [
        m["content"][0]["text"]
        for m in log.derive_messages()
        if m["role"] == "user"
    ]


def test_self_note_visible_only_in_current_turn():
    """self_note 进本轮 user 槽;轮结束后投影不再含它。"""
    log, llm, loop = _build_env()
    note = "【自动轮】此刻没有人跟你说话。你刚醒过来——你最后的记忆停在大约 8 小时前。"
    loop.run_turn(source="self", self_note=note)

    # 本轮模型看到的 user 槽 = self_note(当轮占位保留,是触发点)
    assert "8 小时前" in llm.seen_messages[0][-1]["content"][0]["text"]

    # 事件留痕:user/message 带 source=self,内容就是说明(审计可见)
    users = log.of_type("user/message")
    assert users[0].data["source"] == "self"
    assert "8 小时前" in users[0].data["content"][0]["text"]

    # 轮已结束 → 投影里 self_note 消失(已结束自走占位一律跳过)
    assert _user_texts(log) == []


def test_self_note_does_not_leak_into_next_turn():
    """下一轮自走(普通占位)看不到上一轮的醒来说明。"""
    log, llm, loop = _build_env()
    loop.run_turn(source="self", self_note="你刚醒过来,睡了大概 10 小时。")
    loop.run_turn(source="self")  # 普通自走

    # 第二轮模型输入里没有第一轮的醒来说(历史投影已清理)
    second = llm.seen_messages[1]
    assert all("10 小时" not in m.get("content", "") for m in second)


def test_user_turn_ignores_self_note():
    """self_note 只对 source=self 生效;真人消息轮不受影响。"""
    log, llm, loop = _build_env()
    loop.run_turn("你好", source="user", self_note="不该出现")
    assert _user_texts(log) == ["你好"]


def test_default_self_turn_unchanged():
    """不带 self_note 的自走轮:仍是默认协议占位串(回归保护)。"""
    log, llm, loop = _build_env()
    loop.run_turn(source="self")
    users = log.of_type("user/message")
    assert "【自动轮】" in users[0].data["content"][0]["text"]
    assert _user_texts(log) == []  # 结束后同样不进历史


if __name__ == "__main__":
    test_self_note_visible_only_in_current_turn()
    test_self_note_does_not_leak_into_next_turn()
    test_user_turn_ignores_self_note()
    test_default_self_turn_unchanged()
    print("wake(重启补齐) all tests passed")
