"""Yona 服务层 · 观测视图(view)

thin router 拆分:workspace(桌面)/ agent-feed(内心活动)/ runtime/status
这类端点不做任何"动作",只是**从事件日志投影出给人看的数据** —— 纯读取。

核心思想(VISION 决策 2/3):状态与行为都从事件日志派生,不另存。
- 动作轨迹 = 所有日志里的 tool/call + tool/result(她无论在哪触发,
  陪聊轮还是独处轮,动手了就该在轨迹里可见 —— 观测优先)
- 内心活动 = 生活日志(_life)里 source=self 轮的自语

这些投影函数是纯函数(吃 store/日志),可以脱离 HTTP 单测。
"""

from __future__ import annotations

import asyncio
import json
import time

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from .. import engine

router = APIRouter()


def _sse(obj: dict) -> str:
    """把一个对象变成一条 SSE 帧:`data: {json}\n\n`(浏览器按帧解析)。"""
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


# ---------- 投影辅助(纯函数,可单测) ----------

def _fmt_time(ts: float) -> str:
    """给 UI 的时间:日期 + 时:分(不带秒)。

    UI 显示用 (created_at).slice(-5) 切出 "HH:MM";若带秒会切成 "MM:SS",
    造成 "22:37" 看起来像 22 点(实际是 11:22:37 的分秒)。
    """
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))


def _text_of(content) -> str:
    """assistant 消息 content(blocks 或字符串)→ 纯文本。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            b.get("text", "") for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        )
    return ""


def _first_arg_text(arguments: str) -> str:
    """tool/call 参数 JSON → 第一个值(当动作轨迹标题,如换装目标)。"""
    try:
        obj = json.loads(arguments) if arguments else {}
        if isinstance(obj, dict) and obj:
            key = next(iter(obj))
            return f"{key}: {obj[key]}"[:60]
    except Exception:  # noqa: BLE001
        pass
    return ""


def all_action_trails() -> list[dict]:
    """动作轨迹 = 所有日志(会话 + 生活)里的 tool/call + result。"""
    trails: list[dict] = []
    log_ids = [s["id"] for s in engine._store.list_sessions()] \
        + [engine.LIFE_SESSION_ID]
    for lid in log_ids:
        log = engine._store.load_log(lid)
        for e in log.events:
            if e.type == "tool/call":
                trails.append({
                    "action": e.data.get("name", ""),
                    "title": _first_arg_text(e.data.get("arguments", "")),
                    "text": "",
                    "created_at": _fmt_time(e.time),
                })
            elif e.type == "tool/result":
                text = _text_of(e.data.get("content"))[:100]
                for item in reversed(trails):
                    if item["action"] and not item.get("text"):
                        item["text"] = text
                        break
    return trails


def self_talks() -> list[dict]:
    """内心活动 = 生活日志里自走轮的自语(她独处时在想什么)。"""
    log = engine._store.load_log(engine.LIFE_SESSION_ID)
    self_turns = {
        e.data["turn"] for e in log.events
        if e.type == "turn/start" and e.data.get("source") == "self"
    }
    out: list[dict] = []
    for e in log.events:
        if e.type == "assistant/message" and e.data.get("turn") in self_turns:
            text = _text_of(e.data.get("content"))
            if text.strip():
                out.append({
                    "text": text.strip()[:200],
                    "created_at": _fmt_time(e.time),
                })
    return out


# ---------- 端点(纯读取投影) ----------

@router.get("/workspace")
async def get_workspace(session_id: str | None = None, limit: int = 18):
    """桌面工作区:物件 = 空占位;动作轨迹 = 全日志 tool 派生;内心活动 = 自走自语。"""
    trails = all_action_trails()
    trails.sort(key=lambda a: a["created_at"], reverse=True)
    actions = [
        {"action": x["action"], "title": x["title"], "summary": x["text"],
         "created_at": x["created_at"]}
        for x in trails
    ]
    talks = self_talks()
    talks.sort(key=lambda ev: ev["created_at"], reverse=True)
    events = [
        {"created_at": x["created_at"], "content": x["text"]} for x in talks
    ]
    self_turn_count = sum(
        1 for e in engine._store.load_log(engine.LIFE_SESSION_ID).events
        if e.type == "turn/start" and e.data.get("source") == "self"
    )
    return {
        "objects": [],
        "actions": actions[:limit],
        "events": events[: min(limit, 20)],
        "autonomy": {"cycles": self_turn_count},
        "runtime": {"model_calls": {}, "post_turn": {}},
        "summary": {
            "object_counts": {},
            "action_count": len(actions),
            "life_event_count": len(events),
            "latest_title": "",
            "latest_type": "",
        },
        "pruned": 0,
        "pruned_documents": 0,
    }


@router.get("/objects")
async def get_objects(limit: int = 18):
    return {"objects": []}


@router.get("/admin/agent-feed")
async def get_agent_feed(limit: int = 10):
    """内心活动 = 生活日志里的自走轮自语。"""
    talks = self_talks()
    talks.sort(key=lambda ev: ev["created_at"], reverse=True)
    events = [
        {"created_at": x["created_at"], "content": x["text"]} for x in talks
    ]
    return {"events": events[:limit], "mood": None}


@router.get("/admin/llm-log")
async def get_llm_log():
    """LLM 调用调试日志:一次性快照(UI 打开面板时拉历史,不用轮询)。

    实时更新走 /admin/llm-log/stream(SSE 推送):有 LLM 调用才推,
    没有调用 = 零流量 —— 不做"每 2s 盲轮询拿空"这种蠢事。
    """
    return {"log": engine.llm_log_snapshot()}


@router.get("/admin/llm-log/stream")
async def llm_log_stream():
    """LLM 调用日志推送(SSE 长连接)。

    engine._TracingLLM 每次真实 LLM 调用 → _llm_log_append → 广播到所有
    订阅队列(线程 → 事件循环,同 chat SSE 的桥);这里逐条转发给浏览器。
    连接挂起期间没有任何周期请求 —— 有调用才有帧。
    """

    async def _stream():
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()
        engine.subscribe_llm_log(loop, queue)
        try:
            while True:
                entry = await queue.get()  # 真挂起:没新日志就静默等
                yield _sse(entry)
        finally:
            engine.unsubscribe_llm_log(loop, queue)

    return StreamingResponse(_stream(), media_type="text/event-stream")


@router.get("/runtime/status")
async def get_runtime_status():
    """运行时状态:心跳状态 + 引擎可用性(供 UI/诊断)。"""
    if engine._heartbeat is None:
        hb_status = {"running": False, "cycles": 0}
    else:
        s = engine._heartbeat.status()
        hb_status = {
            "running": s["running"],
            "cycles": s["cycles"],
            "last_wake_at": s["last_wake_at"],
            "busy_until": s["busy_until"],
            "last": s["last"].reason if s["last"] else None,
        }
    return {
        "heartbeat": hb_status,
        "engine": engine._loop is not None,
        "model_calls": {},
        "post_turn": {},
        "tts": {},
    }
