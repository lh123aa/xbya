# -*- coding: utf-8 -*-
"""P5-B5 记账 —— 补上我漏掉的一次。

**这次漏记正是审计脚本存在的意义**：P5-B5 我在 `AGENTS.md` 的叙述里写了
「✅ 完成」，但**忘了改 `tasks-p5.json`**。收口审计一跑就把它照出来了
（「未到终态 1」），因为**机器只读账本**。

与 P5-A1 那次发现的问题同族：**文档说做完了 ≠ 账本记着做完了**。
所以这里把动作补上，并顺手把"evidence 文件要真实存在且非空"再断言一遍。
"""
import io
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
LEDGER = ROOT / "docs" / "agent" / "tasks-p5.json"

data = json.loads(io.open(LEDGER, encoding="utf-8").read())
task = next(t for t in data["tasks"] if t["id"] == "P5-B5")
assert task["state"] == "pending", f"预期 pending，实际 {task['state']}"

moved = list(task.pop("planned_evidence"))
assert moved, "planned_evidence 是空的"
for rel in moved:
    p = ROOT / rel
    assert p.is_file(), f"证据文件不存在：{rel}"
    assert p.stat().st_size > 0, f"证据文件是空的：{rel}"
    print(f"证据存在且非空：{rel}（{p.stat().st_size} bytes）")

# B5 除了主证据，还留了一份原始探针输出（第 0 步对账 + 两次定位探针）
extra = "docs/agent/evidence/p5/serp_parser_raw_probes.txt"
assert (ROOT / extra).is_file(), f"缺原始探针输出：{extra}"

task["state"] = "measured"
task["evidence"] = moved + [extra]
task["conclusion"] = (
    "已用 P4 真实抓取的 8 份 SERP 原文做成离线 fixture"
    "（tests/fixtures/serp/ + manifest.json，逐份记字节数与 MD5，"
    "复制后回读校验一致）。结论：必应可用（从 <li class=\"b_algo\"> 抽出 5 条"
    "真实标题+链接）；百度不可为（1488B 安全验证页，零结果结构，3/3 次逐字节相同）；"
    "谷歌不可为（92KB 脚本中继页，可见正文 53 字符，唯一命中是页脚「反馈」）。"
    "拦下必应**诱饵页**与谷歌页脚链接的都是相关性闸门而不是解析层。"
    "过程中还纠正了源码 docstring 里一句被我实测证伪的说法"
    "（「每一层都是为了绕开实测出来的坑」——实测第 1 层当下并不改变输出，"
    "挡住导航栏的是第 2 层），并把改正后的说法也用 4 条用例钉住。"
    "72 项新用例（tests/agent/test_serp_fixtures.py），全部离线、不发网络请求。"
)

data["tasks"] = [task if t["id"] == "P5-B5" else t for t in data["tasks"]]
io.open(LEDGER, "w", encoding="utf-8", newline="\n").write(
    json.dumps(data, ensure_ascii=False, indent=2) + "\n")

back = json.loads(io.open(LEDGER, encoding="utf-8").read())
b5 = next(t for t in back["tasks"] if t["id"] == "P5-B5")
assert b5["state"] == "measured"
assert b5["evidence"] == moved + [extra]
assert "planned_evidence" not in b5
print(f"复读校验通过：P5-B5 state=measured，evidence={b5['evidence']}")

# 全账本自检：还有没有 pending？
pend = [t["id"] for t in back["tasks"] if t["state"] == "pending"]
print(f"全账本剩余 pending：{pend or '无'}")
if pend:
    raise SystemExit(f"仍有未到终态的项：{pend}")
print("全部 18 项均已到终态")
