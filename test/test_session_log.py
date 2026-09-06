"""SessionLog 自测：一轮带工具的完整 turn，验证投影与回放。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.session_log import SessionLog


def build_turn() -> SessionLog:
    log = SessionLog("test-session")
    log.append("turn/start", turn=1)
    log.append("user/message", content=[{"type": "text", "text": "查一下今天的天气"}])
    log.append("step/start", turn=1, step=1)
    log.append(
        "assistant/message",
        content=[
            {"type": "reasoning", "text": "用户要天气，需要联网搜索。"},
            {
                "type": "tool-call",
                "id": "call_001",
                "name": "web_search",
                "arguments": '{"query": "today weather"}',
            },
        ],
    )
    log.append(
        "tool/result",
        tool_call_id="call_001",
        content=[{"type": "text", "text": "晴天，28°C"}],
    )
    log.append("step/end", turn=1, step=1)
    log.append("step/start", turn=1, step=2)
    log.append(
        "assistant/message",
        content=[{"type": "text", "text": "今天晴天，28 度。"}],
    )
    log.append("step/end", turn=1, step=2)
    log.append("turn/end", turn=1, reason={"kind": "completed"})
    return log


def test_derive_messages():
    log = build_turn()
    msgs = log.derive_messages()
    assert len(msgs) == 4, f"应 4 条消息，实际 {len(msgs)}"

    # 1. 用户消息
    assert msgs[0]["role"] == "user"
    assert msgs[0]["content"][0]["text"] == "查一下今天的天气"
    # 2. assistant 带 tool-call
    assert msgs[1]["role"] == "assistant"
    assert msgs[1]["content"][1]["type"] == "tool-call"
    assert msgs[1]["content"][1]["id"] == "call_001"
    # 3. tool 结果回挂同一个 id
    assert msgs[2]["role"] == "tool"
    assert msgs[2]["tool_call_id"] == "call_001"
    # 4. 最终回答
    assert msgs[3]["role"] == "assistant"
    assert msgs[3]["content"][0]["text"] == "今天晴天，28 度。"


def test_replay_and_recover():
    log = build_turn()
    # 模拟"崩溃在 tool/result 之后"，从那里回放
    crash_seq = log.of_type("tool/result")[0].seq
    tail = log.replay_from(crash_seq)
    assert [e.type for e in tail] == ["tool/result", "step/end", "step/start", "assistant/message", "step/end", "turn/end"]

    # 用 tail 重建一个新 log，derive 应得到"工具结果之后"的消息
    rebuilt = SessionLog(log.session_id, tail)
    msgs = rebuilt.derive_messages()
    assert msgs[0]["role"] == "tool"
    assert msgs[0]["tool_call_id"] == "call_001"


def test_persistence_roundtrip():
    log = build_turn()
    lines = log.to_lines()
    rebuilt = SessionLog.from_lines("test-session", lines)
    assert rebuilt.derive_messages() == log.derive_messages()
    assert rebuilt.last_seq() == log.last_seq()


def test_derive_skips_empty_assistant():
    """空 assistant 消息(无文本/无 tool-call)不进投影,防止下一轮 400。"""
    log = SessionLog("test")
    log.append("turn/start", turn=1)
    log.append("user/message", content=[{"type": "text", "text": "hi"}])
    log.append("assistant/message", content=[])  # 空(如历史遗留)
    log.append("assistant/message", content=[{"type": "text", "text": "正常回答"}])
    msgs = log.derive_messages()
    assert len(msgs) == 2, f"空消息未跳过: {msgs}"
    assert msgs[1]["content"][0]["text"] == "正常回答"


def build_two_turns() -> SessionLog:
    """两轮对话:Q1/A1(轮1)、Q2/A2(轮2)。返回 log 和各自事件 seq。"""
    log = SessionLog("test")
    log.append("turn/start", turn=1)
    u1 = log.append("user/message", content=[{"type": "text", "text": "第一问"}])
    a1 = log.append("assistant/message", content=[{"type": "text", "text": "第一答"}])
    log.append("turn/end", turn=1, reason={"kind": "completed"})
    log.append("turn/start", turn=2)
    u2 = log.append("user/message", content=[{"type": "text", "text": "第二问"}])
    a2 = log.append("assistant/message", content=[{"type": "text", "text": "第二答"}])
    log.append("turn/end", turn=2, reason={"kind": "completed"})
    return log, {"u1": u1.seq, "a1": a1.seq, "u2": u2.seq, "a2": a2.seq}


def test_shadow_hides_from_projection_but_keeps_log():
    """遮蔽 = 打 tag 声明不用:投影跳过,日志原文一字不动。"""
    log, seqs = build_two_turns()
    before = len(log.events)

    # 删掉"第一问 + 第一答"(轮 1 整段)
    log.shadow(seqs["u1"], seqs["a1"], reason="user-deleted")

    msgs = log.derive_messages()
    assert len(msgs) == 2, f"应只剩轮2两条,实际 {msgs}"
    assert msgs[0]["role"] == "user" and msgs[0]["content"][0]["text"] == "第二问"
    assert msgs[1]["role"] == "assistant" and msgs[1]["content"][0]["text"] == "第二答"
    # 日志原文没少,只是多了条注解
    assert len(log.events) == before + 1
    u1_event = next(e for e in log.events if e.seq == seqs["u1"])
    assert u1_event.data["content"][0]["text"] == "第一问"  # 还在


def test_shadow_single_seq_and_states():
    """遮蔽单条 + surface 三态查询。"""
    log, seqs = build_two_turns()
    log.shadow(seqs["u1"], reason="typo")

    assert log.shadowed_seqs() == {seqs["u1"]}
    states = log.surface_states()
    assert states[seqs["u1"]] == "shadowed"
    assert states[seqs["a1"]] == "current"  # 回复还在(只删了问,留答是调用方选择)
    assert states[seqs["u2"]] == "current"
    # turn/start 是 log-only
    first = next(e for e in log.events if e.type == "turn/start")
    assert states[first.seq] == "log-only"


def test_shadow_persists_roundtrip():
    """遮蔽注解随日志持久化:重载后投影一致。"""
    log, seqs = build_two_turns()
    log.shadow(seqs["u1"], seqs["a1"], reason="deleted")
    lines = log.to_lines()
    rebuilt = SessionLog.from_lines("test", lines)
    assert rebuilt.shadowed_seqs() == {seqs["u1"], seqs["a1"]}
    assert rebuilt.derive_messages() == log.derive_messages()


def test_shadow_combines_with_fold_view():
    """遮蔽与折叠视图正交:两者同时生效不冲突。"""
    log = SessionLog("test")
    log.append("turn/start", turn=1)
    u = log.append("user/message", content=[{"type": "text", "text": "换衣服"}])
    log.append("step/start", turn=1, step=1)
    a = log.append(
        "assistant/message",
        content=[
            {"type": "tool-call", "id": "c1", "name": "change_outfit",
             "arguments": '{"clothes": "红卫衣"}'},
        ],
    )
    log.append("tool/result", tool_call_id="c1",
               content=[{"type": "text", "text": "已换"}])
    log.append("step/end", turn=1, step=1)
    log.append("turn/end", turn=1, reason={"kind": "completed"})

    # 遮蔽整段(用户消息 + 带 tool-call 的 assistant + tool/result),
    # 折叠视图也无从谈起:投影为空(注解本身是 log-only 不进投影)
    tool_result_seq = log.of_type("tool/result")[0].seq
    log.shadow(u.seq, tool_result_seq)
    assert log.derive_messages(fold_tool_traces=True) == []


def test_shadow_partial_leaves_orphan_tool():
    """只遮 user+assistant、漏 tool/result 会被拒绝 —— 配对必须同段
    (对齐 dsh fork 约束:不许切出孤儿 tool 消息,否则投影不合法)。"""
    log = SessionLog("test")
    log.append("turn/start", turn=1)
    u = log.append("user/message", content=[{"type": "text", "text": "换衣服"}])
    log.append("step/start", turn=1, step=1)
    a = log.append(
        "assistant/message",
        content=[
            {"type": "tool-call", "id": "c1", "name": "change_outfit",
             "arguments": '{"clothes": "红卫衣"}'},
        ],
    )
    log.append("tool/result", tool_call_id="c1",
               content=[{"type": "text", "text": "已换"}])
    log.append("step/end", turn=1, step=1)
    log.append("turn/end", turn=1, reason={"kind": "completed"})

    try:
        log.shadow(u.seq, a.seq)  # 漏了 tool/result
        raise AssertionError("应当拒绝切开工配对的遮蔽")
    except ValueError as exc:
        assert "tool-call #c1" in str(exc) and "同段" in str(exc)

    # 遮整段(含 tool/result)则合法
    tool_result_seq = log.of_type("tool/result")[0].seq
    log.shadow(u.seq, tool_result_seq)
    assert log.derive_messages(fold_tool_traces=True) == []


def test_shadow_rejects_future_seq():
    """不能遮蔽还没发生的事件(范围超 last_seq 拒绝)。"""
    log, seqs = build_two_turns()
    try:
        log.shadow(seqs["u1"], seqs["u2"] + 5)
        raise AssertionError("应当拒绝超范围遮蔽")
    except ValueError as exc:
        assert "超出已发生事件" in str(exc)


def test_shadow_tail_cut_semantics():
    """UI 删除语义 = fork 类 tail-cut:从选中消息切到日志末尾,后面全作废。
    这里只验证内核行为:shadow 到末尾后,末尾之后新追加的事件不受影响。"""
    log, seqs = build_two_turns()
    # 模拟用户撤回第二问:遮蔽 u2..末尾(最后一条是 turn/end,无工具配对问题)
    log.shadow(seqs["u2"], log.last_seq(), reason="user-retract")
    msgs = log.derive_messages()
    # 只剩轮 1 的两条
    assert [m["content"][0]["text"] for m in msgs] == ["第一问", "第一答"]
    # 日志原文仍在(可审计)
    assert log.of_type("surface/shadow")[0].data["reason"] == "user-retract"


def test_replace_renders_at_shadow_start():
    """compact 原语:遮蔽中间段 + 摘要渲染在遮蔽起点,后续对话顺序不乱。"""
    log = SessionLog("test")
    # 轮 1(将被压缩)
    log.append("turn/start", turn=1)
    u1 = log.append("user/message", content=[{"type": "text", "text": "今天天气?"}])
    a1 = log.append("assistant/message", content=[{"type": "text", "text": "晴天 28 度"}])
    log.append("turn/end", turn=1, reason={"kind": "completed"})
    # 轮 2(将保留,继续对话)
    log.append("turn/start", turn=2)
    u2 = log.append("user/message", content=[{"type": "text", "text": "那明天呢?"}])
    a2 = log.append("assistant/message", content=[{"type": "text", "text": "明天也晴"}])
    log.append("turn/end", turn=2, reason={"kind": "completed"})

    # 压缩轮 1:遮蔽 u1..a1,摘要渲染在轮 1 位置
    log.replace(
        u1.seq, a1.seq,
        [{"type": "text", "text": "【摘要】用户问过天气,答晴天 28 度。"}],
        reason="compact",
    )
    msgs = log.derive_messages()
    texts = [m["content"][0]["text"] for m in msgs]
    assert texts == [
        "【摘要】用户问过天气,答晴天 28 度。",  # 摘要顶在遮蔽段起点
        "那明天呢?",                            # 轮 2 对话在摘要之后,顺序不乱
        "明天也晴",
    ], f"replace 渲染位置错误: {texts}"
    # 被压缩的原文仍在日志(可审计)
    assert log.of_type("assistant/message")[0].data["content"][0]["text"] == "晴天 28 度"


def test_replace_head_tail_and_multiple():
    """replace 可压头部/可连续多次;shadowed 与替换互不干扰。"""
    log = SessionLog("test")
    log.append("turn/start", turn=1)
    u1 = log.append("user/message", content=[{"type": "text", "text": "q1"}])
    log.append("assistant/message", content=[{"type": "text", "text": "a1"}])
    log.append("turn/end", turn=1, reason={"kind": "completed"})
    log.append("turn/start", turn=2)
    u2 = log.append("user/message", content=[{"type": "text", "text": "q2"}])
    log.append("assistant/message", content=[{"type": "text", "text": "a2"}])
    log.append("turn/end", turn=2, reason={"kind": "completed"})

    # 压两轮 → 两条摘要,都渲染在各自遮蔽段起点,按日志顺序排
    log.replace(u1.seq, log.of_type("assistant/message")[0].seq,
                [{"type": "text", "text": "S1"}])
    log.replace(u2.seq, log.of_type("assistant/message")[1].seq,
                [{"type": "text", "text": "S2"}])
    texts = [m["content"][0]["text"] for m in log.derive_messages()]
    assert texts == ["S1", "S2"], f"多 replace 顺序错误: {texts}"


def test_replace_persists_roundtrip():
    """replace 注解+摘要随日志持久化,重载后渲染一致。"""
    log = SessionLog("test")
    log.append("turn/start", turn=1)
    u1 = log.append("user/message", content=[{"type": "text", "text": "q1"}])
    log.append("assistant/message", content=[{"type": "text", "text": "a1"}])
    log.append("turn/end", turn=1, reason={"kind": "completed"})
    log.replace(u1.seq, log.last_seq(), [{"type": "text", "text": "S"}])
    lines = log.to_lines()
    rebuilt = SessionLog.from_lines("test", lines)
    assert rebuilt.derive_messages() == log.derive_messages()
    assert rebuilt.shadowed_seqs() == log.shadowed_seqs()


def _append_completed_turn(log, n: int) -> None:
    """手造一轮完整已结束轮(user -> assistant 一句)。"""
    log.append("turn/start", turn=n, source="user")
    log.append("user/message", content=[{"type": "text", "text": f"问{n}"}],
               source="user", turn=n)
    log.append("step/start", turn=n, step=1)
    log.append("assistant/message",
               content=[{"type": "text", "text": f"答{n}"}], turn=n, step=1)
    log.append("step/end", turn=n, step=1)
    log.append("turn/end", turn=n, reason={"kind": "completed"})


def test_last_turns_window_keeps_recent_ended_plus_open_turn():
    """轮窗口:保留最近 N 个已结束轮 + 当前未结束轮;0/None = 全量。"""
    log = SessionLog("t")
    for i in (1, 2, 3):
        _append_completed_turn(log, i)
    # 当前未结束轮(在跑,永不裁)
    log.append("turn/start", turn=4, source="user")
    log.append("user/message", content=[{"type": "text", "text": "问4"}],
               source="user", turn=4)
    log.append("step/start", turn=4, step=1)
    log.append("assistant/message",
               content=[{"type": "text", "text": "答4(还没说完)"}], turn=4, step=1)

    msgs = log.derive_messages(last_turns=2)
    texts = [m["content"][0]["text"] for m in msgs]
    # 最近 2 个已结束轮 = 轮 2、3;轮 1 被裁;当前轮 4 永不裁
    assert texts == ["问2", "答2", "问3", "答3", "问4", "答4(还没说完)"], texts
    # 0 / None = 全量(行为不变)
    assert len(log.derive_messages(last_turns=0)) == 8
    assert len(log.derive_messages()) == 8
    assert len(log.derive_messages(last_turns=10)) == 8  # 窗口比轮数大 -> 全量


def test_last_turns_drops_whole_turns_never_splits_tool_pairs():
    """窗口外的已结束轮整轮消失(含其 tool 配对),窗口内的配对保持完整。"""
    log = SessionLog("t")
    for n in (1, 2):
        log.append("turn/start", turn=n, source="user")
        log.append("user/message", content=[{"type": "text", "text": f"问{n}"}],
                   source="user", turn=n)
        log.append("step/start", turn=n, step=1)
        # 先调工具(工具对在日志里),再答
        log.append("assistant/message",
                   content=[{"type": "tool-call", "id": f"c{n}", "name": "weather",
                             "arguments": "{}"}], turn=n, step=1)
        log.append("tool/call", turn=n, step=1, call_id=f"c{n}",
                   name="weather", arguments="{}")
        log.append("tool/result", turn=n, step=1, tool_call_id=f"c{n}",
                   content=[{"type": "text", "text": f"结果{n}"}], is_error=False)
        log.append("step/end", turn=n, step=1)
        log.append("assistant/message",
                   content=[{"type": "text", "text": f"答{n}"}], turn=n, step=2)
        log.append("step/end", turn=n, step=2)
        log.append("turn/end", turn=n, reason={"kind": "completed"})
    # 当前轮 3(无工具)
    log.append("turn/start", turn=3, source="user")
    log.append("user/message", content=[{"type": "text", "text": "问3"}],
               source="user", turn=3)

    msgs = log.derive_messages(last_turns=1)
    # 只剩轮 2(工具对完整:tool 消息在) + 当前轮 3;轮 1 整轮没了
    assert [m["role"] for m in msgs] == \
        ["user", "assistant", "tool", "assistant", "user"], msgs
    assert any(m["role"] == "tool" for m in msgs)  # 保留轮的配对在
    all_text = str(msgs)
    assert "问1" not in all_text and "答1" not in all_text
    assert "问2" in all_text and "问3" in all_text


if __name__ == "__main__":
    test_derive_messages()
    test_replay_and_recover()
    test_persistence_roundtrip()
    test_derive_skips_empty_assistant()
    test_shadow_hides_from_projection_but_keeps_log()
    test_shadow_single_seq_and_states()
    test_shadow_persists_roundtrip()
    test_shadow_combines_with_fold_view()
    test_shadow_partial_leaves_orphan_tool()
    test_shadow_rejects_future_seq()
    test_shadow_tail_cut_semantics()
    test_replace_renders_at_shadow_start()
    test_replace_head_tail_and_multiple()
    test_replace_persists_roundtrip()
    test_last_turns_window_keeps_recent_ended_plus_open_turn()
    test_last_turns_drops_whole_turns_never_splits_tool_pairs()
    print("SessionLog all tests passed")
