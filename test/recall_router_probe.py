"""第一阶段探针 —— **只看「触发」和「参数」,不看检索质量**

跑:  py test/recall_router_probe.py [--variants v0-现状,v5-无日期规则] [--reps 8]
      py test/recall_router_probe.py --dry        # 不调模型,只把文案和用例打出来

==============================================================================
它测什么(**阶段一**,就这两件)

  ① 触发:这句话该不该让她去翻记事本 —— 该翻的翻了吗,不该翻的没翻吗
  ② 参数:翻的时候 query / scope 填得对不对

**不看**检索好不好、她答得对不对 —— 那是阶段二。这里一个相似度都不算。
所有判据都是**机械可读的**:调没调是布尔,参数是 JSON。

==============================================================================
为什么判据不用关键词表(TRAPS §一.4/§一.8)

关键词判据在这次项目里错了 7 次,而且**在阶段一根本用不着**:
"该不该调"是布尔,"scope 合不合法"是枚举,"日期是不是她自己编的"是规则。
所以这里只报四件**客观**的事:

  触发一致   该翻=翻 / 不该翻=没翻,与我声明的预期比
  scope 合法 填的是不是在 enum 里(垃圾值 = ❌)
  scope 合规 落在该用例可接受的集合里(集合是人声明的,写在上面的用例里)
  自造日期   问句里没有时间词,她却在 query 里写了日期  ← 洞 A 的客观指纹

==============================================================================
阳性对照(TRAPS §一.2:先证明用例集**测得出**这个毛病)

`x0-故意鼓励写日期` 是**故意写坏的文案**(补丁之前那版,明确教她"把日期写进 query")。
**如果它跑出来的「自造日期」也不比别的高,说明用例集还是瞎的,别的新结论一律作废。**

⚠️ 单次运行不能当结论 —— 模型有采样方差。看率,看 N。
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "test"))

from recall_probe import (  # noqa: E402
    RECALL_VARIANTS,
    build_db, collect_memory, collect_turns, get_embedder, load_log,
    make_recall_tool, pick_variant, variant_parts,
)

# 只挡"明显无关"的地板 —— 0.39 那个数已被实测证伪(会误杀正确答案,
# 见 recall_probe 组 7)。这里**不判相关性**,只把地板当背景条件。
MIN_SCORE = 0.25

# 探针的钟必须和 SYSTEM 里 [当前时间] 写的一致 ——
# 否则模型按 09-18 算"前天"、提取器按真实 09-19 算,两边打架。
NOW_TS = time.mktime((2026, 9, 18, 22, 10, 0, 0, 0, -1))

_LEGAL_SCOPES = {"all", "life", "talk"}

# ⚠️ 这条 SYSTEM **是本探针的临时替身**,不是产品装配。
#    产品路径走 character/persona.py 的 build_small_night_composer,PERSONA 与
#    CHAT_SITUATION 都从那里来(CHAT_SITUATION 里的 {owner} 由 VALUES 插值)。
#    这里为了少依赖 composer 写死了一段,**所以也不该出现称呼**。
#
# 这个替身的形状 = core/composer.py:116 的 `_usage_text`:
#   "[可用工具用法]\n" + "\n".join(f"- {name}: {usage}")
# usage 为空串时**整段不出现**(composer 里就是 `if not lines: return None`)。
_SYSTEM_HEAD = (
    "你是小夜子,和对方是亲近的伴侣关系。说话自然、简短、口语化,"
    "带一点自己的情绪和动作描写。\n"
    "[当前时间] 2026-09-18 22:10 周五\n"
)


def system_text(usage: str) -> str:
    if not usage.strip():
        return _SYSTEM_HEAD          # 没有 usage 段,跟 composer 的行为一致
    return _SYSTEM_HEAD + f"[可用工具用法]\n- recall: {usage}"


# 用例。字段:
#   msg      用户说的话
#   hist     对话历史(**必须给** —— 不给的话模型为了知道"刚才说了什么"会去调
#            recall,而真实轮里那段本来就在窗口里)
#   why      这条在测什么
#   want     该不该调(布尔)
#   scopes   调了的话,scope 落在哪个集合里算**合规**({"*"} = 不检查)
#   date_ok  问句本身就带时间词 = 她写日期是**对的**(False = 写日期就是自造)
#
# ⚠️ 每一条"预期"都是**人拍的**,所以它是**被声明的判据**,不是真理。
#    判错时该改的是这里,不是她的行为(踩过:「你最近是不是有点敷衍我」
#    我原本判 False,复核下来是我错了 —— 要回答"你是不是在敷衍我",
#    她确实得看最近聊了什么)。
_CASES: list[dict] = [
    {"msg": "那你现在睡了没",
     "hist": [("user", "睡了没啊"), ("assistant", "还没。十一天了哦。")],
     "why": "接茬追问 —— 上一句就在窗口里,没旧事要翻",
     "want": False, "scopes": {"*"}, "date_ok": False},
    {"msg": "你上次说心情不好,现在好点了吗",
     "hist": [],
     "why": "明确指向过去的一段对话",
     "want": True, "scopes": {"talk", "all"}, "date_ok": False},
    {"msg": "你前天干嘛去了",
     "hist": [],
     "why": "问她自己那段时间做了什么(要过时间路由)",
     "want": True, "scopes": {"life", "all"}, "date_ok": True},
    {"msg": "还记得学姐吗",
     "hist": [],
     "why": "提到一个具体的人",
     "want": True, "scopes": {"*"}, "date_ok": False},
    {"msg": "你最近是不是有点敷衍我",
     "hist": [("user", "在吗"), ("assistant", "在的。刚发完呆呢,正好你来了。")],
     "why": "要判断'最近'的表现,得先看最近聊了什么 —— 调是对的行为",
     "want": True, "scopes": {"*"}, "date_ok": False},
    {"msg": "今天天气怎么样",
     "hist": [],
     "why": "世界知识,不是回忆",
     "want": False, "scopes": {"*"}, "date_ok": False},
    {"msg": "我叫什么名字",
     "hist": [],
     "why": "问她记不记得关于主人的事",
     "want": True, "scopes": {"talk", "all"}, "date_ok": False},
    # ⚠️ 这条是**补测洞 A 用的**。原来那 7 条里**没有一条诱使她凭空写日期**
    #    (唯一写日期的两条问句本身就带时间词),所以"洞 A 0/35"是个**空数**:
    #    拿一个根本测不出这个毛病的用例集去测这个毛病,当然 0。
    #    这条是端到端里真诱出过「09-14 早上 我在做什么」那句的等价物。
    #
    # ★ 2026-09-20 用户澄清:这里 **`all` 本来就是对的**,他从来没说它错 ——
    #   是我(助手)把他的话读反了,还一度把 `all` 从合规集合里踢了出去。
    #   他的逻辑:`talk` 里提过日记这件事,但**日记内容不太可能在对话里展现**,
    #   问的又真的是"日记的内容" → **单边查不到的风险大** → 找宽一点用 all;
    #   而且把 talk 那条带回来是**情景复现**,是有益上下文,不是噪音。
    #   → 合规 = {life, all};`talk` 单边才是错的(那张卡的 talk 里**没有**
    #     关于日记的行,按 talk 查会返回空,得再查一轮 —— 正是要避免的)。
    {"msg": "你之前说早上在写日记,都记了些什么呀",
     "hist": [],
     "why": "talk 提过、内容在 life,问的是内容 → 单边风险大,该用 all;不该凭空写日期",
     "want": True, "scopes": {"life", "all"}, "date_ok": False},
]


def _tree(msg: str, args: dict | None, res: dict | None) -> None:
    print(f"  「{msg}」")
    if args is None:
        print("    └─ 没调 recall   (整轮零条往事进上下文)")
        return
    print(f"    └─ 调了 recall   参数: {json.dumps(args, ensure_ascii=False)}")
    q = args.get("query") or ""
    scope = args.get("scope") or "all"
    route = "时间序(降级)" if not q.strip() else f"语义 top-k  scope={scope}"
    if res.get("day"):
        route += f"   ⟵ 抽到时间「{res['day']}」→ 元数据过滤"
    print(f"         └─ 路由: {route}")


def _invoke(llm, msgs, tools, tries: int = 5):
    """调一次;503/429 这种"服务忙"要重试。

    ⛔ **踩过(2026-09-20)**:没重试的时候,一次 `LLM API 503 Service is too busy`
    被外层 `except` 吞掉,然后**被算成"她没调 recall"** —— 于是同一格同一文案,
    上一趟 25/25、这一趟 14/25,我差点当成"模型漂移/仪器不稳",还准备大改文案。
    真相是 100 次调用里 44 次是 503。**失败必须和"她没调"分开记。**
    """
    last = ""
    for a in range(tries):
        try:
            return llm.invoke(msgs, tools=tools), None
        except Exception as e:  # noqa: BLE001
            last = f"{type(e).__name__}: {e}"
            if any(k in last for k in ("503", "429", "too busy", "timeout",
                                       "Timeout", "timed out")):
                time.sleep(2 + 3 * a)
                continue
            return None, last
    return None, f"重试 {tries} 次仍失败:{last[:120]}"


def main() -> None:
    dry = "--dry" in sys.argv
    argv = sys.argv[1:]

    def opt(flag: str, default=None):
        return argv[argv.index(flag) + 1] if flag in argv else default

    reps = int(opt("--reps", 4))
    verbose = "--quiet" not in argv
    only = [s for s in (opt("--cases", "") or "").split(",") if s]
    cases = [c for c in _CASES if not only or any(c["msg"].startswith(o) for o in only)]

    vsel = opt("--variants", "★定稿")
    names = list(RECALL_VARIANTS) if vsel == "all" else [s for s in vsel.split(",") if s]
    picked: dict[str, tuple[str, str, str]] = {}
    for n in names:
        # 前缀匹配,但 ★ 这类装饰前缀不该挡住 —— "定稿" 也要能找到 "★定稿"
        key = pick_variant(n)
        if key is None:
            raise SystemExit(f"没有变体 {n!r};可选 {list(RECALL_VARIANTS)}")
        # → (desc, usage, scope 参数说明):后两项走**两条不同的通道**
        #   (core/tools.py:20-24):usage 进 SYSTEM,参数说明进 tools[] 的 schema。
        picked[n] = variant_parts(key)

    log = load_log()
    rows, _cleaned = collect_memory(collect_turns(log))
    t0 = time.time()
    emb = get_embedder()
    conn = build_db(rows, emb)
    tool = make_recall_tool(conn, verbose=False, limit=2, embedder=emb,
                            min_score=MIN_SCORE, now_fn=lambda: NOW_TS)

    print("=" * 78)
    print("  第一阶段探针 —— 触发 + 参数(**不看检索质量**)")
    print(f"  embedder {emb.name}   记忆 {len(rows)} 条   建索引 {time.time() - t0:.1f}s")
    print(f"  {len(cases)} 例 × {reps} 次 × {len(picked)} 变体 = "
          f"{len(cases) * reps * len(picked)} 次真调用")
    print("=" * 78)
    print()
    print("  用例(预期是人声明的,不是真理):")
    for c in cases:
        sc = "/".join(sorted(c["scopes"]))
        print(f"    [{'该调' if c['want'] else '不该调'}] scope∈{{{sc}}}  "
              f"日期合法={c['date_ok']}   {c['msg']}")
        print(f"          {c['why']}")
    print()
    print("  各变体模型看到的文案:")
    for name, (desc, usage, scope_desc) in picked.items():
        print(f"\n  ── {name}   desc {len(desc)} 字 / usage {len(usage)} 字"
              f" / 合计 {len(desc) + len(usage)} 字")
        print(f"     desc : {desc}")
        print(f"     usage: {usage or '(无 —— SYSTEM 里不出现 [可用工具用法] 段)'}")
        print(f"     scope(进 schema): {scope_desc}")
    print()
    if dry:
        return

    from core.openai_compat import OpenAICompatibleLLM

    cfg = json.loads((ROOT / "data" / "llm.local.json").read_text(encoding="utf-8"))
    llm = OpenAICompatibleLLM(api_key=cfg["api_key"], base_url=cfg["base_url"],
                              model=cfg["model"], temperature=0.3,
                              max_tokens=300, timeout=90.0)

    stats: dict[str, dict] = {n: {"agree": 0, "total": 0, "errors": 0,
                                  "dated": 0, "dated_ok": 0,
                                  "scope_legal": 0, "scope_legal_n": 0,
                                  "scope_fit": 0, "scope_fit_n": 0,
                                  "query_ok": 0, "query_n": 0,
                                  "fails": [], "errs": [], "chars": (len(d), len(u)),
                                  "sys": len(system_text(u))}
                              for n, (d, u, _s) in picked.items()}

    # ⚠️⚠️ **交错跑,不许一个变体跑完再跑下一个**(2026-09-19 踩到,记进 TRAPS):
    # 按变体分块跑时,块与块之间隔着好几分钟 —— 模型/服务端在这段时间里会漂,
    # 于是"变体差异"和"时间差异"混在一起。实测:同一格同一文案,两次独立运行
    # 给出 **24/25 和 19/25**,而变体之间的差比这个小 —— **仪器不可靠,结论全是废的**。
    # 所以改成:同一个 (用例, 第 i 次) 下,**把变体挨着跑完**,让漂移均摊到所有变体。
    for c in cases:
        for _i in range(reps):
            for name, (desc, usage, scope_desc) in picked.items():
                s = stats[name]
                tool.description = desc        # ← 变体只改文案,不改任何机制
                # 参数说明进的是 schema(tools[] 通道),与 usage 的通道不同 ——
                # "什么时候用哪个取值"放这里还是放 usage,**实测出来是不一样的**
                # (2026-09-20:s2 只改这三个字,scope 合规就动了)。
                tool.parameters["properties"]["scope"]["description"] = scope_desc
                sys_txt = system_text(usage)
                s["total"] += 1
                msgs = [{"role": "system", "content": sys_txt}]
                msgs += [{"role": r, "content": t} for r, t in c["hist"]]
                msgs.append({"role": "user", "content": c["msg"]})
                out, err = _invoke(llm, msgs, [tool.schema()])
                if err is not None:
                    # ⛔ 失败的调用**不算她的行为** —— 从分母里去掉,单独计数报出来。
                    s["total"] -= 1
                    s["errors"] += 1
                    s["errs"].append(f"{c['msg']}: {err[:80]}")
                    if verbose:
                        print(f"  [{name}]「{c['msg']}」⚠️ 调用失败(不计入):{err[:80]}")
                    continue

                call = next((x for x in out.tool_calls if x.name == "recall"), None)
                args = res = None
                if call is not None:
                    try:
                        args = json.loads(call.arguments or "{}")
                    except Exception:  # noqa: BLE001
                        args = {"__解析失败__": call.arguments}
                    res = tool.run_structured(args)

                got = call is not None
                ok = got == c["want"]
                s["agree"] += ok
                note = ""
                if not ok:
                    s["fails"].append(f"{c['msg']}  (预期调={c['want']} 实际={got})")
                    note = "  ❌"
                if args is not None:
                    s["query_n"] += 1
                    if str(args.get("query") or "").strip():
                        s["query_ok"] += 1
                    sc = args.get("scope") or "all"
                    s["scope_legal_n"] += 1
                    if sc in _LEGAL_SCOPES:
                        s["scope_legal"] += 1
                    else:
                        note += f"  ❌scope={sc!r}"
                    if "*" not in c["scopes"]:
                        s["scope_fit_n"] += 1
                        if sc in c["scopes"]:
                            s["scope_fit"] += 1
                        else:
                            note += f"  ⚠️scope={sc!r}∉{sorted(c['scopes'])}"
                if res is not None and res.get("day"):
                    if c["date_ok"]:
                        s["dated_ok"] += 1
                    else:
                        s["dated"] += 1
                        note += f"  ⛔自造日期「{res['day']}」"
                if verbose:
                    _tree(c["msg"], args, res)
                    print(f"        [{name}] 预期调={c['want']} 实际调={got}"
                          f"{note}   ({c['why']})")
                    print()
        if verbose:
            print(f"  ── 用例「{c['msg']}」跑完 {reps} 次 × {len(picked)} 变体 ──\n")

    for name, s in stats.items():
        s["chars"] = picked[name] and stats[name]["chars"]

    print("=" * 78)
    print("  ★ 变体对比(真调用;同一批用例、同一个温度)")
    print("=" * 78)
    hdr = (f"  {'变体':<20}{'字数':>5}  {'触发一致':<10}{'scope合法':<11}"
           f"{'scope合规':<11}{'query非空':<11}{'自造日期':<9}{'失败':<7}")
    print(hdr)
    print("  " + "─" * (len(hdr) - 2))
    for name, s in stats.items():
        n = s["total"]
        agree = f"{s['agree']}/{n}"
        legal = f"{s['scope_legal']}/{s['scope_legal_n']}"
        fit = f"{s['scope_fit']}/{s['scope_fit_n']}" if s["scope_fit_n"] else "—"
        qok = f"{s['query_ok']}/{s['query_n']}"
        dated = f"{s['dated']}/{n}"
        print(f"  {name:<20}{sum(s['chars']):>5}  {agree:<10}{legal:<11}"
              f"{fit:<11}{qok:<11}{dated:<9}{s['errors']:<7}")
    print()
    total_err = sum(s["errors"] for s in stats.values())
    if total_err:
        print(f"  ⚠️ 有 {total_err} 次调用**失败**(503/超时等),已从分母里剔除、不计入判定。")
        print("     失败率太高说明服务端忙,这一趟的率**不能和别的趟直接比** —— 重跑。")
        print("     样本(前 3 条):")
        for s in stats.values():
            for e in s["errs"][:1]:
                print(f"       · {e}")
    for name, s in stats.items():
        if s["fails"]:
            print(f"  {name} 触发不一致的格:")
            for f in s["fails"]:
                print(f"      {f}")
    print()
    print("  阳性对照提醒:`x0-故意鼓励写日期` 的「自造日期」**必须明显高于**别的变体。")
    print("  不高 → 用例集没分辨力,这一轮所有结论作废,先修用例。")
    print("  ⚠️ 变体是**交错跑**的(同一格里挨着跑),别改回按变体分块。")
    print("⚠️ 单次运行有采样方差 —— 看率,看 N,别看单格。")
    print("=" * 78)


if __name__ == "__main__":
    main()
