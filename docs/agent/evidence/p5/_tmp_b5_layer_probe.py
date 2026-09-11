# -*- coding: utf-8 -*-
"""P5-B5 探针：`extract_snippets` 的**第 1 层（结果块）到底改变了什么**。

起因：我给 fixture 写的反方向用例断言"把 class="b_algo" 抹掉后结果会不一样"，
**它红了** —— 抹掉之后 5 条 URL 一模一样。也就是说我原来那句
"删掉第一层内容会变成导航栏/页脚"是**错的**（至少对这份页面是错的）。

错的断言不能改宽了事，得先弄清楚真实行为。本探针直接**在真实页面上**
对比三个作用域 × 两个模式：

  scope=结果块   × pattern=h2/h3   ← 现状第 1 层
  scope=全页     × pattern=h2/h3   ← 第 2 层
  scope=全页     × pattern=任意链接 ← 第 3 层

结论写清楚之后再回去改用例 —— 判据要贴着事实写，不是贴着我的预期写。
"""
import io
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))

import agent.tools.browser_tools as bt          # noqa: E402
from agent.tools.browser_tools import extract_snippets   # noqa: E402

FX = ROOT / "tests" / "fixtures" / "serp"

for name in ("bing_run1.html", "bing_run2.html", "google_run1.html",
             "baidu_run1.html"):
    html = (FX / name).read_text(encoding="utf-8", errors="replace")
    blocks = bt._ENGINE_RESULT_BLOCK_RE.findall(html)

    print("=" * 74)
    print(f"{name}   bytes={len(html)}  结果块={len(blocks)}")
    print("=" * 74)

    scopes = {
        "① 结果块": ("\n".join(blocks) if blocks else ""),
        "② 全页": html,
    }
    for sname, scope in scopes.items():
        if not scope:
            print(f"  {sname}: 空（没有结果块可拼）")
            continue
        for pname, pat in (("h2/h3", bt._RESULT_TITLE_LINK_RE),
                           ("任意链接", bt._ANY_LINK_RE)):
            got = []
            seen = set()
            if pat is bt._ANY_LINK_RE:
                # 第 3 层只在第 2 层完全没结果时才会被用到，这里单独跑它
                pass
            for m in pat.finditer(scope):
                url = bt.unwrap_engine_url(bt.unescape(m.group(1)))
                title = bt.html_to_text(m.group(2))
                if not title or len(title) < 2 or url in seen:
                    continue
                seen.add(url)
                got.append((title[:38], url[:52]))
                if len(got) >= 5:
                    break
            print(f"  {sname} × {pname}: {len(got)} 条")
            for t, u in got:
                print(f"        · {t!r}  {u}")

    print(f"  → 现状 extract_snippets(): "
          f"{[s['url'][:46] for s in extract_snippets(html, limit=5)]}")

    # 把"结果块"这一步整个拿掉（模拟"第一层不存在"）后，第 2 层会给出什么
    scopes_nowin = [html]
    res_nowin = []
    seen = set()
    for scope in scopes_nowin:
        for pat in (bt._RESULT_TITLE_LINK_RE, bt._ANY_LINK_RE):
            for m in pat.finditer(scope):
                url = bt.unwrap_engine_url(bt.unescape(m.group(1)))
                title = bt.html_to_text(m.group(2))
                if not title or len(title) < 2 or url in seen:
                    continue
                seen.add(url)
                res_nowin.append((title[:38], url[:52]))
                if len(res_nowin) >= 5:
                    break
            if res_nowin:
                break
        if res_nowin:
            break
    print(f"  → 若**删掉第 1 层**（只剩全页 h2/h3 → 任意链接）：{len(res_nowin)} 条")
    for t, u in res_nowin:
        print(f"        · {t!r}  {u}")
    print(f"     与现状相同？{[u for _, u in res_nowin] == [s['url'][:52] for s in extract_snippets(html, limit=5)]}")
    print()
