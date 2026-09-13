# -*- coding: utf-8 -*-
r"""按这张图**真实的解剖比例**做三种裁切，并排对照，再用眼睛选一个。

上一版测量把"脸"算成 412px 高（占全身 35.6%）——那是**脸+脖子+胸口**的肤色
连成一片导致的。真实的头在这张图里约 y[0..290]、脸约 y[110..290]。
所以这里不再靠自动判据，直接**按已知结构手工定裁切线**，出图看效果。

裁切候选（都缩到 128 画布，角色贴底）:
  A 取到胸口  y[0..470]    —— 和上一轮半身像最接近，脸最大
  B 取到腰    y[0..700]    —— 能看见腰带，身形更完整，脸略小
  C 取到大腿  y[0..900]    —— 更完整，脸明显小
  D 全身      y[0..1157]   —— 最完整，脸最小（几乎认不出）
"""
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[4]
OUT = ROOT / "docs" / "agent" / "evidence" / "p5"
cut = Image.open(OUT / "_new_cutout.png").convert("RGBA")
W0, H0 = cut.size
print(f"抠图 {W0}×{H0}")

# 先算出角色在抠图里的紧包围盒（去掉背景残影）
a = np.asarray(cut)
op = a[:, :, 3] > 64          # 阈值放宽到 64，避开半透明残影
ys, xs = np.where(op)
y0, y1, x0, x1 = int(ys.min()), int(ys.max()), int(xs.min()), int(xs.max())
print(f"紧包围盒（alpha>64）x[{x0}..{x1}] y[{y0}..{y1}]  "
      f"{x1-x0+1}×{y1-y0+1}")
ch = y1 - y0 + 1

CROPS = [
    ("A_取到胸口", 0.405),
    ("B_取到腰",   0.605),
    ("C_取到大腿", 0.78),
    ("D_全身",     1.00),
]

TARGET = 128
results = []
tiles = []
for name, frac in CROPS:
    keep = int(ch * frac)
    bust = cut.crop((x0, y0, x1 + 1, y0 + keep))
    bw, bh = bust.size
    # 先按高适配 128，若宽超了再按宽适配
    scale = TARGET / bh
    if bw * scale > TARGET:
        scale = TARGET / bw
    w, h = max(1, int(round(bw * scale))), max(1, int(round(bh * scale)))
    s = bust.resize((w, h), Image.LANCZOS)
    cv = Image.new("RGBA", (TARGET, TARGET), (0, 0, 0, 0))
    ox = (TARGET - w) // 2
    oy = TARGET - 1 - h              # 贴底
    cv.alpha_composite(s, (ox, max(0, oy)))
    p = OUT / f"_comp_{name}.png"
    cv.save(p)
    # 量结果里的角色包围盒与"头部"（取上部 1/3 之前的脸区）
    aa = np.asarray(cv)
    yy, xx = np.where(aa[:, :, 3] > 16)
    ry0, ry1, rx0, rx1 = int(yy.min()), int(yy.max()), int(xx.min()), int(xx.max())
    # 脸：原始脸区 y[110..290] 映射到结果
    face_src_h = 290 - 110
    face_h = face_src_h * scale
    # 眼睛所在（原图约 y=185）映射
    eye_res = (185 - y0) * scale + max(0, oy)
    results.append((name, frac, f"{w}×{h}", f"y[{ry0}..{ry1}]", f"{face_h:.0f}",
                    f"{eye_res:.0f}", p.name))
    tiles.append((name, cv, face_h, eye_res))

print()
print("  构图         原高比例   缩放后    角色y范围      脸高   眼睛y   文件")
print("  " + "-" * 76)
for name, frac, sz, yr, fh, ey, fn in results:
    print(f"  {name:<12} {frac:>5.0%}   {sz:<9} {yr:<13} {fh:>4}px  {ey:>4}    {fn}")

print()
# ---- 并排对照图（4 倍放大 + 真实尺寸）----
Z = 4
TILE = TARGET * Z
PAD = 10
LABEL = 20
CW = len(tiles) * (TILE + PAD) + PAD
CH = LABEL * 2 + TILE + 140
canvas = Image.new("RGB", (CW, CH), (246, 246, 250))
d = ImageDraw.Draw(canvas)
d.text((PAD, 4), "新立绘四种裁切对照（上=4倍放大，下=真实 127px 桌面观感）",
       fill=(24, 24, 34))
for i, (name, cv, fh, ey) in enumerate(tiles):
    x = PAD + i * (TILE + PAD)
    tile = Image.new("RGB", (TILE, TILE), (255, 255, 255))
    big = cv.resize((TILE, TILE), Image.NEAREST)
    tile.paste(big, (0, 0), big)
    canvas.paste(tile, (x, LABEL))
    d.text((x + 4, LABEL + TILE + 3), f"{name}  脸高约 {fh:.0f}px", fill=(70, 70, 90))
    real = cv.resize((127, 127), Image.LANCZOS)
    band = Image.new("RGB", (TILE, 135), (234, 236, 242))
    band.paste(real, ((TILE - 127) // 2, 4), real)
    canvas.paste(band, (x, LABEL * 2 + TILE))
p = OUT / "_new_crop_compare.png"
canvas.save(p)
print(f"对照图: {p.name}  ({CW}×{CH})")
print()
print("判断要点：脸高 ≥ 30px 时五官清楚；眼睛的 y 位置决定后面眨眼框画在哪。")
