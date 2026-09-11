# -*- coding: utf-8 -*-
r"""帧对照表：把关键帧放大并排，人眼核对眨眼与口型是否"读得出来"。

为什么必须看图而不是看数字：动画的判据是**人眼能不能读出动作**。
"眼睛那 10 行被替换了"是数字，"看得像个眨眼"才是结论。所以这里把
静息（含两次眨眼）和说话（口型开合）的关键帧各挑出来放大 4 倍并排。
"""
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[4]
OUT = ROOT / "docs" / "agent" / "evidence" / "p5"
FR = OUT / "_frames_preview"
Z = 4
PAD = 6
LABEL_H = 16


def sheet(anim: str, indices: list[int], title: str, out_name: str):
    frames = []
    for i in indices:
        p = FR / anim / f"frame_{i:03d}.png"
        im = Image.open(p).convert("RGBA")
        frames.append((i, im))
    n = len(frames)
    cw = 128 * Z
    ch = 128 * Z
    W = n * (cw + PAD) + PAD
    H = ch + LABEL_H + PAD * 2
    canvas = Image.new("RGB", (W, H + 22), (250, 250, 252))
    d = ImageDraw.Draw(canvas)
    d.text((PAD, 4), title, fill=(20, 20, 30))
    for k, (idx, im) in enumerate(frames):
        x = PAD + k * (cw + PAD)
        tile = Image.new("RGB", (cw, ch), (255, 255, 255))
        big = im.resize((cw, ch), Image.NEAREST)
        tile.paste(big, (0, 0), big)
        canvas.paste(tile, (x, LABEL_H + PAD))
        d.text((x + 4, LABEL_H + PAD + ch + 2), f"frame_{idx:03d}", fill=(60, 60, 80))
    p = OUT / out_name
    canvas.save(p)
    print(f"{out_name}  ({W}×{canvas.size[1]})  帧 {indices}")


# 静息：frame_008 应为第一次眨眼，frame_020 为第二次
sheet("idle", [1, 4, 8, 12, 16, 20, 24],
      "静息 idle —— 眨眼应出现在 frame_008 与 frame_020（眼睛变成一条线）",
      "_sheet_idle.png")

# 说话：口型开合
sheet("talk", [1, 4, 7, 10, 13, 16, 19],
      "说话 talk —— 嘴应能看到开合（frame_007 前后最开）",
      "_sheet_talk.png")

# 睡眠：应全程闭眼
sheet("sleep", [1, 6, 12, 18, 24],
      "睡眠 sleep —— 应全程闭眼 + 呼吸起伏",
      "_sheet_sleep.png")
