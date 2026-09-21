"""recall 工具的**装配**自测:哪张卡、什么时候同步、卡片删了缓存怎么办。

单元测试(test_recall_tool.py)验的是工具本身;这里验的是**接线**。
接线的坏法全都是静默的:

  ① 翻错卡 —— 她有记忆,只是**别人的**。有 `recall` 又会返回结果,看不出来。
  ② 该同步时没同步 —— 她记不住刚说的话(或者更坏:还记得已经删掉的)。
  ③ 卡片删了缓存还在 —— 归档卡的对话继续留在盘上。

⚠️ 本文件会设 YONA_DATA_DIR / YONA_CACHE_DIR,**必须在 import engine 之前**,
   而且要用真实目录 —— 所以它自己建临时目录,不碰仓库里的 data/ 与 cache/。
"""

import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

_TMP = tempfile.TemporaryDirectory()
os.environ["YONA_DATA_DIR"] = str(Path(_TMP.name) / "data")
os.environ["YONA_CACHE_DIR"] = str(Path(_TMP.name) / "cache")

from server.app import engine  # noqa: E402
from server.store import SessionStore  # noqa: E402
from core.memory import MemoryRow  # noqa: E402
from core.memory_cache import MemoryCache  # noqa: E402
from core.session_log import SessionLog  # noqa: E402


class FakeEmb:
    """只为了让语义路可用(向量随便给);本文件不验检索质量。"""

    name, dim = "fake", 2

    def passage(self, text):
        return [1.0, 0.0]

    def query(self, text):
        return [1.0, 0.0]


_DIRS: list[tempfile.TemporaryDirectory] = []


def _setup():
    """干净的一套:新数据目录 + 假的嵌入器(不加载 BGE)。

    ⚠️ **每个用例一个新数据目录** —— 否则用例之间会互相看见对方的卡,
       `life_session_id()` 这类"最近激活"的判据就会指到上一个用例的卡上。

    ⚠️ 先 `engine.stop()`:它才是**关 sqlite 连接**的那一步(不只是停线程)。
       直接 `_memory.clear()` 会漏连接 —— 在 Windows 上等于文件被占住,
       临时目录都删不掉(实测踩过,报 WinError 32)。
    """
    engine.stop()
    d = tempfile.TemporaryDirectory()
    _DIRS.append(d)
    store = SessionStore(Path(d.name) / "data")
    engine._store = store
    engine._embedder_box.clear()
    engine._embedder_box.update({"tried": True, "emb": FakeEmb()})
    engine._recall_sid["sid"] = None
    return store


def _talk(sid, n, said, reply, t):
    """往日志里记一轮真人对话(形状与 core/session_log 的投影口径一致)。"""
    log = SessionLog(sid)
    log.append("turn/start", at=t, turn=n, source="user")
    log.append("user/message", at=t, turn=n, source="user",
               content=[{"type": "text", "text": said}])
    log.append("assistant/message", at=t + 1, turn=n,
               content=[{"type": "text", "text": reply}])
    log.append("turn/end", at=t + 1, turn=n)
    return log


# ============================================================
# 1. "现在这张卡"
# ============================================================

def test_the_turn_worker_publishes_which_card_is_running():
    """三个来源都从 _submit_turn 进 —— 所以那里是唯一要设"哪张卡"的地方。"""
    _setup()
    seen = {}

    def job():
        seen["in_job"] = engine._recall_sid["sid"]

    engine._submit_turn(job, sid="card-aaa")
    assert seen["in_job"] == "card-aaa", "worker 没把卡号公布出来"
    assert engine._recall_sid["sid"] is None, "跑完没清干净(下一项会读错卡)"


def test_two_cards_never_see_each_others_memory():
    """**最重要的一条**:她的记忆是按卡分的。

    串卡的后果不是报错,是"她有记忆,只是别人的" —— 而且返回得好好的。
    """
    store = _setup()
    a = store.create_session("A 卡")
    b = store.create_session("B 卡")
    store.save_log(a, _talk(a, 1, "今天吃什么", "晚上煮了碗面,卧了个蛋。", time.time()))
    store.save_log(b, _talk(b, 1, "今天吃什么", "把书架上的旧杂志搬下来擦了灰。", time.time()))

    engine._recall_sid["sid"] = a
    engine.memory_sync(a)
    idx_a = engine.recall_index()
    engine._recall_sid["sid"] = b
    engine.memory_sync(b)
    idx_b = engine.recall_index()

    texts_a = [r.text for r in idx_a.rows]
    texts_b = [r.text for r in idx_b.rows]
    assert any("卧了个蛋" in t for t in texts_a)
    assert any("旧杂志" in t for t in texts_b)
    assert not any("旧杂志" in t for t in texts_a), "A 卡读到了 B 卡的记忆"

    tool = engine._tools.get("recall")
    engine._recall_sid["sid"] = b
    out = tool.func({"query": "今天吃什么", "scope": "talk"})
    # ⚠️ 判据要用**够长的词**:单字判据会被别处的字撞上 ——
    #    先前用「面」就撞了边界句里的「上面没写的细节」,白白红了一次。
    assert "旧杂志" in out and "卧了个蛋" not in out, repr(out)


def test_it_follows_the_running_card_not_the_most_recently_talked_to_one():
    """**不能**拿 `life_session_id()` 顶替。

    它算的是"最近有人聊过的卡",而"刚换到一张新卡、说的第一句"那一刻,
    新卡的日志在盘上**还没有真人消息**(`_has_user_talk` 为假)——
    它会指回上一张卡,正好错在最常见的那一步(新卡的第一句话问"我上次说过什么")。

    这里把那个差**造出来**:老卡的 `updated_at` 更新(它是"最近激活"),
    而正在跑的这一轮在新卡上。
    """
    store = _setup()
    old = store.create_session("老卡")
    store.save_log(old, _talk(old, 1, "在吗", "在的。", time.time() - 60))
    fresh = store.create_session("新卡")
    store.save_log(fresh, _talk(fresh, 1, "你还记得吗", "记得什么呀。", time.time()))
    # 老卡变成"最近激活的那张"。
    # ⚠️ 直接改 meta 而不是 `touch_session()`:`updated_at` 是**分钟精度**
    #    ("%Y-%m-%d %H:%M"),同一分钟内建的卡会打平,而打平时它退化成
    #    目录遍历顺序 —— 前提就造不出来了(实测踩过一次)。
    meta = store._read_meta(old)
    meta["updated_at"] = "2099-01-01 00:00"
    store._write_meta(old, meta)

    engine.memory_sync(old)
    engine.memory_sync(fresh)

    # 前提确认:两个判据确实**指向不同的卡** —— 不然这条测试是空的
    assert engine.life_session_id() == old, "前提没造出来"

    engine._recall_sid["sid"] = fresh
    idx = engine.recall_index()
    assert [r.text for r in idx.rows] == ["你还记得吗\n记得什么呀。"], "跟了最近激活那张"

    engine._recall_sid["sid"] = old
    assert [r.text for r in engine.recall_index().rows] == ["在吗\n在的。"]


def test_no_card_running_means_no_index_not_a_wrong_one():
    """不在任何一轮里(比如工具被别处直接调)→ 返回 None,老实说 down。"""
    _setup()
    assert engine.recall_index() is None
    tool = engine._tools.get("recall")
    assert "检索没能跑起来" in tool.func({"query": "随便问问"})


# ============================================================
# 2. 同步:新日志进得去,删掉的话出得来
# ============================================================

def test_sync_picks_up_a_finished_turn():
    store = _setup()
    sid = store.create_session("卡")
    store.save_log(sid, _talk(sid, 1, "在吗", "在的。", time.time()))
    engine.memory_sync(sid)
    rows, _vecs = engine.memory_cache(sid).load()
    assert [r.text for r in rows] == ["在吗\n在的。"]


def test_sync_drops_a_message_the_user_deleted():
    """她在 UI 上删掉的话,**必须**从索引里消失。

    不删的后果不是"多一条结果",是**把她删掉的话翻出来念给他听**。
    """
    store = _setup()
    sid = store.create_session("卡")
    store.save_log(sid, _talk(sid, 1, "在吗", "在的。", time.time()))
    engine.memory_sync(sid)
    assert engine.memory_cache(sid).counts()["total"] == 1

    # 从最早那条可见消息起截断。同样用现取的 id,别硬编(seq 从 1 起)。
    first = store.get_messages(sid)[0]["id"]
    store.delete_messages_from(sid, from_id=first)
    engine.memory_sync(sid)
    assert engine.memory_cache(sid).counts()["total"] == 0, "删掉的话还留在索引里"


def test_editing_a_message_invalidates_its_vector():
    """改正文 → 旧向量作废(否则按旧内容排新内容的名次)。

    ⚠️ 断言要落在**正文**上,不能只断言"欠了一条"。先前这里只看 `pending == 1`,
       而"编辑后丢了半条"也满足它 —— 测试绿着,记忆已经残了(实测踩过)。
    """
    store = _setup()
    sid = store.create_session("卡")
    store.save_log(sid, _talk(sid, 1, "今天吃什么", "晚上煮了碗面,卧了个蛋。", time.time()))
    engine.memory_sync(sid)
    cache = engine.memory_cache(sid)
    cache.stop()                 # 停后台,免得它和下面的手工 fill 抢
    assert cache.fill_one(force=True)
    assert cache.counts()["pending"] == 0

    # 消息 id = 事件 seq。⚠️ 落盘回读后 seq 从 **1** 起(不是 0),
    # 所以这里那句真人消息是 2。用 store.get_messages 现取比硬编更稳:
    ids = [m["id"] for m in store.get_messages(sid) if m["role"] == "user"]
    assert ids, "没找到那句真人消息"
    assert store.update_message_content(sid, ids[0], "今天晚饭吃什么好呢")
    engine.memory_sync(sid)
    assert cache.counts()["pending"] == 1, "正文改了却没作废向量"

    rows, _vecs = cache.load()
    assert [r.text for r in rows] == ["今天晚饭吃什么好呢\n晚上煮了碗面,卧了个蛋。"], \
        f"编辑后那一行残了: {[r.text for r in rows]}"


def test_sync_is_idempotent_and_cheap():
    """它**每一轮都会跑一遍** —— 原样同步一个字节都不该写。"""
    store = _setup()
    # 不补向量 + 停后台线程:后台补向量本身也写库,会污染下面的计数
    engine._embedder_box.update({"tried": True, "emb": None})
    sid = store.create_session("卡")
    store.save_log(sid, _talk(sid, 1, "在吗", "在的。", time.time()))
    engine.memory_sync(sid)
    cache = engine.memory_cache(sid)
    cache.stop()
    before = cache._conn.total_changes
    engine.memory_sync(sid)
    engine.memory_sync(sid)
    assert cache._conn.total_changes == before, "原样同步也写了库"


# ============================================================
# 3. 卡片删了,缓存跟着走
# ============================================================

def test_forget_removes_the_cache_of_a_deleted_card():
    store = _setup()
    sid = store.create_session("卡")
    store.save_log(sid, _talk(sid, 1, "在吗", "在的。", time.time()))
    engine.memory_sync(sid)
    p = engine.memory_cache(sid).path
    assert p.exists()

    assert engine.memory_forget(sid) is True
    assert not p.exists(), "归档卡的对话继续留在盘上"


def test_forget_does_not_create_the_cache_it_is_about_to_delete():
    """⚠️ 走 `memory_cache()` 会**先把它建出来**再删 —— 白起线程、白写文件。

    所以判断"这张卡有没有缓存"必须用纯路径计算。
    """
    _setup()
    sid = "0" * 32
    p = MemoryCache.path_for(engine.MEMORY_DIR, sid)
    assert not p.exists()
    assert engine.memory_forget(sid) is False
    assert not p.exists(), "forget 把缓存建出来了"
    assert not p.parent.exists() or not list(p.parent.glob(f"{sid}*"))


def test_forget_after_a_cache_exists_stops_its_thread():
    _setup()
    sid = "1" * 32
    c = engine.memory_cache(sid)
    c.start()
    assert c.status()["running"] is True
    engine.memory_forget(sid)
    assert engine._memory == {}, "还留在管理器里 → 下次还会用这个已删的缓存"


def run_all():
    fns = [
        test_the_turn_worker_publishes_which_card_is_running,
        test_two_cards_never_see_each_others_memory,
        test_it_follows_the_running_card_not_the_most_recently_talked_to_one,
        test_no_card_running_means_no_index_not_a_wrong_one,
        test_sync_picks_up_a_finished_turn,
        test_sync_drops_a_message_the_user_deleted,
        test_editing_a_message_invalidates_its_vector,
        test_sync_is_idempotent_and_cheap,
        test_forget_removes_the_cache_of_a_deleted_card,
        test_forget_does_not_create_the_cache_it_is_about_to_delete,
        test_forget_after_a_cache_exists_stops_its_thread,
    ]
    try:
        for f in fns:
            f()
    finally:
        engine.stop()
        for d in _DIRS:
            d.cleanup()
    print(f"recall wiring all tests passed ({len(fns)} 条)")


if __name__ == "__main__":
    run_all()
