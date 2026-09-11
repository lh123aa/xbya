# -*- coding: utf-8 -*-
r"""★ 复盘：**沙箱准备脚本自己把 config.yaml 写坏了**，然后应用把它重写成默认值。

这不是猜的，逐步复现：

  1. 应用启动日志：`加载配置文件失败: while scanning a double-quoted scalar
     in "config.yaml", line 105, column 22 / expected escape sequence of
     8 hexadecimal numbers, but found 's'`
  2. 备份（`config.yaml.manual_backup`，是 `init` 跑完之后的快照）**能正常解析**
     —— 说明那时文件还是好的？不，看第 3 步
  3. 但应用**当时读的是** `init` 改过的那份（不是备份）。所以要去问：
     `init` 往白名单那行写了什么。

关键：YAML 的**双引号标量里，反斜杠是转义符**。
`init` 写入的是 `"C:\Users\49060\AppData\Local\Temp\xiaoyi_manual\Desktop"`，
其中 `\U` 被当成 Unicode 转义的开头 → `expected escape sequence of 8
hexadecimal numbers` —— **与日志里的报错逐字一致**。

本脚本用**产品自己的那个函数**复现，而不是我另写一份替换逻辑
（本项目吃过"复刻判据"的亏）。
"""
import io
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))

from tools.prepare_manual_acceptance import (                       # noqa: E402
    SANDBOX, WHITELIST_DIRS, _whitelist_line_index,
)

print("=" * 78)
print("复现：`init` 写的白名单行能不能被 YAML 解析")
print("=" * 78)
print()

# 复刻 init 的写法（与 tools/prepare_manual_acceptance.py:127-128 一致）
dirs = ", ".join(f'"{SANDBOX / d}"' for d in WHITELIST_DIRS)
written = f"    path_whitelist: [{dirs}]\n"

print("`init` 实际写出的那一行（就是它）：")
print(f"  {written.strip()[:150]}")
print()

sample = "agent:\n  safety:\n" + written + "    provider: basic\n"
print("把它放进一个最小 YAML 里解析：")
try:
    got = yaml.safe_load(sample)
    print(f"  ✔ 解析成功：{got}")
    print("     ⇒ 那问题不在这里，得继续找")
except Exception as e:                                              # noqa: BLE001
    print(f"  ✘ 解析失败：{type(e).__name__}")
    print(f"     {str(e)[:200]}")
    print()
    print("  ⇒ **确认**：这一行写出来的不是合法 YAML。")
    print("     原因：双引号标量里反斜杠是转义符，路径里的 `\\U` 被当成")
    print("     Unicode 转义的开头（`\\Uxxxxxxxx` 要 8 位十六进制）。")
    print("     Windows 路径原样塞进双引号必然踩这个坑。")

print()
print("=" * 78)
print("对照：换成正斜杠 / 单引号 / 不加引号，哪种是合法 YAML")
print("=" * 78)
print()
variants = {
    "现状（双引号 + 反斜杠路径）": f'    path_whitelist: [{dirs}]',
    "正斜杠 + 双引号": "    path_whitelist: ["
        + ", ".join(f'"{str(SANDBOX / d).replace(chr(92), "/")}"'
                    for d in WHITELIST_DIRS) + "]",
    "反斜杠 + 单引号": "    path_whitelist: ["
        + ", ".join(f"'{SANDBOX / d}'" for d in WHITELIST_DIRS) + "]",
    "反斜杠 + 不加引号": "    path_whitelist: ["
        + ", ".join(f"{SANDBOX / d}" for d in WHITELIST_DIRS) + "]",
}
for name, line in variants.items():
    try:
        parsed = yaml.safe_load(f"agent:\n  safety:\n{line}\n    provider: basic\n")
        ok = parsed["agent"]["safety"]["path_whitelist"]
        same = [str(Path(p)) for p in ok] == [str(SANDBOX / d) for d in WHITELIST_DIRS]
        print(f"  {name:26s} → 解析成功，**还原出的路径与写入的一致？{same}**")
        for p in ok:
            print(f"        {p}")
    except Exception as e:                                          # noqa: BLE001
        print(f"  {name:26s} → ✘ 失败：{str(e)[:80]}")
print()
print("=" * 78)
print("结论")
print("=" * 78)
print()
print("1. `prepare_manual_acceptance.py` 用双引号包 Windows 路径 ⇒ 写出非法 YAML。")
print("2. 应用启动时 `ConfigManager` 读失败 → **回退默认值** → 随后")
print("   `保存配置成功`（日志里连打 5 次）把默认值**覆盖回磁盘**。")
print("3. 于是 213 行 → 65 行：LLM 变 ollama、ASR 变 base、`agent:` 整段消失。")
print()
print("★ 这条比「配置写坏」更值得记的是第 2 条：")
print("   一个**读失败**会静默演变成**写丢失**。用户的配置没有备份就没了。")
print("   而 `init` 自己是有备份的（`config.yaml.manual_backup`），这次靠它救回来。")
