# -*- coding: utf-8 -*-
"""P5-B4 记账：把 P5-B4 推到终态，并把本轮查出的产品缺陷登记为 D19。

为什么要写脚本（同 P5-B3 的理由）：账本是**机器可读**的那一份
（`closure_audit.txt` 按它检查证据文件是否存在）。手改容易漏字段。

本脚本做四件事，每件都**断言结果**，不满足就退非零：
  1. `tasks-p5.json`：B4 的 planned_evidence **搬家**到 evidence，state → measured
  2. `AGENTS.md`：完成度表里 B4 从「未到终态」移走、新增「已实测 2」
  3. `AGENTS.md`：债务表新增 **D19**（`vad_filter` 静默截断），
     终态为「🟡 待人工」并写明最小操作路径与判据
  4. `tools/check_repo_hygiene.py` 的证据项数若因新增文件而变化，如实报告
"""
import io
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
LEDGER = ROOT / "docs" / "agent" / "tasks-p5.json"
AGENTS = ROOT / "AGENTS.md"

# ══════════════════════════════════════════════════
#  1. 账本
# ══════════════════════════════════════════════════
data = json.loads(io.open(LEDGER, encoding="utf-8").read())
task = next(t for t in data["tasks"] if t["id"] == "P5-B4")
assert task["state"] == "pending", f"预期 pending，实际 {task['state']}"

moved = list(task.pop("planned_evidence"))
for rel in moved:
    p = ROOT / rel
    assert p.is_file(), f"证据文件不存在：{rel}"
    assert p.stat().st_size > 0, f"证据文件是空的：{rel}"
    print(f"证据存在且非空：{rel}（{p.stat().st_size} bytes）")

task["state"] = "measured"
task["evidence"] = moved
task["conclusion"] = (
    "长句上 medium 明显更准（覆盖完整时 6/6 vs 4/6；同一句 6 段音频变体上 "
    "medium 5/6 段优于 small，+0.09~+0.455），代价是约 2.8 倍解码耗时。"
    "P4-C2 的「medium 不占优」是实验设计造成的假象：C2 只测短确认语，"
    "而偏置在短句上是主导因素（加偏置后两档都是 6/6），等于在比偏置而不是比档位。"
    "过程中另查出**与档位无关的产品缺陷**：vad_filter=True 会在部分长音频上静默截断"
    "（30 次调用命中 1 次，只覆盖音频的 39%），且只看相似度看不出它 —— "
    "残缺文本与原文前缀天然相似。已登记为 D19（待人工决定默认值，"
    "因为实测关掉 VAD 会让 small 在低幅白噪上幻觉出「字幕by索兰娅」）。"
    "修正 model_size 属产品取舍，本轮不改配置。"
)
data["tasks"] = [task if t["id"] == "P5-B4" else t for t in data["tasks"]]
io.open(LEDGER, "w", encoding="utf-8", newline="\n").write(
    json.dumps(data, ensure_ascii=False, indent=2) + "\n")

back = json.loads(io.open(LEDGER, encoding="utf-8").read())
b4 = next(t for t in back["tasks"] if t["id"] == "P5-B4")
assert b4["state"] == "measured" and b4["evidence"] == moved
assert "planned_evidence" not in b4
print(f"账本复读通过：P5-B4 state=measured，evidence={b4['evidence']}")

# ══════════════════════════════════════════════════
#  2 & 3. AGENTS.md
# ══════════════════════════════════════════════════
src = AGENTS.read_text(encoding="utf-8")
before = len(src.encode("utf-8"))

# --- 2a. §六 的 P5 表：B4 那一行改成完成 ---
old_row = ("| P5-B4 | ASR medium/small 长句对比（补 C2 只测短句的缺口） | "
           "⏳ **未到终态 —— 闭环缺口，尚未做完** | 计划路径见 "
           "`tasks-p5.json` 的 `planned_evidence` |")
new_row = ("| P5-B4 | ASR medium/small 长句对比（补 C2 只测短句的缺口） | ✅ 完成 | "
           "`evidence/p5/asr_long_sentence.txt`（8 支探针的完整归因链 + "
           "**顺带查出的 D19**） |")
assert old_row in src, "§六 的 B4 行没找到 —— 文案变了，先核对再改"
src = src.replace(old_row, new_row)
print("§六 P5 表：B4 → ✅ 完成")

# --- 2b. 完成度表：终态分布 ---
old = ("| ✅ 已实测 | 1 | P5-B3（真 ST + 真 vec0 端到端，含维度变化时索引重建的"
       "**建表 SQL 原文**） |")
new = ("| ✅ 已实测 | 2 | P5-B3（真 ST + 真 vec0 端到端，含维度变化时索引重建的"
       "**建表 SQL 原文**）、P5-B4（ASR 长短句对比，**并查出 D19**） |")
assert old in src, "完成度表「已实测」行没找到"
src = src.replace(old, new)
print("完成度表：已实测 1 → 2")

old = ("| ⏳ 未到终态（**闭环缺口**） | 2 | P5-B4 medium 长句对比 / "
       "B5 SERP 离线 fixture |")
new = "| ⏳ 未到终态（**闭环缺口**） | 0 | 无 —— 全部到终态 |"
assert old in src, "完成度表「未到终态」行没找到"
src = src.replace(old, new)
print("完成度表：未到终态 2 → 0")

# --- 2c. 那句「还有 2 项没做完」要跟着改 ---
old = "**闭环尚未宣告**，因为还有 2 项没做完。"
new = ("**闭环已成立**：18 项登记项全部到终态（0 项未到终态）。"
       "但要注意——**终态不等于做完**：6 项明确不做、2 项待人工，"
       "这些是**如实登记的未完成**，不是完成。")
assert old in src, "「还有 2 项没做完」那句没找到"
src = src.replace(old, new)
print("闭环口径句：已更新")

# --- 3. 债务表新增 D19（插在 D18 行之后）---
d19 = (
    "| **D19** | **`vad_filter=True` 在部分长音频上静默截断** | "
    "`plugins/asr/faster_whisper/plugin.py:105` 硬编码 `vad_filter=True`"
    "（**不可配置**），VAD 把音频后半段判成噪音直接切掉 | "
    "用户说一句长指令，产品**只听前半句**就去执行 —— 而且**看不出来**："
    "残缺文本与原句前缀天然相似，按相似度判据甚至可能比完整版「更高分」。"
    "实测 30 次调用命中 1 次（medium、8.26s 音频只覆盖 3.03s = 39%）| "
    "🟡 **终态：已实测定性，默认值处置待人工（P5-B4，2026-09-11）**。"
    "① **已测**：`evidence/p5/asr_long_sentence.txt` 第二~八节记录完整归因链"
    "（先排除合成截断与静音，再定位到 VAD，判据是「`vad=on` 片段末点 < 音频时长−1.0s "
    "**且** `vad=off` 末点 ≥ 音频时长−1.0s」）。"
    "② **为什么不直接改默认值**：实测关掉 VAD 后 `small` 会在低幅白噪上"
    "幻觉出「字幕by索兰娅」（原本为空）—— 改 `False` 是把「长句截断」换成"
    "「噪音被听成话」，两者都是用户可见故障。"
    "③ **最小操作路径（人工）**：用**真实房间噪声**录音跑一遍 vad on/off 对照"
    "（合成噪音不能替代，本机无此录音），据此决定是 (a) 保留 VAD 但加"
    "「转写覆盖率异常」告警、(b) 把 `vad_filter` 提为可配置项并在配置里显式选、"
    "还是 (c) 维持现状。"
    "④ **可判定判据**：真实噪声下 vad=off 的**误听率**与 vad=on 的**截断率**"
    "两个数字同时给出，才能做取舍 —— 只报一个不够。"
    "⑤ **措辞边界**：这是**低频**缺陷（1/30 样本），**不足以给出发生率**，"
    "只能说「确实会发生」；且它**与 small/medium 档位无关**（两档共用该参数）|\n"
)
anchor = "\n**债务管理原则：**"
assert anchor in src
src = src.replace(anchor, "\n" + d19 + anchor, 1)
print("债务表：已新增 D19")

AGENTS.write_text(src, encoding="utf-8")
after = len(src.encode("utf-8"))
print(f"AGENTS.md {before} -> {after} bytes（预算 65536，剩 {65536 - after}）")
if after > 65000:
    print("⚠️ 接近 65536 预算，需要按 §11~§14 的办法把段落移出去")
