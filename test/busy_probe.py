"""busy 链路验证:两条消息几乎同时发,第二条应收到 busy 帧。"""
import json
import sys
import time
import urllib.request

API = "http://127.0.0.1:8001"


def _req(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(API + path, data=data, method=method)
    r.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(r) as resp:
        return json.loads(resp.read().decode())


def stream_chat(session_id, text, out):
    body = json.dumps({"session_id": session_id, "message": text}).encode()
    r = urllib.request.Request(API + "/chat/stream", data=body, method="POST")
    r.add_header("Content-Type", "application/json")
    frames = []
    with urllib.request.urlopen(r) as resp:
        buf = b""
        while True:
            chunk = resp.read(256)
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                s = line.decode("utf-8", "replace").strip()
                if s.startswith("data: "):
                    try:
                        frames.append(json.loads(s[6:]))
                    except Exception:
                        pass
    out.append(frames)


sid = _req("POST", "/sessions", {"title": "busy-probe"})["session_id"]
out_a, out_b = [], []
import threading
ta = threading.Thread(target=stream_chat, args=(sid, "你好,跟我说说今天过得怎么样(说详细点)", out_a))
tb = threading.Thread(target=stream_chat, args=(sid, "第二条消息", out_b))
ta.start()
time.sleep(0.15)
tb.start()
ta.join()
tb.join()

for name, frames in (("A(first)", out_a[0]), ("B(second)", out_b[0])):
    kinds = []
    for f in frames:
        if f.get("busy"):
            kinds.append("BUSY:" + f.get("busy_text", "")[:30])
        elif f.get("token"):
            kinds.append("token")
        elif f.get("done"):
            kinds.append("done")
        elif f.get("error"):
            kinds.append("ERROR:" + f.get("error", "")[:60])
    print(name, "->", kinds)
