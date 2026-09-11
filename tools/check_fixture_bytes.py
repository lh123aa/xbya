# -*- coding: utf-8 -*-
"""G14：SERP fixture 的**字节完整性**在版本控制下也必须成立（P5-B5 补充关卡）。

为什么单起一道关卡：B5 的全部价值建立在"fixture 就是当时服务器发回来的字节"
之上，判据是 `manifest.json` 里逐份记下的 **字节数 + MD5**。
而 Git 默认会把 `.html` 当文本做 LF↔CRLF 转换 —— 一旦发生，
`tests/fixtures/serp/*.html` 的字节就变了，**测试报出来的会是"fixture 被改过"**，
而真实原因是版本控制的行尾处理。

这类缺陷在本机永远测不出来（本机工作区是 CRLF，`read_bytes()` 读到的就是
提交后的形态），只有**换一台机器 / 重新 clone** 才会炸 ——
所以必须在这里显式钉住，不能靠"跑一遍测试是绿的"。

本脚本查三件事：
  1. `.gitattributes` 里对 `tests/fixtures/serp/*.html` 关掉了文本转换（`-text`）
  2. Git **实际**认这个规则（`git check-attr`，不是看文件里写了什么）
  3. 工作区文件的字节数与 MD5 与 `manifest.json` 一致（现状自检）

用法：python tools/check_fixture_bytes.py
退出码：0 = 全部通过；1 = 有失败项
"""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "tests" / "fixtures" / "serp"
MANIFEST = FIX / "manifest.json"

failures = []
passes = []


def check(ok: bool, label: str, detail: str = "") -> bool:
    (passes if ok else failures).append(label)
    print(f"[{'PASS' if ok else 'FAIL'}] {label}"
          + (f"  → {detail}" if detail else ""))
    return ok


def git(*args: str) -> str:
    p = subprocess.run(["git", *args], cwd=str(ROOT), capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    return (p.stdout or "").strip()


print("=" * 72)
print("SERP fixture 字节完整性（P5-B5）")
print("=" * 72)

# ── 1. .gitattributes 存在且写了 -text ──
ga = ROOT / ".gitattributes"
check(ga.is_file(), "`.gitattributes` 存在")
if ga.is_file():
    body = ga.read_text(encoding="utf-8")
    check("tests/fixtures/serp/*.html -text" in body,
          "对 SERP 抓取原文关掉了文本转换（`-text`）",
          "否则 Git 会按 autocrlf 改行尾，MD5 判据跨机器失效")

# ── 2. Git **实际**是怎么认的（不看文件里写了什么，问 Git 自己）──
target = FIX / "bing_run1.html"
if target.is_file() and ga.is_file():
    out = git("check-attr", "text", "--", "tests/fixtures/serp/bing_run1.html")
    # 期望形如：tests/fixtures/serp/bing_run1.html: text: unset
    unset = out.endswith("text: unset")
    check(unset, "Git 实际把该路径判为「不做文本转换」", out or "（无输出）")
else:
    check(False, "拿不到用于 check-attr 的样本文件")

# ── 3. 工作区字节与清单一致（现状自检）──
if MANIFEST.is_file():
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    bad = []
    for e in manifest["entries"]:
        p = FIX / e["file"]
        if not p.is_file():
            bad.append(f"{e['file']} 缺失")
            continue
        raw = p.read_bytes()
        if len(raw) != e["bytes"]:
            bad.append(f"{e['file']} 字节 {len(raw)}≠{e['bytes']}")
        if hashlib.md5(raw).hexdigest().upper() != e["md5"]:
            bad.append(f"{e['file']} MD5 不符")
    check(not bad, f"工作区 {len(manifest['entries'])} 份 fixture 与清单逐份一致",
          "; ".join(bad) if bad else "")
else:
    check(False, "`manifest.json` 存在")

# ── 4. 清单里有 fixture 被 Git 跟踪（否则 clone 后拿不到）──
if FIX.is_dir():
    tracked = git("ls-files", "tests/fixtures/serp").splitlines()
    tracked = [t for t in tracked if t.strip()]
    expected = len(manifest["entries"]) + 1 if MANIFEST.is_file() else 0
    check(len(tracked) >= expected,
          f"fixture 已被版本控制跟踪（{len(tracked)} 个文件）",
          "未跟踪 = 别人 clone 不到，测试会因缺文件而失败")

print()
print("=" * 72)
print(f"通过 {len(passes)} 项，失败 {len(failures)} 项")
if failures:
    print("失败项：")
    for f in failures:
        print(f"  ✘ {f}")
    sys.exit(1)
print("SERP fixture 的字节完整性成立（含版本控制层面）")
