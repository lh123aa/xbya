"""浏览器工具测试

覆盖 browser_tools.py：
- URL 规范化（站点别名、补协议）
- SSRF 防护（内网/本机地址拒绝）
- HTML → 文本 / 标题 / 结果提取
- web_open / web_search / web_read 三个工具（网络与浏览器均被 mock）
"""

import base64
import os
import sys
from pathlib import Path
from unittest import mock

import pytest

project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from agent.tools.browser_tools import (
    MAX_TEXT_CHARS,
    SEARCH_ENGINES,
    SITE_ALIASES,
    WebOpenTool,
    WebReadTool,
    WebSearchTool,
    all_browser_tools,
    extract_snippets,
    extract_title,
    results_look_relevant,
    unwrap_engine_url,
    html_to_text,
    is_safe_url,
    normalize_url,
)


# ══════════════════════════════════════════════════
#  URL 规范化
# ══════════════════════════════════════════════════

class TestNormalizeUrl:
    """normalize_url"""

    @pytest.mark.parametrize("alias", list(SITE_ALIASES.keys()))
    def test_site_aliases(self, alias):
        """站点别名解析为首页"""
        assert normalize_url(f"打开{alias}") == SITE_ALIASES[alias]

    @pytest.mark.parametrize("raw,expected", [
        ("example.com", "https://example.com"),
        ("www.example.com", "https://www.example.com"),
        ("example.com/path", "https://example.com/path"),
        ("http://example.com", "http://example.com"),
        ("https://example.com", "https://example.com"),
    ])
    def test_url_completion(self, raw, expected):
        assert normalize_url(raw) == expected

    @pytest.mark.parametrize("raw", ["", "   ", "随便什么", "报告.docx"])
    def test_non_url(self, raw):
        """非网址返回空串"""
        assert normalize_url(raw) == ""


class TestIsSafeUrl:
    """SSRF 防护"""

    def test_public_ok(self):
        """公网地址放行"""
        assert is_safe_url("https://example.com") is True
        assert is_safe_url("http://www.bing.com") is True

    @pytest.mark.parametrize("url", [
        "http://localhost/admin",
        "http://127.0.0.1:8080/",
        "http://192.168.1.1/",
        "http://10.0.0.1/",
        "http://169.254.169.254/latest/meta-data/",   # 云元数据
    ])
    def test_private_blocked(self, url):
        """内网/本机/元数据地址被拒绝"""
        assert is_safe_url(url) is False

    @pytest.mark.parametrize("url", [
        "file:///etc/passwd",
        "javascript:alert(1)",
        "data:text/html,<script>x</script>",
        "ftp://example.com",
        "",
        "not a url",
    ])
    def test_dangerous_scheme_blocked(self, url):
        """危险协议与非 URL 被拒绝"""
        assert is_safe_url(url) is False

    def test_unresolvable_domain_blocked(self):
        """无法解析的域名保守拒绝"""
        assert is_safe_url("https://this-domain-does-not-exist-zzz9.invalid") is False


# ══════════════════════════════════════════════════
#  HTML 处理
# ══════════════════════════════════════════════════

class TestHtmlProcessing:
    """HTML → 文本"""

    def test_strip_tags(self):
        """去标签保留文本"""
        assert html_to_text("<p>你好<b>世界</b></p>") == "你好世界"

    def test_remove_script_style(self):
        """脚本与样式被移除"""
        html = "<script>var x=1;</script><style>.a{}</style><p>正文</p>"
        text = html_to_text(html)
        assert "正文" in text
        assert "var x" not in text and ".a{" not in text

    def test_block_tags_become_newlines(self):
        """块级标签转行"""
        text = html_to_text("<div>一</div><div>二</div>")
        assert "一" in text and "二" in text

    def test_entities_unescaped(self):
        """HTML 实体解码"""
        assert "&" in html_to_text("<p>a &amp; b</p>")

    def test_comments_removed(self):
        """注释被移除"""
        assert "hidden" not in html_to_text("<!-- hidden --><p>v</p>")

    def test_empty(self):
        assert html_to_text("") == ""
        assert html_to_text(None) == ""

    def test_extract_title(self):
        """标题提取"""
        assert extract_title("<title>页面标题</title>") == "页面标题"
        assert extract_title("<TITLE>大写</TITLE>") == "大写"

    def test_extract_title_missing(self):
        assert extract_title("<p>无标题</p>") == ""
        assert extract_title("") == ""

    def test_extract_title_entities(self):
        """标题中的实体被解码"""
        assert extract_title("<title>a &amp; b</title>") == "a & b"

    def test_extract_snippets(self):
        """链接提取"""
        html = """
        <a href="https://a.com">标题一</a>
        <a href="https://b.com">标题二</a>
        <a href="https://a.com">重复</a>
        <a href="/relative">相对链接</a>
        """
        items = extract_snippets(html)
        urls = [i["url"] for i in items]
        assert "https://a.com" in urls
        assert "https://b.com" in urls
        assert len(urls) == len(set(urls)), "应去重"
        assert all(u.startswith("http") for u in urls)

    def test_extract_snippets_limit(self):
        """数量上限"""
        html = "".join(f'<a href="https://s{i}.com">标题{i}</a>' for i in range(20))
        assert len(extract_snippets(html, limit=3)) == 3

    def test_extract_snippets_empty(self):
        assert extract_snippets("") == []
        assert extract_snippets("<p>无链接</p>") == []

    def test_extract_snippets_skips_nav_before_results(self):
        """导航栏排在结果前面时，必须优先取结果块里的链接

        这是真实网络验证抓出来的回归：Bing 结果页最前面的 `<a>` 是导航栏
        （Web/图片/视频/地图/资讯），通用链接扫描会把它们当成搜索结果，
        于是任何查询都只得到"搜到 5 条：图片、视频"。
        """
        html = """
        <nav><a href="https://www.bing.com/nav1">图片</a>
             <a href="https://www.bing.com/nav2">视频</a></nav>
        <ol id="b_results">
          <li class="b_algo"><h2><a href="https://a.com/real">真结果一</a></h2></li>
          <li class="b_algo"><h2><a href="https://b.com/real">真结果二</a></h2></li>
        </ol>
        """
        items = extract_snippets(html)
        assert [i["title"] for i in items] == ["真结果一", "真结果二"]
        assert all("bing.com" not in i["url"] for i in items)

    def test_extract_snippets_falls_back_to_h23(self):
        """没有结果块时，退到 h2/h3 里的链接（而不是整页任意链接）"""
        html = ('<footer><a href="https://junk.com/x">页脚推荐位</a></footer>'
                '<h3><a href="https://good.com">正文标题</a></h3>')
        assert [i["title"] for i in extract_snippets(html)] == ["正文标题"]

    def test_extract_snippets_uses_generic_when_no_structure(self):
        """完全没有结构时仍是通用扫描（保证老行为不丢）"""
        html = '<a href="https://a.com">标题一</a>'
        assert extract_snippets(html) == [
            {"title": "标题一", "url": "https://a.com"}]

    def test_unwrap_engine_url(self):
        """Bing 跳转链接还原成真实地址"""
        real = "https://www.python.org/"
        wrapped = ("https://www.bing.com/ck/a?!&p=abc&u=a1"
                   + base64.urlsafe_b64encode(real.encode()).decode().rstrip("=")
                   + "&ntb=1")
        assert unwrap_engine_url(wrapped) == real
        # 普通网址原样返回
        assert unwrap_engine_url("https://example.com/x") == "https://example.com/x"
        # 解不出来 / 解出来不是 http(s) 时原样返回
        assert unwrap_engine_url("https://x.com/?u=a1!!!") == "https://x.com/?u=a1!!!"
        bad = base64.urlsafe_b64encode(b"ftp://nope").decode().rstrip("=")
        raw = f"https://x.com/?u=a1{bad}"
        assert unwrap_engine_url(raw) == raw

    def test_unwrap_engine_url_survives_decoder_failure(self, monkeypatch):
        """解码器万一抛异常：**原样返回，绝不让搜索挂掉**

        正则只捕获合法 base64 字符，所以正常输入下这条 except 走不到；
        这里把解码器打桩成抛异常来覆盖它——这个守卫的意义正是"解码出意外时
        退化成不做还原"，而不是让一次搜索整个失败。
        """
        def boom(*_a, **_kw):
            raise ValueError("解码器炸了")

        monkeypatch.setattr(base64, "urlsafe_b64decode", boom)
        raw = "https://x.com/?u=a1SGVsbG8&ntb=1"
        assert unwrap_engine_url(raw) == raw

    def test_results_look_relevant(self):
        """相关性护栏：拦住"标题对、结果却是别人 SERP"的诱饵页"""
        hits = [{"title": "上海天气 一周预报", "url": "https://t.com"}]
        junk = [{"title": "MOTOR-TALK - Europas größte Community",
                 "url": "https://motor-talk.de"}]
        assert results_look_relevant("上海天气", hits) is True
        assert results_look_relevant("上海天气", junk) is False
        assert results_look_relevant("Python release", hits) is False
        # 没有结果 / 没有可用词元时不做否判定
        assert results_look_relevant("上海天气", []) is False
        assert results_look_relevant("", hits) is True
        assert results_look_relevant("…", hits) is True


# ══════════════════════════════════════════════════
#  web_open
# ══════════════════════════════════════════════════

class TestWebOpen:
    """打开网页"""

    def test_empty(self):
        r = WebOpenTool().execute({"url": ""})
        assert r.success is False
        assert "哪个网站" in r.summary

    def test_invalid_url(self):
        r = WebOpenTool().execute({"url": "随便什么什么"})
        assert r.success is False
        assert "不像网址" in r.summary

    def test_unsafe_url(self):
        r = WebOpenTool().execute({"url": "http://127.0.0.1/"})
        assert r.success is False
        assert "不能打开" in r.summary

    def test_open_success(self):
        """正常打开（mock webbrowser）"""
        with mock.patch("webbrowser.open", return_value=True) as m:
            r = WebOpenTool().execute({"url": "example.com"})
        assert r.success is True
        assert r.data["url"] == "https://example.com"
        m.assert_called_once()

    def test_open_returns_false(self):
        """webbrowser 返回 False → 提示无浏览器"""
        with mock.patch("webbrowser.open", return_value=False):
            r = WebOpenTool().execute({"url": "example.com"})
        assert r.success is False
        assert "浏览器" in r.summary

    def test_open_exception(self):
        """打开异常被兜底"""
        with mock.patch("webbrowser.open", side_effect=OSError("boom")):
            r = WebOpenTool().execute({"url": "example.com"})
        assert r.success is False

    def test_site_alias(self):
        """站点别名可用"""
        with mock.patch("webbrowser.open", return_value=True):
            r = WebOpenTool().execute({"url": "百度"})
        assert r.success is True
        assert "baidu" in r.data["url"]


# ══════════════════════════════════════════════════
#  web_search
# ══════════════════════════════════════════════════

class FakeResponse:
    """requests 响应桩"""

    def __init__(self, text="", status_code=200, encoding="utf-8"):
        self.text = text
        self.status_code = status_code
        self.encoding = encoding
        self.apparent_encoding = encoding


class TestWebSearch:
    """搜索"""

    def test_empty_query(self):
        r = WebSearchTool().execute({"query": ""})
        assert r.success is False
        assert "搜什么" in r.summary

    def test_search_with_results(self):
        """抓到结果时汇总（假数据照真实 Bing 结构写：结果在 li.b_algo 里）"""
        html = ('<li class="b_algo"><h2><a href="https://a.com">天气 今天</a></h2></li>'
                '<li class="b_algo"><h2><a href="https://b.com">天气 一周</a></h2></li>')
        with mock.patch("requests.get", return_value=FakeResponse(html)), \
             mock.patch("webbrowser.open", return_value=True):
            r = WebSearchTool().execute({"query": "天气"})
        assert r.success is True
        assert r.data["query"] == "天气"
        assert len(r.data["results"]) == 2
        assert "天气" in r.summary

    def test_search_drops_irrelevant_decoy_results(self):
        """引擎诱饵页（标题对、结果无关）宁可不报，退回"已打开浏览器" """
        html = ('<li class="b_algo"><h2><a href="https://x.de">MOTOR-TALK</a></h2></li>'
                '<li class="b_algo"><h2><a href="https://y.bg">БНТ 1</a></h2></li>')
        with mock.patch("requests.get", return_value=FakeResponse(html)), \
             mock.patch("webbrowser.open", return_value=True):
            r = WebSearchTool().execute({"query": "上海天气"})
        assert r.data["results"] == []
        assert "浏览器" in r.summary

    def test_search_no_results(self):
        """抓不到结果但已打开浏览器"""
        with mock.patch("requests.get", return_value=FakeResponse("<p>无</p>")), \
             mock.patch("webbrowser.open", return_value=True):
            r = WebSearchTool().execute({"query": "xyz"})
        assert r.success is True
        assert "浏览器" in r.summary

    def test_search_open_disabled(self):
        """不打开浏览器且无结果"""
        with mock.patch("requests.get", return_value=FakeResponse("<p>无</p>")):
            r = WebSearchTool(default_open=False).execute({"query": "xyz"})
        assert r.success is True
        assert "没搜到" in r.summary

    def test_search_request_fails(self):
        """网络失败不影响打开浏览器"""
        with mock.patch("requests.get", side_effect=OSError("net down")), \
             mock.patch("webbrowser.open", return_value=True) as m:
            r = WebSearchTool().execute({"query": "天气"})
        assert r.success is True
        m.assert_called_once()

    def test_search_status_error(self):
        """非 200 状态码"""
        with mock.patch("requests.get", return_value=FakeResponse("", status_code=500)), \
             mock.patch("webbrowser.open", return_value=True):
            r = WebSearchTool().execute({"query": "x"})
        assert r.success is True
        assert r.data["results"] == []

    @pytest.mark.parametrize("engine", list(SEARCH_ENGINES.keys()))
    def test_engines(self, engine):
        """各搜索引擎 URL 正确"""
        with mock.patch("requests.get", return_value=FakeResponse("")), \
             mock.patch("webbrowser.open", return_value=True):
            r = WebSearchTool().execute({"query": "test", "engine": engine})
        assert r.data["engine"] == engine
        assert "test" in r.data["url"]

    def test_unknown_engine_falls_back(self):
        """未知引擎回退默认"""
        with mock.patch("requests.get", return_value=FakeResponse("")), \
             mock.patch("webbrowser.open", return_value=True):
            r = WebSearchTool(engine="bing").execute({"query": "x", "engine": "yandex"})
        assert r.data["engine"] == "bing"

    def test_query_is_encoded(self):
        """关键词被 URL 编码"""
        with mock.patch("requests.get", return_value=FakeResponse("")), \
             mock.patch("webbrowser.open", return_value=True):
            r = WebSearchTool().execute({"query": "中文 空格"})
        assert "%" in r.data["url"]

    def test_browser_open_failure_isolated(self):
        """打开浏览器失败不影响结果返回"""
        html = '<a href="https://a.com">结果</a>'
        with mock.patch("requests.get", return_value=FakeResponse(html)), \
             mock.patch("webbrowser.open", side_effect=OSError("no browser")):
            r = WebSearchTool().execute({"query": "x"})
        assert r.success is True
        assert len(r.data["results"]) == 1


# ══════════════════════════════════════════════════
#  web_read
# ══════════════════════════════════════════════════

class RawResponse(FakeResponse):
    """带 raw 流的响应桩"""

    def __init__(self, content: bytes, status_code=200, encoding="utf-8"):
        super().__init__("", status_code, encoding)

        class Raw:
            def read(inner, n, decode_content=True):
                return content[:n]

        self.raw = Raw()


class TestWebRead:
    """读网页"""

    def test_empty_url(self):
        r = WebReadTool().execute({"url": ""})
        assert r.success is False
        assert "哪个网页" in r.summary

    def test_invalid_url(self):
        r = WebReadTool().execute({"url": "不是网址"})
        assert r.success is False

    def test_unsafe_url(self):
        r = WebReadTool().execute({"url": "http://192.168.1.1/"})
        assert r.success is False
        assert "不能访问" in r.summary

    def test_read_success(self):
        """正常读取"""
        html = "<html><title>标题</title><body><p>正文内容在这里</p></body></html>"
        with mock.patch("requests.get", return_value=RawResponse(html.encode())):
            r = WebReadTool().execute({"url": "example.com"})
        assert r.success is True
        assert r.data["title"] == "标题"
        assert "正文内容在这里" in r.data["text"]
        assert "标题" in r.summary

    def test_read_truncates(self):
        """超长正文被截断"""
        html = "<p>" + "字" * (MAX_TEXT_CHARS + 500) + "</p>"
        with mock.patch("requests.get", return_value=RawResponse(html.encode())):
            r = WebReadTool().execute({"url": "example.com"})
        assert r.truncated is True
        assert len(r.data["text"]) == MAX_TEXT_CHARS
        assert "内容较长" in r.summary

    def test_read_empty_page(self):
        """无正文"""
        with mock.patch("requests.get", return_value=RawResponse(b"<html></html>")):
            r = WebReadTool().execute({"url": "example.com"})
        assert r.success is False
        assert "没" in r.summary

    def test_request_fails(self):
        """请求异常"""
        with mock.patch("requests.get", side_effect=OSError("net down")):
            r = WebReadTool().execute({"url": "example.com"})
        assert r.success is False
        assert "打不开" in r.summary

    def test_non_200(self):
        """非 200 状态码"""
        with mock.patch("requests.get", return_value=RawResponse(b"", status_code=404)):
            r = WebReadTool().execute({"url": "example.com"})
        assert r.success is False
        assert "404" in r.summary

    def test_read_failure(self):
        """读取流失败"""
        resp = RawResponse(b"x")

        def boom(n, decode_content=True):
            raise OSError("read failed")

        resp.raw.read = boom
        with mock.patch("requests.get", return_value=resp):
            r = WebReadTool().execute({"url": "example.com"})
        assert r.success is False

    def test_bad_encoding_falls_back(self):
        """编码异常回退 utf-8"""
        resp = RawResponse("<p>中文</p>".encode("utf-8"))
        resp.encoding = "no-such-codec"
        resp.apparent_encoding = None
        with mock.patch("requests.get", return_value=resp):
            r = WebReadTool().execute({"url": "example.com"})
        assert r.success is True

    def test_no_title(self):
        """无标题时用通用描述"""
        with mock.patch("requests.get", return_value=RawResponse(b"<p>content</p>")):
            r = WebReadTool().execute({"url": "example.com"})
        assert r.success is True
        assert "这个页面" in r.summary

    def test_custom_max_chars(self):
        """自定义长度上限（含下限保护）"""
        assert WebReadTool(max_chars=500)._max_chars == 500
        assert WebReadTool(max_chars=1)._max_chars == 200      # 低于下限 → 抬到 200
        assert WebReadTool()._max_chars == MAX_TEXT_CHARS      # 默认值


# ══════════════════════════════════════════════════
#  注册辅助
# ══════════════════════════════════════════════════

class TestRegistryHelper:
    """all_browser_tools"""

    def test_returns_three_tools(self):
        tools = all_browser_tools()
        assert len(tools) == 3
        assert {t.name for t in tools} == {"web_open", "web_search", "web_read"}

    def test_all_low_risk(self):
        """浏览器工具均为低风险"""
        assert all(t.risk_level == "low" for t in all_browser_tools())

    def test_engine_passed(self):
        """引擎参数被传递"""
        tools = all_browser_tools(engine="baidu")
        search = next(t for t in tools if t.name == "web_search")
        assert search._engine == "baidu"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
