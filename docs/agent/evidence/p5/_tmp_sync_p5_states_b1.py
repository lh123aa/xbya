# -*- coding: utf-8 -*-
"""把 tasks-p5.json 里已到终态的项改成终态（证据文件必须先存在）

与 `_tmp_sync_p5_states.py` 同一套路，只是每完成一项就追加一条。
**拒绝在证据文件不存在时改状态** —— 那正是"文档说做完了、账本也记着做完了，
但证据没写"这类事的发生方式。
"""
import json
import sys
from pathlib import Path

ROOT = Path(r"E:\程序\桌面宠物\xiaoyi-vrm-worktree")
TASKS = ROOT / "docs" / "agent" / "tasks-p5.json"

#: 编号 → (新状态, 证据路径列表)
FIX = {
    "P5-B1": ("fixed", [
        "docs/agent/evidence/p5/d16_schema_shape.txt",
        "plugins/llm/tool_schemas.py",
        "tests/test_tool_schema_shape.py",
    ]),
}

data = json.loads(TASKS.read_text(encoding="utf-8"))
seen = set()
for t in data.get("tasks", []):
    tid = t.get("id")
    if tid not in FIX:
        continue
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
