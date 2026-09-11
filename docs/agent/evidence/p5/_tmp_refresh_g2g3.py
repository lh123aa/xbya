# -*- coding: utf-8 -*-
"""刷新 `g2_g3_tests_coverage.txt` 并让 AGENTS.md 的完成度数字**从它读**。

为什么要刷新：`g2_g3_tests_coverage.txt` 是 G2/G3 关卡的原始输出留档。
它原来由 `tools/run_acceptance.py` 的 PowerShell 重定向产生 —— 那个文件里
覆盖率行被截断（只有 `TOTAL   7238`，没有后面的未覆盖数与百分比），
于是 `_tmp_sync_completion_numbers.py` 的正则读不到覆盖率，数字同步失明。

本脚本用 **Python subprocess + utf-8** 自己跑一次（不用 PowerShell 重定向，
那会写 UTF-16LE），拿完整输出，再更新文档里的两个数。
"""
import io
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
EVID = ROOT / "docs" / "agent" / "evidence" / "p5" / "g2_g3_tests_coverage.txt"
AGENTS = ROOT / "AGENTS.md"

print("跑全量回归 + 覆盖率（项目口径）...")
proc = subprocess.run(
    [sys.executable, "-m", "pytest", "tests", "-q",
     "--cov=core.kernel", "--cov=agent", "--cov=services.ack_cache",
     "--cov-report=term-missing"],
    cwd=str(ROOT), capture_output=True, text=True,
    encoding="utf-8", errors="replace")
out = (proc.stdout or "") + (proc.stderr or "")
print(f"退出码 {proc.returncode}，输出 {len(out.encode('utf-8'))} bytes")

m_test = re.search(r"(\d+) passed(?:, (\d+) skipped)?", out)
m_cov = re.search(r"^TOTAL\s+(\d+)\s+(\d+)\s+(\d+)%", out, re.M)
if not m_test or not m_cov:
    print("FAIL 拿不到测试数或覆盖率 —— 拒绝写证据与文档")
    print(out[-2000:])
    raise SystemExit(1)

passed, skipped = m_test.group(1), m_test.group(2) or "0"
stmts, missed, pct = m_cov.group(1), m_cov.group(2), m_cov.group(3)
print(f"读得：passed={passed} skipped={skipped} 语句={stmts} 未覆盖={missed} {pct}%")
if missed != "0" or pct != "100":
    print("FAIL 覆盖率不是 100%，先修好再来更新完成度表")
    raise SystemExit(1)
if proc.returncode != 0:
    print(f"FAIL 回归退出码 {proc.returncode} 非 0")
    raise SystemExit(1)

EVID.write_text(out, encoding="utf-8", newline="\n")
print(f"证据已刷新：{EVID.relative_to(ROOT)}（{EVID.stat().st_size} bytes）")

src = AGENTS.read_text(encoding="utf-8")
before = len(src.encode("utf-8"))

src, n1 = re.subn(
    r"\| 覆盖率（唯一有硬分母的） \| `core/kernel \+ agent \+ services\.ack_cache` "
    r"共 \*\*\d+ 语句\*\* \| \*\*\d+ / \d+ = 100%\*\*",
    f"| 覆盖率（唯一有硬分母的） | `core/kernel + agent + services.ack_cache` 共 "
    f"**{stmts} 语句** | **{stmts} / {stmts} = 100%**",
    src)
src, n2 = re.subn(
    r"\| 测试用例 \| 全量 `pytest tests` \| \*\*\d+ passed / \d+ skipped / 0 failed\*\*",
    f"| 测试用例 | 全量 `pytest tests` | "
    f"**{passed} passed / {skipped} skipped / 0 failed**",
    src)
if n1 != 1 or n2 != 1:
    print(f"FAIL 替换没命中（覆盖率 {n1} 处、测试数 {n2} 处），拒绝写入")
    raise SystemExit(1)

AGENTS.write_text(src, encoding="utf-8")
after = len(src.encode("utf-8"))
print(f"AGENTS.md {before} -> {after} bytes（预算 65536，余 {65536 - after}）")
assert after < 65536, "超预算了"
print("完成度数字已与证据文件一致")
