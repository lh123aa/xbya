# -*- coding: utf-8 -*-
"""把 AGENTS.md 的 §15 / §16 整段搬到 `docs/agent/p3-history.md`。

**为什么必须搬**：AGENTS.md 是工作区指令文档，有 **65536 字节**硬预算，
超了会被**静默截断**。本轮加 D19 之后文件到 66697 字节，实测**已经被截断了
1161 字节** —— 权威文档被悄悄截尾，比内容长本身危险得多。

处置方式沿用本项目已有的两次先例（§11~§14 → `phases.md`、
§17 → `f-closure.md`）：**原文一字不删，只换位置**，并在原处留交叉引用。

为什么选 §15/§16：它们体量最大（P3 的实施进度与验收关卡，约 20KB），
且**不是当前阶段的约定** —— 当前是 P5。债务表、P5 闭环段、完成度段
必须留在 AGENTS.md（G13 与审计脚本都按行匹配它们）。

本脚本逐字搬运并断言三件事：
  1. 被搬走的字节 = 原文的字节（不删字）
  2. 搬完后 AGENTS.md < 65536（留出余量）
  3. 交叉引用与索引表都已更新
"""
import io
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
AGENTS = ROOT / "AGENTS.md"
DEST = ROOT / "docs" / "agent" / "p3-history.md"

src = AGENTS.read_text(encoding="utf-8")
before = len(src.encode("utf-8"))
print(f"AGENTS.md 当前 {before} bytes（预算 65536，超 {before - 65536}）")

# ── 1. 定位 §15 与 §16（从 "## 十五、" 到 "## 十七、" 之前）──
start = src.index("## 十五、P3 实施进度")
end = src.index("## 十七、F 系列开放项收尾（摘要）")
moved = src[start:end]
assert "### 16.5 证据归档" in moved, "§16 没搬全 —— 边界找错了"
assert len(moved.encode("utf-8")) > 15000, (
    f"只搬到 {len(moved.encode('utf-8'))} 字节，比预期小太多，边界可能错了")
print(f"待搬运 §15+§16：{len(moved.encode('utf-8'))} bytes")

# ── 2. 写目标文件（开头加来源说明，正文一字不改）──
header = """# P3 阶段实施记录（原 AGENTS.md §15 / §16）

> **来源**：本文件是从 `AGENTS.md` **整段搬出来**的 §15（P3 实施进度）与
> §16（P3 验收关卡），**原文一字未改，只换了位置**。
>
> **为什么搬**：`AGENTS.md` 是工作区指令文档，有 **65536 字节**硬预算，
> 超过会被**静默截断**（实测已发生过一次：P5-B4 加完 D19 后文件到 66697 字节，
> 被截掉 1161 字节）。处置方式与本项目前两次一致：
> §11~§14 → `phases.md`、§17 → `f-closure.md`。
>
> **为什么选这两节**：它们体量最大，且**不是当前阶段的约定**（当前是 P5）。
> 债务表、P5 闭环段、完成度段必须留在 `AGENTS.md` —— G13 与收口审计脚本
> 都按行匹配它们，搬走会让自动检查失明。
>
> **编号不变**：搬出来以后仍叫 §15 / §16，全项目的交叉引用（含源码注释）
> 因此不用改 —— 与 `phases.md` 沿用 §11~§14 的做法一致。

---

"""
DEST.write_text(header + moved, encoding="utf-8")
written = DEST.read_text(encoding="utf-8")
assert moved in written, "写入后正文对不上"
print(f"已写入 {DEST.relative_to(ROOT)}（{DEST.stat().st_size} bytes，含 {len(header.encode('utf-8'))} 字节的说明头）")

# ── 3. 原处替换成交叉引用（简短，但要留住"当前阶段"这一信息）──
stub = """## 十五、P3 实施进度 · 十六、P3 验收关卡

> **这两节已整段搬到 `docs/agent/p3-history.md`**（原文一字未删，只换位置）。
> 原因同 §11~§14 → `phases.md`：`AGENTS.md` 有 **65536 字节**的工作区指令
> 硬预算，超了会被**静默截断** —— 本轮实测触发过一次（加完 D19 后 66697 字节，
> 被截掉 1161 字节）。权威文档被悄悄截尾，比内容长本身危险得多。

**P3 一句话结论**：交付了 D5 多步任务规划与长期记忆两大块；
全量回归 2139 passed；覆盖率 100%（6602 语句）；端到端 43/43。
**逐项细节、关卡表、`# pragma: no cover` 清单的两次勘误、P3 期间修掉的 6 个缺陷
（含 `EventBus.emit` 的 `source` 吞负载字段、`_extract_dest` 丢掉用户显式路径）
全部见 `docs/agent/p3-history.md`。**

> ⚠️ 注意：`# pragma: no cover` 的**当前**总数与逐处理由也维护在那里，
> 但它属**当前约定**，任何新增/删除屏蔽必须同步回本节所在的文档体系 ——
> 现在的权威口径是：**5 处，每处都必须写出结构上不可达的理由**。

"""
src2 = src[:start] + stub + src[end:]

# ── 4. 更新 §十一 的历史阶段索引表 ──
old_row = "| **P3** | 多步任务规划（D5）+ 长期记忆 | **当前阶段，见 §15 / §16** | 本文件 |"
new_row = ("| **P3** | 多步任务规划（D5）+ 长期记忆 | 覆盖率 100%（6602 语句）；"
           "2139 passed；端到端 43/43 | **`p3-history.md` §15 / §16** |")
assert old_row in src2, "索引表的 P3 行没找到"
src2 = src2.replace(old_row, new_row)
print("索引表：P3 行已指向 p3-history.md")

# 顺手把"当前阶段"指向 P5（文档版本行也一起更新）
old_ver = "**文档版本：** v3.1（F 系列开放项收尾 —— 见 §17；P3 阶段记录见 §15 / §16）"
new_ver = ("**文档版本：** v3.2（P5 完全闭环；历史阶段见 `phases.md` / "
           "`p3-history.md` / `f-closure.md` / `p4-history.md`）")
assert old_ver in src2, "文档版本行没找到"
src2 = src2.replace(old_ver, new_ver)
print("文档版本行：v3.1 → v3.2")

AGENTS.write_text(src2, encoding="utf-8")
after = len(src2.encode("utf-8"))
print(f"AGENTS.md {before} -> {after} bytes（预算 65536，余 {65536 - after}）")
assert after < 65536, "还是超预算，得再搬"
if after > 64000:
    print(f"⚠️ 余量只有 {65536 - after} 字节，下次再增内容要小心")
print("OK：已回到预算内")
