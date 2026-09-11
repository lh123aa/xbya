# -*- coding: utf-8 -*-
"""给用户看的：config.yaml 里 key 到底在第几行（只报行号与键名，不打印值）。

为什么要单独跑一次：C1 要改**三处**（主 key 出现在两个位置，而 `cloud` 覆盖
`params`）。用户在编辑器里找这三行时，如果有准确行号会快很多、也不容易漏。
本脚本只输出行号、缩进和键名，**值一律不打印**。
"""
import io
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
CFG = ROOT / "config.yaml"

lines = io.open(CFG, encoding="utf-8").read().splitlines()

print(f"文件：{CFG}")
print(f"共 {len(lines)} 行")
print()
print("以下三行是轮换 key 时要改的（值一律遮蔽，只给行号）：")
print()
targets = {
    "cloud_api_key": None,
    "params_api_key": None,
    "fallback_api_key": None,
}
for i, l in enumerate(lines, 1):
    s = l.strip()
    if not s or s.startswith("#"):
        continue
    key = s.split(":", 1)[0].strip()
    if key in ("api_key", "fallback_api_key", "fallback_base_url", "base_url"):
        indent = len(l) - len(l.lstrip())
        print(f"  第 {i:4d} 行  缩进{indent:2d}  {key}")
        if key == "api_key":
            # 判断属于 cloud 段还是 params 段：往上找最近的 llm 子键
            for j in range(i - 2, max(0, i - 8), -1):
                up = lines[j]
                if re.match(r"\s*(cloud|params):\s*$", up):
                    which = up.strip().rstrip(":")
                    targets[f"{which}_api_key"] = i
                    break
        elif key == "fallback_api_key":
            targets["fallback_api_key"] = i

print()
print("=" * 66)
print("要改的三行（**必须三行都改**）")
print("=" * 66)
print()
for name, ln in targets.items():
    print(f"  {name:22s} → 第 {ln} 行" if ln else f"  {name:22s} → 没找到！")
print()
print("为什么主 key 有两行：`plugins.llm.cloud.api_key` **覆盖**")
print("`plugins.llm.params.api_key`（实测 `core/plugin_params.plugin_params()`")
print("的返回值取自 cloud）。只改 params 那一行的话，生效的还是旧 key。")
