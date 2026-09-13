# -*- coding: utf-8 -*-
"""让 AGENTS.md 的"完成度"数字**从证据文件里读**，而不是手抄

为什么要写成脚本：这一版手抄的数字已经过期过一次（2668 → 实际 2694）。
手抄的数字与证据分离之后，**没有任何机制会发现它们对不上** ——
而"完成度表写着一个比实际小的数"这件事，与"文档说做完了、其实没做"是同一类
（都是文档与事实脱节）。所以：数字一律从 `docs/agent/evidence/p5/g2_g3_tests_coverage.txt`
里正则读出来再写进去，脚本找不到就报错退出，不猜。
"""
import re
import sys
from pathlib import Path

ROOT = Path(r"E:\程序\桌面宠物\xbya-vrm-worktree")
AGENTS = ROOT / "AGENTS.md"
EVID = ROOT / "docs" / "agent" / "evidence" / "p5" / "g2_g3_tests_coverage.txt"

text = EVID.read_text(encoding="utf-8", errors="replace")

m_test = re.search(r"(\d+) passed(?:, (\d+) skipped)?", text)
m_cov = re.search(r"^TOTAL\s+(\d+)\s+(\d+)\s+(\d+)%", text, re.M)
if not m_test or not m_cov:
    print("FAIL 证据文件里读不到测试数/覆盖率，拒绝改写文档")
    sys.exit(1)

passed = m_test.group(1)
skipped = m_test.group(2) or "0"
total_stmts, missed, pct = m_cov.group(1), m_cov.group(2), m_cov.group(3)
print(f"从证据读出：passed={passed} skipped={skipped} 语句={total_stmts} 未覆盖={missed} {pct}%")
if missed != "0" or pct != "100":
    print("FAIL 覆盖率不是 100%，先修好再来更新完成度表")
    sys.exit(1)

src = AGENTS.read_text(encoding="utf-8")
before = len(src.encode("utf-8"))

# 覆盖率行
src, n1 = re.subn(
    r"\| 覆盖率（唯一有硬分母的） \| `core/kernel \+ agent \+ services\.ack_cache` 共 \*\*\d+ 语句\*\* \| \*\*\d+ / \d+ = 100%\*\*",
    f"| 覆盖率（唯一有硬分母的） | `core/kernel + agent + services.ack_cache` 共 "
    f"**{total_stmts} 语句** | **{total_stmts} / {total_stmts} = 100%**",
    src,
)
# 测试用例行
src, n2 = re.subn(
    r"\| 测试用例 \| 全量 `pytest tests` \| \*\*\d+ passed / \d+ skipped / \d+ failed\*\*",
    f"| 测试用例 | 全量 `pytest tests` | "
    f"**{passed} passed / {skipped} skipped / 0 failed**",
    src,
)
if n1 != 1 or n2 != 1:
    print(f"FAIL 替换没命中（覆盖率 {n1} 处、测试数 {n2} 处），拒绝写入")
    sys.exit(1)

AGENTS.write_text(src, encoding="utf-8")
after = len(src.encode("utf-8"))
print(f"AGENTS.md {before} -> {after} bytes（预算 65536，剩 {65536 - after}）")
