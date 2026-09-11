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
#:
#: ⚠️ **勘误（P4 收口轮）**：上一条注释写着"`M1 ... | 通过 | 自检` 一开始被当成已填结论"，
#: 读起来像"已经修好了"。但审计发现「自检」**根本不在下面这个元组里** ——
#: 也就是说那条假结论**至今照样会被算成已填**（实测：匹配到 `M1 搜索文件 | 通过`，
#: 占位词一个都不命中 ⇒ 判为已填）。文档承诺与代码行为不一致，属于"纸面修好了"。
#: 现已补上「自检/示例/样例/模板」，并用 `--self-test` 把这条钉住。
PLACEHOLDER_MARKERS = ("假证据", "占位", "self-test", "selftest", "placeholder", "TODO",
                       "自检", "示例", "样例", "模板")

#: 小于这个字节数的证据文件会被提醒"可能是空文件/占位"
SUSPICIOUS_BYTES = 200


def _is_placeholder(name: str, head: str, size: int) -> bool:
    """这个文件算不算"占位/假证据"？（按文件名 + 开头内容 + 体积判断）

    抽成函数是为了能被 `--self-test` 直接验证 —— 否则"它会报红"这句话没法复现。
    """
    low = (name + " " + head).lower()
    return (any(m.lower() in low for m in PLACEHOLDER_MARKERS)
            or size < SUSPICIOUS_BYTES)


def _has_real_verdict(code: str, text: str) -> bool:
    """`结论.md` 里这一条是否已经写了**真的判定**（而不是"未做"/占位/示例）

    两种**不算**的情况，都是实测踩出来的：
      ① 带占位词的行（`| 通过 | 自检`、`| 通过 | 示例`）—— 假结论能把校验刷绿；
      ② 模板原样保留的 `未做` —— 照抄模板等于没填。
    注意 `不通过` 算**已填**：本脚本查的是"做完没有"，一条诚实的"不通过"是完整结论。

    ⚠️ **为什么按"整行"判，而不是用一条正则去匹配"编号…判定词"**：
    原先的实现是 `^\\s*M1\\b.*?(不通过|部分通过|通过)`，`.*?` 是**非贪婪**的，
    所以匹配到的 `body` 只到 `通过` 为止 —— `| 自检` 根本不在 `body` 里，
    占位词检查**永远看不到它**。
    后果：文档里写着"`M1 ... | 通过 | 自检` 这个假结论已经修掉了"，
    实际上它**至今照样被算成已填**（`--self-test` 一跑就露：判定=True、期望=False）。
    也就是说"修的方式"本身是坏的 —— 这是本轮自检抓到的第 3 个同类问题。
    现在改成：取出整行 → 编号开头且后面不是字母数字 → 整行查判定词与占位词。
    """
    for raw in text.splitlines():
        line = raw.strip()
        if not line.startswith(code):
            continue
        rest = line[len(code):]
        # 避免 `M1` 匹配到 `M10`
        if rest[:1].isalnum() or rest[:1] == "_":
            continue
        if not any(w in line for w in DONE_WORDS):
            continue
        if any(k in line for k in NOT_DONE_WORDS):
            continue
        if any(k.lower() in line.lower() for k in PLACEHOLDER_MARKERS):
            continue
        return True
    return False


def self_test() -> int:
    """反方向验证：证明这个校验器**既会报红、也不乱报红**

    为什么不造真文件来试：第一版就是这么干的，结果一批假证据躺在**真证据目录**里，
    差点让任何人跑一下校验都拿到绿色。所以自检只跑**纯函数判定**，不碰真实目录。
    """
    print("=" * 72)
    print("自检：判定逻辑两个方向都要成立")
    print("=" * 72)

    verdict_cases = [
        # (说明, 结论文本, 期望判定)
        ("真人写了通过", "M1 搜索文件 | 通过 | 现象：1.2s 出结果", True),
        ("真人写了不通过（诚实的失败也是完整结论）", "M1 搜索文件 | 不通过 | 3.4s", True),
        ("部分通过", "M1 搜索文件 | 部分通过 | 有时超时", True),
        ("模板原样：未做", "M1 搜索文件 | 未做 | ", False),
        ("★ 假结论 + 自检标记", "M1 搜索文件 | 通过 | 自检", False),
        ("★ 假结论 + 示例标记", "M1 搜索文件 | 通过 | 示例", False),
        ("★ 假结论 + 模板标记", "M1 搜索文件 | 通过 | 模板", False),
        ("根本没有这一行", "M2 删除确认 | 未做 | ", False),
        ("空文本", "", False),
        ("只有编号没有判定词", "M1 搜索文件 | 现象写这里 | ", False),
        ("占位词在别的编号行上，不应误伤本行", "M1 搜索文件 | 通过 | 真实截图\nG1 | 通过 | 示例", True),
    ]
    bad = 0
    for label, text, expect in verdict_cases:
        got = _has_real_verdict("M1", text) if "M1" in text else _has_real_verdict("M1", text)
        ok = got is expect
        if not ok:
            bad += 1
        print(f"  {'PASS' if ok else 'FAIL'}  {label:<42} 判定={got} 期望={expect}")

    file_cases = [
        # (说明, 文件名, 开头内容, 体积, 期望"是占位")
        ("正常证据文件", "M1-搜索气泡.png", "", 5000, False),
        ("文件名带假证据", "M2-假证据.txt", "x" * 300, 300, True),
        ("内容带占位", "M3-打断.txt", "这是占位内容", 300, True),
        ("内容带 TODO", "M4-降级.txt", "TODO 待补", 300, True),
        ("体积过小（疑似空文件）", "M5-边界.txt", "ok", 50, True),
    ]
    for label, name, head, size, expect in file_cases:
        got = _is_placeholder(name, head, size)
        ok = got is expect
        if not ok:
            bad += 1
        print(f"  {'PASS' if ok else 'FAIL'}  {label:<42} 占位={got} 期望={expect}")

    print("-" * 72)
    total = len(verdict_cases) + len(file_cases)
    if bad:
        print(f"自检失败：{total - bad}/{total} 通过，{bad} 项不符")
        return 1
    print(f"自检通过：{total}/{total} —— 会报红，也不乱报红")
    print("=" * 72)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="人工验收证据校验（P4-A3）")
    ap.add_argument("--list", action="store_true", help="只列出已收到的证据文件")
    ap.add_argument("--self-test", action="store_true",
                    help="反方向验证判定逻辑（不碰真实证据目录，可随时复跑）")
    args = ap.parse_args()

    if args.self_test:
        return self_test()

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
        if _is_placeholder(p.name, head, p.stat().st_size):
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
        # 判定逻辑抽到 `_has_real_verdict()`，这样 `--self-test` 验证的
        # 就是**真正在用的那段代码**，而不是另写一份（本项目踩过"探针自己复刻一份
        # 判据"的亏：复刻的那份会与产品漂移，于是报出产品已不会犯的错）。
        has_line = _has_real_verdict(code, verdict_text)
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
