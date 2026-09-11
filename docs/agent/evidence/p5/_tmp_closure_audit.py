# -*- coding: utf-8 -*-
"""收口审计脚本（P5-AUDIT-CLOSURE）

产出 `docs/agent/evidence/p5/closure_audit.txt`：把"闭环"这件事做成**可逐条核对**的，
而不是一句结论。它做四件事：

1. 逐条列出 `tasks-p5.json` 里每个任务的终态 + 证据（含"证据是否真实存在"）；
2. 把终态分布数出来（四种终态，没有第五种）；
3. 复跑 G13，把逐条输出写进证据（项数从输出里解析，不手写）；
4. 扫一遍文档里是否还残留"以后再说 / 待定 / 后续再看"这类**非终态**措辞。

用法：python docs/agent/evidence/p5/_tmp_closure_audit.py
"""
import json
import re
import subprocess
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(r"E:\程序\桌面宠物\xiaoyi-vrm-worktree")
OUT = ROOT / "docs" / "agent" / "evidence" / "p5" / "closure_audit.txt"
TASKS = ROOT / "docs" / "agent" / "tasks-p5.json"

STATE_LABEL = {
    "fixed": "✅ 已修",
    "measured": "✅ 已实测",
    "done": "✅ 已完成",
    "wont_do": "⬜ 明确不做",
    "human": "🟡 待人工",
    "pending": "⏳ 未到终态",
}

#: 非终态措辞（R1 风险：为了让"闭环"好看而把未验证项写成已闭环；
#: 反面同样危险 —— 用"以后再说"把没做的事糊过去）
BANNED = ["以后再说", "待定", "后续再看", "以后再补", "暂不处理", "有时间再说"]

lines = []
w = lines.append

w("# P5 收口审计（P5-AUDIT-CLOSURE）")
w("")
w(f"生成时间：{datetime.now():%Y-%m-%d %H:%M:%S}")
w("")
w("> 本文件是**可复跑**的：`python docs/agent/evidence/p5/_tmp_closure_audit.py`")
w("> 它不产生结论，只逐条把事实列出来 —— 结论要么被这些行支持，要么不成立。")
w("")

data = json.loads(TASKS.read_text(encoding="utf-8"))
tasks = data.get("tasks") or []

w("## 一、逐条终态与证据")
w("")
w("| 编号 | 终态 | 证据（已留档，逐条核对是否存在） | 计划证据（未留档，不查） |")
w("|------|------|-----------------------------------|--------------------------|")
dist = Counter()
missing = []
for t in tasks:
    state = t.get("state", "?")
    dist[STATE_LABEL.get(state, state)] += 1
    ev = t.get("evidence") or []
    marks = []
    for one in ev:
        ok = (ROOT / one).exists()
        marks.append(("✔ " if ok else "✘ ") + f"`{one}`")
        if not ok:
            missing.append(f"{t.get('id')}→{one}")
    planned = t.get("planned_evidence") or []
    w(f"| {t.get('id')} | {STATE_LABEL.get(state, state)} | "
      f"{'<br>'.join(marks) if marks else '（无）'} | "
      f"{'<br>'.join('`' + p + '`' for p in planned) if planned else '（无）'} |")
w("")

w("## 二、终态分布（四种终态，没有第五种）")
w("")
w("| 终态 | 项数 |")
w("|------|------|")
for label, n in sorted(dist.items(), key=lambda kv: -kv[1]):
    w(f"| {label} | {n} |")
w(f"| **合计** | **{len(tasks)}** |")
w("")
w(f"已留档证据缺失数：**{len(missing)}**"
  + (f" —— {missing}" if missing else "（全部存在）"))
w("")

w("## 三、非终态措辞扫描（正反两个方向都要防）")
w("")
# 扫描范围**自动发现**，不写死文件名 —— 写死过一次（P5-AUDIT 的 G13 同类问题）：
# 原文是 5 个硬编码路径，本轮把 §15/§16 搬到 `p3-history.md` 之后，
# 那份新文档就**悄悄不被扫**了。凡是"审计范围"这种清单，都要能自己长大。
docs = [p.relative_to(ROOT).as_posix()
        for p in sorted((ROOT / "docs" / "agent").glob("*.md"))
        if p.is_file()]
docs = ["AGENTS.md"] + docs
w(f"扫的文件（**自动发现** `AGENTS.md` + `docs/agent/*.md`，共 {len(docs)} 份）：")
w("")
for d in docs:
    w(f"- `{d}`")
w("")
for bad in BANNED:
    hits = []
    for d in docs:
        p = ROOT / d
        if not p.exists():
            continue
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if bad in line:
                hits.append(f"{d}:{i}")
    mark = "✔ 无" if not hits else "✘ 有：" + ", ".join(hits)
    w(f"- 「{bad}」 → {mark}")
w("")

w("## 四、门禁原样复跑")
w("")

# 1) G13
#
# 标题里的项数**从输出里读**，不手写：这里原先写死「20 项」，
# 而 G13 本轮已经涨到 22 项 —— 一个专门做"逐条核对"的脚本，
# 自己的小标题却在手抄数字，与它要防的那类事同族。
w("### G13 仓库卫生（项数从输出里读出来，不手写）")
w("")
w("```")
for script in ("tools/check_repo_hygiene.py",):
    proc = subprocess.run([sys.executable, script], cwd=ROOT, capture_output=True,
                          text=True, encoding="utf-8", errors="replace")
    out = (proc.stdout or "").rstrip()
    w(out)
    w(f"[exit] {proc.returncode}")
    m = re.search(r"仓库卫生自检：(\d+)/(\d+)", out)
    if m:
        w(f"[项数] {m.group(2)} 项（本行由脚本从上面那段输出里解析，不是手抄）")
w("```")
w("")

# 2) 测试数与覆盖率口径（不重跑全量：那是 G2/G3 的事，这里只读最近一次留档）
g2 = ROOT / "docs" / "agent" / "evidence" / "p5" / "g2_g3_tests_coverage.txt"
if g2.exists():
    tail = [ln for ln in g2.read_text(encoding="utf-8", errors="replace").splitlines()
            if re.search(r"passed|^TOTAL", ln)]
    w("### 全量回归 + 覆盖率（读最近一次留档 g2_g3_tests_coverage.txt）")
    w("")
    w("```")
    for ln in tail:
        w(ln.rstrip())
    w("```")
w("")

OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
print(f"写入 {OUT}（{OUT.stat().st_size} bytes）")
print(f"任务 {len(tasks)} 项；证据缺失 {len(missing)} 条")
for label, n in sorted(dist.items(), key=lambda kv: -kv[1]):
    print(f"  {label}: {n}")
