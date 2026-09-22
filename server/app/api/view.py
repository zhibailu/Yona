"""Yona 服务层 · 观测视图(view)

thin router 拆分:workspace(桌面)/ life-events(内心活动)/ runtime/status
这类端点不做任何"动作",只是**从事件日志投影出给人看的数据** —— 纯读取。

⛔ **本模块的面板:用户判定"不留"(2026-09-22 10:45),别当活产品面看。**
   用户原话:「你要分清楚,我发现了,你之前说的动过的部分其实都是你自己写文档的时候
   顺手的,**并不是我真的操刀过这一盘**,所以确实文档里有,但并非是我想让它有才有的,
   所以**项1 应该是不留**且**文档有擅自成分,得改**。」

   事实核对(与用户判断一致的部分):这两个面板的**全部接法与投影实现**都是 baseline
   (`2903b2a chore: baseline`)搬进来的旧 UI 配套,**不是 rewrite 里设计出来的产品面**。
   我上一轮把它们说成"用户操刀过(因为 `8138640` 的'内心跟随当前卡')"是**过度解读** ——
   那一条改的是**目标卡选择**(自走/补写打哪张卡,是真的产品决定),不是"要不要有这个面板";
   把它们当成"rewrite 核心展示"是我写文档时**擅自**加的分量,已按用户要求改正。

   **现状:标注,不摘**(与 `/autonomy/pulse` 同款处理)。端点仍通、UI 仍在。
   **本模块剩下的唯一真消费者判断**:`test/test_view_trails.py` 钉的是
   `all_action_trails()` 的**配对规则**(按 `tool_call_id`,不是找最近一条)—— 那条是
   2026-09-16 修并发工具后的真 bug 修复,与"面板留不留"无关,别一起删。

   终局(用户定,**别人不要自己动**):
     ① 整个摘掉 —— 删本模块的 workspace / life-events 两个端点 + 两个 UI pane
        + `app-presets.js` 里那段 `/admin/life-events` 拉取(**预设 CRUD 要留**,
        那是真的产品功能);
     ② 保留 —— 那要由用户重新拍一次"这是产品面",而不是像现在这样靠文档擅自升格。

核心思想(VISION 决策 2/3):状态与行为都从事件日志派生,不另存。
- 动作轨迹 = 所有卡片日志里的 tool/call + tool/result(观测优先,全局)
- 内心活动 = **某张卡**的 chat.log 里 source=self 轮的生活事件

这些投影函数是纯函数(吃 store/日志),可以脱离 HTTP 单测。
"""

from __future__ import annotations

import asyncio
import json
import time

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from character import personas as personas_mod
from core.session_log import strip_copied_prefix

from .. import engine

router = APIRouter()


def _sse(obj: dict) -> str:
    """把一个对象变成一条 SSE 帧:`data: {json}\n\n`(浏览器按帧解析)。"""
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


def _target_card_id(session_id: str | None) -> str:
    """观测缺省目标卡:显式给 → 用它;没给 → Yona 常驻旗舰。"""
    if session_id:
        return session_id
    return engine._store.flagship_session_id()


# ---------- 投影辅助(纯函数,可单测) ----------

def _fmt_time(ts: float) -> str:
    """给 UI 的时间:日期 + 时:分(不带秒)。

    UI 显示用 (created_at).slice(-5) 切出 "HH:MM";若带秒会切成 "MM:SS",
    造成 "22:37" 看起来像 22 点(实际是 11:22:37 的分秒)。

    ⚠️ 与 `server/store.py:_fmt_time` **逐字相同**(连这段解释都一样)——
    同一份契约的两份实现,改一处必须改另一处。
    没做复用是因为两边都是下划线私有名,跨模块 import 私有名在本仓无先例
    (待拍板:真要去重就并成一处,别靠 import 私有名)。
    """
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))


def _text_of(content) -> str:
    """assistant 消息 content(blocks 或字符串)→ 纯文本。

    ⚠️ 与 `server/store.py:_blocks_text` **逐字相同** —— 同一语义的两份实现,
    改一处必须改另一处。注意这只是"同一族"里最像的一对:同样干这事的还有
    `core/memory.py:_blocks_text`、`core/openai_compat.py:_text_of`、
    `turn_lab.py`、`prompt_lab/tool_recall.py`,那几份行为细节各有出入
    (比如要不要滤空串、非 list 怎么兜底)—— 真要收编得连它们一起定,
    别只并这两份就以为完事。
    """
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
    """动作轨迹 = 所有卡片(含 Yona 与各角色卡)日志里的 tool/call + result。

    配对按 **tool_call_id**,不是"往上找最近一条还没配对的 call" ——
    2026-09-16 内核把一步内的工具改成并发执行后,日志形状从
    call,result,call,result 变成 call,call,result,result,
    "找最近一条"会把结果配到**错的那个 call** 上(后发的 call 先被填)。
    id 是日志里本来就有的,按它配才是准的。
    (`test/test_view_trails.py` 就钉着这一条 —— 那是真 bug 修复,**别跟面板一起删**。)

    ⛔ **本函数服务的是"不留"的面板(2026-09-22 10:45 用户判定)**,见模块头。

    ⚠️ 这里**不跳** `shadowed_seqs()` —— 与 core 的投影规则不一致是**已知且已被
    用户接受**的状态,不要再"顺手修":
      · core 的真投影(`core/session_log.py` 的 `derive_messages`、`store._messages_view`)
        会跳 shadow,所以**聊天窗口**里删掉的东西会消失;
      · 这个面板不跳,所以删掉的 tool 痕迹**还挂在上面**。
    **2026-09-22 我先按"四个口径收齐"修过一版,用户随后判定这个面板不留
    → 该修改已撤回**(理由:不给一个待砍的面板做产品级收口,那属于用户没要的改动)。
    面板真要留下来那天,再按 core 那条规则补上跳 shadow,并同步改这句注释。
    """
    trails: list[dict] = []
    log_ids = [s["id"] for s in engine._store.list_sessions()]
    for lid in log_ids:
        log = engine._store.load_log(lid)
        by_call_id: dict[str, dict] = {}
        for e in log.events:
            if e.type == "tool/call":
                item = {
                    "action": e.data.get("name", ""),
                    "title": _first_arg_text(e.data.get("arguments", "")),
                    "text": "",
                    "created_at": _fmt_time(e.time),
                }
                trails.append(item)
                call_id = e.data.get("call_id") or ""
                if call_id:
                    by_call_id[call_id] = item
            elif e.type == "tool/result":
                item = by_call_id.get(e.data.get("tool_call_id") or "")
                if item is not None and not item.get("text"):
                    item["text"] = _text_of(e.data.get("content"))[:100]
    return trails


def life_events(session_id: str, log=None) -> list[dict]:
    """内心活动 = 某张卡自己 chat.log 里自走轮的生活事件(它独处时在想什么)。

    ⚠️ 读的是**原生日志文本**,但会剥掉"被模型抄进正文"的那行标记
    (`core.session_log.strip_copied_prefix`,与投影层同一个实现)——
    日志原文一个字不动,只影响这里给 UI 看的东西。

    ⛔ **本函数服务的是"不留"的面板(2026-09-22 10:45 用户判定)**,见模块头。
    它的两个消费方 `get_workspace()` 与 `get_life_events()` 同命运。

    ⚠️ 与 `all_action_trails()` 同款:这里**不跳** `shadowed_seqs()`,删掉的生活事件
    还留在面板上 —— 同样是用户已接受的现状,别"顺手修"(那条修改已按用户判定撤回)。

    log 参数只为省一次读盘(store.load_log 无缓存):调用方已经拿到这张卡的
    log 就传进来,None 时自己 load —— 签名向后兼容,老调用点不用改。
    """
    if log is None:
        log = engine._store.load_log(session_id)
    self_turns = {
        e.data["turn"] for e in log.events
        if e.type == "turn/start" and e.data.get("source") == "self"
    }
    out: list[dict] = []
    for e in log.events:
        if e.type == "assistant/message" and e.data.get("turn") in self_turns:
            text = _text_of(e.data.get("content"))
            text = strip_copied_prefix(text, personas_mod.LIFE_EVENT_PREFIX)
            if text.strip():
                out.append({
                    "text": text.strip()[:200],
                    "created_at": _fmt_time(e.time),
                })
    return out


# ---------- 端点(纯读取投影) ----------

@router.get("/workspace")
async def get_workspace(session_id: str | None = None, limit: int = 18):
    """桌面工作区:动作轨迹 = 全卡片 tool 派生(全局);内心活动 = 该卡的生活事件。

    ⛔ 服务的是"**不留**"的面板(2026-09-22 10:45 用户判定),见模块头。
    摘除时连 `static/app-objects-sensory.js` 的 `/workspace` 拉取
    与 `static/index.html` 的 `object-drawer` 那个 pane 一起处理。
    """
    trails = all_action_trails()
    trails.sort(key=lambda a: a["created_at"], reverse=True)
    actions = [
        {"action": x["action"], "title": x["title"], "summary": x["text"],
         "created_at": x["created_at"]}
        for x in trails
    ]
    card_id = _target_card_id(session_id)
    # 这张卡的 log **只读一次**,life_events 与 self_turn_count 共用 ——
    # store.load_log 无缓存(见 docs/pitfalls/HISTORY.md §四),原来这里为
    # self_turn_count 又整读一遍(同一请求里同一张卡的 log 最坏读 4 遍)。
    card_log = engine._store.load_log(card_id)
    found = life_events(card_id, card_log)
    found.sort(key=lambda ev: ev["created_at"], reverse=True)
    events = [
        {"created_at": x["created_at"], "content": x["text"]} for x in found
    ]
    self_turn_count = sum(
        1 for e in card_log.events
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
    """物件舞台 —— **冻结合同位:恒返空是对的,别当死代码删**(2026-09 标注)。

    - 现状:前端**真在调**(`static/app-objects-sensory.js` 拉
      `/objects?limit=18`),所以 URL 契约必须活着;但物件舞台已撤
      (`static/index.html` 里桌面物件 pane 已删),恒空 = 正确行为。
    - 为什么留着:冻结区(sensory/objects/actions,见 `docs/tasks/RULES.md:13`
      第 4 条与 `docs/STRUCTURE.md:66` 那张表:旧 `src/objects+actions` 冻结)。
      URL 契约先留着,免得接回产物层时前端还要改一遍。
    - 什么时候动:接回产物层(物件真产出对象)那天才填这里 —— 届时前端 pane
      也要一起加回来,只改后端会得到一个"有数据但没人显示"的端点。
    - 删它的前置条件:先摘掉 `static/app-objects-sensory.js` 里的 fetch 调用点,
      否则前端会开始吃 404。
    """
    return {"objects": []}


@router.get("/admin/life-events")
async def get_life_events(session_id: str | None = None, limit: int = 10):
    """内心活动 = 某张卡自己日志里的自走轮的生活事件(UI 跟随当前会话)。

    ⛔ 服务的是"**不留**"的面板(2026-09-22 10:45 用户判定),见模块头。
    摘除时连 `static/app-presets.js` 里那段 `/admin/life-events` 拉取
    与 `static/index.html` 的「生活事件」pane 一起处理 ——
    ⚠️ 但 `app-presets.js` 的**预设 CRUD 要留**(那是真的产品功能)。
    """
    found = life_events(_target_card_id(session_id))
    found.sort(key=lambda ev: ev["created_at"], reverse=True)
    events = [
        {"created_at": x["created_at"], "content": x["text"]} for x in found
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
    """运行时状态:心跳状态 + 引擎可用性(供 UI/诊断)。

    ⏳ 占位(2026-09 清点):**当前全仓零调用方** —— 前端 `static/*.js` 与
    `test/` 里都没有打这个 URL(grep `runtime/status` 只命中本文件),
    而同类端点 `/workspace`、`/admin/life-events`、`/admin/llm-log` 都有前端调用。
    它是诊断口:要么接进 UI 诊断面板,要么删。

    ⚠️ 删它零影响(没有任何调用方),但代价是 `engine._heartbeat.status()`
    (`core/heartbeat.py` 的 `Heartbeat.status()`)会失去唯一的消费者 ——
    心跳跑没跑就没地方看了。所以**先留着**,等诊断面板落地时一并定去留。
    """
    if engine._heartbeat is None:
        hb_status = {"running": False, "cycles": 0}
    else:
        s = engine._heartbeat.status()
        hb_status = {
            "running": s["running"],
            "cycles": s["cycles"],
            "last_wake_at": s["last_wake_at"],
            "last": s["last"].reason if s["last"] else None,
        }
    return {
        "heartbeat": hb_status,
        "engine": engine._loop is not None,
        "model_calls": {},
        "post_turn": {},
        "tts": {},
    }
