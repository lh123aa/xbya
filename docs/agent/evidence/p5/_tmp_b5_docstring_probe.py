# -*- coding: utf-8 -*-
"""P5-B5 探针 2：源码 docstring 里那句"只用 h2/h3 会命中页脚推荐位"能不能复核？

`extract_snippets` 的 docstring 写着（browser_tools.py:296-302）：

  · 一上来就通用扫描 → 只会得到"图片、视频"（这条**能复核**，见探针 1）
  · **只用 h2/h3 也还不够** —— 中文查询「上海天气」命中的是页脚论坛推荐位
    （一堆蓝屏/OneDrive 帖子）

第二句只在 P4 当时那次抓取里出现过，我手上这两份留档能不能复现，必须实测。
**不假设它成立，也不假设它不成立** —— 直接看数据。

判据：h2/h3 的第 1 条命中位置 vs 第 1 个 `class="b_algo"` 的位置。
若 h2/h3 命中在 b_algo 之前，说明它先扫到了页头/页脚；若在之后，说明
它命中的就是结果区本身。
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))

import agent.tools.browser_tools as bt          # noqa: E402

FX = ROOT / "tests" / "fixtures" / "serp"
B_ALGO = 'class="b_algo"'

for n in ("bing_run1.html", "bing_run2.html"):
    html = (FX / n).read_text(encoding="utf-8", errors="replace")
    print("=" * 74)
    print(f"{n}  chars={len(html)}")
    print("=" * 74)

    first_algo = html.find(B_ALGO)
    print(f"  第一个 {B_ALGO} 出现在 char {first_algo}"
          f"（{first_algo / len(html):.1%} 处）")

    for pname, pat in (("h2/h3", bt._RESULT_TITLE_LINK_RE),
                       ("任意链接", bt._ANY_LINK_RE)):
        ms = list(pat.finditer(html))
        print(f"  {pname}: 全页共 {len(ms)} 处匹配")
        for m in ms[:3]:
            title = bt.html_to_text(m.group(2))[:40]
            print(f"      char {m.start():7d}（{m.start() / len(html):5.1%}）"
                  f"  {title!r}")
        if ms:
            before = [m for m in ms if m.start() < first_algo]
            print(f"      → 在第一个结果块**之前**的有 {len(before)} 处"
                  f"（这些就是「h2/h3 先扫到页面其他部分」的那部分）")
    print()

    # 直接看"前 N 条 h2/h3 命中"是不是页面导航/页脚
    print("  前 3 条 h2/h3 命中所在的上下文片段：")
    for m in list(bt._RESULT_TITLE_LINK_RE.finditer(html))[:3]:
        lo = max(0, m.start() - 90)
        ctx = re.sub(r"\s+", " ", html[lo:m.start()])[-90:]
        print(f"      …{ctx!r}")
    print()
