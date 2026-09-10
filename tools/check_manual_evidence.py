"""人工验收证据校验（P4-A3）

## 它做什么 / 不做什么

**做**：检查 `docs/agent/evidence/manual/` 里，M1~M5 + G1 每一条
是否都放了证据文件、`结论.md` 里是否都填了结论。缺哪条报哪条，退出码非零。

**不做**：不替你判"通过/不通过"。判定必须是人做的 ——
这个脚本只回答"证据齐了吗、结论写了吗"，因为
"人拍了截图但忘了写结论"和"人根本没做"是两件不同的事，前者催一下就行。

## 为什么值得写

人工验收最容易烂尾的地方不是"看不懂要做什么"，而是**做到一半停了**：
证据收了三张、结论没写、也没人知道还差两条。于是"验收过了吗"这个问题永远
没人答得上来，最后不了了之。这个脚本把"烂尾"变成一条能判定的命令。

用法：
    python tools/check_manual_evidence.py            # 校验
    python tools/check_manual_evidence.py --list     # 只列出已收到的证据
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
EVIDENCE = PROJECT / "docs" / "agent" / "evidence" / "manual"
CONCLUSION = EVIDENCE / "结论.md"

#: (编号, 说明, 该条至少要有几个证据文件)
ITEMS = [
    ("M1", "搜索文件（确认语 ≤1.5s + 结果与磁盘一致）", 1),
    ("M2", "删除确认（预览只列目标文件 + 一起进了回收站）", 2),
    ("M3", "打断（停播报 + 不再播结果 + 不卡死）", 1),
    ("M4", "降级路径（闲聊不被误判成工具）", 1),
    ("M5", "安全边界（拒绝 + 理由可读 + 审计留痕）", 2),
    ("G1", "GUI 观察点（动效 / 气泡 / 自动消失 / 大致的同步）", 1),
]

#: 判定词。分两类，**这个区分是必须的**：
#:   · `DONE_WORDS` = 真的做了判定（"不通过"也算 —— 本脚本查的是"做完没有"，
#:     不是"通过没有"。一条诚实的"不通过"是完整结论，不是缺失）
#:   · `NOT_DONE_WORDS` = 还没做（模板里就写 `未做`，所以**照抄模板等于没填**）
#: 顺序上长的在前，否则"不通过"会被"通过"吃掉半截。
DONE_WORDS = ("不通过", "部分通过", "通过")
NOT_DONE_WORDS = ("未做",)
VERDICT_WORDS = DONE_WORDS + NOT_DONE_WORDS

#: 占位/假证据的识别词。**为什么需要这一条**：
#: 写这个脚本时我自己就造了一批假证据来验证它（否则没法证明它不是空转），
#: 造完差点忘删 —— 而"目录里躺着一批看起来齐全的假证据"正是最危险的状态：
#: 谁跑一下校验都得到绿色，于是没有人再去做真验收。
#: 所以：占位内容一律报红，且**不计入证据数**。
PLACEHOLDER_MARKERS = ("假证据", "占位", "self-test", "selftest", "placeholder", "TODO")

#: 小于这个字节数的证据文件会被提醒"可能是空文件/占位"
SUSPICIOUS_BYTES = 200


def main() -> int:
    ap = argparse.ArgumentParser(description="人工验收证据校验（P4-A3）")
    ap.add_argument("--list", action="store_true", help="只列出已收到的证据文件")
    args = ap.parse_args()

    print("=" * 72)
    print("人工验收证据校验（P4-A3）")
    print("=" * 72)
    print(f"证据目录：{EVIDENCE.relative_to(PROJECT)}")
    print("提醒：本脚本只查「有没有」，不替你判「过不过」—— 判定权在人。")
    print()

    if not EVIDENCE.exists():
        print(f"[FAIL] 证据目录不存在：{EVIDENCE}")
        return 1

    files = [p for p in EVIDENCE.iterdir() if p.is_file()]
    files = [p for p in files if p.name not in ("README.md", "结论.md")
             and not p.name.startswith(".")]

    # ── 剔除占位/假证据（见 PLACEHOLDER_MARKERS 的说明）──
    placeholders, keep = [], []
    for p in files:
        head = ""
        if p.suffix.lower() in (".txt", ".md", ".json", ".log"):
            try:
                head = p.read_text(encoding="utf-8", errors="replace")[:2000]
            except OSError:                          # pragma: no cover - 读不动就当空
                head = ""
        low = (p.name + " " + head).lower()
        if any(m.lower() in low for m in PLACEHOLDER_MARKERS) or p.stat().st_size < SUSPICIOUS_BYTES:
            placeholders.append(p)
        else:
            keep.append(p)
    files = keep

    if args.list:
        if not files and not placeholders:
            print("（还没有任何证据文件）")
        for p in sorted(files):
            print(f"  {p.name}  ({p.stat().st_size} B)")
        for p in sorted(placeholders):
            print(f"  [占位?] {p.name}  ({p.stat().st_size} B) —— 不计入证据")
        return 0

    if placeholders:
        print("以下文件看起来是**占位/自检**内容，不计入证据（请删掉或换成真实证据）：")
        for p in sorted(placeholders):
            print(f"  · {p.name} ({p.stat().st_size} B)")
        print()

    # ── 逐条检查证据文件 ──
    verdict_text = CONCLUSION.read_text(encoding="utf-8") if CONCLUSION.exists() else ""
    missing = []
    for code, desc, need in ITEMS:
        hits = [p for p in files if p.name.startswith(code)]
        got = len(hits)
        ok_file = got >= need
        # 结论行：出现编号 + 一个**真的判定**（不是"未做"、不是占位/示例）。
        # 最后那两条排除是实测补上的：
        #   ① 我自检时往 结论.md 写过 `M1 搜索文件 | 通过 | 自检`，
        #      它一开始被当成"已填结论" —— 假结论能把校验刷成绿色；
        #   ② 模板里就写着 `未做`，若不排除，照抄模板提交上来也是"全绿"。
        line_re = re.compile(rf"^\s*{code}\b.*?({'|'.join(DONE_WORDS)})", re.MULTILINE)
        has_line = False
        for m in line_re.finditer(verdict_text):
            body = m.group(0)
            if any(k.lower() in body.lower() for k in PLACEHOLDER_MARKERS):
                continue
            if any(k in body for k in NOT_DONE_WORDS):
                continue
            has_line = True
            break
        mark = "OK  " if (ok_file and has_line) else "缺  "
        detail = f"证据 {got}/{need}"
        if not has_line:
            detail += "，结论未填（或只是占位/示例）"
        print(f"[{mark}] {code} {desc}\n         {detail}"
              + (f"，文件：{[p.name for p in hits]}" if hits else ""))
        if not (ok_file and has_line):
            missing.append(code)

    print()
    print("-" * 72)
    if missing:
        print(f"还差 {len(missing)} 条：{', '.join(missing)}")
        print()
        print("怎么补：")
        print("  1. 照 docs/agent/manual-acceptance.md 做对应场景")
        print("  2. 证据放进本目录，文件名以编号开头（如 M2-删除-预览气泡.png）")
        print("  3. 在 结论.md 里为它补一行：`M2 ... | 通过 | 说明`")
        print()
        print("⚠️ 本包未完成前，不得宣称产品可用（麦克风采集 / GUI 动效 / 人耳听感"
              "只有人能判）。")
        print("=" * 72)
        return 1

    print("全部 6 条证据与结论齐备 ✅")
    print("→ 记得把结论回写 AGENTS.md §17 与 docs/agent/p4-plan.md（P4-A3）")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
