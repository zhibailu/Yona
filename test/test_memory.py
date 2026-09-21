"""core/memory 自测:记忆行派生 + 混合检索。"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.memory import (  # noqa: E402
    BM25, W_SPARSE, MemoryIndex, MemoryRow, rows_from_events,
)
from core.session_log import SessionLog  # noqa: E402


class Ev:
    """最小事件壳(内核不挑来源,只要有 type/data/time)。"""

    def __init__(self, type_, data, t=0.0):
        self.type, self.data, self.time = type_, data, t


def _self_turn(n, text, t, *, backfill=False):
    """一轮独处:自走轮与补写轮**形状相同**(都是 source=self)。"""
    ev = [Ev("turn/start", {"turn": n, "source": "self"}, t),
          Ev("step/start", {"turn": n, "step": 1}, t),
          Ev("assistant/message", {"turn": n, "content": [{"type": "text", "text": text}]}, t),
          Ev("turn/end", {"turn": n}, t)]
    if backfill:
        ev.insert(0, Ev("turn/start", {"turn": n, "source": "self", "time_cursor": t}, t))
        ev.pop(1)
    return ev


def _user_turn(n, said, reply, t_u, t_a):
    return [Ev("turn/start", {"turn": n, "source": "user"}, t_u),
            Ev("user/message", {"turn": n, "source": "user",
                                "content": [{"type": "text", "text": said}]}, t_u),
            Ev("assistant/message", {"turn": n,
                                     "content": [{"type": "text", "text": reply}]}, t_a),
            Ev("turn/end", {"turn": n}, t_a)]


class FakeEmb:
    """可控向量:按文本给向量,方便断言"闸门取最高余弦"这类性质。"""
    name, dim = "fake", 2

    def __init__(self, table):
        self.table = table

    def passage(self, text):
        return self.table.get(text, [0.0, 0.0])

    def query(self, text):
        return self.table.get("Q:" + text, [1.0, 0.0])


# ============================================================
# 1. 行派生
# ============================================================

def test_life_rows_come_from_every_self_turn():
    """**两种独处轮都是生活事件** —— 判据看 source,不看是哪台机器产的。"""
    ev = _self_turn(1, "泡了杯茶站在窗边。", 100.0)
    ev += _self_turn(2, "补写出来的那一夜。", 200.0, backfill=True)
    rows = rows_from_events(ev)
    assert [r.kind for r in rows] == ["life", "life"], rows
    assert [r.role for r in rows] == ["self", "self"]
    assert [r.turn for r in rows] == [1, 2]
    assert rows[0].text == "泡了杯茶站在窗边。"


def test_talk_is_one_row_per_turn_and_takes_the_earliest_time():
    """talk **一轮一条**(两句话合在一起),时刻取**最先**的那个。"""
    rows = rows_from_events(_user_turn(7, "睡了没啊", "还没,正发呆呢。", 100.0, 160.0))
    assert len(rows) == 1, rows
    r = rows[0]
    assert r.kind == "talk" and r.role == "both"
    assert r.time == 100.0, "时刻必须是这一轮最先的那个,不是最后一段"
    assert r.text == "睡了没啊\n还没,正发呆呢。"


def test_talk_keeps_a_half_when_one_side_is_missing():
    """被打断的轮别丢:只有一边也出一条。"""
    ev = [Ev("turn/start", {"turn": 9, "source": "user"}, 5.0),
          Ev("user/message", {"turn": 9, "source": "user",
                              "content": [{"type": "text", "text": "只说了一半"}]}, 5.0),
          Ev("turn/end", {"turn": 9}, 5.0)]
    rows = rows_from_events(ev)
    assert len(rows) == 1 and rows[0].role == "user", rows
    assert rows[0].text == "只说了一半"


def test_rows_strip_copied_prefix_on_read():
    """日志里的脏数据在**读侧**剥掉(日志原文不动)。"""
    dirty = "user不在时，角色产生的生活事件：09-11 20:41\n（从外面回来…）"
    rows = rows_from_events(_self_turn(1, dirty, 100.0),
                            strip_prefix="user不在时，角色产生的生活事件：{time}")
    assert rows[0].text.startswith("（从外面回来"), rows[0].text
    assert "09-11 20:41" not in rows[0].text


def test_rows_ignore_non_turn_events():
    ev = _self_turn(1, "一件事。", 100.0) + [Ev("tool/call", {"name": "x"}, 1.0)]
    assert len(rows_from_events(ev)) == 1


# ============================================================
# 2. 稀疏路
# ============================================================

def test_bm25_prefers_exact_terms():
    docs = ["在便利店买了罐热牛奶", "在食堂随便打了份番茄炒蛋", "把围巾洗了晾在阳台"]
    bm = BM25(docs)
    s = [bm.score("热牛奶", i) for i in range(3)]
    assert s[0] > s[1] and s[0] > s[2], s
    # 完全不重合 → 0(这是稀疏路唯一干净的地方,但它**不足以当闸门**,见顶层注释)
    assert bm.score("量子力学", 0) == 0.0


# ============================================================
# 3. 索引与路由
# ============================================================

def _three_rows():
    return [
        MemoryRow(turn=1, kind="life", role="self", time=100.0, text="甲"),
        MemoryRow(turn=2, kind="talk", role="both", time=200.0, text="乙"),
        MemoryRow(turn=3, kind="life", role="self", time=300.0, text="丙"),
    ]


def test_scope_and_turn_are_routing_never_scoring():
    """**路由不能改分数** —— 这是"路由与打分正交"的可断言形式。"""
    emb = FakeEmb({"甲": [1.0, 0.0], "乙": [0.5, 0.5], "丙": [0.0, 1.0],
                   "Q:甲": [1.0, 0.0]})
    idx = MemoryIndex(_three_rows(), emb)
    all_hits = {h.row.text: h.score for h in idx.search("甲", limit=3)}
    life_hits = {h.row.text: h.score for h in idx.search("甲", limit=3, scope="life")}
    assert set(life_hits) == {"甲", "丙"}, life_hits        # 路由筛掉了 talk
    for k, v in life_hits.items():
        assert abs(v - all_hits[k]) < 1e-9, (k, v, all_hits[k])  # 分数一字不变

    only2 = idx.search("甲", limit=3, turn=2)
    assert [h.row.text for h in only2] == ["乙"], only2


def test_limit_floor_is_one():
    idx = MemoryIndex(_three_rows(), None)
    assert len(idx.search("甲", limit=0)) == 1
    assert len(idx.search("甲", limit=-5)) == 1


def test_top_cos_is_max_cosine_over_all_rows_not_the_fusion_winner():
    """闸门问的是"有没有",所以它要**最高余弦**,不是融合分第一那条。

    构造:甲 余弦 0.95(略低)但字面完全命中(融合分赢);乙 余弦 1.0(最高)。
    第二条 assert 是**阳性对照** —— 没有它,这条测试可能在"两者恰好相同"时空过。
    """
    rows = [MemoryRow(turn=1, kind="life", role="self", time=1.0, text="热牛奶" * 10),
            MemoryRow(turn=2, kind="life", role="self", time=2.0, text="别的")]
    emb = FakeEmb({"热牛奶" * 10: [0.95, 0.312], "别的": [1.0, 0.0],
                   "Q:热牛奶": [1.0, 0.0]})
    idx = MemoryIndex(rows, emb)
    hits = idx.search("热牛奶", limit=len(rows))
    max_cos = max(h.cos for h in hits)
    assert hits[0].row.text == "热牛奶" * 10, "融合分第一应该是字面命中的那条"
    assert abs(idx.top_cos("热牛奶") - max_cos) < 1e-6, (idx.top_cos("热牛奶"), max_cos)
    assert hits[0].cos < max_cos, "构造失败:融合第一恰好也是最高余弦,这条测试是空的"


def test_no_embedder_degrades_to_sparse_only():
    """语义路不可用时仍能按字面检索(不许整条链炸掉)。"""
    idx = MemoryIndex([MemoryRow(turn=1, kind="life", role="self", time=1.0,
                                 text="在便利店买了罐热牛奶")], None)
    hits = idx.search("热牛奶", limit=2)
    assert len(hits) == 1 and hits[0].cos == 0.0
    assert hits[0].score > 0.0


def test_empty_index_returns_nothing():
    idx = MemoryIndex([], None)
    assert idx.search("随便") == []
    assert idx.top_cos("随便") == 0.0


def run_all():
    fns = [
        test_life_rows_come_from_every_self_turn,
        test_talk_is_one_row_per_turn_and_takes_the_earliest_time,
        test_talk_keeps_a_half_when_one_side_is_missing,
        test_rows_strip_copied_prefix_on_read,
        test_rows_ignore_non_turn_events,
        test_bm25_prefers_exact_terms,
        test_scope_and_turn_are_routing_never_scoring,
        test_limit_floor_is_one,
        test_top_cos_is_max_cosine_over_all_rows_not_the_fusion_winner,
        test_no_embedder_degrades_to_sparse_only,
        test_empty_index_returns_nothing,
    ]
    for f in fns:
        f()
    print(f"memory all tests passed ({len(fns)} 条)")


if __name__ == "__main__":
    run_all()
