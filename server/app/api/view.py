"""Yona 服务层 · 观测视图(view)

thin router 拆分:runtime/status(诊断)+ admin/llm-log(LLM 调用日志)+ objects(冻结合同位)。
这类端点不做任何"动作",只是**从事件日志/引擎状态读出给人看的数据** —— 纯读取。

⛔ **2026-09-22 11:20:原来这里还住着两个观测面板,已按用户拍板摘除 —— 别再长回来。**
   摘掉的是:
     · `GET /workspace`(**桌面工作区**:动作轨迹 + 内心活动 + 自走轮数)
     · `GET /admin/life-events`(**内心活动面板**)
     · 以及它们用到的四个私有投影/格式化 helper(`all_action_trails` /
       `life_events` / `_target_card_id` / `_fmt_time` / `_text_of` / `_first_arg_text`)

   **为什么摘**(用户 2026-09-22 10:45 → 11:20 两轮):
   这两个面板的接法、投影实现、前端 pane **全部是 baseline(`2903b2a`)搬进来的旧 UI
   配套**,不是 rewrite 里设计出来的产品面。用户原话:
   「你要分清楚,我发现了,你之前说的动过的部分其实都是**你自己写文档的时候顺手的**,
    **并不是我真的操刀过这一盘**,所以确实文档里有,但并非是我想让它有才有的,
    所以项1 应该是不留」→ 追问后确认:**"3 摘掉吧那就"**。
   ⚠️ 我上一轮把它们说成"用户操刀过"(拿 `8138640` 的"内心跟随当前卡"当证据)是
   **过度解读** —— 那一条改的是**目标卡选择**(自走/补写打哪张卡,那个确实是用户拍的),
   不是"要不要有这个面板"。把它们写成"rewrite 核心展示"是文档里的擅自升格,已全部改正。

   **连带摘掉的东西**(一起走的,别只找后端):
     · `static/index.html` 的 `#inner-life`(生活事件)与 `#object-drawer`(Yona 的工作区)
       两个 pane —— 顺带带走了里面那个"脉冲"按钮(它本来就被判未启用/待砍);
     · `static/app-presets.js` 的 `_refreshInnerLife()` 与它的定时器
       (**⚠️ 预设 CRUD 留在原处 —— 那是真的产品功能,别跟着删**);
     · `static/app-objects-sensory.js` 整个文件 → 移进 `static/_unused/`
       (它只剩工作区那一段还活着,另两段是早已冻结的感官/物件舞台);
     · `test/test_view_trails.py` —— 它钉的是 `all_action_trails()` 的配对规则
       (按 `tool_call_id` 而非"找最近一条",2026-09-16 修并发工具的真 bug)。
       **投影函数没了,那条守卫没有对象了**,所以随函数一起走。
       ⚠️ 将来若要把面板接回来:配对规则**必须一起接回来**,不能只抄个投影循环
       —— 那个坑的形状是"两个结果互换,不报错、只是安静地配错"。
       恢复入口:`git log --diff-filter=D -- test/test_view_trails.py` 与
       `git show <commit>^:server/app/api/view.py`。

核心思想(VISION 决策 2/3):状态与行为都从事件日志派生,不另存。
"""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from .. import engine

router = APIRouter()


def _sse(obj: dict) -> str:
    """把一个对象变成一条 SSE 帧:`data: {json}\n\n`(浏览器按帧解析)。"""
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


@router.get("/objects")
async def get_objects(limit: int = 18):
    """物件舞台 —— **冻结合同位:恒返空是对的,别当死代码删**(2026-09 标注)。

    - 现状:**前端已经没有任何调用方了** —— 原来拉它的是
      `static/app-objects-sensory.js` 的 `_refreshObjects()`,而那个文件
      2026-09-22 已随工作区面板一起移进 `static/_unused/`。
      恒空 = 正确行为(物件舞台已撤)。
    - 为什么还留着:冻结区(sensory/objects/actions,见 `docs/tasks/RULES.md` 第 4 条与
      `docs/STRUCTURE.md` 那张冻结表:旧 `src/objects+actions` 冻结)。
      **URL 契约先留着**,免得接回产物层时前端还要改一遍。
    - 什么时候动:接回产物层(物件真产出对象)那天才填这里 —— 届时前端 pane
      也要一起加回来,只改后端会得到一个"有数据但没人显示"的端点。
    - 删它的前置条件:**已经满足**(前端调用点已随 `_unused` 移走)。
      所以它现在纯粹是"契约占位";真要清理,删这个函数即可,不会再有人吃 404。
    """
    return {"objects": []}


@router.get("/admin/llm-log")
async def get_llm_log():
    """LLM 调用调试日志:一次性快照(UI 打开面板时拉历史,不用轮询)。

    实时更新走 /admin/llm-log/stream(SSE 推送):有 LLM 调用才推,
    没有调用 = 零流量 —— 不做"每 2s 盲轮询拿空"这种蠢事。
    消费方:`static/app-media-debug.js`(面板展开才订阅)。
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
    `test/` 里都没有打这个 URL(grep `runtime/status` 只命中本文件)。
    它是诊断口:要么接进 UI 诊断面板,要么删。

    ⚠️ 删它零影响(没有任何调用方),但代价是 `engine._heartbeat.status()`
    (`core/heartbeat.py` 的 `Heartbeat.status()`)会失去唯一的消费者 ——
    心跳跑没跑就没地方看了。所以**先留着**,等诊断面板落地时一并定去留。

    (2026-09-22 注:同类的"诊断口"原来还有 `/workspace` 与 `/admin/life-events`,
    那两个已有结论 —— 摘了,见模块头。本端点不跟着走:它是**纯引擎状态**,
    不是"从日志投影出的面板",没有"用户没拍过"的问题。)
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
