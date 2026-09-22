"""背景滚动位置的**往返回归**测试:POST 存进去的,GET 必须读得回来。

为什么单独钉这一条(2026-09-22):这个功能曾经**从头到尾就是空的** ——
`POST /bg-position` 的函数体只有 `return {"status": "ok"}`(什么都不存),
而前端传的 `session_id` 还被 pydantic 的 `extra=ignore` **静默丢掉**
(请求体模型当时只声明了 `position`,而前端发的是 `{session_id, position}`)。
于是:前端拖好背景 → POST 拿到 200 → GET 回来永远是 `{"position": 0.0}`
→ 切走再切回来(或刷新)**背景弹回顶部**。**两端都不报错,控制台一个错都没有**,
所以它不是"坏了",是"从来没生效过",而且查不出来。

这条测试就是防它再退回去:它检查的正是当时失效的两条链 ——
  ① **字段真的被收下**(不是被静默丢弃);
  ② **存下来的值真的读得回来**(写接口真的写了)。

用 `TestClient` 而不是直接调函数:这样**过的是真路由 + 真 pydantic 校验**,
也就是当初出问题的那一层。直接调 `save_bg_position(BgBody(...))` 会绕过校验,
测不出"字段被丢"这类毛病。

跑: py test/test_media_bg_position.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from server.app.api import media  # noqa: E402


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(media.router)
    return TestClient(app)


def test_bg_position_round_trip() -> None:
    """写进去 0.42 → 读回来 0.42(按卡各存各的)。"""
    media._bg_positions.clear()
    c = _client()

    r = c.post("/bg-position", json={"session_id": "sid-A", "position": 0.42})
    assert r.status_code == 200, r.text

    got = c.get("/bg-position/sid-A").json()
    assert got["position"] == 0.42, f"存进去的没读回来:{got}"

    # 各存各的:别的卡不受影响,且默认是 0.0(而不是 500 / 报错)
    assert c.get("/bg-position/sid-B").json() == {"position": 0.0}


def test_bg_position_body_keeps_session_id() -> None:
    """`session_id` 必须真的进得来 —— 当初它被 pydantic 静默丢掉。

    这条单独钉"字段没被丢":只发 `position`(不带 session_id)必须 **422**,
    因为它是必填。**422 就是这里要的信号** —— 说明 schema 认识这个字段;
    当初的毛病恰恰是"不认识它、也不报错"。
    """
    media._bg_positions.clear()
    c = _client()

    r = c.post("/bg-position", json={"position": 0.42})
    assert r.status_code == 422, (
        f"不带 session_id 竟然被接受了({r.status_code})—— "
        f"说明它又变成可有可无的字段了,那 POST 会不知道该存哪张卡"
    )
    assert media._bg_positions == {}, "字段校验没过,不该写入任何东西"

    # session_id 非法(路径穿越样式)必须被挡,且不写入
    bad = c.post("/bg-position", json={"session_id": "../etc", "position": 0.1})
    assert bad.status_code == 400, bad.text
    assert media._bg_positions == {}, f"非法 id 不该落进 dict:{media._bg_positions}"

    # 合法的一次写:确认正常路径仍然通(与上面两条形成对照)
    ok = c.post("/bg-position", json={"session_id": "sid-C", "position": 0.5})
    assert ok.status_code == 200, ok.text
    assert media._bg_positions["sid-C"] == {"position": 0.5}


if __name__ == "__main__":
    test_bg_position_round_trip()
    test_bg_position_body_keeps_session_id()
    print("media bg-position all tests passed")
