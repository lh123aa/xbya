# -*- coding: utf-8 -*-
r"""成品预览：把新角色放到模拟桌面上，**同时给放大版和真实尺寸**。

为什么要两种尺寸：
  · 真实 127px 版是你实际会看到的观感（决定"值不值得用"）
  · 4 倍放大版让五官看得清（决定"像不像她"）
只给放大版会高估效果，只给真实尺寸又看不清细节。
"""
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[4]
OUT = ROOT / "docs" / "agent" / "evidence" / "p5"
SPR = ROOT / "resources" / "sprites" / "xinya"

# 取 idle 的四个不同相位 + talk 的两个开口帧
PICKS = [
    ("idle/001", "静息（呼吸相位 1）"),
    ("idle/008", "静息（眨眼）"),
    ("idle/016", "静息（呼吸相位 2）"),
    ("listen/006", "聆听（歪头）"),
    ("talk/010", "说话（开口）"),
    ("sleep/012", "睡眠（闭眼）"),
]

Z = 4
TILE = 128 * Z
LABEL_H = 18
PAD = 8

frames = []
for rel, label in PICKS:
    p = SPR / f"{rel}.png"
    if not p.is_file():
        alt = SPR / rel.split("/")[0] / f"frame_{int(rel.split('/')[1]):03d}.png"
        p = alt
    frames.append((Image.open(p).convert("RGBA"), label))

W = len(frames) * (TILE + PAD) + PAD
H = LABEL_H * 2 + TILE + 150
canvas = Image.new("RGB", (W, H), (246, 246, 250))
d = ImageDraw.Draw(canvas)
d.text((PAD, 4), "新角色「欣雅」—— 放大 4 倍（上排=128px 原图放大，下排=真实 127px 桌面观感）",
       fill=(24, 24, 34))

for i, (im, label) in enumerate(frames):
    x = PAD + i * (TILE + PAD)
    tile = Image.new("RGB", (TILE, TILE), (255, 255, 255))
    big = im.resize((TILE, TILE), Image.NEAREST)
    tile.paste(big, (0, 0), big)
    canvas.paste(tile, (x, LABEL_H))
    d.text((x + 3, LABEL_H + TILE + 2), label, fill=(70, 70, 90))
    # 真实尺寸：127，贴在同色背景上
    real = im.resize((127, 127), Image.LANCZOS)
    band = Image.new("RGB", (TILE, 127 + 8), (236, 238, 244))
    band.paste(real, ((TILE - 127) // 2, 4), real)
    canvas.paste(band, (x, LABEL_H * 2 + TILE + 16))
    if i == 0:
        d.text((x + 3, LABEL_H * 2 + TILE + 16 + 131), "↑ 真实尺寸不再放大", fill=(120, 120, 140))

p = OUT / "_final_preview.png"
canvas.save(p)
print(f"成品预览: {p.name}  ({W}×{H})")
print()
print("帧清单:")
for rel, label in PICKS:
    print(f"  {label:<20} {rel}")
