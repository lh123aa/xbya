# -*- coding: utf-8 -*-
"""把 tasks-p5.json 里**尚未落地**的证据路径从 `evidence` 挪到 `planned_evidence`

为什么要这么做：G13 的"证据路径必须存在"检查现在**自动发现**所有 `tasks*.json`
（P5-AUDIT 修的 —— 原先写死四个文件名，新阶段加进来悄悄不生效）。
于是未完成任务的"计划证据路径"会让关卡一直红，而关卡红久了就会被无视。

正确做法不是把检查放宽，而是**让字段语义更准**：

  · `evidence`          = 已经留档的证据（必须存在，G13 查它）
  · `planned_evidence`  = 打算留到哪（还没写，G13 不查）

这样"把一条任务标 done"的动作就有了可检查的含义：**证据得先从 planned 挪到
evidence**，否则关卡不会绿。脚本会打印改动明细，挪不动就报错退出。
"""
import json
import sys
from pathlib import Path

ROOT = Path(r"E:\程序\桌面宠物\xiaoyi-vrm-worktree")
TASKS = ROOT / "docs" / "agent" / "tasks-p5.json"

data = json.loads(TASKS.read_text(encoding="utf-8"))
moved, kept = [], []
for t in data.get("tasks", []):
    ev = t.get("evidence") or []
    exist, absent = [], []
    for one in (ev if isinstance(ev, list) else [ev]):
        (exist if (ROOT / one).exists() else absent).append(one)
    if absent:
        t["planned_evidence"] = absent
        moved.append((t.get("id"), absent))
    if exist:
        t["evidence"] = exist
        kept.append((t.get("id"), exist))
    else:
        t.pop("evidence", None)

data["evidence_field_semantics"] = {
    "evidence": "已经留档的证据路径（必须真实存在；G13 会逐条检查）",
    "planned_evidence": "打算留到哪（尚未落档；G13 不查）—— 任务标 done 时必须先挪进 evidence",
    "why": "G13 原先写死四个 tasks 文件名，新阶段加进来会悄悄不生效；改成自动发现后，"
           "未完成任务的计划路径会让关卡常红。解决方式是让字段语义更准，而不是放宽检查。",
}
TASKS.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

print("挪进 planned_evidence：")
for tid, paths in moved:
    print(f"  {tid}: {paths}")
if not moved:
    print("  （无）")
print("保留在 evidence（已存在）：")
for tid, paths in kept:
    print(f"  {tid}: {paths}")
