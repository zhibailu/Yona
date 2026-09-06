"""Yona 新内核 · FastAPI thin router —— 跑: py -m uvicorn server.main:app --port 8000

thin router 拆分(2026-09,布局 B):本文件只留**路由薄壳** —— 每个端点
一两行,真正的业务在应用层(路由只做协议翻译,UI 契约原样保留):

  server/app/engine.py   组合根 + 生活运行时(持有全局单例、start/stop)
  server/app/gate.py     心跳闸门规则(与补写同一原语:概率形状继承 shape)
  server/app/api/*.py    请求域路由:chat(SSE 转发/缓冲/busy)、view(观测投影)、
                         media(图片/背景)—— 同层收子目录
  server/store.py        会话存储(支撑库,测试直连,留包根)
  server/rhythm.py       补写采样算法(§10 连续概率判定;支撑库,留包根)

端点族:
- 会话:sessions CRUD(每会话一个 SessionLog,落盘 data/sessions/)
- 聊天:POST /chat/stream(SSE 逐 token,契约:token/tool_status/busy/done)
- 治理:DELETE /messages/from/{id}(= shadow tail-cut)、PATCH /messages/{id}(= replace)
- 观测:GET /workspace(动作轨迹从日志派生)、GET /admin/agent-feed(内心活动)
- 配置:settings/models/context-sources;预设 CRUD + 模型发现 = server/app/api/config.py
- 杂项:images/bg-position 纯文件存取
"""

from __future__ import annotations

import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .app import engine
from .app.api import chat, config, media, view
from .app.engine import ROOT
from .params import DEFAULT_CONTEXT_ROUNDS, LLM_DEFAULT_TEMPERATURE, LLM_OUTPUT_MAX_TOKENS


# 生命周期

@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        engine.start()
    except RuntimeError as exc:
        print(f"[warn] {exc} —— 聊天/心跳禁用,静态 UI 仍可浏览")
    yield
    engine.stop()


app = FastAPI(title="Yona 2.0 (rewrite)", lifespan=lifespan)

# 子模块路由挂进来(URL 前缀与旧契约一致,include 不改路径)
app.include_router(chat.router)
app.include_router(view.router)
app.include_router(media.router)
app.include_router(config.router)


# 基础:健康 / 设置 / 模型 / 上下文配置

@app.get("/health")
async def health():
    return {"status": "ok", "service": "yona-rewrite"}


@app.get("/models")
async def list_models():
    # 2026-09 连接管理:只列"当前连接端点真正可用"的模型(引擎快照,
    # 由连接向导实测拉通后缓存);未连接 = 空列表,UI 显示首启引导。
    state = engine.llm_state()
    return [
        {"id": m, "name": m, "description": "可用模型"} for m in state["models"]
    ]


@app.get("/settings")
async def get_settings():
    # 2026-09:从这里回的真实 = engine 实际生效值(params.py 唯一来源),
    # UI 显示即真相。temperature 每轮可覆盖;max_tokens 是服务端固定输出上限。
    state = engine.llm_state()
    model = state.get("model")
    return {
        "model": model,
        "temperature": LLM_DEFAULT_TEMPERATURE,
        "max_tokens": LLM_OUTPUT_MAX_TOKENS,
        "top_p": 1.0,
        "configured": state.get("configured", False),
        "models": [
            {"id": m, "name": m, "description": ""} for m in state["models"]
        ],
    }


@app.get("/context/sources")
async def get_context_sources():
    # 2026-09 收紧:这里只回"真生效"的上下文源 —— sliding_window(按轮数,
    # 保留最近 N 轮,loop.run_turn max_rounds 真用)。旧 token_budget /
    # summarize 两行是旧上下文源模型的残留语义(无消费方、compact 未接),
    # 已删 —— 显示即真相,不给假默认(见 DESIGN §9 / STRUCTURE §4)。
    return [
        {"source_id": "sliding_window", "priority": 10, "enabled": True,
         "config": {"max_rounds": DEFAULT_CONTEXT_ROUNDS}},
    ]


# 预设 CRUD 在 server/app/api/config.py(2026-09 用户拍板补全,本文件不再占位)


# 会话 CRUD(存储委托 engine._store)

class SessionCreate(BaseModel):
    title: str = Field("", max_length=200)


class SessionUpdate(BaseModel):
    title: str = Field(..., max_length=200)


@app.post("/sessions")
async def create_session(body: SessionCreate):
    sid = engine._store.create_session(body.title or None)
    return {"session_id": sid, "title": body.title or "新会话"}


@app.get("/sessions")
async def list_sessions():
    return engine._store.list_sessions()


@app.get("/sessions/{session_id}")
async def get_session(session_id: str):
    session = engine._store.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


@app.patch("/sessions/{session_id}")
async def update_session(session_id: str, body: SessionUpdate):
    ok = engine._store.rename_session(session_id, body.title)
    if not ok:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"ok": True}


# ---------- 会话快照(2026-09 任务6:每个会话记住的一组设置) ----------

class SessionSettingsIn(BaseModel):
    settings: dict = Field(default_factory=dict)  # 整体替换;{} = 清空回默认


def _clean_settings(raw: dict) -> dict:
    """白名单 + 类型/范围校验(脏字段静默丢弃,非法值 400)。"""
    clean: dict = {}
    t = raw.get("temperature")
    if t is not None:
        try:
            t = float(t)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="temperature 不是数字")
        if not 0.0 <= t <= 2.0:
            raise HTTPException(status_code=400, detail="temperature 超出 0-2")
        clean["temperature"] = round(t, 2)
    r = raw.get("max_rounds")
    if r is not None:
        try:
            r = int(r)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="max_rounds 不是整数")
        if not 0 <= r <= 40:
            raise HTTPException(status_code=400, detail="max_rounds 超出 0-40")
        clean["max_rounds"] = r  # 0 = 不限制,合法
    sp = raw.get("system_prompt")
    if sp is not None:
        if not isinstance(sp, str) or len(sp) > 4000:
            raise HTTPException(status_code=400, detail="system_prompt 非法或超长")
        sp = sp.strip()
        if sp:
            clean["system_prompt"] = sp  # 空串 = 清掉(旗舰),不落键
    m = raw.get("model")
    if m is not None:
        if not isinstance(m, str) or not m or len(m) > 120:
            raise HTTPException(status_code=400, detail="model 非法")
        # 防呆:连接已配置时必须属于当前端点可用列表(连接没配置时先存着,
        # 合并时 resolve 也会回默认)
        if engine._models and m not in engine._models:
            raise HTTPException(status_code=400,
                                detail=f"模型 {m} 不在当前连接可用列表")
        clean["model"] = m
    return clean


@app.patch("/sessions/{session_id}/settings")
async def update_session_settings(session_id: str, body: SessionSettingsIn):
    """整体替换该会话快照(UI 防抖自动存;{} = 清空回默认)。"""
    clean = _clean_settings(body.settings or {})
    with engine._lock:
        ok = engine._store.set_session_settings(session_id, clean)
    if not ok:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"ok": True, "settings": clean}


@app.delete("/sessions/{session_id}")
async def delete_session(session_id: str):
    engine._store.delete_session(session_id)
    return {"ok": True}


# 消息治理(删除 = shadow tail-cut;编辑 = replace)

@app.delete("/messages/from/{from_id}")
async def delete_messages_from(from_id: int, session_id: str | None = None):
    if session_id is None:
        raise HTTPException(status_code=400, detail="session_id required")
    session = engine._store.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    with engine._lock:
        deleted = engine._store.delete_messages_from(session_id, from_id)
    return {
        "deleted": True,
        "session_id": session_id,
        "deleted_messages": deleted,
        "deleted_objects": 0,
        "deleted_actions": 0,
        "deleted_sensory": 0,
    }


class MessageUpdate(BaseModel):
    content: str = Field("", max_length=8000)


@app.patch("/messages/{msg_id}")
async def update_message(msg_id: int, body: MessageUpdate):
    # 找出该消息所在会话(消息 id 全局查:扫所有会话)
    for s in engine._store.list_sessions():
        if engine._store.get_message(s["id"], msg_id):
            with engine._lock:
                ok = engine._store.update_message_content(
                    s["id"], msg_id, body.content
                )
            if ok:
                return {"updated": True, "session_id": s["id"]}
    raise HTTPException(status_code=404, detail="Message not found")


# 心跳(后台自走)—— 手动端点(UI 脉冲按钮),落生活会话

@app.post("/autonomy/pulse")
async def pulse_autonomy():
    """手动触发一次自走轮:她独处想/做一轮,写给"最近激活的卡"(Yona 兜底)。

    2026-09 每卡 life:不再有匿名生活会话 —— 目标卡 = store.life_target。
    """
    if engine._loop is None:
        raise HTTPException(status_code=503, detail="引擎未启动")
    sid = engine.life_session_id()
    log = engine._store.load_log(sid)
    t0 = time.time()
    engine.begin_self_wake()  # 普通自走轮(脉冲)也产时间预算(2026-09 拍板)
    try:
        with engine._lock:
            # 目标卡快照的人格覆盖(若有)在自走轮同样生效
            snap = engine._store.get_session_settings(sid)
            engine._loop.run_turn(
                source="self", log=log,
                system_prompt=(snap.get("system_prompt")
                               if snap.get("system_prompt") else None),
            )
            engine._store.save_log(sid, log)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=500, detail=f"Autonomy pulse failed: {exc}"
        )
    finally:
        engine.end_self_wake()
    return {"elapsed_ms": int((time.time() - t0) * 1000), "session_id": sid}


# 静态 UI(复用旧 Yona static/,契约原样)

app.mount("/", StaticFiles(directory=str(ROOT / "static"), html=True),
          name="static")
