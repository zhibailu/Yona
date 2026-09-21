"""造大语料:用真模型生成"生活事件 + 它自己的 query + 独有关键词"。

⚠️ **要花真模型钱**(22 次调用,每次约 10~30 秒)。产物**已经提交在仓里**
   (`test/recall_corpus_big.json`),所以只想跑 bench 的话**不用重跑这个脚本**;
   要扩语料、换主题时才用它。

为什么要真模型:模板生成的文本会让检索难度失真(相似度是人造的)。
语料目标:同主题**多条互相接近**(真实卡片就是几十条都在讲同一类事)。

产物落盘(增量写、可续跑:已完成的主题会 skip):
  test/recall_corpus_big.json
  {"items":[{"id","theme","day","kind","text","q","keys":[...]}, ...],
   "meta":{"done":[...], "model":...}}

跑法:py test/recall_corpus_gen.py      (需要 data/llm.local.json)
"""
import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 默认落在 test/ 下 —— recall_bench.py --corpus big 直接读它
OUT = ROOT / "test" / "recall_corpus_big.json"

THEMES = [
    "喝的东西(咖啡/茶/牛奶/汤水)",
    "学姐这个人(她的事、我们之间的事)",
    "衣服、鞋、穿什么",
    "课程与论文、作业、考试",
    "便利店与楼下小店",
    "睡眠、失眠、做梦、赖床",
    "看过的书和电影、听过的歌",
    "打扫、收纳、整理房间",
    "天气、季节、冷热",
    "吃饭、做饭、外卖、食堂",
    "上学路上、公交地铁、走路",
    "手机、电脑、充电、网络故障",
    "室友之间的小事",
    "家里打来的电话、老家的事",
    "运动、散步、身体的小毛病",
    "养的花草、绿植、小动物",
    "花钱、省钱、想买没买的东西",
    "难过、生气、emo 的时刻",
    "开心、被逗笑、小小的得意",
    "拖延、赶 deadline、通宵",
]
SPECIAL = [
    "荒诞但真的发生过的事(例如半夜听见楼下搬家具、梦见自己变成别的东西、"
    "捡到奇怪的东西)——要写得像真事,不要写成笑话",
    "特别琐碎、特别麻烦、但你记得很清楚的那种小事故"
]

PER_THEME = 15

SYS = (
    "你是小夜子的记录者。小夜子是 21 岁的女大学生,独居,性格安静,有点小腹黑。\n"
    "你在把她一个人时发生过的事情写成一条条简短的记录。\n"
    "要求:\n"
    "1. 第一人称,一条一到两句,像随手记的一笔,有具体的时间/地点/物件/动作。\n"
    "2. 同一个主题下的多条**不要重复同样的细节**,各自是不同的一天、不同的事。\n"
    "3. 不要抒情、不要总结、不要写'我学到了什么'、不要提工具或系统。\n"
    "4. 每条附一条**以后用来回忆它的 query**:像是过一阵子想问'那次……是什么来着',"
    "**不许整句照抄原文**,要换一种说法(可以只抓住最特别的那个细节)。\n"
    "5. 每条再给 1~2 个**只有这条才有**的关键词(专名、物件名、地名、店名、数字等)。\n"
    "6. day 写成 MM-DD 形式,同主题里不要重复。\n"
    "只输出 JSON 数组,不要任何解释或 markdown 围栏。格式:\n"
    '[{"day":"09-12","text":"...","q":"...","keys":["...","..."]}, ...]'
)


def load_llm():
    from core.openai_compat import OpenAICompatibleLLM
    cfg = json.loads((ROOT / "data" / "llm.local.json").read_text(encoding="utf-8"))
    return OpenAICompatibleLLM(api_key=cfg["api_key"], base_url=cfg["base_url"],
                               model=cfg["model"], temperature=1.0,
                               # ⚠️ 2600 装不下 15 条 JSON → 尾条被截在半路,整批解析失败
                               # (2026-09-21 实测:14/22 批都栽在这)。留足。
                               max_tokens=8000, timeout=180.0)


def parse(raw: str):
    t = raw.strip()
    t = re.sub(r"^```(?:json)?|```$", "", t, flags=re.M).strip()
    i, j = t.find("["), t.rfind("]")
    body = t[i:j + 1] if (i >= 0 and j > i) else t[i:] if i >= 0 else ""
    if not body:
        return []
    try:
        arr = json.loads(body)
    except Exception:
        # **抢救被截断的数组**:砍到最后一个完整的对象,再补上 ] ——
        # 截断时前 N 条是好的,丢掉整批太浪费
        cut = body.rfind("}")
        arr = []
        if cut > 0:
            try:
                arr = json.loads(body[:cut + 1] + "]")
            except Exception:
                arr = []
    out = []
    for it in arr:
        if not isinstance(it, dict):
            continue
        text = str(it.get("text", "")).strip()
        q = str(it.get("q", "")).strip()
        keys = [str(k).strip() for k in (it.get("keys") or []) if str(k).strip()]
        day = str(it.get("day", "")).strip()
        if len(text) < 8 or len(q) < 3:
            continue
        if not re.match(r"^\d{2}-\d{2}$", day):
            day = ""
        out.append({"day": day, "text": text, "q": q, "keys": keys[:3]})
    return out


def main():
    llm = load_llm()
    data = {"items": [], "meta": {"done": [], "model": llm.model, "at": time.time()}}
    if OUT.exists():
        try:
            data = json.loads(OUT.read_text(encoding="utf-8"))
        except Exception:
            pass
    done = set(data["meta"].get("done", []))

    jobs = [(f"T{i:02d}", f"主题:{t}") for i, t in enumerate(THEMES)]
    jobs += [(f"S{i:02d}", f"特殊情况:{t}") for i, t in enumerate(SPECIAL)]

    for tag, desc in jobs:
        if tag in done:
            print(f"[skip] {tag} {desc[:24]}", flush=True)
            continue
        t0 = time.time()
        items, raw = [], ""
        for attempt in range(1, 4):
            try:
                out = llm.invoke([
                    {"role": "system", "content": SYS},
                    {"role": "user",
                     "content": f"{desc}\n请写 {PER_THEME} 条。"},
                ])
                raw = out.text or ""
                items = parse(raw)
            except Exception as e:  # noqa: BLE001
                print(f"  [{tag}] 第 {attempt} 次异常: {e}", flush=True)
                raw = ""
            if items:
                break
            # 空返回多半是限流/超时 → 等一会儿再试(实测有 0 字的情况)
            print(f"  [{tag}] 第 {attempt} 次拿到 {len(raw)} 字、解析 {len(items)} 条,重试",
                  flush=True)
            time.sleep(4 * attempt)
        if not items:
            # ⛔ **空结果不许记成 done** —— 否则这一格会被永久跳过(踩过一次)
            bad = Path(os.environ["TEMP"]) / f"genfail_{tag}.txt"
            bad.write_text(raw, encoding="utf-8")
            print(f"[空] {tag} {desc[:24]} 三次都没出结果,原文存 {bad.name}"
                  f"({len(raw)} 字),下次重跑", flush=True)
            continue
        for k, it in enumerate(items):
            data["items"].append({
                "id": f"{tag}-{k:02d}",
                "theme": desc,
                "kind": "life",
                **it,
            })
        done.add(tag)
        data["meta"]["done"] = sorted(done)
        OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"[ok] {tag} {desc[:24]} -> {len(items)} 条  "
              f"({time.time() - t0:.1f}s, 累计 {len(data['items'])})", flush=True)

    print(f"\n完成:共 {len(data['items'])} 条 → {OUT}", flush=True)


main()
