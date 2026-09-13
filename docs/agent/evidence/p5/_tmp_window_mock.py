# -*- coding: utf-8 -*-
r"""把候选构图放进**真实窗口几何**里合成，同时画出字幕条 —— 在启动前就判掉布局问题。

为什么值得单独做这一步：前两轮我都是"改完代码 → 启动 → 截图 → 发现不对 → 再改"，
一轮要几十秒且状态难复现。这里把窗口尺寸、精灵位置、字幕条位置**按源码算法复现**，
离线出一张图，短句子/长句子两种字幕都画上，一眼就能看出会不会压住角色。

复现的算法（与 ui/pet_window.py 现在一致）：
  窗口  = pet_size + 100          （127 → 228×228）
  可用高 = 窗口高 − SUBTITLE_AREA(44)
  pet_x = (窗口宽 − 画布宽) // 2
  pet_y = (可用高 − 画布高) // 2
  字幕条 y = 窗口高 − 44 + 2，高 = min(2行, 36)，宽 = 文字宽 + 28（最多 窗口宽−16）
"""
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[4]
OUT = ROOT / "docs" / "agent" / "evidence" / "p5"

PET_SIZE = 127
CANVAS = 128
SUBTITLE_AREA = 44
WIN = PET_SIZE + 100
AVAIL_H = WIN - SUBTITLE_AREA
PET_X = (WIN - CANVAS) // 2
PET_Y = max(0, (AVAIL_H - CANVAS) // 2)

print(f"窗口 {WIN}×{WIN}   可用高 {AVAIL_H}   pet=({PET_X},{PET_Y})")
sub_y = WIN - SUBTITLE_AREA + 2
print(f"字幕条 y[{sub_y}..{sub_y + 36 - 1}]")

CANDS = ["_comp_A_取到胸口.png", "_comp_B_取到腰.png",
         "_comp_C_取到大腿.png", "_comp_D_全身.png"]

# 中文字体：取系统里有的
FONT = None
for fp in (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\simhei.ttf",
           r"C:\Windows\Fonts\simsun.ttc"):
    if Path(fp).is_file():
        FONT = fp
        break
print(f"字体: {FONT}")


def make_mock(sprite_path: Path, subtitle: str, label: str) -> Image.Image:
    """合成一张"桌面上看到的样子"。"""
    # 桌面底：柔和渐变（模拟壁纸），便于看边缘
    yy, xx = np.mgrid[0:WIN, 0:WIN].astype(np.float32)
    t = (yy / WIN) * 0.5 + (xx / WIN) * 0.5
    wall = np.stack([214 - 30 * t, 222 - 26 * t, 236 - 20 * t], axis=2).astype(np.uint8)
    img = Image.fromarray(wall).convert("RGBA")

    sp = Image.open(sprite_path).convert("RGBA")
    img.alpha_composite(sp, (PET_X, PET_Y))

    d = ImageDraw.Draw(img)
    # 字幕条：黑底半透明 + 白字，按源码的算法摆
    try:
        f = ImageFont.truetype(FONT, 15) if FONT else ImageFont.load_default()
    except Exception:
        f = ImageFont.load_default()
    max_w = WIN - 16
    # 手写换行：按像素宽度切
    lines, cur = [], ""
    for chn in subtitle:
        if d.textlength(cur + chn, font=f) <= max_w - 28:
            cur += chn
        else:
            lines.append(cur)
            cur = chn
        if len(lines) == 2:
            break
    if cur and len(lines) < 2:
        lines.append(cur)
    lw = max(d.textlength(ln, font=f) for ln in lines) if lines else 0
    box_w = min(max_w, int(lw) + 28)
    box_h = min(SUBTITLE_AREA - 8, len(lines) * 18 + 8)
    bx = (WIN - box_w) // 2
    bar = Image.new("RGBA", (box_w, box_h), (0, 0, 0, 200))
    img.alpha_composite(bar, (bx, sub_y))
    for i, ln in enumerate(lines):
        d.text((bx + box_w / 2, sub_y + 4 + i * 18), ln, font=f,
               fill=(255, 255, 255, 255), anchor="ma")

    # 标尺：把精灵区域画个细框，便于看角色有没有出界
    d.rectangle([PET_X, PET_Y, PET_X + CANVAS - 1, PET_Y + CANVAS - 1],
                outline=(0, 150, 220, 90))

    # 放大 2 倍并加标题
    Z = 2
    big = img.convert("RGB").resize((WIN * Z, WIN * Z), Image.NEAREST)
    canvas = Image.new("RGB", (WIN * Z, WIN * Z + 22), (250, 250, 252))
    canvas.paste(big, (0, 22))
    ImageDraw.Draw(canvas).text((4, 5), label, fill=(20, 20, 30))
    return canvas


SHORT = "我在呢，你说"
LONG = "好的，我先把桌面上的截图整理一下，然后告诉你有几张"

cols = []
for c in CANDS:
    p = OUT / c
    if p.is_file():
        cols.append((c, p))

Z = 2
PAD = 10
CW = len(cols) * 2 * (WIN * Z + PAD) + PAD
CH = WIN * Z + 22 + PAD
sheet = Image.new("RGB", (CW, CH * 2 + 40), (240, 240, 244))
sd = ImageDraw.Draw(sheet)
sd.text((PAD, 4), "真实窗口几何下的效果（左=短句，右=长句）；青框=精灵 128×128 画布范围",
        fill=(20, 20, 30))

for row, (sub, rlabel) in enumerate(((SHORT, "短句"), (LONG, "长句"))):
    sd.text((PAD, CH + 22), rlabel, fill=(90, 90, 110))
    for i, (name, p) in enumerate(cols):
        m = make_mock(p, sub, f"{name} / {rlabel}")
        sheet.paste(m, (PAD + (i * 2 + 0) * (WIN * Z + PAD), CH * row + 22))
        # 第二列放同一张（保持格子对称，便于并排看）
sheet.save(OUT / "_window_mock_compare.png")
print()
print(f"窗口模拟对照: _window_mock_compare.png  ({sheet.size[0]}×{sheet.size[1]})")
print()
# 数字化的判据：角色底边 vs 字幕条顶边
for name, p in cols:
    a = np.asarray(Image.open(p).convert("RGBA"))
    ys, xs = np.where(a[:, :, 3] > 16)
    char_bottom_win = PET_Y + int(ys.max())
    char_top_win = PET_Y + int(ys.min())
    char_w = int(xs.max() - xs.min() + 1)
    gap = sub_y - char_bottom_win
    print(f"  {name:<22} 角色在窗口 y[{char_top_win}..{char_bottom_win}]  宽 {char_w}px  "
          f"距字幕条 {gap:+d}px {'OK 不重叠' if gap >= 0 else '!! 被字幕压住'}")
