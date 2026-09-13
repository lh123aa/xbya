# -*- coding: utf-8 -*-
"""把 AGENTS.md 的「P4 立项与进度」整段换成紧凑版（内容已移入 docs/agent/p4-history.md）

为什么用脚本而不是手改：这一段有 ~3.3KB，手改容易漏行；
脚本按**标记行**切片、打印改前改后字节数、并在找不到标记时直接失败（不静默跳过）。
"""
import io
import sys
from pathlib import Path

ROOT = Path(r"E:\程序\桌面宠物\xbya-vrm-worktree")
AGENTS = ROOT / "AGENTS.md"
HISTORY = ROOT / "docs" / "agent" / "p4-history.md"

text = AGENTS.read_text(encoding="utf-8")
before = len(text.encode("utf-8"))

START = "### P4 立项与进度（2026-09-10 立项 / 2026-09-11 P0 交付）"
END = "> 唯一能分开它们的手段还是那条老办法：**反方向输入 + 可复跑的检查**。\n\n---\n"

i = text.find(START)
j = text.find(END)
if i < 0 or j < 0 or j < i:
    print(f"FAIL 找不到切片标记 start={i} end={j}")
    sys.exit(1)
j += len(END)

old = text[i:j]
assert "P4-A3" in old and "纸面满足" in old, "切片内容不对，拒绝执行"
# 内容确实已经在 p4-history.md 里（防止"移出去但没落地"）
hist = HISTORY.read_text(encoding="utf-8")
for probe in ("P4-A3", "检查本身出错", "纸面满足", "假绿/假红"):
    assert probe in hist, f"p4-history.md 缺 {probe} —— 拒绝删除原文"
hist_ok = ("interrupt_speech" in hist) and ("T2S_CHARS" in hist)
assert hist_ok, "p4-history.md 缺验收轮 4 条明细"

NEW = """### P5 完全闭环（2026-09-11 立项）· P4 历史见 `docs/agent/p4-history.md`

> P4 的立项背景、**7 处"检查本身出错"**、**3 处"纸面满足"**、验收轮的
> **4 个假绿/假红（19~22）** 明细已移到 **`docs/agent/p4-history.md`**
> （原因同 §11~§14 → `phases.md`：权威文档有 65536 字节预算，超了会**静默截断**）。

**P5 的目标不是"100% 完成"，而是"每条登记项都有终态"** —— 四种，没有第五种：

| 终态 | 判定要求 |
|------|---------|
| ✅ 已修 | 有代码改动 + 有用例 + 过门禁 |
| ✅ 已实测 | 有原始输出留档，结论明确（**允许结论是"不可为"**） |
| ⬜ 明确不做 | 写明理由，且理由能被反驳 |
| 🟡 待人工 | 附**最小操作路径** + **可判定判据** |

计划 `docs/agent/p5-closure-plan.md`；任务 `docs/agent/tasks-p5.json`（17 项，P0 = 3 项）。

| 编号 | 项 | 状态 | 证据 |
|------|----|------|------|
| P5-A1 | 账本不再说谎（`f-closure.md` §17.4 的 7 处 + 完成度连口径） | ✅ 完成 | `f-closure.md` §17.4.1 复核段；本节"完成度" |
| P5-A2 | D17 计划参数类型校验（+ 顺带修掉 D18） | ✅ 完成 | `evidence/p5/d17_plan_param_validation.txt`、`d18_items_probe.txt` |
| P5-A3 | D14 适配层终态登记（明确不做 + 3 条理由） | ✅ 完成 | 债务表 D14 行 |
| P5-B1~B5 | D16 守护 / voice_service 传参 / ST+vec0 / medium 长句 / SERP fixture | ⏳ 待做 | `evidence/p5/` |
| P5-C1~C2 | 轮换 key / 人工验收 M1~M5 | 🟡 待人工 | 见 §六 末"能力边界" |
| P5-C3~C8 | 6 项明确不做（D3 / 天气 IP / D7 / 繁简边界 / 诱饵页 / 历史账本） | ⬜ 已登记 | 各附可反驳理由 |

**口径说明（不许把 13 关整体当"工程完成度"）**：`run_acceptance.py` 的 13 关里，
G1~G7 + G13 是内部关卡（7 项），G8~G12 是外部依赖关卡（6 项，退出码 0 但 G8
可能因配额报"跳过"——按设计 G8 有跳过时**不算通过**）；覆盖率的"100%"只覆盖
`core/kernel + agent + services.ack_cache`（**7238 语句**），`ui/` / `plugins/` /
`core/app.py` / 大部分 `services/` **不在口径内**。任何"工程 X%"的说法都必须带上分母。

---

"""

text = text[:i] + NEW + text[j:]
AGENTS.write_text(text, encoding="utf-8")
after = len(text.encode("utf-8"))
print(f"AGENTS.md: {before} -> {after} bytes (delta {after - before:+d})")
print(f"预算 65536 剩余：{65536 - after}")
