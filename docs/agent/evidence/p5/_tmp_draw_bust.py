# -*- coding: utf-8 -*-
r"""按照片的画一个**上半身卡通形象**（扁平插画风格，纯代码绘制）。

关于能力边界（必须写清楚，避免误读）：
  这不是"AI 画图"。它是**用几何图形拼出来的矢量风格插画** ——
  每个形状的位置、大小、颜色都由本文件的代码决定。
  所以：
    · 能做：配色准确（从照片量出）、发型/服装轮廓贴近、**可参数化动画**
      （眨眼、张嘴、头发摆动、呼吸起伏 —— 因为部件是分开画的）
    · 不能做：像照片里那个人、精细插画质感、写实五官
  产出风格接近"简单动漫 Q 版上色"，不是画师级作品。

尺寸：4 倍超采样绘制再缩回，得到平滑边缘（PIL 没有抗锯齿多边形，
靠超采样等效实现）。
"""
import io
import math
import os
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

# ── 照片量出的调色板（source: _tmp_bust_palette.py）──
# 说明：hair 取到 #04070C（近纯黑）—— 照片是强光外景，头发主色确实极暗。
# 插画里纯黑会让轮廓糊成一团，所以提一点暖调当基色，暗部另行加深。
PAL = {
    "skin":        (225, 194, 180),
    "skin_shadow": (193, 166, 154),
    "skin_line":   (150, 118, 106),
    "blush":       (229, 159, 147),
    "hair":        (28, 22, 24),
    "hair_mid":    (46, 36, 38),
    "hair_hi":     (78, 62, 60),
    "dress":       (213, 216, 213),
    "dress_shade": (186, 190, 187),
    "dress_line":  (150, 154, 152),
    "eye_iris":    (74, 50, 38),
    "eye_deep":    (32, 20, 16),
    "eye_hi":      (255, 255, 255),
    "lips":        (203, 132, 120),
    "mouth_in":    (120, 66, 62),
}

SS = 4                      # 超采样倍率
W, H = 512, 640             # 最终尺寸（上半身，竖构图）


def canvas():
    im = Image.new("RGBA", (W * SS, H * SS), (0, 0, 0, 0))
    return im, ImageDraw.Draw(im)


def ellipse(d, cx, cy, rx, ry, fill=None, outline=None, width=0):
    d.ellipse([(cx - rx) * SS, (cy - ry) * SS, (cx + rx) * SS, (cy + ry) * SS],
              fill=fill, outline=outline, width=int(width * SS) if width else 0)


def poly(d, pts, fill=None, outline=None, width=0):
    d.polygon([(x * SS, y * SS) for x, y in pts], fill=fill,
              outline=outline, width=int(width * SS) if width else 0)


def rounded(d, box, r, fill=None, outline=None, width=0):
    x0, y0, x1, y1 = box
    d.rounded_rectangle([x0 * SS, y0 * SS, x1 * SS, y1 * SS], radius=r * SS,
                        fill=fill, outline=outline,
                        width=int(width * SS) if width else 0)


def draw_bust(blink=0.0, mouth=0.0, sway=0.0, hair_sway=0.0):
    """画上半身。

    Args:
        blink: 0=睁眼 1=闭眼
        mouth: 0=闭 1=张嘴（说话）
        sway: 头部左右摆动像素（呼吸/动作）
        hair_sway: 头发摆动强度
    """
    im, d = canvas()

    # 画布坐标（最终尺寸下的坐标，函数内部乘 SS）
    CX = W / 2 + sway
    HEAD_CY = 232
    FACE_RX, FACE_RY = 96, 116

    # ── 1. 后层头发（脸后面的大轮廓）──
    hair_back_off = hair_sway * 3
    poly(d, [
        (CX - 176 + hair_back_off, 108),
        (CX - 150 + hair_back_off, 60),
        (CX - 40, 26),
        (CX + 70, 40),
        (CX + 152 + hair_back_off, 92),
        (CX + 178 + hair_back_off, 220),
        (CX + 168 + hair_back_off, 470),
        (CX + 132 + hair_back_off, 560),
        (CX + 96 + hair_back_off, 470),
        (CX + 104, 300),
        (CX - 104, 300),
        (CX - 96 + hair_back_off, 470),
        (CX - 132 + hair_back_off, 560),
        (CX - 168 + hair_back_off, 470),
        (CX - 178 - hair_back_off, 220),
    ], fill=PAL["hair"])
    # 后发中间调（分出层次，别一整块死黑）
    ellipse(d, CX + 40, 150, 118, 86, PAL["hair_mid"])

    # ── 2. 脖子与肩 ──
    rounded(d, (CX - 26, HEAD_CY + FACE_RY - 34, CX + 26, 470), 16,
            PAL["skin_shadow"])
    # 锁骨/肩
    poly(d, [
        (CX - 150, 470), (CX - 96, 428), (CX - 30, 414),
        (CX + 30, 414), (CX + 96, 428), (CX + 150, 470),
        (CX + 150, 640), (CX - 150, 640),
    ], fill=PAL["skin"])

    # ── 3. 白裙（无袖，露肩）──
    dress_top = 452
    poly(d, [
        (CX - 118, dress_top + 6), (CX - 60, 434), (CX - 18, 446),
        (CX + 18, 446), (CX + 60, 434), (CX + 118, dress_top + 6),
        (CX + 152, 640), (CX - 152, 640),
    ], fill=PAL["dress"])
    # 胸口 V 形
    poly(d, [(CX - 44, 470), (CX, 512), (CX + 44, 470)],
         fill=PAL["skin_shadow"])
    # 裙身褶皱（竖线）
    for dx in (-84, -40, 8, 56):
        d.line([((CX + dx) * SS, (dress_top + 30) * SS),
                ((CX + dx * 1.5) * SS, 636 * SS)],
               fill=PAL["dress_shade"], width=int(1.6 * SS))
    # 腰部装饰线
    d.line([((CX - 146) * SS, 560 * SS), ((CX + 146) * SS, 560 * SS)],
           fill=PAL["dress_line"], width=int(1.4 * SS))

    # ── 4. 耳朵与脸 ──
    ellipse(d, CX - FACE_RX + 4, HEAD_CY + 8, 16, 24, PAL["skin"])
    ellipse(d, CX + FACE_RX - 4, HEAD_CY + 8, 16, 24, PAL["skin"])
    ellipse(d, CX, HEAD_CY, FACE_RX, FACE_RY, PAL["skin"])
    # 下巴略收（用椭圆叠加压出尖下巴）
    ellipse(d, CX, HEAD_CY + FACE_RY * 0.42, FACE_RX * 0.72, FACE_RY * 0.52,
            PAL["skin"])

    # ── 5. 刘海（盖住额头，分三束）──
    poly(d, [
        (CX - 100, HEAD_CY - 74), (CX - 96, HEAD_CY - 112),
        (CX - 20, HEAD_CY - 130), (CX + 62, HEAD_CY - 118),
        (CX + 102, HEAD_CY - 78), (CX + 74, HEAD_CY - 46),
        (CX + 34, HEAD_CY - 74), (CX - 14, HEAD_CY - 52),
        (CX - 52, HEAD_CY - 78),
    ], fill=PAL["hair"])
    # 刘海高光
    poly(d, [(CX - 62, HEAD_CY - 112), (CX - 6, HEAD_CY - 122),
             (CX + 40, HEAD_CY - 112), (CX + 20, HEAD_CY - 98),
             (CX - 30, HEAD_CY - 100)], fill=PAL["hair_mid"])
    # 两侧鬓发（垂到下巴以下）
    for sign in (-1, 1):
        poly(d, [
            (CX + sign * 92, HEAD_CY - 70),
            (CX + sign * 112, HEAD_CY + 10),
            (CX + sign * 118, HEAD_CY + 150),
            (CX + sign * 92, HEAD_CY + 190),
            (CX + sign * 74, HEAD_CY + 90),
            (CX + sign * 70, HEAD_CY - 30),
        ], fill=PAL["hair"])

    # ── 6. 眉毛 ──
    for sign in (-1, 1):
        bx = CX + sign * 42
        d.line([((bx - 20) * SS, (HEAD_CY - 46) * SS),
                ((bx + 18) * SS, (HEAD_CY - 54) * SS)],
               fill=PAL["hair_mid"], width=int(3.2 * SS))

    # ── 7. 眼睛 ──
    eye_y = HEAD_CY - 8
    eye_dx = 44
    open_h = 25 * (1.0 - blink)
    for sign in (-1, 1):
        ex = CX + sign * eye_dx
        if open_h < 3:
            # 闭眼 → 一条弧线
            d.arc([(ex - 24) * SS, (eye_y - 10) * SS,
                   (ex + 24) * SS, (eye_y + 12) * SS],
                  start=200, end=340, fill=PAL["skin_line"], width=int(3 * SS))
            continue
        # 眼白
        ellipse(d, ex, eye_y, 24, open_h, PAL["eye_hi"])
        # 虹膜
        ellipse(d, ex, eye_y + open_h * 0.08, 17, min(open_h * 0.92, 21),
                PAL["eye_iris"])
        # 瞳孔
        ellipse(d, ex, eye_y + open_h * 0.10, 8, min(open_h * 0.46, 10),
                PAL["eye_deep"])
        # 高光
        ellipse(d, ex - 7, eye_y - open_h * 0.36, 5.5, 5.5, PAL["eye_hi"])
        ellipse(d, ex + 7, eye_y + open_h * 0.30, 3, 3, PAL["eye_hi"])
        # 上睫毛（压住眼睛上缘）
        d.arc([(ex - 25) * SS, (eye_y - open_h - 4) * SS,
               (ex + 25) * SS, (eye_y + open_h - 2) * SS],
              start=185, end=355, fill=PAL["hair"], width=int(4 * SS))

    # ── 8. 鼻（一个极简的点）与嘴 ──
    d.line([((CX + 2) * SS, (HEAD_CY + 40) * SS),
            ((CX + 6) * SS, (HEAD_CY + 48) * SS)],
           fill=PAL["skin_line"], width=int(2 * SS))

    mouth_y = HEAD_CY + 74
    if mouth < 0.15:
        # 微笑
        d.arc([(CX - 18) * SS, (mouth_y - 12) * SS,
               (CX + 18) * SS, (mouth_y + 10) * SS],
              start=15, end=165, fill=PAL["lips"], width=int(3.4 * SS))
    else:
        # 张嘴（说话）
        mh = 6 + 16 * mouth
        ellipse(d, CX, mouth_y, 13, mh, PAL["mouth_in"])
        ellipse(d, CX, mouth_y + mh * 0.45, 9, mh * 0.42, PAL["lips"])
        d.arc([(CX - 13) * SS, (mouth_y - mh) * SS,
               (CX + 13) * SS, (mouth_y + mh) * SS],
              start=200, end=340, fill=PAL["lips"], width=int(2 * SS))

    # ── 9. 腮红 ──
    for sign in (-1, 1):
        blush = Image.new("RGBA", (W * SS, H * SS), (0, 0, 0, 0))
        bd = ImageDraw.Draw(blush)
        ellipse(bd, CX + sign * 62, HEAD_CY + 42, 26, 15, PAL["blush"] + (110,))
        blush = blush.filter(ImageFilter.GaussianBlur(9 * SS))
        im = Image.alpha_composite(im, blush)
        d = ImageDraw.Draw(im)

    return im


def main() -> int:
    outdir = Path(__file__).resolve().parents[1] / "evidence" / "p5"
    outdir.mkdir(parents=True, exist_ok=True)

    print("=" * 70)
    print("按照片画的上半身卡通形象（代码绘制，非 AI 画图）")
    print("=" * 70)
    print()

    variants = {
        "bust_normal":  dict(blink=0.0, mouth=0.0, sway=0, hair_sway=0),
        "bust_blink":   dict(blink=1.0, mouth=0.0, sway=0, hair_sway=0),
        "bust_talk":    dict(blink=0.0, mouth=0.75, sway=1, hair_sway=1),
    }
    for name, kw in variants.items():
        big = draw_bust(**kw)
        small = big.resize((W, H), Image.LANCZOS)
        p = outdir / f"_{name}.png"
        small.save(p)
        print(f"  写出 {p.name}  ({small.size[0]}x{small.size[1]})")

    # 一张 3 联对比图
    sheet = Image.new("RGBA", (W * 3, H), (250, 250, 252, 255))
    for i, name in enumerate(variants):
        sheet.alpha_composite(Image.open(outdir / f"_{name}.png"), (i * W, 0))
    sheet.convert("RGB").save(outdir / "_bust_sheet.png")
    print(f"  写出 _bust_sheet.png（三联对比）")
    print()
    print("★ 请人工看一眼风格能不能接受。")
    print("  不能接受的话不用继续做动画 —— 那才是省时间的做法。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
