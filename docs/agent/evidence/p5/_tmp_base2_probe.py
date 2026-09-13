# -*- coding: utf-8 -*-
r"""在**真实生成的底图**上重新定位五官。

上一版的坐标是从"窗口模拟用的 A 构图"（128×114 却被当成 128×128 摆放）推来的，
底图实际是 128×114 —— 坐标系不同，套过去必然错位。
这里直接对 `_base_xinya2.png` 做像素分析，让坐标出自**同一张图**。

判据（吸取前几轮的错）：
  · 形态学上，脸 = 被头发包围的**肤色连通区**；头发是暗的但不是判据（会连成一片）
  · 眼睛 = 脸内部的暗块（在肤色区**内部**找，避免把头发算进来）
  · 脖子 = 肤色横向宽度**从"脸宽"突增到"肩宽"**的那一行（不是全图最窄行 —— 那个会撞到腰带）
"""
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[4]
OUT = ROOT / "docs" / "agent" / "evidence" / "p5"
im = Image.open(OUT / "_base_xinya2.png").convert("RGBA")
a = np.asarray(im).astype(np.float64)
alpha, rgb = a[:, :, 3], a[:, :, :3]
lum = 0.299 * rgb[:, :, 0] + 0.587 * rgb[:, :, 1] + 0.114 * rgb[:, :, 2]
op = alpha > 128
H, W = op.shape
print(f"底图 {W}×{H}")

ys, xs = np.where(op)
print(f"角色包围盒 x[{xs.min()}..{xs.max()}] y[{ys.min()}..{ys.max()}]")

# 肤色：亮且暖
skin = op & (lum > 150) & ((rgb[:, :, 0] - rgb[:, :, 2]) > 30)
print()
print("肤色逐行横向范围（每 2 行）:")
print("   y     范围            宽   连续段数")
print("  " + "-" * 50)
rows = []
for y in range(H):
    cols = np.where(skin[y])[0]
    if not len(cols):
        continue
    # 连续段数：用来分辨"脸(1段)"与"脸+两臂(3段)"
    segs = 1 + int((np.diff(cols) > 2).sum())
    rows.append((y, int(cols.min()), int(cols.max()), len(cols), segs))
    if y % 2 == 0:
        print(f"  {y:>3}   x[{cols.min():>3}..{cols.max():>3}]   {len(cols):>3}   {segs}")

# 脖子判据：段数从 1 变成 >1 的第一行（手臂出现），再回退几行到下巴
multi = [r for r in rows if r[4] > 1]
first_multi = multi[0][0] if multi else None
print()
print(f"段数首次 >1 的行: y={first_multi}（这一行开始同时出现脸/脖子与手臂）")

# 脸：上部那段"只有 1 段肤色"的区域里，最宽的一批行
single = [r for r in rows if r[4] == 1]
if single:
    top = single[0][0]
    # 脸的底部：单段肤色里宽度开始明显 > 脸宽的（胸口）
    widths = np.array([r[3] for r in single])
    wmax = widths.max()
    face_rows = [r for r in single if r[3] <= wmax * 1.25]
    fy0, fy1 = face_rows[0][0], face_rows[-1][0]
    print(f"单段肤色区 y[{top}..{single[-1][0]}]，最宽 {wmax}px")
    print(f"⇒ 脸/脖子区 y[{fy0}..{fy1}]")

# 脸内部的暗块（眼睛/嘴）
print()
print("脸区域内暗块（lum<100）逐行:")
for y in range(max(0, fy0), min(H, fy1 + 20)):
    cols = np.where(op[y] & (lum[y] < 100))[0]
    # 只保留被肤色包围的（左右各 1px 内有肤色）
    keep = [c for c in cols
            if (c > 0 and skin[y, c - 1]) or (c < W - 1 and skin[y, c + 1])]
    if keep:
        print(f"  y={y:>3}  暗块 x[{min(keep)}..{max(keep)}]  {len(keep)} 个")

# 输出放大核对图
Z = 6
big = im.resize((W * Z, H * Z), Image.NEAREST)
cv = Image.new("RGB", (W * Z, H * Z), (248, 248, 250))
cv.paste(big, (0, 0), big)
d = ImageDraw.Draw(cv)
for gy in range(0, H, 10):
    d.line([(0, gy * Z), (W * Z, gy * Z)], fill=(224, 224, 232))
    d.text((2, gy * Z + 1), str(gy), fill=(130, 130, 150))
for gx in range(0, W, 10):
    d.line([(gx * Z, 0), (gx * Z, H * Z)], fill=(224, 224, 232))
    d.text((gx * Z + 2, 2), str(gx), fill=(130, 130, 150))
p = OUT / "_base_xinya2_zoom.png"
cv.save(p)
print()
print(f"放大核对图: {p.name}  ({W*Z}×{H*Z})")
