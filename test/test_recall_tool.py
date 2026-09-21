"""character/tools 的 recall 工具自测:四态路由 + 日期元数据 + 灰区。

这个工具 2026-09-21 从 `test/recall_probe.py` 毕业,搬家时一字未改。
所以这里盯的是**搬家有没有搬坏**:

  ① 四态有没有混(故障说成"没这回事"会出事;没找着说成"想不起来"会像天天失忆)
  ② 日期抽取是不是**元数据路由**(不能进向量),猜错日期有没有兜底
  ③ 灰区不许硬判(实测同一件事换措辞分数从 0.42 掉到 0.36,硬判会误杀正确答案)
  ④ 两条**不许被"清理"掉的**文案:desc 里那句能力边界、结果侧那句别补细节

用假嵌入器:本文件要验的是**路由与措辞**,不是检索质量 —— 那由 test/recall_bench.py 管。
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from character.tools import (  # noqa: E402
    _BOUNDARY_RECALL, _GREY_TOP, R_DEGRADED, R_DOWN, R_EMPTY, R_OK,
    RECALL_DESC, RECALL_USAGE, extract_day_range, make_recall_tool,
    strip_time_words,
)
from core.memory import MemoryRow  # noqa: E402

_NOW = time.mktime((2026, 9, 21, 15, 0, 0, 0, 0, -1))


def _t(month, day):
    return time.mktime((2026, month, day, 12, 0, 0, 0, 0, -1))


class FakeEmb:
    """向量表:文本 → 单位向量;`Q:` 前缀是 query 侧。

    向量都取 [cos, sin] 形式,所以**余弦 = 两个向量的点积**,可以直接指定。
    """

    name, dim = "fake", 2

    def __init__(self, table):
        self.table = table

    def passage(self, text):
        return self.table.get(text, [0.0, 1.0])

    def query(self, text):
        return self.table.get("Q:" + text, [1.0, 0.0])


def _row(n, text, t, kind="life", role="self"):
    return MemoryRow(turn=n, kind=kind, role=role, time=t, text=text)


def _build(rows, table, **kw):
    """返回 (tool, 结构化 runner)。"""
    from core.memory import MemoryIndex
    idx = MemoryIndex(rows, FakeEmb(table))
    tool = make_recall_tool(lambda: idx, now_fn=lambda: _NOW, **kw)
    return tool, tool.run_structured


def _one_row(cos, **kw):
    """一行、余弦可控的索引 —— 用 [cos, sin] 让点积正好等于 cos。"""
    import math
    rows = [_row(1, "甲甲甲甲", _t(9, 20))]
    table = {"甲甲甲甲": [1.0, 0.0], "Q:丙丙丙丙": [cos, math.sqrt(1 - cos * cos)]}
    return _build(rows, table, **kw)


# ============================================================
# 1. 四态
# ============================================================

def test_ok_state_lists_hits_and_appends_the_boundary():
    tool, run = _one_row(0.9)
    r = run({"query": "丙丙丙丙"})
    assert r["state"] == R_OK and r["why"] == ""
    assert len(r["items"]) == 1
    text = tool.func({"query": "丙丙丙丙"})
    assert text.startswith("[回忆] 与「丙丙丙丙」相关,共 1 条:")
    assert text.endswith(_BOUNDARY_RECALL), "结果侧的边界句掉了"


def test_boundary_sentence_can_be_switched_off_for_labs():
    """实验台要干净结果时关掉它;产品默认开。"""
    tool, _ = _one_row(0.9)
    assert _BOUNDARY_RECALL in tool.func({"query": "丙丙丙丙"})
    tool2, _ = _one_row(0.9, boundary=False)
    assert _BOUNDARY_RECALL not in tool2.func({"query": "丙丙丙丙"})


def test_empty_when_the_scope_has_nothing_says_it_is_really_absent():
    """`empty` = **服务正常、确实没有** → 不许说成"想不起来",也不许编。

    这条是四态里最容易写错的一态:说成"我想不起来了"会让她像天天失忆。
    """
    rows = [_row(1, "甲甲甲甲", _t(9, 20), kind="life")]
    tool, run = _build(rows, {"甲甲甲甲": [1.0, 0.0], "Q:丙丙丙丙": [1.0, 0.0]})
    r = run({"query": "丙丙丙丙", "scope": "talk"})   # talk 里一条都没有
    assert r["state"] == R_EMPTY
    text = tool.func({"query": "丙丙丙丙", "scope": "talk"})
    assert "确实没有相关的事" in text
    assert "别为它编内容" in text


def test_grey_zone_does_not_hard_judge():
    """余弦落在灰区 → 给结果但**标明不确定**,不许硬判成"有"或"没有"。

    实测:同一件事换种措辞分数能从 0.42 掉到 0.36,硬判会误杀正确答案。
    """
    tool, run = _one_row(_GREY_TOP - 0.05)
    r = run({"query": "丙丙丙丙"})
    assert r["state"] == R_OK, "灰区不该判成 empty"
    assert r["scores"][0] < _GREY_TOP
    text = tool.func({"query": "丙丙丙丙"})
    assert "不是很确定" in text
    assert "别当成准的讲" in text


def test_no_query_degrades_to_time_order_and_says_why():
    """query 空 = **降级落点**(不是模型可选模式):给时间序 + 说清下面是什么。"""
    rows = [_row(1, "甲甲甲甲", _t(9, 18)), _row(2, "乙乙乙乙", _t(9, 20))]
    tool, run = _build(rows, {"甲甲甲甲": [1.0, 0.0], "乙乙乙乙": [1.0, 0.0]})
    r = run({"query": ""})
    assert (r["state"], r["why"]) == (R_DEGRADED, "no_query")
    assert [it["text"] for it in r["items"]] == ["乙乙乙乙", "甲甲甲甲"], "没按时间倒序"
    text = tool.func({"query": ""})
    assert "你没说清找什么" in text
    assert "退而求其次" in text


def test_bad_scope_widens_to_all_and_says_so():
    """范围不认识 → **放宽到全部,但必须说出来**(静默放宽 = 她以为查窄了)。"""
    rows = [_row(1, "甲甲甲甲", _t(9, 20), kind="life"),
            _row(2, "乙乙乙乙", _t(9, 21), kind="talk")]
    tool, run = _build(rows, {"甲甲甲甲": [1.0, 0.0], "乙乙乙乙": [1.0, 0.0],
                              "Q:丙丙丙丙": [1.0, 0.0]})
    r = run({"query": "丙丙丙丙", "scope": "昨天的事"})
    assert (r["state"], r["why"]) == (R_DEGRADED, "bad_scope")
    assert r["applied_scope"] == "all", "不认识的范围必须放宽到 all"
    assert "你给的范围我不认识" in tool.func({"query": "丙丙丙丙", "scope": "昨天的事"})


def test_semantic_route_down_degrades_instead_of_silently_going_keyword_only():
    """没有嵌入器 = 语义路塌了 → 退时间序 + 老实说 degraded。

    ⚠️ 这条是**防静默退化**:`MemoryIndex.search` 对嵌入器异常是吞掉的(只剩
       关键词),不先判就会变成"她记性变差",根本查不到原因。
    """
    from core.memory import MemoryIndex
    rows = [_row(1, "甲甲甲甲", _t(9, 20))]
    idx = MemoryIndex(rows, None)          # 没有语义路
    tool = make_recall_tool(lambda: idx, now_fn=lambda: _NOW)
    r = tool.run_structured({"query": "丙丙丙丙"})
    assert (r["state"], r["why"]) == (R_DEGRADED, "semantic_failed")
    assert r["items"], "退化也要给东西(时间序)"
    assert "检索这一步没跑成" in tool.func({"query": "丙丙丙丙"})


def test_no_index_at_all_says_down():
    """引擎没起来 → `down`:示弱,别硬猜(不是"确实没有")。"""
    tool = make_recall_tool(lambda: None, now_fn=lambda: _NOW)
    r = tool.run_structured({"query": "丙丙丙丙"})
    assert (r["state"], r["why"]) == (R_DOWN, "no_index")
    text = tool.func({"query": "丙丙丙丙"})
    assert "检索没能跑起来" in text and "别硬猜" in text


def test_min_score_can_turn_a_weak_hit_into_empty():
    """阈值那道口子还在(产品默认不设,见 `_FLOOR`)。"""
    rows = [_row(1, "甲甲甲甲", _t(9, 20))]
    import math
    table = {"甲甲甲甲": [1.0, 0.0], "Q:丙丙丙丙": [0.1, math.sqrt(1 - 0.01)]}
    _, run = _build(rows, table)
    assert run({"query": "丙丙丙丙"})["state"] == R_OK, "不设阈值就该给出来"
    _, run2 = _build(rows, table, min_score=0.25)
    assert run2({"query": "丙丙丙丙"})["state"] == R_EMPTY


# ============================================================
# 2. 日期:元数据路由 + 猜错日期的兜底
# ============================================================

def test_extract_day_range_reads_the_forms_it_claims_to_read():
    start, end, hit = extract_day_range("9月16日我做了什么", _NOW)
    assert (time.localtime(start).tm_mon, time.localtime(start).tm_mday) == (9, 16)
    assert end - start == 86400
    assert hit == "9月16日"
    # 相对词
    s2, _e2, h2 = extract_day_range("昨天我做了什么", _NOW)
    assert time.localtime(s2).tm_mday == 20 and h2 == "昨天"
    # 不认粗粒度的说法(猜错了比不猜更糟)
    assert extract_day_range("三年前我做了什么", _NOW) is None
    assert extract_day_range("上周我做了什么", _NOW) is None


def test_future_dates_roll_back_a_year():
    """跨年边界:12 月问"1月5日" → 指的是今年 1 月,不是明年。"""
    dec = time.mktime((2026, 12, 20, 12, 0, 0, 0, 0, -1))
    start, _end, _hit = extract_day_range("1月5日我做了什么", dec)
    assert time.localtime(start).tm_year == 2026


def test_time_words_are_stripped_before_the_vector():
    """库里没有日期 —— 留着只会污染向量(它是元数据过滤,不是相似度)。"""
    assert strip_time_words("9月16日我做了什么", "9月16日") == "我做了什么"
    assert strip_time_words("周三我说了什么", "周三") == "我说了什么"
    assert strip_time_words("9月16日", "9月16日") == "9月16日", "剥空了要退回原句"


def test_day_filter_narrows_to_that_day():
    rows = [_row(1, "甲甲甲甲", _t(9, 16)), _row(2, "乙乙乙乙", _t(9, 17))]
    table = {"甲甲甲甲": [1.0, 0.0], "乙乙乙乙": [1.0, 0.0],
             "Q:我说了什么": [1.0, 0.0]}
    _, run = _build(rows, table)
    r = run({"query": "9月17日我说了什么"})
    assert r["day"] == "9月17日"
    assert [it["text"] for it in r["items"]] == ["乙乙乙乙"], "日期过滤没生效"
    assert r["sem_q"] == "我说了什么", "日期词没从向量里剥掉"


def test_day_empty_attaches_nearby_and_warns_it_is_not_that_day():
    """她说了一个日期、那天没记录 → 说"那天没记" + 把**前后的事**附上。

    实测她写的日期很可能是**自己猜的**(问"你之前说早上在写日记",她写「今天早上」)。
    加提示词规则治不好(带日期规则 2/24、不带也 2/24)—— 所以治在机制上:
    让猜错日期的代价从「查不到」降到「查到的不是那天的」。
    """
    rows = [_row(1, "甲甲甲甲", _t(9, 16)), _row(2, "乙乙乙乙", _t(9, 17))]
    table = {"甲甲甲甲": [1.0, 0.0], "乙乙乙乙": [1.0, 0.0],
             "Q:我说了什么": [1.0, 0.0]}
    tool, run = _build(rows, table)
    r = run({"query": "9月19日我说了什么"})     # 那天一条都没有
    assert (r["state"], r["why"]) == (R_EMPTY, "day_empty")
    assert r["items"] == []
    assert r["nearby"], "猜错日期时要把前后的事附上"
    text = tool.func({"query": "9月19日我说了什么"})
    assert "没留下什么记录" in text
    assert "不是「你那天什么都没做」" in text, "抽样生成 ≠ 那天没发生,这句不能掉"
    assert "**不是那天的**" in text, "附上来的东西必须说清不是那天的"
    assert "别硬编那天做了什么" in text


# ============================================================
# 3. schema 与两条不许被"清理"掉的文案
# ============================================================

def test_limit_is_not_in_the_schema_and_floors_at_one():
    """`limit` 是**产品侧算的**,不开口子 —— 开了模型就能填 -1(返回全库)。"""
    tool, _run = _one_row(0.9)
    assert set(tool.parameters["properties"]) == {"query", "scope"}
    assert tool.parameters["required"] == ["query"]
    assert set(tool.parameters["properties"]["scope"]["enum"]) == {"all", "life", "talk"}

    rows = [_row(i, "甲甲甲甲", _t(9, 10 + i)) for i in range(3)]
    table = {"甲甲甲甲": [1.0, 0.0], "Q:丙丙丙丙": [1.0, 0.0]}
    _, run = _build(rows, table, limit=0)
    assert len(run({"query": "丙丙丙丙"})["items"]) == 1, "limit 下限是 1"


def test_usage_reaches_SYSTEM_through_the_composer():
    """两条通道缺一不可(`character/tools.py` 开头那句"瞎半只眼"):

      description -> 进 tools[] schema,教"调用格式"
      usage       -> 经 make_usage_section 进 SYSTEM,教"怎么用得好"

    而 `core/composer.py` 的 `make_usage_section()` 只读 **Tool 上**的 usage。
    我搬过来时先漏挂了一次 —— 漏了**不会报错**,SYSTEM 里就是没有这段,
    症状是触发率与参数合规率一起掉,看不出来是接线问题。
    """
    tool, _run = _one_row(0.9)
    assert tool.usage == RECALL_USAGE, "usage 没挂在 Tool 上 → SYSTEM 里不会有它"

    from core.composer import SystemComposer, make_usage_section
    from core.tools import ToolRegistry
    c = SystemComposer()
    c.register(make_usage_section(ToolRegistry([tool])))
    text = c.compose()
    assert "recall" in text
    assert "天气" in text, "用法段没把那条边界/例子带进去"


def test_desc_keeps_the_capability_boundary_sentence():
    """⛔ 实测:从 desc 拿掉「它只有过去…」→ 天气那格 24/25 掉到 19/25。

    **能删的只有重复,不是内容。** 谁想"精简"这段文案,先看 character/tools.py
    里那段 N=25 的高 reps 数。
    """
    assert "它只有过去" in RECALL_DESC
    assert "看不见现在的事,也看不见外面的事" in RECALL_DESC
    assert "天气" in RECALL_USAGE, "那个具体的词就是起作用的东西,不许删"
    assert "9月16日我做了什么" in RECALL_USAGE, "例子掉了约束就松(实测差 14 倍)"


def run_all():
    fns = [
        test_ok_state_lists_hits_and_appends_the_boundary,
        test_boundary_sentence_can_be_switched_off_for_labs,
        test_empty_when_the_scope_has_nothing_says_it_is_really_absent,
        test_grey_zone_does_not_hard_judge,
        test_no_query_degrades_to_time_order_and_says_why,
        test_bad_scope_widens_to_all_and_says_so,
        test_semantic_route_down_degrades_instead_of_silently_going_keyword_only,
        test_no_index_at_all_says_down,
        test_min_score_can_turn_a_weak_hit_into_empty,
        test_extract_day_range_reads_the_forms_it_claims_to_read,
        test_future_dates_roll_back_a_year,
        test_time_words_are_stripped_before_the_vector,
        test_day_filter_narrows_to_that_day,
        test_day_empty_attaches_nearby_and_warns_it_is_not_that_day,
        test_limit_is_not_in_the_schema_and_floors_at_one,
        test_usage_reaches_SYSTEM_through_the_composer,
        test_desc_keeps_the_capability_boundary_sentence,
    ]
    for f in fns:
        f()
    print(f"recall tool all tests passed ({len(fns)} 条)")


if __name__ == "__main__":
    run_all()
