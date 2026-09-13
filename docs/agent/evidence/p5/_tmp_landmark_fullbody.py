# -*- coding: utf-8 -*-
r"""在**原始立绘**（529×1157）上定位五官，再映射到新画布坐标。

## 为什么不直接在 128×281 画布上找

上一轮（`_base2_probe.py`）是在 128 宽的小图上肉眼读格的，注释里写明
「自动判据在这张图上不可靠 —— 它的肤色偏冷，'亮且暖'的阈值几乎筛不出脸」。
在 128 宽时脸只有约 40px，眼睛约 6px 宽，任何阈值都在噪声里。

原始图有 529 宽，脸约 160px、眼睛约 25px —— **特征大 4 倍**，
自动判据在这一级是可靠的。所以：**在原图上量，再用已知的缩放比映射下去**。
映射关系是确定的（紧包围盒 → 按宽适配到 128），不引入额外误差。

输出：
  · `_lm_head_grid.png`   原图头部 3 倍放大 + 20px 网格 + 检测框（人眼核对）
  · `_lm_canvas_grid.png` 映射后的 128×281 画布 4 倍放大 + 网格 + 映射框
  · 终端打印候选坐标（原图坐标 → 画布坐标）
"""
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[4]
OUT = ROOT / "docs" / "agent" / "evidence" / "p5"
CANVAS_W = 128

cut = Image.open(OUT / "_new_cutout.png").convert("RGBA")
print(f"立绘 {cut.size[0]}×{cut.size[1]}")

arr = np.asarray(cut).astype(np.float64)
alpha = arr[:, :, 3]
rgb = arr[:, :, :3]
lum = rgb @ np.array([0.299, 0.587, 0.114])

# ── 紧包围盒 & 映射 ──
ys, xs = np.where(alpha > 64)
X0, Y0, X1, Y1 = int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())
FW, FH = X1 - X0 + 1, Y1 - Y0 + 1
SCALE = CANVAS_W / FW
CH = int(round(FH * SCALE))
print(f"紧包围盒 x[{X0}..{X1}] y[{Y0}..{Y1}]  {FW}×{FH}")
print(f"按宽适配 → 画布 {CANVAS_W}×{CH}   缩放比 {SCALE:.4f}")


def to_canvas(px, py):
    return (px - X0) * SCALE, (py - Y0) * SCALE


# ── 头部区域：alpha 内、亮度>105 的连通块里最大的那个 = 脸 ──
from scipy import ndimage                                       # noqa: E402

head_band = np.zeros_like(alpha, dtype=bool)
head_band[: int(FH * 0.42) + Y0, :] = True        # 上 42% 视为含头
skin = (alpha > 200) & (lum > 105) & head_band
lab, n = ndimage.label(skin)
if n == 0:
    print("✘ 没找到脸")
    raise SystemExit(1)
sizes = ndimage.sum(skin, lab, range(1, n + 1))
face_id = int(np.argmax(sizes)) + 1
face = lab == face_id
fy, fx = np.where(face)
FX0, FX1, FY0, FY1 = int(fx.min()), int(fx.max()), int(fy.min()), int(fy.max())
print(f"人脸连通块 x[{FX0}..{FX1}] y[{FY0}..{FY1}]  "
      f"{FX1-FX0+1}×{FY1-FY0+1}  （{sizes[face_id-1]:.0f}px）")

# ── 眼睛：脸框内、垂直中段、亮度低的像素，按列投影分成左右两簇 ──
band_y0 = FY0 + int((FY1 - FY0) * 0.30)
band_y1 = FY0 + int((FY1 - FY0) * 0.62)
sub = np.zeros_like(face)
sub[band_y0:band_y1 + 1, FX0:FX1 + 1] = True
dark = face & sub & (lum < 118)
dy, dx = np.where(dark)
print(f"眼带 y[{band_y0}..{band_y1}] 内暗像素 {len(dx)} 个")

# 按 x 中位数劈成左右
mid = (FX0 + FX1) / 2
left = dx < mid
boxes = {}
for tag, sel in (("eye_l", left), ("eye_r", ~left)):
    if sel.sum() < 20:
        print(f"  {tag}: 暗像素太少({sel.sum()})，放弃自动判据")
        continue
    ex, ey = dx[sel], dy[sel]
    boxes[tag] = (int(ex.min()), int(ey.min()), int(ex.max()), int(ey.max()))
    print(f"  {tag} 原图 x[{ex.min()}..{ex.max()}] y[{ey.min()}..{ey.max()}]  "
          f"{ex.max()-ex.min()+1}×{ey.max()-ey.min()+1}  ({sel.sum()}px)")

# ── 嘴：脸框下段、偏红的暗像素 ──
mb_y0 = FY0 + int((FY1 - FY0) * 0.68)
mb_y1 = FY1
msub = np.zeros_like(face)
msub[mb_y0:mb_y1 + 1, FX0:FX1 + 1] = True
r, g, b = rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2]
reddish = face & msub & (lum < 175) & ((r - g) > 12) & ((r - b) > 12)
my, mx = np.where(reddish)
if len(mx) > 20:
    boxes["mouth"] = (int(mx.min()), int(my.min()), int(mx.max()), int(my.max()))
    print(f"  mouth 原图 x[{mx.min()}..{mx.max()}] y[{my.min()}..{my.max()}]  "
          f"{mx.max()-mx.min()+1}×{my.max()-my.min()+1}  ({len(mx)}px)")
else:
    print(f"  mouth: 偏红暗像素只有 {len(mx)} 个，需人工定")

# ── 脖子：脸块底边再往下一点（头身交界）──
neck_y_src = FY1 + int((FY1 - FY0) * 0.10)
print(f"  neck_y 原图 y={neck_y_src}（脸底 +10% 脸高）")

print()
print("=" * 74)
print("映射到画布 %d×%d（缩放 %.4f）" % (CANVAS_W, CH, SCALE))
print("=" * 74)
for tag, (bx0, by0, bx1, by1) in boxes.items():
    c0x, c0y = to_canvas(bx0, by0)
    c1x, c1y = to_canvas(bx1, by1)
    print(f"  {tag:<7} ({int(round(c0x))}, {int(round(c0y))}, "
          f"{int(round(c1x))}, {int(round(c1y))})"
          f"   宽{int(round(c1x-c0x))+1}×高{int(round(c1y-c0y))+1}")
ncx, ncy = to_canvas((FX0 + FX1) / 2, neck_y_src)
print(f"  neck_y  {int(round(ncy))}")
print(f"  head    ({int(round(to_canvas(FX0, FY0)[0]))}, "
      f"{int(round(to_canvas(FX0, FY0)[1]))}, "
      f"{int(round(to_canvas(FX1, FY1)[0]))}, "
      f"{int(round(to_canvas(FX1, FY1)[1]))})")

# ══════════════ 出图核对 ══════════════
pad = 40
cx0, cy0 = max(0, FX0 - pad), max(0, FY0 - pad)
cx1, cy1 = min(cut.size[0], FX1 + pad), min(cut.size[1], neck_y_src + pad)
head = cut.crop((cx0, cy0, cx1, cy1))
Z = 3
big = head.resize((head.size[0] * Z, head.size[1] * Z), Image.NEAREST)
d = ImageDraw.Draw(big)
for gx in range(cx0 - cx0 % 20, cx1, 20):
    X = (gx - cx0) * Z
    d.line([(X, 0), (X, big.size[1])], fill=(0, 160, 255, 255), width=1)
    d.text((X + 2, 2), str(gx), fill=(0, 120, 220, 255))
for gy in range(cy0 - cy0 % 20, cy1, 20):
    Y = (gy - cy0) * Z
    d.line([(0, Y), (big.size[0], Y)], fill=(0, 160, 255, 255), width=1)
    d.text((2, Y + 2), str(gy), fill=(0, 120, 220, 255))
for tag, (bx0, by0, bx1, by1) in boxes.items():
    d.rectangle([(bx0 - cx0) * Z, (by0 - cy0) * Z,
                 (bx1 - cx0) * Z, (by1 - cy0) * Z],
                outline=(255, 40, 40, 255), width=2)
    d.text(((bx0 - cx0) * Z + 2, (by0 - cy0) * Z - 12), tag, fill=(255, 40, 40, 255))
Y = (neck_y_src - cy0) * Z
d.line([(0, Y), (big.size[0], Y)], fill=(255, 210, 0, 255), width=2)
d.text((2, Y + 2), "neck", fill=(255, 210, 0, 255))
big.save(OUT / "_lm_head_grid.png")
print()
print(f"核对图1: _lm_head_grid.png  {big.size[0]}×{big.size[1]}（红框=检测框，黄线=neck）")
