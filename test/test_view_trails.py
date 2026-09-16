"""动作轨迹配对自测:tool/call ↔ tool/result 必须按 **tool_call_id** 配对。

为什么单独钉这一条(2026-09-16):内核把一步内的工具改成**并发执行**后,
日志形状从 `call,result,call,result` 变成 `call,call,result,result` ——
而 view 原来是用"从后往前找最近一条还没配对的 call"来配的。
新形状下那套逻辑会把**两个结果互换**,而且不报错、只是安静地配错。
所以这里用一个"换了形状才会错"的场景把它钉死。

跑: py test/test_view_trails.py
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from server.app.api import view
from server.store import SessionStore


def _two_call_log() -> SessionStore:
    """一条日志:两个 call 先落,两个 result 后落(并发执行后的真实形状)。

    故意让**慢的声明在前**:并发时它其实后返回,但日志按声明顺序写。
    旧逻辑(后往前找)会把两个结果对调 —— 这正是要防的。
    """
    store = SessionStore(Path(tempfile.mkdtemp()))
    sid = store.create_session("轨迹测试")
    log = store.load_log(sid)
    log.append("turn/start", turn=1, source="user")
    log.append("assistant/message", turn=1, step=1, content=[
        {"type": "tool-call", "id": "c_slow", "name": "慢动作", "arguments": '{"x": 1}'},
        {"type": "tool-call", "id": "c_fast", "name": "快动作", "arguments": '{"y": 2}'},
    ])
    log.append("tool/call", turn=1, step=1, call_id="c_slow", name="慢动作",
               arguments='{"x": 1}')
    log.append("tool/call", turn=1, step=1, call_id="c_fast", name="快动作",
               arguments='{"y": 2}')
    log.append("tool/result", turn=1, step=1, tool_call_id="c_slow",
               content=[{"type": "text", "text": "慢的结果"}], is_error=False)
    log.append("tool/result", turn=1, step=1, tool_call_id="c_fast",
               content=[{"type": "text", "text": "快的结果"}], is_error=False)
    log.append("turn/end", turn=1, reason={"kind": "completed"})
    store.save_log(sid, log)
    return store


def test_trails_pair_by_call_id_not_by_adjacency() -> None:
    store = _two_call_log()
    original = view.engine._store
    view.engine._store = store
    try:
        trails = view.all_action_trails()
    finally:
        view.engine._store = original

    by_action = {t["action"]: t for t in trails}
    assert set(by_action) == {"慢动作", "快动作"}, by_action
    # 顺序 = 声明顺序
    assert [t["action"] for t in trails] == ["慢动作", "快动作"]
    # 配对正确:慢的拿慢的结果。旧逻辑在这里会把两者对调。
    assert by_action["慢动作"]["text"] == "慢的结果"
    assert by_action["快动作"]["text"] == "快的结果"
    # 标题取自各自的参数(第一个值),说明也是按 id 认领的
    assert by_action["慢动作"]["title"] == "x: 1"
    assert by_action["快动作"]["title"] == "y: 2"


if __name__ == "__main__":
    test_trails_pair_by_call_id_not_by_adjacency()
    print("action trails all tests passed")
