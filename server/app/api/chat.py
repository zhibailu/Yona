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
from pydantic import BaseModel

from .. import engine

router = APIRouter()


class ChatRequest(BaseModel):
    session_id: str | None = None
    message: str = ""
    # ⏳ 占位(行为上收了不读;2026-09 冻结区,补标注)——
    #   ① 现状:本字段在 server 侧**没有任何读取方**(grep `sensory` 的 .py 命中
    #      只有这一行);而前端**也不在发**:`_sensoryPayload()`
    #      (static/app-objects-sensory.js 的 `_sensoryPayload()`)已无调用点,`sendMessage()` 的
    #      `overrideSensory` 形参(static/app-messages.js 的 `sendMessage()`)声明了从不使用,
    #      POST body(同文件 `sendMessage()` 里那个 `body: JSON.stringify(...)`)里没有这一项 —— 即收发两头都已是死的,
    #      schema 位是唯一活物。
    #   ② 为什么留着:感官是**记录在案的冻结区**(后端无 /sensory/* 端点,UI 入口
    #      已撤,见 static/app-objects-sensory.js 头部与 docs/tasks/RULES.md:13
    #      第 4 条「冻结区(sensory/objects/actions)不进内核」);
    #      schema 已固化,留着不产生任何行为,删了将来接回要连前端一起改。
    #   ③ 什么时候动:接回感官那天(旧 D:\MyProject\Yona\src\sensory)—— 后端加
    #      /sensory/* 端点并让内核把 visual/voice 真正转成消息内容;届时改
    #      server/app/api/(新模块)+ static/app-objects-sensory.js
    #      + static/app-messages.js(把 payload 真接回 body)。
    #   ④ 删它的前置条件:先摘掉前端那三处(两个函数 + 形参链),否则会留下一条
    #      "发了没人收 / 收了没人发"的假通道 —— 这正是 /bg-position 踩过的坑
    #      (media.py:session_id 被 pydantic 静默丢弃,200 且查不出来)。
    sensory: dict | None = None
    # ---- 产品旋钮(2026-09 真接线;见 _run 内注释)----
    # model:同端点换模型 id。**真正生效的校验**在 `engine.merge_turn_settings`
    # 的 `available_models` 形参(判定:`if available_models and model not in
    # available_models: model = default_model`),由 `resolve_turn_settings` 传
    # `tuple(_models)` 进来 —— 不在列表内就回默认模型。别再指向旧函数名
    # (那个只做同样判断的旧函数已作为零调用死代码清掉)。
    model: str | None = None  # ✅ 同端点换模型 id(校验落点见上)
    temperature: float | None = None  # ✅ 每轮覆盖,None = params 默认 0.9
    system_prompt: str | None = None  # ✅ 留空=旗舰人格;填写=本轮起覆盖
    max_rounds: int | None = None  # ✅ 上下文窗口:保留最近 N 轮;0=全量
    # ---- 占位(不接逻辑,防 schema 反复改;2026-09 补全标注)----
    #   ① 现状:收了不读 —— 输出上限由服务端常量定死(server/params.py 的
    #      `LLM_OUTPUT_MAX_TOKENS = 4096`),本字段进不了任何调用;前端已不再发送
    #      (static/app-core.js 与 static/app-messages.js 都注明了"不再发送")。
    #   ② 为什么留着:schema 已固化(code 里明写"防 schema 反复改"),留着的成本
    #      只是多一个永远为 None 的字段。
    #   ③ 什么时候动:真要放开"客户端调输出上限"那天 —— 改 server/app/api/chat.py
    #      (`_run` 里把它并进 resolve_turn_settings/LLM 调用)+ server/params.py;
    #      前端 static/app-core.js 的 getSettings() 要把它加回 body。
    #   ④ 删它的前置条件:前端 getSettings()/body 里确认无此键(**现在已满足**),
    #      且确认没有外部客户端在用这个端点(本地单用户 = 满足)。
    max_tokens: int | None = None  # ⏳ 输出上限固定 4096(params),客户端字段仅占位
    #   ① 现状:收了不读(同 max_tokens);② 为什么留着:摘要压缩 = 未来 compact
    #      (docs/decisions/DESIGN.md §9「未来:compact(压缩)怎么做」),schema 先占位;
    #   ③ 什么时候动:compact 落地时 —— 改 core/session_log.py 的 `replace`(原语已
    #      在)+ server/app/api/chat.py + 记忆语义(`core/memory.py` 那条"compact
    #      是派生物、先进不进记忆"的注释,要一起定);④ 删它的前置条件:确认不做
    #      UI 侧开关(docs/tasks/PROGRESS.md 那条"摘要开关置灰占位"一并撤)。
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

    # ⚠️ 不在这里加载日志!必须等拿到引擎锁之后再 load —— 见 _run() 里的说明。

    # 真正的流式:后台线程跑 run_turn,on_chunk 实时投递到队列,
    # SSE 生成器逐条取出转发 —— 与内核 on_chunk 回调直连。
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()

    def _put(item) -> None:
        """线程 → 事件循环:非阻塞投递(loop 关闭边缘忽略)。

        直接当 on_chunk 传给内核:回调契约是 `Callable[[dict], None]`
        (`core/loop.py` 的 `run_turn`/`_step` 形参 `on_chunk`,调用点 `cb(chunk)`)
        —— 一个位置参数完全对得上,原来外面套的那层
        `def on_chunk(chunk): _put(chunk)` 纯转发已删(删前 grep:全仓只此一处定义、
        只此一处被 run_turn 使用)。
        """
        try:
            loop.call_soon_threadsafe(queue.put_nowait, item)
        except RuntimeError:
            pass  # 事件循环已关(进程退出边缘)

    def _run():
        try:
            # 排队(2026-09-17):不再抢锁,而是把这一轮塞进 turn 队列 ——
            # **user 优先级**(队列里数值小的先出),你一发消息就排到所有
            # 自走/补写前面("已经解绑的会话来了 user 请求,其余靠后被插队")。
            # 前面还有东西就先发 busy 帧,UI 立即提示,不是静默干等。
            t_wait0 = time.time()
            if engine.turn_is_busy():
                _put({"kind": "busy"})
                engine._live("busy:引擎正忙(她在忙别的事),消息排队…")

            def _on_wait():
                engine._live(f"  …排队中 {time.time() - t_wait0:.1f}s")

            def _job():
                # 日志**必须**在"排到我"之后才加载(2026-09-17 修):
                # store.load_log 无缓存 —— 每次读盘返回**新对象**;而写盘是
                # **整体覆盖**。加载早于排队 = 拿一份旧副本去覆盖别人等待期间
                # 写的东西:
                #   * 补写跑到一半你发消息 → 你这份是旧副本 → 存盘把补写抹掉;
                #   * 连发两条 → 第二条读盘时第一条还没落盘 → 她看不到上一条,
                #     存盘时还把第一条覆盖掉。
                # 见 docs/pitfalls/HISTORY.md §四。
                log = engine._session_log(sid)
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
                    message, source="user", log=log, on_chunk=_put,
                    temperature=eff["temperature"],
                    model=eff["model"],
                    max_rounds=eff["max_rounds"],
                    system_prompt=eff["system_prompt"],
                )
                # save_log 已自带 updated_at(store._save_log 落完日志就重写
                # meta.updated_at,与 touch_session 做的是同一件事),所以后面
                # 原那句 engine._store.touch_session(sid) 是重复的读+写,已删。
                engine._store.save_log(sid, log)

            t_turn0 = time.time()
            # 优先级**看目标卡**(2026-09-17 拍板):还没补完 → 排在它的补写后面;
            # 已补完 → 插到所有未解绑补写的前面。
            engine._submit_turn(
                _job,
                priority=engine.user_turn_priority(sid),
                on_wait=_on_wait,
                # 这一轮是**哪张卡** —— recall 靠它翻对卡(不传 = 翻不到记忆)
                sid=sid,
            )
            if time.time() - t_wait0 > 0.5:
                engine._live(f"拿到引擎,排队共 {time.time() - t_wait0:.1f}s,开始回复")
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
