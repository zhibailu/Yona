"""Yona 服务层 · 图片 / 背景(media)

thin router 拆分:纯文件存取,与内核一点关系没有 —— 最容易独立的一块。
聊天背景图、头像、以及背景滚动位置,就是往 data/images/ 读写 jpg。

注意:_bg_positions 是模块级内存 dict(进程内记住各会话背景滚动位置),
不落盘 —— 重启丢位置可接受(UI 会重发)。
"""

from __future__ import annotations

import re

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .. import engine

router = APIRouter()

_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
_IMAGE_KEYS = {"bg", "avatar_ai", "avatar_user"}
_MAX_IMAGE_BYTES = 5 * 1024 * 1024
_bg_positions: dict[str, dict] = {}


def _image_path(session_id: str, key: str):
    """卡片图片路径(sessions/<sid>/images/<key>.jpg),带防穿越校验。

    2026-09 布局:图片跟卡走(一个会话一个目录,URL 契约不变)。
    """
    if not _SESSION_ID_RE.fullmatch(session_id):
        raise HTTPException(status_code=400, detail="Invalid session_id")
    if key not in _IMAGE_KEYS:
        raise HTTPException(status_code=400, detail="Invalid key")
    if engine._store is None:
        raise HTTPException(status_code=503, detail="存储未就绪")
    base = engine._store.images_dir(session_id).resolve()
    base.mkdir(parents=True, exist_ok=True)
    return base / f"{key}.jpg"


@router.get("/images/{session_id}/{key}")
async def get_image(session_id: str, key: str):
    p = _image_path(session_id, key)
    if not p.exists():
        raise HTTPException(status_code=404, detail="Image not found")
    return FileResponse(p, media_type="image/jpeg")


class ImageBody(BaseModel):
    data: str = ""


@router.post("/images/{session_id}/{key}")
async def save_image(session_id: str, key: str, body: ImageBody):
    data_url = body.data or ""
    if not data_url.startswith("data:image/") or "," not in data_url:
        raise HTTPException(status_code=400, detail="Invalid data URL")
    header, b64 = data_url.split(",", 1)
    if ";base64" not in header.lower():
        raise HTTPException(status_code=400, detail="Invalid data URL")
    import base64 as _base64
    try:
        raw = _base64.b64decode(b64, validate=True)
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=400, detail="Invalid image data")
    if len(raw) > _MAX_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail="Image too large")
    _image_path(session_id, key).write_bytes(raw)
    return {"status": "ok"}


@router.delete("/images/{session_id}/{key}")
async def delete_image(session_id: str, key: str):
    p = _image_path(session_id, key)
    if p.exists():
        p.unlink()
    return {"status": "ok"}


class BgBody(BaseModel):
    position: float = 0.0


@router.get("/bg-position/{session_id}")
async def get_bg_position(session_id: str):
    return _bg_positions.get(session_id, {"position": 0.0})


@router.post("/bg-position")
async def save_bg_position(body: BgBody):
    return {"status": "ok"}
