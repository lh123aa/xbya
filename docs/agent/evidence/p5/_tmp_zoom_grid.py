# -*- coding: utf-8 -*-
r"""把指定区域放大 + 打 10px 网格，供**人眼逐格读数**。

为什么还是要人眼读：本项目的立绘是冷调肤色 + 粗眼线，自动阈值在
"眼线/眉毛/眼窝阴影"三者之间分不干净（上一版把眉毛和颧骨都框进去了）。
在这个尺度上，"读格"比"调阈值"可靠 —— 前提是格子够细、图够大。

用法:
    python _tmp_zoom_grid.py                      # 输出默认两个区块
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[4]
OUT = ROOT / "docs" / "agent" / "evidence" / "p5"

cut = Image.open(OUT / "_new_cutout.png").convert("RGBA")
bg = Image.new("RGB", cut.size, (250, 250, 252))
bg.paste(cut, (0, 0), cut)

try:
    F = ImageFont.truetype("C:/Windows/Fonts/consola.ttf", 22)
except Exception:
    F = ImageFont.load_default()

# (标题, 裁切框, 放大倍数)
PANELS = [
    ("eye band  (x 170..380, y 140..215)", (170, 140, 380, 215), 6),
    ("mouth band (x 195..365, y 220..310)", (195, 220, 365, 310), 6),
]
STEP = 10          # 网格步长（原图 px）

tiles = []
for title, box, z in PANELS:
    crop = bg.crop(box)
    big = crop.resize((crop.size[0] * z, crop.size[1] * z), Image.LANCZOS)
    d = ImageDraw.Draw(big)
    x0, y0 = box[0], box[1]
    for gx in range(x0 - x0 % STEP, box[2] + 1, STEP):
        X = (gx - x0) * z
        major = (gx % 50 == 0)
        d.line([(X, 0), (X, big.size[1])],
               fill=(255, 60, 60) if major else (120, 190, 255), width=2 if major else 1)
        if major:
            d.text((X + 3, 3), str(gx), fill=(210, 0, 0), font=F)
    for gy in range(y0 - y0 % STEP, box[3] + 1, STEP):
        Y = (gy - y0) * z
        major = (gy % 50 == 0)
        d.line([(0, Y), (big.size[0], Y)],
               fill=(255, 60, 60) if major else (120, 190, 255), width=2 if major else 1)
        if major:
            d.text((3, Y + 3), str(gy), fill=(210, 0, 0), font=F)
    d.rectangle([0, 0, big.size[0] - 1, big.size[1] - 1], outline=(0, 0, 0), width=2)
    tiles.append((title, big))

W = max(t.size[0] for _, t in tiles)
H = sum(t.size[1] for _, t in tiles) + 40 * len(tiles)
sheet = Image.new("RGB", (W, H), (232, 234, 240))
d = ImageDraw.Draw(sheet)
y = 0
for title, t in tiles:
    d.text((6, y + 8), f"{title}    6x,  blue=10px  red=50px",
           fill=(20, 20, 30), font=F)
    y += 40
    sheet.paste(t, (0, y))
    y += t.size[1]
p = OUT / "_zoom_ruler.png"
sheet.save(p)
print(f"{p.name}  {sheet.size[0]}×{sheet.size[1]}")
