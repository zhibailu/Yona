"""路由教学/排障工具:打印 FastAPI 的路由表 + 模拟请求逐行匹配。

请求进来后 FastAPI 怎么找到 @app 函数?答案就一句话:
**app 内部是一张按注册顺序排的路由表(装饰器 = 往里加一行),
请求带着 method+path 从上到下逐行比对,第一个完全命中(routes 里的
APIRoute)就调用它;都不中则落到最后的 StaticFiles mount(或 404)。**

跑: py test/route_table.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from starlette.routing import Match  # noqa: E402

import server.main as sm  # noqa: E402


def dump_table():
    print("=== app.routes 路由表(注册顺序,从上到下 = 匹配优先级) ===")
    for i, r in enumerate(sm.app.routes):
        path = getattr(r, "path", "")
        methods = ",".join(sorted(getattr(r, "methods", []) or []))
        endpoint = getattr(r, "endpoint", None)
        fn = endpoint.__name__ if endpoint is not None else type(r).__name__
        print(f"[{i:2d}] {type(r).__name__:<16} {methods:<18} {path!r:<32} -> {fn}")


def simulate(method: str, path: str):
    """模拟一个请求,看它逐行匹配到谁。"""
    scope = {
        "type": "http", "method": method, "path": path,
        "headers": [], "query_string": b"",
    }
    print(f"\n--- 模拟 {method} {path} 进来 ---")
    hit = None
    for i, r in enumerate(sm.app.routes):
        m, _ = r.matches(scope)
        if m is Match.FULL:
            endpoint = getattr(r, "endpoint", None)
            fn = endpoint.__name__ if endpoint is not None else type(r).__name__
            print(f"  [{i:2d}] {type(r).__name__} 完全命中 -> {fn}")
            hit = (i, r)
            break
        if m is Match.PARTIAL:
            print(f"  [{i:2d}] {type(r).__name__} 部分匹配(method 对/路径前缀对,继续看后面)")
        else:
            print(f"  [{i:2d}] {type(r).__name__} 不匹配")
    if hit is None:
        print("  全部不匹配 -> 404(或落入 StaticFiles 找静态文件)")


if __name__ == "__main__":
    dump_table()
    simulate("GET", "/workspace")
    simulate("POST", "/chat/stream")
    simulate("GET", "/")
    simulate("GET", "/no/such/endpoint")
