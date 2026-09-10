"""仓库卫生自检（P4-A1 的验收项目，固化成可复跑脚本 + 验收关卡 G13）

## 它守的是什么

P4-A1 发现仓库**根本没有版本控制**（`.git` 是指向已删除父仓库的指针文件），
修好之后必须防止的不是"再次丢失仓库"，而是**以下几类更隐蔽的退化**：

| 检查 | 防的是什么真实事故 |
|------|-------------------|
| 仓库真实可用 | 又一次被 worktree 指针/子模块指针顶掉 |
| `.git` 是目录 | 同上的具体形态 |
| 工作区干净 | 有未提交改动却对外说"已交付" |
| 跟踪的 .py 数量下限 | 误把源码目录加进 .gitignore，仓库里只剩文档 |
| **无被禁文件被跟踪** | `config.yaml`（含活 key）/`data/*.db`/`models/`(85MB)/`.coverage` 混进历史 |
| **跟踪内容无明文密钥** | key 被写进 git 历史（**删不干净**，只能改写历史） |
| `config.example.yaml` 可用 | 模板含密钥，或模板落后于实现（顶层键缺失） |

> 为什么"跟踪内容无密钥"要单独查，而不能只靠 `.gitignore`：
> `.gitignore` 只影响**未跟踪**文件。一个已经被 `git add` 过的文件，
> 之后再加忽略规则**不会**把它移出索引 —— 这正是 P4-A1 首提交时真实踩到的坑
> （`.coverage` 已暂存，加了忽略规则仍然被提交，只能 `git rm --cached` + amend）。

用法：
    python tools/check_repo_hygiene.py            # 作为验收关卡（G13）运行
    python tools/check_repo_hygiene.py --verbose
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PASS, FAIL = "[PASS]", "[FAIL]"
results: list = []

#: 绝对不允许被跟踪的路径（正则，匹配仓库相对路径）
FORBIDDEN_TRACKED = [
    (r"^config\.yaml$", "config.yaml 含活的 API key"),
    (r"^config/api_keys\.yaml$", "密钥文件"),
    (r"^data/", "运行时状态（实体栈/审计库/记忆库/提醒库）"),
    (r"^models/", "第三方模型权重（85MB）"),
    (r"^(.*/)?\.coverage(\.\w+)?$", "覆盖率数据（运行时产物）"),
    (r"__pycache__/", "字节码缓存"),
    (r"\.pyc$", "字节码"),
    (r"^logs/|\.log$", "日志"),
    (r"^build/|^dist/", "构建产物"),
    (r"\.vrm$", "VRM 模型（50MB）"),
    (r"\.ckpt$", "模型检查点"),
]

#: 明文密钥模式。
#:
#: 长度下限取 28：真实 key 远长于此（Groq 的 gsk_ 后接 52 字符、OpenRouter 的
#: sk-or-v1- 后接 64 位十六进制），而测试里用的假 key 是刻意写短的
#: （`sk-abcdefghijklmnopqrst` = 20 字符）。
#:
#: ⚠️ 为什么不用"排除 tests/ 目录"这种省事写法：**真实 key 恰恰可能被误粘进测试文件**，
#: 排除测试目录等于把最需要看守的地方放空。这里靠"长度 + 显式豁免清单"区分真假。
SECRET_RE = r"(gsk_[A-Za-z0-9]{28,}|sk-or-v1-[A-Za-z0-9]{28,}|sk-[A-Za-z0-9]{28,})"

#: **显式豁免**的假密钥：它们是测试"敏感信息不入库"用的占位串，不是真密钥。
#: 豁免必须逐条列出且写明理由 —— 不允许用通配符，否则等于把检测关掉。
#:
#: 现状说明（免得后人以为它是死代码）：下面这条**当前靠长度规则就已放行**
#: （20 < 28，压根进不了正则），所以它现在是一条冗余保险 ——
#: 保留的意义是"万一将来把阈值下调到 20 以下，它仍然安全"。
#: 豁免机制本身是否真的生效，由 `--self-test` 第 3 项临时注入长条目来证明。
ALLOWLIST = {
    "sk-abcdefghijklmnopqrst": "tests/agent/test_memory_*.py 里用于验证敏感判据的假值",
}


def scan_secrets(text: str) -> tuple[list, list]:
    """返回 (真命中, 已豁免命中)"""
    real, waived = [], []
    for m in re.finditer(SECRET_RE, text):
        val = m.group(0)
        if val in ALLOWLIST:
            waived.append(val)
        else:
            real.append(val)
    return real, waived

#: 跟踪的 .py 文件数下限（当前 210；留出余量，低于它说明源码被误忽略）
MIN_TRACKED_PY = 160


def check(ok: bool, label: str, detail: str = "") -> None:
    results.append((bool(ok), label))
    print(f"{PASS if ok else FAIL} {label}" + (f"  → {detail}" if detail else ""))


def git(*args: str) -> tuple[int, str]:
    p = subprocess.run(["git", *args], cwd=str(ROOT), capture_output=True,
                       text=True, encoding="utf-8", errors="replace", check=False)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def self_test() -> int:
    """自检：证明这个扫描器**既不漏也不误报**（"检查本身也要被检查"）

    P4-A1 第一次跑就暴露了误报：跟踪内容里的 3 处命中全是测试用的假 key，
    真扫描器会把一次正常提交拦下来 —— 而被拦的人第一反应是"把规则改松"。
    所以这里把两种情形都钉成用例：真 key 必须抓到、已知假 key 必须豁免。
    """
    print("=" * 72)
    print("扫描器自检（自检失败说明这个关卡本身不可信）")
    print("=" * 72)
    ok = True

    # 1) 真形态的密钥必须抓到（用明显假的字符填充，但长度与形态与真实一致）
    fake_real = ["gsk_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8S9t0U1v2W3x4",
                 "sk-or-v1-" + "0f1e2d3c4b5a69788796a5b4c3d2e1f0" * 2,
                 "sk-" + "Zz9Yy8Xx7Ww6Vv5Uu4Tt3Ss2Rr1Qq0Pp"]
    for s in fake_real:
        real, _ = scan_secrets(s)
        hit = real == [s]
        ok &= hit
        print(f"{PASS if hit else FAIL} 真形态密钥被抓到（长度 {len(s)}）")

    # 2) 占位串不该被抓（靠长度规则或靠豁免清单，两条路都算合格）
    for s in ALLOWLIST:
        real, waived = scan_secrets(s)
        good = not real
        ok &= good
        why = "豁免清单" if waived else f"长度规则（{len(s)} < 28，根本没进正则）"
        print(f"{PASS if good else FAIL} 占位串不被判为密钥：{s}（靠 {why}）")

    # 3) 豁免机制必须是**活代码**：临时塞一个长条目，证明它真的会放行。
    #    不这么做的话，豁免清单可能只是一段永远走不到的装饰
    #    （跟繁简表里那些恒等条目同类：看着像安全机制，实际不生效）。
    probe = "gsk_" + "0" * 40
    ALLOWLIST[probe] = "自检临时注入（用完即删）"
    try:
        real, waived = scan_secrets(probe)
        good = not real and waived == [probe]
        ok &= good
        print(f"{PASS if good else FAIL} 豁免清单真的会放行长密钥（机制是活的，不是装饰）")
    finally:
        del ALLOWLIST[probe]

    # 4) 短串不该被抓（普通英文/短标识符不能被当成密钥）
    for s in ("sk-short", "gsk_tiny", "api_key", "not-a-key"):
        real, _ = scan_secrets(s)
        good = not real
        ok &= good
        print(f"{PASS if good else FAIL} 短串/普通词不误报：{s!r}")

    print("-" * 72)
    print(f"扫描器自检：{'通过 ✅' if ok else '失败 ❌'}")
    print("=" * 72)
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="仓库卫生自检（P4-A1 验收项 / 关卡 G13）")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--self-test", action="store_true",
                    help="只跑扫描器自检（不检查仓库）")
    args = ap.parse_args()

    if args.self_test:
        return self_test()

    print("=" * 72)
    print("仓库卫生自检（P4-A1 / G13）")
    print("=" * 72)

    # ── 1. 仓库真实可用 ──
    code, out = git("rev-parse", "--is-inside-work-tree")
    check(code == 0 and out.strip() == "true", "在真正的 git 工作区内",
          f"rev-parse → {out.strip()!r}")
    if code != 0:
        print("\n仓库不可用，后续检查无意义。")
        return 1

    check((ROOT / ".git").is_dir(), "`.git` 是目录（不是 worktree 指针文件）")

    code, out = git("log", "--oneline")
    check(code == 0 and bool(out.strip()), "至少有一次提交",
          f"{len(out.strip().splitlines())} 个提交")

    code, out = git("stash", "list")
    check(not out.strip(), "没有遗留的 stash", out.strip()[:60] or "空")

    # ── 2. 工作区干净 ──
    #
    # 分两档看，因为"验收运行本身会写证据文件"：
    #   ① 严格档：全部改动（含证据）—— 只作提示
    #   ② 判定档：**排除 docs/agent/evidence/** —— 这才是"有没有未提交的代码/文档改动"
    # 为什么要这么分：验收关卡（run_acceptance.py）会把每次运行的原始输出写进
    # docs/agent/evidence/，那些文件**本来就该每次变**。若把它们算成"脏"，
    # 这条检查会在每次跑完验收后必然报红 —— 而一条必然报红的检查等于没有检查，
    # 最后一定被人删掉。所以把"证据变化"降级为提示，把"代码/文档变化"保留为失败。
    code, all_dirty = git("status", "--porcelain")
    code, code_dirty = git("status", "--porcelain", "--", ".",
                           ":(exclude)docs/agent/evidence")
    all_lines = [ln for ln in all_dirty.splitlines() if ln.strip()]
    code_lines = [ln for ln in code_dirty.splitlines() if ln.strip()]
    if all_lines and not code_lines:
        print(f"      （证据目录有 {len(all_lines)} 项变化 —— 属验收运行的正常产物，"
              f"不计为脏；跑完后应提交）")
    check(not code_lines, "工作区干净（排除验收证据目录）",
          f"{len(code_lines)} 项未提交：{code_lines[:3]}" if code_lines else "干净")

    # ── 3. 源码确实在仓库里 ──
    code, out = git("ls-files")
    tracked = [ln for ln in out.splitlines() if ln.strip()]
    py = [p for p in tracked if p.endswith(".py")]
    check(len(py) >= MIN_TRACKED_PY, f"跟踪的 .py 文件数 ≥ {MIN_TRACKED_PY}",
          f"实际 {len(py)}")

    # ── 4. 被禁文件不得被跟踪 ──
    offenders: list = []
    for path in tracked:
        for pat, why in FORBIDDEN_TRACKED:
            if re.search(pat, path):
                offenders.append(f"{path}（{why}）")
                break
    check(not offenders, "没有被禁文件进入版本库",
          f"{len(offenders)} 个：{offenders[:3]}" if offenders else "全部合规")

    # ── 5. 跟踪内容无明文密钥 ──
    code, out = git("grep", "--cached", "-I", "-E", SECRET_RE)
    # git grep 无命中返回 1；有命中返回 0
    hits = [ln for ln in out.splitlines() if ln.strip()] if code == 0 else []
    real, waived = scan_secrets("\n".join(hits))
    if waived:
        # 豁免的命中**要打印出来**而不是悄悄放过：否则"豁免清单"会变成藏东西的地方
        print(f"      （已豁免 {len(waived)} 处测试占位符："
              f"{sorted(set(waived))} —— 见 ALLOWLIST）")
    check(not real, "跟踪内容无明文密钥（gsk_/sk-or-v1-，长度≥28 且不在豁免清单）",
          f"{len(real)} 处命中：{sorted(set(real))[:2]}" if real else "0 处")

    # ── 6. 配置模板可用且不落后 ──
    tpl = ROOT / "config.example.yaml"
    check(tpl.exists(), "config.example.yaml 存在")
    if tpl.exists():
        body = tpl.read_text(encoding="utf-8")
        leaked = re.findall(SECRET_RE, body)
        check(not leaked, "配置模板无明文密钥",
              f"{len(leaked)} 处" if leaked else "0 处")
        proc = subprocess.run(
            [sys.executable, "tools/make_config_example.py", "--check"],
            cwd=str(ROOT), capture_output=True, text=True,
            encoding="utf-8", errors="replace", check=False,
        )
        txt = ((proc.stdout or "") + (proc.stderr or "")).strip()
        check(proc.returncode == 0, "配置模板未落后于实现（顶层键覆盖齐全）",
              txt.splitlines()[-1] if txt else "")

    # ── 7. 忽略规则仍在生效（防"规则被误删"）──
    for path, want_ignored in (("config.yaml", True), ("models", True),
                               ("data", True), (".coverage", True)):
        rc, _ = git("check-ignore", "-q", path)
        ignored = rc == 0
        check(ignored == want_ignored,
              f"忽略规则：{path} {'应被忽略' if want_ignored else '不应被忽略'}",
              "被忽略" if ignored else "未忽略")

    ok = sum(1 for p, _ in results if p)
    bad = sum(1 for p, _ in results if not p)
    print("-" * 72)
    print(f"仓库卫生自检：{ok}/{ok + bad} 通过，{bad} 失败")
    if bad:
        for p, label in results:
            if not p:
                print(f"  {FAIL} {label}")
    print("=" * 72)
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
