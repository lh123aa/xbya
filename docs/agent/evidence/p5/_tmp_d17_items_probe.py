# -*- coding: utf-8 -*-
"""P5-A2 附加探查：`items` 类型未被校验时会怎样

D17 让规划期按 schema 校验参数类型，但判据（`describe_value_problem`）只看**顶层类型**，
不看 `array` 的 `items` —— 因为**执行期也一样**（`BaseTool.validate_params` 共用同一函数）。

于是有一个问题必须查清：**执行期对"array 里套 array"到底会做什么？**
`_collect_targets` 里有一句 `str(t) for t in explicit`，而 `str(["a.txt"])` 会变成
`"['a.txt']"` —— 那是一个**看起来像列表的字符串**。

三种可能，危害完全不同：
  · 报错（可接受，只是晚了一步）
  · 什么都不做（可接受）
  · **删掉别的文件**（不可接受，必须立即修）

本脚本用**真实工具 + 真实沙箱**把答案跑出来，不做推测。
全程 `dry_run` 语义由工具自身决定：这里只调 `preview()`（不产生副作用），
再对"是否会把字符串化结果当成路径"做静态核对。
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
for sub in ("agent", "core", "services"):
    sys.path.insert(0, str(ROOT / sub))

import logging                                                            # noqa: E402
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

from agent.providers.safety.basic_guard import BasicGuard                  # noqa: E402
from agent.tools.file_tools import FileDeleteTool                          # noqa: E402

sandbox = Path(tempfile.mkdtemp(prefix="xbya_d17_"))
(sandbox / "a.txt").write_text("A", encoding="utf-8")
(sandbox / "b.txt").write_text("B", encoding="utf-8")

guard = BasicGuard(whitelist=[str(sandbox)])
tool = FileDeleteTool(guard)

print("=" * 72)
print(f"沙箱：{sandbox}（内含 a.txt / b.txt）")
print("=" * 72)

CASES = [
    ("正常：targets=['a.txt']",           {"targets": [str(sandbox / "a.txt")]}),
    ("坏值：targets=[['a.txt']]",         {"targets": [[str(sandbox / "a.txt")]]}),
    ("坏值：targets=[{'p':'a.txt'}]",     {"targets": [{"p": str(sandbox / "a.txt")}]}),
    ("坏值：targets=[123]",               {"targets": [123]}),
    ("坏值：targets='a.txt'（字符串）",   {"targets": str(sandbox / "a.txt")}),
]

for label, params in CASES:
    print()
    print("-" * 72)
    print(label)
    # 1) 参数校验怎么说
    try:
        tool.validate_params(params)
        print("  validate_params: 通过")
    except Exception as exc:                                              # noqa: BLE001
        print(f"  validate_params: 拒绝 -> {exc}")
    # 2) 预览说什么（预览 = 用户会听到的确认问句）
    try:
        prev = tool.preview(params)
        print(f"  preview: {prev!r}")
    except Exception as exc:                                              # noqa: BLE001
        print(f"  preview: 抛异常 -> {type(exc).__name__}: {exc}")
    # 3) 内部把它变成了什么路径（这是判断"会不会删错"的关键）
    try:
        raw = tool._collect_targets(params)
        print(f"  _collect_targets: {raw!r}")
    except Exception as exc:                                              # noqa: BLE001
        print(f"  _collect_targets: 抛异常 -> {type(exc).__name__}: {exc}")

print()
print("=" * 72)
print("结论判据（不是推断，是上面输出直接读出来的）")
print("=" * 72)
print("  若某个坏值让 _collect_targets 产出**沙箱里真实存在**的文件路径 → 会删错，必须修")
print("  若产出的是一个字符串化的假路径（如 ['...']）→ 删不到东西，属「晚一步报错」")
print()
print("DONE")
