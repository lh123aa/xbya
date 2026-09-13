# -*- coding: utf-8 -*-
r"""量新立绘（529×1157 全身）在 128 画布里的**脸部尺寸**，判断构图是否可行。

为什么必须先量：这是全身立绘，宽高比 1:2.19。塞进 128 画布意味着宽度只有 59px，
头会更小。桌宠的脸若有 40px 高才读得出五官；20px 就糊了。
先抠图、量出脸和头的位置，再决定用"全身"还是"取到大腿/腰"，不自作主张。

同时量一下**脚下留白**：字幕条要 44px，角色底边必须留出这段，
否则字幕一定压在腿上。
"""
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[4]
OUT = ROOT / "docs" / "agent" / "evidence" / "p5"
SRC = Path(r"C:\Users\49046\.dsh\attachments\v1\objects\b4"
           r"\b419fd775a4ec096ed24f9f43cd4b62429ec6dca114e86e3fc81c6666c88895a")

im = Image.open(SRC).convert("RGBA")
print(f"原图 {im.size[0]}×{im.size[1]}")

from rembg import remove
cut = remove(im)
cut.save(OUT / "_new_cutout.png")

a = np.asarray(cut)
op = a[:, :, 3] > 16
ys, xs = np.where(op)
y0, y1, x0, x1 = int(ys.min()), int(ys.max()), int(xs.min()), int(xs.max())
ch, cw = y1 - y0 + 1, x1 - x0 + 1
print(f"角色包围盒 x[{x0}..{x1}] y[{y0}..{y1}]  {cw}×{ch}  宽高比 1:{ch/cw:.2f}")

# 头顶到下巴 ≈ 头高。用"肤色列宽最大处"往下找下巴：简化做法按经验取
# 头顶往下 18% 作为下巴（成人立绘头约占身高 1/7~1/8，这里排版偏大）
# 更稳的判据：头顶以下第一个"宽度明显收窄又放开"的位置是脖子 → 下巴在它上面一点
widths = np.array([int(op[y, x0:x1 + 1].sum()) for y in range(y0, y1 + 1)])
# 脖子是最窄的一段（在身体上半部）
upper = widths[: int(len(widths) * 0.35)]
neck_rel = int(np.argmin(upper + 1000 * (np.arange(len(upper)) < len(upper) * 0.3)))
neck_y = y0 + neck_rel
print(f"最窄处（脖子候选）在 y={neck_y}  （该行宽 {widths[neck_rel]}px）")
print(f"  头顶 y={y0} → 脖子 y={neck_y}，头高约 {neck_y - y0}px  "
      f"（占全身 {(neck_y - y0) / ch:.0%}）")
print()

# ---- 缩放到不同"角色高"，看脸还剩多少 ----
print("若放进 128×128 画布，角色高度取不同值时的脸部尺寸:")
print("  角色高   角色宽   头高   脸高(约)   结论")
print("  " + "-" * 62)
for char_h in (110, 118, 128, 100, 90, 84):
    scale = char_h / ch
    cw_s = cw * scale
    head_h = (neck_y - y0) * scale
    # 脸（眼睛到嘴）约占头的 40%
    face_h = head_h * 0.42
    verdict = ("脸够大，五官能读" if face_h >= 20 else
               ("勉强，眼睛可能糊" if face_h >= 14 else "太小，认不出"))
    print(f"  {char_h:>4}     {cw_s:>5.0f}    {head_h:>4.0f}    {face_h:>4.0f}      {verdict}")

print()

# ---- 出图：三种构图对照 ----
# 画布 128；字幕条需要底部 44px 净空 → 角色底边应在 y=128-1-? 处
# 但精灵画布本身无法"避开"窗口的字幕区（字幕画在窗口坐标上），
# 所以这里同时给出"角色贴底"与"角色上移留白"两种，供人眼判断
variants = []
for name, crop_frac, char_h in (
    ("全身_贴底", 1.00, 128),
    ("全身_留白", 1.00, 100),
    ("取到膝上", 0.78, 118),
    ("取到腰上", 0.55, 110),
):
    keep = int(ch * crop_frac)
    bust = cut.crop((x0, y0, x1 + 1, y0 + keep))
    scale = char_h / keep
    w = max(1, int(round(cw * scale)))
    s = bust.resize((w, char_h), Image.LANCZOS)
    cv = Image.new("RGBA", (128, 128), (0, 0, 0, 0))
    cv.alpha_composite(s, ((128 - w) // 2, 128 - 1 - char_h))
    p = OUT / f"_newvariant_{name}.png"
    cv.save(p)
    aa = np.asarray(cv)
    yy, xx = np.where(aa[:, :, 3] > 16)
    variants.append((name, crop_frac, w, char_h, p.name,
                     int(yy.min()), int(yy.max())))
    print(f"  {name}: 裁到角色高 {keep}px → 缩放 {w}×{char_h}  "
          f"放在 y[{yy.min()}..{yy.max()}]  → {p.name}")

print()
print("提示：'全身_贴底'的角色底边到 y=127，窗口字幕条在下方，会被压住；")
print("      '全身_留白'把角色缩到 100px 并在画布底留 27px 空白，字幕条可落在空白里。")

# 标注图：把脖子候选线画出来核对
Z = 3
big = cut.resize((cut.size[0] * Z // 3, cut.size[1] * Z // 3), Image.LANCZOS)
canvas = Image.new("RGB", big.size, (250, 250, 252))
canvas.paste(big, (0, 0), big)
d = ImageDraw.Draw(canvas)
sc = Z / 3
d.line([(0, (neck_y) * sc), (canvas.size[0], (neck_y) * sc)], fill=(255, 0, 0), width=2)
d.text((4, neck_y * sc + 2), f"neck? y={neck_y}", fill=(255, 0, 0))
d.line([(0, y0 * sc), (canvas.size[0], y0 * sc)], fill=(0, 160, 0), width=2)
d.line([(0, y1 * sc), (canvas.size[0], y1 * sc)], fill=(0, 160, 0), width=2)
p = OUT / "_new_cutout_annot.png"
canvas.save(p)
print(f"标注图: {p.name}")
