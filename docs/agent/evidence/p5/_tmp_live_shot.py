# -*- coding: utf-8 -*-
r"""抓桌宠窗口的实截图 —— 「启动了」不等于「画出来了」。

为什么要截图：进程活着只说明它没崩。桌宠可能因为精灵图路径不对、
动画 clip 为空等原因**显示成一片透明**，而日志里未必有 ERROR。
本脚本找到标题含「欣雅」的窗口，把它那块屏幕区域截下来存证。
"""
import ctypes
import ctypes.wintypes as wt
import sys
import time
from pathlib import Path

from PIL import Image, ImageGrab

ROOT = Path(__file__).resolve().parents[4]
OUT = ROOT / "docs" / "agent" / "evidence" / "gui"

user32 = ctypes.windll.user32
user32.SetProcessDPIAware()   # 高 DPI 下不设这个，截出来的区域会错位


def find_window(title_part: str):
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    def cb(hwnd, _):
        n = user32.GetWindowTextLengthW(hwnd)
        if n:
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            if title_part in buf.value and user32.IsWindowVisible(hwnd):
                found.append((hwnd, buf.value))
        return True

    user32.EnumWindows(cb, 0)
    return found


deadline = time.time() + 40
wins = []
while time.time() < deadline:
    wins = find_window("欣雅")
    if wins:
        break
    time.sleep(1)

if not wins:
    print("没找到标题含「欣雅」的可见窗口")
    sys.exit(1)

hwnd, title = wins[0]
rect = wt.RECT()
user32.GetWindowRect(hwnd, ctypes.byref(rect))
x0, y0, x1, y1 = rect.left, rect.top, rect.right, rect.bottom
print(f"窗口: title={title!r}  hwnd={hwnd}")
print(f"位置: ({x0},{y0}) 尺寸 {x1-x0}x{y1-y0}")

# 多截几帧，确认它在动（不动就是动画没跑）
frames = []
for i in range(3):
    img = ImageGrab.grab(bbox=(x0, y0, x1, y1))
    frames.append(img)
    time.sleep(1.2)

OUT.mkdir(parents=True, exist_ok=True)
# 拼成一张：左到右三帧 + 每帧的不透明像素占比
W, H = frames[0].size
sheet = Image.new("RGB", (W * 3 + 16, H + 20), (240, 240, 244))
for i, f in enumerate(frames):
    sheet.paste(f, (i * (W + 8), 20))
sheet.save(OUT / "pet_live_3frames.png")
print(f"三帧实截: pet_live_3frames.png  ({sheet.size[0]}x{sheet.size[1]})")

import numpy as np
for i, f in enumerate(frames):
    a = np.asarray(f.convert("RGB")).astype(int)
    # 与"最左上角像素"比较，数出有多少像素明显不同（= 画了东西）
    bg = a[0, 0]
    diff = (np.abs(a - bg).sum(axis=2) > 24)
    ys, xs = np.where(diff)
    if len(xs):
        print(f"  第{i+1}帧: 与背景不同的像素 {diff.mean():.1%}   "
              f"区域 x[{xs.min()}..{xs.max()}] y[{ys.min()}..{ys.max()}]")
    else:
        print(f"  第{i+1}帧: !! 整幅都是背景色 —— 宠物没画出来")

# 帧间差异 → 动画是否在跑
d01 = np.abs(np.asarray(frames[0], dtype=int) - np.asarray(frames[1], dtype=int)).sum()
d12 = np.abs(np.asarray(frames[1], dtype=int) - np.asarray(frames[2], dtype=int)).sum()
print()
print(f"帧间总差: 帧1-帧2={d01}  帧2-帧3={d12}")
print("  （>0 说明画面在变，即动画真的在跑；注意呼吸幅度小，差值可能不大）")
