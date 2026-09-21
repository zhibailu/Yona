"""core/memory_cache 自测:sqlite 索引缓存的**欠账语义**。

这里盯的都不是"能不能跑",而是四条**静默错**:
  ① 正文变了但向量没作废 → 按旧内容排新内容的名次
  ② 日志里删掉的话还留在缓存里 → 把她删掉的话继续翻出来
  ③ 批量补向量 → 前台那一下就卡住(所以"一次一条"是**契约**,不是实现细节)
  ④ 欠账等太久没人管 → 索引饿死,症状是"她记性变差"

以及在跑之前先把边界订死:缓存是**派生物**,删了必须能重建(断点续跑)。
"""

import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.memory import MemoryIndex, MemoryRow  # noqa: E402
from core.memory_cache import MemoryCache, _pack, _unpack  # noqa: E402


def _row(n, text, *, kind="talk", role="both", t=None):
    return MemoryRow(turn=n, kind=kind, role=role,
                     time=float(t if t is not None else n), text=text)


class CountingEmb:
    """数着调用次数的嵌入器 —— 用来证明"没重算""只补了一条"。"""

    def __init__(self, *, boom_on=(), vec=None):
        self.passages: list[str] = []
        self.queries: list[str] = []
        self.boom_on = set(boom_on)
        self.vec = vec if vec is not None else [1.0, 0.0, 0.0, 0.0]

    def passage(self, text):
        self.passages.append(text)
        if text in self.boom_on:
            raise RuntimeError("encoder blew up")
        return list(self.vec)

    def query(self, text):
        self.queries.append(text)
        return list(self.vec)


class RecordingGuard:
    """记录"这次是让路拿(blocking=False)还是插一脚拿(blocking=True)"。"""

    def __init__(self):
        self.calls: list[bool] = []
        self._l = threading.Lock()

    def acquire(self, blocking=True):
        self.calls.append(bool(blocking))
        return self._l.acquire(blocking=blocking)

    def release(self):
        self._l.release()

    def hold(self):
        """测试自己占住名额(不计入 calls —— 数的是**缓存**怎么拿的)。"""
        self._l.acquire()

    def let_go(self):
        self._l.release()


def _tmp_cache(**kw):
    d = tempfile.TemporaryDirectory()
    return MemoryCache(Path(d.name) / "sessions" / "s1.sqlite3", **kw), d


# ============================================================
# 1. 建表 / 同步 —— 欠账怎么记账
# ============================================================

def test_ensure_is_idempotent():
    """会话建出来时调,**重复调不许炸**(她就是会反复重建引擎)。"""
    c, d = _tmp_cache()
    try:
        c.ensure()
        c.ensure()
        c.ensure()
        assert c.counts() == {"total": 0, "pending": 0}
    finally:
        c.close()
        d.cleanup()


def test_new_rows_land_pending_and_carry_no_vector():
    """新行**立刻入库、vec 留空** —— 索引不是"建完才能用",是"先有账再还账"。"""
    c, d = _tmp_cache()
    try:
        c.ensure()
        rep = c.sync([_row(1, "她把窗子推开了一点。"), _row(2, "煮了面。")])
        assert (rep.new, rep.changed, rep.same, rep.dropped) == (2, 0, 0, 0)
        assert c.counts() == {"total": 2, "pending": 2}
        _, vecs = c.load()
        assert vecs == [None, None]
    finally:
        c.close()
        d.cleanup()


def test_unchanged_row_does_not_get_rewritten():
    """同样的行再同步一次 = 原样,**一个字节都不写**。

    这条盯的是成本,不是正确性:`sync()` **每一轮都会跑一遍**(日志永远在长),
    无条件 UPDATE 会让每次同步都是 O(卡的全部历史)—— 卡活得越久越慢,
    而它慢在的地方是她那一轮里。
    """
    c, d = _tmp_cache(embedder=CountingEmb())
    try:
        c.ensure()
        rows = [_row(1, "甲"), _row(2, "乙")]
        c.sync(rows)
        assert c.fill_one(force=True) and c.fill_one(force=True)
        assert c.counts() == {"total": 2, "pending": 0}

        before = c._conn.total_changes          # sqlite 自己数的"写了几行"
        rep = c.sync(rows)
        assert (rep.new, rep.changed, rep.same) == (0, 0, 2)
        assert c._conn.total_changes == before, "原样同步也写了库"
        assert c.counts()["pending"] == 0, "原样同步把向量弄丢了"
    finally:
        c.close()
        d.cleanup()


def test_changed_time_is_written_even_though_the_vector_survives():
    """对照组:时刻变了就得写下去(日期路由读的是它),但**不动向量**。

    没有这条,"不写库"那个优化就可能写成"什么都不写",日期路由静默失效。
    """
    c, d = _tmp_cache(embedder=CountingEmb())
    try:
        c.ensure()
        c.sync([_row(1, "煮了面。", t=100.0)])
        assert c.fill_one(force=True)
        before = c._conn.total_changes
        c.sync([_row(1, "煮了面。", t=999.0)])
        assert c._conn.total_changes > before, "时刻变了却没写下去"
        assert c.load()[0][0].time == 999.0
        assert c.counts()["pending"] == 0, "只改时刻不该作废向量"
    finally:
        c.close()
        d.cleanup()


# ============================================================
# 2. 静默错 ①:正文变了必须作废向量
# ============================================================

def test_text_change_invalidates_the_vector():
    """一条 talk 行会随日志长出来(先只有用户那句,她回了话才补齐)。

    正文一变,旧向量就是**按旧内容算的**,不作废 = 按旧内容排新内容的名次。
    """
    c, d = _tmp_cache(embedder=CountingEmb())
    try:
        c.ensure()
        c.sync([_row(1, "用户问她吃了吗")])
        assert c.fill_one(force=True)
        assert c.counts()["pending"] == 0

        rep = c.sync([_row(1, "用户问她吃了吗\n她说吃了,下的面")])
        assert rep.changed == 1 and rep.new == 0
        assert c.counts()["pending"] == 1, "正文变了却没作废向量"
        assert c.load()[1] == [None]
    finally:
        c.close()
        d.cleanup()


def test_time_change_alone_does_not_invalidate():
    """对照组:只有时刻/路由量变了,向量**不该**作废 —— 向量只认正文。

    没有这条,上面那条就可能被写成"一律作废",白烧编码。
    """
    c, d = _tmp_cache(embedder=CountingEmb())
    try:
        c.ensure()
        c.sync([_row(1, "煮了面。", t=100.0)])
        assert c.fill_one(force=True)
        rep = c.sync([_row(1, "煮了面。", t=999.0)])   # 只改时刻
        assert (rep.changed, rep.same) == (0, 1)
        assert c.counts()["pending"] == 0
        assert c.load()[0][0].time == 999.0, "时刻没跟进"
    finally:
        c.close()
        d.cleanup()


# ============================================================
# 3. 静默错 ②:日志里删掉的话不许还留着
# ============================================================

def test_prune_drops_what_the_log_no_longer_has():
    """她在 UI 上删掉几条消息 → 日志被截断 → 缓存必须跟着删。

    不删的后果不是"多一条结果",是**把她删掉的话翻出来念给她听**。
    """
    c, d = _tmp_cache()
    try:
        c.ensure()
        c.sync([_row(1, "甲"), _row(2, "乙"), _row(3, "丙")])
        rep = c.sync([_row(1, "甲")])
        assert rep.dropped == 2
        assert c.counts()["total"] == 1
        assert [r.turn for r in c.load()[0]] == [1]
    finally:
        c.close()
        d.cleanup()


def test_prune_false_keeps_them():
    """对照组:显式关掉 prune 就不删 —— 证明上面那条是 prune 干的,不是别的。"""
    c, d = _tmp_cache()
    try:
        c.ensure()
        c.sync([_row(1, "甲"), _row(2, "乙")])
        rep = c.sync([_row(1, "甲")], prune=False)
        assert rep.dropped == 0
        assert c.counts()["total"] == 2
    finally:
        c.close()
        d.cleanup()


# ============================================================
# 4. 静默错 ③:一次一条,绝不批量
# ============================================================

def test_fill_one_does_exactly_one_per_call():
    """批 16 的总吞吐更好(24.3 ms/条 vs 44.7),但持有 CPU 名额的长度是 16 倍。

    "一次一条"是**契约**:她按住发送那一刻,前台最多被占 44 ms。
    """
    emb = CountingEmb()
    c, d = _tmp_cache(embedder=emb)
    try:
        c.ensure()
        c.sync([_row(i, f"第{i}条") for i in range(5)])
        for expect in range(5):
            assert c.fill_one(force=True) is True
            assert len(emb.passages) == expect + 1, "一次补了不止一条"
        assert c.fill_one(force=True) is False, "没欠账了还在空转"
        assert len(emb.passages) == 5
    finally:
        c.close()
        d.cleanup()


# ============================================================
# 5. 静默错 ④:让路 vs 饿死
# ============================================================

def test_lets_a_busy_engine_pass():
    """引擎锁被占(前台在跑)→ 这一条**让路**,不编码、不等待。"""
    emb = CountingEmb()
    c, d = _tmp_cache(embedder=emb, max_wait=3600.0)   # 远没到欠账上限
    guard = RecordingGuard()
    c.guard = guard
    try:
        c.ensure()
        c.sync([_row(1, "甲")])
        guard.hold()             # 模拟前台正持锁
        try:
            assert c.fill_one() is False
        finally:
            guard.let_go()
        assert emb.passages == [], "让路了却还是编码了"
        assert guard.calls == [False], "让路必须是非阻塞拿"
        assert c.counts()["pending"] == 1, "让路不该把欠账弄丢"
        # 引擎空了 → 正常补
        assert c.fill_one() is True
        assert guard.calls == [False, False]
    finally:
        c.close()
        d.cleanup()


def test_overdue_debt_cuts_in_instead_of_starving():
    """欠账等过头就**插一脚**(阻塞拿名额)。

    连续对话时引擎锁基本不空;没有这条兜底,后台会一直让路 → 索引饿死,
    而饿死的症状是"新聊的事她搜不到",她只会觉得她记性变差。
    """
    emb = CountingEmb()
    c, d = _tmp_cache(embedder=emb, max_wait=0.0)   # 立刻算欠账过期
    guard = RecordingGuard()
    c.guard = guard
    try:
        c.ensure()
        c.sync([_row(1, "甲")])
        assert c.fill_one() is True
        assert guard.calls == [True], "过期欠账必须阻塞拿名额(插一脚)"
    finally:
        c.close()
        d.cleanup()


# ============================================================
# 6. 缓存是派生物:崩了要能接着干
# ============================================================

def test_resume_after_a_half_finished_backlog():
    """进程半路死掉不该丢活:重启后**只补剩下的**,已补的不重算。

    这条能成立,靠的是"有没有向量"= 一列 `vec IS NULL` —— 队列状态不用另存,
    所以它没有"队列文件写坏了"这种失败模式。
    """
    emb = CountingEmb()
    c, d = _tmp_cache(embedder=emb)
    try:
        c.ensure()
        c.sync([_row(i, f"第{i}条") for i in range(5)])
        assert c.fill_one(force=True) and c.fill_one(force=True)
        assert c.counts() == {"total": 5, "pending": 3}
    finally:
        c.close()

    emb2 = CountingEmb()
    c2 = MemoryCache(c.path, embedder=emb2)
    try:
        rows, vecs = c2.load()
        assert len(rows) == 5
        assert [v is None for v in vecs] == [False, False, True, True, True]
        assert c2.counts()["pending"] == 3
        assert c2.fill_one(force=True) is True
        assert len(emb2.passages) == 1, "已补好的行被重算了"
    finally:
        c2.close()
        d.cleanup()


def test_encode_failure_keeps_the_row_and_backs_off():
    """编码炸了:**行留着**,退避一会儿再试。

    删行的后果是"这条记忆永远查不到,而且看不出来" —— 比慢一点坏得多。
    """
    emb = CountingEmb(boom_on={"毒药"})
    c, d = _tmp_cache(embedder=emb, retry_backoff=3600.0)
    try:
        c.ensure()
        c.sync([_row(1, "毒药")])
        assert c.fill_one(force=True) is False
        assert c.counts() == {"total": 1, "pending": 1}, "失败把行删了"
        assert c.status()["errors"] == 1
        assert "RuntimeError" in c.status()["last_error"]
        # 退避期内不再空转重试(force 只跳过让路,不禁退避排队)
        emb.boom_on.clear()          # 编码器好了,但队还没到
        c._conn.execute("UPDATE memory SET dirty_at=? WHERE turn=1", (time.time() + 3600,))
        c._conn.commit()
        assert c.fill_one(force=True) is True   # 退避到点了照样能干
    finally:
        c.close()
        d.cleanup()


# ============================================================
# 7. 后台线程
# ============================================================

def test_worker_thread_drains_the_backlog():
    """守护线程自己把欠账吃掉 —— 没人催它,她也不该知道它在。"""
    emb = CountingEmb()
    c, d = _tmp_cache(embedder=emb, poll=0.01, max_wait=0.0)
    try:
        c.ensure()
        c.start()
        c.sync([_row(i, f"第{i}条") for i in range(6)])
        deadline = time.time() + 5.0
        while c.counts()["pending"] and time.time() < deadline:
            time.sleep(0.02)
        assert c.counts()["pending"] == 0, f"后台没吃完: {c.status()}"
        assert len(emb.passages) == 6
    finally:
        c.close()
        d.cleanup()


def test_an_embedder_without_warm_is_not_a_failure():
    """没有 warm 方法的嵌入器(测试用的假的)→ 不算失败,直接开始干活。"""
    c, d = _tmp_cache(embedder=CountingEmb())
    try:
        c.ensure()
        assert c.warm() is True
        assert c.status()["warmed"] is True
        assert c.status()["embedder_dead"] is False
    finally:
        c.close()
        d.cleanup()


def test_no_embedder_means_no_work_not_a_crash():
    """拿不到嵌入器 = 只有关键词那一路,不是崩。"""
    c, d = _tmp_cache(embedder=None)
    try:
        c.ensure()
        c.sync([_row(1, "甲")])
        assert c.fill_one(force=True) is False
        assert c.counts()["pending"] == 1
        assert c.status()["has_embedder"] is False
    finally:
        c.close()
        d.cleanup()


# ============================================================
# 7.5 暖机排在补账前面(用户 2026-09-21 纠正)
# ============================================================

def test_worker_warms_before_it_touches_the_backlog():
    """后台线程**第一件事是暖机**,不是"等第一条账要用才加载"。

    反过来的坏处实测过:她问"我上次是不是说过…"而索引还空着 → 查询自己就得先把
    模型读进来(4.18 s),正好落在她等回答那一刻。暖机排在前面,这 4.18 秒就落在
    "引擎刚起来、UI 还在连"那段空白里。
    """
    order: list[str] = []

    class W(CountingEmb):
        def warm(self):
            order.append("warm")
            return self

        def passage(self, text):
            order.append("passage")
            return super().passage(text)

    c, d = _tmp_cache(embedder=W(), poll=0.01, max_wait=0.0)
    try:
        c.ensure()
        c.start()
        c.sync([_row(1, "甲"), _row(2, "乙")])
        deadline = time.time() + 5.0
        while c.counts()["pending"] and time.time() < deadline:
            time.sleep(0.02)
        assert order and order[0] == "warm", f"暖机没排在前面: {order}"
        assert "passage" in order
        assert c.status()["warmed"] is True
    finally:
        c.close()
        d.cleanup()


def test_warm_is_attempted_once_not_per_row():
    """暖机幂等 —— 每补一条就重读一次模型(1242 MB)是灾难。"""
    calls: list[int] = []

    class W(CountingEmb):
        def warm(self):
            calls.append(1)
            return self

    c, d = _tmp_cache(embedder=W())
    try:
        c.ensure()
        c.sync([_row(i, f"第{i}条") for i in range(4)])
        assert c.warm() is True and c.warm() is True
        for _ in range(4):
            c.fill_one(force=True)
        assert len(calls) == 1, f"暖了 {len(calls)} 次"
    finally:
        c.close()
        d.cleanup()


def test_a_dead_embedder_degrades_and_says_why():
    """拿不到模型 → 降级成纯关键词,而且**把原因留在 status 里**。

    静默退化会让"她记性变差"看起来像模型的问题,查不到真因。
    ⚠️ 但**账不能丢**:欠向量的行继续留着,哪天模型回来了还补得上。
    """

    class Bad(CountingEmb):
        def warm(self):
            raise RuntimeError("这台机器上没有这个模型")

    c, d = _tmp_cache(embedder=Bad(), max_warm_attempts=2)
    try:
        c.ensure()
        c.sync([_row(1, "甲")])
        assert c.warm() is False
        assert c.status()["embedder_dead"] is False, "第一次失败就判死,太急"
        assert c.warm() is False
        assert c.status()["embedder_dead"] is True
        assert "这台机器上没有这个模型" in c.status()["last_error"]
        # 死透了就不再空转
        assert c.fill_one(force=True) is False
        assert c.counts() == {"total": 1, "pending": 1}, "降级把欠账弄丢了"
    finally:
        c.close()
        d.cleanup()


def test_a_dead_embedder_stops_the_worker_instead_of_spinning():
    """后台线程拿不到模型就该**安静退出**,不许每 2 秒刷一次错。"""

    class Bad(CountingEmb):
        def warm(self):
            raise RuntimeError("no model")

    c, d = _tmp_cache(embedder=Bad(), poll=0.01, max_warm_attempts=1)
    try:
        c.ensure()
        c.start()
        deadline = time.time() + 3.0
        while c._thread is not None and c._thread.is_alive() and time.time() < deadline:
            time.sleep(0.02)
        assert not c._thread.is_alive(), "拿不到模型还在空转"
        assert c.status()["errors"] == 1, "判死之后不该继续刷错"
    finally:
        c.close()
        d.cleanup()


# ============================================================
# 8. 与 MemoryIndex 接上
# ============================================================

def test_load_feeds_the_index_without_re_encoding():
    """缓存唯一的意义:**已补好的行不再重算**(315 条重算一次 = 14 秒)。"""
    emb = CountingEmb()
    c, d = _tmp_cache(embedder=emb)
    try:
        c.ensure()
        c.sync([_row(1, "煮了面。"), _row(2, "看了会儿书。", kind="life", role="self")])
        assert c.fill_one(force=True) and c.fill_one(force=True)
        emb.passages.clear()

        rows, vecs = c.load()
        idx = MemoryIndex(rows, emb, vecs=vecs)
        assert emb.passages == [], "从缓存读回来的向量被重算了"
        assert len(idx.rows) == 2
        assert idx.search("面", limit=1)
    finally:
        c.close()
        d.cleanup()


def test_index_computes_only_the_rows_still_missing_a_vector():
    """半成品也能用:补好的走缓存,没补的**就地现算**(而不是全库退化成关键词)。"""
    emb = CountingEmb()
    c, d = _tmp_cache(embedder=emb)
    try:
        c.ensure()
        c.sync([_row(1, "甲"), _row(2, "乙")])
        assert c.fill_one(force=True)          # 只补了 1 条
        emb.passages.clear()
        rows, vecs = c.load()
        assert [v is None for v in vecs] == [False, True]
        MemoryIndex(rows, emb, vecs=vecs)
        assert len(emb.passages) == 1, "没补的那条应当现算,且只有它"
    finally:
        c.close()
        d.cleanup()


def test_dot_treats_a_missing_vector_as_unknown():
    """`None` 向量算 0 —— 它意思是"还不知道",不是"不相关"。

    拿到嵌入器时 `None` 会就地现算(上一条测的);**没有嵌入器**时它就只能
    保持 None,此刻 `_dot` 必须容忍,而不是把整次检索炸掉。
    """
    idx = MemoryIndex([_row(1, "甲")], None, vecs=[None])
    assert idx.top_cos("随便") == 0.0
    hits = idx.search("随便")
    assert hits and hits[0].cos == 0.0
    assert hits[0].score == 0.0, "没有语义路时,融合分只能来自关键词那一路"


# ============================================================
# 9. 编码往返 / 边界
# ============================================================

def test_pack_roundtrip_dense_and_sparse():
    dense = [0.5, -0.25, 0.125]
    blob, kind = _pack(dense)
    assert kind == "dense"
    assert _unpack(blob, kind) == dense, "float32 往返丢了精度"

    sparse = {"她": 0.5, "他": 1.0}
    blob, kind = _pack(sparse)
    assert kind == "sparse"
    assert _unpack(blob, kind) == sparse

    assert _pack(None) == (None, None)
    assert _unpack(None, None) is None


def test_session_id_cannot_escape_the_cache_dir():
    """session_id 会被当文件名用 —— 路径穿越必须当场拒绝。

    放过去的话,"删这张卡的缓存"会删到 cache/ 外面的东西。
    """
    root = Path(tempfile.gettempdir()) / "yona-cache-root"
    for bad in ["../evil", "a/b", "a\\b", "", ".."]:
        try:
            MemoryCache.for_session(root, bad)
        except ValueError:
            continue
        raise AssertionError(f"放过了非法 session_id: {bad!r}")
    c = MemoryCache.for_session(root, "fffd3cf53a4942aaaddb24eb604ab1d0")
    assert c.path.name == "fffd3cf53a4942aaaddb24eb604ab1d0.sqlite3"
    assert c.path.parent.name == "sessions"


def test_prune_file_removes_the_cache_of_a_deleted_card():
    """卡片删了/归档了,它的缓存就该走 —— 残骸里是全部对话,不该留。"""
    c, d = _tmp_cache()
    try:
        c.ensure()
        c.sync([_row(1, "甲")])
        path = c.path
        assert path.exists()
        assert c.prune_file() is True
        assert not path.exists()
    finally:
        d.cleanup()


def run_all():
    fns = [
        test_ensure_is_idempotent,
        test_new_rows_land_pending_and_carry_no_vector,
        test_unchanged_row_does_not_get_rewritten,
        test_changed_time_is_written_even_though_the_vector_survives,
        test_text_change_invalidates_the_vector,
        test_time_change_alone_does_not_invalidate,
        test_prune_drops_what_the_log_no_longer_has,
        test_prune_false_keeps_them,
        test_fill_one_does_exactly_one_per_call,
        test_lets_a_busy_engine_pass,
        test_overdue_debt_cuts_in_instead_of_starving,
        test_resume_after_a_half_finished_backlog,
        test_encode_failure_keeps_the_row_and_backs_off,
        test_worker_thread_drains_the_backlog,
        test_an_embedder_without_warm_is_not_a_failure,
        test_worker_warms_before_it_touches_the_backlog,
        test_warm_is_attempted_once_not_per_row,
        test_a_dead_embedder_degrades_and_says_why,
        test_a_dead_embedder_stops_the_worker_instead_of_spinning,
        test_no_embedder_means_no_work_not_a_crash,
        test_load_feeds_the_index_without_re_encoding,
        test_index_computes_only_the_rows_still_missing_a_vector,
        test_dot_treats_a_missing_vector_as_unknown,
        test_pack_roundtrip_dense_and_sparse,
        test_session_id_cannot_escape_the_cache_dir,
        test_prune_file_removes_the_cache_of_a_deleted_card,
    ]
    for f in fns:
        f()
    print(f"memory_cache all tests passed ({len(fns)} 条)")


if __name__ == "__main__":
    run_all()
