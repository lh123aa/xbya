# -*- coding: utf-8 -*-
r"""核验 xbya 的动画在**真实尺寸**下看不看得出来。

全身立绘按宽适配后脸只有 ~49px 高（眼睛 11×9、嘴 17×7），
所以关键问题是：**眨眼和口型在这个尺度上还剩多少**。

出两张图：
  ① 头部区域的逐帧放大网格（4x）—— 看眨眼是否"闭得上"、口型是否"张得开"
  ② 整身的真实尺寸（128 宽）条带 —— 看整体姿态有没有怪处（头身接缝、脚被裁）
"""
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[4]
SPR = ROOT / "resources" / "sprites" / "xbya"
OUT = ROOT / "docs" / "agent" / "evidence" / "p5"
NAME = "xbya"

HEAD_BOX = (38, 26, 98, 82)      # 头部+嘴（画布坐标）
Z = 4
COLS = 12


def load(anim):
    d = SPR / anim
    return [Image.open(p).convert("RGBA") for p in sorted(d.glob("frame_*.png"))]


def grid(anim, frames):
    tw, th = (HEAD_BOX[2] - HEAD_BOX[0]) * Z, (HEAD_BOX[3] - HEAD_BOX[1]) * Z
    rows = (len(frames) + COLS - 1) // COLS
    sheet = Image.new("RGB", (COLS * tw, rows * (th + 16) + 6), (244, 245, 249))
    d = ImageDraw.Draw(sheet)
    try:
        F = ImageFont.truetype("C:/Windows/Fonts/consola.ttf", 13)
    except Exception:
        F = ImageFont.load_default()
    for i, fr in enumerate(frames):
        r, c = divmod(i, COLS)
        x, y = c * tw, r * (th + 16) + 6
        bg = Image.new("RGB", (tw, th), (250, 250, 252))
        crop = fr.crop(HEAD_BOX).resize((tw, th), Image.NEAREST)
        bg.paste(crop, (0, 0), crop)
        sheet.paste(bg, (x, y))
        d.rectangle([x, y, x + tw - 1, y + th - 1], outline=(190, 192, 200))
        d.text((x + 3, y + th + 1), f"{anim} #{i+1}", fill=(70, 70, 90), font=F)
    return sheet


def strip(anims):
    """每类动画取 3 帧，按**真实尺寸**（128 宽）并排。"""
    tiles = []
    for a in anims:
        fr = load(a)
        pick = [fr[0], fr[len(fr) // 2], fr[len(fr) // 3]]
        tiles.append((a, pick))
    real = Image.open(SPR / "idle" / "frame_001.png").convert("RGBA")
    W, H = real.size
    pad = 14
    total_w = sum(3 * W + pad for _, _ in tiles) + pad
    sheet = Image.new("RGB", (total_w, H + 30), (244, 245, 249))
    d = ImageDraw.Draw(sheet)
    try:
        F = ImageFont.truetype("C:/Windows/Fonts/consola.ttf", 14)
    except Exception:
        F = ImageFont.load_default()
    x = pad
    for a, pick in tiles:
        for fr in pick:
            bg = Image.new("RGB", (W, H), (250, 250, 252))
            bg.paste(fr, (0, 0), fr)
            sheet.paste(bg, (x, 24))
            x += W
        d.text((x - 3 * W, 5), a, fill=(30, 30, 45), font=F)
        x += pad
    return sheet


def main() -> int:
    print("=" * 74)
    print("xbya 动画可读性核验")
    print("=" * 74)
    man_size = Image.open(SPR / "idle" / "frame_001.png").size
    print(f"  画布 {man_size[0]}×{man_size[1]}")

    panels = []
    for a in ("idle", "talk", "sleep"):
        fr = load(a)
        panels.append((a, fr, grid(a, fr)))
        print(f"  {a:<7} {len(fr)} 帧")

    # 量化：闭眼帧与睁眼帧在眼框内的差异；开口帧与闭嘴帧在嘴框内的差异
    el, er, mo = (53, 41, 63, 49), (72, 41, 83, 48), (59, 60, 75, 66)
    def px(im, box):
        a = np.asarray(im.crop((box[0], box[1], box[2] + 1, box[3] + 1))
                       .convert("RGB")).astype(float)
        return a
    idle = load("idle")
    ref = px(idle[0], el)
    diffs = [(i + 1, float(np.abs(px(f, el) - ref).mean())) for i, f in enumerate(idle)]
    diffs.sort(key=lambda t: -t[1])
    print()
    print(f"  idle 里与第1帧差异最大的 3 帧（眼框内平均色差）: "
          + ", ".join(f"#{i}={d:.1f}" for i, d in diffs[:3]))
    talk = load("talk")
    mref = px(idle[0], mo)
    md = [(i + 1, float(np.abs(px(f, mo) - mref).mean())) for i, f in enumerate(talk)]
    md.sort(key=lambda t: -t[1])
    print(f"  talk 里嘴框差异最大的 3 帧: "
          + ", ".join(f"#{i}={d:.1f}" for i, d in md[:3]))
    print(f"  → 眼框 11×9px、嘴框 17×7px；色差 >8 通常肉眼可辨")

    # 拼总图
    gap = 34
    W = max(p[2].size[0] for p in panels)
    H = sum(p[2].size[1] + gap for p in panels)
    gsheet = Image.new("RGB", (W, H + 10), (232, 234, 240))
    d = ImageDraw.Draw(gsheet)
    try:
        F = ImageFont.truetype("C:/Windows/Fonts/consola.ttf", 18)
    except Exception:
        F = ImageFont.load_default()
    y = 0
    for a, fr, im in panels:
        d.text((6, y + 6), f"{a}  ({len(fr)} frames, head crop 4x)",
               fill=(20, 20, 30), font=F)
        y += gap
        gsheet.paste(im, (0, y))
        y += im.size[1]
    p1 = OUT / f"_{NAME}_head_grid.png"
    gsheet.save(p1)
    print(f"\n图1: {p1.name}  {gsheet.size[0]}×{gsheet.size[1]}")

    st = strip(("idle", "talk", "sleep", "dance"))
    p2 = OUT / f"_{NAME}_real_size.png"
    st.save(p2)
    print(f"图2: {p2.name}  {st.size[0]}×{st.size[1]}  （128 宽真实尺寸）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
