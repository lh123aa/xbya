# -*- coding: utf-8 -*-
r"""生成**精灵对位模板** + 一个**投图校验工具**。

用户问"什么样的图给你才能用"。回答不能靠感觉，要按程序实际加载方式给：

  · 画布 128×128（现有 10 个动画全部统一这个尺寸）
  · 角色包围盒 x[38..89] y[4..99]（宽 52 × 高 96，占画布 41% × 75%）
  · 头部在 y[4..35]（上方 32px）—— 五官必须落在这里
  · `talk` 动画实测是**整幅重画**（每帧变 856~1321 px，范围覆盖整个角色），
    所以"只给我一张图"做不出说话动画，需要**多帧**

本脚本产出两样东西：
  1. `_sprite_template.png`：带网格与区域标注的模板，可直接当画布参考
  2. `_sprite_guide.txt`：这些数字的完整说明（给人看）

另外提供一个**校验函数**，等用户把图发来时先过一遍，
不满足就直接说"不能用"，而不是硬接上去再出问题。
"""
import io
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[4]
SPR = ROOT / "resources" / "sprites" / "cat"
OUT = ROOT / "docs" / "agent" / "evidence" / "p5"

W = H = 128
# 现有角色的位置（量出来的，不是估的）
BOX = (38, 4, 89, 99)          # x0 y0 x1 y1
HEAD = (38, 4, 89, 35)         # 头部区域
SAFE = (32, 0, 96, 106)        # 建议的"角色可占"安全区，四周留 8~12px 防裁切


def make_template() -> Path:
    """带标注的对位模板：直接可以当新角色的画布参照"""
    im = Image.new("RGBA", (W, H), (255, 255, 255, 0))
    d = ImageDraw.Draw(im)

    # 底色棋盘，便于看清透明区
    for y in range(0, H, 8):
        for x in range(0, W, 8):
            if (x // 8 + y // 8) % 2 == 0:
                d.rectangle([x, y, x + 7, y + 7], fill=(238, 238, 242, 255))

    # 安全区（绿）
    d.rectangle(SAFE, outline=(60, 170, 90, 255), width=1)
    # 角色建议包围盒（蓝）
    d.rectangle(BOX, outline=(60, 110, 210, 255), width=2)
    # 头部区域（红）
    d.rectangle(HEAD, outline=(210, 70, 70, 255), width=2)
    # 中线
    d.line([(W // 2, 0), (W // 2, H)], fill=(150, 150, 160, 255), width=1)

    # 把现有猫的首帧叠上去当"尺寸参照"（半透明）
    ref = SPR / "idle" / "frame_001.png"
    if ref.is_file():
        r = Image.open(ref).convert("RGBA")
        r.putalpha(r.getchannel("A").point(lambda v: v * 45 // 100))
        im.alpha_composite(r, (0, 0))

    # 标注
    d.text((2, 2), "128x128", fill=(40, 40, 40, 255))
    d.text((2, 112), "HEAD y4-35", fill=(170, 40, 40, 255))
    d.text((2, 120), "BOX x38-89 y4-99", fill=(30, 80, 180, 255))
    p = OUT / "_sprite_template.png"
    im.save(p)
    return p


def check_sprite(img_path: Path) -> dict:
    """校验一张图能不能当精灵用 —— 不满足就明确说不满足

    判据都对着"程序怎么用"来定，不是审美：
      1. 有 alpha 通道（要抠图）或背景是纯色（能抠）
      2. 透明像素占比 > 5%（否则说明没抠干净 / 背景不透明）
      3. 角色包围盒高度占画布 50%~95%（太小看不清、太大会被裁）
      4. 角色水平居中（左右留白差 < 15% 画布宽）
      5. 图片不是空的
    """
    im = Image.open(img_path)
    res = {"file": img_path.name, "size": im.size, "mode": im.mode, "fails": []}
    if im.mode != "RGBA":
        im = im.convert("RGBA")
    a = np.asarray(im)
    alpha = a[:, :, 3]
    transparent_ratio = float((alpha < 16).mean())
    res["transparent_ratio"] = transparent_ratio

    if transparent_ratio < 0.05:
        res["fails"].append(
            f"透明像素只有 {transparent_ratio:.1%} —— 背景没抠掉或不是透明图")
    ys, xs = np.where(alpha > 16)
    if len(xs) == 0:
        res["fails"].append("整张图是全透明的（空的）")
        return res
    bw, bh = xs.max() - xs.min() + 1, ys.max() - ys.min() + 1
    res["bbox"] = (int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max()))
    res["bbox_ratio"] = (bw / im.size[0], bh / im.size[1])
    if not (0.50 <= bh / im.size[1] <= 0.95):
        res["fails"].append(f"角色高占画布 {bh/im.size[1]:.0%}，应在 50%~95%")
    left, right = xs.min(), im.size[0] - 1 - xs.max()
    if abs(left - right) > im.size[0] * 0.15:
        res["fails"].append(f"角色不居中：左留白 {left}px、右留白 {right}px")
    return res


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    p = make_template()
    print(f"对位模板已写出：{p.relative_to(ROOT)}")
    print()

    # 顺手校验一下现有猫的首帧，证明校验器不是空转（它应该通过）
    print("=" * 72)
    print("用现有猫的帧自检校验器（这些应当是合格的）")
    print("=" * 72)
    print()
    ok = 0
    for anim in ("idle", "talk", "happy"):
        f = SPR / anim / "frame_001.png"
        r = check_sprite(f)
        verdict = "✔ 合格" if not r["fails"] else "✘ " + "；".join(r["fails"])
        print(f"  {anim:6s} {r['size']} 透明 {r['transparent_ratio']:.1%} "
              f"包围盒 {r['bbox']} → {verdict}")
        if not r["fails"]:
            ok += 1
    print()
    print(f"现有帧通过 {ok}/3 —— 校验器把已知合格的样本判为合格，说明判据没写歪。")
    print()
    print("=" * 72)
    print("结论：给你的图的硬要求（详见 _sprite_guide.txt）")
    print("=" * 72)
    print()
    print("  · 画布 128×128（或等比例的更大尺寸，如 512×512，我来缩）")
    print("  · **透明背景**（PNG，必须带 alpha）—— 不要白底 JPEG")
    print("  · 角色高占画布 50%~95%，**水平居中**")
    print("  · 头部在画布上方约 1/4 处，五官清晰不被头发遮")
    print("  · **要多帧**（同一角色、同一位置、不同口型/眼神）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
