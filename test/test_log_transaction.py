"""日志事务自测(2026-09-17 修:锁外加载 + 锁内全量覆盖 = 静默丢数据)

**坑**:`store.load_log` **无缓存** —— 每次读盘返回一个**新对象**;而
`save_log` 是**整体覆盖**(`p.write_text("\\n".join(log.to_lines()))`)。
于是"加载 → 改 → 存盘"这三步必须整段在同一把锁(`engine._lock`)内,
否则**锁外加载的旧副本会在拿到锁之后,覆盖掉别人在等待期间写进去的东西**。

症状(都不是报错,是静默):
  1. 补写跑到一半你发消息 → 你那份是旧副本 → 存盘把补写整段抹掉;
  2. 连发两条 → 第二条读盘时第一条还没落盘 → 她看不到你上一条,存盘还把它覆盖掉。

修法:三个入口都改成**持锁之后才加载**。本文件钉住:
  - 事实层:load_log 无缓存 / save_log 全量覆盖(为什么会出问题);
  - 反证层:旧写法(锁外加载)确实会丢数据;
  - 产品层:engine.LifeLoop.run_turn 与 main.pulse_autonomy 加载时确实持锁。

⚠️ 聊天入口(`server/app/api/chat.py`)走 FastAPI 请求对象,本文件不直接驱动;
它的修法与另外两处一致(注释里指向 docs/pitfalls/HISTORY.md §四)。

跑: py test\\test_log_transaction.py
"""

import asyncio
import os
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

# ⚠️ 记忆索引缓存指到临时目录(必须在 import engine **之前**)。
#    本文件会跑真实的 turn 队列,而 turn worker 每跑完一项就同步一次索引缓存 ——
#    不指走的话,跑测试会在**仓库里**留下 cache/sessions/*.sqlite3。
#    规矩出处:测试不许要求仓库为它让路,临时产物写系统 temp。
os.environ["YONA_CACHE_DIR"] = tempfile.mkdtemp(prefix="yona-test-cache-")

from core.session_log import SessionLog        # noqa: E402
from server.store import SessionStore          # noqa: E402
from server.app import engine as eng           # noqa: E402
from server.app.gate import ServerGate         # noqa: E402

# 假连接配置:_build_engine 只构造 LLM 客户端对象,不发网络请求(测试安全)。
_FAKE_CFG = {
    "base_url": "http://127.0.0.1:9",
    "api_key": "sk-fake-test",
    "model": "m",
    "models": ["m"],
}


def _fresh_store() -> SessionStore:
    store = SessionStore(Path(tempfile.mkdtemp()))
    store.create_session()
    return store


# ---------- 事实层:为什么会出问题 ----------


def test_load_log_has_no_cache():
    """load_log 每次返回**新对象**。若哪天加了缓存,这条不变量要重新审。"""
    store = _fresh_store()
    sid = store.create_session()
    a = store.load_log(sid)
    b = store.load_log(sid)
    assert a is not b, "load_log 开始返回同一对象了 —— 请重新评估锁外加载的前提"


def test_save_log_overwrites_whole_file():
    """save_log 是整体覆盖:拿**旧副本**存盘 = 把新写的东西回退掉。"""
    store = _fresh_store()
    sid = store.create_session()

    stale = store.load_log(sid)                    # 旧副本
    fresh = store.load_log(sid)                    # 新副本
    fresh.append("user/message", content=[{"type": "text", "text": "新的"}])
    store.save_log(sid, fresh)
    assert len(store.load_log(sid).events) == 1

    store.save_log(sid, stale)                     # ← 旧副本后写,赢了
    assert len(store.load_log(sid).events) == 0, "旧副本没覆盖成功?前提变了"


def test_stale_copy_loaded_outside_the_lock_loses_data():
    """反证:**锁外加载**会让一次写盘整段消失(旧写法的病)。

    线程 A 走旧写法:锁外加载 → 等锁 → 追加 → 存盘。
    线程 B 走新写法:持锁加载 → 追加 → 存盘。
    B 先完成;A 拿着过期副本后完成 —— A 的存盘把 B 抹掉。
    """
    store = _fresh_store()
    sid = store.create_session()
    lock = threading.Lock()
    b_done = threading.Event()

    def writer_b():
        with lock:
            log = store.load_log(sid)              # ✅ 持锁加载
            log.append("user/message", content=[{"type": "text", "text": "B"}])
            store.save_log(sid, log)
        b_done.set()

    def writer_a_old_way():
        log = store.load_log(sid)                  # ❌ 锁外加载(旧写法)
        b_done.wait(2.0)                           # 让 B 先跑完
        with lock:
            log.append("user/message", content=[{"type": "text", "text": "A"}])
            store.save_log(sid, log)               # 用旧副本覆盖 → B 没了

    ta = threading.Thread(target=writer_a_old_way)
    tb = threading.Thread(target=writer_b)
    ta.start()
    tb.start()
    ta.join(5.0)
    tb.join(5.0)

    texts = [
        e.data["content"][0]["text"]
        for e in store.load_log(sid).events
        if e.type == "user/message"
    ]
    assert "B" not in texts, (
        "反证失效了 —— 旧写法居然没丢数据,说明本文件的前提理解有误"
    )
    assert texts == ["A"], texts


def test_load_inside_the_lock_keeps_both_writers():
    """正证:两边都**持锁加载**,两条都留得下。"""
    store = _fresh_store()
    sid = store.create_session()
    lock = threading.Lock()
    barrier = threading.Barrier(2)

    def writer(tag):
        barrier.wait(2.0)
        with lock:
            log = store.load_log(sid)              # ✅ 持锁加载
            log.append("user/message", content=[{"type": "text", "text": tag}])
            store.save_log(sid, log)

    ts = [threading.Thread(target=writer, args=(t,)) for t in ("A", "B")]
    for t in ts:
        t.start()
    for t in ts:
        t.join(5.0)

    texts = sorted(
        e.data["content"][0]["text"]
        for e in store.load_log(sid).events
        if e.type == "user/message"
    )
    assert texts == ["A", "B"], texts


# ---------- 产品层:两个入口加载时确实持锁 ----------


def _spy_load() -> tuple[list[bool], object]:
    """记录每次 load_log 时引擎锁是否被持有。返回 (观测列表, 原函数)。"""
    seen: list[bool] = []
    real = eng._store.load_log

    def spy(session_id):
        seen.append(eng._lock.locked())
        return real(session_id)

    eng._store.load_log = spy
    return seen, real


def test_lifeloop_loads_log_while_holding_the_lock():
    """心跳/自走轮:加载发生在这把锁**里面**(engine.LifeLoop.run_turn)。"""
    eng._build_engine(dict(_FAKE_CFG))
    original_store = eng._store
    eng._store = _fresh_store()
    seen, real = _spy_load()
    try:
        life_loop = eng.LifeLoop(ServerGate())
        # source="self" + 空日志 → begin_self_wake 返回 0 → 安静结束。
        # 不调 LLM、不发网络请求,但 load_log 已经走过了。
        result = life_loop.run_turn(source="self")
        assert result is None, "空日志应当安静结束"
        assert seen, "run_turn 没调用 load_log?"
        assert all(seen), f"load_log 在锁外被调用了: {seen}"
    finally:
        eng._store.load_log = real
        eng._store = original_store


def test_pulse_loads_log_while_holding_the_lock():
    """手动脉冲:加载同样发生在这把锁**里面**(server/main.py)。"""
    from server import main as srv

    eng._build_engine(dict(_FAKE_CFG))
    original_store = eng._store
    eng._store = _fresh_store()
    seen, real = _spy_load()
    try:
        out = asyncio.run(srv.pulse_autonomy())
        assert out.get("quiet") is True, out
        assert seen, "pulse 没调用 load_log?"
        assert all(seen), f"load_log 在锁外被调用了: {seen}"
    finally:
        eng._store.load_log = real
        eng._store = original_store


if __name__ == "__main__":
    test_load_log_has_no_cache()
    test_save_log_overwrites_whole_file()
    test_stale_copy_loaded_outside_the_lock_loses_data()
    test_load_inside_the_lock_keeps_both_writers()
    test_lifeloop_loads_log_while_holding_the_lock()
    test_pulse_loads_log_while_holding_the_lock()
    print("log transaction(锁内加载 · 不丢数据) all tests passed")
