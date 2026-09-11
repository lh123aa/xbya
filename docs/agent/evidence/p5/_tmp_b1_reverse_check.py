# -*- coding: utf-8 -*-
"""反向验证：把 D16 的原始写法临时塞回去，确认守卫用例**真的会红**

为什么必须做这一步：本项目已经吃过"检查在空转"的亏（`_has_real_verdict` 的
非贪婪正则、`check_manual_evidence` 的假结论、`interrupt_speech` 的假件）。
一条**永远通过**的守卫用例与一条**真的在守**的用例，在测试报告里长得一模一样。

做法：备份 → 改回原始写法 → 跑守卫 → 断言"确实失败了" → 恢复 →
再跑一次确认恢复后全绿。**全程在原文件上做，最后必须复原**（脚本用 try/finally 保证）。

用法：python docs/agent/evidence/p5/_tmp_b1_reverse_check.py
"""
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(r"E:\程序\桌面宠物\xiaoyi-vrm-worktree")
OUT = ROOT / "docs" / "agent" / "evidence" / "p5" / "d16_schema_shape.txt"

OPENAI = ROOT / "plugins/llm/openai_api/plugin.py"
OPENROUTER = ROOT / "plugins/llm/openrouter/plugin.py"
TESTFILE = "tests/test_tool_schema_shape.py"


def run_tests() -> tuple:
    p = subprocess.run([sys.executable, "-m", "pytest", TESTFILE, "-q"],
                       cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return p.returncode, (p.stdout or "")


def tail(text: str, n: int = 3) -> str:
    lines = [ln for ln in text.splitlines()
             if "passed" in ln or "failed" in ln or "FAILED" in ln]
    return "\n".join(lines[-n:]) if lines else "(无结论行)"


lines = []
w = lines.append
w("# P5-B1 / D16 证据：工具 schema 形制归一 + 反向验证")
w("")
w(f"生成时间：{datetime.now():%Y-%m-%d %H:%M:%S}")
w("")
w("## 一、正向：修复后的守卫用例")
w("")
code, out = run_tests()
w("```")
w(f"$ pytest {TESTFILE} -q")
w(tail(out, 2))
w(f"[exit] {code}")
w("```")
w("")
assert code == 0, "正向没绿，先别做反向验证"
w("## 二、反向：把原始写法塞回去，守卫**必须**变红")
w("")
w("本项目吃过三次「检查在空转」的亏，所以每条新守卫都要用**反方向输入**验一遍：")
w("永远通过的守卫与真的在守的守卫，在报告里长得一模一样。")
w("")

RESTORE = {}
try:
    # ── 反向 1：把两个插件的 to_openai_tools 换回手写包一层 ──
    for path, old, new in (
        (OPENAI,
         "            payload_tools = to_openai_tools(tools)",
         '            payload_tools = [{"type": "function", "function": t} for t in tools]'),
        (OPENROUTER,
         "        payload_tools = to_openai_tools(tools)",
         '        payload_tools = [{"type": "function", "function": t} for t in tools]'),
    ):
        RESTORE[path] = path.read_text(encoding="utf-8")
        src = RESTORE[path]
        assert new not in src, f"{path} 里已经是坏写法了？"
        assert old in src, f"{path} 里找不到要替换的那一行：{old!r}"
        path.write_text(src.replace(old, new), encoding="utf-8")

    code1, out1 = run_tests()
    w("### 反向 1：插件改回 `[{\"type\": \"function\", \"function\": t} for t in tools]`")
    w("")
    w("```")
    w(f"$ pytest {TESTFILE} -q   # 已把两个插件改回原始写法")
    w(tail(out1, 6))
    w(f"[exit] {code1}")
    w("```")
    w("")
    w(f"- 结论：**{'✅ 守卫确实变红了（不是空转）' if code1 != 0 else '❌ 坏了还全绿 —— 守卫在空转！'}**")
    w("")
    assert code1 != 0, "把 bug 塞回去守卫却全绿 —— 这些用例是空转的"
finally:
    for path, src in RESTORE.items():
        path.write_text(src, encoding="utf-8")

# ── 反向 2：拿掉 `except ToolSchemaError: raise`，形制错误会被宽口 except 吃掉 ──
w("### 反向 2：拿掉 `except ToolSchemaError: raise`")
w("")
w("这一条守的是「形制错误必须穿透宽口 `except Exception`」——")
w("被吃掉就退化成 `return None` / `(False, None)`，而上层把 `None` 读成")
w("「模型本轮用文字回答」，于是**接错又变回静默失效**。")
w("")
RESTORE2 = {}
try:
    for path, needle in (
        (OPENAI, "        except ToolSchemaError:\n"),
        (OPENROUTER, "        except ToolSchemaError:\n"),
    ):
        RESTORE2[path] = path.read_text(encoding="utf-8")
        src = RESTORE2[path]
        assert needle in src, f"{path} 里找不到 except ToolSchemaError"
        # 连同紧随其后的注释块一起去掉，直到下一个 except 之前
        i = src.index(needle)
        j = src.index("        except Exception", i)
        path.write_text(src[:i] + src[j:], encoding="utf-8")

    code2, out2 = run_tests()
    w("```")
    w(f"$ pytest {TESTFILE} -q   # 已拿掉 except ToolSchemaError: raise")
    w(tail(out2, 6))
    w(f"[exit] {code2}")
    w("```")
    w("")
    w(f"- 结论：**{'✅ 守卫变红（这一条也在真的守）' if code2 != 0 else '❌ 全绿 —— 这条守卫没在守'}**")
    w("")
finally:
    for path, src in RESTORE2.items():
        path.write_text(src, encoding="utf-8")

# ── 恢复确认 ──
w("## 三、恢复后复跑（确认原文件已复原）")
w("")
code3, out3 = run_tests()
w("```")
w(f"$ pytest {TESTFILE} -q")
w(tail(out3, 2))
w(f"[exit] {code3}")
w("```")
w("")
w(f"- 结论：{'✅ 已复原并全绿' if code3 == 0 else '❌ 没恢复干净'}")
w("")
w("## 四、git 层面的复原核对")
w("")
diff = subprocess.run(["git", "diff", "--stat", "--",
                       "plugins/llm/openai_api/plugin.py",
                       "plugins/llm/openrouter/plugin.py"],
                      cwd=ROOT, capture_output=True, text=True,
                      encoding="utf-8", errors="replace")
w("```")
w("$ git diff --stat -- plugins/llm/openai_api/plugin.py plugins/llm/openrouter/plugin.py")
w((diff.stdout or "").strip() or "(无差异输出)")
w("```")
w("")
w("> 这里的差异应当是**本轮修复带来的那几行**（引入 to_openai_tools / 新增 except 分支），")
w("> 不应包含反向验证留下的痕迹 —— 脚本用 try/finally 复原，且第三步复跑已确认全绿。")

OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
print(f"写入 {OUT}（{OUT.stat().st_size} bytes）")
print(f"正向 exit={code}（应 0）")
print(f"反向1 exit={code1}（应非 0）")
print(f"反向2 exit={code2}（应非 0）")
print(f"恢复后 exit={code3}（应 0）")
assert code3 == 0, "恢复后没绿 —— 原文件可能没还原干净！"
print("DONE")
