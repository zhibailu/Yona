"""SSE 教学实验:三幕 —— SSE 是什么 / HTTP 请求长什么样 / async 并发 vs 阻塞。

纯标准库,不起我们的 server、不调模型。直接跑: py test/sse_edu.py

幕 1  构造一个真实的 HTTP POST 请求,打印它长什么样
幕 2  迷你 SSE 服务器(同一个协议!),两个客户端并发连,
      对比"阻塞版"与"async 版"的时间线 —— 这就是之前 bug 的缩小复现
幕 3  打印客户端实际收到的原始字节(响应头 + data: 块)
"""

import asyncio
import json
import socket
import time

# ============================================================
# 幕 1:HTTP POST 请求到底长什么样
# ============================================================

def act1_show_post_request():
    print("=" * 70)
    print("幕 1 · 一个真实的 HTTP POST 请求(网络上传的原始字节)")
    print("=" * 70)

    # 这就是前端每次发消息时组装的东西(见 static/app-messages.js)
    body = {
        "session_id": "abc123",
        "message": "你好",
        "temperature": 0.9,
        "max_tokens": 2048,
    }
    body_bytes = json.dumps(body).encode("utf-8")

    # HTTP 请求 = 请求行 + 请求头 + 空行 + 请求体
    raw = (
        b"POST /chat/stream HTTP/1.1\r\n"
        b"Host: 127.0.0.1:8000\r\n"
        b"Content-Type: application/json\r\n"
        + f"Content-Length: {len(body_bytes)}\r\n".encode()
        + b"\r\n"
        + body_bytes
    )
    print("\n前端发到网络上的原始字节:\n")
    print(raw.decode("utf-8"))
    print("\n拆开看:\n")
    print("  POST /chat/stream HTTP/1.1   ← 请求行:方法 + 路径 + 协议版本")
    print("  Host: ...                    ← 请求头(键值对)")
    print("  Content-Type: application/json ← 告诉服务器 body 是 JSON")
    print("  Content-Length: N            ← body 有多少字节")
    print("  (空行)                       ← 头结束的标志")
    print("  {json body}                  ← 真正的数据")
    print("\n  → 关键:请求体是一个 JSON 字符串,里面装着你要说的话。")


# ============================================================
# 幕 2:迷你 SSE 服务器(两种实现,看并发差别)
# ============================================================

async def handle_connection(reader, writer, mode: str):
    """一个客户端的处理协程。

    mode="async":  用 await asyncio.sleep —— 睡的时候让出事件循环,别人能跑
    mode="block":  用 time.sleep        —— 真·阻塞线程,事件循环被占死
    """
    # 读请求(第一行 + 所有头,教学版忽略内容)
    await reader.readline()
    while True:
        line = await reader.readline()
        if line in (b"\r\n", b"\n", b""):
            break

    # 响应头:告诉浏览器这是 SSE,连接保持打开,内容按块推
    writer.write(
        b"HTTP/1.1 200 OK\r\n"
        b"Content-Type: text/event-stream\r\n"   # ← SSE 的标记
        b"Cache-Control: no-cache\r\n"
        b"\r\n"
    )
    await writer.drain()

    # 推 5 块,每块间隔 0.5 秒 —— 模拟模型"边想边说"
    for i in range(5):
        payload = json.dumps({"token": f"第{i+1}块"})
        writer.write(f"data: {payload}\n\n".encode("utf-8"))
        await writer.drain()
        if mode == "async":
            await asyncio.sleep(0.5)   # 让出:事件循环去服务别的连接
        else:
            time.sleep(0.5)            # 占死:整个事件循环睡死 0.5s

    writer.close()
    await writer.wait_closed()


def make_server(mode: str, port: int):
    async def _run():
        return await asyncio.start_server(
            lambda r, w: handle_connection(r, w, mode),
            "127.0.0.1", port,
        )
    return _run


async def client(name: str, port: int, timeline: list):
    """一个客户端:连上,发请求,逐块读,记录每块到达时刻。"""
    t0 = timeline[0]
    reader, writer = await asyncio.open_connection("127.0.0.1", port)

    # 发请求(手写 HTTP,见幕 1)
    writer.write(
        b"GET /stream HTTP/1.1\r\nHost: x\r\n\r\n"
    )
    await writer.drain()

    # 跳过响应头(读到空行)
    while True:
        line = await reader.readline()
        if line in (b"\r\n", b"\n", b""):
            break
        if line.startswith(b"HTTP/"):
            timeline.append((time.time() - t0, f"{name} 收到响应头: {line.decode().strip()}"))

    # 读 data: 块
    while True:
        line = await reader.readline()
        if not line:
            break
        timeline.append((time.time() - t0, f"{name} 收到 {line.decode().strip()}"))

    timeline.append((time.time() - t0, f"{name} 连接关闭"))
    writer.close()


def run_timeline(mode: str, port: int):
    """起服务器,两个客户端并发连,打印时间线。"""
    print(f"\n--- 迷你 SSE 服务器(mode={mode}) ---")
    print("   两个客户端 A、B 几乎同时连接。A 先到,B 后到。\n")

    async def _main():
        server = await make_server(mode, port)()
        t0 = time.time()
        timeline = [t0]
        # 两个客户端并发启动(A 先 0.05s)
        await asyncio.gather(
            client("A", port, timeline),
            asyncio.sleep(0.05),
            client("B", port, timeline),
        )
        server.close()
        await server.wait_closed()

        print(f"   时间线(秒,自 A 连接起):")
        for ts, msg in timeline[1:]:
            print(f"     [{ts:5.2f}s] {msg}")

    asyncio.run(_main())


# ============================================================
# 幕 3:原始字节(客户端视角的响应)
# ============================================================

def act3_raw_bytes():
    print("=" * 70)
    print("幕 3 · 客户端实际看到的原始字节(响应)")
    print("=" * 70)
    print("""
HTTP/1.1 200 OK                          ← 普通 HTTP 响应!
Content-Type: text/event-stream          ← 只是头不一样 + 体不一次给完
Cache-Control: no-cache

data: {"token": "第1块"}                  ← 一个"事件" = data: + JSON + 空行
                                         (空行)
data: {"token": "第2块"}                  ← 服务器慢慢推,连接保持开着
                                         (空行)
data: {"token": "第3块"}
                                         (空行)
... 5 块推完,服务器关连接,浏览器知道"说完了"

  → SSE 不是新协议,就是普通 HTTP:
    响应头先到(200),body 像水龙头一样一块一块流,
    流之间的空隙连接不关,服务器有货就推。
    客户端(fetch + reader)读一行处理一行,边收边显示 —— 这就是"打字机效果"。
""")


# ============================================================
# main
# ============================================================

if __name__ == "__main__":
    act1_show_post_request()
    act3_raw_bytes()
    run_timeline("async", 8911)   # 修复后:await asyncio.sleep → 并发
    run_timeline("block", 8912)   # 修复前:time.sleep → 占死
    print("\n教学实验完。")
