# -*- coding: utf-8 -*-
r"""在 128×128 的成品图里定位五官：**先量，再决定能做什么动画**。

为什么必须先量：这张图是**单张静态立绘**，没有分层、没有骨骼。
能做的动画只有"对局部区域做几何变换"——眨眼要动眼睛那几行，说话要动嘴那几行。
所以"眼睛在第几行、嘴在第几行、脸宽多少像素"不是参考信息，而是**硬约束**：
  眼睛只有 2~3 px 高 ⇒ 眨眼就是"把这几行涂成肤色"，看得出但不精细；
  嘴只有 3~4 px 宽 ⇒ 口型只能做开合，做不出圆唇/扁唇。

量法：脸是亮的（肤色），头发是暗的。在头部区域内逐行统计
  · 肤色像素数（判定脸的宽度）
  · 暗像素数（判定眼睛/眉毛/嘴这两条"暗带"）
然后按行剖面找两条暗带的中心线。
"""
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[4]
OUT = ROOT / "docs" / "agent" / "evidence" / "p5"
SPR = OUT / "_pet_variant_D.png"        # 上一轮选定的 D 变体（89×110）

im = Image.open(SPR).convert("RGBA")
a = np.asarray(im).astype(np.float64)
alpha = a[:, :, 3]
rgb = a[:, :, :3]

# 亮度
lum = 0.299 * rgb[:, :, 0] + 0.587 * rgb[:, :, 1] + 0.114 * rgb[:, :, 2]
opaque = alpha > 128

ys, xs = np.where(opaque)
y0, y1 = int(ys.min()), int(ys.max())
x0, x1 = int(xs.min()), int(xs.max())
h = y1 - y0 + 1
print(f"角色包围盒: x[{x0}..{x1}] y[{y0}..{y1}]  {x1-x0+1}×{h}")
print()

# 头部：取角色上部 1/3
head_y1 = y0 + h // 3
print(f"头部区域（上 1/3）: y[{y0}..{head_y1}]")
print()
print("逐行剖面（只列头部）:")
print("  行   不透明像素  肤色(亮)  暗(<70)  说明")
print("  " + "-" * 58)

rows = []
for y in range(y0, head_y1 + 1):
    m = opaque[y]
    n_op = int(m.sum())
    lm = lum[y][m]
    n_skin = int((lm > 120).sum())
    n_dark = int((lm < 70).sum())
    rows.append((y, n_op, n_skin, n_dark))

# 只打印有内容的行，并对暗带做标记
max_dark = max(r[3] for r in rows) or 1
for y, n_op, n_skin, n_dark in rows:
    tag = ""
    if n_dark >= max_dark * 0.55:
        tag = "◀ 暗带（眼/眉/嘴候选）"
    bar = "#" * int(round(n_op / 2))
    print(f"  {y:>3}   {n_op:>3}      {n_skin:>3}    {n_dark:>3}   {bar} {tag}")

print()

# 暗带分组：连续的"暗像素多"的行归为一组
groups = []
cur = []
thr = max(3, max_dark * 0.35)
for y, n_op, n_skin, n_dark in rows:
    if n_dark >= thr:
        cur.append(y)
    else:
        if cur:
            groups.append((cur[0], cur[-1]))
            cur = []
if cur:
    groups.append((cur[0], cur[-1]))

print(f"暗带分组（阈值 暗像素≥{thr:.0f}）:")
labels = ["眉毛/上眼线", "眼睛", "鼻/嘴", "下颌阴影"]
for i, (gy0, gy1) in enumerate(groups):
    name = labels[i] if i < len(labels) else f"第{i+1}条"
    mid = (gy0 + gy1) / 2
    # 该带内暗像素的横向范围（取整组并集）
    xs_dark = []
    for y in range(gy0, gy1 + 1):
        row_dark = np.where((lum[y] < 70) & opaque[y])[0]
        if len(row_dark):
            xs_dark.extend([int(row_dark.min()), int(row_dark.max())])
    span = f"x[{min(xs_dark)}..{max(xs_dark)}]" if xs_dark else "x[--]"
    print(f"  {name:<12} y[{gy0}..{gy1}]  高 {gy1-gy0+1}px  中心 y={mid:.0f}  {span}")

print()
print("⇒ 结论（这些数字决定动画能做到什么程度）:")
for i, (gy0, gy1) in enumerate(groups):
    name = labels[i] if i < len(labels) else f"第{i+1}条"
    hh = gy1 - gy0 + 1
    if hh <= 3:
        print(f"   {name}: 只有 {hh}px 高 —— 能做整行替换（眨眼/闭眼），做不出渐变式表情")
    elif hh <= 7:
        print(f"   {name}: {hh}px 高 —— 眨眼与口型开合都可行")
    else:
        print(f"   {name}: {hh}px 高 —— 偏大，多半是头发/阴影混进这条带，需要按 x 再分左右")

# 脸的左右边界（用肤色列）
head = np.zeros_like(opaque, dtype=bool)
head[y0:head_y1 + 1] = True
skin_cols = ((lum > 120) & opaque & head).sum(axis=0)
sc = np.where(skin_cols >= 3)[0]
if len(sc):
    print()
    print(f"   脸部横向范围（肤色列，≥3 行有肤色）: x[{sc.min()}..{sc.max()}]  宽 {sc.max()-sc.min()+1}px")
