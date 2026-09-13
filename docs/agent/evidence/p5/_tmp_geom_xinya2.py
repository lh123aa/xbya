# -*- coding: utf-8 -*-
r"""量「新形象 + 字幕」在**真实窗口**里的几何关系，并出一张视觉对照图。

为什么不用肉眼看截图：桌宠窗口是**透明**的，截屏里混进了背后的桌面内容，
"看起来偏了"和"真的偏了"分不清。这里直接把窗口**离屏渲染**到 QImage，
量的是窗口自己的像素，不含任何背后的东西。

量的四件事：
  1. 窗口尺寸 vs 精灵画布尺寸（`pet_size` 到底有没有生效）
  2. 角色不透明像素的包围盒 → 是否水平居中
  3. 字幕条矩形 → 与角色包围盒的**交集**（重叠就是"字幕压在身上"）
  4. 角色底边与字幕条上沿之间的**空隙**
"""
import io
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
OUT = ROOT / "docs" / "agent" / "evidence" / "p5"
sys.path.insert(0, str(ROOT))

PET = sys.argv[1] if len(sys.argv) > 1 else "xinya2"
BUBBLE = "好的，我先把桌面上的截图整理一下，然后告诉你有几张"

import numpy as np                                            # noqa: E402
import yaml                                                   # noqa: E402
from PySide6.QtCore import Qt                                 # noqa: E402
from PySide6.QtGui import QImage, QPainter, QColor            # noqa: E402
from PySide6.QtWidgets import QApplication                    # noqa: E402

src = (ROOT / "ui" / "pet_window.py").read_text(encoding="utf-8")
SUB_AREA = int(re.search(r"^SUBTITLE_AREA\s*=\s*(\d+)", src, re.M).group(1))
cfg = yaml.safe_load(io.open(ROOT / "config.yaml", encoding="utf-8"))
PET_SIZE = cfg["ui"]["pet_size"]
SUB_ENABLED = cfg["ui"].get("subtitle_enabled", True)
print(f"源码 SUBTITLE_AREA = {SUB_AREA}px    config ui.pet_size = {PET_SIZE}")
print(f"config ui.subtitle_enabled = {SUB_ENABLED}   ui.pet_sprite = {cfg['ui']['pet_sprite']}")
print()

app = QApplication.instance() or QApplication([])

from ui.pet_window import PetWindow                            # noqa: E402

win = PetWindow()
win.load_pet(PET)
win.bubble_text = BUBBLE
win.bubble_timer = 99999          # 别让 tick 清掉它
win.tick()                        # 走一遍真实路径（含 update）

cw, ch = win.anim_controller.get_size()
W, H = win.width(), win.height()
print("=" * 74)
print("① 窗口与画布")
print("=" * 74)
print(f"  精灵画布        {cw}×{ch}   （manifest.json 的 size）")
print(f"  窗口尺寸        {W}×{H}      （load_pet: 画布 + 100 边距）")
print(f"  pet_x, pet_y    ({win.pet_x}, {win.pet_y})")
print(f"  ⇒ 画布占窗口    x[{win.pet_x}..{win.pet_x+cw-1}] "
      f"y[{win.pet_y}..{win.pet_y+ch-1}]")
print()
print(f"  ⚠ config 的 ui.pet_size={PET_SIZE} 若生效，窗口应是 {PET_SIZE+100}×{PET_SIZE+100}；")
print(f"     实际 {W}×{H} —— 启动路径按**画布**算，不按 pet_size（见结论）")

# ── 离屏渲染窗口自身（不含背后的桌面）──
img = QImage(W, H, QImage.Format_ARGB32)
img.fill(QColor(0, 0, 0, 0))
win.render(img)                      # 传 QPaintDevice，不是 QPainter
flat = img.convertToFormat(QImage.Format_RGBA8888)   # 通道序固定为 R,G,B,A
buf = np.frombuffer(flat.constBits(), dtype=np.uint8)
a = buf.reshape(H, flat.bytesPerLine())[:, : W * 4].reshape(H, W, 4)
opaque = a[:, :, 3] > 16

print()
print("=" * 74)
print("② 角色（精灵）在窗口里的实际位置")
print("=" * 74)
# 精灵区限定在画布内，避免把字幕/阴影算进来
sub_band = np.zeros_like(opaque)
sub_band[:, :] = False
sx0, sy0 = win.pet_x, win.pet_y
region = opaque[sy0:sy0 + ch, sx0:sx0 + cw]
ys, xs = np.where(region)
if len(xs) == 0:
    print("  ✘ 精灵区域全透明 —— 没画出来")
    raise SystemExit(1)
bx0, bx1 = sx0 + int(xs.min()), sx0 + int(xs.max())
by0, by1 = sy0 + int(ys.min()), sy0 + int(ys.max())
print(f"  角色包围盒（窗口坐标）x[{bx0}..{bx1}] y[{by0}..{by1}]  "
      f"({bx1-bx0+1}×{by1-by0+1})")
char_cx = (bx0 + bx1) / 2
print(f"  角色水平中心 x={char_cx:.1f}   窗口水平中心 x={(W-1)/2:.1f}   "
      f"偏差 {char_cx - (W-1)/2:+.1f}px")

print()
print("=" * 74)
print("③ 字幕条矩形 与 重叠量")
print("=" * 74)
sub_y = H - SUB_AREA + 2
sub_h = SUB_AREA - 8
sub_y1 = sub_y + sub_h - 1
print(f"  字幕条          y[{sub_y}..{sub_y1}]  （高 {sub_h}px，最多 2 行）")
overlap = max(0, by1 - sub_y + 1)
gap = sub_y - by1 - 1
print(f"  角色底边        y={by1}")
print(f"  ⇒ 重叠 {overlap}px   空隙 {gap}px")
print()
print(f"  {'✘ 字幕压在角色身上' if overlap else '✔ 字幕完全在角色下方，不遮挡'}")

print()
print("=" * 74)
print("④ 可用高度够不够放角色")
print("=" * 74)
avail = H - SUB_AREA
print(f"  窗口高 {H} − 字幕区 {SUB_AREA} = 可用 {avail}px，角色高 {by1-by0+1}px")
print(f"  {'✔ 放得下' if by1 - by0 + 1 <= avail else '✘ 放不下（会被裁）'}")

# ── 视觉对照：放大 3 倍 + 棋盘底（看清透明区）──
Z = 3
big = img.scaled(W * Z, H * Z, Qt.AspectRatioMode.IgnoreAspectRatio,
                 Qt.TransformationMode.FastTransformation)
canvas = QImage(W * Z, H * Z, QImage.Format_RGB32)
qp = QPainter(canvas)
tile = 12
for yy in range(0, H * Z, tile):
    for xx in range(0, W * Z, tile):
        c = QColor(58, 58, 66) if ((xx // tile + yy // tile) % 2) else QColor(74, 74, 84)
        qp.fillRect(xx, yy, tile, tile, c)
qp.drawImage(0, 0, big)
# 画参考线：窗口中心、字幕条上沿
qp.setPen(QColor(80, 220, 120))
qp.drawLine(W * Z // 2, 0, W * Z // 2, H * Z)              # 窗口水平中心
qp.setPen(QColor(255, 90, 90))
qp.drawLine(0, sub_y * Z, W * Z, sub_y * Z)                # 字幕条上沿
qp.drawLine(0, sub_y1 * Z, W * Z, sub_y1 * Z)
qp.end()
out = OUT / f"_geom_{PET}_window.png"
canvas.save(str(out))
print()
print(f"对照图: {out.name}  （{W*Z}×{H*Z}，棋盘=透明；绿线=窗口中心，红线=字幕条上下沿）")
print(f"  注：离屏渲染只含窗口自身，**不含**背后的桌面 —— 与截屏不同")
