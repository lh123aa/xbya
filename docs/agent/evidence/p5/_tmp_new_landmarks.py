# -*- coding: utf-8 -*-
r"""量新形象（A 构图，128×128）的五官像素位置 —— 动画的变换框全靠这几个数。

流程与上一张图完全一致（那一轮踩过的坑都已固化在里面）：
  1. 先打印**像素分类图**（S=肤色/d=中暗/D=很暗/.=透明），用像素说话，不靠肉眼估
  2. 从"脸内部"的暗块找眼睛（先在肤色连通块内找，避免把头发算进来）
  3. 找出肤色区域的横向收窄处当**脖子**（上一版用"全图最窄行"是错的，撞到了腰带）
"""
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[4]
OUT = ROOT / "docs" / "agent" / "evidence" / "p5"
im = Image.open(OUT / "_comp_A_取到胸口.png").convert("RGBA")
a = np.asarray(im).astype(np.float64)
alpha = a[:, :, 3]
rgb = a[:, :, :3]
lum = 0.299 * rgb[:, :, 0] + 0.587 * rgb[:, :, 1] + 0.114 * rgb[:, :, 2]
op = alpha > 128

ys, xs = np.where(op)
print(f"A 构图角色包围盒 x[{xs.min()}..{xs.max()}] y[{ys.min()}..{ys.max()}]  "
      f"{xs.max()-xs.min()+1}×{ys.max()-ys.min()+1}")

Y0, Y1 = 30, 95
X0, X1 = 30, 100
print()
print(f"像素分类图  x[{X0}..{X1}] y[{Y0}..{Y1}]")
print("  S=肤色(亮且暖)  d=中暗  D=很暗(眼/发)  .=透明")
print("     " + "".join(str(x % 10) for x in range(X0, X1 + 1)))
for y in range(Y0, Y1 + 1):
    row = []
    for x in range(X0, X1 + 1):
        if alpha[y, x] < 100:
            row.append(".")
        else:
            R, G, B = rgb[y, x]
            L = lum[y, x]
            if L > 150 and (R - B) > 30:
                row.append("S")
            elif L > 75:
                row.append("d")
            else:
                row.append("D")
    print(f"  {y:>3} " + "".join(row))

print()
print("肤色行的横向范围（脸/脖子/胸口）:")
for y in range(Y0, Y1 + 1, 2):
    cols = [x for x in range(X0, X1 + 1)
            if alpha[y, x] >= 100 and lum[y, x] > 150 and (rgb[y, x][0] - rgb[y, x][2]) > 30]
    if cols:
        print(f"  y={y:>3}  x[{min(cols)}..{max(cols)}]  宽 {max(cols)-min(cols)+1}")
