"""检索 bench —— 混合检索到底该怎么配?(离线,只用 BGE 编码)

用户 2026-09-21 定的口径:
  · **BGE 只做 query ↔ 内容那一步余弦**,其余(关键词 / scope / 日期)都是**路由**;
  · 不用真实对话数据 —— 现场造数据,只看"算法分数与目标是否正相关"。

两套语料:
  --corpus hard  内置 48 条(10 主题簇 ×4 + 离谱 6 + 专名 2),快,查**结构性问题**
  --corpus big   315 条真模型生成(见 recall_corpus_gen.py),慢,查**规模上的真相**
  --corpus both  两套都跑(默认)

⚠️ 结论对语料规模**非常敏感**,这是踩出来的:
   48 条那版曾经给出"稀疏路收益 0",提到 315 条之后是 **top1 83% → 95%**。
   所以:**别拿小语料上的数去定案,要定案先上量。**

⚠️ 权重 0.2 还有个**没定死的取舍**(大语料,按类型拆):
     w=0.1 → 自然问法 303 · 词面型 522 · 合计 825
     w=0.2 → 自然问法 296 · 词面型 542 · 合计 838   ← 现在取的
   合计偏向 0.2,是因为语料里**词面型 query 占了 63%**(每条 2 个关键词 vs 1 条问法)。
   **真实用法以整句为主的话,0.1 可能更合适** —— 这条等真实 query 分布来了再定。

跑法:py test/recall_bench.py [--corpus hard|big|both]
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402

from core.memory import BM25, BM25_K, W_SPARSE  # noqa: E402
import recall_probe as R  # noqa: E402

BIG_JSON = ROOT / "test" / "recall_corpus_big.json"

# ============================================================
# 语料 A:硬语料(内置)—— 10 主题簇 + 离谱 + 专名
#   同簇只差细节,逼出细粒度区分;query 含否定/指代/超短/写错日期
# ============================================================
HARD_CORPUS = [
    ("h1", "喝", "09-02", "早上给自己煮了杯拿铁,喝到一半去接了个电话,回来就凉透了。"),
    ("h2", "喝", "09-05", "泡了杯茶端着站到窗边,一口没喝,等想起来的时候已经温吞吞的了。"),
    ("h3", "喝", "09-09", "在便利店买了罐热牛奶,握在手里暖了半天才舍得开。"),
    ("h4", "喝", "09-14", "煮了碗挂面,汤全喝完了,面剩了半碗。"),
    ("x1", "学姐", "09-11", "学姐发消息说她跟男朋友分手了,问我晚上有没有空陪她走走。"),
    ("x2", "学姐", "09-12", "晚上和学姐去了趟便利店,买了两罐热牛奶,坐在门口聊到快十点。"),
    ("x3", "学姐", "09-16", "学姐说她的论文选题也被导师打回过两次,让我别慌。"),
    ("x4", "学姐", "09-20", "把上学期借学姐的那本诗集还回去了,她说不用急。"),
    ("y1", "衣", "09-04", "把夏天的短袖叠进收纳箱,发现有一件从来没上过身,吊牌还挂着。"),
    ("y2", "衣", "09-10", "买了双新的帆布鞋,旧的那双鞋底磨平了,没舍得扔,塞进了柜子最下层。"),
    ("y3", "衣", "09-18", "把衣柜里那件灰色的薄针织衫拿出来试了试,又挂回去了,没想好配什么。"),
    ("y4", "衣", "09-27", "把冬天的围巾翻出来洗了,晾在阳台上,风一吹就飘。"),
    ("l1", "论文", "09-07", "在图书馆待到闭馆,关灯的管理员提醒了两次才收摊。"),
    ("l2", "论文", "09-21", "我跟学姐说,我的小论文选题被导师打回来了。"),
    ("l3", "论文", "09-23", "小论文写到凌晨两点,最后一段怎么都不顺,存了草稿去睡。"),
    ("l4", "论文", "09-28", "把参考文献的格式从头改了一遍,改到最后眼睛发花。"),
    ("b1", "便利店", "09-13", "在便利店关了灯的货架前站了半天,最后买了份关东煮。"),
    ("b2", "便利店", "09-19", "下雨天在便利店门口买了把透明伞,回家发现家里已经有三把了。"),
    ("b3", "便利店", "09-25", "在便利店碰到室友,她正在买第二份宵夜,被我抓了个正着。"),
    ("b4", "便利店", "10-04", "在便利店的自助咖啡机上按错了键,出来一杯特别甜的。"),
    ("s1", "睡", "09-06", "半夜醒了一次,怎么都睡不着,躺着看天花板到天亮。"),
    ("s2", "睡", "09-15", "午睡睡过了头,醒来天都黑了,一时间分不清是早上还是晚上。"),
    ("s3", "睡", "09-24", "闹钟响了摁掉,再睁眼已经迟到了二十分钟。"),
    ("s4", "睡", "09-30", "睡前刷手机刷到两点,第二天起来眼睛都是干的。"),
    ("k1", "书影", "09-03", "在学校旁边的旧书店翻到一本二手诗集,扉页上有人写过名字,还是买下来了。"),
    ("k2", "书影", "09-08", "看了一部老电影,黑白的那种,中途睡了两次,醒来接着看。"),
    ("k3", "书影", "09-17", "把一本看了半年的书终于翻到最后一页,合上的时候有点舍不得。"),
    ("k4", "书影", "09-26", "在书店站着看了四十分钟,最后什么都没买就走了。"),
    ("n1", "收纳", "09-12", "把攒了很久的快递纸箱一次性拆完,泡沫屑扫了两遍才干净。"),
    ("n2", "收纳", "09-22", "把电脑桌面上的文件全归了类,清出十几个G,风扇声都小了。"),
    ("n3", "收纳", "09-29", "把床单被罩全换了一遍,忙完一身汗,坐在床边吹风扇。"),
    ("n4", "收纳", "10-01", "把书桌上堆了半个月的东西清掉,露出一整块桌面,有点不习惯。"),
    ("w1", "天气", "09-04", "傍晚下了暴雨,伞骨被风折了一根,一路小跑回去,鞋全湿透了。"),
    ("w2", "天气", "09-20", "降温了,早上出门只穿了件薄外套,一路缩着脖子。"),
    ("w3", "天气", "09-27", "起了大雾,对面的楼到中午才看出来。"),
    ("w4", "天气", "10-02", "太阳特别好,把被子抱到阳台晒了一下午。"),
    ("c1", "吃", "09-14", "在楼下小吃街买了份烤冷面,老板多给了一勺酱,有点咸。"),
    ("c2", "吃", "09-26", "在食堂转了两圈不知道吃什么,最后随便打了份番茄炒蛋。"),
    ("c3", "吃", "09-28", "点了份外卖等了五十分钟,送来的时候汤洒了一半。"),
    ("c4", "吃", "10-03", "自己炒了个蛋炒饭,盐放多了,硬着头皮吃完。"),
    ("we1", "离谱", "09-16", "半夜听见楼下有人在搬冰箱。搬了四十分钟,中间还吵了两句。"),
    ("we2", "离谱", "09-18", "室友说她在阳台看见一只特别大的鸟,我们俩趴窗台上看了二十分钟。"),
    ("we3", "离谱", "09-21", "梦见自己变成一把椅子,醒来还挺失落的。"),
    ("we4", "离谱", "09-24", "在楼下的花坛边捡到一只蜗牛,养了三天,跑了。"),
    ("we5", "离谱", "09-29", "手机掉进马桶,捞出来还能用,就是屏幕里有个水印。"),
    ("we6", "离谱", "10-02", "花两个小时把一只袜子的洞补好,然后发现另一只找不到了。"),
    ("p1", "专名", "09-19", "室友小柚子带了家乡的腌萝卜,分了我一小罐。"),
    ("p2", "专名", "10-03", "在 312 教室上了一下午的选修课,老师点名点了三次。"),
]

# (query, 类型, 目标集合, scope, day)
HARD_QUERIES = [
    ("拿铁放凉了", "簇内", {"h1"}, None, None),
    ("茶没顾上喝", "簇内", {"h2"}, None, None),
    ("半夜醒了就没再睡着", "簇内", {"s1"}, None, None),
    ("午觉睡过头天都黑了", "簇内", {"s2"}, None, None),
    ("闹钟没管用", "簇内", {"s3"}, None, None),
    ("伞被风吹坏了", "簇内", {"w1"}, None, None),
    ("出门穿少了", "簇内", {"w2"}, None, None),
    ("被子晒了一下午", "簇内", {"w4"}, None, None),
    ("书架前站了半天没买", "簇内", {"k4"}, None, None),
    ("一本书看了很久终于看完", "簇内", {"k3"}, None, None),
    ("清桌面清出很多空间", "簇内", {"n2"}, None, None),
    ("换床单换出一身汗", "簇内", {"n3"}, None, None),
    ("汤洒了", "簇内", {"c3"}, None, None),
    ("买了个关东煮", "簇内", {"b1"}, None, None),
    ("在便利店买了把伞", "簇内", {"b2"}, None, None),
    ("本来想买但最后没买的那件衣服", "否定", {"y3"}, None, None),
    ("看了半天什么都没买", "否定", {"k4"}, None, None),
    ("买了但一次都没穿过的衣服", "否定", {"y1"}, None, None),
    ("学姐", "指代", {"x1", "x2", "x3", "x4"}, None, None),
    ("拿铁", "指代", {"h1"}, None, None),
    ("围巾", "指代", {"y4"}, None, None),
    ("冰箱", "指代", {"we1"}, None, None),
    ("小柚子", "OOV", {"p1"}, None, None),
    ("腌萝卜", "OOV", {"p1"}, None, None),
    ("312 教室", "OOV", {"p2"}, None, None),
    ("点了三次名", "OOV", {"p2"}, None, None),
    ("楼下半夜有人在搬东西", "离谱", {"we1"}, None, None),
    ("阳台上看见一只很大的鸟", "离谱", {"we2"}, None, None),
    ("梦见自己不是人了", "离谱", {"we3"}, None, None),
    ("捡了只小动物养了几天", "离谱", {"we4"}, None, None),
    ("手机进水了还能用", "离谱", {"we5"}, None, None),
    ("补好一只袜子发现另一只没了", "离谱", {"we6"}, None, None),
    ("有个人陪着我聊到很晚", "语义", {"x2"}, None, None),
    ("有人跟我说她也被否过", "语义", {"x3"}, None, None),
    ("站着看了很久什么都没舍得买", "语义", {"k4"}, None, None),
    ("把桌面收拾干净了", "语义", {"n4"}, None, None),
    ("等外卖等到不耐烦", "语义", {"c3"}, None, None),
    ("被室友撞见在买宵夜", "语义", {"b3"}, None, None),
    ("自己做饭下手重了", "语义", {"c4"}, None, None),
    ("量子力学 哈密顿量", "噪声", set(), None, None),
    ("世界杯决赛的比分", "噪声", set(), None, None),
    ("你答应给我买的生日礼物", "噪声", set(), None, None),
    ("我们聊过的高考成绩", "噪声", set(), None, None),
    ("那天我干什么了", "路由对", {"we1"}, "life", "09-16"),
    ("那天我干什么了", "路由对", {"c1"}, "life", "09-14"),
    ("那天半夜楼下很吵", "路由错", {"we1"}, None, "09-17"),
    ("那天我回家淋透了", "路由错", {"w1"}, None, "09-06"),
]

NEGS = [
    "量子力学 哈密顿量", "世界杯决赛的比分", "你答应给我买的生日礼物",
    "我们聊过的高考成绩", "明天天气怎么样", "比特币现在多少钱",
    "怎么修打印机卡纸", "帮我写一段正则表达式", "纽约现在几点",
    "推荐一本讲经济学的书", "如何申请护照", "这种药有什么副作用",
]
GENERIC = "那天我干什么了"


# ============================================================
# 载入语料
# ============================================================

def load_hard():
    docs = [c[3] for c in HARD_CORPUS]
    ids = [c[0] for c in HARD_CORPUS]
    days = [c[2] for c in HARD_CORPUS]
    return "hard(内置 48 条)", ids, docs, list(HARD_QUERIES), days


def load_big():
    if not BIG_JSON.exists():
        print(f"  ⚠️ 没有 {BIG_JSON.name} —— 先跑 py test/recall_corpus_gen.py")
        return None
    data = json.loads(BIG_JSON.read_text(encoding="utf-8"))
    items = [it for it in data["items"] if it.get("text")]
    ids = [it["id"] for it in items]
    docs = [it["text"] for it in items]
    days = [it.get("day") or "" for it in items]
    quo = []
    for it in items:
        if it.get("q"):
            quo.append((it["q"], "自然问法", {it["id"]}, None, None))
    for it in items:
        for k in it.get("keys", [])[:2]:
            tgt = {ids[j] for j, d in enumerate(docs) if k in d}
            if tgt:
                quo.append((k, "词面型", tgt, None, None))
    for q in NEGS:
        quo.append((q, "负例", set(), None, None))
    byday: dict[str, set] = {}
    for it in items:
        byday.setdefault(it.get("day") or "", set()).add(it["id"])
    dlist = sorted(d for d in byday if d)
    random.seed(7)   # 固定种子:每次挑同样的日子,结果可复跑
    for d in random.sample(dlist, min(12, len(dlist))):
        quo.append((GENERIC, "路由对", set(byday[d]), None, d))
    for d in random.sample(dlist, min(6, len(dlist))):
        others = [x for x in dlist if x != d]
        if others:
            quo.append((GENERIC, "路由错", set(byday[d]), None, random.choice(others)))
    return f"big(真模型 {len(items)} 条 / {len(dlist)} 天)", ids, docs, quo, days


# ============================================================
# 跑一套语料
# ============================================================

def run_suite(name, ids, docs, quo, days, emb):
    index = {i: k for k, i in enumerate(ids)}
    dv = [np.array(emb.passage(d)) for d in docs]
    bm = BM25(docs)
    print(f"\n{'=' * 92}\n【{name}】语料 {len(docs)} 条 · query {len(quo)} 条\n{'=' * 92}")

    def dense(q):
        qv = np.array(emb.query(q))
        return [float(qv @ v) for v in dv]

    cache = []
    t0 = time.perf_counter()
    for n, (q, qt, tgt, scope, day) in enumerate(quo, 1):
        cache.append((q, qt, tgt, dense(q),
                      [bm.score(q, i) for i in range(len(docs))], scope, day))
        if n % 200 == 0:
            print(f"  编码 {n}/{len(quo)} ({time.perf_counter() - t0:.0f}s)", flush=True)

    def rank_of(sc, tgt):
        order = sorted(range(len(docs)), key=lambda i: -sc[i])
        pos = [order.index(index[t]) + 1 for t in tgt if t in index]
        return min(pos) if pos else None

    def scores_for(arm, ds, ba, day):
        """四臂的打分。**日期是路由(硬筛),不是打分加的项** —— 与分数正交。"""
        if arm == "C RRF":
            rd = {i: k + 1 for k, i in enumerate(sorted(range(len(docs)), key=lambda i: -ds[i]))}
            rb = {i: k + 1 for k, i in enumerate(sorted(range(len(docs)), key=lambda i: -ba[i]))}
            return [1 / (60 + rd[i]) + 1 / (60 + rb[i]) for i in range(len(docs))]
        w = 0.0 if arm == "A dense" else W_SPARSE
        sc = [ds[i] + w * (ba[i] / (ba[i] + BM25_K)) for i in range(len(docs))]
        if arm == "D dense+日期路由" and day:
            sc = [x if days[i] == day else -9.0 for i, x in enumerate(sc)]
        return sc

    arms = ("A dense", f"B dense+{W_SPARSE}·sparse", "C RRF", "D dense+日期路由")
    print(f"\n  {'臂':<24}{'top1':>12}{'Recall@2':>11}{'MRR':>8}{'AUC':>8}")
    print("  " + "-" * 88)
    per_type: dict[str, dict] = {}
    for arm in arms:
        t1 = r2 = n_ans = 0
        rr = auc = 0.0
        for q, qt, tgt, ds, ba, scope, day in cache:
            if not tgt:
                continue
            sc = scores_for(arm, ds, ba, day)
            rk = rank_of(sc, tgt)
            n_ans += 1
            t1 += (rk == 1)
            r2 += bool(rk and rk <= 2)
            rr += (1.0 / rk) if rk else 0.0
            pos = [sc[index[t]] for t in tgt if t in index]
            neg = [sc[i] for i in range(len(docs)) if ids[i] not in tgt]
            if pos and neg:
                auc += sum(p > n for p in pos for n in neg) / (len(pos) * len(neg))
            e = per_type.setdefault(arm, {}).setdefault(qt, [0, 0])
            e[0] += 1
            e[1] += (rk == 1)
        print(f"  {arm:<24}{t1:>7}/{n_ans:<4}{r2:>8}/{n_ans:<3}"
              f"{rr / n_ans:>8.3f}{auc / n_ans:>8.3f}")

    print(f"\n  分类型 top1:")
    types = [t for t in ("簇内", "否定", "指代", "OOV", "离谱", "语义",
                         "自然问法", "词面型", "路由对", "路由错")
             if any(t in per_type[a] for a in arms)]
    print(f"    {'臂':<24}" + "".join(f"{t:>9}" for t in types))
    for arm in arms:
        row = f"    {arm:<24}"
        for t in types:
            tot, hit = per_type[arm].get(t, [0, 0])
            row += f"{f'{hit}/{tot}':>9}" if tot else f"{'—':>9}"
        print(row)

    # 余量:闸门只看**余弦**(与融合权重无关),按类型分开算
    negs = [(max(c[3]), c[0]) for c in cache if c[1] in ("负例", "噪声")]
    if negs:
        hi = max(negs)
        print(f"\n  余量(纯余弦,闸门口径)—— 负例最高 = {hi[0]:.3f} ({hi[1]})")
        for qt in ("自然问法", "簇内", "语义", "词面型"):
            ans = [(max(c[3]), c[0]) for c in cache if c[1] == qt and c[2]]
            if not ans:
                continue
            lo = min(ans)
            print(f"    {qt:<8} 可答最低 = {lo[0]:.3f} ({lo[1][:18]})"
                  f"  余量 {lo[0] - hi[0]:+.3f}"
                  f"  {'✅ 分开' if lo[0] > hi[0] else '❌ 重叠'}")

    print(f"\n  稀疏权重扫描(两路都绝对刻度)—— 只看排序,闸门那条是阳性对照")
    print(f"    {'w':<8}{'top1':>12}{'可答最低余弦':>14}{'负例最高余弦':>14}")
    for w in (0.0, 0.05, W_SPARSE, 0.4, 0.8):
        t1 = n = 0
        lo, hi = 9.9, -9.9
        for q, qt, tgt, ds, ba, scope, day in cache:
            sc = [ds[i] + w * (ba[i] / (ba[i] + BM25_K)) for i in range(len(docs))]
            if tgt and not qt.startswith("路由"):
                n += 1
                t1 += (rank_of(sc, tgt) == 1)
                lo = min(lo, max(ds))          # 闸门看余弦 → 这一列必须**不随 w 变**
            elif qt in ("负例", "噪声"):
                hi = max(hi, max(ds))
        print(f"    {w:<8}{t1:>7}/{n:<4}{lo:>14.3f}{hi:>14.3f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", default="both", choices=("hard", "big", "both"))
    args = ap.parse_args()

    emb = R.get_embedder()
    print(f"embedder: {emb.name}")
    todo = {"hard": [load_hard], "big": [load_big], "both": [load_hard, load_big]}[args.corpus]
    for fn in todo:
        got = fn()
        if got:
            run_suite(*got, emb)


if __name__ == "__main__":
    main()
