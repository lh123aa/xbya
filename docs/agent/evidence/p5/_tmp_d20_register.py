# -*- coding: utf-8 -*-
"""把本轮查出的**验收脚本缺陷**登记为 D20（状态：已修）。

为什么要登记而不是"改完就算"：本项目 §六 的原则是"每项债务必须登记，
不允许隐性债务"。这条缺陷的性质是**验收脚本替自己做不成立的声明** ——
与 P4 那 7 处「检查本身出错」同族，属于必须留痕、以后能复查的那类。

与 D16/D17/D18 一样：一行写完「问题 / 影响 / 终态证据」，
终态给「已修」就必须有**代码改动 + 用例 + 过门禁**。
"""
import io
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
AGENTS = ROOT / "AGENTS.md"

src = AGENTS.read_text(encoding="utf-8")
before = len(src.encode("utf-8"))

row = (
    "| **D20** | **验收脚本把「外部配额用尽」报成「产品的 function calling 失败」** | "
    "`tools/verify_f3_real_llm.py` 的 `[F3-a]` 段两条工具调用断言"
    "（「真实函数调用返回了工具名」「选中的工具在 schema 清单内」）**没有查配额观察器**，"
    "429 一律记 FAIL；而同文件的规划器那条早就用 `skipped()` 处置过 | "
    "免费档 8000 TPM 用尽时，G8 会报 **2 个 FAIL**，读起来是「function calling 坏了」——"
    "而真相是「模型根本没被问到」。更糟的是同一次运行的汇总还印着"
    "「凡受此影响的断言都已归入 [SKIP]，**没有**被算成通过」——"
    "**验收脚本替自己做了一句不成立的声明**（该次明明有 2 个 FAIL）。"
    "与 P4 那 7 处「检查本身出错」同族，也属「纸面满足」 | "
    "✅ **已修（P5-C1 轮次，2026-09-11）**：调用前后各记一次 `len(quota.hits)`，"
    "只有「返回 `None` **且**这次调用期间真的抓到 429」才降级为 `skipped()`。"
    "**反方向核查过不是空转**：① `_QuotaWatch` 行为逐条核对 **7/7**"
    "（只认 429/限流/配额，**401/403 不认** —— 否则「key 失效」会被悄悄跳过，比原 bug 更坏）；"
    "② 真值表四行证明 `probe is None` 但无 429 时**仍报 FAIL**，没有把所有失败都放过；"
    "③ 现场对照：同代码同脚本，配额用尽时 `16/18（2 FAIL）`，"
    "配额恢复后一字未改重跑 `17/17（0 FAIL, 1 SKIP）`。"
    "证据 `docs/agent/evidence/p5/g8_quota_skip.txt`。"
    "**已知局限（未修）**：`_QuotaWatch` 靠**日志文本**匹配，厂商改文案就会失明；"
    "结构性修法是让插件把状态码作为字段往上抛，改动面更大 |\n"
)

anchor = "\n**债务管理原则：**"
assert anchor in src, "找不到债务表末尾锚点"
assert "**D20**" not in src, "D20 已经登记过了（不重复登记）"
src = src.replace(anchor, "\n" + row + anchor, 1)

AGENTS.write_text(src, encoding="utf-8")
after = len(src.encode("utf-8"))
print(f"AGENTS.md {before} -> {after} bytes（预算 65536，余 {65536 - after}）")
assert after < 65536, "超预算了，先搬运再登记"
assert "**D20**" in AGENTS.read_text(encoding="utf-8")
print("D20 已登记（状态：已修）")
