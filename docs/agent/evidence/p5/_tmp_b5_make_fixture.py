# -*- coding: utf-8 -*-
"""P5-B5：把 P4 真实抓到的 SERP HTML 变成**离线 fixture + 清单**。

为什么必须从真实抓取复制，而不是手写一段"像必应"的 HTML：
本项目的解析器是**照着真实页面的坑长出来的** —— `extract_snippets` 的三层
提取顺序（结果块 → h2/h3 → 任意链接）就是为了绕开实测出来的两个坑
（第一层能避开导航栏；只用 h2/h3 会命中页脚推荐位）。手写 fixture 只能
证明"我按自己以为的结构写的东西能被解析"，证明不了**真页面上能不能**。

本脚本做的三件事：
  1. 把 8 份原文**逐字节复制**进 `tests/fixtures/serp/`（不重新编码、不改行尾）
  2. 生成 `manifest.json`：每份的字节数 / MD5 / 引擎 / 页面自称的查询词 /
     解析条数 / 相关性判定 / 判定层
  3. 复制后**立刻回读校验 MD5**，并断言与 P4 记录的数字一致

MD5 是这里的核心判据：它保证"fixture == 当时服务器真的发回来的那串字节"。
没有它，fixture 就只是一份来源不明的文件。
"""
import hashlib
import io
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))

from agent.tools.browser_tools import (      # noqa: E402
    extract_snippets,
    extract_title,
    html_to_text,
    results_look_relevant,
)

P4 = ROOT / "docs" / "agent" / "evidence" / "p4"
DEST = ROOT / "tests" / "fixtures" / "serp"
RUNS = json.loads(io.open(P4 / "_tmp_c4_runs.json", encoding="utf-8").read())

QUERY = "上海天气"

#: 引擎 → 这份 fixture 在**本机**的固定结局。写死在清单里而不是测试里，
#: 是为了让"当时的结论"和"现在的复算"能被对比 —— 若解析器行为变了，
#: 测试会因为清单对不上而变红，而不是悄悄跟着一起变。
VERDICT = {
    "bing": "可用 —— 真实结果块（<li class=\"b_algo\">）解析出标题+链接",
    "baidu": "不可为（反爬）—— 1488 字节「百度安全验证」页，无结果结构",
    "google": "不可为（反爬-需执行 JS）—— 92KB 脚本中继页，可见正文 53 字符",
}


def md5(raw: bytes) -> str:
    return hashlib.md5(raw).hexdigest().upper()


DEST.mkdir(parents=True, exist_ok=True)

# 目标文件名：去掉 `_tmp_c4_raw_` 前缀（它是 P4 探针的临时命名，不该进 fixture 名）
sources = sorted(P4.glob("_tmp_c4_raw_*.html"))
print(f"发现 {len(sources)} 份 P4 原始 HTML，复制到 {DEST.relative_to(ROOT)}/\n")

entries = []
problems = []

for src in sources:
    raw = src.read_bytes()
    html = raw.decode("utf-8", "replace")

    # 引擎与轮次从**已对账过的** runs.json 取；不在 runs.json 里的（bing）
    # 单独标注来源，不假装它有正式记录
    rec = next((r for r in RUNS
                if src.name == f"_tmp_c4_raw_{r['engine']}_run{r['run']}.html"), None)
    engine = rec["engine"] if rec else src.name.split("_raw_")[1].split("_run")[0]
    run = rec["run"] if rec else int(src.name.rsplit("run", 1)[1].split(".")[0])

    snips = extract_snippets(html, limit=5)
    rel = results_look_relevant(QUERY, snips) if snips else True

    # 判定层：说清楚这 N 条是三层里的哪一层抽出来的，否则"能解析"很含糊
    n_blocks = html.count('class="b_algo"')
    if n_blocks:
        layer = "结果块（<li class=\"b_algo\">）"
    elif snips:
        layer = "兜底（任意链接）"
    else:
        layer = "无（三层都没抽到）"

    name = src.name.replace("_tmp_c4_raw_", "")
    dst = DEST / name
    shutil.copyfile(src, dst)

    back = dst.read_bytes()
    if back != raw:
        problems.append(f"{name}: 复制后字节不一致")
    if md5(back) != md5(raw):
        problems.append(f"{name}: 复制后 MD5 不一致")

    body = html_to_text(html)
    entry = {
        "file": name,
        "engine": engine,
        "run": run,
        "source": (f"docs/agent/evidence/p4/{src.name}"),
        "recorded_in_runs_json": rec is not None,
        "bytes": len(back),
        "md5": md5(back),
        "title": extract_title(html),
        "page_declared_query": QUERY if QUERY in extract_title(html) else "",
        "result_blocks": n_blocks,
        "visible_text_chars": len(body),
        "parsed": len(snips),
        "parsed_layer": layer,
        "titles": [s["title"] for s in snips],
        "urls": [s["url"] for s in snips],
        "relevant_to_query": rel,
        "verdict": VERDICT.get(engine, ""),
    }
    if rec is not None:
        # 与 P4 正式记录逐条对账（口径照抄 P4：解析出 0 条时 relevant 记 True）
        entry["cross_check"] = {
            "bytes_match": len(raw) == rec["content_bytes"],
            "parsed_match": len(snips) == rec["parsed_by_project_parser"],
            "relevant_match": rel == rec["relevant"],
            "md5_recorded_in_p4": None,
        }
        if not all(v for k, v in entry["cross_check"].items() if k != "md5_recorded_in_p4"):
            problems.append(f"{name}: 与 P4 记录对账不一致 → {entry['cross_check']}")
    entries.append(entry)
    print(f"  {name:26s} {engine:7s} run{run}  {len(back):7d}B  "
          f"md5={entry['md5'][:12]}…  解析 {len(snips)} 条  ({layer})")

manifest = {
    "purpose": "P5-B5：搜索引擎结果页解析器的**离线** fixture",
    "why_real_capture": (
        "解析器的三层提取顺序是照真实页面的坑长出来的（导航栏 / 页脚推荐位），"
        "手写 HTML 只能证明「我自己以为的结构能被解析」"
    ),
    "provenance": "docs/agent/evidence/p4/_tmp_c4_raw_*.html（P4-C4 真实抓取，逐字节复制）",
    "query": QUERY,
    "captured_at": "2026-09-11（P4-C4 评估期间，本机时钟）",
    "engine_verdict": VERDICT,
    "entries": entries,
}

mf = DEST / "manifest.json"
io.open(mf, "w", encoding="utf-8", newline="\n").write(
    json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
)
print(f"\n清单已写入 {mf.relative_to(ROOT)}（{len(entries)} 条）")

if problems:
    print("\n**发现问题，不许当 fixture 用：**")
    for p in problems:
        print(f"  ✘ {p}")
    raise SystemExit(1)

print("复制校验通过：逐字节一致（bytes 与 MD5 都比过），且与 P4 正式记录对账一致。")
