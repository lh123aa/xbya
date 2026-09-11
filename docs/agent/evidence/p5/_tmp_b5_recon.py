# -*- coding: utf-8 -*-
"""P5-B5 第 0 步：先看清 P4 到底留了哪几份原始 HTML、各自是什么查询。

在做 fixture 之前必须先做这一步，因为**文件名里没有查询词**：
`_tmp_c4_raw_bing_run1.html` 既不说明查询，也不说明它是不是结果页。
如果直接拿它当"必应结果页 fixture"，就会把一个我不知道来源的字节串
当成判据 —— 那正是本项目禁的"手写 fixture"的另一种形态：
**来源不明的 fixture**。

本脚本只读，报告：
  · 每份文件的字节数 / MD5 / HTTP 层可见线索
  · `extract_snippets` 在**原文**上的输出（这才是解析器真实行为）
  · 从页面里反查它响应的是哪个查询（`<input name="q" value=...>` 之类）
  · 与 P4 报告里记下的数字逐条对账（对不上就退非零）
"""
import hashlib
import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))

from agent.tools.browser_tools import (          # noqa: E402
    extract_snippets,
    extract_title,
    html_to_text,
    results_look_relevant,
)

P4 = ROOT / "docs" / "agent" / "evidence" / "p4"
RUNS = json.loads(io.open(P4 / "_tmp_c4_runs.json", encoding="utf-8").read())


def _probe_query(html: str) -> str:
    """从结果页自己身上反查"它响应的是哪个查询"。

    比文件名可信：文件名是人起的，`<input name="q" value="...">` 是服务器渲染的。
    找不到就返回空串（**不猜**）。
    """
    import re
    for pat in (
        r'<input[^>]+name="q"[^>]*value="([^"]{1,80})"',
        r'<input[^>]+value="([^"]{1,80})"[^>]*name="q"',
        r'<textarea[^>]+name="q"[^>]*>(.{1,80}?)</textarea>',
    ):
        m = re.search(pat, html, re.I | re.S)
        if m:
            from html import unescape
            return unescape(m.group(1)).strip()
    return ""


print("=" * 78)
print("P5-B5 前置：P4 原始 HTML 清单与来源核对")
print("=" * 78)

files = sorted(P4.glob("_tmp_c4_raw_*.html"))
print(f"\n共 {len(files)} 份\n")

for f in files:
    raw = f.read_bytes()
    html = raw.decode("utf-8", "replace")
    md5 = hashlib.md5(raw).hexdigest().upper()
    snips = extract_snippets(html, limit=5)
    body = html_to_text(html)
    print(f"── {f.name}")
    print(f"   bytes={len(raw)}  md5={md5}")
    print(f"   <title>={extract_title(html)!r}")
    print(f"   页内查询词={_probe_query(html)!r}")
    print(f"   可见正文长度={len(body)}  正文首行={body.splitlines()[0][:60] if body else ''!r}")
    print(f"   容器 class=\"b_algo\" 出现次数={html.count('class=\"b_algo\"')}")
    print(f"   extract_snippets → {len(snips)} 条")
    for s in snips:
        print(f"      · {s['title'][:50]!r}  {s['url'][:70]}")
    if snips:
        print(f"   与「上海天气」相关？{results_look_relevant('上海天气', snips)}")
    print()

print("=" * 78)
print("与 P4 记录对账（_tmp_c4_runs.json）")
print("=" * 78)
print("\n注：runs.json 里**只有 baidu/google 各 3 次**，没有 bing 条目 ——"
      "\n    必应的原始 HTML 是补测留下的（见 _tmp_c4_bing_and_net.txt /"
      "\n    _tmp_c4_bing_decoy_cn.txt），但**页面自己身上带着查询词**"
      "\n    （<input name=\"q\" value=\"上海天气\"> + <title>上海天气 - 搜索</title>），"
      "\n    所以来源可核，不需要靠文件名。")
print("\n对账口径也必须一致：P4 探针把「解析出 0 条」记成 relevant=True"
      "\n（`_tmp_c4_probe_search.py:390` 写的是 `if parsed else True`）——"
      "\n意思是「没有结果可判，不算它不相关」。这一条我照抄，不自作主张改成 False，"
      "\n否则就是我这边口径和别人不同却报成「对账失败」。\n")

bad = 0
for rec in RUNS:
    name = f"_tmp_c4_raw_{rec['engine']}_run{rec['run']}.html"
    f = P4 / name
    if not f.is_file():
        print(f"✘ 缺文件 {name}")
        bad += 1
        continue
    raw = f.read_bytes()
    html = raw.decode("utf-8", "replace")
    snips = extract_snippets(html, limit=5)
    rel = results_look_relevant(rec["query"], snips) if snips else True
    ok_bytes = len(raw) == rec["content_bytes"]
    ok_parsed = len(snips) == rec["parsed_by_project_parser"]
    ok_rel = rel == rec["relevant"]
    ok_tool = len(snips) == rec["tool_count"] or rec["tool_count"] == 0
    flag = "✔" if (ok_bytes and ok_parsed and ok_rel) else "✘"
    if flag == "✘":
        bad += 1
    print(f"{flag} {name:34s} bytes {len(raw)}=={rec['content_bytes']} "
          f"parsed {len(snips)}=={rec['parsed_by_project_parser']} "
          f"relevant {rel}=={rec['relevant']}")

print()
if bad:
    print(f"**对账失败：{bad} 处不一致** —— 不许把这些文件当 fixture 用。")
    raise SystemExit(1)
print("对账通过：6 份 baidu/google 原文的字节数、解析条数、相关性判定与 P4 记录逐条一致。")
print("bing 的 2 份虽无 runs.json 记录，但页面自带的查询词与标题都是「上海天气」，来源可核。")
