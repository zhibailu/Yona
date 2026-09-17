"""Yona 产品 · 工人的手 —— 只读取材料的基础工具集

**2026-09-16 用户批准从实验台毕业**(原住 test/lab/tools.py)。
落产品层而不是内核,理由就一条:它做的是**网络与文件 IO** ——
`core/README.md` 的边界是"纯逻辑",这里不是纯逻辑。
也不进 `character/`:它跟"她是谁"没关系,是能力不是人设。

用途:给子代理(`core/subrun.py`)一双手。
  `docs/protocols/SUBAGENT.md` §3.3 的结论是"没有工具的子代理是个更贵的包装";
  这批工具就是它的手,同时是"给子代理能力打分"的前置材料(§3.4)。

四个工具,全部**只读**:
  web_search(query)              搜 → [{title, url, snippet}]
  http_get(url)                  抓 URL → 纯文本(去标签、可截断)
  list_files(path, pattern)      列目录/匹配(带跳过表)
  read_text_file(path, ...)      读文本(行范围 + 上限)

三条纪律:
  1. **不给时间工具**:VISION 决策 8 已判死 —— 时间 = 每轮注入的死信息,不是工具;
     外部信息(搜索/抓取)才是工具。
  2. **不写角色文案**:返回结构化事实(JSON 信封),措辞留给内容层。
     `usage` 散文只写"怎么用得好"的功能性说明,不做人格化。
  3. **沙箱根必须显式传入,不给产品默认值**(不许替用户拍语义)。
     `read_text_file` / `list_files` 只能落在根之内。

**不做写类工具**:SUBAGENT.md §3.3「有副作用的动作留在主循环」那条划分线
还没拍 (待确认),不预判。

**返回值约定**:一律返回 JSON 字符串,统一信封
  {"ok": true, ...}  成功
  {"ok": false, "error": "..."}  可预期的失败(网络错、文件不存在、越界)
预期失败**不抛异常** —— 抛出去会被 ToolRegistry 压成 "tool error: ..." 一行,
对读结果的模型来说,结构化的失败信息更有用。参数类型错属于编程错误,照抛。

**网络失败的信封**(2026-09-16 用户定规矩,别改):
  error   = **真实异常原文**,`str(exc)` 逐字,不加工不替换;
  type    = 异常类名(便于搜/便于日志);
  kind    = 错误池 NET_ERROR_KINDS 里的**有限枚举**之一(认不出 = "unknown");
  message = 该类的**人话翻译**(只有池子里有的才翻,没有就写"不翻译")。
一句话:**报原话;翻译只在已知有限集合内做**。不许为某个具体症状写一次性分支 ——
那会把"某台机器的环境问题"当成产品缺陷(踩过,见 NET_ERROR_KINDS 上面的注)。

**已知缺口(产品化前必须补,现在如实标着)**:
  - **搜索是脆的**:默认 provider 用 DDG 的 `lite` 端点(html 端点实测被反爬挡)。
    对方改版就断 —— 接口可换 keyed API,但换哪家要拍。
  - 没有内网地址屏蔽(SSRF):本地实验不做,产品化必须做;
  - 重定向后的最终地址没有再校验;
  - 无速率限制 / 无重试 / 无缓存。
"""

from __future__ import annotations

import html as _html
import json
import re
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

import requests

from core.tools import Tool

# ============================================================
# 取回层(可注入 —— 测试不打网络)
# ============================================================

USER_AGENT = "Mozilla/5.0 (compatible; Yona/0.1)"


@dataclass
class FetchResult:
    """一次抓取的结果。status/body 分开,调用方自己决定怎么用。"""

    status: int
    url: str  # 重定向后的最终地址,没有就是原地址
    body: bytes
    content_type: str = ""


class Opener(Protocol):
    def __call__(self, url: str, timeout: float) -> FetchResult: ...


def requests_opener(max_bytes: int = 2_000_000) -> Opener:
    """默认取回实现:`requests`。

    (2026-09-16 用户指出)这里最早是 urllib,当时的理由是"纯标准库、不引第三方" ——
    但产品**早就依赖 requests** 了(openai_compat / llm_setup / api/config 都在用),
    白扛 urllib 的裸 OSError 和手工重定向,换不来任何东西。
    更要紧的是:requests 给出一套**有限、分得清**的异常类型,而这正是错误池
    需要的分类依据(见下面 NET_ERROR_KINDS);urllib 只会甩一个 URLError 加一句
    底层 WinError 文本,没法分类,只能靠猜 —— 上一版就是靠猜猜错的。

    非 2xx **不在这里抛**:状态码是真实事实,原样交给调用方判断。
    """

    def _open(url: str, timeout: float) -> FetchResult:
        resp = requests.get(
            url,
            headers={"User-Agent": USER_AGENT},
            timeout=timeout,
            allow_redirects=True,
            stream=True,
        )
        try:
            chunks: list[bytes] = []
            size = 0
            for chunk in resp.iter_content(8192):
                if not chunk:
                    continue
                chunks.append(chunk)
                size += len(chunk)
                if size >= max_bytes:
                    break
            body = b"".join(chunks)[:max_bytes]
            return FetchResult(
                status=resp.status_code,
                url=resp.url or url,  # 重定向后的最终地址
                body=body,
                content_type=resp.headers.get("Content-Type", ""),
            )
        finally:
            resp.close()

    return _open


# ============================================================
# HTML -> 纯文本(不引依赖)
# ============================================================

_DROP_ELEMENTS = re.compile(
    r"<(script|style|noscript|svg|head)\b[^>]*>.*?</\1\s*>", re.S | re.I
)
_BLOCK_END = re.compile(
    r"</(p|div|li|tr|h[1-6]|section|article|br|ul|ol|table|blockquote)\s*>", re.I
)
_BR = re.compile(r"<br\s*/?>", re.I)
_TAG = re.compile(r"<[^>]+>")
_INLINE_WS = re.compile(r"[ \t\r\f\v\u00a0]+")
_MANY_NL = re.compile(r"\n{3,}")


def html_to_text(raw: str) -> str:
    """极简 HTML 去标签。够用就好 —— 目标不是成为解析器,是把网页变成能读的文本。"""
    text = _DROP_ELEMENTS.sub(" ", raw)
    text = _BR.sub("\n", text)
    text = _BLOCK_END.sub("\n", text)
    text = _TAG.sub("", text)
    text = _html.unescape(text)
    text = _INLINE_WS.sub(" ", text)
    text = "\n".join(line.strip() for line in text.splitlines())
    return _MANY_NL.sub("\n\n", text).strip()


# ============================================================
# 搜索引擎(可插拔 —— 测试注入假 provider)
# ============================================================


class SearchProvider(Protocol):
    def search(self, query: str, count: int) -> list[dict[str, str]]: ...


class DuckDuckGoLite:
    """DuckDuckGo 的 `lite` 端点。无需 key。

    **实测记录(2026-09-15,本机)**:选这个端点是量出来的,不是猜的 ——

    | 候选 | 实测 |
    |---|---|
    | `html.duckduckgo.com/html/` | ❌ **202 + 反爬挑战页**(`anomaly`×67、`challenge`×13),0 条结果 |
    | `api.duckduckgo.com`(Instant Answer) | ❌ 不是通用搜索,普通查询恒返 0 条 |
    | `lite.duckduckgo.com/lite/` | ✅ 200、9 条 `result-link`、无挑战 —— **用它** |
    | 维基百科 API | ✅ 200、有结果,但**只限维基**(可作备选,见下) |

    仍然脆:对方改版就断。所以它只是**默认可替换的实现**,接口是 SearchProvider ——
    将来换 keyed API(Brave/Tavily/Serper 之类)只换实现,不动工具。
    """

    ENDPOINT = "https://lite.duckduckgo.com/lite/"

    def __init__(self, opener: Opener | None = None, timeout: float = 15.0) -> None:
        self._opener = opener or requests_opener()
        self._timeout = timeout

    def search(self, query: str, count: int) -> list[dict[str, str]]:
        url = self.ENDPOINT + "?" + urllib.parse.urlencode({"q": query})
        result = self._opener(url, self._timeout)
        if result.status >= 400:
            # 不静默返回 0 条 —— "端点挂了"和"确实没结果"是两件事,
            # 后者会让工人以为这世上真没这东西。
            raise requests.HTTPError(f"搜索端点返回 HTTP {result.status}")
        html = result.body.decode("utf-8", errors="replace")
        return parse_ddg_lite(html, count)


# lite 的锚点:`<a rel="nofollow" href="//duckduckgo.com/l/?uddg=..." class='result-link'>标题</a>`
_ANCHOR = re.compile(
    r"<a\b[^>]*class=['\"][^'\"]*result-link[^'\"]*['\"][^>]*>(.*?)</a>", re.S | re.I
)
_HREF = re.compile(r"href=['\"]([^'\"]+)['\"]", re.I)
_SNIPPET = re.compile(
    r"<td\b[^>]*class=['\"][^'\"]*result-snippet[^'\"]*['\"][^>]*>(.*?)</td>", re.S | re.I
)


def parse_ddg_lite(html: str, count: int) -> list[dict[str, str]]:
    """解析 lite 结果页。

    href 在锚点标签属性里(可能在 class 前后),所以先整段匹配锚点,再从锚点原文里
    抠 href —— 不假设属性顺序。snippet 是独立的 <td>,按出现顺序与链接配对
    (lite 结构里两者数量一致)。
    """
    anchors = list(_ANCHOR.finditer(html))
    snippets = [html_to_text(m.group(1)) for m in _SNIPPET.finditer(html)]
    out: list[dict[str, str]] = []
    for index, match in enumerate(anchors):
        if len(out) >= count:
            break
        href_match = _HREF.search(match.group(0))
        title = html_to_text(match.group(1))
        url = _unwrap_ddg(href_match.group(1)) if href_match else ""
        if not title or not url:
            continue
        out.append(
            {
                "title": title,
                "url": url,
                "snippet": snippets[index] if index < len(snippets) else "",
            }
        )
    return out


def _unwrap_ddg(href: str) -> str:
    """DDG 把结果链接包成 //duckduckgo.com/l/?uddg=<编码后的真地址>。"""
    if href.startswith("//"):
        href = "https:" + href
    parsed = urllib.parse.urlparse(href)
    if parsed.query:
        params = urllib.parse.parse_qs(parsed.query)
        target = params.get("uddg")
        if target:
            return target[0]
    return href


# ============================================================
# 路径沙箱
# ============================================================


def _resolve_in_root(root: Path, raw: str) -> Path:
    """把用户给的路径解析到根之内;越界直接拒绝(不静默截断)。"""
    if raw is None or str(raw).strip() == "":
        candidate = root
    else:
        candidate = Path(str(raw))
        if not candidate.is_absolute():
            candidate = root / candidate
    resolved = candidate.resolve()
    root_resolved = root.resolve()
    if resolved != root_resolved and root_resolved not in resolved.parents:
        raise ValueError(f"路径越界(只允许根之内): {raw}")
    return resolved


def _envelope_ok(**data: Any) -> str:
    return json.dumps({"ok": True, **data}, ensure_ascii=False)


def _envelope_err(text: str, **data: Any) -> str:
    """失败信封。第一个参数叫 text 不叫 message —— 因为 data 里可能有一个
    叫 `message` 的字段(错误池的人话翻译),同名会撞。"""
    return json.dumps({"ok": False, "error": text, **data}, ensure_ascii=False)


# ============================================================
# 网络错误池(有限集合)—— 先有池,再谈翻译
# ============================================================
# 规矩(2026-09-16 用户定):
#   ① 信封里的 `error` **永远是抓到的真实异常原文** —— 不加工、不替换、不缩写;
#   ② 只有在**已知有限集合**里,才额外给一句方便人看的翻译(`kind` + `message`);
#   ③ 认不出来就老实写 kind="unknown",**不硬翻译**。
#
# ⚠️ 反例(曾经写在这儿,已删):为"本机一个没在运行的代理"专门写了一段
#    "连接被它挡在最前面"。那是**把某台机器的环境症状当成产品缺陷**,
#    而且把推测当事实。症状归症状,池子归池子 —— 要加先往池子里加一类。
#
# 分类依据只有**异常类型**(requests 给的有限集合),不做文本匹配:
# 从错误字符串里捞关键字是脆的,而且那等于用猜的代替分类。
NET_ERROR_KINDS: dict[str, str] = {
    "timeout": "超时:对方在约定时间内没有回应。",
    "connection": "连不上对方。具体原因(拒绝/不可达/DNS)见 error 原文。",
    "proxy": "代理连接失败:环境里配的代理没接通。",
    "tls": "TLS 握手失败:证书或加密协商没过。",
    "redirect_loop": "重定向次数过多。",
    "http_status": "对方回了错误状态码(见 status / error)。",
    "unknown": "认不出的失败 —— 原文就在 error 里,不翻译。",
}


def _classify(exc: BaseException) -> str:
    """把异常归进错误池的某一类。

    顺序要紧:requests 的异常是有继承关系的 ——
    ProxyError / ConnectTimeout 都是 ConnectionError 的子类,
    先判具体再看一般,否则全会被归成 "connection"。
    """
    if isinstance(exc, requests.exceptions.ProxyError):
        return "proxy"
    if isinstance(exc, requests.exceptions.SSLError):
        return "tls"
    if isinstance(exc, requests.exceptions.TooManyRedirects):
        return "redirect_loop"
    if isinstance(exc, requests.exceptions.HTTPError):
        return "http_status"
    if isinstance(exc, requests.exceptions.Timeout):
        return "timeout"
    if isinstance(exc, requests.exceptions.ConnectionError):
        return "connection"
    return "unknown"


def _net_error(exc: BaseException, url: str, **data: Any) -> str:
    """网络类失败的统一收口。

    error = `str(exc)` **逐字原样**;type = 异常类名(便于搜/便于日志);
    kind/message = 错误池里的分类与翻译(认不出就是 "unknown")。
    """
    kind = _classify(exc)
    return _envelope_err(
        str(exc),
        url=url,
        type=type(exc).__name__,
        kind=kind,
        message=NET_ERROR_KINDS[kind],
        **data,
    )


# ============================================================
# 工具集工厂
# ============================================================

DEFAULT_SKIP_DIRS = (".git", "__pycache__", "node_modules", ".venv", ".idea", ".vscode")


def make_read_only_tools(
    root: str | Path,
    *,
    opener: Opener | None = None,
    search_provider: SearchProvider | None = None,
    timeout: float = 15.0,
    max_bytes: int = 2_000_000,
    fetch_chars: int = 4000,
    search_count: int = 5,
    skip_dirs: tuple[str, ...] = DEFAULT_SKIP_DIRS,
) -> list[Tool]:
    """造一套只读工具。`root` 必填 —— 沙箱根不给产品默认值。

    opener / search_provider 可注入:测试不打网络,真实验走探针。
    """
    root_path = Path(root).resolve()
    # ↑ 必须 resolve:`_resolve_in_root` 返回的是绝对路径,若 root 留在相对形态,
    #   后面 `child.relative_to(root_path)` 会当场 ValueError(真机踩到,见测试
    #   test_relative_root_is_resolved)。
    fetch = opener or requests_opener(max_bytes=max_bytes)
    searcher = search_provider or DuckDuckGoLite(opener=fetch, timeout=timeout)
    # 网络失败的信封里要报地址:优先用注入的搜索者自带的(测试替身没有 ENDPOINT)
    searcher_endpoint = getattr(searcher, "ENDPOINT", "") or "https://"

    # ---------- web_search ----------

    def _web_search(args: dict[str, Any]) -> str:
        query = str(args.get("query") or "").strip()
        if not query:
            return _envelope_err("query 不能为空")
        try:
            count = max(1, min(int(args.get("count") or search_count), 10))
        except (TypeError, ValueError):
            count = search_count
        try:
            hits = searcher.search(query, count)
        except Exception as exc:  # noqa: BLE001
            # 走错误池:error 是 str(exc) 原文,kind/message 是分类与翻译
            return _net_error(exc, searcher_endpoint, query=query)
        return _envelope_ok(query=query, count=len(hits), results=hits)

    # ---------- http_get ----------

    def _http_get(args: dict[str, Any]) -> str:
        url = str(args.get("url") or "").strip()
        if not url:
            return _envelope_err("url 不能为空")
        scheme = urllib.parse.urlparse(url).scheme.lower()
        if scheme not in ("http", "https"):
            return _envelope_err(f"只支持 http/https,收到: {scheme or '(空)'}", url=url)
        try:
            limit = max(200, min(int(args.get("max_chars") or fetch_chars), 20000))
        except (TypeError, ValueError):
            limit = fetch_chars
        try:
            result = fetch(url, timeout)
        except Exception as exc:  # noqa: BLE001
            return _net_error(exc, url)
        if result.status >= 400:
            # 状态码是真实事实,原样报;不在这里编原因
            return _envelope_err(
                f"HTTP {result.status}",
                url=result.url or url,
                status=result.status,
                kind="http_status",
                message=NET_ERROR_KINDS["http_status"],
            )

        body = result.body[:max_bytes]
        charset = _charset_of(result.content_type)
        raw = body.decode(charset, errors="replace")
        is_html = "html" in result.content_type.lower() or "<html" in raw[:2000].lower()
        text = html_to_text(raw) if is_html else raw.strip()
        truncated = len(text) > limit
        return _envelope_ok(
            url=result.url,
            status=result.status,
            content_type=result.content_type,
            format="text",
            chars=min(len(text), limit),
            truncated=truncated,
            text=text[:limit],
        )

    # ---------- list_files ----------

    def _list_files(args: dict[str, Any]) -> str:
        try:
            target = _resolve_in_root(root_path, str(args.get("path") or "."))
        except ValueError as exc:
            return _envelope_err(str(exc))
        pattern = str(args.get("pattern") or "*")
        try:
            limit = max(1, min(int(args.get("max") or 50), 500))
        except (TypeError, ValueError):
            limit = 50
        if not target.exists():
            return _envelope_err(f"目录不存在: {args.get('path')}", path=str(args.get("path")))
        if not target.is_dir():
            return _envelope_err(f"不是目录: {args.get('path')}", path=str(args.get("path")))

        entries: list[dict[str, Any]] = []
        truncated = False
        for child in sorted(target.glob(pattern), key=lambda p: (p.is_dir() is False, p.name)):
            if child.name in skip_dirs:
                continue
            if any(part in skip_dirs for part in child.relative_to(root_path).parts[:-1]):
                continue
            if len(entries) >= limit:
                truncated = True
                break
            try:
                size = child.stat().st_size if child.is_file() else None
            except OSError:
                size = None
            entries.append(
                {
                    "path": str(child.relative_to(root_path)).replace("\\", "/"),
                    "name": child.name,
                    "is_dir": child.is_dir(),
                    "size": size,
                }
            )
        return _envelope_ok(
            path=str(target.relative_to(root_path)).replace("\\", "/") or ".",
            pattern=pattern,
            count=len(entries),
            truncated=truncated,
            entries=entries,
        )

    # ---------- read_text_file ----------

    def _read_text_file(args: dict[str, Any]) -> str:
        raw_path = str(args.get("path") or "").strip()
        if not raw_path:
            return _envelope_err("path 不能为空")
        try:
            target = _resolve_in_root(root_path, raw_path)
        except ValueError as exc:
            return _envelope_err(str(exc))
        if not target.exists():
            return _envelope_err(f"文件不存在: {raw_path}", path=raw_path)
        if not target.is_file():
            return _envelope_err(f"不是文件: {raw_path}", path=raw_path)
        try:
            size = target.stat().st_size
        except OSError as exc:
            return _envelope_err(f"读不到文件属性: {exc}", path=raw_path)
        if size > max_bytes:
            return _envelope_err(
                f"文件过大({size} 字节 > {max_bytes}),先缩小范围", path=raw_path
            )
        try:
            raw = target.read_bytes()
        except OSError as exc:
            return _envelope_err(f"读取失败: {exc}", path=raw_path)
        if b"\x00" in raw[:4096]:
            return _envelope_err("看着是二进制文件,不读", path=raw_path)

        text = raw.decode("utf-8", errors="replace")
        lines = text.splitlines()
        try:
            start = max(1, int(args.get("start_line") or 1))
        except (TypeError, ValueError):
            start = 1
        try:
            max_lines = max(1, min(int(args.get("max_lines") or 200), 2000))
        except (TypeError, ValueError):
            max_lines = 200
        window = lines[start - 1 : start - 1 + max_lines]
        return _envelope_ok(
            path=str(target.relative_to(root_path)).replace("\\", "/"),
            total_lines=len(lines),
            start_line=start,
            returned_lines=len(window),
            truncated=(start - 1 + len(window)) < len(lines),
            text="\n".join(window),
        )

    # ---------- 装配成 Tool 三件套 ----------
    # usage 只写功能性说明(怎么用得好),不做人格化 —— 角色文案属于内容层。

    return [
        Tool(
            name="web_search",
            description="用关键词搜索网页,返回标题 / 链接 / 摘要的列表。用于获取系统不知道的外部信息。",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索关键词"},
                    "count": {"type": "integer", "description": "要几条结果,默认 5,最多 10"},
                },
                "required": ["query"],
            },
            func=_web_search,
            usage=(
                "只拿到标题和摘要,正文要再用 http_get 打开具体链接;"
                "搜完想回答得准,通常还要打开最相关的一两条。"
            ),
        ),
        Tool(
            name="http_get",
            description="抓取一个 http/https 网址,把网页转成纯文本返回。用于读某个具体页面的内容。",
            parameters={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "完整的 http/https 网址"},
                    "max_chars": {"type": "integer", "description": "最多返回多少字符,默认 4000"},
                },
                "required": ["url"],
            },
            func=_http_get,
            usage=(
                "配合 web_search 用:先搜到链接,再打开最相关的一两条;"
                "返回里有 truncated 字段,说明被截断了,别当成全文。"
            ),
        ),
        Tool(
            name="list_files",
            description="列出目录下的文件与子目录(可带通配符),用于找文件。",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "相对根目录的路径,默认根目录"},
                    "pattern": {"type": "string", "description": "通配符,如 *.md,默认 *"},
                    "max": {"type": "integer", "description": "最多几条,默认 50"},
                },
            },
            func=_list_files,
            usage="先 list 找到路径,再用 read_text_file 读内容;别猜路径。",
        ),
        Tool(
            name="read_text_file",
            description="读取一个文本文件的内容(可指定起始行与行数上限)。",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "相对根目录的文件路径"},
                    "start_line": {"type": "integer", "description": "从第几行开始,默认 1"},
                    "max_lines": {"type": "integer", "description": "最多读几行,默认 200"},
                },
                "required": ["path"],
            },
            func=_read_text_file,
            usage="文件很长时用 start_line/max_lines 分段读;返回里 truncated 说明后面还有。",
        ),
    ]


def _charset_of(content_type: str) -> str:
    """从 Content-Type 里抠 charset;没有就按 utf-8 猜。"""
    for part in (content_type or "").split(";"):
        part = part.strip()
        if part.lower().startswith("charset="):
            return part.split("=", 1)[1].strip().strip('"') or "utf-8"
    return "utf-8"
