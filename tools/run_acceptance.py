"""验收执行器：一条命令跑完全部验收关卡，并生成汇总报告

## 为什么需要它

本轮的验收关卡分散在 6 个脚本里（单元测试 / 覆盖率 / 端到端 smoke / 真实 LLM /
真实网络与真机调用 / 真实 GUI / 语音回环）。逐个手敲容易漏、也容易被"挑着跑"。
这个执行器把它们**固定成大表**，原始输出一律落盘到
`docs/agent/evidence/acceptance/`，并在结尾生成 `SUMMARY.md`。

## 两类关卡，结论不能混

- **内部关卡**（`external=False`）：结论只取决于本仓库的代码。它们失败 = 验收不通过。
- **外部关卡**（`external=True`）：依赖第三方（LLM 服务、搜索引擎、TTS/ASR 模型、
  桌面弹窗）。它们的失败**可能是外部原因**（配额 429、服务端慢、引擎反爬），
  所以单独计数、单独打印，**不计入失败的内部关卡**，但也**绝不写成通过**。

用法：
    python tools/run_acceptance.py                # 全部跑
    python tools/run_acceptance.py --skip-external
    python tools/run_acceptance.py --only G2 G3
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "agent" / "evidence" / "acceptance"

#: (关卡, 名称, 命令, 从输出里提取结论的正则, 是否依赖外部)
GATES: list[tuple[str, str, list[str], str, bool]] = [
    ("G1", "语法检查（全部改动模块）",
     [sys.executable, "-m", "py_compile",
      "agent/tools/browser_tools.py", "agent/tools/productivity_tools.py",
      "agent/tools/file_tools.py", "agent/providers/planner/llm_planner.py",
      "agent/providers/productivity/llm_translate.py",
      "agent/providers/productivity/open_meteo.py",
      "agent/providers/productivity/reminder_scheduler.py",
      "agent/plugins/productivity_providers_plugin.py",
      "agent/plugins/productivity_tools_plugin.py",
      "agent/seams/planner.py", "agent/providers/safety/basic_guard.py",
      "core/app.py", "core/kernel/events.py", "ui/pet_window.py",
      "plugins/llm/openrouter/plugin.py",
      "tools/verify_gui_launch.py"],
     r"(?m)^$", False),
    ("G2", "全量回归",
     [sys.executable, "-m", "pytest", "tests/", "-q"],
     r"(\d+ passed[^\n]*)", False),
    ("G3", "覆盖率（项目口径 100%）",
     [sys.executable, "-m", "pytest", "tests/agent", "tests/kernel", "-q",
      "--cov=core.kernel", "--cov=agent", "--cov=services.ack_cache",
      "--cov-report=term-missing"],
     r"(TOTAL\s+\d+\s+\d+\s+\d+%)", False),
    ("G4", "P1 端到端验收",
     [sys.executable, "tools/p1_acceptance_smoke.py"],
     r"(验收结果[^\n]*)", False),
    ("G5", "P3 端到端验收",
     [sys.executable, "tools/p3_acceptance_smoke.py"],
     r"(验收结果[^\n]*)", False),
    ("G6", "指标测量",
     [sys.executable, "tools/measure_acceptance_metrics.py"],
     r"(通过[^\n]*)", True),
    ("G7", "G0 内核示例可运行",
     [sys.executable, "docs/agent/examples/kernel_demo.py"],
     r"(kernel_demo:[^\n]*)", False),
    ("G8", "F3 真实 LLM 全链路",
     [sys.executable, "tools/verify_f3_real_llm.py"],
     r"(F3 结果[^\n]*)", True),
    ("G9", "F4/F5 真实网络 + 真机系统调用",
     [sys.executable, "tools/verify_f4_f5_real.py"],
     r"(F4/F5 结果[^\n]*)", True),
    ("G10", "F7 真实生产力服务",
     [sys.executable, "tools/verify_f7_real_services.py"],
     r"(F7 结果[^\n]*)", True),
    ("G11", "真实 GUI 启动与优雅退出",
     [sys.executable, "tools/verify_gui_launch.py"],
     r"(GUI 结果[^\n]*)", True),
    ("G12", "语音 5 场景回环（TTS→ASR→管线）",
     [sys.executable, "tools/voice_scenarios_loopback.py", "--model", "small"],
     r"(\d+\s*/\s*\d+\s*通过[^\n]*)", True),
]


def run_gate(gid, name, cmd, pattern, external, timeout) -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    log = OUT / f"{gid}.txt"
    t0 = time.perf_counter()
    try:
        proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True,
                              text=True, encoding="utf-8", errors="replace",
                              timeout=timeout, check=False)
        code, body = proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except subprocess.TimeoutExpired:
        code, body = None, f"[超时] 超过 {timeout}s 未结束"
    dt = time.perf_counter() - t0

    body = re.sub(r"\x1b\[[0-9;]*m", "", body)
    body = re.sub(r"(gsk_|sk-or-v1-)[A-Za-z0-9\-]+", r"\1<REDACTED>", body)
    header = (f"# 关卡: {gid} {name}\n# 命令: {' '.join(cmd)}\n"
              f"# 用时: {dt:.1f}s\n# 退出码: {code}\n"
              f"# 外部依赖: {'是' if external else '否'}\n" + "-" * 72 + "\n")
    log.write_text(header + body, encoding="utf-8")

    hits = re.findall(pattern, body)
    detail = hits[-1].strip() if hits else ""
    if not detail and code == 0:
        detail = "（无结论行，退出码 0）"
    # 结论行里的"跳过 N 项"要单独拎出来：某些验收脚本（F3 真实 LLM）
    # 会在**外部依赖确实不可用**时把断言记为 SKIP（既不算通过也不算失败）。
    # 那种情况下 exit code 是 0，但"这一轮其实没验到"必须显式可见 ——
    # 否则"关卡通过"会被读成"这些项验过了"，那是另一种假绿。
    sk = re.search(r"(\d+)\s*(?:项)?\s*跳过", detail)
    return {"id": gid, "name": name, "code": code, "elapsed": dt,
            "detail": detail, "external": external, "log": log.name,
            "skipped": int(sk.group(1)) if sk else 0}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*", default=None)
    ap.add_argument("--skip-external", action="store_true")
    ap.add_argument("--timeout", type=float, default=900.0)
    args = ap.parse_args()

    gates = [g for g in GATES
             if (not args.only or g[0] in args.only)
             and not (args.skip_external and g[4])]

    print("=" * 78)
    print(f"验收执行：{len(gates)} 个关卡    {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 78)

    rows = []
    for gid, name, cmd, pattern, external in gates:
        print(f"\n[{gid}] {name} ... ", end="", flush=True)
        r = run_gate(gid, name, cmd, pattern, external, args.timeout)
        rows.append(r)
        mark = "OK " if r["code"] == 0 else "NG "
        print(f"{mark}({r['elapsed']:.0f}s) {r['detail'][:60]}")

    internal = [r for r in rows if not r["external"]]
    external_rows = [r for r in rows if r["external"]]
    bad = [r for r in internal if r["code"] != 0]
    ext_bad = [r for r in external_rows if r["code"] != 0]
    skipped = [r for r in rows if r.get("skipped")]

    lines = ["# 验收结果汇总", "",
             f"- 生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
             f"- 内部关卡：**{len(internal) - len(bad)}/{len(internal)} 通过**",
             f"- 外部依赖关卡：{len(external_rows) - len(ext_bad)}/{len(external_rows)} 返回 0"
             f"（失败可能是外部原因，单独列出、不计入内部失败）", ""]
    if skipped:
        lines += ["- ⚠️ **有跳过项的关卡**（退出码 0，但这一轮**没有验到**那些项，"
                  "不要读成通过）：", ""]
        for r in skipped:
            lines.append(f"  - **{r['id']}** {r['name']}：`{r['detail']}`")
        lines.append("")
    lines += ["| 关卡 | 名称 | 结论 | 用时 | 外部依赖 |", "|---|---|---|---|---|"]
    for r in rows:
        if r["code"] == 0:
            state = ("✅ 通过" if not r.get("skipped")
                     else f"✅ 通过（{r['skipped']} 项跳过未验证）")
        else:
            state = f"❌ 退出码 {r['code']}"
        lines.append(f"| {r['id']} | {r['name']} | {state} | {r['elapsed']:.0f}s | "
                     f"{'是' if r['external'] else '否'} |")
    lines += ["", "## 各关卡结论行", ""]
    for r in rows:
        lines.append(f"- **{r['id']}** {r['name']}：`{r['detail']}`（原始输出 `{r['log']}`）")
    (OUT / "SUMMARY.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    print("\n" + "=" * 78)
    print(f"内部关卡：{len(internal) - len(bad)}/{len(internal)} 通过")
    if bad:
        for r in bad:
            print(f"  ❌ {r['id']} {r['name']} → {r['detail']}")
    print(f"外部依赖关卡：{len(external_rows) - len(ext_bad)}/{len(external_rows)} 返回 0")
    for r in ext_bad:
        print(f"  ⚠️  {r['id']} {r['name']} → {r['detail']}")
    if skipped:
        print("⚠️  以下关卡退出码 0，但**有跳过项（本轮没验到）**，不要读成通过：")
        for r in skipped:
            print(f"  ⚠️  {r['id']} {r['name']} → {r['detail']}")
    print(f"汇总报告：{(OUT / 'SUMMARY.md').relative_to(ROOT)}")
    print("=" * 78)
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
