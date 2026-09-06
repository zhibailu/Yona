"""Yona 服务层 · 聊天 SSE 通道(chat)

thin router 拆分:聊天是"转发 + 缓冲"最典型的端点,单独一个模块。
路由(URL 解析/参数校验)在 main,真正跑 LLM 的流式转发在这。

**这里就是"转发/缓冲"发生的地方,学习时对照看**:
- 用户 POST /chat/stream → FastAPI 调 chat_stream(本文件)
- chat_stream 马上返回一个 `StreamingResponse`(HTTP 200 + text/event-stream)
  —— 连接不关,服务端持续往这条连接"推"事件帧(`data: {...}\n\n`)。
- 真正的 LLM 调用跑在**后台线程**(`threading.Thread`):因为 OpenAI 客户端
  是同步阻塞的,不能占着 FastAPI 的事件循环(会卡死整个服务)。
- 线程产出 → 放进 `asyncio.Queue`(缓冲):
    * 线程侧:`loop.call_soon_threadsafe(queue.put_nowait, item)`
      —— 跨线程安全地把 item 塞回事件循环的队列(不会锁死循环)
    * async 侧:`item = await queue.get()` —— 事件循环真挂起等下一个帧,
      有帧就取出转发给浏览器(逐 token)
- 队列的 None 哨兵 = "流结束":线程跑完往队列放 None,async 侧收到 None
  补发一条 `done` 帧(带上本轮消息的 id)就 return,连接关闭。
- busy 探测:线程启动前**非阻塞抢全局锁**(`acquire(blocking=False)`)。
  抢不到 = 引擎正忙(她在心跳自走),先发 `busy` 帧,UI 提示"她在忙",
  然后阻塞排队等锁 —— 同一时刻她只做一件事,你来了,她忙完就来。
"""

from __future__ import annotations

import asyncio
import json
import threading
import time

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .. import engine

router = APIRouter()


class ChatRequest(BaseModel):
    session_id: str | None = None
    message: str = ""
    sensory: dict | None = None
    # ---- 产品旋钮(2026-09 真接线;见 _run 内注释)----
    model: str | None = None  # ✅ 同端点换模型 id(engine.resolve_model 校验列表内才生效)
    temperature: float | None = None  # ✅ 每轮覆盖,None = params 默认 0.9
    system_prompt: str | None = None  # ✅ 留空=旗舰人格;填写=本轮起覆盖
    max_rounds: int | None = None  # ✅ 上下文窗口:保留最近 N 轮;0=全量
    # ---- 占位(不接逻辑,防 schema 反复改;compact 落地后启用)----
    max_tokens: int | None = None  # ⏳ 输出上限固定 4096(params),客户端字段仅占位
    enable_summarize: bool | None = None  # ⏳ 摘要压缩 = 未来 compact(DESIGN §9)


def _sse(obj: dict) -> str:
    """把一个对象变成一条 SSE 帧:`data: {json}\n\n`(浏览器按帧解析)。"""
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


@router.post("/chat/stream")
async def chat_stream(request: Request, body: ChatRequest):
    if engine._loop is None:
        raise HTTPException(status_code=503, detail="引擎未启动(缺 LLM 配置)")
    sid = body.session_id
    if not sid or engine._store.get_session(sid) is None:
        # 前端总会先 createSession;兜底自动建
        sid = engine._store.create_session()
    message = (body.message or "").strip()
    if not message:
        raise HTTPException(status_code=400, detail="Message is required")

    log = engine._session_log(sid)
    user_seq_before = log.last_seq()

    # 真正的流式:后台线程跑 run_turn,on_chunk 实时投递到队列,
    # SSE 生成器逐条取出转发 —— 与内核 on_chunk 回调直连。
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()

    def _put(item) -> None:
        """线程 → 事件循环:非阻塞投递(loop 关闭边缘忽略)。"""
        try:
            loop.call_soon_threadsafe(queue.put_nowait, item)
        except RuntimeError:
            pass  # 事件循环已关(进程退出边缘)

    def on_chunk(chunk: dict) -> None:
        _put(chunk)

    def _run():
        try:
            # busy 探测:非阻塞抢全局锁。抢不到 = 引擎正忙(心跳自走等),
            # 用户消息会排队 —— 先发 busy 帧,UI 立即提示,不是静默干等。
            if not engine._lock.acquire(blocking=False):
                t_wait0 = time.time()
                _put({"kind": "busy"})
                engine._live("busy:引擎正忙(她在忙别的事),消息排队…")
                # 进度条式等待:分段抢锁,每 0.5s 打一行等待时间(打印台实时现场)
                while not engine._lock.acquire(timeout=0.5):
                    engine._live(f"  …排队中 {time.time() - t_wait0:.1f}s")
                engine._live(f"拿到引擎,排队共 {time.time() - t_wait0:.1f}s,开始回复")
            t_turn0 = time.time()
            try:
                # 产品旋钮(2026-09 真接线 + 任务6 路线 B):
                # - 当轮显式传的 > 会话快照 > 全局默认(engine.resolve_turn_settings)
                # - temperature/max_rounds/system_prompt/model 四件套同一合并链
                # - max_tokens 不上传:输出上限固定 4096(server/params.py);
                #   enable_summarize 同理(schema 占位,等 compact,见 DESIGN §9)
                prompt = (body.system_prompt or "").strip()
                eff = engine.resolve_turn_settings(sid, {
                    "temperature": body.temperature,
                    "max_rounds": body.max_rounds,
                    "system_prompt": prompt or None,
                    "model": body.model,
                })
                engine._loop.run_turn(
                    message, source="user", log=log, on_chunk=on_chunk,
                    temperature=eff["temperature"],
                    model=eff["model"],
                    max_rounds=eff["max_rounds"],
                    system_prompt=eff["system_prompt"],
                )
                engine._store.save_log(sid, log)
                engine._store.touch_session(sid)
            finally:
                engine._lock.release()
            engine._live(f"回复完成,耗时 {time.time() - t_turn0:.1f}s")
        except Exception as exc:  # noqa: BLE001
            _put({"kind": "error", "error": str(exc)})
        finally:
            _put(None)  # 哨兵:流结束

    threading.Thread(target=_run, daemon=True, name="chat-turn").start()

    async def _stream():
        emitted_tool_status = False
        while True:
            item = await queue.get()  # 真 await:不占死事件循环
            if item is None:
                # 轮次收尾:取本轮最后一条 assistant 消息 seq 发 done
                log2 = engine._session_log(sid)
                user_id = None
                assistant_id = None
                for e in log2.events:
                    if e.type == "user/message" and e.data.get("source") == "user":
                        user_id = e.seq
                    if e.type == "assistant/message":
                        assistant_id = e.seq
                yield _sse(
                    {
                        "done": True,
                        "user_msg_id": user_id,
                        "assistant_msg_id": assistant_id,
                    }
                )
                return
            kind = item.get("kind")
            if kind == "error":
                yield _sse({"error": item.get("error", "服务暂时不可用")})
            elif kind == "busy":
                # 她正在忙(心跳自走等),用户消息排队 —— UI 显示提示,不是干等
                yield _sse({
                    "busy": True,
                    "busy_text": "她正在忙别的事,你的消息排在后面——等她忙完马上来。",
                })
            elif kind == "text":
                yield _sse({"token": item["text"]})
            elif kind == "tool_call":
                # UI 契约:tool_status 显示"她在调用工具"
                name = item.get("name", "工具")
                if not emitted_tool_status:
                    yield _sse({"tool_status": f"她正在用 {name} …"})
                    emitted_tool_status = True
            # finish 无输出(等 done 哨兵)

    return StreamingResponse(_stream(), media_type="text/event-stream")
