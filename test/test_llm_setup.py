"""连接管理纯函数自测(server/app/llm_setup.py,2026-09 任务③)。

只测不碰网络的纯函数:运行时配置读写往返、sanitize 不外泄 key、
fetch_models 的空参数/畸形分支。真网络路径由 /admin/llm-config 冒烟负责。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from server.app import llm_setup  # noqa: E402


def test_config_path_and_runtime_roundtrip(tmp: str = ""):
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        assert llm_setup.load_runtime(d) is None  # 没配过 = None
        cfg = {"base_url": "https://api.deepseek.com", "api_key": "sk-x",
               "model": "deepseek-v4-pro",
               "models": ["deepseek-v4-flash", "deepseek-v4-pro"]}
        llm_setup.save_runtime(d, cfg)
        loaded = llm_setup.load_runtime(d)
        assert loaded["model"] == "deepseek-v4-pro"
        assert loaded["models"] == cfg["models"]
        assert loaded["base_url"] == "https://api.deepseek.com"
        # 文件损坏 -> 当未配置,不炸
        (llm_setup.config_path(d)).write_text("{broken", encoding="utf-8")
        assert llm_setup.load_runtime(d) is None


def test_sanitize_never_leaks_api_key():
    cfg = {"base_url": "https://x", "api_key": "sk-super-secret",
           "model": "m1", "models": ["m1", "m2"]}
    s = llm_setup.sanitize(cfg)
    assert "api_key" not in s
    assert "sk-super-secret" not in str(s)
    assert s["configured"] is True and s["models"] == ["m1", "m2"]
    # 未配置也有恒定键形状(UI/消费方不用猜键)
    empty = llm_setup.sanitize(None)
    assert empty == {"configured": False, "base_url": "", "model": "",
                     "models": []}


def test_fetch_models_rejects_empty_without_network():
    ok, err = llm_setup.fetch_models("", "")
    assert ok is None and err
    ok, err = llm_setup.fetch_models("https://x", "")
    assert ok is None and err


if __name__ == "__main__":
    test_config_path_and_runtime_roundtrip()
    test_sanitize_never_leaks_api_key()
    test_fetch_models_rejects_empty_without_network()
    print("llm_setup all tests passed")
