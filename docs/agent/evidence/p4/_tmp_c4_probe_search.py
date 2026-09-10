"""C4 临时探针：百度 / 谷歌搜索路径的真实 HTTP 证据

**不修改任何源码**，只从外部驱动真实代码路径：

    真实路径 = agent.tools.browser_tools.WebSearchTool.execute({"query":..., "engine":...})
               （browser_tools.py:397 → requests.get:413 → extract_snippets:416
                 → results_look_relevant:419 → ToolResult:442）

为了让"状态码/长度/耗时"这些数字来自**真实发生的那次请求**（而不是我在外面
另发一份 requests.get 的副本），本脚本用包装器替换 `requests.get`：
调用仍然由产品代码发起、仍然用产品的 HEADERS/URL/超时，我只是把**真实返回的
那个 response 对象**抄一份留档。

`open_browser=False` 是产品自己的参数（browser_tools.py:407-408），
走的是同一条 HTTP 与解析路径，只是不去抢用户的浏览器窗口。

用法：python tools/_tmp_c4_probe_search.py          # 打全部证据
      python tools/_tmp_c4_probe_search.py --selfcheck  # 只跑判据自检
"""

import json
import os
import re
import sys
import time
import traceback
from html import unescape
from typing import Any, Dict, List, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import requests  # noqa: E402

from agent.tools import browser_tools as bt  # noqa: E402

OUT_DIR = os.path.join(ROOT, "docs", "agent", "evidence", "p4")

#: 真实原始响应留档（每次运行覆盖）
_CAPTURED: List[Dict[str, Any]] = []
_REAL_GET = requests.get


def _encoding_snapshot(resp) -> Dict[str, Any]:
    """记录产品代码改写编码前的全部可观测事实"""
    raw = resp.content or b""
    declared = resp.encoding                     # requests 从 header 猜出来的
    apparent = None
    try:
        apparent = resp.apparent_encoding        # 产品代码真正用的那个
    except Exception as e:                       # pragma: no cover
        apparent = f"<apparent_encoding 抛异常 {type(e).__name__}: {e}>"
    return {
        "status": resp.status_code,
        "reason": str(resp.reason or ""),
        "final_url": resp.url,
        "declared_encoding": declared,
        "apparent_encoding": apparent,
        "content_bytes": len(raw),
        "content_type": str(resp.headers.get("Content-Type", "")),
        "raw_head_hex": raw[:16].hex(),
        "content": raw,                          # 原始字节，供二次核对
    }


def _capturing_get(url, **kwargs):
    """包装真实 requests.get：请求由产品代码发起，我只留档返回对象"""
    resp = _REAL_GET(url, **kwargs)
    snap = _encoding_snapshot(resp)
    snap["request_url"] = url
    snap["request_headers"] = dict(kwargs.get("headers") or {})
    snap["request_timeout"] = kwargs.get("timeout")
    _CAPTURED.append(snap)
    return resp


# ══════════════════════════════════════════════════════
#  判据（verdict）—— 必须双向验证过
# ══════════════════════════════════════════════════════

#: 反爬/拦截页特征（服务端行为）
BLOCK_MARKERS = (
    "百度安全验证", "网络不给力", "请稍后再试", "安全验证", "wappass.baidu.com",
    "unusual traffic", "我们的系统检测到", "/sorry/", "not a robot",
    "人机验证", "系统检测到您的网络存在异常", "verify you are human",
    "Before you continue", "consent.google.com",
)

#: 结果容器特征（HTML 里"本该能解析出结果"的结构）
RESULT_CONTAINERS = {
    "baidu": ('class="result"', "class='result'", 'id="content_left"',
              "mu=\"http", "c-container"),
    "google": ('id="search"', 'id="rso"', "<h3", 'class="g"', "data-s"),
    "bing": ('class="b_algo"',),
}

#: 可见正文少于此值 + 无结果容器 → 判定为"必须执行 JS 才能出结果的空壳页"
JS_GATE_MIN_TEXT = 150

_SCRIPT_STYLE_RE = None


def visible_text(html: str) -> str:
    """去掉 script/style/noscript 与标签后的可见文字（判"空壳页"用）"""
    text = bt._DROP_TAG_RE.sub(" ", html or "")
    text = re.sub(r"(?is)<!--.*?-->", " ", text)
    text = bt._ANY_TAG_RE.sub(" ", text)
    text = unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def detect_flags(engine: str, status: Optional[int], html: str,
                 parsed_count: int, http_error: str = "",
                 relevant: bool = True) -> Dict[str, Any]:
    """把一次运行归到互斥的几档里（网络失败 / 拦截页 / 解析器 bug / 正常）

    ⚠ 判据设计的关键教训（第一版自检就抓出来了）：
    **"解析器拿到了 N 条"不能证明"有结果"**。百度的拦截页里就有
    `<a href="https://wappass.baidu.com/...">百度安全验证</a>`，解析器会把它
    当"1 条结果"。所以"有结果"必须是
        parsed_count > 0  AND  该结果通过了产品自己的相关性判据(results_look_relevant)
    拦截页反过来也是靠这一点识别的：页面上的链接不是结果，所以相关性为假。

    这个函数是本报告的判据核心，所以它必须被"反方向输入"验证过：见 selfcheck()。
    """
    html = html or ""
    low = html.lower()
    hits = [m for m in BLOCK_MARKERS if m.lower() in low]
    container_hits = [m for m in RESULT_CONTAINERS.get(engine, ()) if m.lower() in low]
    has_results = parsed_count > 0 and relevant
    vis = visible_text(html)
    js_gate = bool(not container_hits and len(vis) < JS_GATE_MIN_TEXT
                   and ('id="enablejs"' in low or 'src="/httpservice' in low
                        or "enablejs" in low))

    flags = {
        "markers_found": hits,
        "container_markers_found": container_hits,
        "has_result_container": bool(container_hits),
        "has_results": has_results,
        "visible_text_chars": len(vis),
        "visible_text_head": vis[:200],
        "js_gate": js_gate,
        "parser_bug": False,
        "verdict": "",
    }

    if http_error or status is None:
        # 网络本身失败（DNS/连接/超时/SSL）—— 与"服务端拦截"是两回事，绝不合并
        flags["verdict"] = "网络不通"
    elif status != 200:
        flags["verdict"] = f"HTTP {status}（非 200，抓取被拒）"
    elif has_results and not hits:
        flags["verdict"] = "可为"
    elif has_results and hits:
        flags["verdict"] = "可为（页面夹带拦截提示字样）"
    elif hits:
        # 有反爬标记，且没有任何与查询相关的结果
        flags["verdict"] = "不可为（反爬）"
        if container_hits:
            flags["note"] = ("页面同时含结果容器标记；是否属解析器 bug "
                             "需看 HTML 里是否真有结果标题")
    elif js_gate:
        # HTML 里没有结果结构，只有脚本骨架 + 极短可见文字 → 服务端不下发结果，
        # 要求客户端执行 JS（反爬/防脚本），**不是**解析器的问题
        flags["verdict"] = "不可为（反爬-需执行 JS 的空壳页）"
    elif not container_hits:
        flags["verdict"] = "无法判定（无任何结果容器标记、无拦截标记）"
    else:
        # 有结果容器标记、无拦截标记，却拿不到相关结果 → 最可能是解析器/相关性判据的问题
        flags["parser_bug"] = parsed_count == 0
        flags["verdict"] = ("可为但解析器有 bug" if parsed_count == 0
                            else "解析出结果但与查询不相关（诱饵页/相关性判据过严）")
    return flags


def find_marker_evidence(engine: str, html: str, limit: int = 4) -> List[str]:
    """逐条列出"HTML 里真的有结果"的行级证据（用 grep 式的原始行，不加工）"""
    html = html or ""
    out: List[str] = []
    for m in RESULT_CONTAINERS.get(engine, ()):
        start = 0
        while True:
            i = html.lower().find(m.lower(), start)
            if i < 0 or len(out) >= limit:
                break
            line_no = html.count("\n", 0, i) + 1
            line = html.splitlines()[line_no - 1] if html.splitlines() else ""
            out.append(f"命中 {m!r} → 第 {line_no} 行 / 偏移 {i}：{line.strip()[:180]}")
            start = i + 1
        if len(out) >= limit:
            break
    return out[:limit]


# ══════════════════════════════════════════════════════
#  判据自检（双向）
# ══════════════════════════════════════════════════════

GOOD_BAIDU = """<html><head><title>上海天气_百度搜索</title></head><body>
<div id="content_left">
  <div class="result c-container" id="1" mu="http://www.weather.com.cn/">
    <h3 class="t"><a href="http://www.weather.com.cn/">上海天气预报</a></h3>
  </div>
  <div class="result c-container" id="2" mu="https://www.dianhua.cn/">
    <h3 class="t"><a href="https://www.dianhua.cn/">上海天气 15 天</a></h3>
  </div>
</div></body></html>"""

BAD_BAIDU = """<html><head><title>百度安全验证</title></head><body>
<div class="wappass">网络不给力，请稍后重试
<a href="https://wappass.baidu.com/static/captcha/tuxing.html">百度安全验证</a>
</div></body></html>"""

GOOD_GOOGLE = """<html><head><title>上海天气 - Google 搜索</title></head><body>
<div id="search"><div id="rso">
  <div class="g"><a href="https://weather.com/"><h3>上海天气 - The Weather Channel</h3></a></div>
  <div class="g"><a href="https://www.accuweather.com/"><h3>上海天气 - AccuWeather</h3></a></div>
</div></div></body></html>"""

BAD_GOOGLE = """<html><head><title>https://www.google.com/search?q=...</title></head><body>
<div>我们的系统检测到您的计算机网络中存在异常流量。此网页用于确认这些请求是由您而不是自动程序发出的。
<a href="/sorry/index">unusual traffic</a></div></body></html>"""

#: 已知有结果容器、但结构不符合解析器（模拟"解析器 bug"那一档）
PARSER_BUG_BAIDU = """<html><head><title>上海天气_百度搜索</title></head><body>
<div id="content_left">
  <div class="result c-container" id="1" mu="http://www.weather.com.cn/">
    <div class="t"><span>上海天气预报</span></div>
  </div>
</div></body></html>"""

#: 真实观测到的谷歌响应形状（92KB 脚本骨架 + 48 字符可见正文，无结果结构）
JS_GATE_GOOGLE = """<!DOCTYPE html><html lang="zh-CN"><head><title>Google Search</title>
<style>body{background-color:var(--xhUGwc)}</style>
<script nonce="abc">window.google = window.google || {};</script></head>
<body><div id="yvlrue"><noscript><a href="/httpservice/retry/enablejs?sei=x">
Enable JavaScript</a></noscript>
<div>Google Search 如果您无法访问 Google 搜索，请 点击此处 ，或发送 反馈 。</div>
</div></body></html>"""

#: 反方向：一个**真**结果页，但页面很长、可见文字很多 —— 不许被误判成空壳页
LONG_GOOD_BAIDU = ("<html><head><title>上海天气_百度搜索</title></head><body>"
                   + "".join(f'<div class="result c-container" id="{i}" '
                             f'mu="http://www.weather.com.cn/{i}"><h3 class="t">'
                             f'<a href="http://www.weather.com.cn/{i}">上海天气预报 {i}</a>'
                             f"</h3></div>" for i in range(1, 4))
                   + "</body></html>")


def selfcheck() -> int:
    """判据的双向自检

    每一例的 (parsed_count, relevant) **由产品自己的函数现算**：
        parsed = len(bt.extract_snippets(html))
        relevant = bt.results_look_relevant(query, bt.extract_snippets(html))
    所以这里检验的是"判据在真实解析结果上会给什么结论"，而不是我手填的数字。
    期望值写死，用来钉住判据不会一律输出同一档。
    """
    fails = 0
    query = "上海天气"
    dummy = "不相关查询词xyzzy"          # 用来把"好页"喂成"不相关"，模拟诱饵页
    cases = [
        ("baidu 已知好页（真页面结构）", "baidu", 200, GOOD_BAIDU, query, "", "可为"),
        ("baidu 已知拦截页（含一个可解析链接）", "baidu", 200, BAD_BAIDU, query, "",
         "不可为（反爬）"),
        ("google 已知好页（真页面结构）", "google", 200, GOOD_GOOGLE, query, "", "可为"),
        ("google 已知拦截页", "google", 200, BAD_GOOGLE, query, "",
         "不可为（反爬）"),
        ("baidu 容器在、容器内无链接（模拟解析器读不出）", "baidu", 200,
         PARSER_BUG_BAIDU, query, "", "可为但解析器有 bug"),
        ("好页 + 不相关查询（模拟引擎诱饵页）", "baidu", 200, GOOD_BAIDU, dummy, "",
         "解析出结果但与查询不相关（诱饵页/相关性判据过严）"),
        ("网络失败（DNS 抛错）", "baidu", None, "", query,
         "ConnectionError: DNS 解析失败", "网络不通"),
        ("HTTP 403 且无标记", "google", 403, "<html><body>Forbidden</body></html>",
         query, "", "HTTP 403（非 200，抓取被拒）"),
        ("google 空壳页（实测形状：脚本骨架 + 48 字符可见文字）", "google", 200,
         JS_GATE_GOOGLE, query, "", "不可为（反爬-需执行 JS 的空壳页）"),
        ("反方向：真结果页不会被误判成空壳页", "baidu", 200, LONG_GOOD_BAIDU,
         query, "", "可为"),
        ("反方向：拦截提示 + 真结果（不许因为有标记就判死）", "baidu", 200,
         GOOD_BAIDU.replace("<body>", "<body>网络不给力"), query, "",
         "可为（页面夹带拦截提示字样）"),
        ("已知局限：查询词=拦截页标题时相关性判据会被骗", "baidu", 200, BAD_BAIDU,
         "百度安全验证", "", "可为（页面夹带拦截提示字样）"),
    ]
    print("=" * 78)
    print("[自检] 判据双向验证 —— 好页 / 坏页 / 解析器 bug / 网络失败 必须分类不同")
    print("=" * 78)
    for label, engine, status, html, q, err, expect in cases:
        out = bt.extract_snippets(html)
        parsed = len(out)
        relevant = bt.results_look_relevant(q, out) if out else True
        flags = detect_flags(engine, status, html, parsed, err, relevant)
        got = flags["verdict"]
        ok = got == expect
        fails += 0 if ok else 1
        print(f"  [{'OK ' if ok else 'FAIL'}] {label}")
        print(f"         parsed={parsed} relevant={relevant} markers={flags['markers_found']} "
              f"containers={flags['container_markers_found']}")
        print(f"         判据给出 {got!r}")
        if not ok:
            print(f"         ★ 期望 {expect!r} —— 判据需要修")
    print(f"\n  自检结论：{len(cases) - fails}/{len(cases)} 一致"
          f"（{'判据不是空转' if fails == 0 else '判据有错，必须修'}）")

    # 额外：证明"解析器在好页上真的能出结果"，否则"解析器 bug"这一档无法成立
    print("\n  [附加] 解析器在合成好页上的实际产出（证明解析器本身是活的）：")
    print("    baidu 好页 →", json.dumps(bt.extract_snippets(GOOD_BAIDU),
                                       ensure_ascii=False))
    print("    google 好页 →", json.dumps(bt.extract_snippets(GOOD_GOOGLE),
                                        ensure_ascii=False))
    print("    相关性 baidu 好页/好查询 →",
          bt.results_look_relevant("上海天气", bt.extract_snippets(GOOD_BAIDU)))
    print("    相关性 baidu 好页/坏查询 →",
          bt.results_look_relevant(dummy, bt.extract_snippets(GOOD_BAIDU)))
    print("    拦截页 parsed 与 products 层结果对比：",
          f"parsed={len(bt.extract_snippets(BAD_BAIDU))}",
          f"relevant={bt.results_look_relevant('上海天气', bt.extract_snippets(BAD_BAIDU))}")
    return fails


# ══════════════════════════════════════════════════════
#  主流程：跑产品真实代码路径
# ══════════════════════════════════════════════════════

def run_engine(engine: str, query: str, times: int = 3,
               gap_s: float = 3.0) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for i in range(1, times + 1):
        # ⚠ 产品在 execute() 内部 `import requests`（browser_tools.py:412），
        # 所以拿不到 bt.requests；必须在 requests 模块上打补丁才能截到
        # **产品自己发起的那一次请求**（不是我在外面另发的副本）。
        # 请求由产品代码发起：URL/HEADERS/超时全部用产品里的常量。
        requests.get = _capturing_get
        _CAPTURED.clear()
        tool = bt.WebSearchTool(engine=engine, default_open=False)
        err = ""
        res = None
        t0 = time.perf_counter()
        try:
            # ★ 这就是产品路径本身
            res = tool.execute({"query": query, "open_browser": False})
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
            traceback.print_exc()
        elapsed = (time.perf_counter() - t0) * 1000
        requests.get = _REAL_GET

        snap = _CAPTURED[-1] if _CAPTURED else None
        row: Dict[str, Any] = {
            "engine": engine, "run": i, "query": query,
            "http_error": err, "elapsed_ms": round(elapsed, 1),
            "tool_success": bool(res.success) if res else False,
            "tool_summary": (res.summary if res else ""),
            "tool_count": int(res.count) if res else 0,
            "tool_results": (res.data.get("results") if res and isinstance(res.data, dict)
                             else []),
            "url": (res.data.get("url") if res and isinstance(res.data, dict) else ""),
        }
        if snap:
            row.update({
                "status": snap["status"], "reason": snap["reason"],
                "final_url": snap["final_url"],
                "content_bytes": snap["content_bytes"],
                "declared_encoding": snap["declared_encoding"],
                "apparent_encoding": snap["apparent_encoding"],
                "content_type": snap["content_type"],
                "raw_head_hex": snap["raw_head_hex"],
                "request_headers": snap["request_headers"],
                "request_timeout": snap["request_timeout"],
            })
            encoding = snap["apparent_encoding"] or "utf-8"
            try:
                html = snap["content"].decode(encoding, errors="replace")
            except (LookupError, UnicodeDecodeError):
                html = snap["content"].decode("utf-8", errors="replace")
            row["chars"] = len(html)
            markers = [m for m in BLOCK_MARKERS if m.lower() in html.lower()]
            row["markers"] = markers
            row["containers"] = [m for m in RESULT_CONTAINERS.get(engine, ())
                                 if m.lower() in html.lower()]
            parsed = bt.extract_snippets(html, limit=5)
            row["parsed_by_project_parser"] = len(parsed)
            row["parser_output"] = parsed
            # 产品自己还有一道相关性闸门（browser_tools.py:419），拦截页上的
            # "百度安全验证"链接会被它挡掉 —— 所以"解析出 N 条"必须再过这一道
            row["relevant"] = (bt.results_look_relevant(query, parsed)
                               if parsed else True)
            row["marker_evidence"] = find_marker_evidence(engine, html)
            row["head200"] = html[:200].replace("\n", "\\n")
            row["title"] = bt.extract_title(html)
            row["raw_dump"] = os.path.join(
                OUT_DIR, f"_tmp_c4_raw_{engine}_run{i}.html")
            os.makedirs(OUT_DIR, exist_ok=True)
            with open(row["raw_dump"], "wb") as f:
                f.write(snap["content"])
        else:
            row.update({"status": None, "chars": 0, "content_bytes": 0,
                        "markers": [], "containers": [], "relevant": None,
                        "parsed_by_project_parser": 0, "head200": "",
                        "marker_evidence": [], "title": "",
                        "http_error": err or "requests.get 未被调用（异常在更早处抛出）"})
        row["flags"] = detect_flags(
            engine, row.get("status"), html if snap else "",
            row["parsed_by_project_parser"], row["http_error"],
            relevant=bool(row.get("relevant", True)))
        rows.append(row)
        print(f"  [{engine} #{i}] status={row.get('status')} "
              f"bytes={row.get('content_bytes')} chars={row.get('chars')} "
              f"markers={row['markers']} containers={row['containers']} "
              f"parsed={row['parsed_by_project_parser']} "
              f"relevant={row.get('relevant')} "
              f"tool_count={row['tool_count']} {row['elapsed_ms']}ms "
              f"→ {row['flags']['verdict']}")
        print(f"            head200={row['head200'][:200]!r}")
        if i < times:
            time.sleep(gap_s)
    return rows


def main() -> int:
    argv = sys.argv[1:]
    os.makedirs(OUT_DIR, exist_ok=True)
    fails = selfcheck()
    if "--selfcheck" in argv:
        return 1 if fails else 0

    query = "上海天气"
    print("\n" + "=" * 78)
    print(f"[真实路径] WebSearchTool.execute(engine=<E>, query={query!r}, open_browser=False)")
    print(f"引擎模板：{json.dumps(bt.SEARCH_ENGINES, ensure_ascii=False)}")
    print("=" * 78)

    all_rows: List[Dict[str, Any]] = []
    for engine in ("baidu", "google"):
        print(f"\n---- {engine} ----")
        all_rows += run_engine(engine, query, times=3)

    out = os.path.join(OUT_DIR, "_tmp_c4_runs.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(all_rows, f, ensure_ascii=False, indent=1)
    print(f"\n[留档] {out}")
    print(f"[留档] 原始 HTML 见 {OUT_DIR}\\_tmp_c4_raw_<engine>_run<N>.html")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
