# -*- coding: utf-8 -*-
r"""精灵帧生成器（**按 profile 驱动**，可复用于多个形象）。

与 `_tmp_make_frames.py` 的关系：那一版把坐标写死在模块常量里，
只能服务于第一个形象。这一版把「立绘来源 + 五官坐标 + 动作幅度」抽成
`PROFILES` 字典 —— 换形象只加一个 profile，动画逻辑一份、不复制。

## 三个已经踩过的坑（固化在代码里，别再犯）

1. **接缝白线**：`Image.paste(moved, (0,0), mask)` 会把颜色与 alpha 分开混合，
   半透明像素合成时插值出原图里**不存在的浅色行**。必须 `alpha_composite`。
2. **闭眼的填充色**：不能"从眼睛正上方裁一块贴回去"（那块可能含头发/眉毛），
   也不能"用脸中央肤色填纯色"（脸中央是脸颊，比眼窝亮，会留下亮补丁）。
   现在用**眉毛以下的额头**那条干净肤色带取中位数。
3. **口型不能平移原嘴唇**（会看着像嘴在脸上滑动），也不在整块嘴框上画椭圆
   （嘴框含下巴，椭圆会盖到鼻子）。现在把原唇切成上下两片、中间撑开露出口腔。

## 顺序无关
所有帧都先 `head_transform`（整幅变换），**再**在结果上画眨眼/口型。
反过来会让眼睛和嘴跟着头部二次位移。
"""
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parents[4]
OUT = ROOT / "docs" / "agent" / "evidence" / "p5"

NECK_FEATHER = 6        # 头身交界处的渐变行数


# ============================ 形象 profile ============================
PROFILES = {
    # 第二个形象：用户第二次给的立绘（529×1157 全身 → 取到胸口，脸更大）
    "xinya2": {
        "src": r"C:\Users\49046\.dsh\attachments\v1\objects\b4"
               r"\b419fd775a4ec096ed24f9f43cd4b62429ec6dca114e86e3fc81c6666c88895a",
        "crop_frac": 0.405,      # 只取上 40.5%（到胸口）—— 全身的话脸只剩 20px
        "char_h": 128,           # 角色在 128 画布里的高度（贴底）
        "bottom_margin": 0,      # 贴底（这个构图下方不需要留白，见窗口布局）
        # 五官（在 128×114 底图画布上的坐标）
        # 定位方式：`_tmp_base2_probe.py` 输出了 6 倍放大 + 10px 网格图
        # （`_base_xinya2_zoom.png`），逐格读数得到；自动判据在这张图上不可靠
        # —— 它的肤色偏冷（RGB 差小），"亮且暖"的阈值几乎筛不出脸。
        "head": (30, 10, 100, 90),
        "neck_y": 90,
        # 眼框 y 取 58~66：核对图上眼球可见部分在 y≈56~64，而**闭眼线画在框高 45% 处**，
        # 所以框要往下压一点，线才落在眼睛上。取 54~61 时实测线跑到眉毛高度，
        # 睡眠帧看起来像戴了副眼镜（见 _sheet2_sleep.png 的第一版）。
        # 眼框必须**覆盖整个可见眼睛**（含外眼角）。取窄了会只盖住内侧，
        # 外眼角仍露着 —— 实测出来是"半睁半闭"，看着像画了条眉毛
        # （见 _blink_detail.png 的第三格第一版）。
        "eye_l": (48, 56, 63, 66),
        "eye_r": (65, 56, 80, 66),
        "mouth": (58, 72, 72, 80),
        "brow_band": (52, 44, 77, 51),   # (x0, y0, x1, y1) 眉毛以上的额头干净带
        "skin": (238, 205, 196),
        "line": (40, 30, 34),
        # 动作幅度（按新角色高度重标：旧形象角色高 110，这个 128）
        "amp": 1.0,
    },
    # 第一个形象（保留，便于回归对照）
    "xinya": {
        "src": r"C:\Users\49046\.dsh\attachments\v1\objects\ec"
               r"\ec9e12817c46f2b69e527966eaff2864b3a052dc5130b94fa4c44ff99c34d425",
        "crop_frac": 0.78,
        "char_h": 110,
        "bottom_margin": 6,
        "head": (20, 12, 107, 78),
        "neck_y": 78,
        "eye_l": (58, 45, 64, 50),
        "eye_r": (65, 45, 70, 50),
        "mouth": (55, 58, 72, 68),
        "brow_band": (57, 41, 70, 43),   # (x0, y0, x1, y1)
        "skin": (239, 197, 176),
        "line": (28, 26, 32),
        "amp": 1.0,
    },
}


def build_base(p: dict, dest: Path) -> Image.Image:
    """从原始立绘产出 128×128 底图（抠图 → 裁切 → 缩放 → 对位）。"""
    from rembg import remove
    cut = remove(Image.open(Path(p["src"])).convert("RGBA"))
    a = np.asarray(cut)
    ys, xs = np.where(a[:, :, 3] > 64)
    x0, y0, x1, y1 = int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())
    ch = y1 - y0 + 1
    keep = int(ch * p["crop_frac"])
    bust = cut.crop((x0, y0, x1 + 1, y0 + keep))
    bw, bh = bust.size
    scale = p["char_h"] / bh
    if bw * scale > 128:                 # 太宽就按宽适配，别出画布
        scale = 128 / bw
    w, h = max(1, int(round(bw * scale))), max(1, int(round(bh * scale)))
    s = bust.resize((w, h), Image.LANCZOS)
    cv = Image.new("RGBA", (128, 128), (0, 0, 0, 0))
    cv.alpha_composite(s, ((128 - w) // 2, max(0, 128 - p["bottom_margin"] - h)))
    cv.save(dest)
    print(f"  底图: 裁到角色高 {keep}px → 缩放 {w}×{h}  角色包围盒见下")
    aa = np.asarray(cv)
    yy, xx = np.where(aa[:, :, 3] > 16)
    print(f"        x[{xx.min()}..{xx.max()}] y[{yy.min()}..{yy.max()}]")
    return cv


# ============================ 变换原语 ============================

def _shift(im: Image.Image, dx: int, dy: int) -> Image.Image:
    """整数像素平移，**移出的部分补透明**。

    这里曾经用 `ImageChops.offset`（坑 4）：它是**环绕平移** —— 移出画布的像素
    会从对侧绕回来。头部上移时，顶部的头发就出现在画布底部；实测表现为
    **帧顶部/底部多出一条头发残影**（见 `_sheet2_idle.png` 的 frame_016/020）。
    改用 `transform` + AFFINE，空白处填透明，没有环绕。
    """
    if dx == 0 and dy == 0:
        return im
    W, H = im.size
    return im.transform((W, H), Image.AFFINE, (1, 0, -dx, 0, 1, -dy),
                        resample=Image.BICUBIC, fillcolor=(0, 0, 0, 0))


def head_transform(im: Image.Image, p: dict, dx: float, dy: float, rot: float) -> Image.Image:
    """整幅平移+旋转，只在 y<neck_y 生效，下沿 NECK_FEATHER 行渐变收起。"""
    if abs(dx) < 1e-3 and abs(dy) < 1e-3 and abs(rot) < 1e-3:
        return im
    W, H = im.size
    neck_y = p["neck_y"]
    cx = (p["head"][0] + p["head"][2]) / 2          # 旋转轴心取脖子中心
    moved = im.rotate(rot, resample=Image.BICUBIC, center=(cx, neck_y),
                      fillcolor=(0, 0, 0, 0))
    moved = _shift(moved, int(round(dx)), int(round(dy)))

    m = np.zeros((H, W), dtype=np.uint8)
    for y in range(max(0, neck_y - NECK_FEATHER), neck_y):
        m[y, :] = int(255 * (neck_y - y) / NECK_FEATHER)
    m[:max(0, neck_y - NECK_FEATHER), :] = 255
    mask = Image.fromarray(m, "L")

    faded = moved.copy()
    faded.putalpha(ImageChops.multiply(moved.getchannel("A"), mask))
    out = im.copy()
    out.alpha_composite(faded)                       # 不能用 paste（见 docstring 坑 1）
    return out


def close_eye(im: Image.Image, p: dict, box) -> Image.Image:
    """闭眼：用**眼睛正上方的眼睑**肤色盖住眼球，再补一条 1px 的闭眼弧线。

    填充色取哪里，踩过三次坑（都留在这里）：
      · 取"眉毛以上的额头"（`brow_band`）—— 额头比眼窝**亮**，盖下去是一条
        明显的浅色横带，看起来像贴了张创可贴（见 `_sheet2_sleep.png` 第二版）。
      · 取"眼睛正上方 2~3 行的块"—— 眼框左右已贴到头发，裁出的块可能带进发色。
      · 取"脸中央肤色中位数"—— 脸颊比眼窝亮，同样留亮补丁。
    现在的做法：取**眼睛正上方 2 行**（那是眼睑本身，色调与眼窝一致），
    逐行采样、去掉偏暗的像素（发丝），再按行微微压暗。
    """
    x0, y0, x1, y1 = box
    out = im.copy()
    w, h = x1 - x0 + 1, y1 - y0 + 1
    # 填充色取**两眼之间（鼻梁）那一小块**的颜色。
    # 为什么不取"眼睛正上方"（第 4 版的做法）：那只眼睛的眼角带腮红，
    # 采样得到的偏粉，盖下去还是一块**粉色补丁**（见 _blink_detail.png 第三格）。
    # 鼻梁在两眼中间、必然无妆无发，是这个尺度上最干净的同色源。
    # 这里用两个眼框的内缘算区间，不靠 `box is p["eye_l"]` 判身份 ——
    # 字面量元组的身份不保证稳定，那种写法迟早会静默走错分支。
    el, er = p["eye_l"], p["eye_r"]
    if el[0] <= er[0]:
        gx0, gx1 = el[2] - 1, er[0] + 1
    else:
        gx0, gx1 = er[2] - 1, el[0] + 1
    between = im.crop((max(0, gx0), y0, min(im.size[0], gx1 + 1), y1 + 1))
    ba = np.asarray(between).reshape(-1, 4).astype(np.float64)
    ba = ba[ba[:, 3] > 200]
    base = (tuple(int(v) for v in np.median(ba[:, :3], axis=0))
            if len(ba) else p["skin"])
    d = ImageDraw.Draw(out)
    # 覆盖形状用**椭圆（杏仁形）+ 抗锯齿软边**，不用矩形。
    # 矩形是"一块平涂方块压在眼窝上"，即使颜色取对了也显假（实测放大 8 倍时
    # 四角能明显看出边界）。眼形本来就是杏仁状，椭圆贴合得多。
    SS = 4                                   # 4 倍超采样后再缩回，得到平滑边缘
    mask_big = Image.new("L", (w * SS, h * SS), 0)
    ImageDraw.Draw(mask_big).ellipse(
        [0, h * SS * 0.10, w * SS - 1, h * SS * 0.96], fill=255)
    mask = mask_big.resize((w, h), Image.LANCZOS)
    ma = np.asarray(mask).astype(np.float32) / 255.0
    # 按行压暗（贴着上眼睑稍亮、往下渐暗），做出眼窝的体积感
    patch = np.zeros((h, w, 4), dtype=np.float64)
    for r in range(h):
        fade = 1.0 - 0.12 * (r / max(1, h - 1))
        patch[r, :, 0] = base[0] * fade
        patch[r, :, 1] = base[1] * fade
        patch[r, :, 2] = base[2] * fade
        patch[r, :, 3] = 255
    # 用**软遮罩**把原眼睛压掉、换成眼睑色（alpha 按遮罩线性混合，边界自然过渡）
    reg = np.asarray(out.crop((x0, y0, x1 + 1, y1 + 1))).astype(np.float64)
    a_m = ma[:, :, None]
    blended = reg * (1 - a_m) + patch * a_m
    out.paste(Image.fromarray(blended.astype(np.uint8), "RGBA"), (x0, y0))
    # 闭眼线**画两行**：1px 在这张图的尺度上细到看不见（实测放大 8 倍才勉强分辨），
    # 而 2px 缩到 127 显示时仍清楚。上面一行稍浅，像睫毛的层次。
    d = ImageDraw.Draw(out)
    for i in range(w):
        xx = x0 + i
        t = (i - (w - 1) / 2) / max(1e-6, (w - 1) / 2)
        yy = y0 + (h - 1) * 0.52 + (1 - t * t) * 0.8
        yc = int(round(yy))
        ln = p["line"]
        d.point((xx, yc), fill=ln + (255,))
        d.point((xx, yc - 1), fill=tuple(min(255, c + 46) for c in ln) + (255,))
    return out


def blink(im: Image.Image, p: dict) -> Image.Image:
    return close_eye(close_eye(im, p, p["eye_l"]), p, p["eye_r"])


def speak(im: Image.Image, p: dict, openness: float) -> Image.Image:
    """开口：把**原图的嘴唇**切成上下两片分开放，中间露出深色口腔。"""
    if openness <= 0.06:
        return im
    x0, y0, x1, y1 = p["mouth"]
    w, h = x1 - x0 + 1, y1 - y0 + 1
    out = im.copy()
    src = im.crop((x0, y0, x1 + 1, y1 + 1))
    lip_core = max(2, int(round(h * 0.42)))
    open_px = int(round(openness * (h - lip_core) * 0.85))
    if open_px < 1:
        return im
    d = ImageDraw.Draw(out)
    cav = (96, 44, 48, 255) if openness > 0.5 else (146, 84, 84, 255)
    cx, cy = (x0 + x1) / 2, y0 + h * 0.52
    ow, oh = w * 0.62, open_px + 1.6
    d.ellipse([cx - ow / 2, cy - oh / 2, cx + ow / 2, cy + oh / 2], fill=cav)

    m = np.zeros((h, w), dtype=np.float32)
    for r in range(h):
        if r < lip_core:
            m[r, :] = 1.0
        elif r < lip_core + 2:
            m[r, :] = 1.0 - (r - lip_core) / 2.0
    up = src.copy()
    up.putalpha(Image.fromarray((m * 255).astype(np.uint8), "L"))
    out.alpha_composite(up, (x0, y0))

    n = np.zeros((h, w), dtype=np.float32)
    for r in range(h):
        if r >= lip_core:
            n[r, :] = 1.0
        elif r >= lip_core - 2:
            n[r, :] = 0.5 * (lip_core - r) / 2.0
    lo = src.copy()
    lo.putalpha(Image.fromarray((n * 255).astype(np.uint8), "L"))
    out.alpha_composite(lo, (x0, y0 + open_px))

    if openness > 0.4:
        tw = ow * 0.66
        d.rectangle([cx - tw / 2, cy - oh / 2 + 0.4, cx + tw / 2, cy - oh / 2 + 1.4],
                    fill=(206, 196, 190, 255))
    return out


# ============================ 各状态的动法 ============================
# 幅度都乘 profile 的 amp，换角色时只需改这一个数

def f_idle(b, p, i, n):
    A = p["amp"]
    t = i / n
    f = head_transform(b, p, 0.5 * A * np.sin(np.pi * t),
                       -1.2 * A * np.sin(2 * np.pi * t), 0.7 * A * np.sin(np.pi * t))
    if i in (n // 3, n - n // 5):
        f = blink(f, p)
    return f


def f_talk(b, p, i, n):
    A = p["amp"]
    t = i / n
    f = head_transform(b, p, 0.0, -0.8 * A * np.sin(2 * np.pi * t),
                       1.0 * A * np.sin(2 * np.pi * t * 2))
    return speak(f, p, float(0.5 + 0.5 * np.sin(2 * np.pi * t * 3)))


def f_listen(b, p, i, n):
    A = p["amp"]
    t = i / n
    return head_transform(b, p, 0.0, -0.5 * A * np.sin(2 * np.pi * t * 2),
                          2.2 * A * np.sin(2 * np.pi * t))


def f_think(b, p, i, n):
    A = p["amp"]
    t = i / n
    return head_transform(b, p, 0.0, -1.6 * A - 0.6 * A * np.sin(2 * np.pi * t * 2),
                          1.4 * A * np.sin(2 * np.pi * t))


def f_happy(b, p, i, n):
    A = p["amp"]
    t = i / n
    f = head_transform(b, p, 0.0, -1.8 * A * abs(np.sin(2 * np.pi * t * 2)),
                       2.0 * A * np.sin(2 * np.pi * t))
    if i == n // 2:
        f = blink(f, p)
    return f


def f_sad(b, p, i, n):
    A = p["amp"]
    t = i / n
    return head_transform(b, p, 0.0, 1.8 * A + 0.3 * A * np.sin(2 * np.pi * t),
                          0.5 * A * np.sin(2 * np.pi * t))


def f_sleep(b, p, i, n):
    A = p["amp"]
    t = i / n
    f = head_transform(b, p, 0.0, 1.2 * A - 1.5 * A * np.sin(2 * np.pi * t),
                       0.4 * A * np.sin(2 * np.pi * t))
    return blink(f, p)


def f_stare(b, p, i, n):
    A = p["amp"]
    t = i / n
    return head_transform(b, p, 0.0, -0.4 * A * np.sin(2 * np.pi * t), 0.0)


def f_dance(b, p, i, n):
    A = p["amp"]
    t = i / n
    return head_transform(b, p, 1.6 * A * np.sin(2 * np.pi * t * 2),
                          -1.2 * A * abs(np.sin(2 * np.pi * t * 4)),
                          4.0 * A * np.sin(2 * np.pi * t * 2))


def f_wander(b, p, i, n):
    A = p["amp"]
    t = i / n
    return head_transform(b, p, 1.8 * A * np.sin(2 * np.pi * t), 0.0,
                          3.0 * A * np.sin(2 * np.pi * t))


ANIMS = {
    "idle": (24, f_idle), "talk": (20, f_talk), "listen": (20, f_listen),
    "think": (20, f_think), "happy": (20, f_happy), "sad": (20, f_sad),
    "sleep": (24, f_sleep), "stare": (16, f_stare), "dance": (24, f_dance),
    "wander": (20, f_wander),
}


def main() -> int:
    if len(sys.argv) < 3:
        print("用法: python _tmp_make_frames2.py <profile> <输出目录> [--rebuild-base]")
        print("profile:", ", ".join(PROFILES))
        return 2
    name, dest_s = sys.argv[1], sys.argv[2]
    if name not in PROFILES:
        print(f"没有 profile: {name}")
        return 2
    p = PROFILES[name]
    dest = Path(dest_s)

    base_path = OUT / f"_base_{name}.png"
    if "--rebuild-base" in sys.argv or not base_path.is_file():
        base = build_base(p, base_path)
    else:
        base = Image.open(base_path).convert("RGBA")
        print(f"  底图: 复用 {base_path.name}")

    total = 0
    for anim, (n, fn) in ANIMS.items():
        d = dest / anim
        d.mkdir(parents=True, exist_ok=True)
        for old in d.glob("frame_*.png"):
            old.unlink()
        for i in range(n):
            fn(base, p, i, n).save(d / f"frame_{i + 1:03d}.png")
        total += n
    man = {"size": [128, 128],
           "animations": {a: {"fps": 12, "loop": a != "talk"} for a in ANIMS}}
    (dest / "manifest.json").write_text(
        json.dumps(man, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  共 {total} 帧 → {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
