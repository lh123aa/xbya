# -*- coding: utf-8 -*-
r"""把眼睛区域放大 16 倍并打印像素分类图，**用像素说话**。

前面几轮都在"猜眼睛边框在哪"，结果反复出错（暗带法 → 连通块法 → 纯色填充）。
这次直接打印：每个像素标成 S(肤色)/D(暗=眼或发)/_(透明)，一眼就能看出
"眼部皮肤到底到哪一列为止"。
"""
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[4]
OUT = ROOT / "docs" / "agent" / "evidence" / "p5"
im = Image.open(OUT / "_pet_variant_D.png").convert("RGBA")
a = np.asarray(im).astype(np.float64)
alpha = a[:, :, 3]
rgb = a[:, :, :3]
lum = 0.299 * rgb[:, :, 0] + 0.587 * rgb[:, :, 1] + 0.114 * rgb[:, :, 2]

X0, X1 = 44, 92
Y0, Y1 = 38, 56

print(f"像素分类图  x[{X0}..{X1}] y[{Y0}..{Y1}]")
print("  S=肤色(亮且暖)  d=中暗  D=很暗(眼/发)  .=透明")
print()
hdr = "     " + "".join(str(x % 10) for x in range(X0, X1 + 1))
print(hdr)
hdr2 = "     " + "".join(
    (str(x // 10 % 10) if (x % 10 == 0) else " ") for x in range(X0, X1 + 1))
print(hdr2)
for y in range(Y0, Y1 + 1):
    row = []
    for x in range(X0, X1 + 1):
        if alpha[y, x] < 100:
            row.append(".")
        else:
            R, G, B = rgb[y, x]
            L = lum[y, x]
            warm = R - B
            if L > 150 and warm > 30:
                row.append("S")
            elif L > 75:
                row.append("d")
            else:
                row.append("D")
    print(f"  {y:>3}  " + "".join(row))

print()
print("每行的肤色列范围:")
for y in range(Y0, Y1 + 1):
    cols = [x for x in range(X0, X1 + 1)
            if alpha[y, x] >= 100 and lum[y, x] > 150 and (rgb[y, x][0] - rgb[y, x][2]) > 30]
    if cols:
        print(f"  y={y:>3}  肤色 x[{min(cols)}..{max(cols)}]  ({len(cols)} 列)")

# 放大图
crop = im.crop((X0, Y0, X1 + 1, Y1 + 1))
Z = 16
big = crop.resize((crop.size[0] * Z, crop.size[1] * Z), Image.NEAREST)
canvas = Image.new("RGB", (big.size[0], big.size[1]), (255, 255, 255))
canvas.paste(big, (0, 0), big)
from PIL import ImageDraw
d = ImageDraw.Draw(canvas)
for i, x in enumerate(range(X0, X1 + 1)):
    d.line([(i * Z, 0), (i * Z, big.size[1])], fill=(210, 210, 225), width=1)
    if x % 5 == 0:
        d.text((i * Z + 2, 2), str(x), fill=(90, 90, 120))
for j, y in enumerate(range(Y0, Y1 + 1)):
    d.line([(0, j * Z), (big.size[0], j * Z)], fill=(210, 210, 225), width=1)
    d.text((2, j * Z + 2), str(y), fill=(90, 90, 120))
p = OUT / "_eye_pixel_zoom.png"
canvas.save(p)
print()
print(f"16 倍放大图: {p.name}  ({big.size[0]}×{big.size[1]})")
