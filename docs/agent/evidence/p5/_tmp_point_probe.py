# -*- coding: utf-8 -*-
r"""把精灵加载探针指向指定角色，并**回读确认**改动生效（不做"以为改了"）。

上一次直接在 PowerShell 里用 `python -c` 拼 `t.replace('load_pet("xinya")', ...)`，
反斜杠和引号被 shell 吃掉，脚本抛 SyntaxError —— 而因为管道后面还有
`Select-Object`，**错误被淹没在输出里没被注意**，结果我读的是旧角色的数据。
教训：改文件要回读验证，别信"我明明改了"。
"""
import io
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
probe = ROOT / "docs" / "agent" / "evidence" / "p5" / "_tmp_verify_sprite_load.py"
target = sys.argv[1] if len(sys.argv) > 1 else "xinya2"

t = io.open(probe, encoding="utf-8").read()
before = re.findall(r"load_pet\(([\"'])([^\"']+)\1\)", t)
t2 = re.sub(r"load_pet\(([\"'])[^\"']+\1\)", f'load_pet("{target}")', t)
io.open(probe, "w", encoding="utf-8").write(t2)

# 回读确认
back = io.open(probe, encoding="utf-8").read()
now = re.findall(r"load_pet\(([\"'])([^\"']+)\1\)", back)
print(f"改前 load_pet 调用: {[n for _, n in before]}")
print(f"改后 load_pet 调用: {[n for _, n in now]}")
ok = all(n == target for _, n in now) and bool(now)
print("回读确认:", "OK 已生效" if ok else "!! 没生效")
raise SystemExit(0 if ok else 1)
