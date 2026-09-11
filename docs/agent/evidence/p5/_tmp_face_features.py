# -*- coding: utf-8 -*-
r"""精确量出左右眼与嘴的包围盒 —— 动画的位移/替换都靠这几个框。

上一版"整幅脸内部暗像素剖面"看不出眼睛在哪：脸的左右两侧就是头发，
所以每一行都从 x=50 到 x=76 全是暗像素。**必须在 x 方向也做连通块**，
而且要把"贴着脸的边缘那一圈头发"排除掉。

做法：在脸部亮连通块内取暗像素（lum<95），做连通块并**排除接触搜索窗外框的块**
（那些是头发，不是五官），剩下的按面积排序 → 最大的两个是左右眼，再往下的是嘴。
"""
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[4]
OUT = ROOT / "docs" / "agent" / "evidence" / "p5"
SPR = OUT / "_pet_variant_D.png"

im = Image.open(SPR).convert("RGBA")
a = np.asarray(im).astype(np.float64)
alpha = a[:, :, 3]
rgb = a[:, :, :3]
lum = 0.299 * rgb[:, :, 0] + 0.587 * rgb[:, :, 1] + 0.114 * rgb[:, :, 2]
opaque = alpha > 128

ys, xs = np.where(opaque)
y0, y1, x0, x1 = int(ys.min()), int(ys.max()), int(xs.min()), int(xs.max())
print(f"角色包围盒: x[{x0}..{x1}] y[{y0}..{y1}]")

# 脸部区域：上一轮已确认蓝框 x[50..76] y[32..70] 正好卡在脸上
# 这里再往外留 1px 余量，便于剔除"贴着框边"的头发
FX0, FY0, FX1, FY1 = 50, 32, 76, 70
# 五官只在脸的上 2/3（额→下巴），下半是脖子和阴影
SEARCH_Y1 = 68

sub_lum = lum[FY0:SEARCH_Y1 + 1, FX0:FX1 + 1]
sub_op = opaque[FY0:SEARCH_Y1 + 1, FX0:FX1 + 1]
dark = sub_op & (sub_lum < 95)
H, W = dark.shape
print(f"搜索窗 x[{FX0}..{FX1}] y[{FY0}..{SEARCH_Y1}]  {W}×{H}  暗像素 {int(dark.sum())}")

# 连通块（4 邻接），并标记是否接触窗口边框
lab = np.zeros((H, W), dtype=np.int32)
cur = 0
comps = []
for sy in range(H):
    for sx in range(W):
        if dark[sy, sx] and lab[sy, sx] == 0:
            cur += 1
            stack = [(sy, sx)]
            lab[sy, sx] = cur
            pix = []
            touch = False
            while stack:
                cy, cx = stack.pop()
                pix.append((cy, cx))
                if cy in (0, H - 1) or cx in (0, W - 1):
                    touch = True
                for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    ny, nx = cy + dy, cx + dx
                    if 0 <= ny < H and 0 <= nx < W and dark[ny, nx] and lab[ny, nx] == 0:
                        lab[ny, nx] = cur
                        stack.append((ny, nx))
            py = [p[0] for p in pix]
            px = [p[1] for p in pix]
            comps.append({
                "n": len(pix), "touch": touch,
                "box": (min(px) + FX0, min(py) + FY0, max(px) + FX0, max(py) + FY0),
            })

inner = [c for c in comps if not c["touch"]]
inner.sort(key=lambda c: -c["n"])
print()
print(f"连通块共 {len(comps)} 个，其中不接触边框（=非头发）{len(inner)} 个。")
print("内部块按面积排序（前 10）:")
print("  面积   包围盒                        尺寸")
print("  " + "-" * 52)
for c in inner[:10]:
    bx0, by0, bx1, by1 = c["box"]
    print(f"  {c['n']:>4}   x[{bx0}..{bx1}] y[{by0}..{by1}]    "
          f"{bx1-bx0+1}×{by1-by0+1}")

# ---- 判定：最大的两块中，靠上的必然是眼睛 ----
# 脸是左右对称的，所以"左右眼"应当是两个 x 相邻、y 接近、面积相近的块
eyes = [c for c in inner if c["box"][1] < 58][:4]
print()
if len(eyes) >= 2:
    e = sorted(eyes[:2], key=lambda c: c["box"][0])
    for name, c in zip(("左眼", "右眼"), e):
        bx0, by0, bx1, by1 = c["box"]
        print(f"{name}: x[{bx0}..{bx1}] y[{by0}..{by1}]  {bx1-bx0+1}×{by1-by0+1}  "
              f"中心 ({((bx0+bx1)/2):.1f},{((by0+by1)/2):.1f}) 面积 {c['n']}")
    # 嘴：面积次之、y 在眼睛下方的块
    below = [c for c in inner if c["box"][1] > max(cc["box"][3] for cc in e) - 2]
    if below:
        m = below[0]
        bx0, by0, bx1, by1 = m["box"]
        print(f"嘴  : x[{bx0}..{bx1}] y[{by0}..{by1}]  {bx1-bx0+1}×{by1-by0+1}  "
              f"中心 ({((bx0+bx1)/2):.1f},{((by0+by1)/2):.1f}) 面积 {m['n']}")
    else:
        print("嘴  : 未找到（可能在 lum<95 阈值下不够暗，需放宽）")

# ---- 采样肤色与发色（眨眼要把眼睛涂成肤色，闭眼线要发色）----
face_skin = rgb[FY0:FY1 + 1, FX0:FX1 + 1][(lum[FY0:FY1 + 1, FX0:FX1 + 1] > 130)
                                          & opaque[FY0:FY1 + 1, FX0:FX1 + 1]]
if len(face_skin):
    med = np.median(face_skin, axis=0)
    print()
    print(f"脸部肤色中位数: ({med[0]:.0f},{med[1]:.0f},{med[2]:.0f})")
hair = rgb[(lum < 60) & opaque]
if len(hair):
    hm = np.median(hair, axis=0)
    print(f"发色中位数    : ({hm[0]:.0f},{hm[1]:.0f},{hm[2]:.0f})")
