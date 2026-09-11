# -*- coding: utf-8 -*-
"""SERP 解析器**离线** fixture 测试（P5-B5）

与 `tests/agent/test_browser_tools.py` 里的解析用例**分工不同**：

| | 那份 | 这份 |
|---|---|---|
| 输入 | 手写的小段 HTML | **P4 真实抓取的整页原文** |
| 能证明 | 我按自己以为的结构写的东西能被解析 | 当时服务器真的发回来的页面能被解析 |

后者是前者证明不了的：`extract_snippets` 的三层提取顺序（结果块 → h2/h3
→ 任意链接）就是**照着真实页面的坑**长出来的（第一层避开导航栏；只用
h2/h3 会命中页脚推荐位）。手写 HTML 永远测不出这两个坑是否又被踩回去。

fixture 的来源与完整性由 `tests/fixtures/serp/manifest.json` 的 **MD5**
保证 —— 它对着 P4 当时的留档，不是"一份看起来像必应结果页的文件"。
若有人手改了这些 HTML，MD5 用例会立刻变红。

本文件全部**不发网络请求**：这正是 B5 的意义（把"必须有网才能测"的
解析器行为变成可回归的离线判据）。
"""
import hashlib
import json
import sys
from pathlib import Path

import pytest

project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

import agent.tools.browser_tools as bt          # noqa: E402
from agent.tools.browser_tools import (      # noqa: E402
    extract_snippets,
    extract_title,
    html_to_text,
    results_look_relevant,
)

#: 解析器内部的两个正则 —— 这几个用例要断言的是"**哪一层**在挡导航栏"，
#: 必须能分别驱动两层，所以直接取它们；这不是"测试摸私有实现"，
#: 而是这条判据本身就是关于分层的
_RESULT_TITLE_LINK_RE = bt._RESULT_TITLE_LINK_RE
_ANY_LINK_RE = bt._ANY_LINK_RE

FIXTURES = project_root / "tests" / "fixtures" / "serp"
MANIFEST = FIXTURES / "manifest.json"
P4_RUNS = (project_root / "docs" / "agent" / "evidence" / "p4"
           / "_tmp_c4_runs.json")

QUERY = "上海天气"


def _load() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


MANIFEST_DATA = _load()
ENTRIES = MANIFEST_DATA["entries"]
BY_ENGINE: dict = {}
for _e in ENTRIES:
    BY_ENGINE.setdefault(_e["engine"], []).append(_e)

BING = BY_ENGINE.get("bing", [])
BAIDU = BY_ENGINE.get("baidu", [])
GOOGLE = BY_ENGINE.get("google", [])


# ══════════════════════════════════════════════════
#  一、fixture 本身是不是"真的那一份"
# ══════════════════════════════════════════════════

class TestFixtureIntegrity:
    """先钉住"输入没被换过"，后面的结论才有意义"""

    def test_manifest_exists(self):
        assert MANIFEST.is_file(), "缺 manifest.json —— fixture 没有来源声明"

    def test_three_engines_covered(self):
        """三个引擎各一份以上 —— 只测必应就不是"三形态对比"了"""
        assert set(BY_ENGINE) == {"bing", "baidu", "google"}

    def test_bing_has_two_runs(self):
        """必应留了两次抓取：两次内容不同（诱饵页每次不一样），
        只有一条会被当成"稳定结果" —— 所以要两份都留着"""
        assert len(BING) >= 2

    @pytest.mark.parametrize("entry", ENTRIES, ids=lambda e: e["file"])
    def test_bytes_match_manifest(self, entry):
        raw = (FIXTURES / entry["file"]).read_bytes()
        assert len(raw) == entry["bytes"], (
            f"{entry['file']} 字节数与清单不符：文件被改过？"
        )

    @pytest.mark.parametrize("entry", ENTRIES, ids=lambda e: e["file"])
    def test_md5_match_manifest(self, entry):
        """MD5 是这份 fixture 与"当时服务器发回来的字节"之间唯一的那根线"""
        raw = (FIXTURES / entry["file"]).read_bytes()
        assert hashlib.md5(raw).hexdigest().upper() == entry["md5"]

    @pytest.mark.parametrize("entry", ENTRIES, ids=lambda e: e["file"])
    def test_source_file_still_exists(self, entry):
        """fixture 是 P4 留档的复制品 —— 原件必须在，
        否则"可追溯到真实抓取"这句话就断了"""
        assert (project_root / entry["source"]).is_file()

    def test_manifest_declares_provenance(self):
        assert "evidence/p4" in MANIFEST_DATA["provenance"]
        assert MANIFEST_DATA["why_real_capture"]


# ══════════════════════════════════════════════════
#  二、解析器现在跑真页面，结果与清单逐条一致
# ══════════════════════════════════════════════════

class TestRealBingPage:
    """必应：**唯一可用**的引擎，判定层必须是「结果块」而不是兜底"""

    @pytest.mark.parametrize("entry", BING, ids=lambda e: e["file"])
    def test_page_declares_the_query(self, entry):
        """页面标题自己带着查询词 —— 这是"它响应的确实是这个查询"的页面内证据，
        比文件名可信（文件名是人起的）"""
        html = (FIXTURES / entry["file"]).read_text(encoding="utf-8",
                                                    errors="replace")
        assert extract_title(html) == f"{QUERY} - 搜索"

    @pytest.mark.parametrize("entry", BING, ids=lambda e: e["file"])
    def test_parses_five_results(self, entry):
        html = (FIXTURES / entry["file"]).read_text(encoding="utf-8",
                                                    errors="replace")
        assert len(extract_snippets(html, limit=5)) == entry["parsed"] == 5

    @pytest.mark.parametrize("entry", BING, ids=lambda e: e["file"])
    def test_found_via_result_blocks_not_fallback(self, entry):
        """**关键**：必应的结果必须是从 `<li class="b_algo">` 里抽的。

        如果哪天有人把第一层删了，兜底层仍会返回 5 条，条数断言照样过 ——
        但内容会变成导航栏/页脚。所以这里断言的是**判定层**，不是条数。
        """
        html = (FIXTURES / entry["file"]).read_text(encoding="utf-8",
                                                    errors="replace")
        assert entry["result_blocks"] >= 10, "真页面里结果块数量应远多于 limit"
        assert entry["parsed_layer"] == '结果块（<li class="b_algo">）'

    @pytest.mark.parametrize("entry", BING, ids=lambda e: e["file"])
    def test_navigation_junk_is_ahead_of_results_and_must_not_be_picked(
            self, entry):
        """**这份 fixture 最值钱的一条**：页面最前面的 `<a>` 是导航栏。

        实测位置：导航栏（图片/视频/地图/资讯/航班）在整页 **33%** 处，
        真实结果块在 **50%** 处、真实 `<h2>` 在 **52%** 处。
        一上来就通用扫描 → 只会得到「搜到 5 条：图片、视频」这种胡说。

        断言两件事：
          1. 那 5 个导航项**确实在结果之前**（这条 fixture 真的带这个坑）
          2. 解析器**没有**把它们当成结果
        """
        html = (FIXTURES / entry["file"]).read_text(encoding="utf-8",
                                                    errors="replace")
        first_algo = html.find('class="b_algo"')
        assert first_algo > 0

        # 导航项确实排在结果区之前（否则这条 fixture 证明不了这个坑）
        for junk in ("图片", "视频", "地图"):
            pos = html.find(f">{junk}</a>")
            if pos == -1:
                pos = html.find(junk)
            assert 0 < pos < first_algo, (
                f"「{junk}」应当出现在结果块之前（实测在整页 33% 处）"
            )

        titles = [s["title"] for s in extract_snippets(html, limit=5)]
        assert not (set(titles) & {"图片", "视频", "地图", "资讯", "航班"}), (
            f"解析器把导航栏当成了结果：{titles}"
        )

    @pytest.mark.parametrize("entry", BING, ids=lambda e: e["file"])
    def test_h23_layer_returns_zero_before_the_result_area(self, entry):
        """为什么"挡住导航栏的其实是第 2 层"（而不是第 1 层）。

        实测：h2/h3 的命中**全部**落在结果区之内（第一个 b_algo 之后），
        而任意链接的命中里有 5 处落在结果区**之前** —— 正是那 5 个导航项。

        这条用例把"哪一层真的在挡"钉住，免得以后有人以为第 1 层是唯一防线
        而把它当成可以随意改动的性能优化。
        """
        html = (FIXTURES / entry["file"]).read_text(encoding="utf-8",
                                                    errors="replace")
        first_algo = html.find('class="b_algo"')

        h23 = list(_RESULT_TITLE_LINK_RE.finditer(html))
        anyl = list(_ANY_LINK_RE.finditer(html))
        assert h23, "真页面里应当有 h2/h3 结构（结果标题就在 h2 里）"

        before_h23 = [m for m in h23 if m.start() < first_algo]
        before_any = [m for m in anyl if m.start() < first_algo]
        assert before_h23 == [], (
            "h2/h3 不该在结果区之前命中 —— 若这条挂了，说明页面形态变了，"
            "文档里的位置数字也要跟着更新"
        )
        assert len(before_any) >= 3, (
            "任意链接应当能在结果区之前命中导航项 —— 这条 fixture 的价值就在这里"
        )

    @pytest.mark.parametrize("entry", BING, ids=lambda e: e["file"])
    def test_first_layer_does_not_currently_change_the_output(self, entry):
        """**如实记录一条"与直觉相反"的实测结论**。

        我原本以为"删掉第 1 层（结果块）会让结果变成导航栏/页脚"，据此写了
        一条反方向断言 —— **它红了**：把 `class="b_algo"` 全抹掉之后，
        抽出来的 5 条 URL 一模一样。

        原因是真实的 10 条结果本身就都在 `<h2>` 里，第 2 层扫全页也能拿到
        同样 5 条（因为 h2/h3 在全页只有那 10 处命中，没有别的 h2/h3）。

        所以这条用例断言的是**事实**而不是我的预期：第 1 层当下的作用是
        「把范围收窄到结果区」，不是「筛掉内容」。源码 docstring 已按实测改写。
        **如果哪天这条变红了**（第 1 层真的改变了输出），那是好消息 ——
        说明页面形态里多出了页面级的 h2/h3，此时该更新文档里的说法。
        """
        html = (FIXTURES / entry["file"]).read_text(encoding="utf-8",
                                                    errors="replace")
        stripped = html.replace('class="b_algo"', 'class="_removed_"')
        assert extract_snippets(stripped, limit=5) == extract_snippets(html,
                                                                      limit=5)

    @pytest.mark.parametrize("entry", BING, ids=lambda e: e["file"])
    def test_urls_are_unwrapped_external_links(self, entry):
        """必应把真实地址塞在跳转参数 `u=a1<base64url>` 里；
        解析后用户看到的应当是真地址，不是一长串 bing.com/ck/a"""
        for url in entry["urls"]:
            assert url.startswith("https://")
            assert "bing.com/ck/a" not in url
            assert "u=a1" not in url

    @pytest.mark.parametrize("entry", BING, ids=lambda e: e["file"])
    def test_titles_are_plain_text(self, entry):
        for t in entry["titles"]:
            assert t and len(t) >= 2
            assert "<" not in t and ">" not in t

    @pytest.mark.parametrize("entry", BING, ids=lambda e: e["file"])
    def test_decoy_page_is_caught_by_the_relevance_gate(self, entry):
        """**这才是这份 fixture 最值钱的地方**。

        必应对脚本客户端返回的是**诱饵页**：标题写着「上海天气」，
        10 个结果块也齐，但内容与查询毫无关系（实测拿到的是日本司法书士
        协会、波兰足球比分）。解析层识别不了诱饵 —— 它"成功"解析出 5 条。

        能拦住它的是相关性闸门。所以这里断言：
          · 解析层确实抽到了 5 条（如实记录"解析成功"）
          · 闸门判定为**不相关**（这才是产品最终不把它念给用户听的原因）
        如果谁把闸门去了，或者把这些"看起来成功"的结果直接返回，
        这条用例会红。
        """
        html = (FIXTURES / entry["file"]).read_text(encoding="utf-8",
                                                    errors="replace")
        snips = extract_snippets(html, limit=5)
        assert len(snips) == 5
        assert entry["relevant_to_query"] is False
        assert results_look_relevant(QUERY, snips) is False

    @pytest.mark.parametrize("entry", BING, ids=lambda e: e["file"])
    def test_gate_accepts_a_genuinely_relevant_title(self, entry):
        """反方向：同一道闸门对**真的相关**的标题必须放行。

        否则"闸门拦住了诱饵"可能只是因为它什么都拦 —— 那就不是判据，
        是常数。这里拿真页面的结构造一条标题含查询词的结果，闸门应当放行。
        """
        fake = [{"title": f"{QUERY} - 中国天气网", "url": "https://example.com/"}]
        assert results_look_relevant(QUERY, fake) is True


class TestRealBaiduPage:
    """百度：**不可为（反爬）**—— 页面上根本没有结果结构"""

    @pytest.mark.parametrize("entry", BAIDU, ids=lambda e: e["file"])
    def test_is_the_captcha_page(self, entry):
        html = (FIXTURES / entry["file"]).read_text(encoding="utf-8",
                                                    errors="replace")
        assert extract_title(html) == "百度安全验证"
        assert "网络不给力" in html_to_text(html)

    @pytest.mark.parametrize("entry", BAIDU, ids=lambda e: e["file"])
    def test_parses_nothing(self, entry):
        """如实结论：**0 条**。不假装能解析，也不因为"反爬"就去特判绕它"""
        html = (FIXTURES / entry["file"]).read_text(encoding="utf-8",
                                                    errors="replace")
        assert extract_snippets(html, limit=5) == []
        assert entry["parsed"] == 0
        assert entry["result_blocks"] == 0

    def test_three_runs_are_byte_identical(self):
        """三次抓取逐字节相同（MD5 同一个）—— 说明这不是"偶发拦截"，
        是**确定性**的：重试不解决问题。这条决定了 P5-C4 的终态是
        「不可为」而不是「重试即可」"""
        md5s = {e["md5"] for e in BAIDU}
        assert len(md5s) == 1, f"三次抓取居然不一样：{md5s}"


class TestRealGooglePage:
    """谷歌：**不可为（反爬-需执行 JS）**—— 返回的是脚本中继页"""

    @pytest.mark.parametrize("entry", GOOGLE, ids=lambda e: e["file"])
    def test_has_no_result_structure(self, entry):
        """92KB 里结果容器计数全 0，可见正文只有几十个字符 —— 
        "文件很大"和"有内容"是两回事，这条用例就是把它们分开"""
        html = (FIXTURES / entry["file"]).read_text(encoding="utf-8",
                                                    errors="replace")
        body = html_to_text(html)
        assert entry["result_blocks"] == 0
        assert len(body) == entry["visible_text_chars"]
        assert len(body) < 200, "可见正文应当只有几十字符（脚本中继页）"
        assert len(html) > 90000, "但它确实有 90KB —— 大的是脚本，不是内容"

    @pytest.mark.parametrize("entry", GOOGLE, ids=lambda e: e["file"])
    def test_requests_javascript(self, entry):
        html = (FIXTURES / entry["file"]).read_text(encoding="utf-8",
                                                    errors="replace")
        assert "enablejs" in html, "应当能找到要求启用 JS 的中继目标"

    @pytest.mark.parametrize("entry", GOOGLE, ids=lambda e: e["file"])
    def test_only_fallback_link_and_it_is_filtered(self, entry):
        """兜底层捞到的唯一一条是页脚的「反馈」链接 —— 
        它在解析层"算一条结果"，必须被相关性闸门拦掉。
        不做这道闸门的话，产品会对用户念出「搜到 1 条：反馈」。"""
        html = (FIXTURES / entry["file"]).read_text(encoding="utf-8",
                                                    errors="replace")
        snips = extract_snippets(html, limit=5)
        assert len(snips) == 1 == entry["parsed"]
        assert entry["parsed_layer"] == "兜底（任意链接）"
        assert snips[0]["title"] == "反馈"
        assert results_look_relevant(QUERY, snips) is False
        assert entry["relevant_to_query"] is False


# ══════════════════════════════════════════════════
#  三、fixture 与 P4 正式记录互相对账
# ══════════════════════════════════════════════════

class TestCrossCheckWithP4Evidence:
    """**两份独立留档必须对得上**。

    `manifest.json` 是我这一轮生成的；`_tmp_c4_runs.json` 是 P4 评估当时
    生成的。如果 fixture 的数字只能和「我自己刚写的清单」对上，那只是一份
    自证；能同时和**上一轮的留档**对上，才说明 fixture 真的是那批字节。
    """

    def test_p4_runs_json_exists(self):
        assert P4_RUNS.is_file()

    def test_every_recorded_run_has_a_fixture(self):
        recs = json.loads(P4_RUNS.read_text(encoding="utf-8"))
        have = {e["file"] for e in ENTRIES}
        missing = [
            f"{r['engine']}_run{r['run']}.html"
            for r in recs
            if f"{r['engine']}_run{r['run']}.html" not in have
        ]
        assert missing == [], f"P4 记录里有抓取但 fixture 里没有：{missing}"

    def test_bytes_and_parsed_counts_agree(self):
        recs = json.loads(P4_RUNS.read_text(encoding="utf-8"))
        by_file = {e["file"]: e for e in ENTRIES}
        for r in recs:
            e = by_file[f"{r['engine']}_run{r['run']}.html"]
            assert e["bytes"] == r["content_bytes"], r["engine"]
            assert e["parsed"] == r["parsed_by_project_parser"], r["engine"]
            # 口径照抄 P4：解析出 0 条时那一边记 True（没有结果可判）
            expected_rel = r["relevant"] if e["parsed"] else True
            assert e["relevant_to_query"] == expected_rel, r["engine"]

    def test_bing_is_not_in_p4_runs_but_says_so(self):
        """必应的两份**不在** P4 的 runs.json 里（是补测留的）。

        不许假装它有一份正式记录 —— 清单里必须如实标 false，
        并且它的来源要能靠**页面自身**核出来（标题里带查询词）。
        """
        for e in BING:
            assert e["recorded_in_runs_json"] is False
            assert e["page_declared_query"] == QUERY


# ══════════════════════════════════════════════════
#  四、文档说法不许和实测打架
# ══════════════════════════════════════════════════

class TestDocstringMatchesMeasurement:
    """**把"改正过的说法"也钉住**。

    起因：`extract_snippets` 的 docstring 原先写「每一层都是为了绕开实测出来的
    坑」，其中"只用 h2/h3 也还不够 —— 会命中页脚论坛推荐位"这一句，
    在我手上这两份 P4 留档里**复现不出来**（h2/h3 的命中全部落在结果区之内）。

    已按实测改写。这几条用例保证**改回去会红** —— 否则"修文档"就只是一次性动作，
    下次有人重写注释时又会写回那个更强的说法。
    """

    def _doc(self) -> str:
        return bt.extract_snippets.__doc__ or ""

    def test_docstring_exists(self):
        assert len(self._doc()) > 200, "docstring 被删了？它承载着判据说明"

    def test_does_not_claim_every_layer_was_measured_as_necessary(self):
        assert "每一层都是为了绕开实测出来的坑" not in self._doc(), (
            "这句话把「结构上合理」说成了「实测必要」—— 已按 P5-B5 实测改写，"
            "不要写回去"
        )

    def test_says_the_h23_layer_is_what_blocks_the_nav_bar(self):
        """实测结论必须写在 docstring 里，否则下一个人还会以为第 1 层是防线"""
        doc = self._doc()
        assert "导航" in doc
        assert "h2" in doc.lower() or "h3" in doc.lower()

    def test_admits_the_first_layer_does_not_change_output_today(self):
        """**最重要的一条**：docstring 必须承认第 1 层当下没有改变输出。

        不承认的话，读者会以为删掉它就会退化成导航栏 —— 那是错的，
        而且会让人不敢动这段代码（或反过来，以为它是"关键的过滤"而加更多
        依赖它的逻辑）。
        """
        doc = self._doc()
        assert "逐条相同" in doc or "没有改变" in doc or "不改变" in doc
        assert "P5-B5" in doc, "要写明这条结论是从哪来的（可追溯）"
