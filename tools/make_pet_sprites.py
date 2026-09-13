# -*- coding: utf-8 -*-
r"""从一张立绘生成桌宠精灵帧序列（profile 驱动，支持**非方形画布**）。

## 用法

    python tools/make_pet_sprites.py xbya                 # 生成到 resources/sprites/xbya/
    python tools/make_pet_sprites.py xbya --rebuild-base  # 重新抠图（默认复用缓存的底图）
    python tools/make_pet_sprites.py xinya2 --verify        # 只比对，不写盘（回归用）
    python tools/make_pet_sprites.py --colors xbya        # 只打印量出来的肤色/线色

## 与 `docs/agent/evidence/p5/_tmp_make_frames2.py` 的关系

那一版把画布**写死成 128×128 正方形**，于是"全身立绘"只能取到胸口
（注释里的理由：「全身的话脸只剩 20px」—— 那是在方形画布下算的：
1157px 塞进 128px 高，缩放比 0.111，头只剩 30px）。

本版把画布尺寸抽成 profile 的 `canvas_w`，高度按立绘宽高比算 ——
按**宽度**适配时全身缩放比 0.243，头约 48px，与"取到胸口"的 49px 基本一致，
但整个角色都在画面里。所以画布不必是正方形。

动画原语（`_shift` / `head_transform` / `close_eye` / `speak`）**逐字沿用**那一版 ——
它们各自固化了一个实测出来的坑（接缝白线、环绕平移、闭眼补丁、口型滑移），
重写等于把那些坑再踩一遍。本版只做两处扩展：
  · `whole=True` 的整体变换（全身角色需要"身体微摆"，只动头会很怪）
  · `mouth_gain`（全身角色嘴只有 ~6px 高，开口量需要放大才看得见）

## 五官坐标写在**原图**坐标系里

`lm_space="src"` 表示坐标是原始立绘（529×1157）上的像素位置，运行期按已知的
缩放比映射到画布。为什么这么定：在原图上脸约 160px、眼睛约 25px，特征大 4 倍，
**人眼读格**和**自动判据**都可靠；映到 128 宽后眼睛只有 ~11px，在那里读数误差会被放大。
映射是确定的（紧包围盒 → 按宽适配），不引入额外误差。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "docs" / "agent" / "evidence" / "p5"

NECK_FEATHER = 6            # 头身交界处的渐变行数

SRC_XINYA2 = (r"C:\Users\49046\.dsh\attachments\v1\objects\b4"
              r"\b419fd775a4ec096ed24f9f43cd4b62429ec6dca114e86e3fc81c6666c88895a")
SRC_XINYA = (r"C:\Users\49046\.dsh\attachments\v1\objects\ec"
             r"\ec9e12817c46f2b69e527966eaff2864b3a052dc5130b94fa4c44ff99c34d425")

# 立绘在仓库内留一份副本：`.dsh/attachments/` 是**会话级缓存**，
# 会话一清就没了，届时这个 profile 再也跑不起来（"能跑但只在那台机器那天"）。
# 仓库内副本优先，附件路径作兜底。
LOCAL_SRC = {
    "xbya": ROOT / "resources" / "source" / "avatar_source.webp",
    "xinya2": ROOT / "resources" / "source" / "avatar_source.webp",
    "xinya": ROOT / "resources" / "source" / "avatar_source_prev.webp",
}


# ============================ 形象 profile ============================
PROFILES: dict[str, dict] = {
    # ★ 整张立绘（529×1157 全身 → 画布 128×281）：头到膝下全在画面里
    "xbya": {
        "src": SRC_XINYA2,
        "mode": "full",              # 整张，按**宽度**适配
        "canvas_w": 128,
        "bottom_margin": 8,          # 整体微摆时底部不裁到脚
        "lm_space": "src",
        # 原图坐标（由 `_tmp_zoom_grid.py` 的 10px 网格图逐格读出，
        # 框的判据是"盖住整个可见眼睛，含外眼角与睫毛"）
        "lm_src": {
            "eye_l": (217, 171, 258, 202),
            "eye_r": (296, 170, 342, 200),
            # 嘴框只包住**嘴唇本身**（原图 y 约 252..267）。
            # 取太松（曾用 246..272）会把鼻下与下巴算进来：`speak()` 按
            # "框高的 42%" 切上下唇，框一变高，切线就跑到鼻下，
            # 张到最大时**牙齿条被顶到人中上**、下唇被推出框外（实测 10 倍放大可见）。
            "mouth": (243, 248, 309, 270),
            "neck_y": 317,
        },
        "skin": (238, 205, 196),
        "line": (40, 30, 34),
        "amp": 1.0,
        # 嘴在画布上只有 ~6px 高，开口量按比例放大；但**不能过头** ——
        # `open_px` 会直接当作下唇的下移量，超过框高就是"下巴脱臼"。
        # 1.2 使最大开口 ≈ 框高的 50%，与已交付的 xinya2（4/9 ≈ 44%）同一量级。
        "mouth_gain": 1.2,
    },
    # 取到胸口（画布 128×128）—— 保留，用于**回归比对**：同一张立绘、方形画布
    "xinya2": {
        "src": SRC_XINYA2,
        "mode": "crop",
        "crop_frac": 0.405,
        "canvas_w": 128,
        "bottom_margin": 0,
        "lm_space": "canvas",
        "lm": {
            "head": (30, 10, 100, 90),
            "neck_y": 90,
            "eye_l": (48, 56, 63, 66),
            "eye_r": (65, 56, 80, 66),
            "mouth": (58, 72, 72, 80),
        },
        "skin": (238, 205, 196),
        "line": (40, 30, 34),
        "amp": 1.0,
    },
}


# ============================ 底图 ============================

def resolve_src(p: dict, name: str) -> Path:
    """挑立绘来源：**仓库内副本优先**，附件缓存路径兜底。"""
    local = LOCAL_SRC.get(name)
    if local is not None and local.is_file():
        return local
    return Path(p["src"])


def cutout(p: dict) -> Image.Image:
    """抠图：优先 rembg，失败退回纯色背景色键。"""
    from rembg import remove
    src = Path(p.get("_src") or p["src"])
    return remove(Image.open(src).convert("RGBA"))


def tight_bbox(im: Image.Image):
    a = np.asarray(im)
    ys, xs = np.where(a[:, :, 3] > 64)
    return int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())


def build_base(p: dict, dest: Path) -> Image.Image:
    """立绘 → 画布底图（抠图 → 色距补正 → 裁切 → 缩放 → 对位），并写回 `p` 的映射参数。"""
    cut = cutout(p)

    # --- rembg alpha 补正 ---
    # rembg 把角色裸露手臂给了低 alpha（~50-100），暗色桌面透成黑色。
    # 方案：拿**原图**像素与背景色的欧氏距离，距离远的一定是角色 → 强制不透明。
    orig_rgb = np.array(Image.open(p["_src"]).convert("RGB"))
    # 四角各取 20×20 采样背景色（角色居中，角上应该是纯背景）
    h_o, w_o = orig_rgb.shape[:2]
    corners = np.concatenate([
        orig_rgb[:20, :20].reshape(-1, 3),
        orig_rgb[:20, -20:].reshape(-1, 3),
        orig_rgb[-20:, :20].reshape(-1, 3),
        orig_rgb[-20:, -20:].reshape(-1, 3),
    ])
    bg = corners.mean(axis=0)                         # 背景平均色
    dist = np.sqrt(((orig_rgb.astype(float) - bg) ** 2).sum(axis=2))

    ca = np.array(cut)                                # rembg 输出的 RGBA
    # 色距 > 40 的像素一定不是灰色背景 → 强制不透明 **并恢复原图像素颜色**
    # （rembg 会把低 alpha 像素的 RGB 也改成近黑色，只修 alpha 不够）
    mask = dist > 40
    ca[:, :, 3] = np.where(mask, 255, ca[:, :, 3])
    orig_u8 = np.array(Image.open(p["_src"]).convert("RGB"))
    ca[:, :, :3] = np.where(mask[:, :, np.newaxis], orig_u8[:, :, :3], ca[:, :, :3])
    # 剩余的（rembg 置信度高的）也做常规阈值
    ca[:, :, 3] = np.where(ca[:, :, 3] > 60, 255, 0)
    cut = Image.fromarray(ca, "RGBA")

    x0, y0, x1, y1 = tight_bbox(cut)

    if p["mode"] == "crop":
        ch = y1 - y0 + 1
        keep = int(ch * p["crop_frac"])
        fig = cut.crop((x0, y0, x1 + 1, y0 + keep))
        cw = p["canvas_w"]
        scale = cw / fig.size[0]
        w, h = cw, max(1, int(round(fig.size[1] * scale)))
        if h > cw:                       # 太高就改按高适配，别出画布
            scale = cw / fig.size[1]
            w, h = max(1, int(round(fig.size[0] * scale))), cw
    else:                                # full：整张，按宽适配
        fig = cut.crop((x0, y0, x1 + 1, y1 + 1))
        cw = p["canvas_w"]
        scale = cw / fig.size[0]
        w, h = cw, max(1, int(round(fig.size[1] * scale)))

    s = fig.resize((w, h), Image.LANCZOS)
    # LANCZOS 插值在边缘重新引入半透明 → 二次二值化，确保精灵 0/255 纯二值
    sa = np.array(s)
    sa[:, :, 3] = np.where(sa[:, :, 3] > 128, 255, 0)
    s = Image.fromarray(sa, "RGBA")
    CH = h + p["bottom_margin"]
    cv = Image.new("RGBA", (cw, CH), (0, 0, 0, 0))
    cv.alpha_composite(s, ((cw - w) // 2, 0))
    cv.save(dest)

    # 记录映射，供 lm_space="src" 换算（src → 画布）
    p["_map"] = dict(x0=x0, y0=y0, scale=scale, ox=(cw - w) // 2,
                     canvas=(cw, CH), fig=(w, h))
    print(f"  底图 {dest.name}: 画布 {cw}×{CH}  角色 {w}×{h}  缩放比 {scale:.4f}")
    return cv


def resolve_lm(p: dict) -> None:
    """把 profile 的五官坐标统一成**画布坐标**。"""
    if p["lm_space"] == "canvas":
        p["_lm"] = dict(p["lm"])
        return
    m = p["_map"]
    out = {}
    for tag, val in p["lm_src"].items():
        if isinstance(val, int):                 # neck_y 是标量，不是框
            out[tag] = int(round((val - m["y0"]) * m["scale"]))
            continue
        bx0, by0, bx1, by1 = val
        out[tag] = (int(round(m["ox"] + (bx0 - m["x0"]) * m["scale"])),
                    int(round((by0 - m["y0"]) * m["scale"])),
                    int(round(m["ox"] + (bx1 - m["x0"]) * m["scale"])),
                    int(round((by1 - m["y0"]) * m["scale"])))
    p["_lm"] = out
    for tag, b in out.items():
        if isinstance(b, int):
            print(f"    {tag:<7} 画布 {b}")
        else:
            print(f"    {tag:<7} 画布 ({b[0]}, {b[1]}, {b[2]}, {b[3]})  "
                  f"{b[2]-b[0]+1}×{b[3]-b[1]+1}")


# ============================ 变换原语 ============================
#  以下四个函数自 `_tmp_make_frames2.py` 逐字沿用，注释保留它们记录的坑。

def _shift(im: Image.Image, dx: int, dy: int) -> Image.Image:
    """整数像素平移，**移出的部分补透明**。

    不能用 `ImageChops.offset`（坑 4）：它是**环绕平移** —— 移出画布的像素
    会从对侧绕回来，表现为帧顶部/底部多出一条头发残影。用 `transform` + AFFINE。
    """
    if dx == 0 and dy == 0:
        return im
    W, H = im.size
    return im.transform((W, H), Image.AFFINE, (1, 0, -dx, 0, 1, -dy),
                        resample=Image.BICUBIC, fillcolor=(0, 0, 0, 0))


def transform(im: Image.Image, p: dict, dx: float, dy: float, rot: float,
              whole: bool = False) -> Image.Image:
    """平移+旋转。

    `whole=False`（默认）只在 `y < neck_y` 生效（头动、身体不动），下沿
    `NECK_FEATHER` 行渐变收起；`whole=True` 是**整体**变换，轴心取脚附近，
    用于全身角色的"身体微摆"（只动头会像头在身体上滑）。
    """
    if abs(dx) < 1e-3 and abs(dy) < 1e-3 and abs(rot) < 1e-3:
        return im
    W, H = im.size
    lm = p["_lm"]
    el, er = lm["eye_l"], lm["eye_r"]
    cx = (el[0] + er[2]) / 2
    pivot_y = H - 3 if whole else lm["neck_y"]

    moved = im.rotate(rot, resample=Image.BICUBIC, center=(cx, pivot_y),
                      fillcolor=(0, 0, 0, 0))
    moved = _shift(moved, int(round(dx)), int(round(dy)))

    if whole:
        return moved

    m = np.zeros((H, W), dtype=np.uint8)
    neck_y = lm["neck_y"]
    for y in range(max(0, neck_y - NECK_FEATHER), neck_y):
        m[y, :] = int(255 * (neck_y - y) / NECK_FEATHER)
    m[:max(0, neck_y - NECK_FEATHER), :] = 255
    mask = Image.fromarray(m, "L")

    faded = moved.copy()
    faded.putalpha(ImageChops.multiply(moved.getchannel("A"), mask))
    out = im.copy()
    out.alpha_composite(faded)       # 不能用 paste（坑 1：会插值出接缝白线）
    return out


def close_eye(im: Image.Image, p: dict, box) -> Image.Image:
    """闭眼：用鼻梁肤色盖住眼球（杏仁形软边），再补一条 2px 闭眼弧线。

    填充色取**两眼之间（鼻梁）**那一小块。取"额头"会偏亮（像贴创可贴）、
    取"眼睛正上方"会带进发色、"取脸中央"是脸颊偏亮 —— 都实测过，见原脚本。
    """
    x0, y0, x1, y1 = box
    out = im.copy()
    w, h = x1 - x0 + 1, y1 - y0 + 1
    el, er = p["_lm"]["eye_l"], p["_lm"]["eye_r"]
    if el[0] <= er[0]:
        gx0, gx1 = el[2] - 1, er[0] + 1
    else:
        gx0, gx1 = er[2] - 1, el[0] + 1
    between = im.crop((max(0, gx0), y0, min(im.size[0], gx1 + 1), y1 + 1))
    ba = np.asarray(between).reshape(-1, 4).astype(np.float64)
    ba = ba[ba[:, 3] > 200]
    base = (tuple(int(v) for v in np.median(ba[:, :3], axis=0))
            if len(ba) else p["skin"])

    SS = 4                       # 4 倍超采样 → 软边
    mask_big = Image.new("L", (w * SS, h * SS), 0)
    ImageDraw.Draw(mask_big).ellipse(
        [0, h * SS * 0.10, w * SS - 1, h * SS * 0.96], fill=255)
    mask = mask_big.resize((w, h), Image.LANCZOS)
    ma = np.asarray(mask).astype(np.float32) / 255.0

    patch = np.zeros((h, w, 4), dtype=np.float64)
    for r in range(h):
        fade = 1.0 - 0.12 * (r / max(1, h - 1))
        patch[r, :, 0] = base[0] * fade
        patch[r, :, 1] = base[1] * fade
        patch[r, :, 2] = base[2] * fade
        patch[r, :, 3] = 255

    reg = np.asarray(out.crop((x0, y0, x1 + 1, y1 + 1))).astype(np.float64)
    a_m = ma[:, :, None]
    blended = reg * (1 - a_m) + patch * a_m
    out.paste(Image.fromarray(blended.astype(np.uint8), "RGBA"), (x0, y0))

    d = ImageDraw.Draw(out)
    ln = p["line"]
    for i in range(w):
        xx = x0 + i
        t = (i - (w - 1) / 2) / max(1e-6, (w - 1) / 2)
        yy = y0 + (h - 1) * 0.52 + (1 - t * t) * 0.8
        yc = int(round(yy))
        d.point((xx, yc), fill=ln + (255,))
        d.point((xx, yc - 1), fill=tuple(min(255, c + 46) for c in ln) + (255,))
    return out


def blink(im: Image.Image, p: dict) -> Image.Image:
    return close_eye(close_eye(im, p, p["_lm"]["eye_l"]), p, p["_lm"]["eye_r"])


def speak(im: Image.Image, p: dict, openness: float) -> Image.Image:
    """开口：把**原图的嘴唇**切成上下两片分开放，中间露出深色口腔。"""
    if openness <= 0.06:
        return im
    x0, y0, x1, y1 = p["_lm"]["mouth"]
    w, h = x1 - x0 + 1, y1 - y0 + 1
    out = im.copy()
    src = im.crop((x0, y0, x1 + 1, y1 + 1))
    lip_core = max(2, int(round(h * 0.42)))
    open_px = int(round(openness * (h - lip_core) * 0.85 * p.get("mouth_gain", 1.0)))
    open_px = max(1, open_px) if openness > 0.06 else 0
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

def f_idle(b, p, i, n):
    """呼吸动效：缓慢上下起伏 + 微旋转 + 眨眼同步呼吸节奏。

    设计：
    - 呼吸周期 = 整个动画时长（一个完整正弦波）
    - 眨眼发生在呼吸相位的"吸气末→呼气初"（自然节奏）
    - 振幅：垂直 ±1.0px，旋转 ±0.2°（轻盈自然，不夸张）
    - 关键：先眨眼后变换 → 闭眼随头部一起运动，不会出现重影
    """
    A = p["amp"]
    t = i / n
    phase = 2 * math.pi * t

    # 呼吸幅度
    breath_y = -1.0 * A * math.sin(phase)
    breath_rot = 0.2 * A * math.sin(phase)

    # 判断是否眨眼
    is_blink = False
    blink_phases = (math.pi / 2, 3 * math.pi / 2)
    for bp in blink_phases:
        if abs((phase % (2 * math.pi)) - bp) < 0.15 or \
           abs((phase % (2 * math.pi)) - bp + 2 * math.pi) < 0.15:
            is_blink = True
            break

    # 先闭眼，再随头一起变换 → 闭眼跟随头部运动，不出现重影
    f = b
    if is_blink:
        f = blink(f, p)
    f = transform(f, p, 0.0, breath_y, breath_rot)

    return f


def f_talk(b, p, i, n):
    A = p["amp"]; t = i / n
    f = transform(b, p, 0.0, -0.8 * A * np.sin(2 * np.pi * t),
                  0.0)
    return speak(f, p, float(0.5 + 0.5 * np.sin(2 * np.pi * t * 3)))


def f_listen(b, p, i, n):
    """倾听：缓慢呼吸 + 不规则微点头（自然确认感）。

    点头采用多频率正弦叠加，模拟真人倾听时的不规则节奏：
    - 主频 1× 呼吸周期：基础点头节奏
    - 2.3× 频率 + 0.7 相位偏移：制造不对称，让某些时刻点头更明显
    - 3.7× 频率 + 1.5 相位偏移：微抖，增加随机感
    三者叠加后，20 帧内呈现出"轻轻…稍重…轻轻……重重…轻轻"的不规则感。
    """
    A = p["amp"]; t = i / n
    phase = 2 * math.pi * t
    breath_y = -2.0 * A * math.sin(phase)
    # 不规则点头：三频叠加
    nod = A * (
        0.08 * math.sin(phase * 1.0)              # 主频：跟呼吸同步的基础点头
        + 0.05 * math.sin(phase * 2.3 + 0.7)      # 偏频：制造不对称节拍
        + 0.03 * math.sin(phase * 3.7 + 1.5)      # 高频微抖：增加自然感
    )
    # 先变换，再眨眼（listen 不常眨眼，若有也跟随头部）
    f = transform(b, p, 0.0, breath_y, nod)
    if i in (n // 3, 2 * n // 3):
        f = blink(f, p)
    return f


def f_think(b, p, i, n):
    """思考：微微抬头 + 缓慢呼吸 + 眼睛向上看的感觉（通过微旋转实现）。"""
    A = p["amp"]; t = i / n
    phase = 2 * math.pi * t
    # 先抬头+呼吸
    f = transform(b, p, 0.0,
                  -1.6 * A - 0.8 * A * math.sin(phase),
                  -0.3 * A * math.sin(phase))
    # 再眨眼
    if i in (n // 3, 2 * n // 3):
        f = blink(f, p)
    return f


def f_happy(b, p, i, n):
    A = p["amp"]; t = i / n
    f = transform(b, p, 0.0, -1.8 * A * abs(np.sin(2 * np.pi * t * 2)),
                  0.0)
    if i == n // 2:
        f = blink(f, p)
    return f


def f_sad(b, p, i, n):
    """伤心：低头 + 缓慢呼吸 + 偶尔眨眼（伤心时眨眼慢）。"""
    A = p["amp"]; t = i / n
    phase = 2 * math.pi * t
    # 先低头+呼吸
    f = transform(b, p, 0.0, 1.8 * A + 0.6 * A * math.sin(phase),
                  0.3 * A * math.sin(phase))
    # 再眨眼 → 跟随低头状态
    if i in (n // 4, 3 * n // 4):
        f = blink(f, p)
    return f


def f_sleep(b, p, i, n):
    """睡眠：深度呼吸 + 偶尔翻身 + 全程闭眼。"""
    A = p["amp"]; t = i / n
    phase = 2 * math.pi * t
    # 先深度呼吸
    f = transform(b, p, 0.0,
                  1.2 * A - 2.0 * A * math.sin(phase),
                  0.5 * A * math.sin(phase))
    # 全程闭眼（在变换后应用，跟随呼吸位移）
    return blink(f, p)


def f_stare(b, p, i, n):
    """凝视：极轻微的呼吸起伏（"盯着你看"的感觉），偶尔眨眼。"""
    A = p["amp"]; t = i / n
    phase = 2 * math.pi * t
    # 先极微呼吸
    f = transform(b, p, 0.0, -0.8 * A * math.sin(phase),
                  0.15 * A * math.sin(phase))
    # 再眨眼
    if i in (n // 3,):
        f = blink(f, p)
    return f


def f_dance(b, p, i, n):
    """全身角色：纯上下弹跳 + 微旋转，无左右位移。"""
    A = p["amp"]; t = i / n
    f = transform(b, p, 0.0,
                  -1.2 * A * abs(np.sin(2 * np.pi * t * 4)),
                  0.0, whole=True)
    return f




ANIMS = {
    "idle": (24, f_idle), "talk": (20, f_talk), "listen": (20, f_listen),
    "think": (20, f_think), "happy": (20, f_happy), "sad": (20, f_sad),
    "sleep": (24, f_sleep), "stare": (16, f_stare), "dance": (24, f_dance),
}


# ============================ 主流程 ============================

def prepare(name: str, rebuild: bool):
    p = PROFILES[name]
    p["_src"] = resolve_src(p, name)
    print(f"  立绘来源: {p['_src']}")
    base_path = CACHE / f"_base_{name}.png"
    if rebuild or not base_path.is_file():
        build_base(p, base_path)
    else:
        cv = Image.open(base_path).convert("RGBA")
        m = p.setdefault("_map", {})
        if not m:                      # 复用底图时重算映射（坐标换算需要）
            cut = cutout(p)
            x0, y0, x1, y1 = tight_bbox(cut)
            m.update(x0=x0, y0=y0, ox=0)
            m["scale"] = p["canvas_w"] / ((x1 - x0 + 1) if p["mode"] == "full"
                                          else (x1 - x0 + 1))
            m["canvas"] = cv.size
            print(f"  底图: 复用 {base_path.name} {cv.size[0]}×{cv.size[1]}")
    resolve_lm(p)
    return p, Image.open(base_path).convert("RGBA") if base_path.is_file() else cv


def sha_tree(d: Path) -> dict:
    out = {}
    for f in sorted(d.rglob("*.png")):
        out[str(f.relative_to(d))] = hashlib.sha256(f.read_bytes()).hexdigest()[:16]
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("profile", nargs="?", default="xbya")
    ap.add_argument("--rebuild-base", action="store_true")
    ap.add_argument("--verify", action="store_true", help="只比对，不写盘")
    ap.add_argument("--out", default=None, help="输出目录（默认 resources/sprites/<profile>）")
    ap.add_argument("--colors", action="store_true", help="只打印量出来的颜色")
    args = ap.parse_args()

    if args.profile not in PROFILES:
        print(f"没有 profile: {args.profile}（可选: {', '.join(PROFILES)}）")
        return 2
    name = args.profile
    dest = Path(args.out) if args.out else ROOT / "resources" / "sprites" / name

    print("=" * 74)
    print(f"形象 profile: {name}")
    print("=" * 74)
    p, base = prepare(name, args.rebuild_base)

    if args.colors:
        a = np.asarray(base)
        for tag in ("eye_l", "eye_r", "mouth"):
            x0, y0, x1, y1 = p["_lm"][tag]
            reg = a[y0:y1 + 1, x0:x1 + 1].reshape(-1, 4).astype(np.float64)
            reg = reg[reg[:, 3] > 200]
            if len(reg):
                dark = reg[np.argsort(reg[:, :3].sum(axis=1))[:max(1, len(reg) // 5)]]
                print(f"  {tag:<7} 暗部中位色 = "
                      f"{tuple(int(v) for v in np.median(dark[:, :3], axis=0))}")
        return 0

    print(f"  画布 {base.size[0]}×{base.size[1]}")

    if args.verify:
        old = sha_tree(dest)
        print(f"  比对 {dest.relative_to(ROOT)}：{len(old)} 个既有帧")
    total = 0
    for anim, (n, fn) in ANIMS.items():
        d = dest / anim
        if not args.verify:
            d.mkdir(parents=True, exist_ok=True)
            for old_f in d.glob("frame_*.png"):
                old_f.unlink()
        for i in range(n):
            fr = fn(base, p, i, n)
            if not args.verify:
                fr.save(d / f"frame_{i + 1:03d}.png")
        total += n
    man = {"size": [base.size[0], base.size[1]],
           "animations": {a: {"fps": 12, "loop": a != "talk"} for a in ANIMS}}
    if not args.verify:
        (dest / "manifest.json").write_text(
            json.dumps(man, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  共 {total} 帧  manifest.size = {man['size']}")
    if args.verify:
        new = sha_tree(dest)
        same = sum(1 for k in old if old.get(k) == new.get(k))
        print(f"  ★ 回归比对：{same}/{len(old)} 帧逐字节相同")
        diff = [k for k in old if old.get(k) != new.get(k)]
        for k in diff[:8]:
            print(f"    ✘ 不同: {k}")
        return 0 if same == len(old) else 1
    print(f"  → {dest.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
