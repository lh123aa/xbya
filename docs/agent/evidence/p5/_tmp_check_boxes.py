# -*- coding: utf-8 -*-
r"""把动画要用的关键框画在 6 倍放大图上核对。

为什么要先核对：眼睛/嘴的框错 2px，眨眼的弧线就会画到脸上、口型就会切到鼻子。
后面 208 帧全部依赖这几个框，**先花 10 秒看一眼比事后调 200 帧便宜**。
"""
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[4]
OUT = ROOT / "docs" / "agent" / "evidence" / "p5"

HEAD = (20, 12, 107, 70)
EYE_L = (52, 43, 67, 52)
EYE_R = (68, 43, 83, 52)
MOUTH = (55, 58, 72, 68)
NECK_Y = 72

# 每个框配一个"从 (page, py) 指向框中心"的小箭头
PAGE = 6          # 放大倍数
BOXES = [
    ("HEAD", HEAD, (255, 160, 0)),
    ("EYE_L", EYE_L, (255, 0, 0)),
    ("EYE_R", EYE_R, (255, 0, 0)),
    ("MOUTH", MOUTH, (0, 200, 60)),
    ("NECK_Y", (0, NECK_Y, 127, NECK_Y), (0, 120, 255)),
]

im = Image.open(OUT / "_pet_variant_D.png").convert("RGBA")
W, H = im.size
big = im.resize((W * PAGE, H * PAGE), Image.NEAREST)
canvas = Image.new("RGB", (W * PAGE, H * PAGE), (248, 248, 250))
canvas.paste(big, (0, 0), big)
d = ImageDraw.Draw(canvas)

for gy in range(0, H, 10):
    d.line([(0, gy * PAGE), (W * PAGE, gy * PAGE)], fill=(226, 226, 234), width=1)
    d.text((2, gy * PAGE + 1), str(gy), fill=(130, 130, 150))
for gx in range(0, W, 10):
    d.line([(gx * PAGE, 0), (gx * PAGE, H * PAGE)], fill=(226, 226, 234), width=1)
    d.text((gx * PAGE + 2, 2), str(gx), fill=(130, 130, 150))

for name, (x0, y0, x1, y1), col in BOXES:
    d.rectangle([x0 * PAGE, y0 * PAGE, x1 * PAGE + PAGE - 1, y1 * PAGE + PAGE - 1],
                outline=col, width=2)
    d.text((x0 * PAGE + 3, max(0, y0 * PAGE - 14)), name, fill=col)

p = OUT / "_frame_landmark_check.png"
canvas.save(p)
print(f"核对图已写出: {p.name}  （{W*PAGE}×{H*PAGE}，6 倍放大）")
print()
print("框：")
for name, (x0, y0, x1, y1), _ in BOXES:
    print(f"  {name:<7} x[{x0}..{x1}] y[{y0}..{y1}]   {x1-x0+1}×{y1-y0+1}")
print()
print("请核对：红框是否正好套住两只眼睛、绿框是否套住嘴、橙框是否只含头部、")
print("        蓝线是否在脖子位置（头身分界）。")
