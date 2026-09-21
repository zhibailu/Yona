"""Yona 内核 · 记忆索引缓存(memory_cache)

把 `core/memory.py` 的**记忆行 + 向量**落在 sqlite 里,并在后台"吃性能剩饭"补齐向量。

## 为什么它是缓存、不是数据(用户 2026-09-21 拍板)

    chat.log = 本体(日志即真相)
    索引     = 派生物  →  删了能重建、**不需要备份**

所以它住**顶层 `cache/`**,与 `data/` 分开 —— 塞进 data/ 会让人误以为它是要备份的
数据,而它删掉只损失一点重建时间。代价:卡片被删时它的缓存文件得跟着删
(`prune_file`),否则 cache/ 会攒下已归档卡的残骸。

## 什么时候建(用户 2026-09-21 定,数字是实测)

    会话建出来 → 跟着建表            实测 18.4 ms(含建文件);表已存在再开 1.1 ms
    新增日志   → 行**立刻入库、vec 留空**
    后台线程   → **第一件事:暖机**    实测 4.18 s(权重 1242 MB,进程峰值 1894 MB)
               → 然后空闲时一条一条补 实测 147 ms/条(走本模块的真实路径,不压线程)

**暖机排在最前面,不是"等到要用"**(用户 2026-09-21 纠正):懒加载**省不到**那
1.9 GB —— 只要补过一条向量模型就常驻,早晚都要付,差的只是这 4.2 秒落在哪一刻。
晚暖有可能落在"她刚问完、正等回答"那一刻(她问"我上次是不是说过…"而索引还空着
→ 查询自己就得先把模型读进来)。判定与实测见 `core/embed.py` 模块头。

**"空闲"的判据是"本机没有别的 CPU 活在跑",不是"没有 LLM"。**
LLM 是远端 HTTP(`core/openai_compat.py`),等它的整段时间里本机 CPU 是空的 ——
那恰恰是补向量的好时段,不该被排除。真正要躲的是"她刚按下发送、正在裁窗+装配+
写日志"那几十毫秒。本模块用 `guard`(注入 `engine._lock`,**非阻塞** try-acquire)
表达"让路":后台**只在没有人跑 turn 的时候补**,而那一刻前台本来就不在等。

⚠️ 所以**别再用"压 torch 线程数"去加强让路** —— 实测两头都更糟(605 ms/条 对
147 ms/条),因为后台跑的时候前台根本不在跑。见 `core/embed.py`。

## 断点续跑是白送的

"有没有向量"= 一列 `vec IS NULL` 就查得到,所以**队列状态不用另存**:
进程半路死掉、缓存删一半、换模型重来,重启后 `fill_one()` 自己接着干。

## ⚠️ 两条静默错,都靠本模块堵

1. **文案变了必须作废向量** —— `(kind, turn)` 是主键,`text` 一变就把 `vec` 打回
   NULL。不作废就会"按旧内容的向量排新内容的名次",且**查不出来**。
2. **日志里删掉的消息必须从库里移走** —— `store.delete_messages_from` 会截断日志,
   缓存不同步就会把她**已经删掉的话**继续翻出来。`sync()` 默认 `prune=True`。

日志原文一个字不动(日志即真相),这里只做投影 + 派生。
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from array import array
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from .memory import MemoryRow

_SCHEMA = """
CREATE TABLE IF NOT EXISTS memory(
    kind     TEXT    NOT NULL,   -- 'life' | 'talk'
    turn     INTEGER NOT NULL,   -- 轮号
    role     TEXT    NOT NULL,   -- 路由量,不进 embedding
    time     REAL    NOT NULL,   -- 记忆自身时刻(日期路由用)
    text     TEXT    NOT NULL,   -- 被索引的正文
    vec      BLOB,               -- NULL = 还没补向量
    vec_kind TEXT,               -- 'dense' | 'sparse'
    dirty_at REAL,               -- 进队时刻(NULL = 已补齐);只在"欠账"时有值
    PRIMARY KEY(kind, turn)
)
"""


def _pack(vec: Any) -> tuple[bytes | None, str | None]:
    """向量 → (blob, kind)。

    稠密(float list)→ float32 原始字节;稀疏(bigram 占位 embedder 的 dict)→ JSON。
    ⚠️ float32 走 `array` 的**本机字节序** —— 这缓存本来就只服务同一台机器,换机器
       重建一次即可(不要为了跨端可移植把它变成 base64,那是拿 4/3 的体积换一个
       用不上的性质)。
    """
    if vec is None:
        return None, None
    if isinstance(vec, dict):
        return json.dumps({str(k): float(v) for k, v in vec.items()},
                          separators=(",", ":")).encode("utf-8"), "sparse"
    return array("f", (float(x) for x in vec)).tobytes(), "dense"


def _unpack(blob: bytes | None, kind: str | None) -> Any:
    if blob is None:
        return None
    if kind == "sparse":
        return {k: float(v) for k, v in json.loads(blob.decode("utf-8")).items()}
    a = array("f")
    a.frombytes(blob)
    return list(a)


@dataclass
class SyncReport:
    new: int       # 首次入库(欠向量)
    changed: int   # 正文变了 → 旧向量已作废
    same: int      # 原样
    dropped: int   # 日志里没有了 → 从库里移走

    @property
    def pending(self) -> int:
        return self.new + self.changed


class MemoryCache:
    """一张卡的记忆索引缓存(sqlite)。

    embedder: 任何有 `.passage(text)` / `.query(text)` 的对象
              (`core/memory.py` 的 `MemoryIndex` 认同一份协议)。
              给 None = 只存行不补向量,检索退化成纯关键词。
    guard:    一个 `acquire(blocking=False)` / `release()` 的对象(通常
              `server.app.engine._lock`)。补向量前试着拿,**拿不到就让路**。
    max_wait: 欠账等超过这么多秒就**插一脚**(blocking 拿 guard 也要补)。
              没有它,连续对话时 guard 一直不空 → 索引会**饿死**。
    """

    def __init__(
        self,
        path: str | Path,
        *,
        embedder: Any = None,
        guard: Any = None,
        poll: float = 2.0,
        max_wait: float = 120.0,
        retry_backoff: float = 30.0,
        max_warm_attempts: int = 3,
    ) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.embedder = embedder
        self.guard = guard
        self.poll = poll
        self.max_wait = max_wait
        self.retry_backoff = retry_backoff
        self.max_warm_attempts = max_warm_attempts
        # check_same_thread=False:同一 step 的 ≥2 个工具调用跑在
        # ThreadPoolExecutor 里(core/loop.py:428),连接要能被别的线程用。
        # 串行化交给下面这把锁,不交给 sqlite。
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._db_lock = threading.RLock()
        self._running = False
        self._thread: threading.Thread | None = None
        self._wake = threading.Event()
        self._done = 0
        self._errors = 0
        self._last_error: str | None = None
        self._warmed = False
        self._warm_attempts = 0
        self._embedder_dead = False

    # ---------- 建表(会话建出来时调,毫秒级) ----------

    def ensure(self) -> None:
        """建表 + 建目录。**幂等**,重复调只花几毫秒(实测 1.1 ms)。"""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db_lock:
            self._conn.execute(_SCHEMA)
            self._conn.commit()

    @staticmethod
    def path_for(cache_root: str | Path, session_id: str) -> Path:
        """这张卡的缓存文件在哪。**纯路径计算:不建目录、不建文件、不连接。**

        拆出来是因为"删一张卡的缓存"不该**先把缓存建出来** —— 建了再删等于
        白起一个后台线程、白写一个 sqlite 文件和它的目录。

        ⚠️ session_id 会被当成文件名,**不许**把它直接拼进来就完事:
           路径穿越(`../`)会让缓存写到 cache/ 外面去 —— 而删缓存那一步
           会照着这个路径 unlink,那就删到不该删的东西了。
        """
        if not session_id or "/" in session_id or "\\" in session_id or ".." in session_id:
            raise ValueError(f"session_id 不能当文件名用: {session_id!r}")
        return Path(cache_root) / "sessions" / f"{session_id}.sqlite3"

    @staticmethod
    def for_session(cache_root: str | Path, session_id: str, **kw) -> "MemoryCache":
        """一个会话一个缓存文件:`<cache_root>/sessions/<sid>.sqlite3`。

        与 `data/sessions/<sid>/` 同构 —— 一个会话 = 一个档案袋,缓存跟着它走。
        `**kw` 原样转给 `__init__`(embedder / guard / poll / max_wait …)——
        装配处(engine)只该知道"哪张卡",不该自己拼路径。
        """
        return MemoryCache(MemoryCache.path_for(cache_root, session_id), **kw)

    # ---------- 同步(新增日志时调) ----------

    def sync(self, rows: Sequence[MemoryRow], *, prune: bool = True) -> SyncReport:
        """把记忆行同步进库。新行/改过的行**一律 vec 留空**,等后台补。

        prune=True(默认):日志里已经没有的 `(kind, turn)` 从库里删掉 ——
        她删掉一条消息后,缓存不许继续把它翻出来。
        """
        now = time.time()
        rep = SyncReport(0, 0, 0, 0)
        with self._db_lock:
            have = {(k, t): (txt, role, tm) for k, t, txt, role, tm in
                    self._conn.execute(
                        "SELECT kind, turn, text, role, time FROM memory")}
            seen: set[tuple[str, int]] = set()
            for r in rows:
                key = (r.kind, r.turn)
                seen.add(key)
                old = have.get(key)
                if old is None:
                    rep.new += 1
                    self._conn.execute(
                        "INSERT INTO memory(kind,turn,role,time,text,vec,vec_kind,dirty_at)"
                        " VALUES(?,?,?,?,?,NULL,NULL,?)",
                        (r.kind, r.turn, r.role, float(r.time), r.text, now),
                    )
                elif old[0] != r.text:
                    rep.changed += 1
                    # 正文变了 → 旧向量作废,否则按旧内容排新内容的名次(静默错)
                    self._conn.execute(
                        "UPDATE memory SET role=?, time=?, text=?,"
                        " vec=NULL, vec_kind=NULL, dirty_at=? WHERE kind=? AND turn=?",
                        (r.role, float(r.time), r.text, now, r.kind, r.turn),
                    )
                else:
                    rep.same += 1
                    # ⚠️ 只有真变了才写。这个函数**每轮都会跑一遍**(日志永远在长),
                    #    无条件 UPDATE 会让每次同步都是 O(卡的全部历史)——
                    #    卡活得越久越慢,而它慢在的地方是她那一轮里。
                    if old[1] != r.role or old[2] != float(r.time):
                        self._conn.execute(
                            "UPDATE memory SET role=?, time=? WHERE kind=? AND turn=?",
                            (r.role, float(r.time), r.kind, r.turn),
                        )
            if prune:
                gone = [k for k in have if k not in seen]
                for k in gone:
                    self._conn.execute("DELETE FROM memory WHERE kind=? AND turn=?", k)
                rep.dropped = len(gone)
            self._conn.commit()
        if rep.pending:
            self._wake.set()   # 有欠账,叫醒后台
        return rep

    # ---------- 读回(检索时调) ----------

    def load(self) -> tuple[list[MemoryRow], list[Any]]:
        """返回 `(rows, vecs)`,**按下标对齐**;没补上向量的位置是 None。

        交给 `MemoryIndex(rows, embedder, vecs=vecs)` —— 已补齐的行不再重算。
        """
        with self._db_lock:
            cur = self._conn.execute(
                "SELECT kind, turn, role, time, text, vec, vec_kind"
                " FROM memory ORDER BY time, turn"
            )
            got = cur.fetchall()
        rows, vecs = [], []
        for kind, turn, role, t, text, blob, vk in got:
            rows.append(MemoryRow(turn=turn, kind=kind, role=role,
                                  time=t, text=text))
            vecs.append(_unpack(blob, vk))
        return rows, vecs

    def counts(self) -> dict:
        with self._db_lock:
            total, pending = self._conn.execute(
                "SELECT COUNT(*), COUNT(*)-COUNT(vec) FROM memory"
            ).fetchone()
        return {"total": total or 0, "pending": pending or 0}

    # ---------- 后台补向量(吃性能剩饭) ----------

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="yona-memory-index"
        )
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._running = False
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None

    def close(self) -> None:
        self.stop()
        with self._db_lock:
            self._conn.close()

    def _loop(self) -> None:
        while self._running:
            # ⚠️ **先暖机,再补账**(用户 2026-09-21 纠正)。
            # 放在这个线程里是有意的:暖机和补账**串行同队**,所以不存在两条
            # 路径同时抢着加载模型;而"等到第一次要用才暖"会把 4.18 秒挪到
            # "她刚问完、正等回答"那一刻 —— 那是全流程里最坏的位置。
            if not self._warmed:
                self.warm()
                if self._embedder_dead:
                    return          # 拿不到模型 → 这个线程没活可干了
                if not self._warmed:
                    self._wake.wait(self.poll)
                    self._wake.clear()
                    continue
            did = False
            try:
                did = self.fill_one()
            except Exception as exc:  # noqa: BLE001 — 后台线程不许因单条失败而死
                self._errors += 1
                self._last_error = f"{type(exc).__name__}: {exc}"
            if did:
                continue          # 有活就接着干(一次一条,但不用等 poll)
            # 没活 / 让路了 → 睡到被叫醒或 poll 到期
            self._wake.wait(self.poll)
            self._wake.clear()

    def warm(self) -> bool:
        """把模型读进来(实测 4.18 s / 进程峰值 1894 MB)。**幂等**。

        为什么值得早花:懒加载**省不到那 1.9 GB** —— 只要补过一条向量模型就常驻,
        早晚都要付,差的只是这 4.2 秒落在哪一刻(判定见 core/embed.py 模块头)。

        拿不到模型 = 降级成纯关键词,**并把原因留在 status()** —— 静默退化会让
        "她记性变差"看起来像模型的问题,查不到真因。
        """
        if self._warmed:
            return not self._embedder_dead
        if self.embedder is None:
            self._warmed = True
            return True
        fn = getattr(self.embedder, "warm", None)
        if fn is None:               # 测试里的假嵌入器没有 warm,不算失败
            self._warmed = True
            return True
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            self._warm_attempts += 1
            self._errors += 1
            self._last_error = f"warm: {type(exc).__name__}: {exc}"
            if self._warm_attempts >= self.max_warm_attempts:
                # 拿不到模型通常是**配置问题**(没装包 / 没下模型 / 离线无缓存),
                # 一直重试只是刷日志。
                self._embedder_dead = True
                self._warmed = True
                return False
            return False
        self._warmed = True
        return True

    def _peek(self) -> tuple[str, int, str, float] | None:
        with self._db_lock:
            return self._conn.execute(
                "SELECT kind, turn, text, dirty_at FROM memory"
                " WHERE vec IS NULL ORDER BY dirty_at, time LIMIT 1"
            ).fetchone()

    def fill_one(self, *, force: bool = False) -> bool:
        """补一条向量。返回 True = 真干了一件。

        **一次只有一条**(44.7 ms)。绝不批量:批 16 虽然总吞吐更好(24.3 ms/条),
        但那意味着持有 guard 的长度是 16 倍 —— 前台那一下就卡住了。

        force=True:不等让路,直接补(测试与"醒来就追平"用)。
        """
        if self.embedder is None or self._embedder_dead:
            return False
        row = self._peek()
        if row is None:
            return False
        kind, turn, text, dirty_at = row
        if not self._take_slot(dirty_at, force):
            return False
        try:
            try:
                vec = self.embedder.passage(text)
            except Exception as exc:  # noqa: BLE001
                # 编码失败:**行留着**,退避一会儿再试 —— 删行会变成"这条记忆
                # 永远查不到"且看不出来。
                self._errors += 1
                self._last_error = f"{type(exc).__name__}: {exc}"
                with self._db_lock:
                    self._conn.execute(
                        "UPDATE memory SET dirty_at=? WHERE kind=? AND turn=?",
                        (time.time() + self.retry_backoff, kind, turn),
                    )
                    self._conn.commit()
                return False
            blob, vk = _pack(vec)
            with self._db_lock:
                self._conn.execute(
                    "UPDATE memory SET vec=?, vec_kind=?, dirty_at=NULL"
                    " WHERE kind=? AND turn=?",
                    (blob, vk, kind, turn),
                )
                self._conn.commit()
            self._done += 1
            return True
        finally:
            self._release_slot()

    def _take_slot(self, dirty_at: float | None, force: bool) -> bool:
        """拿到"可以占用 CPU"的名额。

        让路(非阻塞拿 guard)优先;**欠账等超过 max_wait 就插一脚**(阻塞拿)——
        连续对话时 guard 一直不空,没有这条兜底索引会饿死,而饿死的症状是
        "新聊的事搜不到",她只会觉得她记性变差了。
        """
        if self.guard is None:
            return True
        overdue = dirty_at is not None and (time.time() - dirty_at) >= self.max_wait
        return bool(self.guard.acquire(blocking=(force or overdue)))

    def _release_slot(self) -> None:
        if self.guard is not None:
            self.guard.release()

    def status(self) -> dict:
        c = self.counts()
        return {
            "path": str(self.path),
            "total": c["total"],
            "pending": c["pending"],
            "done": self._done,
            "errors": self._errors,
            "last_error": self._last_error,
            "running": self._running,
            "has_embedder": self.embedder is not None,
            "warmed": self._warmed,
            "embedder_dead": self._embedder_dead,
            "warm_attempts": self._warm_attempts,
        }

    # ---------- 卡片被删时 ----------

    def prune_file(self) -> bool:
        """删掉这张卡的缓存文件(卡片被删/归档时调)。返回是否真删了。

        缓存是可重建的派生物,所以卡片一走它就**该走** —— 否则 cache/ 会攒下
        已归档卡的残骸,而残骸里是她和那个角色的全部对话。
        """
        self.close()
        if self.path.exists():
            self.path.unlink()
            return True
        return False
