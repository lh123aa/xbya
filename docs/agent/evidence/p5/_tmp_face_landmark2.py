# -*- coding: utf-8 -*-
r"""把选定变体放大并叠上坐标网格，用眼睛定五官位置。

上一版用"暗像素"找眼睛是错的（判据错）：**头发本身就是暗的**，
所以整条头部都被标成暗带，眼睛淹没在里面。

正确的做法分两步：
  1. **先把脸和头发分开**：脸是亮的连通区，头发是暗的。
     脸 = 头部范围内最大的"亮像素连通块"（4 邻接、面积最大）。
  2. **再在脸内部找暗像素**：脸内部的暗块就是眼睛/眉毛/嘴 —— 这一步才成立，
     因为已经把头发排除在搜索范围之外了。

同时输出一张 8 倍放大的标注图，便于人眼核对机器给的框对不对。
"""
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[4]
OUT = ROOT / "docs" / "agent" / "evidence" / "p5"
SPR = OUT / "_pet_variant_D.png"

im = Image.open(SPR).convert("RGBA")
a = np.asarray(im).astype(np.float64)
alpha = a[:, :, 3]
rgb = a[:, :, :3]
lum = 0.299 * rgb[:, :, 0] + 0.587 * rgb[:, :, 1] + 0.114 * rgb[:, :, 2]
opaque = alpha > 128

ys, xs = np.where(opaque)
y0, y1, x0, x1 = int(ys.min()), int(ys.max()), int(xs.min()), int(xs.max())
print(f"角色包围盒: x[{x0}..{x1}] y[{y0}..{y1}]  {x1-x0+1}×{y1-y0+1}")

# ---- 步骤 1：脸 = 亮像素连通块（4 邻接，BFS）----
bright = opaque & (lum > 115)
lab = np.zeros(bright.shape, dtype=np.int32)
cur = 0
best = (0, None)
H, W = bright.shape
for sy in range(H):
    for sx in range(W):
        if bright[sy, sx] and lab[sy, sx] == 0:
            cur += 1
            stack = [(sy, sx)]
            lab[sy, sx] = cur
            pix = []
            while stack:
                cy, cx = stack.pop()
                pix.append((cy, cx))
                for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    ny, nx = cy + dy, cx + dx
                    if 0 <= ny < H and 0 <= nx < W and bright[ny, nx] and lab[ny, nx] == 0:
                        lab[ny, nx] = cur
                        stack.append((ny, nx))
            if len(pix) > best[0]:
                best = (len(pix), pix)

n_face, face_pix = best
if face_pix is None:
    print("没找到亮连通块")
    raise SystemExit(1)
fy = [p[0] for p in face_pix]
fx = [p[1] for p in face_pix]
FACE = (min(fx), min(fy), max(fx), max(fy))
print(f"脸部亮连通块: {n_face} 像素  x[{FACE[0]}..{FACE[2]}] y[{FACE[1]}..{FACE[3]}] "
      f" {FACE[2]-FACE[0]+1}×{FACE[3]-FACE[1]+1}（可能是脸+脖子+裙子连成一片）")

# ---- 步骤 2：脸内部找暗块（眼睛/嘴）----
# 限制在"头部"：角色上部 40%，且 x 在脸部连通块范围内
head_bottom = y0 + int((y1 - y0 + 1) * 0.40)
inner_dark = np.zeros_like(opaque)
inner_dark[:, :] = (lum < 90) & opaque
# 只保留被亮像素在左右两侧包围的暗像素（即"脸上开的暗洞"）
face_slice = inner_dark[y0:head_bottom, FACE[0]:FACE[2] + 1]
print(f"头部搜索窗: y[{y0}..{head_bottom-1}] x[{FACE[0]}..{FACE[2]}]")
print(f"  该窗内暗像素 {int(face_slice.sum())} 个")

# 逐行剖面（这次真的是脸内部了）
print()
print("脸内部逐行暗像素剖面:")
print("  行   暗像素  横向范围")
print("  " + "-" * 40)
rows_info = []
for y in range(y0, head_bottom):
    cols = np.where(inner_dark[y, FACE[0]:FACE[2] + 1])[0]
    if len(cols):
        rows_info.append((y, len(cols), int(cols.min()) + FACE[0], int(cols.max()) + FACE[0]))
for y, n, c0, c1 in rows_info:
    print(f"  {y:>3}   {n:>3}    x[{c0}..{c1}]  " + "=" * n)

# ---- 输出放大标注图 ----
Z = 8
big = im.resize((W * Z, H * Z), Image.NEAREST)
canvas = Image.new("RGB", (W * Z, H * Z), (250, 250, 252))
canvas.paste(big, (0, 0), big)
d = ImageDraw.Draw(canvas)
for gx in range(0, W, 10):
    d.line([(gx * Z, 0), (gx * Z, H * Z)], fill=(220, 220, 230), width=1)
    d.text((gx * Z + 2, 2), str(gx), fill=(120, 120, 140))
for gy in range(0, H, 10):
    d.line([(0, gy * Z), (W * Z, gy * Z)], fill=(220, 220, 230), width=1)
    d.text((2, gy * Z + 2), str(gy), fill=(120, 120, 140))
# 框：角色包围盒（绿）、脸部亮连通块（蓝）
d.rectangle([x0 * Z, y0 * Z, x1 * Z + Z - 1, y1 * Z + Z - 1], outline=(0, 170, 0), width=2)
d.rectangle([FACE[0] * Z, FACE[1] * Z, FACE[2] * Z + Z - 1, FACE[3] * Z + Z - 1],
            outline=(30, 90, 220), width=2)
p = OUT / "_face_landmark_zoom.png"
canvas.save(p)
print()
print(f"标注图（8 倍放大 + 10px 网格）：{p.name}")
print("  绿框=角色包围盒，蓝框=脸部亮连通块")
