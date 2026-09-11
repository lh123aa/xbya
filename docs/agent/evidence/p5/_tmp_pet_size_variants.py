# -*- coding: utf-8 -*-
r"""尺寸变体对比：**放在模拟桌面上看**，而不是白底单看。

为什么要在模拟桌面上比：桌宠是贴在壁纸上的小窗口。同一个立绘在纯白背景上
看着挺大，贴到壁纸上可能就"缩水"了；而且壁纸颜色会和角色的冷暖调打架。
所以这里合成一张 1920×1080 的假壁纸，把 4 个尺寸的变体各贴一份，直接比。

4 个变体的区别只在"角色在宠物窗口里占多大"：
  A  52×65   —— 复刻现有猫的比例（角色占画布 41%×75%），脸最小
  B  63×78   —— 角色占画布高 61%，脸更大，仍然留边
  C  76×95   —— 角色占画布高 74%，接近"半身像"该有的存在感
  D  88×110  —— 角色占画布高 86%，几乎顶满，脸最大但可能显得压抑
"""
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[4]
OUT = ROOT / "docs" / "agent" / "evidence" / "p5"
SRC = Path(r"C:\Users\49046\.dsh\attachments\v1\objects\ec"
           r"\ec9e12817c46f2b69e527966eaff2864b3a052dc5130b94fa4c44ff99c34d425")

PET = 127                    # config.yaml 里 ui.pet_size
TARGET = 128                 # 精灵画布
CANVAS_BOTTOM_MARGIN = 6     # 角色底部离画布底留几像素（防裁到胸口太生硬）

VARIANTS = [
    ("A 复刻猫比例", 65, (38, 4)),
    ("B 占高 61%", 78, None),
    ("C 占高 74%", 95, None),
    ("D 占高 86%", 110, None),
]


def build(cut: Image.Image, char_h: int, pos) -> Image.Image:
    """把抠好的上半身缩到 char_h 高，居中（或按 pos 对齐），放进 128 透明画布。"""
    ar = cut.size[0] / cut.size[1]
    w = max(1, int(round(char_h * ar)))
    s = cut.resize((w, char_h), Image.LANCZOS)
    cv = Image.new("RGBA", (TARGET, TARGET), (0, 0, 0, 0))
    if pos is None:
        x = (TARGET - w) // 2
        y = max(0, TARGET - CANVAS_BOTTOM_MARGIN - char_h)
    else:
        x, y = pos
    cv.alpha_composite(s, (x, y))
    return cv


def main() -> int:
    from rembg import remove
    cut = remove(Image.open(SRC).convert("RGBA"))
    a = np.asarray(cut)
    ys, xs = np.where(a[:, :, 3] > 16)
    x0, y0, x1, y1 = int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())
    ch = y1 - y0 + 1
    bust = cut.crop((x0, y0, x1 + 1, y0 + int(ch * 0.78)))
    print(f"抠图+裁上半身完成: {bust.size[0]}x{bust.size[1]}")

    # 假壁纸：柔和渐变，偏冷偏亮（接近常见的 Windows 壁纸亮度）
    W, H = 1920, 1080
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    t = (yy / H) * 0.6 + (xx / W) * 0.4
    wall = np.stack([
        232 - 46 * t, 238 - 40 * t, 246 - 30 * t], axis=2).astype(np.uint8)
    wall_img = Image.fromarray(wall)
    # 右下角放一个深色窗口，验证角色在深浅两种底色上都看得清
    d = ImageDraw.Draw(wall_img)
    d.rectangle([W - 760, H - 520, W - 40, H - 40], fill=(38, 42, 52))
    d.rectangle([W - 760, H - 520, W - 40, H - 486], fill=(58, 64, 78))
    d.text((W - 744, H - 508), "dark window", fill=(150, 158, 172))

    x_cursor = 60
    tiles = []
    # alpha_composite 要求底图也是 RGBA —— RGB 底图会 ValueError: image has wrong mode
    wall_rgba = wall_img.convert("RGBA")
    for label, char_h, pos in VARIANTS:
        sp = build(bust, char_h, pos)
        p = OUT / f"_pet_variant_{label[0]}.png"
        sp.save(p)
        # 贴到壁纸上，按真实宠物窗口尺寸
        big = sp.resize((PET, PET), Image.LANCZOS)
        wall_rgba.alpha_composite(big, (x_cursor, 120))
        d.text((x_cursor, 96), label, fill=(20, 24, 34))
        # 记录该变体脸部高度
        aa = np.asarray(sp)
        ys2, xs2 = np.where(aa[:, :, 3] > 16)
        hh = int(ys2.max() - ys2.min() + 1)
        ww = int(xs2.max() - xs2.min() + 1)
        tiles.append((label, ww, hh, hh // 3, p.name))
        x_cursor += PET + 40

    mock = OUT / "_pet_size_mock.png"
    wall_rgba.convert("RGB").save(mock)

    print()
    print("变体         角色尺寸   脸高约   文件")
    print("-" * 62)
    for label, ww, hh, fh, name in tiles:
        print(f"{label:<12} {ww:>3}x{hh:<3}    {fh:>2}px    {name}")
    print()
    print(f"模拟桌面已写出: {mock.name}  ({W}x{H})")
    print("  左到右 4 个变体贴在浅色渐变壁纸上，右下有深色窗口作深底对照。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
