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
    """会话落盘 → 重载:消息视图一致,遮蔽仍生效;Yona 常驻旗舰排第一。"""
    store, sid = _store_with_turn()
    store.delete_messages_from(sid, 2)
    store2 = SessionStore(store.sessions_dir.parent)  # 同一 data dir
    after = store2.get_session(sid)["messages"]
    assert after == []
    ids = [s["id"] for s in store2.list_sessions()]
    assert store2.flagship_session_id() == ids[0], "Yona 应常驻第一顺位"
    assert sid in ids, "普通会话仍在列表"


def test_flagship_recreated_after_delete_and_archived():
    """删 Yona = 归档整袋 + 立即重建空 Yona(重置她)。"""
    store = SessionStore(Path(tempfile.mkdtemp()))
    yid = store.flagship_session_id()
    log = store.load_log(yid)
    log.append("turn/start", turn=1)
    log.append("user/message", content=[{"type": "text", "text": "hello"}],
               source="user", turn=1)
    log.append("assistant/message", content=[{"type": "text", "text": "hi"}], turn=1)
    log.append("turn/end", turn=1, reason={"kind": "completed"})
    store.save_log(yid, log)
    assert store.load_log(yid).events  # Yona 有历史
    archived = store.delete_session(yid)  # 删 Yona → 归档
    assert archived and "archive" in archived
    new_yid = store.flagship_session_id()  # 保底:重建空 Yona
    assert new_yid != yid
    assert store.load_log(new_yid).events == []  # 新的从空白开始
    assert store.get_session(yid) is None  # 旧档案已不在 sessions 下


def test_self_turn_not_in_chat_view():
    """自走轮(source=self)的内容不进聊天流视图(占位/生活事件走内心活动)。"""
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


def test_life_backfill_order_is_shortest_gap_first():
    """补写处理顺序 = 离线间隔升序(updated_at 降序):当前卡排第一。

    2026-09-17 拍板(方案"乙"):启动时遍历所有有历史的卡,当前卡天然第一 ——
    它补完你就能立刻对话,其余在后台接着补。没聊过的卡不进这个列表。
    """
    store = SessionStore(Path(tempfile.mkdtemp()))
    old = store.create_session("老卡")
    new = store.create_session("新卡")
    silent = store.create_session("只点开过没说话")

    for sid, text in ((old, "很久以前"), (new, "刚刚")):
        log = store.load_log(sid)
        log.append("user/message", content=[{"type": "text", "text": text}],
                   source="user", turn=1)
        store.save_log(sid, log)
    # silent:建了卡但没有任何真人 user 消息

    order = store.life_backfill_order()
    assert silent not in order, "没聊过的卡不该进补写列表"
    assert set(order) == {old, new}, order
    # 刚聊过的那张排第一(离线间隔最短)
    store._write_meta(new, {**store._read_meta(new), "updated_at": "2099-01-01 00:00"})
    assert store.life_backfill_order()[0] == new, store.life_backfill_order()


def test_deleted_all_messages_kills_the_card_as_life_target():
    """把一张卡的消息**全删了** → 它不再算"有真人说过话",退出补写/自走目标。

    2026-09-22 10:45 用户拍板(口径原话):
    「如果是说当前会话 0 消息了的话,应该是整个对应日志归档,**会被补写等过滤掉**,
      当然**不继续补东西,就是死掉了**。」

    删除走的是 tail-cut shadow(日志原文一字不动),所以"全删"= 所有消息的 seq
    都被 `surface/shadow` 盖住 —— 判据必须按**可见消息**算,不能按原始事件算。
    按原始事件算的后果:卡被清空了,她还会被选为目标,继续往里写生活。

    这条同时钉住"四个地方一个口径":`derive_messages` / `_messages_view` /
    `view` 的两个投影 / 这里的卡选择,全都跳 shadow。
    """
    store = SessionStore(Path(tempfile.mkdtemp()))
    kept = store.create_session("还看得见")
    wiped = store.create_session("聊过但被清空")

    for sid, text in ((kept, "在吗"), (wiped, "在吗")):
        log = store.load_log(sid)
        log.append("user/message", content=[{"type": "text", "text": text}],
                   source="user", turn=1)
        store.save_log(sid, log)

    # 前置:两张卡都算"有真人说过话"
    assert set(store.life_backfill_order()) == {kept, wiped}

    # 把 wiped 整条删掉(tail-cut shadow,从第一条删到末尾)
    store.delete_messages_from(wiped, 0)

    # 日志原文还在(只是被遮蔽)—— 这是"归档 vs 遮蔽"的区别所在
    assert store.load_log(wiped).of_type("user/message"), "日志原文不该被改"
    assert store.load_log(wiped).shadowed_seqs(), "应有 shadow 注解"

    # 判据按可见消息算 → 它死掉了
    assert not store._has_user_talk(wiped), "全删光后不该再算'有真人说过话'"
    assert store._has_user_talk(kept), "没被删的卡不受影响"
    assert wiped not in store.life_backfill_order(), "清空的卡该退出补写目标"
    assert kept in store.life_backfill_order()


def test_emptied_session_log_gets_archived_and_card_stays():
    """清空消息 → **整段日志归档**,而**卡还在**(2026-09-22 11:20 用户拍板:"2 要")。

    用户口径:「如果是说当前会话 0 消息了的话,**应该是整个对应日志归档**,
    会被补写等过滤掉,当然**不继续补东西,就是死掉了**。」

    钉三件事,每一件都是一条"踩过就疼"的边界:
      ① 空了才搬,还有可见消息时**不搬**(护栏在 `archive_log()` 里,不靠调用方自觉);
      ② 只搬 `chat.log`,**`meta.json` 留着** —— 卡不能被搬走。原因见 `archive_log()`
         docstring:UI 的"重新生成/重试"走的是**同一个**删除端点、删完立刻重发,
         整卡搬走会让重发落进一个**没有 meta.json 的目录** → 会话从侧边栏消失
         而聊天还在往里写(`list_sessions()` 按 `*/meta.json` 枚举);
      ③ 幂等:再调一次返回 None、不抛(端点可能因并发/重复请求再调)。
    """
    store = SessionStore(Path(tempfile.mkdtemp()))
    kept = store.create_session("还有消息")
    wiped = store.create_session("聊过但被清空")

    for sid in (kept, wiped):
        log = store.load_log(sid)
        log.append("user/message", content=[{"type": "text", "text": "在吗"}],
                   source="user", turn=1)
        store.save_log(sid, log)

    # ① 护栏:还有可见消息 → 不搬
    assert store.archive_log(kept) is None, "还有消息的卡不该被归档"
    assert store._log_path(kept).exists(), "不该动它的 chat.log"

    # 全删光 → 判据为假、日志原文仍在(删除只加 shadow 注解)
    store.delete_messages_from(wiped, 0)
    assert not store._has_user_talk(wiped)
    assert store._log_path(wiped).exists(), "shadow 不删原文"

    # ② 归档:只搬 chat.log,meta.json 留在原位
    dest = store.archive_log(wiped)
    assert dest is not None, "空了的卡该被归档"
    assert (Path(dest) / "chat.log").exists(), "归档里应有 chat.log"
    assert not store._log_path(wiped).exists(), "活路径上的 chat.log 该没了"
    assert store.get_session(wiped) is not None, "卡必须还在(meta.json 不能搬走)"
    assert wiped in [s["id"] for s in store.list_sessions()], "卡还应在会话列表里"
    assert store.get_session(wiped)["messages"] == [], "它是个空卡"

    # ③ 幂等
    assert store.archive_log(wiped) is None, "再归档一次应安全返回 None"

    # 归档完还能重新开始(重发一条),且落在**这张卡自己的目录**里 ——
    # 这就是"只搬日志不搬卡"换来的好处
    log = store.load_log(wiped)
    log.append("user/message", content=[{"type": "text", "text": "重新开始"}],
               source="user", turn=1)
    store.save_log(wiped, log)
    assert store._log_path(wiped).parent == store._sid_dir(wiped)
    assert store._has_user_talk(wiped)


if __name__ == "__main__":
    test_message_view_projection()
    test_delete_from_is_tail_cut_shadow()
    test_update_message_content_is_replace()
    test_persistence_roundtrip()
    test_flagship_recreated_after_delete_and_archived()
    test_self_turn_not_in_chat_view()
    test_created_at_no_seconds_for_ui_slice()
    test_life_backfill_order_is_shortest_gap_first()
    test_deleted_all_messages_kills_the_card_as_life_target()
    test_emptied_session_log_gets_archived_and_card_stays()
    print("server/store all tests passed")
