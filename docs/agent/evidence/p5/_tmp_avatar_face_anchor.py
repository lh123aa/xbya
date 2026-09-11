# -*- coding: utf-8 -*-
r"""用**人脸检测**定位，替掉我手估的坐标 —— 手估那版全框错了。

上一版实测（见 `_palette_crops.png` 联系表）：
  · 我标 `face` 的框，实际框到了**头顶头发**
  · 我标 `arm` 的框，实际框到了**树叶**
  · 只有 `dress` 一个框是对的

这类错误的危险在于**它不报错**：取出来的"肤色"是深棕（其实是头发），
如果直接拿去建模，就会生成一个棕色脸的角色，而我还会以为"这是照片里的肤色"。

所以改成：先检测人脸，**以脸的框为锚点**按比例推其它部位。
锚点是机器的输出，不是我目测的位置。
"""
import os
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw

SRC = Path(os.environ["USERPROFILE"]) / ".dsh" / "attachments" / "v1" / "objects" / "55" \
      / "556fecf5ca2faa42dc6cb5ef8cca579dded31d7213778aba4f4267df25725394"
OUTDIR = Path(__file__).resolve().parents[4] / "docs" / "agent" / "evidence" / "p5"

bgr = cv2.imread(str(SRC))
H, W = bgr.shape[:2]
print(f"源图 {W}x{H}")

# ── 人脸检测（多级联都试，取最大的那个脸）──
gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
cascades = {
    "haarcascade_frontalface_default": cv2.data.haarcascades + "haarcascade_frontalface_default.xml",
    "haarcascade_frontalface_alt2": cv2.data.haarcascades + "haarcascade_frontalface_alt2.xml",
    "haarcascade_profileface": cv2.data.haarcascades + "haarcascade_profileface.xml",
}
found = []
for name, path in cascades.items():
    c = cv2.CascadeClassifier(path)
    if c.empty():
        continue
    det = c.detectMultiScale(gray, scaleFactor=1.05, minNeighbors=6,
                             minSize=(int(W * 0.04), int(H * 0.04)))
    for (x, y, w, h) in det:
        found.append((w * h, name, (int(x), int(y), int(w), int(h))))
    print(f"  {name}: 检出 {len(det)} 个")

if not found:
    print()
    print("**没检出人脸** —— 那就不能靠人脸锚点，得换办法（见文末）。")
    raise SystemExit(1)

found.sort(reverse=True)
_, who, (fx, fy, fw, fh) = found[0]
print()
print(f"取最大的脸：{who}  box=({fx},{fy},{fw},{fh})  "
      f"中心=({fx+fw//2},{fy+fh//2})  占图宽 {fw/W:.1%}")

img = Image.open(SRC).convert("RGB")

# ── 以脸的框为锚点推其它部位（比例来自人像常识，但锚点是人脸检测给的）──
def rel(ax, ay, aw, ah):
    """相对脸框的偏移 → 绝对框（x,y,w,h），偏移量以脸宽 fw 为单位"""
    return (int(fx + ax * fw), int(fy + ay * fh),
            int(aw * fw), int(ah * fh))


REGIONS = {
    # 脸颊：脸框中央偏下，避开眼睛/头发边缘
    "skin_face":  rel(0.28, 0.45, 0.44, 0.30),
    # 额头在脸框上方一点（避开头发，再往下收 10%）
    "skin_fore":  rel(0.32, 0.16, 0.36, 0.14),
    # 头发：脸框正上方（头顶），以及脸框左侧外缘
    "hair_top":   rel(0.15, -0.55, 0.70, 0.30),
    "hair_side":  rel(-0.35, 0.20, 0.25, 0.60),
    # 唇：脸框下半部中间一小条
    "lips":       rel(0.36, 0.72, 0.28, 0.10),
    # 白裙：脸框下方约 1.6 个脸高处（半身像里裙子在胸口以下）
    "dress":      rel(0.05, 2.00, 0.90, 0.60),
}

print()
print("| 区域 | 绝对框 | 中位色 HEX | 亮度 |")
print("|------|--------|-----------|------|")
vis = img.copy()
d = ImageDraw.Draw(vis)
med = {}
for name, (x, y, w, h) in REGIONS.items():
    x = max(0, x); y = max(0, y)
    w = min(w, W - x); h = min(h, H - y)
    if w <= 1 or h <= 1:
        print(f"| {name} | 越界，跳过 | — | — |")
        continue
    c = img.crop((x, y, x + w, y + h))
    a = np.asarray(c).reshape(-1, 3).astype(np.float64)
    m = np.median(a, axis=0)
    lum = float((a @ np.array([.2126, .7152, .0722])).mean())
    med[name] = m
    print(f"| {name} | ({x},{y},{w},{h}) | "
          f"`#{int(m[0]):02X}{int(m[1]):02X}{int(m[2]):02X}` | "
          f"{lum:.0f} |")
    d.rectangle((x, y, x + w, y + h), outline=(255, 0, 0), width=max(3, W // 300))
    d.text((x + 6, y + 6), name, fill=(255, 0, 0))
# 脸框也画出来（绿）
d.rectangle((fx, fy, fx + fw, fy + fh), outline=(0, 255, 0), width=max(3, W // 220))
d.text((fx + 6, fy - 24), "DETECTED FACE", fill=(0, 255, 0))

vis.save(OUTDIR / "_palette_regions2.png")
print()
print(f"带框总览（绿=检测到的人脸，红=取样框）：{OUTDIR / '_palette_regions2.png'}")

# 联系表
tiles = []
for name, (x, y, w, h) in REGIONS.items():
    x = max(0, x); y = max(0, y)
    w = min(w, W - x); h = min(h, H - y)
    if w <= 1 or h <= 1:
        continue
    tiles.append((name, img.crop((x, y, x + w, y + h)).resize((150, 150))))
sheet = Image.new("RGB", (150 * len(tiles), 172), (255, 255, 255))
d2 = ImageDraw.Draw(sheet)
for i, (name, t) in enumerate(tiles):
    sheet.paste(t, (i * 150, 0))
    d2.text((i * 150 + 4, 152), name, fill=(0, 0, 0))
sheet.save(OUTDIR / "_palette_crops2.png")
print(f"各框内容联系表：{OUTDIR / '_palette_crops2.png'}")
print()
print("⚠️ 仍然要人工过一眼联系表 —— 检测到脸不代表我按比例推的其它框都对。")
