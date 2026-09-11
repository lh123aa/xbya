"""浏览器工具集

三个工具：
- web_open    用默认浏览器打开网址（或把"打开百度"转成搜索）
- web_search  搜索引擎查询（打开结果页 + 返回可读描述）
- web_read    抓取网页正文并转纯文本

技术选型（对应 AGENTS.md 债务 D6）：
- 用 stdlib `webbrowser` + 已有依赖 `requests`，**不引入 Playwright**
- 只做"打开/搜索/读正文"，不做点击/填表等需要真实浏览器的操作
- 后续若需要真正的页面交互，再引入 Playwright 作为独立 Provider

依赖约束：requests 已在 requirements.txt；不新增第三方依赖。
"""

import base64
import ipaddress
import logging
import re
import socket
import urllib.parse
from html import unescape
from typing import Any, Dict, List, Optional

from agent.tools.base import BaseTool, ToolResult

logger = logging.getLogger(__name__)

#: 单页抓取字节上限
MAX_FETCH_BYTES = 2 * 1024 * 1024

#: 返回正文字符上限
MAX_TEXT_CHARS = 4000

#: HTTP 超时
HTTP_TIMEOUT = 12

#: 请求头（部分站点拒绝空 UA）
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

#: 搜索引擎模板
SEARCH_ENGINES = {
    "bing": "https://www.bing.com/search?q={q}",
    "baidu": "https://www.baidu.com/s?wd={q}",
    "google": "https://www.google.com/search?q={q}",
}

#: 站点别名（说"打开百度"→ 首页）
SITE_ALIASES = {
    "百度": "https://www.baidu.com",
    "bing": "https://www.bing.com",
    "必应": "https://www.bing.com",
    "谷歌": "https://www.google.com",
    "github": "https://github.com",
    "知乎": "https://www.zhihu.com",
    "b站": "https://www.bilibili.com",
    "哔哩哔哩": "https://www.bilibili.com",
    "淘宝": "https://www.taobao.com",
    "京东": "https://www.jd.com",
}

#: 危险协议（一律拒绝）
BLOCKED_SCHEMES = ("file:", "javascript:", "data:", "vbscript:", "ftp:")


# ══════════════════════════════════════════════════════
#  URL 与正文处理
# ══════════════════════════════════════════════════════

#: 形如域名的模式（严格 ASCII：避免把"报告.docx"这类中文文件名当网址）
_DOMAIN_RE = re.compile(
    r"^(?:www\.)?(?:[A-Za-z0-9\-]+\.)+[A-Za-z]{2,24}(?:/\S*)?$"
)


def normalize_url(raw: str) -> str:
    """把口语化网址补全为完整 URL

    "打开百度" → 站点别名表命中
    "openai.com" → 补 https://
    "报告.docx" → 空串（不是网址）
    """
    text = str(raw or "").strip()
    if not text:
        return ""

    lower = text.lower()
    for alias, url in SITE_ALIASES.items():
        if alias in lower:
            return url

    if lower.startswith(("http://", "https://")):
        return text

    # 形如 example.com / www.example.com/path（域名段必须是 ASCII）
    if _DOMAIN_RE.match(text):
        return "https://" + text

    return ""


def is_safe_url(url: str) -> bool:
    """URL 安全校验：拒绝危险协议与内网地址

    防 SSRF：不允许访问本机/内网地址（Agent 可能被诱导读取内网服务）。
    """
    if not url:
        return False

    parsed = urllib.parse.urlparse(url)
    if parsed.scheme.lower() not in ("http", "https"):
        return False
    # 双保险：BLOCKED_SCHEMES 目前全是非 http(s) 协议，上面一句已拦截；
    # 保留此判断以防后续新增形如 "http://恶意镜像" 的条目
    if any(url.lower().startswith(s) for s in BLOCKED_SCHEMES):  # pragma: no cover
        return False

    host = parsed.hostname
    if not host:
        return False

    # 内网/本机地址拒绝
    try:
        infos = socket.getaddrinfo(host, None)
        for info in infos:
            ip = ipaddress.ip_address(info[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                logger.warning("[browser] 拒绝内网地址: %s → %s", host, ip)
                return False
    except (socket.gaierror, ValueError):
        # 解析不了域名 → 保守拒绝
        logger.debug("[browser] 域名解析失败: %s", host)
        return False

    return True


#: 内联标签（删除时不引入空格，避免"你好<b>世界"变成"你好 世界"）
INLINE_TAGS = (
    "b|i|u|em|strong|span|a|small|big|sub|sup|code|kbd|samp|mark|"
    "abbr|cite|q|s|del|ins|label|font|time|bdi|bdo|ruby|rt|rp|wbr"
)

_INLINE_TAG_RE = re.compile(rf"(?is)</?(?:{INLINE_TAGS})\b[^>]*>")
_BLOCK_TAG_RE = re.compile(
    r"(?is)<br\s*/?>|</(?:p|div|li|tr|h[1-6]|section|article|header|footer|"
    r"blockquote|pre|table|ul|ol|dl|dd|dt|figure|figcaption|main|nav|aside)>"
)
_DROP_TAG_RE = re.compile(r"(?is)<(?:script|style|noscript|svg|head|iframe)[^>]*>.*?</[^>]+>")
_ANY_TAG_RE = re.compile(r"<[^>]+>")


def html_to_text(html: str) -> str:
    """HTML → 纯文本（去脚本/样式/标签，不引入 BeautifulSoup）

    标签处理分三类：
    - 丢弃整段：script / style / head / iframe
    - 转成换行：块级标签（p / div / br / li ...）
    - 直接删除：内联标签（b / span / a ...），**不引入空格**
    - 其余标签：退化为空格（保守处理未知标签）
    """
    if not html:
        return ""

    text = _DROP_TAG_RE.sub(" ", html)
    text = re.sub(r"(?is)<!--.*?-->", " ", text)
    text = _BLOCK_TAG_RE.sub("\n", text)
    text = _INLINE_TAG_RE.sub("", text)
    text = _ANY_TAG_RE.sub(" ", text)
    text = unescape(text)
    text = re.sub(r"[ \t\u00a0]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()


def extract_title(html: str) -> str:
    """提取页面标题"""
    m = re.search(r"(?is)<title[^>]*>(.*?)</title>", html or "")
    return unescape(m.group(1)).strip() if m else ""


#: Bing 每一条搜索结果都在一个 `<li class="b_algo">` 块里，块到下一个 `<li`、
#: 列表结束或字符串结尾为止（**结尾也必须算**，否则最后一条结果会被丢掉）
_ENGINE_RESULT_BLOCK_RE = re.compile(
    r'(?is)<li class="b_algo".*?(?=<li class="b_algo"|</ol>|\Z)'
)

#: 搜索结果标题的结构：`<h2>`/`<h3>` 里的链接（Bing 用 h2，DuckDuckGo 用 h2，
#: Google 用 h3）—— 按这个结构取才能避开页面导航栏
_RESULT_TITLE_LINK_RE = re.compile(
    r'(?is)<h[23][^>]*>\s*<a[^>]+href="(https?://[^"]+)"[^>]*>(.*?)</a>'
)

#: 兜底：任意链接（页面上没有 h2/h3 结构时用）
_ANY_LINK_RE = re.compile(
    r'(?is)<a[^>]+href="(https?://[^"]+)"[^>]*>(.{2,120}?)</a>'
)

#: Bing 把真实地址塞在跳转链接的 `u=a1<base64url>` 参数里
_BING_REDIRECT_U_RE = re.compile(r"[?&]u=a1([A-Za-z0-9_\-]+)")


def unwrap_engine_url(url: str) -> str:
    """把搜索引擎的跳转链接还原为真实地址（还原不出来就原样返回）

    Bing 的结果链接长这样：
        https://www.bing.com/ck/a?...&u=a1aHR0cHM6Ly93d3cucHl0aG9uLm9yZy8&ntb=1
    其中 `u=a1` 后面是目标地址的 base64url。还原后用户看到的是
    `https://www.python.org/` 而不是一长串跳转参数。
    """
    m = _BING_REDIRECT_U_RE.search(url or "")
    if not m:
        return url

    raw = m.group(1)
    try:
        padded = raw + "=" * (-len(raw) % 4)
        real = base64.urlsafe_b64decode(padded).decode("utf-8", "ignore")
    except Exception:
        return url
    return real if real.startswith(("http://", "https://")) else url


def _collect_links(
    scope: str,
    pattern: "re.Pattern",
    limit: int,
    results: List[Dict[str, str]],
    seen: set,
) -> None:
    """从一段 HTML 里按给定模式收集标题+链接（原地追加到 results）"""
    for m in pattern.finditer(scope):
        url = unwrap_engine_url(unescape(m.group(1)))
        title = html_to_text(m.group(2))
        if not title or len(title) < 2 or url in seen:
            continue
        seen.add(url)
        results.append({"title": title, "url": url})
        if len(results) >= limit:
            return


#: 相关性判断用的最小词长（ASCII）与中文片段长度
_RELEVANCE_MIN_WORD = 3
_RELEVANCE_CJK_NGRAM = 2


def results_look_relevant(query: str, results: List[Dict[str, str]]) -> bool:
    """粗略判断返回的结果是否真的和查询有关

    为什么要这道判断：搜索引擎对**脚本客户端**会返回"标题正确、结果却是别人 SERP"的
    页面（实测 Bing：查「上海天气」拿回波兰维基/保加利亚电视台/德国列车时刻表）。
    解析层无法识别这种诱饵，但**至少可以不把它当答案念给用户听** ——
    宁可说"已经在浏览器里搜了"，也不能说"上海天气搜到 5 条：MOTOR-TALK"。

    判断很宽松（标题里出现查询的任一个词/中文二字片段就算相关），
    只为拦住"完全无关"这一档，不做精细语义匹配。
    """
    titles = " ".join(str(r.get("title") or "") for r in results or [])
    if not titles:
        return False

    probe = str(query or "").strip().lower()
    if not probe:
        return True

    words = [w for w in re.split(r"[\s,，、。!！?？:：;；/]+", probe)
             if len(w) >= _RELEVANCE_MIN_WORD]
    grams = [probe[i:i + _RELEVANCE_CJK_NGRAM]
             for i in range(len(probe) - _RELEVANCE_CJK_NGRAM + 1)
             if all("\u4e00" <= c <= "\u9fff" for c in
                    probe[i:i + _RELEVANCE_CJK_NGRAM])]
    tokens = words + grams
    if not tokens:
        return True

    low = titles.lower()
    return any(t in low for t in tokens)


def extract_snippets(html: str, limit: int = 5) -> List[Dict[str, str]]:
    """从搜索结果页提取标题+链接

    提取顺序（**为了绕开实测出来的坑**）：

    1. 只在**结果块**里找（Bing 的 `<li class="b_algo">`）
    2. 退回 `<h2>`/`<h3>` 里的链接
    3. 最后才是任意链接 —— 纯兜底

    真正的坑（离线 fixture 可复核，见 `tests/agent/test_serp_fixtures.py`
    与 `docs/agent/evidence/p5/serp_parser.txt`）：

    **页面最前面的 `<a>` 是导航栏**（Web / 图片 / 视频 / 地图 / 资讯 / 航班，
    实测在整页的 33% 处），一上来就通用扫描的话，任何查询都只会得到
    「搜到 5 条：图片、视频」这种胡说，而真正的 10 条结果一条都拿不到。
    挡住它的是**第 2 层**的 h2/h3 结构（真实的 10 条结果都在 h2 里，
    实测在整页的 52% 处）。

    ⚠️ **不要以为第 1 层"必然改变了选中的内容"**：在这两份留档的必应页面上，
    第 1 层（结果块）与"只有第 2 层"的输出**逐条相同** —— 去掉第 1 层也一样能
    拿到那 5 条。第 1 层的作用是**把范围收窄到结果区**（页面其他部分未来
    多出一个 h2 时才会真正显出差别），而不是当下就在筛内容。
    这条是 P5-B5 实测出来的：这里原先的说法把「结构上合理」讲成了
    「每一层都实测必要」，已按实测结果改写。
    （旧说法里含一句被用例断言禁止的措辞，故此处不照抄原文 ——
    同 §16.4 那次"注释文字被自己的审计 grep 数进去"的教训。）
    """
    text_html = html or ""
    results: List[Dict[str, str]] = []
    seen: set = set()

    blocks = _ENGINE_RESULT_BLOCK_RE.findall(text_html)
    scopes = ["\n".join(blocks)] if blocks else []
    scopes.append(text_html)

    for scope in scopes:
        for pattern in (_RESULT_TITLE_LINK_RE, _ANY_LINK_RE):
            _collect_links(scope, pattern, limit, results, seen)
            if results:
                return results

    return results


# ══════════════════════════════════════════════════════
#  1. web_open
# ══════════════════════════════════════════════════════

class WebOpenTool(BaseTool):
    """用默认浏览器打开网址"""

    name = "web_open"
    description = "用默认浏览器打开一个网址，或打开常见网站（百度/知乎/GitHub 等）"
    risk_level = "low"
    timeout = 10
    params_schema = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "网址或网站名"},
        },
        "required": ["url"],
    }

    def execute(self, params: Dict[str, Any]) -> ToolResult:
        raw = str(params.get("url") or "").strip()
        if not raw:
            return ToolResult.fail("你想打开哪个网站呀？", emotion="think")

        url = normalize_url(raw)
        if not url:
            return ToolResult.fail(
                f"「{raw}」我看着不像网址呢，说个域名或者网站名试试？", emotion="think"
            )

        if not is_safe_url(url):
            return ToolResult.fail("这个地址我不能打开哦", emotion="surprised")

        try:
            import webbrowser
            opened = webbrowser.open(url, new=2)
        except Exception as e:
            logger.warning("[web_open] 打开失败: %s", e)
            return ToolResult.fail("浏览器没打开成功呢", emotion="sad")

        if not opened:
            return ToolResult.fail("系统里好像没找到可用的浏览器呢", emotion="sad")

        return ToolResult.ok(
            data={"url": url},
            summary=f"已经用浏览器打开啦",
            emotion="happy",
        )


# ══════════════════════════════════════════════════════
#  2. web_search
# ══════════════════════════════════════════════════════

class WebSearchTool(BaseTool):
    """搜索引擎查询"""

    name = "web_search"
    description = "在搜索引擎中查询关键词，并返回结果摘要"
    risk_level = "low"
    timeout = 20
    params_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "搜索关键词"},
            "engine": {"type": "string", "enum": list(SEARCH_ENGINES.keys()),
                       "description": "搜索引擎，默认 bing"},
            "open_browser": {"type": "boolean", "description": "是否用浏览器打开结果页"},
        },
        "required": ["query"],
    }

    def __init__(self, engine: str = "bing", default_open: bool = True) -> None:
        self._engine = engine if engine in SEARCH_ENGINES else "bing"
        self._default_open = default_open

    def execute(self, params: Dict[str, Any]) -> ToolResult:
        query = str(params.get("query") or "").strip()
        if not query:
            return ToolResult.fail("你想搜什么呀？", emotion="think")

        engine = str(params.get("engine") or self._engine).lower()
        if engine not in SEARCH_ENGINES:
            engine = self._engine

        url = SEARCH_ENGINES[engine].format(q=urllib.parse.quote(query))
        should_open = params.get("open_browser")
        should_open = self._default_open if should_open is None else bool(should_open)

        results: List[Dict[str, str]] = []
        try:
            import requests
            resp = requests.get(url, headers=HEADERS, timeout=HTTP_TIMEOUT)
            if resp.status_code == 200:
                resp.encoding = resp.apparent_encoding or "utf-8"
                found = extract_snippets(resp.text, limit=5)
                # 搜索引擎对脚本客户端会回"标题对、结果却是别人 SERP"的诱饵页，
                # 这种无关结果宁可不报（会退回"已经在浏览器里搜了"）
                if found and results_look_relevant(query, found):
                    results = found
                elif found:
                    logger.info("[web_search] 丢弃 %d 条与查询无关的结果（疑似引擎诱饵页）",
                                len(found))
        except Exception as e:
            logger.debug("[web_search] 抓取结果失败（不影响打开浏览器）: %s", e)

        if should_open:
            try:
                import webbrowser
                webbrowser.open(url, new=2)
            except Exception as e:
                logger.warning("[web_search] 打开浏览器失败: %s", e)

        if results:
            names = "、".join(r["title"][:20] for r in results[:2])
            summary = f"「{query}」搜到 {len(results)} 条：{names}"
        elif should_open:
            summary = f"已经在浏览器里搜「{query}」啦"
        else:
            summary = f"「{query}」没搜到结果呢"

        return ToolResult.ok(
            data={"query": query, "url": url, "results": results, "engine": engine},
            summary=summary,
            count=len(results),
            emotion="happy" if results else "talk",
        )


# ══════════════════════════════════════════════════════
#  3. web_read
# ══════════════════════════════════════════════════════

class WebReadTool(BaseTool):
    """抓取网页正文并转纯文本"""

    name = "web_read"
    description = "读取一个网页的正文内容"
    risk_level = "low"
    timeout = 25
    params_schema = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "网址"},
        },
        "required": ["url"],
    }

    def __init__(self, max_chars: int = MAX_TEXT_CHARS) -> None:
        self._max_chars = max(200, max_chars)

    def execute(self, params: Dict[str, Any]) -> ToolResult:
        raw = str(params.get("url") or "").strip()
        if not raw:
            return ToolResult.fail("你想读哪个网页呀？", emotion="think")

        url = normalize_url(raw)
        if not url:
            return ToolResult.fail(f"「{raw}」我看着不像网址呢", emotion="think")

        if not is_safe_url(url):
            return ToolResult.fail("这个地址我不能访问哦", emotion="surprised")

        try:
            import requests
            resp = requests.get(
                url, headers=HEADERS, timeout=HTTP_TIMEOUT, stream=True
            )
        except Exception as e:
            logger.warning("[web_read] 请求失败: %s", e)
            return ToolResult.fail("这个网页打不开呢，检查下网络？", emotion="sad")

        if resp.status_code != 200:
            return ToolResult.fail(
                f"这个网页返回了 {resp.status_code}，读不到内容呢", emotion="sad"
            )

        try:
            content = resp.raw.read(MAX_FETCH_BYTES, decode_content=True) or b""
        except Exception as e:
            logger.warning("[web_read] 读取失败: %s", e)
            return ToolResult.fail("网页内容读不下来呢", emotion="sad")

        encoding = resp.encoding or resp.apparent_encoding or "utf-8"
        try:
            html = content.decode(encoding, errors="replace")
        except (LookupError, UnicodeDecodeError):
            html = content.decode("utf-8", errors="replace")

        title = extract_title(html)
        text = html_to_text(html)
        if not text:
            return ToolResult.fail("这个页面好像没什么正文内容呢", emotion="talk")

        truncated = len(text) > self._max_chars
        shown = text[: self._max_chars] if truncated else text

        head = f"「{title}」" if title else "这个页面"
        preview = shown.replace("\n", " ")[:60]
        summary = f"{head}里面说：{preview}"
        if truncated:
            summary += "…（内容较长，先念这么多）"

        return ToolResult.ok(
            data={"url": url, "title": title, "text": shown, "chars": len(text)},
            summary=summary,
            truncated=truncated,
            emotion="talk",
        )


# ══════════════════════════════════════════════════════
#  注册辅助
# ══════════════════════════════════════════════════════

def all_browser_tools(engine: str = "bing", default_open: bool = True) -> List[BaseTool]:
    """构造全部浏览器工具实例"""
    return [
        WebOpenTool(),
        WebSearchTool(engine=engine, default_open=default_open),
        WebReadTool(),
    ]
