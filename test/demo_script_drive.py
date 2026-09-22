"""Demo 剧本驱动:按步骤打真实端点,输出原样内容(UTF-8 干净)。

步骤:
 1 建会话 + 聊天(触发工具:换衣服)
 2 busy:两条并发,第二条先收 busy 帧
 3 重启补齐由 server 启动逻辑负责(需要重启进程,脚本外验证)

⚠️ 2026-09-22 11:20:原来还有一幕拉 `GET /admin/life-events`(内心活动)与
`GET /workspace`(动作轨迹)。这两个观测面板已按用户拍板**摘除**(端点一起删),
所以那两幕去掉了 —— 见 `server/app/api/view.py` 的模块头。
"""
import json
import sys
import threading
import time
import urllib.request

API = "http://127.0.0.1:8001"


def req(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(API + path, data=data, method=method)
    r.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(r) as resp:
        return resp.read().decode("utf-8")


def show(title, text):
    print(f"\n===== {title} =====")
    print(text)


def chat(sid, text, out):
    body = json.dumps({"session_id": sid, "message": text}).encode()
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


def pretty_frames(frames):
    parts = []
    for f in frames:
        if f.get("busy"):
            parts.append("[busy] " + f.get("busy_text", ""))
        elif f.get("token"):
            parts.append(f["token"])
        elif f.get("tool_status"):
            parts.append("[" + f["tool_status"] + "]")
        elif f.get("done"):
            parts.append("[done]")
        elif f.get("error"):
            parts.append("[ERROR] " + f["error"])
    return "".join(parts)


# (2026-09-22 11:20:原来这里第 0 幕拉 GET /admin/life-events(内心活动),
#  第 2 幕拉 GET /workspace(动作轨迹)。两个观测面板都已按用户拍板**摘除**
#  (连带端点一起删),再打只会得到 404 —— 所以这两幕删掉,演示从聊天开始。
#  要复原请看 git 历史里本文件的旧版本与 server/app/api/view.py 的模块头。)

# 1) 聊天触发工具
sid = json.loads(req("POST", "/sessions", {"title": "demo-1"}))["session_id"]
out = []
t = threading.Thread(target=chat, args=(sid, "现在几点了?顺便我想换件红卫衣", out))
t.start()
t.join()
show("1. 你说话 —— 聊天流(她调了 change_outfit 工具?)", pretty_frames(out[0]))
show("   会话消息视图", req("GET", f"/sessions/{sid}")[:800])
time.sleep(0.5)
