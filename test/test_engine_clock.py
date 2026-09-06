"""引擎时钟钩子自测(2026-09 实验台拨"当前时间"用):

1. _clock_override["ts"]>0 → 陪聊/自走 composer 的世界 section 报它(单时间源被拨,
   仍是一个源);ts=0 = 真实墙钟(产品默认,行为不变)。
2. 补写视图判定收窄:log 设了时间游标 **且** _backfill_clock["ts"] 在转才算回放轮。
   实验台"拨当前时间"的普通轮只设游标 → 走陪聊/自走视图(带覆盖的当前时刻),
   不会被误判成补写回放轮。

跑: py test\\test_engine_clock.py
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.session_log import SessionLog  # noqa: E402
from server.app import engine as eng      # noqa: E402
from character import personas            # noqa: E402

# 假连接配置:_build_engine 只构造 LLM 客户端对象,不发网络请求(测试安全)。
_FAKE_CFG = {
    "base_url": "http://127.0.0.1:9",
    "api_key": "sk-fake-test",
    "model": "m",
    "models": ["m"],
}


def _epoch(y, mo, d, h, mi):
    return time.mktime(time.struct_time((y, mo, d, h, mi, 0, 0, 0, -1)))


def _world_text(sections) -> str:
    for name, text in sections:
        if name == "world":
            return text
    return ""


def _situation_text(sections) -> str:
    for name, text in sections:
        if name == "situation":
            return text
    return ""


def test_clock_override_feeds_world_section():
    """_clock_override ts>0 → 自走 composer 的世界 section 报该时刻。"""
    eng._build_engine(dict(_FAKE_CFG))
    ts = _epoch(2026, 9, 6, 23, 30)  # 深夜 23:30
    eng._clock_override["ts"] = ts
    try:
        log = SessionLog("clock-test")
        sections = eng.system_component_sections("self", log)
        world = _world_text(sections)
        assert "[当前时间] 2026-09-06 23:30" in world, world
        assert "周日" in world or "周六" in world or "周五" in world or \
            "周四" in world or "周三" in world or "周二" in world or \
            "周一" in world or "周日" in world, world
    finally:
        eng._clock_override["ts"] = 0.0


def test_zero_override_means_real_clock():
    """ts=0 → 世界 section 仍是墙钟格式(不崩,报真实时间)。"""
    eng._build_engine(dict(_FAKE_CFG))
    eng._clock_override["ts"] = 0.0
    log = SessionLog("clock-test")
    world = _world_text(eng.system_component_sections("self", log))
    assert "[当前时间] 20" in world  # 2026 年的年份前缀,墙钟年份
    assert world.count("[当前时间]") == 1


def test_cursor_without_backfill_clock_is_normal_self_view():
    """只设 log 游标(实验台拨当前时间)→ 仍是自走视图(situation=SELF)。"""
    eng._build_engine(dict(_FAKE_CFG))
    ts = _epoch(2026, 9, 6, 23, 30)
    eng._clock_override["ts"] = ts
    eng._backfill_clock["ts"] = 0.0
    log = SessionLog("clock-test")
    log.set_time_cursor(ts)
    try:
        sections = eng.system_component_sections("self", log)
        sit = _situation_text(sections)
        assert sit == personas.SELF_SITUATION, sit
        world = _world_text(sections)
        assert "[当前时间] 2026-09-06 23:30" in world, world
    finally:
        eng._clock_override["ts"] = 0.0
        log.clear_time_cursor()


def test_cursor_with_backfill_clock_is_backfill_view():
    """游标 **且** 补写钟在转 → 补写视图(situation=BACKFILL)。"""
    eng._build_engine(dict(_FAKE_CFG))
    ts = _epoch(2026, 9, 5, 14, 0)
    eng._backfill_clock["ts"] = ts
    log = SessionLog("clock-test")
    log.set_time_cursor(ts)
    try:
        sections = eng.system_component_sections("self", log)
        sit = _situation_text(sections)
        assert sit == personas.BACKFILL_SITUATION, sit
        world = _world_text(sections)
        assert "[当前时间] 2026-09-05 14:00" in world, world
    finally:
        eng._backfill_clock["ts"] = 0.0
        log.clear_time_cursor()


def test_timeline_shows_gap_from_last_user_message():
    """时间线段(VISION 决策 8):普通轮视图里,距上次真人互动多久被注入。

    世界=绝对时间(_clock_override),时间线=相对时间(从日志最后一条
    source=user 的消息派生)—— 实验台把当前时间拨到 13:00,而日志里
    真人消息停在 09:00 → 时间线应报 4 小时前(模型据此区分"刚聊完 vs 久别")。
    """
    eng._build_engine(dict(_FAKE_CFG))
    t_user = _epoch(2026, 9, 7, 9, 0)
    now = _epoch(2026, 9, 7, 13, 0)
    log = SessionLog("timeline-test")
    log.append("turn/start", at=t_user, turn=1, source="user")
    log.append("user/message", at=t_user, source="user", turn=1,
               content=[{"type": "text", "text": "在吗"}])
    log.append("assistant/message", at=t_user + 5, turn=1,
               content=[{"type": "text", "text": "在的"}])
    log.append("turn/end", at=t_user + 6, turn=1, reason={"kind": "completed"})
    eng._clock_override["ts"] = now
    try:
        sections = eng.system_component_sections("self", log)
        tl = [t for n, t in sections if n == "timeline"]
        assert tl and "距上次和主人说话: 4 小时前" in tl[0], sections
        # 时间线与世界同一只钟:世界段报 13:00,时间线报 4 小时前
        assert "[当前时间] 2026-09-07 13:00" in _world_text(sections)
    finally:
        eng._clock_override["ts"] = 0.0


def test_timeline_absent_when_no_user_history():
    """没跟真人说过话 → 时间线段不出现(全新会话,没什么可算的)。"""
    eng._build_engine(dict(_FAKE_CFG))
    log = SessionLog("timeline-test")
    eng._clock_override["ts"] = _epoch(2026, 9, 7, 13, 0)
    try:
        sections = eng.system_component_sections("self", log)
        names = [n for n, _ in sections]
        assert "timeline" not in names, names
    finally:
        eng._clock_override["ts"] = 0.0


def test_wake_budget_section_appears_when_active():
    """普通轮时间预算(2026-09 用户拍板:自走/心跳/脉冲与补写同一事件算法,
    只是触发点不同 —— 普通轮也产'这段时间约 X 分钟'的预算)。

    _wake_budget["min"]>0 → self composer 出现 [时间预算] 段(数字是系统给的,
    人设文案不动);=0 → 不出现(产品普通路径没设时等于没这回事)。
    """
    eng._build_engine(dict(_FAKE_CFG))
    eng._wake_budget["min"] = 90.0
    log = SessionLog("budget-test")
    try:
        sections = eng.system_component_sections("self", log)
        names = [n for n, _ in sections]
        assert "wake_budget" in names, names
        txt = [t for n, t in sections if n == "wake_budget"][0]
        assert "1 小时 30 分" in txt or "90 分钟" in txt, txt
        assert "只做一件事" in txt and "做不完" in txt, txt
    finally:
        eng._wake_budget["min"] = 0.0


def test_wake_budget_absent_when_zero():
    """预算未激活(_wake_budget 恒 0)= 段不出现(产品普通路径不抢戏)。"""
    eng._build_engine(dict(_FAKE_CFG))
    eng._wake_budget["min"] = 0.0
    log = SessionLog("budget-test")
    names = [n for n, _ in eng.system_component_sections("self", log)]
    assert "wake_budget" not in names, names


def test_draw_budget_min_within_mix_bounds():
    """抽预算纯函数:值落在 DEFAULT_DURATION_MIX 覆盖区间内(短10-25/中30-90/长100-200)。"""
    from server.rhythm import DEFAULT_DURATION_MIX, draw_budget_min
    lo = min(r for _, (r, _) in DEFAULT_DURATION_MIX)
    hi = max(r for _, (_, r) in DEFAULT_DURATION_MIX)
    for _ in range(50):
        m = draw_budget_min()
        assert lo <= m <= hi, m
    # 注入 rng 可固定复现
    import random
    a = draw_budget_min(rng=random.Random(42))
    b = draw_budget_min(rng=random.Random(42))
    assert a == b


if __name__ == "__main__":
    test_clock_override_feeds_world_section()
    test_zero_override_means_real_clock()
    test_cursor_without_backfill_clock_is_normal_self_view()
    test_cursor_with_backfill_clock_is_backfill_view()
    test_timeline_shows_gap_from_last_user_message()
    test_timeline_absent_when_no_user_history()
    test_wake_budget_section_appears_when_active()
    test_wake_budget_absent_when_zero()
    test_draw_budget_min_within_mix_bounds()
    print("engine_clock all tests passed")
