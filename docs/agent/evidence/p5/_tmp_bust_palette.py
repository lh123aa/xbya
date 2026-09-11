# -*- coding: utf-8 -*-
r"""为"画一个上半身卡通形象"取准配色。

前两次取色的教训：
  · 手估坐标 → 框到了头发和树上
  · 按人脸比例外推 → 头顶框跑到树冠、侧发框跑到背景树
  · 按颜色分割最大连通块 → 头发块把背景树的暗部也吃进来了

这次改法：**先从人脸框出发，再在每个部位用小框多点采样，然后用"只保留
与中位色接近的像素"迭代收紧**（类似一次 K-means 的剔野值），
把背景污染挤出去。并且**打印每个部位最终保留的像素比例**——
比例过低说明框本身就不对，必须重取，不能硬用。
"""
import os
from pathlib import Path

import numpy as np
from PIL import Image

SRC = Path(os.environ["USERPROFILE"]) / ".dsh" / "attachments" / "v1" / "objects" / "55" \
      / "556fecf5ca2faa42dc6cb5ef8cca579dded31d7213778aba4f4267df25725394"

img = Image.open(SRC).convert("RGB")
W, H = img.size
A = np.asarray(img).astype(np.float64)

# 人脸检测得到的框（703,495,396,396）—— 由 OpenCV 给出，不是我目测的
FX, FY, FW, FH = 702, 495, 396, 396
cx, cy = FX + FW / 2, FY + FH / 2
print(f"图 {W}x{H}；人脸框中心 ({cx:.0f},{cy:.0f})，脸宽 {FW}")
print()


def sample(name, x0, y0, x1, y1, tol=38, rounds=3):
    """小框多点 + 迭代剔野值（保留与中位色接近的像素）

    tol: 允许的 RGB 欧氏距离；每轮用上一轮的中位色当中心重新筛，
         逐步把背景像素挤出去。
    """
    x0, y0 = max(0, int(x0)), max(0, int(y0))
    x1, y1 = min(W, int(x1)), min(H, int(y1))
    if x1 - x0 < 3 or y1 - y0 < 3:
        print(f"  {name:10s} 框太小/越界，跳过")
        return None
    box = A[y0:y1, x0:x1].reshape(-1, 3)
    keep = np.ones(len(box), bool)
    for _ in range(rounds):
        cur = box[keep]
        if len(cur) < 20:
            break
        med = np.median(cur, axis=0)
        d = np.linalg.norm(box - med, axis=1)
        keep = d < tol
    px = box[keep]
    ratio = len(px) / len(box)
    med = np.median(px, axis=0) if len(px) else np.array([0, 0, 0])
    lum = float((px @ np.array([.2126, .7152, .0722])).mean()) if len(px) else 0
    flag = "" if ratio > 0.35 else "  ⚠️ 保留比例低，框可能没对准"
    print(f"  {name:10s} HEX=#{int(med[0]):02X}{int(med[1]):02X}{int(med[2]):02X} "
          f"保留 {ratio:5.1%} ({len(px)}/{len(box)}) 亮度 {lum:3.0f}{flag}")
    return med


print("=" * 72)
print("各部位取色（迭代剔野值后）")
print("=" * 72)
print()

# 脸部：脸框中央偏下（避开眼睛与刘海）
skin_face = sample("脸-颊部", cx - FW * 0.20, cy + FH * 0.08, cx + FW * 0.20, cy + FH * 0.26)
# 额头：脸框上方一点，但要避开刘海 —— 取脸框内靠上 20%
skin_brow = sample("脸-额部", cx - FW * 0.18, cy - FH * 0.12, cx + FW * 0.18, cy + FH * 0.02)

# 头发：取**脸框内部两侧**（那里一定是头发，因为脸框包住了两侧垂发）
hair_l = sample("发-左", FX + FW * 0.02, cy, FX + FW * 0.20, cy + FH * 0.5, tol=45)
hair_r = sample("发-右", FX + FW * 0.80, cy, FX + FW * 0.98, cy + FH * 0.5, tol=45)

# 裙子：脸框下方 2.2~3.2 个脸高，中间偏左（避开垂发与手臂）
dress = sample("裙", cx - FW * 0.55, FY + FH * 2.6, cx + FW * 0.55, FY + FH * 3.6)
# 手臂：脸框左下、身体外侧（照片里她双手背在身后，前臂可见）
arm = sample("手臂", cx - FW * 1.05, FY + FH * 1.5, cx - FW * 0.80, FY + FH * 2.2)

# 嘴唇：脸框下部 1/4 的中间窄条
lips = sample("唇", cx - FW * 0.12, cy + FH * 0.30, cx + FW * 0.12, cy + FH * 0.44, tol=30)

print()
print("=" * 72)
print("给插画用的最终调色板")
print("=" * 72)
print()


def hx(v):
    return f"#{int(v[0]):02X}{int(v[1]):02X}{int(v[2]):02X}" if v is not None else "—"


# 脸：额部与颊部取平均（同一张脸的两处）
if skin_face is not None and skin_brow is not None:
    skin = (skin_face + skin_brow) / 2
elif skin_face is not None:
    skin = skin_face
else:
    skin = skin_brow

# 头发：左右取较暗的那个（头发主色，避免高光）
if hair_l is not None and hair_r is not None:
    hair = hair_l if hair_l.sum() < hair_r.sum() else hair_r
else:
    hair = hair_l if hair_l is not None else hair_r

pal = {
    "skin": skin,
    "hair": hair,
    "dress": dress,
    "arm": arm,
    "lips": lips,
}
for k, v in pal.items():
    print(f"  {k:6s} = {hx(v)}")

print()
print("派生色（插画需要阴影/高光，不能只有一个平色）：")


def shift(v, k):
    return None if v is None else np.clip(v * k, 0, 255)


if hair is not None:
    print(f"  hair_shadow  = {hx(shift(hair, 1.6))}   ← 头发高光（提亮）")
    print(f"  hair_line    = {hx(shift(hair, 0.6))}   ← 头发线稿/暗部")
if skin is not None:
    print(f"  blush        = {hx(np.clip(skin * np.array([1.02, 0.82, 0.82]), 0, 255))}   ← 腮红")
    print(f"  skin_shadow  = {hx(shift(skin, 0.86))}   ← 颈/下巴阴影")
if dress is not None:
    print(f"  dress_shadow = {hx(shift(dress, 0.88))}   ← 裙子褶皱")

print()
print("★ 眼睛颜色：照片里眼睛太小，取色不可靠 —— 按观察取暖褐色")
print("  eye   = #4A3226")
print("  pupil = #120C0A")
