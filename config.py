"""Yona 新内核 · 配置加载

从 .env / 环境变量读取配置。密钥绝不进源码,只进 .env(已 gitignore)。
"""

from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()  # 自动加载项目根目录的 .env


def _get(name: str, default: str = "") -> str:
    """环境变量取值 —— 留着只为"怎么取环境变量"这件事**只有一个地方**。

    它是 `os.environ.get(name, default)` 的直通包装,眼下不带来任何行为差异;
    留着的理由:日后要给环境变量加统一前缀、统一 strip、或把 .env 换成别的源时,
    改这里一行就够,不必去动下面三处调用。
    ⏸ 若认定"能删就不留":删本函数后下面三处调用**完全等价**,分别是
      `os.environ.get("LLM_API_KEY", "")` / `("LLM_BASE_URL", "")` /
      `("LLM_MODEL", "deepseek-chat")` —— 已逐处核过,无行为变化。
    """
    return os.environ.get(name, default)


# ══════════════════════════════════════════════════════════════════
# ⏸ 占位 —— 本模块**只给 test/ 下的脚本用,产品侧一个都不 import**。
#
# ① 现状(2026-09-22 grep 逐处核过):
#      · 产品代码**零引用** —— `server/` 全目录不 import 本模块,`engine.py`
#        走的是 `server/app/llm_setup.load_runtime(data/llm.local.json)`。
#      · 只有 **10 个 test 脚本**在用,全部是同一句
#        `from config import require_llm_config` + `api_key, base_url, model = require_llm_config()`:
#            test/backfill_probe.py            test/self_view_probe.py
#            test/lie_repro_test.py            test/self_view_probe2.py
#            test/heartbeat_log_dump.py        test/legacy/demo_real.py
#            test/heartbeat_context_probe.py   test/legacy/mvp.py
#            test/real_route_test.py           test/legacy/heartbeat_demo.py
# ② 产品连接的**唯一入口**是 `server/app/llm_setup`:
#      真值住在 `data/llm.local.json`(UI 首启向导落盘);
#      `llm_setup.py` 的模块 docstring 已写明"UI 是唯一入口 …`.env` 只留给
#      脚本/探针(config.py),**服务端不再把 .env 当产品配置**"。
#      → 也就是说:这两套配置**本来就不是一套东西**,不是"忘了统一"。
# ③ 真要把两套合成一套(让 10 个脚本也读 `data/llm.local.json`):
#      先改那 **10 处** `from config import require_llm_config` /
#      `require_llm_config()` 调用点(清单见上),再删本模块的这三个常量与
#      `require_llm_config`。⚠️ 注意脚本与产品的语义不同:脚本要的是
#      "裸跑就报错"(缺配置直接 raise),而 `load_runtime` 是"缺配置返回 None";
#      合并时得保留 raise 行为,否则脚本会带着空 key 跑出一堆假结果。
#
# **不要删这三个常量/这个函数** —— 删了上面那 10 个脚本当场红。
# ══════════════════════════════════════════════════════════════════
LLM_API_KEY = _get("LLM_API_KEY")
LLM_BASE_URL = _get("LLM_BASE_URL")
LLM_MODEL = _get("LLM_MODEL", "deepseek-chat")


def require_llm_config() -> tuple[str, str, str]:
    """取 (key, base_url, model),缺 key 就报错,防止裸跑。

    ⏸ 产品侧不调用它(见上方占位说明):它是**脚本/探针**的裸跑护栏。
    """
    if not LLM_API_KEY:
        raise RuntimeError(
            "缺少 LLM_API_KEY:请复制 .env.example 为 .env 并填入真实值"
        )
    if not LLM_BASE_URL:
        raise RuntimeError("缺少 LLM_BASE_URL:请在 .env 里配置兼容端点")
    return LLM_API_KEY, LLM_BASE_URL, LLM_MODEL
