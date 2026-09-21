"""Yona 服务层 · 图片 / 背景(media)

thin router 拆分:纯文件存取,与内核一点关系没有 —— 最容易独立的一块。
聊天背景图、头像、以及背景滚动位置,就是往 `data/sessions/<sid>/images/` 读写 jpg
(2026-09 布局:图片跟卡走,一张卡一个目录;旧的平铺 `data/images/<sid>/` 由
 `server/store.py:_migrate_legacy_layout` 启动时一次性搬进来)。

注意:_bg_positions 是模块级内存 dict(进程内记住各会话背景滚动位置),
不落盘 —— 重启丢位置可接受(UI 会重发)。
"""

from __future__ import annotations

import base64
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

    ⚠️ 这里**只算路径,不建目录**:GET 图片走同一条路径 —— 原来在里面
    `mkdir(parents=True, exist_ok=True)`,一个**读**请求会顺手往盘上造目录
    (读路径产生写副作用)。现在建目录的职责归 `save_image`,
    GET 遇到目录不存在就老老实实 404。
    """
    if not _SESSION_ID_RE.fullmatch(session_id):
        raise HTTPException(status_code=400, detail="Invalid session_id")
    if key not in _IMAGE_KEYS:
        raise HTTPException(status_code=400, detail="Invalid key")
    if engine._store is None:
        raise HTTPException(status_code=503, detail="存储未就绪")
    return engine._store.images_dir(session_id).resolve() / f"{key}.jpg"


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
    try:
        raw = base64.b64decode(b64, validate=True)
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=400, detail="Invalid image data")
    if len(raw) > _MAX_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail="Image too large")
    p = _image_path(session_id, key)
    p.parent.mkdir(parents=True, exist_ok=True)  # 建目录归写路径(见 _image_path)
    p.write_bytes(raw)
    return {"status": "ok"}


@router.delete("/images/{session_id}/{key}")
async def delete_image(session_id: str, key: str):
    p = _image_path(session_id, key)
    if p.exists():
        p.unlink()
    return {"status": "ok"}


class BgBody(BaseModel):
    """背景滚动位置的上报体(前端 `static/app-media-debug.js` 里那个 POST body)。

    session_id 是**必填**:这里原来只声明了 position,而前端发的是
    `{session_id, position}` —— pydantic 默认 `extra=ignore`,
    **多出来的 session_id 被静默丢弃**,于是 POST 处理体永远不知道自己该存哪张卡。
    """

    session_id: str
    position: float = 0.0


@router.get("/bg-position/{session_id}")
async def get_bg_position(session_id: str):
    return _bg_positions.get(session_id, {"position": 0.0})


@router.post("/bg-position")
async def save_bg_position(body: BgBody):
    """记住某张卡的背景滚动位置(内存态)。

    2026-09 修:这里原来函数体只有 `return {"status": "ok"}`,**什么都不存** ——
    前端拖完背景 POST 出去拿到 200,GET 回来永远是 `{"position": 0.0}`,
    "记住背景位置"彻底失效,而且**两端都不报错、查不出来**。
    这是这类错最典型的形态:**契约字段名/字段表对不上时不报错,只是安静地丢掉**。
    (前端:发 `static/app-media-debug.js` 里那个 `fetch('/bg-position', ...)`,
    读回同文件的 `fetch('/bg-position/' + sid)`,body 是 `{session_id, position}`。)

    ⚠️ 这是**内存态**:`_bg_positions` 是模块级 dict,重启就丢(UI 会重发,可接受)。
    将来真要持久化,落这张卡的 `meta.json`(`server/store.py` 的
    `_write_meta`/`set_session_settings` 那套),**别另开文件**。
    """
    if not _SESSION_ID_RE.fullmatch(body.session_id):
        raise HTTPException(status_code=400, detail="Invalid session_id")
    _bg_positions[body.session_id] = {"position": body.position}
    return {"status": "ok"}
