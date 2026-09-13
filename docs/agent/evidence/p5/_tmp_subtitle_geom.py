# -*- coding: utf-8 -*-
r"""量字幕与宠物的几何关系：**谁压在谁上面、压掉多少像素**。

这是"字幕位置不对"的定量版。用户看到的是"字幕挡住了角色下半身"，
这里把它变成数字：精灵在窗口里的 y 范围 vs 字幕条的 y 范围，求交集。
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
OUT = ROOT / "docs" / "agent" / "evidence" / "p5"

# ---- 从源码读常量（不手抄，避免和代码脱节）----
import re

src = (ROOT / "ui" / "pet_window.py").read_text(encoding="utf-8")
def const(name):
    m = re.search(rf"^{name}\s*=\s*(\d+)", src, re.M)
    return int(m.group(1)) if m else None

SUBTITLE_AREA = const("SUBTITLE_AREA")
print(f"ui/pet_window.py  SUBTITLE_AREA = {SUBTITLE_AREA}")

PET_CANVAS = 128
for pet_size_line in re.finditer(r"pet_size", (ROOT / "config.yaml").read_text(encoding="utf-8")):
    pass
import yaml, io
cfg = yaml.safe_load(io.open(ROOT / "config.yaml", encoding="utf-8"))
PET_SIZE = cfg["ui"]["pet_size"]
print(f"config.yaml       ui.pet_size   = {PET_SIZE}")
print()

# ---- 老角色（cat）与新角色（xinya）在各自 128 画布里的实际包围盒 ----
import numpy as np
from PIL import Image

def bbox_of(p: Path):
    a = np.asarray(Image.open(p).convert("RGBA"))
    ys, xs = np.where(a[:, :, 3] > 16)
    return int(ys.min()), int(ys.max()), int(xs.min()), int(xs.max())

for name in ("cat", "xinya"):
    p = ROOT / "resources" / "sprites" / name / "idle" / "frame_001.png"
    if not p.is_file():
        print(f"{name}: 没有 idle/frame_001.png")
        continue
    y0, y1, x0, x1 = bbox_of(p)
    print(f"{name:<7} 画布 128×128  角色包围盒 x[{x0}..{x1}] y[{y0}..{y1}]  "
          f"({x1-x0+1}×{y1-y0+1})")
    print(f"        角色底边在画布 y={y1}  →  画布底部还剩 {127-y1} px 空白")

print()

# ---- 窗口几何：精灵贴在哪、字幕条贴在哪 ----
# pet_x/pet_y 初值都是 100（见 pet_window.py），但若被布局覆盖则以此为准。
# 这里按"精灵画布 128×128 贴在 (px,py)"计算；窗口 = 精灵 + 100 边距
PET_X = 50
PET_Y = 50
draw_h = PET_CANVAS if PET_SIZE == PET_CANVAS else PET_SIZE
print("窗口几何（按 128 画布、window = 画布 + 100 边距推算）:")
print(f"  窗口尺寸        {PET_CANVAS + 100}×{PET_CANVAS + 100}")
print(f"  精灵绘制区域    y[{PET_Y}..{PET_Y + draw_h - 1}]   （高 {draw_h}px）")
sub_y = PET_CANVAS + 100 - SUBTITLE_AREA + 2
sub_h = SUBTITLE_AREA - 8
print(f"  字幕条区域      y[{sub_y}..{sub_y + sub_h - 1}]   （高 {sub_h}px, 最多 2 行）")
print()

overlap0 = max(0, (PET_Y + draw_h) - sub_y)
print(f"  ⇒ 精灵与字幕条重叠 {overlap0} px（精灵底部这 {overlap0}px 被字幕盖住）")
print()

# ---- 换成新图会怎样：新图是全身立绘（比例 2.187），角色会更高 ----
print("新图（529×1157，全身立绘）若按同样方式放入 128 画布:")
ratio = 1157 / 529
print(f"  立绘宽高比 1:{ratio:.2f}（上一张半身像是 1776×2256 = 1:1.27）")
print(f"  若按'角色高 = 110px'缩放 → 宽只有 {110/ratio:.0f}px —— 太窄，脸会小到看不清")
new_h = 128
new_w = int(round(new_h / ratio))
print(f"  若按'占满画布高 128px'缩放 → 尺寸 {new_w}×{new_h}px")
print(f"  此时角色底边 y=127，与字幕条重叠 {max(0, (PET_Y + new_h) - sub_y)} px")
print()
print("  结论：全身立绘**必须有字幕区下方留白**，否则字幕一定压在身上；")
print("        半身像天然有留白是因为它胸前就是画布底了。")
print()
print(f"  需要精灵总高 ≈ {sub_y - PET_Y}px 才能在字幕条上方完整显示角色。")
