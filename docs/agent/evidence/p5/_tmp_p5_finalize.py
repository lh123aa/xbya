# -*- coding: utf-8 -*-
"""P5 收口：重建审计证据 → 跑 13 关 → 复跑 G13 → 提交

顺序很关键，因为 G13 会检查 **工作区是否干净**：
  1. 重建 closure_audit.txt（读最新的 tasks-p5.json 与 AGENTS.md）
  2. 在**还没提交**的状态下跑 13 关 —— G13 会红，这是**正确**的，如实留档
  3. 提交（此时 evidence 里落的是"提交前那一刻"的 G13 原文）
  4. 提交后再复跑一次 G13 并追加到 g13_hygiene.txt，此时才可能 20/20

用 Python 而不是 PowerShell：`*>>` 默认写 UTF-16LE，与 UTF-8 正文混在一起中文会乱码。
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(r"E:\程序\桌面宠物\xbya-vrm-worktree")
P5 = ROOT / "docs" / "agent" / "evidence" / "p5"


def run(args, log=None, echo_tail=12):
    print(f"\n$ {' '.join(str(a) for a in args)}")
    proc = subprocess.run(args, cwd=ROOT, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    out = (proc.stdout or "")
    if proc.stderr:
        out += "\n[stderr]\n" + proc.stderr
    if log:
        with (P5 / log).open("a", encoding="utf-8") as fh:
            fh.write(f"\n$ {' '.join(str(a) for a in args)}\n")
            fh.write(out)
            fh.write(f"[exit] {proc.returncode}\n")
    for line in out.splitlines()[-echo_tail:]:
        print(line)
    print(f"[exit {proc.returncode}]")
    return proc.returncode


# ── 1. 重建审计证据 ──
run([sys.executable, "docs/agent/evidence/p5/_tmp_closure_audit.py"], echo_tail=10)

# ── 2. 13 关验收（工作区此刻是脏的 ⇒ G13 必然红，如实留档）──
run([sys.executable, "tools/run_acceptance.py"], echo_tail=25)

# ── 3. 复跑 G13 并把原文追加进证据 ──
run([sys.executable, "docs/agent/evidence/p5/_tmp_append_g13.py"], echo_tail=10)

# ── 4. 提交 ──
run(["git", "add", "-A"], echo_tail=3)
run(["git", "commit", "-F", "docs/agent/evidence/p5/_commit_msg3.txt"], echo_tail=6)

# ── 5. 提交后复跑 G13（此轮才应当 20/20）──
run([sys.executable, "docs/agent/evidence/p5/_tmp_append_g13.py"], echo_tail=10)

# ── 6. 脏文件检查（G13 的可见证据只落一份，故最后再看一次）──
run(["git", "status", "--short"], echo_tail=10)
print("\nDONE")
