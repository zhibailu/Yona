"""Yona 服务层 · LLM 连接配置(runtime,2026-09 任务③"连接管理")

产品配置路径(2026-09 用户拍板):
- **UI 是唯一入口** —— 开发者与试玩用户同流程;`.env` 只留给脚本/探针
  (config.py),**服务端不再把 .env 当产品配置**。
- 运行时配置 = 本地文件 `data/llm.local.json`(gitignored 的 data/ 下),
  UI 首启向导填写 base_url+key → 实测拉通模型列表 → 落盘 → engine 热重配
  (免重启进程,见 engine.reconfigure_llm)。

安全与诚实约定:
- 落盘含 api_key(本地单用户 app 惯例,v1 不做系统钥匙串);
- **任何出 HTTP 的面都不回传 api_key**(sanitize 只留 public 字段);
- 保存前必须用新 key 实测 `GET {base}/models` 成功(拉不通不落盘)。
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import requests

_CONFIG_FILENAME = "llm.local.json"


def config_path(data_dir: str | Path) -> Path:
    return Path(data_dir) / _CONFIG_FILENAME


def load_runtime(data_dir: str | Path) -> dict | None:
    """读运行时配置(None = 未配置)。文件损坏按未配置处理(不炸启动)。"""
    p = config_path(data_dir)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not data.get("api_key") or not data.get("base_url"):
        return None
    return data


def save_runtime(data_dir: str | Path, cfg: dict) -> None:
    """落盘运行时配置(base_url/api_key/model/models/updated_at)。"""
    p = config_path(data_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    body = {
        "base_url": (cfg.get("base_url") or "").strip(),
        "api_key": (cfg.get("api_key") or "").strip(),
        "model": (cfg.get("model") or "").strip(),
        "models": [m for m in (cfg.get("models") or []) if isinstance(m, str)],
        "updated_at": int(time.time()),
    }
    p.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")


def fetch_models(base_url: str, api_key: str) -> tuple[list[str] | None, str]:
    """用给定端点+key 实测拉模型列表。

    返回 (models, "") 成功 / (None, 错误信息) 失败 —— 保存前置校验,
    拉不通 = 配置无效,不落盘。
    """
    base = (base_url or "").strip().rstrip("/")
    if not base or not api_key:
        return None, "base_url 或 api_key 为空"
    try:
        resp = requests.get(
            f"{base}/models",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=15,
        )
        resp.raise_for_status()
        payload = resp.json()
    except requests.RequestException as exc:
        detail = ""
        if exc.response is not None:
            detail = (exc.response.text or "")[:300]
        return None, f"请求失败: {exc} {detail}".strip()
    except ValueError as exc:
        return None, f"响应不是 JSON: {exc}"

    items: list = []
    if isinstance(payload, dict):
        items = payload.get("data") or payload.get("models") or []
    elif isinstance(payload, list):
        items = payload
    models = [m.get("id") for m in items if isinstance(m, dict) and m.get("id")]
    if not models:
        return None, "端点没返回任何模型(检查 key 权限/端点)"
    return models, ""


def sanitize(cfg: dict | None) -> dict:
    """给 HTTP 的公开视图:绝不包含 api_key。键形状恒定,UI/消费方不用猜。"""
    if not cfg:
        return {"configured": False, "base_url": "", "model": "", "models": []}
    return {
        "configured": True,
        "base_url": cfg.get("base_url", ""),
        "model": cfg.get("model", ""),
        "models": [m for m in (cfg.get("models") or []) if isinstance(m, str)],
    }
