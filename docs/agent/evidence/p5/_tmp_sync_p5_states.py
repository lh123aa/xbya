# -*- coding: utf-8 -*-
"""把 tasks-p5.json 的状态字段对齐现实（收口审计的前置动作）

审计脚本一跑就照出一个**账本与现实不一致**：AGENTS.md 的 P5 表里
P5-A1/A3/AUDIT 都是 ✅ 完成，而 tasks-p5.json 里它们还是 `pending`。
这正是本项目反复防的那类问题 —— **"文档说做完了"与"账本记着做完了"是两件事**，
而机器只读后者。审计脚本的价值就在这：它读的是机器可读的那份。

本脚本只做一件事：把状态与证据字段改成与事实一致，改不动就报错退出。
"""
import json
import sys
from pathlib import Path

ROOT = Path(r"E:\程序\桌面宠物\xbya-vrm-worktree")
TASKS = ROOT / "docs" / "agent" / "tasks-p5.json"

#: 编号 → (新状态, 要落档的证据路径)
FIX = {
    "P5-A1": ("done", ["docs/agent/f-closure.md", "AGENTS.md"]),
    "P5-A3": ("done", ["AGENTS.md", "docs/agent/p5-closure-plan.md"]),
    "P5-AUDIT": ("fixed", ["tools/check_repo_hygiene.py",
                          "docs/agent/evidence/p5/g13_hygiene.txt"]),
    "P5-AUDIT-CLOSURE": ("done", ["docs/agent/evidence/p5/closure_audit.txt"]),
}

data = json.loads(TASKS.read_text(encoding="utf-8"))
seen = set()
for t in data.get("tasks", []):
    tid = t.get("id")
    if tid in FIX:
        state, ev = FIX[tid]
        missing = [p for p in ev if not (ROOT / p).exists()]
        if missing:
            print(f"FAIL {tid} 的证据还不存在：{missing} —— 拒绝改状态")
            sys.exit(1)
        t["state"] = state
        t["evidence"] = ev
        t.pop("planned_evidence", None)
        seen.add(tid)
        print(f"  {tid}: state={state}  evidence={ev}")

missing_ids = set(FIX) - seen
if missing_ids:
    print(f"FAIL 这些编号在 tasks-p5.json 里没找到：{sorted(missing_ids)}")
    sys.exit(1)

TASKS.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print("已写回", TASKS)
