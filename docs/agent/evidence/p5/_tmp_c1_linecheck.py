# -*- coding: utf-8 -*-
"""核对 config.yaml 里 `cloud:` / `params:` 两个段各在第几行，以及 api_key 归属。

起因：我先用一支探针报「要改 149/154/156」，后一支脚本报「cloud=154、params=149」
—— **两支脚本对同一件事给出了相反的行号**。这种自相矛盾必须先查清，
不能挑一个信。查法：直接把那一带的原文打出来（key 值遮蔽），看段落归属。
"""
import io
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
lines = io.open(ROOT / "config.yaml", encoding="utf-8").read().splitlines()

print("config.yaml 第 140~165 行（api_key 的值一律遮蔽，只显示长度）")
print("=" * 74)
for i in range(139, min(166, len(lines) + 1)):
    s = lines[i - 1].rstrip()
    if re.search(r"(api_key|fallback_api_key)\s*:", s):
        key = s.split(":", 1)[0].strip()
        val = s.split(":", 1)[1].strip()
        print(f"{i:4d} | {key:20s} = <遮蔽 len={len(val)}>")
    else:
        print(f"{i:4d} | {s[:68]}")

print()
print("=" * 74)
print("按缩进与最近的段标题判断归属（这才是权威判据）")
print("=" * 74)
print()
owner = {}
for i, l in enumerate(lines):
    m = re.match(r"(\s*)(cloud|params):\s*$", l)
    if m:
        owner[m.group(2)] = {"header_line": i + 1, "indent": len(m.group(1)),
                             "keys": {}}

for i, l in enumerate(lines):
    s = l.strip()
    if not s or s.startswith("#") or ":" not in s:
        continue
    key = s.split(":", 1)[0].strip()
    if key not in ("api_key", "fallback_api_key", "model", "base_url"):
        continue
    indent = len(l) - len(l.lstrip())
    for name, info in owner.items():
        if indent > info["indent"]:
            info["keys"][key] = i + 1

for name, info in owner.items():
    print(f"`{name}:` 段标题在第 {info['header_line']} 行 —— 它下面的键：")
    for k, ln in info["keys"].items():
        print(f"    第 {ln:4d} 行  {k}")
    print()
