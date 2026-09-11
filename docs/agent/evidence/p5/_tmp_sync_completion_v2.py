# -*- coding: utf-8 -*-
"""同步 AGENTS.md 完成度表：让它与 `tasks-p5.json` 及 D20 一致。

为什么要脚本：这张表已经**两次**与账本脱节过（P5-B5 那次、以及"已实测 2 应为 3"）。
两张表放在两个地方，靠人记得对齐一定会再错 —— 所以改动一律走脚本 + 断言。

本次要改三处：
  1. `已实测 1 → 3`（B3 / B4 / B5），并更新具体项列表
  2. 新增一栏说明**债务角度的终态**（D14 明确不做、D16~D18/D20 已修、D19 待人工），
     因为 D19/D20 是本轮新登记的，完成度表里看不到它们
  3. G8 的口径脚注：加上"配额导致的跳过已由 D20 正名"这一条
"""
import io
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
AGENTS = ROOT / "AGENTS.md"
LEDGER = ROOT / "docs" / "agent" / "tasks-p5.json"

# ── 先从账本里数，不信记忆 ──
data = json.loads(io.open(LEDGER, encoding="utf-8").read())
states = {}
for t in data["tasks"]:
    states.setdefault(t["state"], []).append(t["id"])
label = {"fixed": "✅ 已修", "done": "✅ 已完成", "measured": "✅ 已实测",
         "wont_do": "⬜ 明确不做", "human": "🟡 待人工", "pending": "⏳ 未到终态"}
print("账本终态分布：")
for k in ("fixed", "done", "measured", "wont_do", "human", "pending"):
    if k in states:
        print(f"  {label[k]:10s} {len(states[k]):2d}  {states[k]}")
assert "pending" not in states, "还有 pending，先处理"

src = AGENTS.read_text(encoding="utf-8")
before = len(src.encode("utf-8"))

# ── 1. 「已实测」那一行 ──
old_measured = re.search(r"\| ✅ 已实测 \| \d+ \|[^\n]*\n", src)
assert old_measured, "找不到「已实测」行"
new_measured = (
    f"| ✅ 已实测 | {len(states.get('measured', []))} | "
    "P5-B3（真 ST + 真 vec0 端到端，含维度变化时索引重建的**建表 SQL 原文**）、"
    "P5-B4（ASR 长短句对比，**并查出 D19**）、"
    "P5-B5（8 份真实 SERP 原文做成离线 fixture，含 MD5 判据与**版本控制层面的字节保全**） |\n"
)
src = src[:old_measured.start()] + new_measured + src[old_measured.end():]
print(f"「已实测」行：{old_measured.group(0).strip()[:40]}… → {len(states.get('measured', []))} 项")

# ── 2. 新增「债务角度的终态」小节（放在终态表之后）──
anchor = "> **上表的数字是审计脚本数出来的，不是我数出来的**"
assert anchor in src
extra = """> **债务表也要连口径**（上面那张表只覆盖 P5 的 18 项登记项；
> 技术债是另一条线，容易漏读）：
>
> | 债务 | 终态 |
> |------|------|
> | D1~D15（除 D3 / D7 / D14） | ✅ 已偿还 |
> | D3 单用户假设 / D7 间歇崩溃 / D14 双 EventBus | ⬜ 明确不做或已有效消除（见各行） |
> | D16 形制归一 / D17 计划参数校验 / D18 非字符串 targets | ✅ 已修 |
> | **D19 `vad_filter` 静默截断** | 🟡 **待人工**（默认值取舍需真实噪声录音） |
> | **D20 验收脚本把配额报成产品失败** | ✅ **已修**（P5-C1 轮次发现的） |
>
> ⚠️ D19 / D20 都是**本轮执行中查出来的**，不在最初 18 项里 ——
> 这说明"闭环"之后仍可能有新问题，登记表要一直能长大。

"""
src = src.replace(anchor, extra + anchor, 1)
print("债务终态小节：已插入")

# ── 3. G8 口径脚注 ──
old_g8 = "| G8/G9/G10 退出码 0 | 依赖免费配额与第三方站点；G8 配额耗尽时会**报跳过**，报告单独标出 |"
assert old_g8 in src
new_g8 = (old_g8[:-2] +
          "；G8 的配额跳过原先还会被**误报成 2 个 FAIL**，已由 D20 修好 |")
src = src.replace(old_g8, new_g8)
print("G8 口径脚注：已更新")

AGENTS.write_text(src, encoding="utf-8")
after = len(src.encode("utf-8"))
print(f"AGENTS.md {before} -> {after} bytes（预算 65536，余 {65536 - after}）")
assert after < 65536, "超预算了"
assert "D20 验收脚本把配额报成产品失败" in AGENTS.read_text(encoding="utf-8")
print("OK：完成度表与账本、D19/D20 已对齐")
