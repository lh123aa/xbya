# -*- coding: utf-8 -*-
r"""从照片里量出形象要用的配色（而不是我"看着估"）。

为什么必须量：颜色靠眼睛估会偏（显示器、压缩、光照都会影响判断），
而生成模型时这些值会直接变成材质颜色。量出来的数可复现、可复核。

做法：
  1. 把图缩到固定尺寸，按**区域**取样（头发/脸/裙子/背景）——
     区域靠人脸与人体的相对位置估，但**只用来定位，不用来配色**
  2. 每个区域取**中位色**（不是均值：均值会被高光/阴影拉偏）
  3. 同时报该区域的明度分布，便于判断"是不是把阴影当成了主色"

⚠️ 边界：区域框是我按人像比例估的，图上没有标注。若取色看起来不对，
   应该调框重取，而不是硬用。
"""
import io
import os
import sys
from pathlib import Path

import numpy as np
from PIL import Image

SRC = Path(os.environ["USERPROFILE"]) / ".dsh" / "attachments" / "v1" / "objects" / "55" \
      / "556fecf5ca2faa42dc6cb5ef8cca579dded31d7213778aba4f4267df25725394"


def hexof(rgb) -> str:
    return "#%02X%02X%02X" % tuple(int(max(0, min(255, c))) for c in rgb)


def hex_to_rgb01(h: str):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))


img = Image.open(SRC).convert("RGB")
W, H = img.size
print(f"源图：{W}x{H}")
print()

# 按人像比例定区域（相对坐标，0~1）。这是"定位"用的框，不是"配色"判据。
# 依据：常见竖构图半身像，人物居中偏上，头占上方约 1/4。
REGIONS = {
    "头发（顶部/两侧）": (0.34, 0.10, 0.66, 0.20),
    "头发（垂落侧发）": (0.28, 0.28, 0.40, 0.45),
    "脸部（额头+脸颊）": (0.44, 0.16, 0.56, 0.24),
    "嘴唇": (0.48, 0.215, 0.52, 0.235),
    "裙子（主体）": (0.42, 0.52, 0.58, 0.66),
    "手臂/肩（肤色）": (0.315, 0.30, 0.345, 0.36),
    "背景（左上植物）": (0.02, 0.05, 0.14, 0.20),
    "背景（湖面）": (0.72, 0.20, 0.86, 0.28),
}

print("=" * 74)
print("按区域取中位色（中位数比均值稳，不被高光/阴影拉偏）")
print("=" * 74)
print()
print("| 区域 | 中位色 | HEX | 亮度均值 | 亮度 P10~P90 |")
print("|------|--------|-----|---------|-------------|")
result = {}
for name, (x0, y0, x1, y1) in REGIONS.items():
    box = img.crop((int(x0 * W), int(y0 * H), int(x1 * W), int(y1 * H)))
    a = np.asarray(box).reshape(-1, 3).astype(np.float64)
    med = np.median(a, axis=0)
    lum = a @ np.array([0.2126, 0.7152, 0.0722])
    result[name] = med
    print(f"| {name} | ({med[0]:.0f},{med[1]:.0f},{med[2]:.0f}) | `{hexof(med)}` | "
          f"{lum.mean():.0f} | {np.percentile(lum, 10):.0f}~{np.percentile(lum, 90):.0f} |")

print()
print("=" * 74)
print("归并成建模要用的参数")
print("=" * 74)
print()

hair = result["头发（顶部/两侧）"]
skin = result["脸部（额头+脸颊）"]
dress = result["裙子（主体）"]
lips = result["嘴唇"]

# 头发取"顶部+侧发"里更暗的那个：头发的高光会把它提亮，主色应偏暗
hair_side = result["头发（垂落侧发）"]
hair_main = hair if (hair @ np.array([.2126, .7152, .0722])) < \
    (hair_side @ np.array([.2126, .7152, .0722])) else hair_side

print(f"肤色  skin  = {hexof(skin)}   ← 脸部中位色")
print(f"发色  hair  = {hexof(hair_main)}   ← 顶部/侧发里较暗的那个（避免取到高光）")
print(f"裙色  dress = {hexof(dress)}   ← 裙子主体")
print(f"唇色  lips  = {hexof(lips)}   ← 唇部区域")
print()
print("⚠️ 两个已知偏差，用之前必须知道：")
print("   1. **唇部框只有 4%×2% 大小**，很容易取到周围皮肤；上面那个唇色要人工过一眼。")
print("   2. 照片是**强光外景**，肤色偏亮、发色偏亮；模型上没有这种光照，")
print("      直接照搬会显得发灰。所以建模时通常要把明度**压暗一档**。")
print()
print("给 `gen_chibi_vrm.py` 的建议值（明度各压约 15%）：")
def darken(rgb, k=0.85):
    return hexof([c * k for c in rgb])
print(f"  skin  = {darken(skin)}")
print(f"  hair  = {darken(hair_main, 0.9)}")
print(f"  dress = {darken(dress, 0.95)}")
print(f"  lips  = {darken(lips, 0.9)}")
