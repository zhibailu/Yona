"""Yona 新内核 · 配置加载

从 .env / 环境变量读取配置。密钥绝不进源码,只进 .env(已 gitignore)。
"""

from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()  # 自动加载项目根目录的 .env


def _get(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


LLM_API_KEY = _get("LLM_API_KEY")
LLM_BASE_URL = _get("LLM_BASE_URL")
LLM_MODEL = _get("LLM_MODEL", "deepseek-chat")


def require_llm_config() -> tuple[str, str, str]:
    """取 (key, base_url, model),缺 key 就报错,防止裸跑。"""
    if not LLM_API_KEY:
        raise RuntimeError(
            "缺少 LLM_API_KEY:请复制 .env.example 为 .env 并填入真实值"
        )
    if not LLM_BASE_URL:
        raise RuntimeError("缺少 LLM_BASE_URL:请在 .env 里配置兼容端点")
    return LLM_API_KEY, LLM_BASE_URL, LLM_MODEL
