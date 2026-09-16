"""实验台只读工具集自测(离线、确定、不打网络):
- 工具三件套形状(schema/description/parameters/usage),且**不含时间工具**(VISION 决策 8)
- 返回统一 JSON 信封;可预期失败走 ok:false,**不抛异常**
- http_get:HTML→纯文本(去 script/style、解实体、块标签转换行)、截断、charset、协议校验
- web_search:provider 可注入、count 夹取、端点报错变信封
- list_files / read_text_file:沙箱越界拒绝、跳过表、行窗口、二进制/超大拒绝
- 与 ToolRegistry / 子运行接得上(端到端小任务)

跑: py test/test_lab_tools.py
"""

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

from core.llm import AssistantOutput, ToolCall
from core.tools import ToolRegistry
from core.subrun import SubRunSpec, execute
from server.app.worker_tools import (
    FetchResult,
    _unwrap_ddg,
    html_to_text,
    make_read_only_tools,
    parse_ddg_lite,
)
from mock_llm import MockLLM

# 结构取自 2026-09-15 真实抓到的 lite 页面(一条结果块,原样截取)。
# 解析器对着真结构测,而不是对着我脑补的结构测。
_DDG_LITE_SAMPLE = """
<tr><td valign="top">1.&nbsp;</td><td>
<a rel="nofollow" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fdocs.python.org%2F3%2Flibrary%2Furllib.html&amp;rut=641d421c" class='result-link'>urllib — URL handling modules — Python 3.14.7 documentation</a>
</td></tr>
<tr><td>&nbsp;&nbsp;&nbsp;</td><td class='result-snippet'>
Source code: Lib/<b>urllib</b>/ <b>urllib</b> is a package that collects several modules for working with URLs.
</td></tr>
"""


# ---------------- 替身 ----------------

class FakeOpener:
    """按 URL 返回预置响应;记录被请求过的 URL。"""

    def __init__(self, routes: dict[str, FetchResult]) -> None:
        self.routes = routes
        self.seen: list[str] = []

    def __call__(self, url: str, timeout: float) -> FetchResult:
        self.seen.append(url)
        if url in self.routes:
            return self.routes[url]
        import urllib.error

        raise urllib.error.HTTPError(url, 404, "Not Found", None, None)  # type: ignore[arg-type]


class FakeSearch:
    def __init__(self, hits: list[dict]) -> None:
        self.hits = hits
        self.seen: list[tuple[str, int]] = []

    def search(self, query: str, count: int) -> list[dict]:
        self.seen.append((query, count))
        return self.hits[:count]


class BoomSearch:
    def search(self, query: str, count: int) -> list[dict]:
        raise RuntimeError("provider 炸了")


def _payload(text: str) -> dict:
    return json.loads(text)


def _tool(tools: list, name: str):
    return next(t for t in tools if t.name == name)


def _call(tools: list, name: str, args: dict) -> dict:
    return _payload(_tool(tools, name).func(args))


# ---------------- 夹具 ----------------

def _tree(name: str) -> Path:
    """系统 temp 下的干净树,不往仓库里写东西。"""
    root = Path(tempfile.gettempdir()) / "yona-lab-tools-tests" / name
    if root.exists():
        import shutil

        shutil.rmtree(root)
    (root / "docs").mkdir(parents=True)
    (root / "notes.txt").write_text("第一行\n第二行\n第三行\n第四行\n第五行\n", encoding="utf-8")
    (root / "docs" / "a.md").write_text("# A\n内容\n", encoding="utf-8")
    (root / "docs" / "b.md").write_text("# B\n内容\n", encoding="utf-8")
    (root / "docs" / "skip.log").write_text("noise\n", encoding="utf-8")
    (root / ".git").mkdir()
    (root / ".git" / "HEAD").write_text("ref: x\n", encoding="utf-8")
    (root / "blob.bin").write_bytes(b"\x00\x01\x02binary")
    (root / "huge.txt").write_text("x" * 5000, encoding="utf-8")
    return root


# ---------------- 三件套形状 ----------------

def test_tool_set_shape_and_no_time_tool() -> None:
    tools = make_read_only_tools(_tree("shape"))
    names = sorted(t.name for t in tools)
    assert names == ["http_get", "list_files", "read_text_file", "web_search"]
    # VISION 决策 8:时间 = 每轮注入的死信息,不是工具。这里必须没有。
    assert not any("time" in n or "clock" in n for n in names)
    for tool in tools:
        schema = tool.schema()
        assert schema["type"] == "function"
        assert schema["function"]["name"] == tool.name
        assert schema["function"]["description"].strip()
        assert schema["function"]["parameters"]["type"] == "object"
        assert tool.usage.strip()
        assert tool.retain_result is False  # 只读工具不需要跨轮保真


def test_tools_register_into_tool_registry() -> None:
    """端到端走 ToolRegistry 也不该把可预期失败变成异常。

    注意:这里**注入** provider —— 单元测试一律离线,绝不打网络。
    """
    provider = FakeSearch([{"title": "T", "url": "https://e.com/1", "snippet": "s"}])
    registry = ToolRegistry(make_read_only_tools(_tree("registry"), search_provider=provider))
    assert sorted(registry.names()) == ["http_get", "list_files", "read_text_file", "web_search"]
    text, is_error = registry.execute("web_search", {"query": "x"})
    assert is_error is False
    assert _payload(text)["results"][0]["url"] == "https://e.com/1"
    # 可预期失败:不抛异常,由 registry 原样交出 ok:false 信封
    text, is_error = registry.execute("http_get", {"url": "file:///etc/passwd"})
    assert is_error is False
    assert _payload(text)["ok"] is False
    # 未知工具才是 registry 自己的错误
    text, is_error = registry.execute("nope", {})
    assert is_error is True


# ---------------- html_to_text ----------------

def test_html_to_text_strips_scripts_and_decodes_entities() -> None:
    raw = (
        "<html><head><style>body{color:red}</style></head>"
        "<body><script>var x = 1 < 2;</script>"
        "<p>你好 &amp; 世界</p><div>第二段</div><br><span>尾巴</span></body></html>"
    )
    text = html_to_text(raw)
    assert "var x" not in text and "color:red" not in text
    assert "你好 & 世界" in text
    assert "第二段" in text
    assert "<" not in text and ">" not in text


def test_unwrap_ddg_returns_real_target() -> None:
    wrapped = "//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa%3Fb%3D1&rut=zz"
    assert _unwrap_ddg(wrapped) == "https://example.com/a?b=1"
    assert _unwrap_ddg("https://plain.example.com/x") == "https://plain.example.com/x"


def test_parse_ddg_lite_reads_real_structure() -> None:
    hits = parse_ddg_lite(_DDG_LITE_SAMPLE, 5)
    assert len(hits) == 1
    assert hits[0]["url"] == "https://docs.python.org/3/library/urllib.html"
    assert hits[0]["title"].startswith("urllib — URL handling modules")
    assert "package that collects several modules" in hits[0]["snippet"]
    assert "<b>" not in hits[0]["snippet"]  # 高亮标签被清掉
    assert parse_ddg_lite(_DDG_LITE_SAMPLE, 0) == []  # count=0 就一条都不给


# ---------------- web_search ----------------

def test_web_search_wraps_provider_results() -> None:
    provider = FakeSearch([{"title": f"T{i}", "url": f"https://e.com/{i}", "snippet": "s"} for i in range(8)])
    tools = make_read_only_tools(_tree("search"), search_provider=provider)
    out = _call(tools, "web_search", {"query": "yona", "count": 3})
    assert out["ok"] is True
    assert out["count"] == 3
    assert [r["title"] for r in out["results"]] == ["T0", "T1", "T2"]
    assert provider.seen == [("yona", 3)]


def test_web_search_clamps_count_and_rejects_empty_query() -> None:
    provider = FakeSearch([{"title": "T", "url": "u", "snippet": "s"}] * 30)
    tools = make_read_only_tools(_tree("search2"), search_provider=provider)
    # provider 有 30 条,但要 999 条 -> 夹到上限 10
    assert _call(tools, "web_search", {"query": "q", "count": 999})["count"] == 10
    assert provider.seen[-1][1] == 10
    bad = _call(tools, "web_search", {"query": "   "})
    assert bad["ok"] is False and "query" in bad["error"]


def test_web_search_provider_failure_becomes_envelope_not_exception() -> None:
    tools = make_read_only_tools(_tree("search3"), search_provider=BoomSearch())
    out = _call(tools, "web_search", {"query": "q"})
    assert out["ok"] is False
    assert "provider 炸了" in out["error"]


class DeadProxyOpener:
    """连不上(模拟"环境里配了个没在跑的代理")。"""

    def __call__(self, url: str, timeout: float) -> FetchResult:
        import urllib.error

        raise urllib.error.URLError(
            "[WinError 10061] 由于目标计算机积极拒绝，无法连接。"
        )


def test_network_failure_points_at_the_proxy_when_one_is_set(monkeypatch=None) -> None:
    """真机踩过:User 级环境变量里留着死代理 -> 模型通、网页全拒。

    报错本身没错,但没人能从一行 WinError 里看出"你的代理没在跑" ——
    所以信封里要带上代理地址和一句人话。(2026-09-16)
    """
    import os

    keys = ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy")
    old = {k: os.environ.get(k) for k in keys}
    try:
        for key in keys:  # 先清干净,别让本机真实环境干扰断言
            os.environ.pop(key, None)
        os.environ["HTTPS_PROXY"] = "http://127.0.0.1:7892"
        tools = make_read_only_tools(_tree("proxy"), opener=DeadProxyOpener())
        out = _call(tools, "http_get", {"url": "https://e.com/a"})
        assert out["ok"] is False
        assert "10061" in out["error"]
        assert "127.0.0.1:7892" in out["hint"]
        assert "HTTPS_PROXY" in out["hint"]

        # 没有代理变量时就不该冒出这句提示(别无中生有)
        for key in keys:
            os.environ.pop(key, None)
        out2 = _call(tools, "http_get", {"url": "https://e.com/a"})
        assert out2["ok"] is False
        assert "hint" not in out2
    finally:
        for key, value in old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


# ---------------- http_get ----------------

def test_http_get_returns_plain_text_and_truncates() -> None:
    body = ("<html><body><script>hidden()</script><p>" + "内容" * 300 + "</p></body></html>").encode()
    opener = FakeOpener(
        {
            "https://e.com/a": FetchResult(
                status=200, url="https://e.com/a", body=body, content_type="text/html; charset=utf-8"
            )
        }
    )
    tools = make_read_only_tools(_tree("get"), opener=opener)
    out = _call(tools, "http_get", {"url": "https://e.com/a", "max_chars": 300})
    assert out["ok"] is True
    assert out["status"] == 200
    assert "hidden()" not in out["text"]
    assert out["truncated"] is True
    assert out["chars"] == 300


def test_http_get_rejects_bad_scheme_and_reports_http_errors() -> None:
    opener = FakeOpener({})
    tools = make_read_only_tools(_tree("get2"), opener=opener)
    bad = _call(tools, "http_get", {"url": "file:///etc/passwd"})
    assert bad["ok"] is False and "http/https" in bad["error"]
    assert opener.seen == []  # 根本没发请求
    miss = _call(tools, "http_get", {"url": "https://e.com/missing"})
    assert miss["ok"] is False and "404" in miss["error"]


def test_http_get_reads_charset_from_content_type() -> None:
    body = "中文内容".encode("gbk")
    opener = FakeOpener(
        {
            "https://e.com/gbk": FetchResult(
                status=200, url="https://e.com/gbk", body=body, content_type="text/plain; charset=gbk"
            )
        }
    )
    tools = make_read_only_tools(_tree("get3"), opener=opener)
    out = _call(tools, "http_get", {"url": "https://e.com/gbk"})
    assert out["ok"] is True
    assert "中文内容" in out["text"]


# ---------------- list_files ----------------

def test_list_files_lists_skips_and_reports_truncation() -> None:
    root = _tree("list")
    tools = make_read_only_tools(root)
    out = _call(tools, "list_files", {"path": "docs"})
    names = [e["name"] for e in out["entries"]]
    assert names == ["a.md", "b.md", "skip.log"]
    assert out["count"] == 3 and out["truncated"] is False

    limited = _call(tools, "list_files", {"path": "docs", "pattern": "*.md", "max": 1})
    assert limited["count"] == 1 and limited["truncated"] is True

    top = _call(tools, "list_files", {})
    assert ".git" not in [e["name"] for e in top["entries"]]


def test_list_files_rejects_escape_and_missing_dir() -> None:
    tools = make_read_only_tools(_tree("list2"))
    escape = _call(tools, "list_files", {"path": "../.."})
    assert escape["ok"] is False and "越界" in escape["error"]
    missing = _call(tools, "list_files", {"path": "nope"})
    assert missing["ok"] is False and "不存在" in missing["error"]


# ---------------- read_text_file ----------------

def test_relative_root_is_resolved() -> None:
    """真机踩到:root 传相对路径时 relative_to 会炸 —— 夹具用绝对路径掩盖了它。"""
    root = _tree("relroot")
    previous = os.getcwd()
    os.chdir(root.parent)  # 切到 temp 父目录,好用相对路径当 root
    try:
        tools = make_read_only_tools(root.name)  # 故意用相对路径
        listed = _call(tools, "list_files", {})
        assert listed["ok"] is True
        assert "notes.txt" in [e["name"] for e in listed["entries"]]
        read = _call(tools, "read_text_file", {"path": "notes.txt", "max_lines": 1})
        assert read["ok"] is True
        assert read["path"] == "notes.txt"
    finally:
        os.chdir(previous)


def test_read_text_file_window_and_truncation() -> None:
    root = _tree("read")
    tools = make_read_only_tools(root)
    out = _call(tools, "read_text_file", {"path": "notes.txt", "start_line": 2, "max_lines": 2})
    assert out["ok"] is True
    assert out["total_lines"] == 5
    assert out["start_line"] == 2
    assert out["returned_lines"] == 2
    assert out["truncated"] is True
    assert out["text"] == "第二行\n第三行"


def test_read_text_file_rejects_binary_huge_and_escape() -> None:
    root = _tree("read2")
    tools = make_read_only_tools(root, max_bytes=1000)
    binary = _call(tools, "read_text_file", {"path": "blob.bin"})
    assert binary["ok"] is False and "二进制" in binary["error"]
    huge = _call(tools, "read_text_file", {"path": "huge.txt"})
    assert huge["ok"] is False and "过大" in huge["error"]
    escape = _call(tools, "read_text_file", {"path": "../../secret.txt"})
    assert escape["ok"] is False and "越界" in escape["error"]
    missing = _call(tools, "read_text_file", {"path": "nope.txt"})
    assert missing["ok"] is False and "不存在" in missing["error"]


# ---------------- 与子运行接得上 ----------------

def test_tools_drive_a_subrun_end_to_end() -> None:
    """子运行有了手:它能自己读文件,结论回填给父 —— 这就是 SUBAGENT §3.3 的前提。"""
    root = _tree("subrun")
    tools = make_read_only_tools(root)
    llm = MockLLM(
        [
            AssistantOutput(
                tool_calls=[
                    ToolCall(id="c1", name="read_text_file", arguments='{"path": "notes.txt"}')
                ]
            ),
            AssistantOutput(text="这份笔记有 5 行,第一行是「第一行」。"),
        ]
    )
    rec = execute(
        SubRunSpec(task="读 notes.txt 并说它有几行", system="测试用任务说明", tools=tools, max_steps=3),
        llm,
    )
    assert rec.status == "completed"
    assert "5 行" in rec.output
    assert sorted(t["function"]["name"] for t in (llm.seen_tools[0] or [])) == [
        "http_get",
        "list_files",
        "read_text_file",
        "web_search",
    ]
    assert "第一行" in str(llm.seen_messages[-1])  # 工具结果真的进了它的上下文


if __name__ == "__main__":
    test_tool_set_shape_and_no_time_tool()
    test_tools_register_into_tool_registry()
    test_html_to_text_strips_scripts_and_decodes_entities()
    test_unwrap_ddg_returns_real_target()
    test_parse_ddg_lite_reads_real_structure()
    test_web_search_wraps_provider_results()
    test_web_search_clamps_count_and_rejects_empty_query()
    test_web_search_provider_failure_becomes_envelope_not_exception()
    test_network_failure_points_at_the_proxy_when_one_is_set()
    test_http_get_returns_plain_text_and_truncates()
    test_http_get_rejects_bad_scheme_and_reports_http_errors()
    test_http_get_reads_charset_from_content_type()
    test_list_files_lists_skips_and_reports_truncation()
    test_list_files_rejects_escape_and_missing_dir()
    test_relative_root_is_resolved()
    test_read_text_file_window_and_truncation()
    test_read_text_file_rejects_binary_huge_and_escape()
    test_tools_drive_a_subrun_end_to_end()
    print("lab read-only tools all tests passed")
