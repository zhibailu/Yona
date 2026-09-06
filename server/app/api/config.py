"""Yona 服务层 · 配置域(config):模型发现 + 上下文预设

thin router 拆分:这些是"设置页"背后的读写 —— 预设存盘、模型列表发现。
之前 main.py 里是占位(返回空),2026-09 用户拍板补成真功能:
- 预设 = 一串 UI 设置的命名快照(模型/温度/人设/上下文窗口),落盘 data/presets/
- 模型发现 = 拿用户填的 base_url+key 问那个兼容端点要模型列表(几行转发)

存储是纯文件(每预设一个 JSON),可脱离 HTTP 单测。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import requests
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import engine, llm_setup

router = APIRouter()

# 预设文件名白名单:英文/数字/下划线/短横线,防路径穿越
_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _presets_dir() -> Path:
    d = engine.DATA_DIR / "presets"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _preset_path(name: str) -> Path:
    if not _NAME_RE.match(name):
        raise HTTPException(status_code=400, detail="预设名只允许英文/数字/_-")
    return _presets_dir() / f"{name}.json"


# ---------- 预设 CRUD ----------

class PresetIn(BaseModel):
    name: str = Field(..., max_length=80)
    title: str = Field("", max_length=120)
    description: str = Field("", max_length=500)
    prompt: str = Field("", max_length=4000)
    model: str | None = None
    temperature: float | None = None
    sources: dict | None = None  # {sliding_window:{max_rounds}}(2026-09 收紧:只存真生效旋钮)


@router.get("/presets")
async def list_presets():
    """预设列表(下拉用):每个预设的 name + title。"""
    out: list[dict] = []
    for f in sorted(_presets_dir().glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        out.append({"name": data.get("name", f.stem), "title": data.get("title", f.stem)})
    return out


@router.get("/presets/{name}")
async def get_preset(name: str):
    """取一个预设的完整内容(UI 加载后回填设置页)。"""
    p = _preset_path(name)
    if not p.exists():
        raise HTTPException(status_code=404, detail="预设不存在")
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise HTTPException(status_code=500, detail=f"预设文件损坏: {exc}") from exc


@router.post("/presets")
async def save_preset(body: PresetIn):
    """保存/覆盖一个预设(名字已存在 = 覆盖)。"""
    data = {
        "name": body.name,
        "title": body.title or body.name,
        "description": body.description,
        "prompt": body.prompt,
        "model": body.model,
        "temperature": body.temperature,
        "sources": body.sources or {},
    }
    p = _preset_path(body.name)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"saved": True, "name": body.name, "title": data["title"]}


@router.delete("/presets/{name}")
async def delete_preset(name: str):
    """删除一个预设。"""
    p = _preset_path(name)
    if not p.exists():
        raise HTTPException(status_code=404, detail="预设不存在")
    p.unlink()
    return {"deleted": True, "name": name}


# ---------- 模型发现(UI:填 base_url+key → 问它要模型列表) ----------

class DiscoverIn(BaseModel):
    base_url: str = Field(..., max_length=500)
    api_key: str = Field("", max_length=500)


@router.post("/admin/discover-models")
def discover_models(body: DiscoverIn):
    """拿用户填的兼容端点 + key,请求 GET {base_url}/models 返回模型列表。

    转发而已(几行):UI 用返回填充模型下拉。失败给 error 字段,UI 弹提示。
    用 def(非 async):requests 是同步阻塞的,放线程池跑,不卡事件循环。
    """
    base = body.base_url.rstrip("/")
    try:
        resp = requests.get(
            f"{base}/models",
            headers={"Authorization": f"Bearer {body.api_key}"},
            timeout=15,
        )
        resp.raise_for_status()
        payload = resp.json()
    except requests.RequestException as exc:
        detail = ""
        if exc.response is not None:
            detail = exc.response.text[:300]
        return {"error": f"请求失败: {exc} {detail}".strip()}
    except ValueError as exc:
        return {"error": f"响应不是 JSON: {exc}"}

    # 两种形态都认:OpenAI 标准 {"data":[...]},或直接返回 list 的兼容端点
    items = []
    if isinstance(payload, dict):
        items = payload.get("data") or payload.get("models") or []
    elif isinstance(payload, list):
        items = payload
    models = [
        {
            "id": m.get("id", ""),
            "name": m.get("id", ""),
            "owned_by": m.get("owned_by", ""),
        }
        for m in items
        if isinstance(m, dict) and m.get("id")
    ]
    return {"models": models}


# ---------- 连接管理(2026-09 任务③:UI 首启向导的唯一入口) ----------
# .env 不再是产品配置;这里保存/热重配"运行时连接",免重启进程。
# 安全:任何返回都不含 api_key(engine.llm_state 已 sanitize)。

class LlmConnectIn(BaseModel):
    base_url: str = Field(..., max_length=500)
    api_key: str = Field(..., max_length=500)
    model: str = Field("", max_length=120)  # 空 = 端点列表第一个


@router.get("/admin/llm-config")
async def get_llm_config():
    """当前连接状态(公开视图,无 key):configured/base_url/model/models。"""
    return engine.llm_state()


@router.post("/admin/llm-config")
async def post_llm_config(body: LlmConnectIn):
    """连接你的模型:实测拉通 → 热重配引擎(锁内,免重启) → 落盘。

    拉不通(端点错/key 无权限/无模型)→ 400,什么都不改。
    """
    base = body.base_url.strip().rstrip("/")
    models, err = llm_setup.fetch_models(base, body.api_key)
    if err:
        raise HTTPException(status_code=400, detail=err)
    model = body.model.strip()
    if model and model not in models:
        raise HTTPException(status_code=400, detail=f"模型 {model} 不在该端点可用列表内")
    model = model or models[0]
    cfg = {"base_url": base, "api_key": body.api_key.strip(),
           "model": model, "models": models}
    try:
        engine.reconfigure_llm(cfg)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"引擎热重配失败: {exc}") from exc
    llm_setup.save_runtime(engine.DATA_DIR, cfg)  # 重配成功才落盘
    return engine.llm_state()
