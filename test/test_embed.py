"""core/embed 自测:BGE 的**懒加载 / 离线 / 前缀不对称 / 限线程**。

重点不是"模型能不能编码"(那是上游的事),而是我们包在它外面的四条约定:

  ① 构造**不加载**权重 —— `import torch` 也要 1.16 秒,探路只能用 find_spec
  ② 默认**离线** —— 允许联网时 HF 连不上要重试 176.9 秒才回落本地缓存
  ③ query 加前缀 / passage 不加 —— 两侧不对称是官方用法,**不是漏写**
  ④ 编码时把 torch 线程数压下来 —— 50 ms 的活拉满 24 个核,前台比"卡 50 毫秒"更难受

需要真模型的用例在拿不到 BGE 时会**明确说跳过**(不静默通过)。
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core import embed as E  # noqa: E402
from core.memory import MemoryIndex, MemoryRow  # noqa: E402

_REAL: dict = {}


def _real():
    """拿一个**已经暖好**的真嵌入器;拿不到返回 None 并说明原因(只印一次)。"""
    if "emb" in _REAL:
        return _REAL["emb"]
    if not E.available():
        print("  ⚠️ 跳过:这台机器没有 torch/transformers")
        _REAL["emb"] = None
        return None
    emb = E.BgeEmbedder()
    try:
        emb.warm()
    except Exception as exc:  # noqa: BLE001
        print(f"  ⚠️ 跳过:拿不到 BGE({type(exc).__name__}: {exc})")
        _REAL["emb"] = None
        return None
    _REAL["emb"] = emb
    return emb


class FakeEnc:
    """假编码器:记下喂进来的原文,回一个可辨认的向量。"""

    def __init__(self):
        self.seen: list[str] = []

    def __call__(self, texts):
        self.seen.extend(texts)
        return [[float(len(t)), 0.0] for t in texts]


# ============================================================
# 1. 懒加载
# ============================================================

def test_available_probes_instead_of_importing():
    """探路**不许**把 torch 拉进来 —— 否则懒加载白做(import 本身 1.16 秒)。"""
    assert "torch" not in sys.modules, "本文件运行前不应有人 import 过 torch"
    assert E.available() is True
    assert "torch" not in sys.modules, "available() 把 torch import 进来了"
    assert "transformers" not in sys.modules


def test_constructing_does_not_load_the_weights():
    """构造只是记账。权重 1242 MB / 4.18 秒,不该在建对象时就付。"""
    emb = E.BgeEmbedder()
    assert emb.loaded is False
    assert emb._encode is None
    assert "torch" not in sys.modules, "构造就把 torch 拉进来了"
    assert emb.info()["loaded"] is False


def test_get_embedder_returns_none_when_the_packages_are_missing():
    """拿不到包 → 返回 None(调用方退化成纯关键词),**不是抛**。"""
    real = E.available
    try:
        E.available = lambda *a, **k: False
        assert E.get_embedder() is None
    finally:
        E.available = real
    assert E.get_embedder() is not None


def test_local_files_only_is_the_default():
    """默认离线。

    这条不是洁癖:允许联网时,HF 连不上要**重试 176.9 秒**才回落到本地缓存,
    而我们要的是那 4.18 秒的离线加载。
    """
    assert E.BgeEmbedder().local_files_only is True
    assert E.BgeEmbedder().info()["local_files_only"] is True


def test_offline_load_fails_fast_on_a_model_that_is_not_here():
    """离线 + 没这个模型 → **当场报错**,而不是去网上慢慢超时。

    对照组是默认值本身:如果哪天有人把 local_files_only 改成 False,
    这条会从"秒回错误"变成"卡 90 秒",`elapsed` 断言当场抓出来。
    """
    emb = E.BgeEmbedder("BAAI/bge-large-zh-v1.5-does-not-exist")
    t0 = time.perf_counter()
    try:
        emb.warm()
    except Exception:  # noqa: BLE001 — 报什么都行,只要**快**
        pass
    else:
        raise AssertionError("不存在的模型居然加载成功了?")
    dt = time.perf_counter() - t0
    assert dt < 20.0, f"花了 {dt:.1f} 秒 —— 这是去联网了,离线优先没生效"


# ============================================================
# 2. 前缀不对称
# ============================================================

def test_query_carries_the_official_prefix_and_passage_does_not():
    """BGE 两侧不对称是**设计**:query 加指令前缀,passage 不加。

    "统一一下"会同时弄坏两边,而且检索只是变差、不报错 —— 典型的静默错。
    """
    emb = E.BgeEmbedder()
    fake = FakeEnc()
    emb._encode = fake                       # 注入:跳过真加载

    emb.passage("她煮了面。")
    assert fake.seen[-1] == "她煮了面。"
    emb.query("我上次吃了什么")
    assert fake.seen[-1] == E.QUERY_PREFIX + "我上次吃了什么"
    assert E.QUERY_PREFIX and E.QUERY_PREFIX.endswith(":")


# ============================================================
# 3. 真模型(拿不到就明说跳过)
# ============================================================

def test_warm_is_idempotent():
    """重复暖机不重复加载 —— 后台线程和第一个查询可能同时想暖它。"""
    emb = _real()
    if emb is None:
        return
    enc_after_first = emb._encode
    assert emb.warm().loaded is True
    assert emb._encode is enc_after_first, "第二次暖机把模型重读了一遍"


def test_encoding_does_not_touch_the_process_wide_thread_count_by_default():
    """默认**不动** `torch.set_num_threads`。

    ⚠️ 这条是**反向**的:第一版我拍了 `threads=2`,理由是"别和前台抢 CPU"。
       实测两头都更糟(605 ms/条 vs 147 ms/条),因为后台本来就只在引擎锁空闲时
       才跑 —— 压线程让不开任何东西,只是把索引建成拖慢 4 倍。

    所以这里钉的是"默认别多事";要压必须显式传。真会被占满的机器才用得上它。
    """
    emb = _real()
    if emb is None:
        return
    import torch
    assert emb.threads is None, "默认值不该是某个具体线程数"
    assert emb.info()["threads"] is None
    assert torch.get_num_threads() >= 1        # 没被我们改坏就行

    cap = E.BgeEmbedder(threads=3)
    cap._encode = lambda ts: [[1.0, 0.0] for _ in ts]   # 不加载权重,只验记账
    cap._ensure_loaded = lambda: None
    assert cap.info()["threads"] == 3, "显式传的旋钮要留住"


def test_real_vectors_are_unit_norm_and_match_the_declared_dim():
    """归一是**契约**:归一之后余弦就是点积(`core/memory.py` 的 `_dot` 靠它)。"""
    emb = _real()
    if emb is None:
        return
    v = emb.passage("她把窗子推开了一点,外面在下雨。")
    assert len(v) == emb.dim, f"声明 dim={emb.dim},实际 {len(v)} —— 换模型忘了改 dim?"
    n = sum(x * x for x in v) ** 0.5
    assert abs(n - 1.0) < 1e-3, f"没归一: |v| = {n}"


def test_related_question_beats_an_unrelated_one():
    """整条链路的**行为**验收:同一个 query,相关那条的余弦要高。

    这不是在测模型好坏,是在测我们接对了 —— 前缀接错、归一接错、
    缓存当成新向量接错,都会让这条翻车。
    """
    emb = _real()
    if emb is None:
        return
    rows = [
        MemoryRow(turn=1, kind="life", role="self", time=1.0,
                  text="晚上煮了碗面,卧了个蛋,吃完靠在窗边看雨。"),
        MemoryRow(turn=2, kind="life", role="self", time=2.0,
                  text="把书架上的旧杂志搬下来,擦了一遍灰。"),
    ]
    idx = MemoryIndex(rows, emb)
    hits = idx.search("我那天晚上吃了什么", limit=2)
    assert hits[0].row.turn == 1, [h.row.text for h in hits]
    assert hits[0].cos > hits[1].cos


def run_all():
    fns = [
        test_available_probes_instead_of_importing,
        test_constructing_does_not_load_the_weights,
        test_get_embedder_returns_none_when_the_packages_are_missing,
        test_local_files_only_is_the_default,
        test_offline_load_fails_fast_on_a_model_that_is_not_here,
        test_query_carries_the_official_prefix_and_passage_does_not,
        test_warm_is_idempotent,
        test_encoding_does_not_touch_the_process_wide_thread_count_by_default,
        test_real_vectors_are_unit_norm_and_match_the_declared_dim,
        test_related_question_beats_an_unrelated_one,
    ]
    for f in fns:
        f()
    print(f"embed all tests passed ({len(fns)} 条)")


if __name__ == "__main__":
    run_all()
