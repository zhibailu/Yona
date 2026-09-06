"""离线生活补写探针(收编后,扩展版):分布/不变量/明细表/真模型多样本。

用法: py test/backfill_probe.py dist            # 采样分布(不调模型)
       py test/backfill_probe.py inv            # 数学不变量扫描(不调模型)
       py test/backfill_probe.py table          # 明细表:间隔|seed|件数|每件start+预算
       py test/backfill_probe.py real 13h       # 真模型:自动挑形状各异的 seed 逐段生成
       py test/backfill_probe.py real 13h 3,11  # 真模型:指定 seed 列表
"""
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

from server.app import engine as sm  # noqa: E402  (原 server.main facade 已移除,直达 engine)
from character.persona import build_small_night_composer  # noqa: E402
from config import require_llm_config  # noqa: E402
from core.loop import AgentLoop  # noqa: E402
from core.openai_compat import OpenAICompatibleLLM  # noqa: E402
from core.session_log import SessionLog  # noqa: E402
from core.tools import ToolRegistry  # noqa: E402
from server.rhythm import LifeSampler  # noqa: E402


def _ts(month, day, hour, minute):
    return time.mktime((2026, month, day, hour, minute, 0, 0, 0, -1))


def _fmt(ts):
    return time.strftime("%m-%d %H:%M", time.localtime(ts))


INTERVALS = {
    "2h": (_ts(9, 4, 12, 0), _ts(9, 4, 14, 0)),
    "4h": (_ts(9, 4, 13, 0), _ts(9, 4, 17, 0)),
    "6h": (_ts(9, 4, 9, 0), _ts(9, 4, 15, 0)),
    "13h": (_ts(9, 4, 11, 35), _ts(9, 5, 0, 30)),
    "24h": (_ts(9, 4, 6, 0), _ts(9, 5, 6, 0)),
    "night": (_ts(9, 4, 22, 0), _ts(9, 5, 3, 0)),
    "long": (_ts(9, 3, 8, 0), _ts(9, 5, 8, 0)),
}


def part_table():
    """表格:间隔 | seed | 件数 | 每件的 start + 预算 —— 直接看采样语义。"""
    print("=" * 78)
    print("采样明细表(每间隔挑 1件/2件/3件 各一个 seed)")
    print("=" * 78)
    print(f"{'间隔':>5} {'seed':>4} {'件数':>3} | 事件明细(start → 预算)")
    print("-" * 78)
    for label, (t0, t1) in INTERVALS.items():
        if label in ("night", "long"):
            continue  # night 基本空、long 件数多,单列
        found = {}
        for seed in range(0, 500):
            n = len(LifeSampler(t0, t1, seed=seed).sample())
            if n in (1, 2, 3) and n not in found:
                found[n] = seed
        for n in sorted(found):
            seed = found[n]
            evs = LifeSampler(t0, t1, seed=seed).sample()
            detail = " | ".join(
                f"{_fmt(e.start).split(' ')[1]}~{e.budget_min:.0f}m"
                for e in evs
            )
            print(f"{label:>5} {seed:>4} {n:>3} | {detail}")
    # night / long 单独行
    for label in ("night", "long"):
        t0, t1 = INTERVALS[label]
        n = 0
        seed = None
        for s_ in range(500):
            evs = LifeSampler(t0, t1, seed=s_).sample()
            if label == "night" and evs:
                n, seed = len(evs), s_
                break
            if label == "long" and len(evs) >= 4:
                n, seed = len(evs), s_
                break
        if seed is not None:
            evs = LifeSampler(t0, t1, seed=seed).sample()
            detail = " | ".join(
                f"{_fmt(e.start).split(' ')[1]}~{e.budget_min:.0f}m"
                for e in evs
            )
            print(f"{label:>5} {seed:>4} {n:>3} | {detail}")
    print("=" * 78)


def part1_distribution():
    print("=" * 72)
    print("第一部分 · 采样段数分布(每种间隔 × 60 seed)")
    print("=" * 72)
    for label, (t0, t1) in INTERVALS.items():
        counts = [len(LifeSampler(t0, t1, seed=s).sample()) for s in range(60)]
        mean = statistics.mean(counts)
        empty = counts.count(0)
        print(f"  {label:>5s}: 均值 {mean:.1f} | 空 {empty}/60 | "
              f"min {min(counts)} max {max(counts)} | {sorted(counts)}")


def part2_invariants():
    print("=" * 72)
    print("第二部分 · 数学不变量扫描(每种间隔 × 500 seed)")
    print("=" * 72)
    total_bad = 0
    for label, (t0, t1) in INTERVALS.items():
        bad = 0
        for s in range(500):
            evs = LifeSampler(t0, t1, seed=s).sample()
            prev_end = t0
            for ev in evs:
                if not (t0 <= ev.start < ev.end <= t1):
                    bad += 1
                elif ev.start < prev_end:
                    bad += 1
                elif ev.budget_min > 6 * 60:
                    bad += 1
                lt = time.localtime(ev.start)
                h = lt.tm_hour + lt.tm_min / 60.0
                if h >= 23.5 or h < 6.5:
                    bad += 1  # start 落在睡眠窗内
                # 名义结束(budget 段)落进睡眠窗(23:30~次日06:30)算违规(她该睡了);
                # 容差 1s 防浮点边界
                import datetime as _dt
                day0 = _dt.datetime(lt.tm_year, lt.tm_mon, lt.tm_mday)
                sleep_lo = (day0 + _dt.timedelta(hours=23.5)).timestamp()
                sleep_hi = (day0 + _dt.timedelta(days=1, hours=6.5)).timestamp()
                if sleep_lo + 1 < ev.end < sleep_hi:
                    bad += 1
                prev_end = ev.end
        total_bad += bad
        print(f"  {label:>5s}: 违规 {bad}/500")
    print(f"  总计违规: {total_bad}")


def _pick_shape_seeds(label, want=(1, 2, 3)):
    """挑 seed:分别落在 1件/2件/3件… 组,每组取第一个。"""
    t0, t1 = INTERVALS[label]
    found = {}
    for seed in range(0, 2000):
        n = len(LifeSampler(t0, t1, seed=seed).sample())
        if n in want and n not in found:
            found[n] = seed
        if len(found) == len(want):
            break
    return [found[n] for n in sorted(found)]


def _run_one_seed(loop, label, seed):
    t0, t1 = INTERVALS[label]
    evs = LifeSampler(t0, t1, seed=seed).sample()
    print("\n" + "=" * 72)
    print(f"seed {seed}  采样 {len(evs)} 个事件")
    print("=" * 72)
    empty = ToolRegistry([])
    life_log = SessionLog("_probe_life")  # 每 seed 独立日志(seed 间互不串历史)
    for i, e in enumerate(evs):
        print(f"  --- 事件 {_fmt(e.start)} 起(~{e.budget_min:.0f} 分钟预算)")
        if i == 0:
            gap_note = ""
        else:
            prev = evs[i - 1]
            gap = e.start - (prev.start + prev.budget_min * 60)
            gap_note = (
                f"\n距离你上一件事做完已经过了约 {sm._human_gap(gap)}"
                "(中间的时间平平淡淡,没发生值得记的事)。"
                if gap > 60 else "\n你上一件事刚做完不久。"
            )
        note = (
            f"这段时间(约 {sm._human_gap(e.budget_min * 60)})里你只做了"
            "**一件事**——就是现在刚做完/正在做的这一件。"
            f"{gap_note}"
        )
        life_log.set_time_cursor(e.start)
        _clock_holder[0] = e.start
        try:
            loop.run_turn(source="self", log=life_log, tools=empty, self_note=note)
        finally:
            life_log.clear_time_cursor()
            _clock_holder[0] = 0.0
        turn = [e.data["turn"] for e in life_log.events
                if e.type == "turn/start"][-1]
        for e in life_log.events:
            if e.type == "assistant/message" and e.data.get("turn") == turn:
                txt = "".join(b.get("text", "") for b in e.data.get("content", [])
                              if b.get("type") == "text")
                print(f"      {txt.strip()}")


def part3_real(label, seed_str=None):
    t0, t1 = INTERVALS[label]
    api_key, base_url, model = require_llm_config()
    llm = OpenAICompatibleLLM(api_key=api_key, base_url=base_url, model=model,
                              temperature=0.9, max_tokens=500)
    backfill_composer = build_small_night_composer(
        sm.PERSONA, sm._state, sm._tools,
        situation=sm.BACKFILL_SITUATION,
        world_now=lambda: time.localtime(_clock_holder[0]),
    )
    self_composer = build_small_night_composer(
        sm.PERSONA, sm._state, sm._tools, situation=sm.SELF_SITUATION)
    chat_composer = build_small_night_composer(
        sm.PERSONA, sm._state, sm._tools, situation=sm.CHAT_SITUATION)

    def sys_by_source(registry, source, log=None):
        if log is not None and log.time_cursor is not None:
            return backfill_composer.compose({**sm.VALUES, "registry": registry})
        composer = self_composer if source == "self" else chat_composer
        return composer.compose({**sm.VALUES, "registry": registry})

    loop = AgentLoop(SessionLog("_probe"), llm, sm._tools,
                     system_prompt=sys_by_source, max_steps=8)

    if seed_str:
        seeds = [int(x) for x in seed_str.split(",")]
    else:
        seeds = _pick_shape_seeds(label, want=(1, 2, 3))
        print(f"[{label}] 自动挑 seed: 1件={seeds[0]}, 2件={seeds[1]}, 3件={seeds[2]}"
              if len(seeds) == 3 else f"[{label}] 挑到的 seed: {seeds}")
    for seed in seeds:
        _run_one_seed(loop, label, seed)


_clock_holder = [0.0]


def main():
    args = sys.argv[1:]
    mode = args[0] if args else "all"
    if mode in ("dist",):
        part1_distribution()
    elif mode in ("inv",):
        part2_invariants()
    elif mode in ("table",):
        part_table()
    elif mode == "real":
        label = args[1] if len(args) > 1 else "13h"
        seed_str = args[2] if len(args) > 2 else None
        part3_real(label, seed_str)
    elif mode == "all":
        part1_distribution()
        part2_invariants()
        part_table()
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
