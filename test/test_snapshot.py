"""会话快照(2026-09 任务6)自测:存储往返 + 三层合并链。

合并链(engine.merge_turn_settings,纯函数):当轮 > 会话快照 > 全局默认。
关键语义:max_rounds=0 是"不限制"的真值(不是没给);幽灵模型回默认;
system_prompt 空 = 旗舰。
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from server.app import engine as E  # noqa: E402
from server.store import SessionStore  # noqa: E402


def _mkstore():
    tmp = tempfile.TemporaryDirectory()
    return tmp, SessionStore(tmp.name)


def test_store_settings_roundtrip_and_clear():
    tmp, store = _mkstore()
    try:
        sid = store.create_session("t")
        assert store.get_session_settings(sid) == {}  # 默认无快照
        store.set_session_settings(sid, {"temperature": 0.5, "max_rounds": 0,
                                         "system_prompt": "侦探人格"})
        got = store.get_session_settings(sid)
        assert got["temperature"] == 0.5 and got["max_rounds"] == 0
        assert got["system_prompt"] == "侦探人格"
        store.set_session_settings(sid, {})  # 清空回默认
        assert store.get_session_settings(sid) == {}
        # 不存在的会话 -> False
        assert store.set_session_settings("0" * 32, {}) is False
    finally:
        tmp.cleanup()


def test_merge_chain_order_and_semantics():
    snap = {"system_prompt": "侦探人格", "temperature": 0.5, "max_rounds": 10,
            "model": "deepseek-v4-pro"}
    d = dict(default_temperature=0.9, default_rounds=20,
             default_model="deepseek-v4-flash",
             available_models=("deepseek-v4-flash", "deepseek-v4-pro"))
    # 当轮给值 > 快照
    out = E.merge_turn_settings(snap, {"temperature": 0.2}, **d)
    assert out["temperature"] == 0.2 and out["model"] == "deepseek-v4-pro"
    # 当轮缺字段 -> 快照
    out = E.merge_turn_settings(snap, {"model": None}, **d)
    assert out["temperature"] == 0.5 and out["max_rounds"] == 10
    assert out["system_prompt"] == "侦探人格"
    # 0 = 不限制:真值透传,不是"没给"
    out = E.merge_turn_settings(snap, {"max_rounds": 0}, **d)
    assert out["max_rounds"] == 0
    # 幽灵模型(不在可用列表)-> 回默认(防呆:连接换了端点不至于拿幽灵模型调)
    out = E.merge_turn_settings(snap, {"model": "ghost"}, **d)
    assert out["model"] == "deepseek-v4-flash"
    # 空快照 + 空请求 = 全局默认;system_prompt None = 旗舰
    out = E.merge_turn_settings({}, {}, **d)
    assert out == {"temperature": 0.9, "max_rounds": 20,
                   "system_prompt": None, "model": "deepseek-v4-flash"}


if __name__ == "__main__":
    test_store_settings_roundtrip_and_clear()
    test_merge_chain_order_and_semantics()
    print("snapshot all tests passed")
