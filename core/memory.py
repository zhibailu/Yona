"""Yona 内核 · 记忆检索(memory)

把事件日志摊成**可检索的记忆行**,并提供一路混合检索。
纯内核:零 IO、不 import server / character(分层规则 STRUCTURE §1)。

## 行定义(用户 2026-09-21 拍板)

    life = **生活事件** —— 她独处时做的/说的。
           自走轮与补写轮同属:**触发时机不同,产物是同一个东西**,
           所以判据看 `source == "self"`,不看是哪台机器产的。
    talk = **聊过的一轮** —— **一轮一条**:她的话和她的回答合在一起;
           时刻取这一轮里**最先**出现的那个(不是最后一段)。
    role = 'self' | 'user' | 'both' —— **只是路由量,不进 embedding**。

## 打分(2026-09-21 实测,w=0.2 见 test/recall_bench.py)

    排序 = dense 余弦  +  W_SPARSE × BM25(两路都**绝对刻度**)

    ⛔ **绝不按 query 内最大值归一** —— 归一化会把"一条都没命中"的 query
       的最高分也抬成 1.0,直接毁掉绝对刻度的可比性(实测踩过两次:
       `max` 归一的融合 top1 32/44,改成绝对刻度 37/44)。
    ⛔ **闸门("确实没有")不用融合分** —— 融合分随 query 类型漂移;
       只有 dense 余弦是可标定的那一个。融合分**只负责排序**。

实测(315 条语料 / 21 主题 / 911 query,`test/recall_bench.py --corpus big`):

    dense 单独            top1 748/899 (83.2%)
    dense + 0.2·BM25      top1 838/881 (95.1%)   ← 本模块的实现
    RRF(dense, BM25)      top1 822/899 (91.4%)   ← 不用:牺牲自然问法换字面型
    日期路由单向          通用 query 0/12 → 12/12
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

# 融合权重与 BM25 的刻度常数 —— 都是**实测出来的**,别凭手感改
W_SPARSE = 0.2      # 稀疏路权重(0.2 是扫描里的平台起点)
BM25_K = 4.0        # s/(s+K):把无上界的 BM25 压到 0~1,且**保留绝对刻度**

LIFE = "life"
TALK = "talk"
SCOPES = ("all", LIFE, TALK)


@dataclass(frozen=True)
class MemoryRow:
    """一条可检索的记忆。"""
    turn: int
    kind: str          # 'life' | 'talk'
    role: str          # 'self' | 'user' | 'both'(路由量,不进 embedding)
    time: float
    text: str


# ============================================================
# 1. 从事件日志派生记忆行
# ============================================================

def _blocks_text(content: Any) -> str:
    """把 content 块列表压成纯文本(只要 text 块)。"""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    out = []
    for b in content:
        if isinstance(b, dict) and b.get("type") == "text":
            out.append(str(b.get("text", "")))
    return "\n".join(x for x in out if x)


def _turns(events: Iterable[Any]) -> list[dict]:
    """按轮归拢:每轮的 source / 真人消息 / 她的消息(与投影层同款口径)。

    ## surface 遮蔽必须跳(2026-09-21 实测踩到)

    用户在 UI 上删消息走的是 `log.shadow()`(`server/store.py:345`)——
    **日志原文一律留着**(日志即真相),只追加一条 `surface/shadow` 注解,
    由投影层跳过。所以这里也必须跳,否则后果不是"多一条结果",
    而是**把她删掉的话翻出来念给他听**。

    ⚠️ 遮蔽集合**从这批事件自己算**,不靠调用方传 —— 传参就会有人忘,
       而忘了的后果是静默的(记忆看着好好的)。
    编辑走的是同一条路:`update_message_content` = 遮蔽原文 + 追加修正消息,
    所以跳过原文之后,`_turns` 的"后者覆盖前者"自然取到修正后的正文。

    ⚠️ **编辑/压缩留下的"替身消息"必须锚回原文的轮次**(2026-09-21 补)。
       `update_message_content`(`server/store.py:350`)的形状是:
       遮蔽原文 + 追加一条 `source="user-edit"` 且带 `replaces={start,end}` 的消息,
       而那条追加消息**不带 turn**。投影层是靠 `replaces.start` 把它摆回原文位置的
       (`core/session_log.py:431-434`)。
       不锚回去的后果:**原文被遮蔽、替身因为没有 turn 被丢掉** → 那一半记忆
       凭空消失,而且**看不出来**(行还在,只是少了一边)。
       —— 只"过滤掉 replace 标记"是不够的:过滤只会让它继续消失。

    ⚠️ `assistant/message` 不带 source —— 靠**轮号**归属(与 core/session_log
    的 `self_turns` 同一个判据)。
    """
    evs = list(events)
    hidden: set[int] = set()
    for e in evs:
        if e.type == "surface/shadow":
            hidden.update(range(e.data["start"], e.data["end"] + 1))
    by_seq = {e.seq: e for e in evs}

    # 只有"用户那半边"认这两种 source。
    # ⏳ `compact`(压缩摘要)是**派生物**,不是她说的话 —— 它的记忆语义没拍板,
    #    先不进记忆。compact 落地时这里要一起定(否则被压缩掉的那段会只剩她半边)。
    _USER_SOURCES = ("user", "user-edit")

    def _origin_of(e) -> tuple[int | None, float | None]:
        """替身消息 → (原文的轮次, 原文的时刻);普通事件 → (自己的, None)。"""
        rep = e.data.get("replaces")
        if isinstance(rep, dict) and isinstance(rep.get("start"), int):
            origin = by_seq.get(rep["start"])
            if origin is not None:
                t = origin.data.get("turn")
                return (t if isinstance(t, int) else None), origin.time
        return None, None

    acc: dict[int, dict] = {}
    for e in evs:
        if e.seq in hidden:
            continue
        t = e.data.get("turn")
        at = e.time
        if not isinstance(t, int):
            t, origin_time = _origin_of(e)
            # 时刻也跟着原文走:她编辑一条旧消息,不该让那条记忆的日期跳到"现在"
            if origin_time is not None:
                at = origin_time
        if not isinstance(t, int):
            continue
        d = acc.setdefault(t, {"turn": t, "source": "?", "user": None, "assistant": None})
        if e.type == "turn/start":
            d["source"] = e.data.get("source", "?")
        elif e.type == "user/message":
            if e.data.get("source", "user") in _USER_SOURCES:
                d["user"] = {"text": _blocks_text(e.data.get("content")), "time": at}
        elif e.type == "assistant/message":
            txt = _blocks_text(e.data.get("content"))
            if txt.strip():
                d["assistant"] = {"text": txt, "time": at}
    return [acc[k] for k in sorted(acc)]


def rows_from_events(events: Iterable[Any], *, strip_prefix: str = "") -> list[MemoryRow]:
    """事件 → 记忆行。**日志原文一个字不动**(日志即真相),这里只是投影。

    strip_prefix:传内容层那个标记模板时,会把**被模型抄进正文**的那行剥掉
    (见 `core.session_log.strip_copied_prefix`)—— 脏数据在日志里,治在读侧。
    """
    from .session_log import strip_copied_prefix

    rows: list[MemoryRow] = []
    for t in _turns(events):
        if t["source"] == "self" and t["assistant"]:
            txt = t["assistant"]["text"]
            if strip_prefix:
                txt = strip_copied_prefix(txt, strip_prefix)
            rows.append(MemoryRow(turn=t["turn"], kind=LIFE, role="self",
                                  time=t["assistant"]["time"], text=txt))
        elif t["source"] == "user":
            said = t["user"]["text"] if t["user"] else ""
            reply = t["assistant"]["text"] if t["assistant"] else ""
            if not (said or reply):
                continue
            stamps = [x["time"] for x in (t["user"], t["assistant"]) if x]
            rows.append(MemoryRow(
                turn=t["turn"], kind=TALK,
                role="both" if (said and reply) else ("user" if said else "self"),
                # 时刻 = 这一轮里**最先**出现的那个(用户拍板:
                # "talk 记忆算该 step 最先的那个")—— 不许取最后一段
                time=min(stamps),
                text="\n".join(x for x in (said, reply) if x),
            ))
    rows.sort(key=lambda r: r.time)
    return rows


# ============================================================
# 2. 稀疏路(BM25,字级 bigram —— 中文不引分词依赖)
# ============================================================

def _bigrams(text: str) -> list[str]:
    t = re.sub(r"\s+", "", text)
    if len(t) < 2:
        return [t] if t else []
    return [t[i:i + 2] for i in range(len(t) - 1)]


class BM25:
    """纯 Python BM25(k1/b 取常规值)。

    ⚠️ **原始 BM25 分没有上界**、且随 query 长度变化 —— 所以它**不适合当闸门**,
       只适合进融合分排序(实测:负例的 BM25 最高 11.96,比很多正例还高)。
    """

    def __init__(self, docs: Sequence[str], k1: float = 1.5, b: float = 0.75) -> None:
        self.k1, self.b = k1, b
        self.tokens = [_bigrams(d) for d in docs]
        self.n = len(self.tokens)
        self.avgdl = (sum(len(t) for t in self.tokens) / self.n) if self.n else 1.0
        self.df: dict[str, int] = {}
        for toks in self.tokens:
            for w in set(toks):
                self.df[w] = self.df.get(w, 0) + 1

    def score(self, query: str, i: int) -> float:
        toks, doc = _bigrams(query), self.tokens[i]
        dl = len(doc) or 1
        tf: dict[str, int] = {}
        for w in doc:
            tf[w] = tf.get(w, 0) + 1
        s = 0.0
        for w in toks:
            if w not in tf:
                continue
            n = self.df.get(w, 0)
            idf = math.log(1 + (self.n - n + 0.5) / (n + 0.5))
            s += idf * (tf[w] * (self.k1 + 1)) / (
                tf[w] + self.k1 * (1 - self.b + self.b * dl / self.avgdl))
        return s


def _dot(a: Any, b: Any) -> float:
    """点积:兼容 list(稠密)与 dict(稀疏 bigram 占位 embedder)。

    `None` = "这条还没向量"(缓存里 `vec IS NULL`,见 core/memory_cache.py)——
    算 0,不代表不相关,只代表**还不知道**。
    """
    if a is None or b is None:
        return 0.0
    if isinstance(a, dict) or isinstance(b, dict):
        if not isinstance(a, dict) or not isinstance(b, dict):
            return 0.0
        if len(a) > len(b):
            a, b = b, a
        return sum(v * b.get(k, 0.0) for k, v in a.items())
    return float(sum(x * y for x, y in zip(a, b)))


# ============================================================
# 3. 索引 + 检索
# ============================================================

@dataclass(frozen=True)
class Hit:
    row: MemoryRow
    score: float      # 融合分(用于**排序**)
    cos: float        # dense 余弦(用于**闸门** —— 只有它是可标定的)


class MemoryIndex:
    """记忆索引:一路 dense + 一路 BM25,**融合分排序 / 余弦判有没有**。

    embedder: 任何有 `.passage(text)` / `.query(text)` 的对象
              (与 test/recall_probe.py 的 embedder 同款协议)。
              `.passage` 拿不到时索引仍然建得起来,只是没有语义路。
    """

    def __init__(self, rows: Sequence[MemoryRow], embedder: Any = None,
                 vecs: Sequence[Any] | None = None) -> None:
        """
        vecs: 预计算好的向量,与 `rows` **按下标对齐**(`core/memory_cache.py`
              从 sqlite 读回来的那份)。给了就不再重算 —— 这是缓存唯一的意义,
              实测 BGE 逐条 44.7 ms,315 条重算一次 14 秒。
              个别位置给 `None` = 那条还没补上,就地现算(有 embedder 的话)。
        """
        self.rows = list(rows)
        self.embedder = embedder
        self._vecs: list[Any] = []
        for i, r in enumerate(self.rows):
            v = vecs[i] if vecs is not None and i < len(vecs) else None
            if v is None and embedder is not None:
                v = embedder.passage(r.text)
            self._vecs.append(v)
        self._bm = BM25([r.text for r in self.rows])

    # ---------- 检索 ----------
    @property
    def has_semantic_route(self) -> bool:
        """语义路可用吗(有嵌入器 **且** 至少有一条向量)。

        给工具判"语义路塌了没"用 —— 塌了就退时间序并老实说 degraded,
        而不是**悄无声息地只剩关键词**(那样她只会觉得"她记性变差")。
        """
        return self.embedder is not None and any(v is not None for v in self._vecs)

    def search(
        self,
        query: str,
        *,
        limit: int = 2,
        scope: str | None = None,
        turn: int | None = None,
        day_range: tuple[float, float] | None = None,
    ) -> list[Hit]:
        """返回 top-`limit`。

        ⚠️ `scope` / `turn` / `day_range` 走的是**路由**(硬筛),不参与打分 ——
           路由与分数正交,这样加路由不会动分数的刻度。
        `limit` 永远是产品侧算的,**不进 schema**;下限 1。
        """
        limit = max(1, int(limit))
        idx = [i for i, r in enumerate(self.rows)
               if (scope in (None, "all") or r.kind == scope)
               and (turn is None or r.turn == turn)
               and (day_range is None or day_range[0] <= r.time <= day_range[1])]
        if not idx:
            return []
        q_cos = [0.0] * len(self.rows)
        if self.embedder is not None and self._vecs:
            try:
                qv = self.embedder.query(query)
                q_cos = [_dot(qv, v) for v in self._vecs]
            except Exception:  # noqa: BLE001
                q_cos = [0.0] * len(self.rows)
        hits = []
        for i in idx:
            b = self._bm.score(query, i)
            sparse = b / (b + BM25_K)          # ← 保留绝对刻度,不按 query 内最大值归一
            hits.append(Hit(row=self.rows[i],
                            score=q_cos[i] + W_SPARSE * sparse,
                            cos=q_cos[i]))
        hits.sort(key=lambda h: -h.score)
        return hits[:limit]

    def top_cos(self, query: str, *, scope: str | None = None) -> float:
        """只问"有没有":返回**最高余弦**(闸门专用,与融合分无关)。

        ⚠️ 不能拿 `search(limit=1)` 的结果 —— 那是**融合分**第一的那条,
           它的余弦未必最高(实测两路排序不一致是常态)。
        """
        idx = [i for i, r in enumerate(self.rows)
               if scope in (None, "all") or r.kind == scope]
        if not idx or self.embedder is None or not self._vecs:
            return 0.0
        try:
            qv = self.embedder.query(query)
        except Exception:  # noqa: BLE001
            return 0.0
        return max(_dot(qv, self._vecs[i]) for i in idx)
