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
- 观测:GET /workspace(动作轨迹从日志派生)、GET /admin/life-events(内心活动)
- 配置:settings/models/context-sources;预设 CRUD + 模型发现 = server/app/api/config.py
- 杂项:images/bg-position 纯文件存取
"""

from __future__ import annotations

import time
from contextlib import asynccontextmanager

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


# 2026-09 清理:原来这里有 `from pathlib import Path` —— 全文件零引用(路径拼接
# 都走 engine.ROOT / StaticFiles),已删。若哪天要在这里拼路径,记得 import 回来。


# 基础:健康 / 设置 / 模型 / 上下文配置

@app.get("/health")
async def health():
    """运维探活(容器/反代/部署脚本用,不看业务状态)。

    ⚠ 全仓**零调用**(2026-09 清理时标注,grep `/health` 只剩本行与 docstring
      里那处列举):静态 UI 不调它、测试不调它。**留着**是因为探活端点是部署面
      的公开契约(仓库外可能有监控在打),删掉会让外部探活 404;它没有状态、
      没有依赖,留着零成本。将来手术:确认没有外部消费者之后连这段一起删。
    """
    return {"status": "ok", "service": "yona-rewrite"}


@app.get("/models")
async def list_models():
    """列"当前连接端点真正可用"的模型(引擎快照)。⏳ UI 已不再调它。

    ⚠ 被 `/settings` 取代(2026-09 清理时标注,**不删**):UI 的模型下拉走
      `GET /settings` 的 `models` 字段(static/app-core.js 的 `loadModels()`),
      不是这个端点。保留原因是它是**公开 API**,可能有仓库外消费者(脚本/探针),
      删掉等于对外改契约。将来手术:确认无外部消费者再删,同时把 main.py 头部的
      端点族列举(文件头 docstring "配置:settings/models/context-sources")一起改。

    2026-09 连接管理:只列"当前连接端点真正可用"的模型(引擎快照,
    由连接向导实测拉通后缓存);未连接 = 空列表,UI 显示首启引导。
    """
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
    """白名单 + 类型/范围校验(脏字段静默丢弃,非法值 400)。

    ⚠ 下面有三条**硬编码的产品边界,绕过了 server/params.py**(2026-09 清理时标注,
      行为一个字节没动 —— 搬它们要改 server/params.py,不在本次授权范围内):
        * `if not 0.0 <= t <= 2.0`    温度上限 2.0
        * `if not 0 <= r <= 40`       上下文轮数上限 40(0 = 不限制,合法)
        * `len(sp) > 4000`            人设覆盖串长度上限 4000
      为什么算"漏网":产品语义参数的唯一来源是 server/params.py(见该文件表头),
      而这三条只活在这个 router 里 —— 改的时候两边互不知道,漂移了也没有人喊。
      ⚠ 尤其 40 与 params 的 DEFAULT_CONTEXT_ROUNDS(server/params.py 的 `DEFAULT_CONTEXT_ROUNDS` = 20)
      是**两条线**:默认 20、可填到 40,别把两者当同一个值。
      将来手术:params.py 加三条(如 LLM_MAX_TEMPERATURE / CONTEXT_MAX_ROUNDS /
      SYSTEM_PROMPT_MAX_LEN),这里改成引用它们;错误文案里的 "0-2" / "0-40" 也要
      跟着改成插值,否则文案与真边界会分叉。
    """
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
    # 卡片走了,它的记忆索引缓存也该走 —— 缓存里是她和那个角色的全部对话。
    # 不删的后果不是"占点磁盘",是**归档卡的对话继续留在盘上**。
    engine.memory_forget(session_id)
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
    # 日志被截断了 → 缓存必须跟着删,否则她会把**用户已经删掉的话**
    # 继续翻出来念给他听(实测过最坏的那种:用户删了,她还记得)
    engine.memory_sync(session_id)
    return {
        "deleted": True,
        "session_id": session_id,
        "deleted_messages": deleted,
        # ⚠ **占位字段,恒 0**(2026-09 清理时保留并标注):`deleted_objects` **前端在读**
        #    —— static/app-messages.js 三处
        #    `if (data.deleted_objects) showSystem(`已收走 ... 个绑定产物。`)`。
        #    事实:本次删除只做 shadow tail-cut(server/store.py 的
        #    `delete_messages_from`,只 shadow 日志事件 + 存盘),**一个产物都没回收**,
        #    所以这里只能是 0,前端那句提示永远不会响。产物层(绑定的 objects)在
        #    当前实现里是冻结区。
        #    接回那天:store 真回收产物之后这里换成实际回收数。
        #    ⚠ 删它要**同时**改前端那三行 —— 否则前端读到 undefined,`if` 分支静默
        #    不变(不报错、不显形),最容易埋成"以后没人记得这里缺一块"。
        "deleted_objects": 0,
        # 2026-09 清理:**已删** `deleted_actions` / `deleted_sensory` 两个字段。
        #    判据:grep 全仓各只命中**本处那一行**(定义即唯一出现),永远 0;前端三处
        #    只读 `deleted_objects`(见上),没有任何消费方。留着会让人误以为
        #    "动作/感官产物真的在回收"。
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
                # 正文改了 → 那条的旧向量作废(否则按旧内容排新内容的名次)
                engine.memory_sync(s["id"])
                return {"updated": True, "session_id": s["id"]}
    raise HTTPException(status_code=404, detail="Message not found")


# 心跳(后台自走)—— 手动端点(UI 脉冲按钮),落生活会话

@app.post("/autonomy/pulse")
async def pulse_autonomy():
    """手动触发一次自走轮:她独处想/做一轮,写给"最近激活的卡"(Yona 兜底)。

    ⛔ **未启用 / 待砍(2026-09-22 10:20 用户拍板,别当活功能看)。**

    用户原话:「脉冲我还是没听懂,因为我 rewrite 里根本没有操刀过这一处,如果只是
    顺手搬过来的,那出问题就不奇怪了,因为现在的触发条件和以前完全不一样了,以前的
    脉冲根本没有什么准入门槛。标注掉吧,不启用,绑了 UI 的地方也标注掉,甚至我后面
    可能会把它整个砍掉,否则大概率要开很多特权才复活得了它。」

    事实核对(与用户判断一致):
      · 这个端点是 **baseline 就搬进来的旧物**(`git log -S'/autonomy/pulse'`
        第一个 commit 就是 `2903b2a chore: baseline`),**不是 rewrite 里长出来的**;
      · 旧 Yona 的脉冲=按需手动戳一下,没有准入门槛;而现在自走轮这一套已经有
        **闸门 / 时间预算 / 补写 / 冷却 / 队列优先级**,脉冲**一个都没接**。
        它只是被顺手塞进 `_submit_turn`,旧语义与新机制不是一套东西;
      · 所以**别拿它当"真 bug"报**。2026-09-22 清理时补的 `sid=sid` + `mark_self()`
        只是让它在现有机制下"不至于静默跑错卡",**不代表这功能被承认**。

    **现状:标注,不摘。** 端点仍通、UI 按钮仍在(用户要的是"标注掉",不是删)。
    **允许的两种终局**(由用户定,别人不要自己动):
      ① 整个砍掉 —— 摘 UI 按钮 + 删本端点 + `engine.py` 里
         `LifeLoop` / `_maybe_backfill_life` 是不受影响的(它们各走各的入口);
      ② 复活 —— 那就要先把自走轮的门槛补齐(闸门 / 时间预算 / 冷却 / 与心跳的
         `mark_self` 关系),否则它会绕过所有准入规则,行为与心跳自走轮不同源。
        这也是用户说的"大概率要开很多特权"。

    ⚠️ 下面这段是留给终局 ② 的笔记("**假如**它真被复活,要遵守什么规则"),
    **不是**当前生效说明:

      2026-09 每卡 life:不再有匿名生活会话 —— 目标卡 = `store.life_target`。
      三个**自走入口**必须都做同样两件事:
        self 心跳自走 = `engine.LifeLoop`(`engine.py` 的 `mark_self()` 调用)
        离线补写      = `engine._maybe_backfill_life`(`engine.py`)
        手动脉冲      = 本函数
      两件事:(a) `_submit_turn(..., sid=sid)` —— 告诉 worker"这一项是哪张卡";
             (b) 跑完 `mark_self()` —— 让这一轮进心跳冷却。
      **少 (a) 的症状**:worker 里 `_recall_sid["sid"]` = None →
      `recall_index()`(`engine.py` 的 `if sid is None ... return None`)回 None →
      recall 工具给模型的事实是"检索没跑起来",于是**她会开口说一时想不起来**,
      而真相只是引擎不知道翻哪张卡;同时 worker 的 `if sid is not None` 跳过
      `memory_sync`,脉冲产出的生活事件不进索引(下一次也检索不到)。
      **少 (b) 的症状**:脉冲跑完不进冷却 → 心跳会在几秒后再自走一轮,节奏衔接断。
      这两条 2026-09-22 清理时已按上面对齐(行为变更)—— 但见最上面的 ⛔:
      **这不让脉冲变成"被承认的功能"**。
    """  # noqa: D401
    if engine._loop is None:
        raise HTTPException(status_code=503, detail="引擎未启动")
    sid = engine.life_session_id()
    t0 = time.time()

    def _job():
        # 加载 → 决策 → 跑轮 → 落盘 = **一个队列项**(worker 已持 _lock)。
        # 2026-09-17:store.load_log 无缓存(每次读盘返回新对象)、写盘整体覆盖
        # —— 加载与落盘之间不能松手。见 docs/pitfalls/HISTORY.md §四。
        log = engine._store.load_log(sid)
        budget = engine.begin_self_wake(log)  # 普通自走轮(脉冲)时间预算(同补写事件算法,2026-09)
        if budget <= 0:
            # 2026-09 拍板:窗口 [日志尾, 当前] 无事件 → 该轮不触发任何事件,
            # 安静结束(不调 LLM、不动日志)。UI 收到 quiet 即知"这轮无事可叙"。
            return False
        try:
            # 目标卡快照的人格覆盖(若有)在自走轮同样生效
            snap = engine._store.get_session_settings(sid)
            engine._loop.run_turn(
                source="self", log=log,
                system_prompt=(snap.get("system_prompt")
                               if snap.get("system_prompt") else None),
            )
            engine._store.save_log(sid, log)
            # 真跑了一轮 → 进心跳冷却(2026-09 清理补上,与 LifeLoop / 补写同款)。
            # 判空是必须的:_life_gate 只在 _start_heartbeat 里赋值,引擎未起心跳时
            # (测试、UI 只连了连接没起心跳)它是 None —— 这里不该因此炸掉脉冲。
            # 注意"安静结束"那条 return False 在上面**早退**,不进冷却(正确:
            # 什么都没发生就不该占冷却,心跳从同一锚继续等下一件事件)。
            if engine._life_gate is not None:
                engine._life_gate.mark_self()
            return True
        finally:
            engine.end_self_wake()

    try:
        # sid=sid **必须给**(2026-09 清理补上,行为变更):见本函数 docstring ——
        # 少了它,recall 会翻不到卡、本轮记忆也不进索引。另外三个提交点都传了:
        # server/app/api/chat.py(user 轮的 `_submit_turn`)、
        # engine.py 的 `LifeLoop`(自走轮)、engine.py 的 `_maybe_backfill_life._run()`(离线补写)。
        ran = engine._submit_turn(_job, priority=engine._QUEUE_SELF, sid=sid)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=500, detail=f"Autonomy pulse failed: {exc}"
        )
    if not ran:
        return {"quiet": True,
                "reason": "窗口 [日志尾 → 当前] 无事件,本轮安静结束",
                "session_id": sid}
    return {"elapsed_ms": int((time.time() - t0) * 1000), "session_id": sid}


# 静态 UI(复用旧 Yona static/,契约原样)

app.mount("/", StaticFiles(directory=str(ROOT / "static"), html=True),
          name="static")
