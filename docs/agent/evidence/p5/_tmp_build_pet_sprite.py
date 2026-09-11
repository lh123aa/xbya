# -*- coding: utf-8 -*-
r"""把生成的立绘接成桌宠精灵：**先校验、再抠图、再缩放**，每一步都留证据。

流程（每一步都可能失败，失败就停在这里，不硬往下做）：
  1. **读图并体检**：尺寸、是否透明、背景是什么
  2. **抠图**：优先 rembg；失败则退回"纯色背景色键抠图"
  3. **裁到上半身并居中**：按角色实际包围盒裁，不是按画布裁
  4. **缩到 128×128 并对位**：按 `_sprite_template.png` 的包围盒位置
  5. ★ **小尺寸可读性检查**：把结果放大回来看，五官还在不在
     —— 这一关不过，动画做了也没意义

为什么第 5 步最重要：这张图 1776×2256，而桌宠只显示 128px。
**约 14 倍的缩小**会让细发丝、眼睛高光、嘴的细线全部消失。
所以必须在做动画之前先看小尺寸效果。
"""
import io
import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[4]
OUT = ROOT / "docs" / "agent" / "evidence" / "p5"
SRC = Path(r"C:\Users\49046\.dsh\attachments\v1\objects\ec"
           r"\ec9e12817c46f2b69e527966eaff2864b3a052dc5130b94fa4c44ff99c34d425")

TARGET = 128
# 模板实测的角色包围盒（现有猫的位置，新角色对齐到同一处）
T_BOX = (38, 4, 89, 99)          # x0 y0 x1 y1


def report(title):
    print()
    print("=" * 74)
    print(title)
    print("=" * 74)
    print()


def main() -> int:
    report("① 读图与体检")
    im = Image.open(SRC)
    print(f"  文件    : {SRC.name}")
    print(f"  尺寸    : {im.size[0]}x{im.size[1]}  模式={im.mode}")
    rgb = im.convert("RGB")
    a = np.asarray(rgb)
    # 取四角平均，判断背景色
    corners = np.concatenate([
        a[0:40, 0:40].reshape(-1, 3), a[0:40, -40:].reshape(-1, 3),
        a[-40:, 0:40].reshape(-1, 3), a[-40:, -40:].reshape(-1, 3)])
    bg = np.median(corners, axis=0)
    print(f"  四角背景色: ({bg[0]:.0f},{bg[1]:.0f},{bg[2]:.0f})  "
          f"#{int(bg[0]):02X}{int(bg[1]):02X}{int(bg[2]):02X}")
    print(f"  四角色差  : {corners.std(axis=0).max():.1f}（小 ⇒ 背景是纯色块）")
    print(f"  是否 RGBA : {'是（已带透明）' if im.mode == 'RGBA' else '否 —— 需要抠图'}")

    report("② 抠图")
    cut = None
    try:
        from rembg import remove
        print("  用 rembg 抠图（模型首次调用会下载/加载，稍等）...")
        cut = remove(im.convert("RGBA"))
        print(f"  ✔ rembg 完成，输出模式 {cut.mode}")
    except Exception as e:                                          # noqa: BLE001
        print(f"  ✘ rembg 失败：{type(e).__name__}: {str(e)[:160]}")
        print("  → 退回「纯色背景色键抠图」（背景是纯色块，可行）")

    if cut is None:
        # 色键：与背景色距离小于阈值的判为背景
        arr = np.asarray(rgb).astype(np.float64)
        dist = np.linalg.norm(arr - bg, axis=2)
        alpha = np.where(dist < 34, 0, 255).astype(np.uint8)
        cut = rgb.convert("RGBA")
        cut.putalpha(Image.fromarray(alpha))
        print(f"  ✔ 色键抠图完成（阈值 34）")

    ca = np.asarray(cut)
    opaque = ca[:, :, 3] > 16
    print(f"  不透明像素占比: {opaque.mean():.1%}")
    ys, xs = np.where(opaque)
    if len(xs) == 0:
        print("  ✘ 抠完是空的")
        return 1
    bbox = (int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max()))
    print(f"  角色包围盒: x[{bbox[0]}..{bbox[2]}] y[{bbox[1]}..{bbox[3]}]  "
          f"宽 {bbox[2]-bbox[0]+1} × 高 {bbox[3]-bbox[1]+1}")

    report("③ 裁到上半身（去掉下方多余部分）")
    # 生成的是半身像，但可能到腰以下。按角色包围盒高度取上 78% 作为"上半身"
    x0, y0, x1, y1 = bbox
    ch = y1 - y0 + 1
    keep_h = int(ch * 0.78)
    crop = cut.crop((x0, y0, x1 + 1, y0 + keep_h))
    print(f"  裁切框: x[{x0}..{x1}] y[{y0}..{y0+keep_h-1}]  → {crop.size[0]}x{crop.size[1]}")

    report("④ 缩放到 128×128 并对位到模板包围盒")
    # 目标：角色在 128 画布里的包围盒 = T_BOX
    tw = T_BOX[2] - T_BOX[0] + 1
    th = T_BOX[3] - T_BOX[1] + 1
    ar = crop.size[0] / crop.size[1]
    tar = tw / th
    if ar > tar:                       # 太宽 → 按宽适配
        new_w, new_h = tw, max(1, int(round(tw / ar)))
    else:                              # 太高 → 按高适配
        new_h, new_w = th, max(1, int(round(th * ar)))
    small = crop.resize((new_w, new_h), Image.LANCZOS)
    canvas = Image.new("RGBA", (TARGET, TARGET), (0, 0, 0, 0))
    ox = T_BOX[0] + (tw - new_w) // 2
    oy = T_BOX[3] - new_h + 1          # 底部对齐（脚/腰在包围盒底部）
    canvas.alpha_composite(small, (ox, max(0, oy)))
    print(f"  缩放后的角色: {new_w}x{new_h}  放在 ({ox},{max(0,oy)})")
    p_small = OUT / "_pet_from_comic_128.png"
    canvas.save(p_small)
    print(f"  写出 {p_small.name}")

    report("⑤ ★ 小尺寸可读性检查（这一关决定后面能不能做动画）")
    check = Image.new("RGBA", (TARGET, TARGET), (245, 245, 248, 255))
    check.alpha_composite(canvas)
    zoom = check.resize((TARGET * 4, TARGET * 4), Image.NEAREST)
    p_zoom = OUT / "_pet_from_comic_zoom.png"
    zoom.save(p_zoom)
    print(f"  4 倍放大（最近邻，看清像素级细节）: {p_zoom.name}")

    # 量化：数一数眼睛区域还有多少可用像素
    # 眼睛大约在角色头部区域的中部；头部占角色高约 1/3
    ca2 = np.asarray(canvas)
    ys2, xs2 = np.where(ca2[:, :, 3] > 16)
    if len(xs2):
        h0, h1 = ys2.min(), ys2.max()
        head_h = (h1 - h0 + 1) // 3
        face = ca2[h0:h0 + head_h, xs2.min():xs2.max() + 1]
        print(f"  角色在 128 画布: 高 {h1-h0+1}px，其中头部约 {head_h}px")
        print(f"  脸部区域尺寸: {face.shape[1]}x{face.shape[0]} 像素")
        print()
        print(f"  ⇒ 脸部只有约 {face.shape[1]}×{face.shape[0]} 像素。")
        if face.shape[0] < 20:
            print("     **偏小**：眼睛和嘴可能要各占 2~4 px，动画帧之间几乎看不出差别。")
            print("     处置建议：把角色整体裁得更小（只到胸口），让脸占比更大；")
            print("     或接受'表情变化很小'这一事实。")
        else:
            print("     尺寸尚可，眼睛与嘴应当各能占到 4~8 px，做帧替换可行。")

    report("结论")
    print(f"  抠图方式 : {'rembg' if ca is not None and 'rembg' in str(locals().get('_',''))  else '色键/rembg（见上）'}")
    print(f"  输出     : {p_small.name}（128×128）、{p_zoom.name}（4 倍放大便于人眼判断）")
    print()
    print("  ★ 请看 _pet_from_comic_zoom.png：**在这个尺寸下还认得出是她吗？**")
    print("    认得出 → 我接着做眨眼/说话帧")
    print("    认不出 → 应该只取到胸口（脸占比更大），或换构图重生成")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
