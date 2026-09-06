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


if __name__ == "__main__":
    test_clock_override_feeds_world_section()
    test_zero_override_means_real_clock()
    test_cursor_without_backfill_clock_is_normal_self_view()
    test_cursor_with_backfill_clock_is_backfill_view()
    print("engine_clock all tests passed")
