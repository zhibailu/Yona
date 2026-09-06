"""server/store.py 自测:消息视图投影 / shadow 删除 / replace 编辑 / 持久化。

UI 契约核心:消息 id = 事件 seq;删除 = tail-cut shadow;编辑 = replace。
跑: py test\\test_server_store.py
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from server.store import SessionStore
from core.session_log import SessionLog


def _store_with_turn() -> tuple[SessionStore, str]:
    """建一个临时 store + 一个有一轮对话的会话。"""
    store = SessionStore(Path(tempfile.mkdtemp()))
    sid = store.create_session("测试会话")
    log = store.load_log(sid)
    log.append("turn/start", turn=1)
    log.append("user/message", content=[{"type": "text", "text": "你好"}],
               source="user", turn=1)
    log.append("assistant/message", content=[{"type": "text", "text": "嗨,在的"}], turn=1)
    log.append("turn/end", turn=1, reason={"kind": "completed"})
    store.save_log(sid, log)
    return store, sid


def test_message_view_projection():
    """消息视图:user/assistant 事件 → 行,id = 事件 seq。"""
    store, sid = _store_with_turn()
    msgs = store.get_session(sid)["messages"]
    assert [m["id"] for m in msgs] == [2, 3], msgs  # turn/start seq=1
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[0]["content"] == "你好"
    assert msgs[1]["content"] == "嗨,在的"


def test_delete_from_is_tail_cut_shadow():
    """UI 级联删除 = shadow tail-cut:投影清空,日志原文保留 + 注解留痕。"""
    store, sid = _store_with_turn()
    deleted = store.delete_messages_from(sid, 2)  # 从 user 消息删到末尾
    assert deleted == 2
    after = store.get_session(sid)["messages"]
    assert after == []
    # 日志原文仍在(可审计)
    log = store.load_log(sid)
    assert log.of_type("surface/shadow"), "应有遮蔽注解"
    assert len(log.events) >= 5, "原文事件未删"
    # 但投影(模型视角)也没有这两条了
    msgs = log.derive_messages()
    assert all(m["role"] != "user" for m in msgs)


def test_update_message_content_is_replace():
    """编辑消息 = 遮蔽该条 + 追加修正消息(replaces),投影顶在原位。"""
    store, sid = _store_with_turn()
    ok = store.update_message_content(sid, 2, "你好呀(改)")
    assert ok
    msgs = store.get_session(sid)["messages"]
    # 只剩修正后的 user 消息(assistant 保留?编辑只动 user 那条)
    user_rows = [m for m in msgs if m["role"] == "user"]
    assert len(user_rows) == 1
    assert user_rows[0]["content"] == "你好呀(改)", msgs
    # 日志有遮蔽注解
    log = store.load_log(sid)
    assert log.of_type("surface/shadow"), "编辑应产生遮蔽注解"


def test_persistence_roundtrip():
    """会话落盘 → 重载:消息视图一致,遮蔽仍生效。"""
    store, sid = _store_with_turn()
    store.delete_messages_from(sid, 2)
    store2 = SessionStore(store.sessions_dir.parent)  # 同一 data dir
    after = store2.get_session(sid)["messages"]
    assert after == []
    assert store2.list_sessions()[0]["id"] == sid


def test_self_turn_not_in_chat_view():
    """自走轮(source=self)的内容不进聊天流视图(占位/自语走内心活动)。"""
    store = SessionStore(Path(tempfile.mkdtemp()))
    sid = store.create_session("test")
    log = store.load_log(sid)
    # 自走轮
    log.append("turn/start", turn=1, source="self")
    log.append("user/message",
               content=[{"type": "text", "text": "【自动轮】占位串"}],
               source="self", turn=1)
    log.append("assistant/message",
               content=[{"type": "text", "text": "凌晨的路灯还亮着……"}], turn=1)
    log.append("turn/end", turn=1, reason={"kind": "completed"})
    # 用户轮
    log.append("turn/start", turn=2)
    log.append("user/message", content=[{"type": "text", "text": "在吗"}],
               source="user", turn=2)
    log.append("assistant/message", content=[{"type": "text", "text": "在的"}], turn=2)
    log.append("turn/end", turn=2, reason={"kind": "completed"})
    store.save_log(sid, log)

    msgs = store.get_session(sid)["messages"]
    assert [m["content"] for m in msgs] == ["在吗", "在的"], msgs


def test_created_at_no_seconds_for_ui_slice():
    """created_at 不带秒:UI 用 slice(-5) 取 HH:MM。"""
    store, sid = _store_with_turn()
    msgs = store.get_session(sid)["messages"]
    for m in msgs:
        # "2026-09-04 11:43" 末尾 5 位是 "11:43",冒号在倒数第 3 位
        assert m["created_at"][-3] == ":", m["created_at"]
        tail = m["created_at"][-5:]
        assert len(tail) == 5 and ":" in tail, tail
        # 不带秒:冒号后只有 2 位数字
        assert m["created_at"][-2:].isdigit(), m["created_at"]
        assert len(m["created_at"].split(" ")[1].split(":")) == 2, m["created_at"]


if __name__ == "__main__":
    test_message_view_projection()
    test_delete_from_is_tail_cut_shadow()
    test_update_message_content_is_replace()
    test_persistence_roundtrip()
    test_self_turn_not_in_chat_view()
    test_created_at_no_seconds_for_ui_slice()
    print("server/store all tests passed")
