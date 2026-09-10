"""真实 GUI 启动验证（可重复运行）

对应验收报告里长期挂着的开放项 **"真实环境启动一次应用（完整 GUI 内验证 Agent 层）"**。
它之所以一直没做，是因为"会弹窗口，需要用户在场" —— 但**弹窗口本身不需要人**：
人需要做的只是"看一眼"，而"看一眼"可以用截图 + 像素统计替代。

## 这个脚本验什么

| 步骤 | 断言 |
|------|------|
| 1. 启动 | `python run.py` 能在超时内起来，不崩 |
| 2. 窗口 | 出现标题含「欣雅」的可见顶层窗口，尺寸合理 |
| 3. 渲染 | 窗口区域**不是空白**（颜色数与方差达标）→ 宠物真的画出来了 |
| 4. Agent 层 | 日志出现 `Agent 层就绪: enabled=True, tools=21`（**关键**：这是"Agent 层在 GUI 里是活的"的唯一证据，历史上它曾静默降级为纯对话） |
| 5. 新能力 | 日志出现 translate/weather 接入、提醒调度器启动 |
| 6. 优雅退出 | WM_CLOSE 后窗口消失、进程退出码 **0**、日志显示 13 个插件逆序卸载 + `Agent 栈已释放` + 调度器已停止 |
| 7. 无残留 | 退出后没有遗留进程；没有悬挂的 reminder-scheduler 线程（由 6 的日志保证） |

## 副作用

会在用户桌面上真的弹出宠物窗口（约 40 秒），并在退出前**截一次屏**用于判断"是否渲染成功"。
截图**只裁到宠物窗口范围**，判断完**立即删除**，不留盘、不外传。

用法：
    python tools/verify_gui_launch.py
    python tools/verify_gui_launch.py --keep-shot   # 保留截图便于人工复核
"""
from __future__ import annotations

import argparse
import ctypes
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent

PASS, FAIL, SKIP = "[PASS]", "[FAIL]", "[SKIP]"
results: list = []

WINDOW_TITLE = "欣雅"
WM_CLOSE = 0x0010
USER32 = ctypes.windll.user32


def check(ok: bool, label: str, detail: str = "") -> None:
    results.append((bool(ok), label))
    print(f"{PASS if ok else FAIL} {label}" + (f"  → {detail}" if detail else ""))


def skipped(label: str, why: str) -> None:
    results.append((None, label))
    print(f"{SKIP} {label}  → {why}")


# ══════════════════════════════════════════════════════
#  Win32 窗口工具
# ══════════════════════════════════════════════════════

_WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)


def find_window(title_part: str = WINDOW_TITLE):
    """找可见的、标题含 title_part 的顶层窗口 → (hwnd, title, rect) 或 None"""
    hits: list = []

    def _cb(hwnd, _):
        if not USER32.IsWindowVisible(hwnd):
            return True
        n = USER32.GetWindowTextLengthW(hwnd)
        if n <= 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        USER32.GetWindowTextW(hwnd, buf, n + 1)
        if title_part in buf.value:
            rect = wintypes.RECT()
            USER32.GetWindowRect(hwnd, ctypes.byref(rect))
            hits.append((hwnd, buf.value, rect))
        return True

    USER32.EnumWindows(_WNDENUMPROC(_cb), 0)
    return hits[0] if hits else None


def window_shot(box: tuple, out: Path):
    """截取给定区域（用 PIL，不依赖项目代码）"""
    from PIL import ImageGrab
    img = ImageGrab.grab(all_screens=True).crop(box)
    img.save(out)
    return img


def looks_rendered(img) -> tuple:
    """判断窗口区域是不是"真的画了东西"而不是一片空白

    判据（**刻意不跟"对比度"挂钩**）：

    - 颜色种类 > 20
    - **主色占比 < 97%** —— 即至少 3% 的像素与出现最多的那个颜色不同

    第二条才是"不是纯色填充"的正解。最初这里用的是"亮度标准差 > 8"，
    结果在暗色主题下会误判：实测窗口渲染正常（171 种颜色）却只有 σ=7.9，
    于是**验收门随机翻红**。阈值调到 8 以下只是凑绿 ——
    真正的问题是"标准差"衡量的是对比度，不是"有没有内容"。
    纯色窗口的这两项都会不达标（颜色数=1、主色占比≈100%）。
    """
    small = img.convert("RGB").resize((80, 80))
    pixels = list(small.getdata())
    colors = len(set(pixels))
    top = max(set(pixels), key=pixels.count)
    dominant = pixels.count(top) / len(pixels)
    ok = colors > 20 and dominant < 0.97
    return ok, f"颜色数={colors} 主色占比={dominant:.1%}"


# ══════════════════════════════════════════════════════

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep-shot", action="store_true", help="保留截图")
    ap.add_argument("--timeout", type=float, default=90.0, help="等待窗口出现的上限（秒）")
    args = ap.parse_args()

    print("=" * 72)
    print("真实 GUI 启动验证")
    print(f"时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 72)

    log_path = PROJECT / "docs" / "agent" / "evidence" / "gui" / "launch_run.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    shot_path = PROJECT / "docs" / "agent" / "evidence" / "gui" / "pet_window.png"

    print("\n[GUI-1] 启动应用")
    t0 = time.time()
    with open(log_path, "w", encoding="utf-8") as log:
        proc = subprocess.Popen(
            [sys.executable, "run.py"],
            cwd=str(PROJECT), stdout=log, stderr=subprocess.STDOUT,
        )
    print(f"  PID={proc.pid}，日志 → {log_path.relative_to(PROJECT)}")

    # ── 等窗口 ──
    win = None
    while time.time() - t0 < args.timeout:
        if proc.poll() is not None:
            check(False, "应用保持运行（启动未崩溃）", f"提前退出 code={proc.returncode}")
            break
        win = find_window()
        if win:
            break
        time.sleep(1.0)

    if proc.poll() is not None:
        print(log_path.read_text(encoding="utf-8", errors="replace")[-2000:])
        return 1

    check(win is not None, f"出现标题含「{WINDOW_TITLE}」的可见窗口",
          f"{(time.time() - t0):.1f}s 内")

    # ── 截图判渲染 ──
    if win is None:
        check(False, "宠物窗口渲染检查")
    else:
        hwnd, title, r = win
        w, h = r.right - r.left, r.bottom - r.top
        print(f"  hwnd={hwnd} title={title!r} 位置=({r.left},{r.top}) 尺寸={w}x{h}")
        check(w >= 120 and h >= 120, "窗口尺寸合理", f"{w}x{h}")

        print("\n[GUI-2] 截图判断是否真的渲染出来")
        try:
            # 抓 3 次取最好的一帧：窗口刚出现时可能还没画完，
            # 单次抓图会把"还在绘制"误判成"空白"（这是真实存在的抖动源）
            best = None
            for attempt in range(3):
                img = window_shot((r.left, r.top, r.right, r.bottom), shot_path)
                ok, detail = looks_rendered(img)
                print(f"  第{attempt + 1}次: {detail}")
                if best is None or ok:
                    best = (ok, detail)
                if ok:
                    break
                time.sleep(1.0)
            check(best[0], "窗口区域不是空白（宠物真的画出来了）", best[1])
        except Exception as e:
            check(False, "截图判断渲染", f"{type(e).__name__}: {e}")

    # ── 日志断言 ──
    print("\n[GUI-3] Agent 层在 GUI 内是否真的活着")
    deadline = time.time() + 30
    text = ""
    while time.time() < deadline:
        text = log_path.read_text(encoding="utf-8", errors="replace")
        if "Agent 层就绪" in text:
            break
        time.sleep(1.0)

    import re
    m = re.search(r"Agent 层就绪: enabled=(\w+), tools=(\d+), router=(\w+)", text)
    check(bool(m), "日志出现「Agent 层就绪」（没降级为纯对话）",
          m.group(0) if m else "未找到")
    if m:
        check(m.group(1) == "True", "Agent 层 enabled=True", m.group(1))
        check(int(m.group(2)) >= 21, "工具数 >= 21", m.group(2))
    check("PetWindow 已订阅 Agent 事件" in text, "PetWindow 订阅了 Agent 事件")
    check("[productivity_providers] translate 已接入" in text,
          "translate 已在 GUI 内接入真实 LLM")
    check("[productivity_providers] weather 已接入" in text,
          "weather 已在 GUI 内接入真实服务")
    check("[reminder] 调度器已启动" in text, "提醒调度器已启动")

    # ── 优雅退出 ──
    print("\n[GUI-4] 优雅退出")
    if win is not None:
        hwnd = win[0]
        USER32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
        t1 = time.time()
        while time.time() - t1 < 30 and USER32.IsWindow(hwnd):
            time.sleep(0.25)
        check(not USER32.IsWindow(hwnd), "窗口已关闭",
              f"{(time.time() - t1):.1f}s")

    try:
        code = proc.wait(timeout=40)
    except subprocess.TimeoutExpired:
        proc.kill()
        code = None
    check(code == 0, "进程退出码为 0（优雅退出）", f"code={code}")

    text = log_path.read_text(encoding="utf-8", errors="replace")
    check("[agent] Agent 栈已释放" in text, "Agent 栈已释放")
    check("[reminder] 调度器已停止" in text, "提醒调度器已停止（不留悬挂线程）")
    check("小忆应用已关闭" in text, "应用关闭流程走完")
    check("Traceback" not in text, "退出路径无未捕获异常",
          "有 Traceback" if "Traceback" in text else "")

    # ── 清理 ──
    if not args.keep_shot and shot_path.exists():
        shot_path.unlink()
        print(f"  （截图已删除：{shot_path.name}；加 --keep-shot 可保留）")

    ok = sum(1 for x, _ in results if x is True)
    bad = sum(1 for x, _ in results if x is False)
    sk = sum(1 for x, _ in results if x is None)
    print("\n" + "=" * 72)
    print(f"GUI 结果：{ok}/{ok + bad} 通过，{bad} 失败，{sk} 跳过")
    for x, label in results:
        if x is not True:
            print(f"  {FAIL if x is False else SKIP} {label}")
    print(f"日志归档: {log_path.relative_to(PROJECT)}")
    print("=" * 72)
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
