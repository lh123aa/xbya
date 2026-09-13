# -*- coding: utf-8 -*-
r"""打印新立绘的**宽度剖面**，用真实结构定"脖子在哪、脸在哪"。

上一版用"最窄行 = 脖子"是错的：这张图**白色连衣裙的窄腰带**比脖子还窄，
于是判据撞在腰上（y=122 行宽 245px）。这类"判据指错了对象"的错必须先看图，
所以这里把逐行宽度打出来，让结构自己说话。
"""
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[4]
OUT = ROOT / "docs" / "agent" / "evidence" / "p5"
cut = Image.open(OUT / "_new_cutout.png").convert("RGBA")
a = np.asarray(cut)
op = a[:, :, 3] > 16
H, W = op.shape
ys, xs = np.where(op)
y0, y1 = int(ys.min()), int(ys.max())
print(f"抠图后角色 y[{y0}..{y1}]  高 {y1-y0+1}")
print()
print("逐行宽度剖面（每 12 行取一行，避免刷屏）:")
print("   y     占y比例   行宽   左右边界          图示")
print("  " + "-" * 66)
for y in range(y0, y1 + 1, 12):
    cols = np.where(op[y])[0]
    if not len(cols):
        continue
    n = len(cols)
    rel = (y - y0) / (y1 - y0)
    bar = "#" * int(round(n / 14))
    print(f"  {y:>4}   {rel:>6.1%}   {n:>4}   x[{cols.min():>3}..{cols.max():>3}]   {bar}")

print()
# 用"肤色"找脸：肤色像素（暖色，R>G>B 且够亮）
r_, g_, b_ = a[:, :, 0].astype(int), a[:, :, 1].astype(int), a[:, :, 2].astype(int)
skin = op & (r_ > 150) & (r_ - b_ > 25) & (r_ - g_ > 10)
print("肤色像素的逐行分布（每 8 行）—— 脸和手臂都是肤色，但脸在最上面:")
print("   y     肤色像素   横向范围")
print("  " + "-" * 44)
first_skin = None
for y in range(y0, y1 + 1, 8):
    cols = np.where(skin[y])[0]
    if not len(cols):
        continue
    if first_skin is None:
        first_skin = y
    print(f"  {y:>4}   {len(cols):>5}     x[{cols.min()}..{cols.max()}]")

print()
# 脸 = 最上面一段连续肤色；脖子结束处肤色会断（被头发/衣服遮）
print("判读：肤色从 y=%s 开始（额头）。" % first_skin)
# 找第一段连续肤色行的终点
run_end = first_skin
for y in range(first_skin, y1 + 1):
    if skin[y].sum() >= 3:
        run_end = y
    else:
        # 允许 6 行的空隙（睫毛/嘴线）
        if y - run_end > 6:
            break
print(f"      第一段连续肤色到 y={run_end}（含睫毛/嘴的短空隙）")
print(f"      ⇒ 脸部高度约 {run_end - first_skin + 1}px，占全身 "
      f"{(run_end - first_skin + 1) / (y1 - y0 + 1):.1%}")

print()
# 输入 scipy 或 matplotlib 都不用；直接给 DPI 友好的标注图
Z = 1.0
img = cut.copy()
d = ImageDraw.Draw(img)
d.line([(0, first_skin), (W, first_skin)], fill=(0, 200, 0), width=3)
d.text((6, first_skin + 4), f"skin top y={first_skin}", fill=(0, 200, 0))
d.line([(0, run_end), (W, run_end)], fill=(255, 0, 0), width=3)
d.text((6, run_end + 4), f"skin run end y={run_end}", fill=(255, 0, 0))
p = OUT / "_new_structure_annot.png"
img.save(p)
print(f"标注图: {p.name}  （绿线=肤色起始，红线=第一段肤色结束）")
