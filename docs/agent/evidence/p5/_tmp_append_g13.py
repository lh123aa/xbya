# -*- coding: utf-8 -*-
"""把 G13 的原始输出以 UTF-8 追加进证据文件

为什么要单独写脚本：PowerShell 的 `*>>` 重定向默认写 UTF-16LE，
而文件其余部分是 UTF-8 —— 混在一起中文会乱码（第一次生成时就踩到了）。
用 Python 以 utf-8 文本模式重定向子进程输出，编码就是确定的。
"""
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(r"E:\程序\桌面宠物\xbya-vrm-worktree")
OUT = ROOT / "docs" / "agent" / "evidence" / "p5" / "g13_hygiene.txt"

proc = subprocess.run(
    [sys.executable, "tools/check_repo_hygiene.py"],
    cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
)
with OUT.open("a", encoding="utf-8") as fh:
    fh.write(f"\n生成时间：{datetime.now():%Y-%m-%d %H:%M:%S}\n")
    fh.write(f"命令：python tools/check_repo_hygiene.py（cwd={ROOT}）\n\n")
    fh.write(proc.stdout or "")
    if proc.stderr:
        fh.write("\n[stderr]\n" + proc.stderr)
    fh.write(f"\n[exit] {proc.returncode}\n")

print(f"exit={proc.returncode}  写入 {OUT.stat().st_size} bytes")
for line in (proc.stdout or "").splitlines()[-8:]:
    print(line)
