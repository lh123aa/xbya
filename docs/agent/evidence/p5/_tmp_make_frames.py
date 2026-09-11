# -*- coding: utf-8 -*-
r"""生成精灵帧：**从一张静态立绘做出会呼吸、会眨眼、会说话的桌宠**。

诚实的前提（写在最前面，免得后面误读）：
  我**没有**这个角色的多张表情图，也没有骨骼分层。所以这里的动画不是"画出新表情"，
  而是**对一张图做局部变换**：
    · 呼吸/摇晃/点头 → 整幅亚像素平移 + 以脖子为轴心旋转
    · 眨眼           → 把眼白区域用周围肤色盖掉，再补一条细的闭眼弧线
    · 说话           → 在嘴的位置**直接画**张开的口腔与上下唇
  这三样在 36px 高的脸上**读得出来**（五官位置已由 `_frame_landmark_check.png` 核对）。
  做不出来的：真正的表情（笑/哭/生气）—— 那需要新的表情图，不是这类变换能变出来的。

## 三个已经踩过的坑（都在这里写好，免得重犯）

1. **接缝白线**：`Image.paste(moved, (0,0), mask)` 会把**颜色与 alpha 分开混合**，
   遮罩 50% 处颜色取半、alpha 也取半，半透明像素再合成时会插值出原图里
   **不存在的浅色行** —— 表现就是头下方一条白线。必须用 `alpha_composite`。
2. **闭眼不能"涂一片肤色"**：那样看着像眼睛被抠掉了。真正的闭眼有一条**细线**；
   而线也不能弯成抛物线（第一版弯了 ±2px，7px 高的眼睛上看着是粗锯齿）。
   现在弯幅压到 1px 以内。
3. **口型不能"平移原嘴唇"**：把原块往下挪半像素就变成"嘴在脸上滑动"。
   现在张开时直接画几何（口腔 + 上下唇），原图只在闭合时原样保留。

## 顺序无关（重要）
所有帧都先做 `head_transform`（整幅变换），**再**在结果上画眨眼/口型。
不要反过来"先画嘴再变换"——那样嘴的位置会跟着头部偏移二次位移。
"""
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parents[4]
OUT = ROOT / "docs" / "agent" / "evidence" / "p5"

# ---- 实测的关键点（128×128 画布坐标，见 `_frame_landmark_check.png`）----
HEAD = (20, 12, 107, 78)          # 头部（含头发）
NECK_Y = 78                       # 头身分界：头部变换在 y<NECK_Y 生效，下方 6 行渐变收起
                                  # （原定 72，核对图上发现 72 切在下巴上，低头时下巴会离开身体）
EYE_L = (58, 45, 64, 50)          # 左眼：像素图实测 y45~50 处脸只有 x[57..70] 宽，
EYE_R = (65, 45, 70, 50)          # 右眼：两侧紧贴头发，眼框必须落在这个窄带里
MOUTH = (55, 58, 72, 68)          # 嘴（含上下唇）
SKIN = (239, 197, 176)            # 脸部肤色中位数（实测）
LIP_UP = (168, 104, 102)          # 上唇
LIP_LO = (206, 128, 124)          # 下唇（下唇比上唇亮）
LINE = (28, 26, 32)               # 闭眼线/睫毛色


def build_base() -> Image.Image:
    """从**原始上传的立绘**直接产出 128×128 底图（抠图 → 裁上半身 → 缩放 → 对位）。

    为什么要从原图重算而不是直接用 `_pet_variant_D.png`：
      那个文件是"探索尺寸"那一轮的中间产物。成品的生成链路应当从**唯一事实来源**
      （用户给的立绘）出发，否则中间文件一旦丢失或改动，帧就无法复现。
      参数与 D 变体一致：角色高 110px、水平居中、底部留 6px。
    """
    import io

    from rembg import remove
    src = Path(r"C:\Users\49046\.dsh\attachments\v1\objects\ec"
               r"\ec9e12817c46f2b69e527966eaff2864b3a052dc5130b94fa4c44ff99c34d425")
    cut = remove(Image.open(src).convert("RGBA"))
    a = np.asarray(cut)
    ys, xs = np.where(a[:, :, 3] > 16)
    x0, y0, x1, y1 = int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())
    bust = cut.crop((x0, y0, x1 + 1, y0 + int((y1 - y0 + 1) * 0.78)))

    CHAR_H = 110
    ar = bust.size[0] / bust.size[1]
    w = max(1, int(round(CHAR_H * ar)))
    small = bust.resize((w, CHAR_H), Image.LANCZOS)
    canvas = Image.new("RGBA", (128, 128), (0, 0, 0, 0))
    canvas.alpha_composite(small, ((128 - w) // 2, max(0, 128 - 6 - CHAR_H)))
    return canvas


def load_base() -> Image.Image:
    """载入底图：优先用流水线产出的 `_pet_base_128.png`，没有就现算一份。"""
    p = OUT / "_pet_base_128.png"
    if p.is_file():
        return Image.open(p).convert("RGBA")
    b = build_base()
    b.save(p)
    return b


def head_transform(im: Image.Image, dx: float, dy: float, rot: float) -> Image.Image:
    """整幅做平移+旋转，但只有 y<NECK_Y 的部分真正动，下沿 6 行渐变收起。

    旋转轴心取脖子中心，否则"低头"会变成"头绕画布角滑走"。
    """
    W, H = im.size
    if abs(dx) < 1e-3 and abs(dy) < 1e-3 and abs(rot) < 1e-3:
        return im
    cx = (HEAD[0] + HEAD[2]) / 2
    moved = im.rotate(rot, resample=Image.BICUBIC, center=(cx, NECK_Y),
                      fillcolor=(0, 0, 0, 0))
    moved = ImageChops.offset(moved, int(round(dx)), int(round(dy)))

    m = np.zeros((H, W), dtype=np.uint8)
    for y in range(max(0, NECK_Y - 6), NECK_Y):
        m[y, :] = int(255 * (NECK_Y - y) / 6)
    m[:max(0, NECK_Y - 6), :] = 255
    mask = Image.fromarray(m, "L")

    faded = moved.copy()
    faded.putalpha(ImageChops.multiply(moved.getchannel("A"), mask))
    out = im.copy()
    out.alpha_composite(faded)
    return out


def close_eye(im: Image.Image, box) -> Image.Image:
    """闭眼：用**眼睑那一行的肤色**盖住眼球，再补一条 1px 的闭眼弧线。

    填充色踩过两次坑，都留在这里：
      · 第三版取"眼睛正上方 2~3 行"当填充块 —— 眼框左右已贴到头发，
        裁出来的块右边一列可能落在头发上，盖下去就在眼角留深色方块。
      · 第四版改成"脸中央肤色中位数填纯色" —— 脸中央是**脸颊**，比眼窝**更亮更暖**，
        结果眼睛位置出现两块**发亮的补丁**（一眼就能看出来）。
    现在的做法：填充色取**眉毛以下的额头**（实测 y41~43 整行都是干净肤色，
    所以横着采样绝不会碰到头发），再按行微微压暗，跟眼窝阴影同向。
    """
    x0, y0, x1, y1 = box
    out = im.copy()
    w, h = x1 - x0 + 1, y1 - y0 + 1
    # 额头采样带：y[41..43]，x 跨越整个眼框
    band = im.crop((min(x0, 57), 41, max(x1, 70) + 1, 44))
    ba = np.asarray(band).reshape(-1, 4)
    ba = ba[ba[:, 3] > 200]
    base = tuple(int(v) for v in np.median(ba[:, :3], axis=0)) if len(ba) else SKIN
    d = ImageDraw.Draw(out)
    for r in range(h):
        fade = 1.0 - 0.16 * (r / max(1, h - 1))     # 往下微微压暗，跟眼窝阴影同向
        col = tuple(max(0, min(255, int(c * fade))) for c in base)
        d.rectangle([x0, y0 + r, x1, y0 + r], fill=col + (255,))
    for i in range(w):
        xx = x0 + i
        t = (i - (w - 1) / 2) / max(1e-6, (w - 1) / 2)
        yy = y0 + (h - 1) * 0.45 + (1 - t * t) * 0.7     # 弯幅 0.7px：只够暗示"向下弯"
        # 线宽**严格 1px**。第一版在 yy+1 又点了一次（想压实抗锯齿），
        # 结果线变成 2~3px 的粗括号，看着不像睡着像画了眼影。
        d.point((xx, int(round(yy))), fill=LINE + (255,))
    return out


def blink(im: Image.Image) -> Image.Image:
    return close_eye(close_eye(im, EYE_L), EYE_R)


def speak(im: Image.Image, openness: float) -> Image.Image:
    """开口：把**原图的嘴唇**切成上下两片分开放，中间露出深色口腔。

    为什么不用"画两个椭圆当上下唇"（第二版的做法，也错了）：
      嘴框取的是 y[58..68]（11px），但**真正的嘴唇皮肤只占其中约 4px**，
      下面 7px 是下巴。在 11px 的画布上画椭圆，直接盖到鼻子和下巴上 ——
      实测出来是一条**竖着的棕色长条**（见 `_sheet_talk.png` 第二版）。
    正确做法是**贴着原唇形**：原块上沿当上唇、下沿当下唇，原块"消失"在
    两者的重叠处（1-a 通道），只有中间撑开的缝里才露出深色 —— 缝隙天然被嘴唇夹住。

    openness<=0.06 时**原样返回**（一个像素都不改），所以闭合帧完全等于原图唇形。
    """
    if openness <= 0.06:
        return im
    x0, y0, x1, y1 = MOUTH
    w, h = x1 - x0 + 1, y1 - y0 + 1
    out = im.copy()
    src = im.crop((x0, y0, x1 + 1, y1 + 1))

    # 原唇纵向"厚度"：嘴唇皮肤只占嘴框的一部分，用实测比例定
    lip_core = max(2, int(round(h * 0.42)))
    open_px = int(round(openness * (h - lip_core) * 0.85))
    if open_px < 1:
        return im

    # (a) 缝隙里的口腔：暖红棕，不用纯黑（纯黑看着像被挖了个洞）
    d = ImageDraw.Draw(out)
    cav = (96, 44, 48, 255) if openness > 0.5 else (146, 84, 84, 255)
    cx = (x0 + x1) / 2
    cy = y0 + h * 0.52
    ow = w * 0.62
    oh = open_px + 1.6
    d.ellipse([cx - ow / 2, cy - oh / 2, cx + ow / 2, cy + oh / 2], fill=cav)

    # (b) 上唇：原块上沿，**下边渐隐**，这样和口腔交界是软的
    m = np.zeros((h, w), dtype=np.float32)
    for r in range(h):
        if r < lip_core:
            m[r, :] = 1.0
        elif r < lip_core + 2:
            m[r, :] = 1.0 - (r - lip_core) / 2.0
    up = src.copy()
    up.putalpha(Image.fromarray((m * 255).astype(np.uint8), "L"))
    out.alpha_composite(up, (x0, y0))

    # (c) 下唇：原块下沿，**上边渐隐**，直接贴到 y0+open_px
    n = np.zeros((h, w), dtype=np.float32)
    for r in range(h):
        if r >= lip_core:
            n[r, :] = 1.0
        elif r >= lip_core - 2:
            n[r, :] = 0.5 * (lip_core - r) / 2.0
    lo = src.copy()
    lo.putalpha(Image.fromarray((n * 255).astype(np.uint8), "L"))
    out.alpha_composite(lo, (x0, y0 + open_px))

    # (d) 牙关：缝隙上缘一条浅色，避免整块死黑
    if openness > 0.4:
        tw = ow * 0.66
        d.rectangle([cx - tw / 2, cy - oh / 2 + 0.4, cx + tw / 2, cy - oh / 2 + 1.4],
                    fill=(206, 196, 190, 255))
    return out


# ---- 各状态的"动法" ----
# 每个函数都返回"先变换、再画局部"的结果，保证顺序一致

def f_idle(b, i, n):
    t = i / n
    f = head_transform(b, 0.5 * np.sin(np.pi * t), -1.2 * np.sin(2 * np.pi * t),
                       0.7 * np.sin(np.pi * t))
    if i in (n // 3, n - n // 5):
        f = blink(f)
    return f


def f_talk(b, i, n):
    t = i / n
    op = 0.5 + 0.5 * np.sin(2 * np.pi * t * 3)
    f = head_transform(b, 0.0, -0.8 * np.sin(2 * np.pi * t), 1.0 * np.sin(2 * np.pi * t * 2))
    return speak(f, float(op))


def f_listen(b, i, n):
    t = i / n
    return head_transform(b, 0.0, -0.5 * np.sin(2 * np.pi * t * 2), 2.2 * np.sin(2 * np.pi * t))


def f_think(b, i, n):
    """思考：抬头一点 + 慢摆（与聆听区分：聆听是歪头，思考是抬头）。"""
    t = i / n
    return head_transform(b, 0.0, -1.6 - 0.6 * np.sin(2 * np.pi * t * 2), 1.4 * np.sin(2 * np.pi * t))


def f_happy(b, i, n):
    t = i / n
    f = head_transform(b, 0.0, -1.8 * abs(np.sin(2 * np.pi * t * 2)), 2.0 * np.sin(2 * np.pi * t))
    if i == n // 2:
        f = blink(f)
    return f


def f_sad(b, i, n):
    """低落：低头 + 极慢的摆（"几乎不动"本身就是低落的读法）。"""
    t = i / n
    return head_transform(b, 0.0, 1.8 + 0.3 * np.sin(2 * np.pi * t), 0.5 * np.sin(2 * np.pi * t))


def f_sleep(b, i, n):
    t = i / n
    f = head_transform(b, 0.0, 1.2 - 1.5 * np.sin(2 * np.pi * t), 0.4 * np.sin(2 * np.pi * t))
    return blink(f)


def f_stare(b, i, n):
    """凝视：只有极轻微的呼吸（"盯着你看"）。"""
    t = i / n
    return head_transform(b, 0.0, -0.4 * np.sin(2 * np.pi * t), 0.0)


def f_dance(b, i, n):
    t = i / n
    return head_transform(b, 1.6 * np.sin(2 * np.pi * t * 2),
                          -1.2 * abs(np.sin(2 * np.pi * t * 4)),
                          4.0 * np.sin(2 * np.pi * t * 2))


def f_wander(b, i, n):
    """张望：慢慢从一边看到另一边（周期=整段）。"""
    t = i / n
    return head_transform(b, 1.8 * np.sin(2 * np.pi * t), 0.0, 3.0 * np.sin(2 * np.pi * t))


# 动画名 → (帧数, 生成函数)。帧数按"12fps 下的观感"定：
#   idle/sleep/dance 要更长才不显得重复；stare 就是"几乎不动"，短一点够用
ANIMS = {
    "idle":   (24, f_idle),
    "talk":   (20, f_talk),
    "listen": (20, f_listen),
    "think":  (20, f_think),
    "happy":  (20, f_happy),
    "sad":    (20, f_sad),
    "sleep":  (24, f_sleep),
    "stare":  (16, f_stare),
    "dance":  (24, f_dance),
    "wander": (20, f_wander),
}

MANIFEST = {
    "size": [128, 128],
    "animations": {name: {"fps": 12, "loop": True} for name in ANIMS},
}
# talk 在项目原 manifest 里是 loop:false（播一遍停住），保持一致
MANIFEST["animations"]["talk"]["loop"] = False


def main() -> int:
    if len(sys.argv) < 2:
        print("用法: python _tmp_make_frames.py <输出目录>")
        return 2
    dest = Path(sys.argv[1])
    base = load_base()
    print(f"底图: _pet_variant_D.png  {base.size[0]}×{base.size[1]}")

    total = 0
    for name, (n, fn) in ANIMS.items():
        d = dest / name
        d.mkdir(parents=True, exist_ok=True)
        for old in d.glob("frame_*.png"):
            old.unlink()
        for i in range(n):
            fn(base, i, n).save(d / f"frame_{i + 1:03d}.png")
        total += n
        print(f"  {name:<7} {n:>3} 帧")

    (dest / "manifest.json").write_text(
        json.dumps(MANIFEST, ensure_ascii=False, indent=2), encoding="utf-8")
    print()
    print(f"共 {total} 帧 → {dest}")
    print(f"写出 manifest.json（{len(ANIMS)} 个动画，talk 为 loop:false）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
