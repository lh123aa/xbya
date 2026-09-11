# -*- coding: utf-8 -*-
"""生成 P5-B5 证据：`docs/agent/evidence/p5/serp_parser.txt`

内容 = 「真实抓取 → 离线 fixture → 解析结论」这条链的原始输出，
外加上一轮我写错断言、被自己的探针纠正的过程（这段必须留档 ——
它是"判据要贴着事实写"的现场记录，不是花絮）。

同 P5-B3 的教训：**用 Python 写文件、不用 PowerShell 重定向**
（`*>>` 写出来的是 UTF-16LE，和 UTF-8 文本混在一起就是乱码）。
"""
import hashlib
import io
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
OUT = ROOT / "docs" / "agent" / "evidence" / "p5" / "serp_parser.txt"
FX = ROOT / "tests" / "fixtures" / "serp"
P4 = ROOT / "docs" / "agent" / "evidence" / "p4"

sys.path.insert(0, str(ROOT))
import agent.tools.browser_tools as bt            # noqa: E402
from agent.tools.browser_tools import (           # noqa: E402
    extract_snippets, extract_title, html_to_text, results_look_relevant,
)

L = []
w = L.append


def rule(title):
    w("")
    w("─" * 78)
    w(f" {title}")
    w("─" * 78)
    w("")


def run(cmd, cwd=ROOT):
    p = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return p.returncode, (p.stdout or "") + (p.stderr or "")


MANIFEST = json.loads((FX / "manifest.json").read_text(encoding="utf-8"))
ENTRIES = MANIFEST["entries"]

w("═" * 78)
w("P5-B5 证据：搜索引擎结果页（SERP）解析器的离线 fixture")
w("═" * 78)
w("")
w(f"生成时间：{datetime.now():%Y-%m-%d %H:%M:%S}")
w("可复跑：`python docs/agent/evidence/p5/_tmp_b5_evidence.py`")
w("原始数据：`docs/agent/evidence/p4/_tmp_c4_raw_*.html`（P4-C4 真实抓取）")
w("fixture  ：`tests/fixtures/serp/`（8 份 + manifest.json）")
w("用例     ：`tests/agent/test_serp_fixtures.py`")
w("")
w("**本文件不发任何网络请求** —— 这正是 B5 的目的：把「必须有网才能测」的")
w("解析器行为变成可回归的离线判据。")

rule("零、为什么 fixture 必须是真实抓取的字节，不能手写")
w("`extract_snippets` 的三层提取顺序是**照着真实页面的坑**长出来的。")
w("手写 HTML 只能证明「我按自己以为的结构写的东西能被解析」；")
w("真实字节才能证明**当时服务器真的发回来的那份页面**能被解析。")
w("所以 fixture 的完整性判据是 **MD5** —— 它对着 P4 当时的留档，")
w("不是「一份看起来像必应结果页的文件」。")

rule("一、P4 留档清单（fixture 的来源）")
w("| 文件 | 引擎 | 字节 | MD5 | 解析条数 | 判定层 |")
w("|------|------|------|-----|---------|--------|")
for e in ENTRIES:
    w(f"| `{e['file']}` | {e['engine']} | {e['bytes']} | `{e['md5'][:16]}…` | "
      f"{e['parsed']} | {e['parsed_layer']} |")
w("")
w(f"查询词统一为「{MANIFEST['query']}」（URL 编码 "
  "%E4%B8%8A%E6%B5%B7%E5%A4%A9%E6%B0%94）")

rule("二、与 P4 正式记录对账（`_tmp_c4_runs.json`）")
recs = json.loads((P4 / "_tmp_c4_runs.json").read_text(encoding="utf-8"))
by_file = {e["file"]: e for e in ENTRIES}
w("口径说明：P4 探针把「解析出 0 条」记成 `relevant=True`"
  "（`_tmp_c4_probe_search.py:390` 写的是 `if parsed else True`），")
w("意思是「没有结果可判，不算它不相关」。**这一条我照抄**，")
w("不自作主张改成 False —— 否则就是我这边口径不同却报成「对账失败」")
w("（第一版探针正是这么错了一次）。")
w("")
ok_all = True
for r in recs:
    e = by_file[f"{r['engine']}_run{r['run']}.html"]
    b = e["bytes"] == r["content_bytes"]
    p = e["parsed"] == r["parsed_by_project_parser"]
    exp = r["relevant"] if e["parsed"] else True
    rel = e["relevant_to_query"] == exp
    ok_all &= (b and p and rel)
    w(f"- `{e['file']}`：字节 {e['bytes']}=={r['content_bytes']} "
      f"{'✔' if b else '✘'} · 解析 {e['parsed']}=={r['parsed_by_project_parser']} "
      f"{'✔' if p else '✘'} · 相关性一致 {'✔' if rel else '✘'}")
w("")
w(f"**对账结论：{'6/6 逐条一致' if ok_all else '有不一致'}**")
w("")
w("必应的两份**不在** runs.json 里（是 P4 补测留的），清单里如实标 "
  "`recorded_in_runs_json: false`。")
w("它们的来源**不靠文件名**，靠页面自身：`<title>上海天气 - 搜索</title>` "
  "+ `<input name=\"q\" value=\"上海天气\">`。")

rule("三、三个引擎的实测结论（离线复算）")
for eng in ("bing", "baidu", "google"):
    es = [e for e in ENTRIES if e["engine"] == eng]
    w(f"### {eng} —— {MANIFEST['engine_verdict'][eng]}")
    w("")
    for e in es:
        html = (FX / e["file"]).read_text(encoding="utf-8", errors="replace")
        snips = extract_snippets(html, limit=5)
        rel = results_look_relevant(MANIFEST["query"], snips) if snips else True
        w(f"- `{e['file']}`：解析 **{len(snips)} 条**"
          f"（清单 {e['parsed']}）· 与查询相关？**{rel}**"
          f"（清单 {e['relevant_to_query']}）· 可见正文 {len(html_to_text(html))} 字符")
        for s in snips[:5]:
            w(f"    - `{s['title'][:52]}` → `{s['url'][:64]}`")
    w("")

rule("四、★ 本轮最值得记的一条：必应的诱饵页")
bing = [e for e in ENTRIES if e["engine"] == "bing"]
w("必应对脚本客户端返回的是**诱饵页**：")
w("")
w("- 标题写着「上海天气 - 搜索」，`<li class=\"b_algo\">` 结果块 **10 个**齐备，")
w("  解析层**成功**抽出 5 条 —— 从解析层看，一切正常")
for e in bing:
    w(f"- 但 `{e['file']}` 抽到的实际是："
      + "、".join(f"`{t[:26]}`" for t in e["titles"][:2]) + " …")
w("")
w("两轮抓取的内容**互不相同**（一轮是日本司法书士协会，一轮是波兰足球比分），")
w("**都不是**「上海天气」的答案。")
w("")
w("→ 所以：**解析层识别不了诱饵**，能拦住它的是相关性闸门")
w("  （`results_look_relevant`，browser_tools.py:419 调用）。")
w("  两条结论都写进了用例：解析层如实报 5 条，闸门如实判 False。")
w("  谁把闸门去了、或把这些「看起来成功」的结果直接念给用户，用例会红。")

rule("五、★ 我自己写错的一条断言，被探针当场纠正")
w("**起因**：我给 fixture 写反方向用例时断言：")
w("")
w("> 把 `class=\"b_algo\"` 全抹掉后，抽出来的结果**必须不一样** ——")
w("> 否则说明条数不是第 1 层给的")
w("")
w("**它红了**：抹掉之后 5 条 URL **一模一样**。")
w("")
w("**不能把断言改宽了事，先测清楚真实行为**（探针 "
  "`_tmp_b5_layer_probe.py` / `_tmp_b5_docstring_probe.py`，均留档）。")
w("")
w("实测（`bing_run1.html`，整页 118102 字符）：")
w("")
w("| 位置 | 内容 |")
w("|------|------|")
w("| 33% 处 | 导航栏 `<a>`：图片 / 视频 / 地图 / 资讯 / 航班 |")
w("| 50% 处 | 第一个真实结果块 `<li class=\"b_algo\">` |")
w("| 52% 处 | 第一条真实结果标题 `<h2>` |")
w("")
w("| 作用域 | 模式 | 抽出条数 | 内容 |")
w("|--------|------|---------|------|")
w("| ① 结果块 | h2/h3 | 5 | 真实结果 |")
w("| ② 全页 | h2/h3 | 5 | **与①逐条相同** |")
w("| ② 全页 | 任意链接 | 5 | **图片/视频/地图/资讯/航班** ← 导航栏 |")
w("")
w("**三条结论（与我原先的预期不同）**：")
w("")
w("1. 挡住导航栏的是**第 2 层**（h2/h3 结构），**不是**第 1 层 ——")
w("   全页 h2/h3 的 10 处命中**全部**在结果区之内（0 处在结果区之前），")
w("   而任意链接有 5 处在结果区之前，正是那 5 个导航项。")
w("2. 第 1 层（结果块）在这两份抓取上**没有改变输出**：")
w("   去掉它以后结果逐条相同（因为真实结果本来就都在 `<h2>` 里）。")
w("   它的作用是「把范围收窄到结果区」，不是当下就在筛内容。")
w("3. 源码 docstring 里「只用 h2/h3 也还不够 —— 会命中页脚论坛推荐位」")
w("   这一句，**在我手上这两份留档里复现不出来**（h2/h3 没有命中任何")
w("   结果区之前的位置）。我不据此宣布它错 —— 那是 P4 当时另一次抓取的")
w("   观察，我手上没有那次的字节。**如实记为「本轮无法复核」**。")
w("")
w("**处置**：")
w("")
w("- docstring 里「每一层都是为了绕开实测出来的坑」这类把「结构上合理」")
w("  说成「实测必要」的说法，按实测改写；把三条结论写进去")
w("- 反方向断言改成断言**事实**：第 1 层当下不改变输出（`test_first_layer_"
  "does_not_currently_change_the_output`）")
w("- 新增 `TestDocstringMatchesMeasurement` 4 条：**改写过的说法也被钉住**，")
w("  写回去就变红（否则「修文档」只是一次性动作）")
w("")
w("⚠️ 顺带踩到、并已绕开的一个坑：我在 docstring 的**改正说明里引用了旧措辞**，")
w("结果被自己那条「旧措辞不得出现」的用例命中 —— grep 型审计会被自己的")
w("说明文字骗到。这与 §16.4 那次「`# pragma: no cover` 被注释数进去」同源。")
w("处置：改正说明里不照抄那句字面量，并写明原因。")

rule("六、用例运行（离线，含反方向验证）")
rc, out = run([sys.executable, "-m", "pytest",
               "tests/agent/test_serp_fixtures.py", "-q"])
w("```")
w(out.strip())
w("```")
w("")
w(f"退出码：{rc}")
w("")
w("### 反方向验证：把这些用例的判据拿掉，会不会红？")
w("")
w("本轮有两个判据是**在写的过程中被证伪/证实的**，所以不需要另做人为破坏 ——")
w("它们本身就是反方向验证：")
w("")
w("| # | 我原先的说法 | 探针结果 | 处置 |")
w("|---|-------------|---------|------|")
w("| 1 | 抹掉结果块 → 结果必须变 | **红了**：逐条相同 | 断言改成事实，docstring 改写 |")
w("| 2 | P4 记的 baidu `relevant=True` 与我的复算不符 | 是**口径不同**：P4 对 0 条记 True | 照抄 P4 口径，并把口径写进对账段 |")
w("")
w("第 2 条特别值得留：第一版对账探针报「3 处不一致」，看起来像"
  "「fixture 有问题」，")
w("实际是**我这边口径和别人不同**。若当时直接去「修」数据迎合我的口径，")
w("就是亲手把一份真实留档改坏。")

rule("七、边界声明（这份证据**不**说明什么）")
w("- 它**不**说明百度/谷歌将来也不能用：结论绑定在**这两批字节 + 本机网络**上")
w("  （百度 3 次逐字节相同 = 确定性命中验证码页；谷歌是 meta refresh 中继页）")
w("- 它**不**验证真实搜索的召回质量：必应的两份是**诱饵页**，")
w("  即「解析器能工作」与「结果对用户有用」在这里是分开的两件事")
w("- 它**不**覆盖其他搜索引擎（DuckDuckGo 等）：`SEARCH_ENGINES` 里没有的")
w("  引擎没有留档，不猜它们能不能解析")
w("- 第 1 层「当下不改变输出」是**当下这两份页面**的结论；")
w("  页面形态一变（页面级出现 h2/h3）该结论就会变 —— 用例会红，届时需更新")

w("")
w("═" * 78)
w(" 结论")
w("═" * 78)
w("")
w("`extract_snippets` 在**真实抓取的整页**上：")
w("")
w("| 引擎 | 结论 | 依据 |")
w("|------|------|------|")
w("| bing | **可用**（解析层） | 从 `<li class=\"b_algo\">` 抽出 5 条真实标题+链接 |")
w("| baidu | **不可为（反爬）** | 1488B 安全验证页，零结果结构，3/3 次逐字节相同 |")
w("| google | **不可为（反爬-需 JS）** | 92KB 脚本中继页，可见正文 53 字符，唯一命中是页脚「反馈」 |")
w("")
w("并且拦下必应诱饵页、拦下谷歌页脚「反馈」的，都是**相关性闸门**而不是解析层 ——")
w("这一点现在有了可回归的离线证据。")

OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text("\n".join(L) + "\n", encoding="utf-8")
print(f"写入 {OUT}（{OUT.stat().st_size} bytes）")
print(f"用例退出码={rc}")
