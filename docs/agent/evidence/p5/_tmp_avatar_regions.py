# -*- coding: utf-8 -*-
r"""先**看清楚我取的框落在哪**，再谈配色。

上一版直接把区域框取到的中位色当成肤色，得出 `#5D5A3D`（橄榄绿）——
那显然不是皮肤。原因是区域框**落在背景植物上了**，不是落在人身上。

教训与"判据要贴着事实写"同族：**取色之前必须先确认框的位置正确**，
否则量出来的数是"背景的颜色"，而我却把它当成"人的颜色"去建模。
这类错误不会报错，只会安静地产出一个绿色的脸。

做法：把每个框画到图上，另外裁出各框内容单独存成小图，
再打印每个框的**色相分布**——皮肤是暖色低饱和，植物是绿色高饱和，
色相一看就能判断框落对没有。
"""
import colorsys
import io
import os
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

SRC = Path(os.environ["USERPROFILE"]) / ".dsh" / "attachments" / "v1" / "objects" / "55" \
      / "556fecf5ca2faa42dc6cb5ef8cca579dded31d7213778aba4f4267df25725394"
OUTDIR = Path(__file__).resolve().parents[4] / "docs" / "agent" / "evidence" / "p5"
OUTDIR.mkdir(parents=True, exist_ok=True)

img = Image.open(SRC).convert("RGB")
W, H = img.size
print(f"源图 {W}x{H}")

# 先按"我看到的构图"重设区域（相对坐标）。
# 依据（看图得出，不再按通用人像比例瞎猜）：
#   · 人脸中心约在 x≈0.50, y≈0.21；脸宽约 0.13W、高约 0.12H
#   · 头发顶部 y≈0.10；两侧垂发到 y≈0.45，x 约 0.28~0.36 / 0.64~0.72
#   · 白裙主体 y≈0.52~0.78，x≈0.32~0.68
#   · 手臂在身侧 x≈0.24~0.30 / 0.70~0.76，y≈0.32~0.40
REGIONS = {
    "face":   (0.445, 0.165, 0.555, 0.225),
    "hair":   (0.30,  0.34,  0.36,  0.44),
    "dress":  (0.40,  0.56,  0.60,  0.72),
    "arm":    (0.255, 0.30,  0.30,  0.38),
    "lips":   (0.478, 0.208, 0.522, 0.226),
    "bg_leaf": (0.02, 0.05,  0.12,  0.15),
}

print()
print("=" * 78)
print("每个框的色相分布（皮肤=暖色低饱和；植物=绿；这是判断框落对没有的判据）")
print("=" * 78)
print()
print("| 框 | 中位色 | HEX | 色相众数 | 饱和度 | 亮度 | 判断 |")
print("|----|--------|-----|---------|--------|------|------|")

crops = {}
for name, (x0, y0, x1, y1) in REGIONS.items():
    box = (int(x0 * W), int(y0 * H), int(x1 * W), int(y1 * H))
    c = img.crop(box)
    crops[name] = (box, c)
    a = np.asarray(c).reshape(-1, 3).astype(np.float64) / 255.0
    med = np.median(a, axis=0)
    hsv = np.array([colorsys.rgb_to_hsv(*p) for p in a[::7]])
    hue_mode = np.median(hsv[:, 0]) * 360
    sat = np.median(hsv[:, 1])
    val = np.median(hsv[:, 2])
    if 20 <= hue_mode <= 45 and sat < 0.45:
        verdict = "偏肤色 ✔"
    elif 60 <= hue_mode <= 160:
        verdict = "**偏绿 —— 可能落在背景植物上**"
    elif sat < 0.15:
        verdict = "近灰白（可能是白裙）"
    else:
        verdict = "其他"
    print(f"| {name} | ({med[0]*255:.0f},{med[1]*255:.0f},{med[2]*255:.0f}) | "
          f"`#{int(med[0]*255):02X}{int(med[1]*255):02X}{int(med[2]*255):02X}` | "
          f"{hue_mode:.0f}° | {sat:.2f} | {val:.2f} | {verdict} |")

print()
# 画框总览图
vis = img.copy()
d = ImageDraw.Draw(vis)
for i, (name, (box, _)) in enumerate(crops.items()):
    d.rectangle(box, outline=(255, 0, 0), width=max(3, W // 300))
    d.text((box[0] + 6, box[1] + 6), name, fill=(255, 0, 0))
vis.save(OUTDIR / "_palette_regions.png")
print(f"带框总览已存：{(OUTDIR / '_palette_regions.png').relative_to(Path(__file__).resolve().parents[4])}")

# 各框内容拼一张联系表（便于肉眼核）
tiles = []
for name, (_, c) in crops.items():
    t = c.resize((140, 140))
    tiles.append((name, t))
sheet = Image.new("RGB", (140 * len(tiles), 160), (255, 255, 255))
d2 = ImageDraw.Draw(sheet)
for i, (name, t) in enumerate(tiles):
    sheet.paste(t, (i * 140, 0))
    d2.text((i * 140 + 4, 142), name, fill=(0, 0, 0))
sheet.save(OUTDIR / "_palette_crops.png")
print(f"各框内容联系表已存：{(OUTDIR / '_palette_crops.png').relative_to(Path(__file__).resolve().parents[4])}")
print()
print("⚠️ 请人工看一眼上面那两张图：**框有没有落在人身上**。")
print("   `face` 那个框必须框住脸（不是树、不是头发），否则取出来的'肤色'就是错的。")
