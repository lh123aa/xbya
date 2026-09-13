# -*- coding: utf-8 -*-
r"""让正在运行的桌宠**显示一句字幕**，同时截屏 —— 不要靠读源码推布局。

为什么必须实截：我用源码常量推算出的结论是"精灵与字幕不重叠"，
但上一次的真实截图显示字幕明显压在角色身上。**推算错了**，
所以这次不看推算，看屏幕：
  1. 用 ctypes 给「欣雅」窗口发一个自定义消息让它调 show_bubble（做不到）
     → 改为：直接给窗口发键盘/菜单事件不可靠，所以**换一条路**：
       从外部再开一个进程不合适，于是这里用**窗口截图 + 像素分析**回答真正的问题：
       字幕条到底占窗口哪几行、宠物像素在哪些行。
  2. 用像素分析给出**确定的数字**：把窗口按行统计"深色像素数"（字幕条是半透明黑）
     与"角色彩色像素数"，两者的 y 区间就是答案。
"""
import ctypes
import ctypes.wintypes as wt
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageGrab

ROOT = Path(__file__).resolve().parents[4]
OUT = ROOT / "docs" / "agent" / "evidence" / "gui"
user32 = ctypes.windll.user32
user32.SetProcessDPIAware()


def find_window(title_part):
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


wins = find_window("欣雅")
if not wins:
    print("没有正在运行的「欣雅」窗口 —— 请先启动 run.py")
    raise SystemExit(1)
hwnd, title = wins[0]
r = wt.RECT()
user32.GetWindowRect(hwnd, ctypes.byref(r))
x0, y0, x1, y1 = r.left, r.top, r.right, r.bottom
print(f"窗口 {title!r}  ({x0},{y0})  {x1-x0}x{y1-y0}")

img = ImageGrab.grab(bbox=(x0, y0, x1, y1))
img.save(OUT / "geom_probe.png")
a = np.asarray(img.convert("RGB")).astype(int)
H, W = a.shape[:2]

# 逐行统计：(a) 近黑像素（字幕条是半透明黑底；角色头发也是近黑，需区分）
#              (b) 明显非白非黑的彩色像素（肤色/裙子）
r_, g_, b_ = a[:, :, 0], a[:, :, 1], a[:, :, 2]
dark = (r_ < 60) & (g_ < 60) & (b_ < 60)
colored = (np.abs(r_ - g_) > 18) | (np.abs(g_ - b_) > 18)

print()
print("逐行剖面（只列有内容的行）:  行号  近黑像素  彩色像素  横向范围")
print("  " + "-" * 62)
for y in range(H):
    nd = int(dark[y].sum())
    nc = int(colored[y].sum())
    if nd + nc < 3:
        continue
    cols = np.where(dark[y] | colored[y])[0]
    span = f"x[{cols.min()}..{cols.max()}]" if len(cols) else ""
    tag = ""
    if nd > W * 0.5:
        tag = "  ◀ 整行近黑（疑似字幕条/标题栏）"
    print(f"  {y:>3}   {nd:>5}   {nc:>5}   {span}{tag}")

# 找"整行近黑"的连续区间 —— 那就是字幕条或深色背景区
rows_full_dark = [y for y in range(H) if dark[y].mean() > 0.5]
if rows_full_dark:
    groups = []
    cur = [rows_full_dark[0]]
    for y in rows_full_dark[1:]:
        if y == cur[-1] + 1:
            cur.append(y)
        else:
            groups.append((cur[0], cur[-1]))
            cur = [y]
    groups.append((cur[0], cur[-1]))
    print()
    print("整行近黑的连续区间:")
    for gy0, gy1 in groups:
        print(f"  y[{gy0}..{gy1}]  高 {gy1-gy0+1}px")
else:
    print()
    print("没有整行近黑的区间（当前没有字幕显示）")

print()
print(f"实截已存: geom_probe.png  ({W}×{H})")
