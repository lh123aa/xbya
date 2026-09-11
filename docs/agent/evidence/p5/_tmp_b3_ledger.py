# -*- coding: utf-8 -*-
"""P5-B3 记账：把 P5-B3 从 pending 推到实测终态。

为什么单独写脚本而不是手改 JSON：
本项目的账本是**机器可读**的那一份（G13 会按它检查证据文件是否存在），
手改容易漏字段（比如只把 planned_evidence 挪走却忘了改 state）。
这里做三件事并**逐条断言结果**，不满足就直接退非零：
  1. state: pending -> measured
  2. planned_evidence -> evidence（**必须是搬家，不是复制**：留着 planned 就等于
     账本里同时写着"还没写"和"已归档"）
  3. evidence 指向的文件必须真实存在且非空（G13 查的是存在，这里连"非空"一起查）
"""
import io
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
LEDGER = ROOT / "docs" / "agent" / "tasks-p5.json"

raw = io.open(LEDGER, encoding="utf-8").read()
data = json.loads(raw)

task = next(t for t in data["tasks"] if t["id"] == "P5-B3")

assert task["state"] == "pending", f"预期 pending，实际 {task['state']}（不重复记账）"
assert task["planned_evidence"], "planned_evidence 不该为空 —— 否则无从搬家"

moved = list(task.pop("planned_evidence"))
for rel in moved:
    p = ROOT / rel
    assert p.is_file(), f"证据文件不存在：{rel}"
    assert p.stat().st_size > 0, f"证据文件是空的：{rel}"
    print(f"证据存在且非空：{rel}（{p.stat().st_size} bytes）")

task["state"] = "measured"
task["evidence"] = moved
# 结论必须写进账本，否则"实测过"和"实测出什么"会分家
task["conclusion"] = (
    "本机实测通过：真 ST（all-MiniLM-L6-v2，384 维）懒加载 13.29s，"
    "首次 embed_one 后 ready()=True，向量 L2 范数 1.000000；"
    "真 ST + 真 vec0 下写入 3 条并检索，语义命中正确（「浏览器」/「Chrome」"
    "命中同一条、「外星人」0 命中）；"
    "同一库文件从 256 维切到 384 维**不抛异常**，dim 如实报 384，"
    "旧文本条目仍在（count=1），且硬证据显示 vec0 虚表已被重建为 float[384]"
    "（schema 原文留档）；强制 vector_backend='python' 的对照也可写入与检索"
)

data["tasks"] = [task if t["id"] == "P5-B3" else t for t in data["tasks"]]

io.open(LEDGER, "w", encoding="utf-8", newline="\n").write(
    json.dumps(data, ensure_ascii=False, indent=2) + "\n"
)

# 复读一次，确认落盘的就是我们以为写下去的东西
back = json.loads(io.open(LEDGER, encoding="utf-8").read())
b3 = next(t for t in back["tasks"] if t["id"] == "P5-B3")
assert b3["state"] == "measured"
assert b3["evidence"] == moved
assert "planned_evidence" not in b3
print("复读校验通过：state=measured，evidence=%s，planned_evidence 已移除" % b3["evidence"])
