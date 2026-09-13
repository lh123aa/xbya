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
from scipy import ndimage

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "docs" / "agent" / "evidence" / "p5"

NECK_FEATHER = 6            # 头身交界处的渐变行数

SRC_XINYA2 = (r"C:\Users\49046\.dsh\attachments\v1\objects\b4"
              r"\b419fd775a4ec096ed24f9f43cd4b62429ec6dca114e86e3fc81c6666c88895a")
SRC_XINYA = (r"C:\Users\49046\.dsh\attachments\v1\objects\ec"
             r"\ec9e12817c46f2b69e527966eaff2864b3a052dc5130b94fa4c44ff99c34d425")
#: 2026-09 换的立绘：白底、半身、1792×2272
SRC_NEW = (r"C:\Users\49046\.dsh\attachments\v1\objects\82"
           r"\82e8846100aa387ec5ba79504efd4a324dd4901c3699dc86214e3d93bbc8f977")

# 立绘在仓库内留一份副本：`.dsh/attachments/` 是**会话级缓存**，
# 会话一清就没了，届时这个 profile 再也跑不起来（"能跑但只在那台机器那天"）。
# 仓库内副本优先，附件路径作兜底。
LOCAL_SRC = {
    "xbya": ROOT / "resources" / "source" / "avatar_new.webp",
    "xinya2": ROOT / "resources" / "source" / "avatar_source.webp",
    "xinya": ROOT / "resources" / "source" / "avatar_source_prev.webp",
}


# ============================ 形象 profile ============================
PROFILES: dict[str, dict] = {
    # ★ 2026-09 换装：**已抠好的带 alpha 的 PNG**（1792×2272）→ 画布 128×283
    #   keyer="alpha"：**直接用源图自带的 alpha**，绝不重算 ——
    #   源图实测 256 个 alpha 值 / 58% 全透明 / 40.6% 不透明 / 1.39% 半透明，
    #   那 1.39% 就是画师做好的抗锯齿边，比任何重抠都准。
    #   先前误判为"白底图"并重算，导致 ① 白裙子被打穿 ② 臂腰之间的镂空被填满。
    #   五官坐标写在**原图坐标系**（lm_space="src"），逐轮"画框→复核→修正"标定。
    "xbya": {
        "src": SRC_NEW,
        "mode": "full",              # 整张，按**宽度**适配
        "canvas_w": 128,
        "bottom_margin": 6,
        "lm_space": "src",
        "keyer": "alpha",            # ← 用源图 alpha，不重抠
        "alpha_floor": 6.0,          # 缩放后低于此 alpha 清零（去雾状残留）
        "lm_src": {
            # 由源图 3 倍放大复核图（`_eye_read_check.png`）逐格读出并画框确认：
            #   眼框必须完整包住**可见眼睛**（虹膜 + 眼白 + 上下睫毛 + 外眼角），
            #   但**不能带上眉毛**（带上会让 close_eye 的补丁盖到眉毛，形成横带）。
            # ⚠️ 前两版都偏：v1 整体偏低 6px（虹膜露在外面 → 眨眼看不见）；
            #   v2 偏高（补丁盖到眉毛 → 错位）。程序判据（亮度/颜色连通域）
            #   都被"虹膜高光 + 睫毛阴影"带偏，最终靠**人眼读放大图 + 画框复核**定稿。
            "eye_l": (768, 380, 866, 456),
            "eye_r": (948, 382, 1064, 458),
            "mouth": (825, 522, 999, 632),
            "neck_y": 640,
        },
        "skin": (238, 205, 196),
        "line": (40, 30, 34),
        "amp": 1.0,
        # 嘴在画布上只有 ~5px 高，开口量按比例放大；但不能过头 ——
        # `open_px` 直接是下唇下移量，超过框高就是"下巴脱臼"。
        "mouth_gain": 1.2,
    },
    # 上一版立绘（529×1157 全身 → 画布 128×281）。保留以便回归比对
    # —— 旧 profile 用 rembg 路径（无 keyer 键 → 走 rembg），
    # 换 keyer 只影响新形象，不会把旧形象一起改掉。
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


def cutout_source_alpha(p: dict) -> Image.Image:
    """**直接用源图自带的 alpha**（源图已经是抠好的 PNG）

    ## 为什么这是首选（也是"裙子镂空 + 臂腰之间被填满"的根因修复）

    2026-09 那张立绘**本身就是一个带 alpha 的 PNG**，实测：
        256 个 alpha 值、58.0% 全透明、40.6% 全不透明、**1.39% 半透明**
    这 1.39% 正是**画师/工具已经做好的抗锯齿边** —— 比任何重新抠图都准。

    我先前误判它是"纯白背景"，于是用白底色键 + 边界泛洪重算 alpha，
    造成两个用户可见的破坏：

      ① **白裙子被打穿**：裙子高光 ~245 距纯白 255 只有 10，
         色键把它判成背景 → 裙面出现大片透明（实测源图裙子区 100% 不透明）
      ② **胳膊与腰之间的镂空被填满**：那处"背景"不与画布外边界连通
         （被手臂和身体围住），泛洪判它不是背景 → 强行填成不透明
         （实测源图该区 37~43% 是全透明）

    教训：**有现成的 alpha 就用它**。重新抠图只有在"源图没有 alpha / alpha 明显是错的"
    时才有理由 —— 判据是"源图 alpha 是否可用"，不是"背景看起来是什么颜色"。
    """
    src = Path(p.get("_src") or p["src"])
    im = Image.open(src)
    if im.mode != "RGBA":
        im = im.convert("RGBA")
    return im.copy()


def safe_cutout(p: dict) -> Image.Image:
    """**白底立绘**（无 alpha）的抠图：边界泛洪 + 形态学羽化

    ⚠️ 只在源图**确实没有可用 alpha** 时才该走这条路。有 alpha 的图请用
    `cutout_source_alpha`（`keyer: "alpha"`）—— 见那个函数的 docstring，
    重算 alpha 会把白裙子打穿、把臂腰间隙填满。

    ## 为什么不用 rembg

    rembg 在"纯白背景 + 白裙子"这类立绘上表现反而更差，实测（1792×2272）：
        rembg + 硬二值化 : 100 个连通域，**92 个微小碎片**，**51 个孔洞**
        rembg 软 alpha   : 39% 像素是半透明（整条手臂都被判成半透明）

    ## 判据链

    背景是**纯白**时也**不能用"距离白近"直接判背景**：白裙子高光 ~245，
    到纯白距离只有 10，会被一起打穿。真正区分背景与角色的是**是否与画布边界连通**：

      ① 到纯白距离 <= white_t  → 候选背景（宽松，含压缩噪声）
      ② 其中与画布四边连通的才是真背景 —— 白裙子不连通边界 → 完整保留
      ③ 前景填孔洞 + 去 <min_frag 的碎片（细发丝保留，噪点丢弃）
      ④ 硬掩码高斯羽化 → 抗锯齿边；再用颜色距离夹紧（深色发丝收紧、浅色裙边放松）
    """
    src = Path(p.get("_src") or p["src"])
    rgb = np.array(Image.open(src).convert("RGB")).astype(np.float32)

    d = np.sqrt(((255.0 - rgb) ** 2).sum(axis=2))
    whitish = d <= p.get("white_t", 18.0)

    lab, n = ndimage.label(whitish)
    border_labels = set()
    if n:
        border_labels = (set(lab[0, :].tolist()) | set(lab[-1, :].tolist())
                         | set(lab[:, 0].tolist()) | set(lab[:, -1].tolist()))
        border_labels.discard(0)
    bg = (np.isin(lab, list(border_labels)) if border_labels
          else np.zeros(whitish.shape, dtype=bool))

    fg = ~bg
    fg = ndimage.binary_fill_holes(fg)
    lab2, n2 = ndimage.label(fg)
    if n2 > 1:
        sizes = ndimage.sum(fg, lab2, range(1, n2 + 1))
        fg = np.isin(lab2, np.where(sizes >= p.get("min_frag", 64))[0] + 1)

    lo, hi = p.get("soft_lo", 2.0), p.get("soft_hi", 26.0)
    soft = ndimage.gaussian_filter(fg.astype(np.float32), sigma=p.get("feather", 0.8))
    color_soft = np.clip((d - lo) / max(1e-6, hi - lo), 0.0, 1.0)
    a = np.clip(np.maximum(soft, fg.astype(np.float32) * color_soft), 0.0, 1.0)
    core = ndimage.binary_erosion(fg, iterations=p.get("core_erode", 3))
    a = np.where(core, 1.0, a)

    return Image.fromarray(np.dstack([rgb, a * 255.0]).astype(np.uint8), "RGBA")


def cutout(p: dict) -> Image.Image:
    """抠图分发。

    取哪种方式**由源图自身的性质决定**，不是由"背景看起来是什么颜色"决定：

      `alpha`（默认）— 源图已带可用 alpha → **直接用它**，绝不重算
      `white`        — 源图是无 alpha 的白底图 → 边界泛洪
      `rembg`        — 兜底（复杂背景）
    """
    keyer = p.get("keyer", "alpha")
    if keyer == "alpha":
        return cutout_source_alpha(p)
    if keyer == "white":
        return safe_cutout(p)
    from rembg import remove
    src = Path(p.get("_src") or p["src"])
    return remove(Image.open(src).convert("RGBA"))


def tight_bbox(im: Image.Image):
    a = np.asarray(im)
    ys, xs = np.where(a[:, :, 3] > 64)
    return int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())


def build_base(p: dict, dest: Path) -> Image.Image:
    """立绘 → 画布底图（抠图 → 裁切 → 缩放 → 对位），并写回 `p` 的映射参数。

    ## 三处关键点（都直接对应用户可见的问题）

    1. **抠图方式取决于源图有没有 alpha**，不取决于背景颜色。
       源图已是带 alpha 的 PNG → `cutout_source_alpha` 直接用它的 alpha。
       重算 alpha 的代价是实测过的：白裙子被打穿、胳膊与腰之间的镂空被填满。

    2. **不做 rembg 的"色距补正"**。那段代码是**为 rembg 的错误输出打的补丁**
       （rembg 把裸露手臂的 alpha 压到 50~100），它只会把错误固化下来。

    3. **不硬二值化**。旧版结尾是：
           sa[:, :, 3] = np.where(sa[:, 3] > 128, 255, 0)
       LANCZOS 缩放产生的抗锯齿 alpha 被这一行全部压成 0/255，
       128px 宽的精灵上就是一条肉眼可见的阶梯 —— 这就是"毛刺"。
       现在保留软 alpha，只把极低 alpha 的外围雾气清零。
    """
    cut = cutout(p)
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
    # ★ 保留软 alpha（抗锯齿），只清掉极低 alpha 的外围雾气。
    #   旧的 `> 128 → 255 else 0` 是毛刺的直接来源。
    sa = np.array(s).astype(np.float32)
    floor = p.get("alpha_floor", 8.0)
    sa[:, :, 3] = np.where(sa[:, :, 3] < floor, 0.0, sa[:, :, 3])
    s = Image.fromarray(sa.astype(np.uint8), "RGBA")
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


def close_eye(im: Image.Image, p: dict, box, cover: float = 1.0) -> Image.Image:
    """闭眼：用鼻梁肤色盖住眼球（杏仁形软边），再补一条闭眼弧线。

    Args:
        cover: 遮盖比例 0~1。1.0 = 全闭；0.5 = 半闭（用于眨眼的过渡帧，
            让"张开→闭合"不是硬切，观感自然得多）。

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

    # 全闭/半闭的椭圆覆盖范围。
    # ⚠️ 上边界必须从 **-0.15**（而不是 0.10）起：
    #   眼框的上缘会擦到上睫毛/双眼皮，从 0.10 起会让那一行留下深色残影
    #   （逐行扫描实测：全闭帧 y=41~44 仍有 1~4 个暗像素，就是它）。
    #   椭圆顶部略微溢出框外，让覆盖从框外开始，残影才彻底消失。
    top = h * (-0.15 if cover >= 0.85 else (0.05 + (1.0 - cover) * 0.45))
    bot = h * (1.02 if cover >= 1.0 else 0.42 + cover * 0.30)

    SS = 4                       # 4 倍超采样 → 软边
    mask_big = Image.new("L", (w * SS, h * SS), 0)
    ImageDraw.Draw(mask_big).ellipse(
        [0, top * SS, w * SS - 1, bot * SS], fill=255)
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
    # 闭眼弧线：只在**接近全闭**时画。
    #
    # ⚠️ 两个实测出来的坑：
    #   ① **只画 1px**。原来画 yc 和 yc-1 两行（说是"给高光"），
    #      在 13×11 的眼框上就是一条 2px 粗的全宽黑条 —— 放大看像"拿记号笔
    #      划了一道"，而不是闭眼的褶皱（逐行扫描实测：y=45 有 13 个暗像素、
    #      y=46 有 9 个，其余行 0~4 个，那条就是它）。
    #   ② **位置在上 1/3，不是正中**。闭眼时褶皱在**原瞳孔上缘附近**
    #      （约框高 30%），画在 0.52 会让整条线落在眼睛中间，观感是"眼睛
    #      被横切成两半"。
    if cover >= 0.85:
        # 颜色比纯线色浅一档，避免在小尺寸上糊成黑块
        arc = tuple(min(255, c + 30) for c in ln)
        ymid = y0 + (h - 1) * 0.32
        for i in range(w):
            xx = x0 + i
            t = (i - (w - 1) / 2) / max(1e-6, (w - 1) / 2)
            # 中间略低、两端上翘，形成自然的眼睑弧
            yy = ymid + (1 - t * t) * 0.9
            d.point((xx, int(round(yy))), fill=arc + (255,))
    return out


def blink(im: Image.Image, p: dict) -> Image.Image:
    """全闭眼"""
    return close_eye(close_eye(im, p, p["_lm"]["eye_l"]), p, p["_lm"]["eye_r"])


def half_blink(im: Image.Image, p: dict) -> Image.Image:
    """半闭（眨眼过渡帧）：眼皮垂到一半，仍看得见一点瞳孔下缘"""
    return close_eye(close_eye(im, p, p["_lm"]["eye_l"], cover=0.5),
                     p, p["_lm"]["eye_r"], cover=0.5)


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

def blink_by_blend(im: Image.Image, p: dict, alpha: float) -> Image.Image:
    """**素材层眨眼**：把"闭眼立绘"按 alpha 混进眼框 —— 不做任何形状推断。

    这是 `docs/agent/blink-solutions.md` 推荐的正解（方案 1/2）。
    与 `close_eye()` 的根本区别：

      | | close_eye（旧） | blink_by_blend（本函数） |
      |---|---|---|
      | 眼睛从哪来 | **合成**：肤色椭圆 / 压扁 / 采样 | **真实**：另一张立绘 |
      | 依赖 | 眼框坐标必须**精确** | 只需知道**贴哪一块**（余量宽容） |
      | 13px 下的表现 | 露出色块或虹膜残留 | 与睁眼图同源，观感一致 |

    为什么合成路线在 128px 画布上必然失败（实测）：
        整只眼睛只有 **13×11 = 143 px**，虹膜占 61 px。
        "盖住 61 个虹膜像素"同时"边缘与周围皮肤无缝"这两件事，
        在这个像素预算下无法同时满足 —— 7 种合成做法全部失败，见那份文档。

    Args:
        alpha: 0=睁眼（返回原图），1=全闭。中间值给"半闭"过渡帧。

    Returns:
        混合后的图。**没有 `src_closed` 素材时原样返回** —— 宁可不眨眼，
        也不贴一块假的上去（用户反馈的"位置不对"本质是"看着就是假的"）。
    """
    if alpha <= 0.01:
        return im
    closed = p.get("_closed")
    if closed is None:
        return im                     # 没有闭眼素材 → 不眨眼（见 docstring）
    box = p.get("_lm", {}).get("eye_l")
    box2 = p.get("_lm", {}).get("eye_r")
    if box is None:
        return im
    out = im.copy()
    a = max(0.0, min(1.0, float(alpha)))
    for b in (box, box2):
        if b is None:
            continue
        # 外扩 2px：让混入区域略大于眼框，避免边缘出现"贴片"分界线
        x0, y0, x1, y1 = b
        x0, y0 = max(0, x0 - 2), max(0, y0 - 2)
        x1 = min(im.size[0] - 1, x1 + 2)
        y1 = min(im.size[1] - 1, y1 + 2)
        region = im.crop((x0, y0, x1 + 1, y1 + 1))
        region2 = closed.crop((x0, y0, x1 + 1, y1 + 1))
        out.paste(Image.blend(region, region2, a), (x0, y0))
    return out


def _prand(seed: float) -> float:
    """确定性伪随机 [0,1) —— 由坐标/帧号算出，**同输入必得同输出**

    为什么不用 `random`：精灵帧是**离线预渲染**的，必须可复现
    （同一份 profile 重跑要得到逐字节相同的 208 帧，D25 就是靠这条做的回归）。
    `random` 会引入不可复现的抖动，让"重建后 208/208 帧相同"这条验收失效。
    这里用 sin 哈希（经典 fract(sin(x)*43758.5453)），确定、无依赖、分布够均匀。

    ⚠️ 定义位置必须在**所有动画函数之前** —— 它对 `f_idle` / `f_listen`
       都是前置依赖，放在中间会让人读代码时误以为"上面还没定义就能用"。
    """
    x = math.sin(seed * 12.9898 + 78.233) * 43758.5453
    return x - math.floor(x)


def _prand_range(seed: float, lo: float, hi: float) -> float:
    """确定性伪随机，落在 [lo, hi)"""
    return lo + (hi - lo) * _prand(seed)


def f_idle(b, p, i, n):
    """呼吸动效：缓慢上下起伏 + 微旋转 + 顺势眨眼。

    ## 眨眼为什么必须**短**

    真人眨眼全程约 100~150ms：闭合 ~50ms、张开 ~50ms。12fps 下 1 帧 = 83ms，
    所以**闭眼只该占 1 帧**（最多 2 帧做"半闭"过渡）。

    旧实现用 `abs(phase - π/2) < 0.15` 判定，在 n=24 时相位步长 2π/24≈0.262，
    ±0.15 的窗口**连续命中 2 帧**；再叠加"±2π 的回绕判断"重复命中，
    实测闭眼持续 **7~9 帧 ≈ 0.6~0.75 秒** —— 那不是眨眼，是"瞪着眼发呆"。
    用户反馈的"优化眨眼动效"指的就是这个。

    新实现按**帧序号**精确指定（不再用相位容差），并留出半闭过渡帧：
        闭合：第 5 帧（全闭）+ 第 4/6 帧半闭
        闭合：第 17 帧（全闭）+ 第 16/18 帧半闭
    两次眨眼间隔不等（避免机械的等周期感）。

    ## 眨眼位置也做伪随机

    若永远"第 5 帧眨眼"，看久了同样是机械的。这里用 `_prand` 在**两个区间内**
    取位（第 1 次落在 18%~28%，第 2 次落在 62%~80%），既有随机感又保证
    两次眨眼不会挤在一起。取值是确定性的 → 重建帧仍逐字节可复现。
    """
    A = p["amp"]
    t = i / n
    phase = 2 * math.pi * t

    # 呼吸也换成无理数频率比：纯 1× 正弦会让"呼吸—眨眼"完全锁相，
    # 观感像节拍器；叠一层 0.618（黄金分割）后周期拉长、不再整齐。
    # 幅度：1.95px → 1.45px（用户反馈"点头幅度调小一些"；idle 是默认静止态，
    # 出现时间最长，同样的位移在这里最容易被注意到）。
    breath_y = -0.75 * A * math.sin(phase) - 0.19 * A * math.sin(phase * 1.618)
    breath_rot = 0.16 * A * math.sin(phase) + 0.05 * A * math.sin(phase * 2.414)

    # 伪随机眨眼位置（确定性）
    b1 = int(round(n * _prand_range(n + 11.0, 0.18, 0.28)))
    b2 = int(round(n * _prand_range(n + 12.0, 0.62, 0.80)))
    full = {b1, b2}
    half = {f - 1 for f in full} | {f + 1 for f in full}

    # 眨眼走**素材混合**（blink_by_blend）。没有 `src_closed` 素材时它原样返回
    # → 等于"不眨眼"。这是刻意的：见 blink_by_blend 的 docstring 与
    # `docs/agent/blink-solutions.md` —— 128px 下合成眨眼必然露破绽，
    # **宁可不眨，也不贴假的**。
    f = b
    if i in full:
        f = blink_by_blend(f, p, 1.0)
    elif i in half:
        f = blink_by_blend(f, p, 0.5)
    # 先闭眼再变换 → 闭眼随头部一起运动，不出现重影
    f = transform(f, p, 0.0, breath_y, breath_rot)
    return f


def f_talk(b, p, i, n):
    A = p["amp"]; t = i / n
    f = transform(b, p, 0.0, -0.8 * A * np.sin(2 * np.pi * t),
                  0.0)
    return speak(f, p, float(0.5 + 0.5 * np.sin(2 * np.pi * t * 3)))


def _nod_schedule(n: int) -> list:
    """生成一条**不规则**的点头幅度序列（长度 n，循环无缝）

    ## 为什么不用"多频正弦叠加"（我试过，是错的）

    先后试过两版频率叠加：

      | 方案 | 主频能量占比 | 差分标准差 | 首尾落差 |
      |------|-------------|-----------|---------|
      | 固定三频 1.0/2.3/3.7       | 66.6% | 0.0223 | 0.069 |
      | 无理数比 1.0/1.618/2.414…  | **69.5%** ↓ | 0.0164 ↓ | **0.165** ↑ |

    第二版**每一项都更差**。原因是：只有 20~24 个采样点时，
    1.618 / 2.414 这类频率会被**混叠**回低频，非但没拉长周期，
    反而让 1× 基频占比更高（更像节拍器）；额外加的大幅抖动还让
    **循环接缝**出现明显跳变（首尾落差 0.069 → 0.165）。

    ## 现在怎么做：直接写不规则的时间表

    短循环动画想要"不规律"，正确做法不是堆频率，而是**把节拍表本身写乱**：
    在 n 个帧里安放 **3 个强度不等**的点头条目（幅度 0.6 / 1.0 / 0.45），
    位置彼此不等距，其余帧只有微弱呼吸。这样每个循环内就有
    "重—轻—很轻—重"的自然节奏，且**首尾天然连续**（两端都接近 0）。

    确定性：位置与幅度都写死（不依赖 random），重建逐字节可复现（D25 验收）。
    """
    s = [0.0] * n
    # 三个点头点：位置（比例）与强度都刻意不等 —— 这就是"不固定频率"的来源
    knocks = ((0.12, 1.00), (0.47, 0.55), (0.78, 0.80))
    width = max(1.5, n * 0.09)          # 每个点头的影响半宽（帧）
    for pos, amp in knocks:
        c = pos * n
        for i in range(n):
            d = abs(i - c)
            d = min(d, n - d)           # 环绕距离 → 循环无缝
            if d < width:
                s[i] += amp * (1.0 - d / width) ** 2
    return s


def f_listen(b, p, i, n):
    """倾听：缓慢呼吸 + **不规则**微点头（自然确认感）。

    点头幅度取自 `_nod_schedule`（写死的不规则节拍表，见那里的对照实验）。

    ## 幅度为什么调小（2026-09 用户反馈"幅度调小一些"）

    实测各动画的**位移极差**（画布像素）：

        idle 1.95 | listen **4.00** | think 1.60 | happy 1.71
        sad 1.20  | sleep **4.00**  | stare 1.60 | dance 1.04

    `listen` 与 `sleep` 是**唯一的两个 4.00px**，其余都在 1.0~2.0。
    在 128px 宽的角色上，4px 的纵向位移等于**整个人上下跳了 3%** ——
    倾听是个持续状态，长时间挂在那里会显得"坐不住/在点头打拍子"。
    这里把呼吸收到一半（±2.0 → ±1.0），与 idle 的量级对齐。

    ⚠️ 注意：真正可见的"点头"是**纵向位移**，不是 `nod` 那个旋转角 ——
       实测 nod 分量极差只有 **0.088°**（几乎不可见），
       而 breath_y 是 **4.00px**。调幅度要调前者，调后者没有效果。
    """
    A = p["amp"]
    t = i / n
    phase = 2 * math.pi * t

    # 呼吸纵向位移：±2.0 → ±1.0（减半，与 idle 的 1.95px 量级对齐）
    breath_y = -1.0 * A * math.sin(phase)

    sched = _nod_schedule(n)
    # 旋转分量保持很小（实测本就不显眼），再略微收一点
    nod = A * (0.075 * sched[i % n] + 0.010 * math.sin(phase * 1.0))
    # 帧级伪随机微抖：够小（±0.010rad）不至于抖成抽搐，但足以打破规律感
    nod += A * _prand_range(i * 5.3 + n, -0.010, 0.010)

    f = transform(b, p, 0.0, breath_y, nod)

    # 眨眼位置伪随机（两个区间内取整，确定性）；走素材混合，无素材则不动
    b1 = int(round(n * _prand_range(n + 1.0, 0.20, 0.35)))
    b2 = int(round(n * _prand_range(n + 2.0, 0.62, 0.80)))
    if i in (b1 - 1, b2 - 1):          # 半闭
        f = blink_by_blend(f, p, 0.5)
    elif i in (b1, b2):                # 全闭
        f = blink_by_blend(f, p, 1.0)
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
        f = blink_by_blend(f, p, 1.0)
    return f


def f_happy(b, p, i, n):
    A = p["amp"]; t = i / n
    f = transform(b, p, 0.0, -1.8 * A * abs(np.sin(2 * np.pi * t * 2)),
                  0.0)
    if i == n // 2:
        f = blink_by_blend(f, p, 1.0)
    return f


def f_sad(b, p, i, n):
    """伤心：低头 + 缓慢呼吸 + 偶尔眨眼（伤心时眨眼慢）。

    ## 位移为什么必须"围绕 0"（与 f_sleep 同一个坑）

    原式 `1.8 + 0.6*sin` 值域 [1.2, 2.4] —— **恒为正**，取整后**每一帧都是同一个值**
    （要么全 1 要么全 2），互相关实测 `dy 全为 0`：整个动画是**死的**。
    "低头"这个姿态靠 `transform` 的常量偏移表达不了（它每帧相同 = 没有动画），
    正确做法是让呼吸**围绕 0 摆动**，低头感由 `amp` 之外的常量在
    **底图阶段**处理，而不是在逐帧变换里。
    """
    A = p["amp"]; t = i / n
    phase = 2 * math.pi * t
    # 围绕 0 摆动（±0.7 → 取整后 −1/0/+1）
    f = transform(b, p, 0.0, 0.7 * A * math.sin(phase),
                  0.3 * A * math.sin(phase))
    # 再眨眼 → 跟随低头状态
    if i in (n // 4, 3 * n // 4):
        f = blink_by_blend(f, p, 1.0)
    return f


def f_sleep(b, p, i, n):
    """睡眠：深度呼吸 + 偶尔翻身 + 全程闭眼。

    ## 为什么改成"围绕 0 摆动"（实测出来的坑）

    `transform()` 的 `_shift()` 是**整像素**的（`int(round(dy))`）。
    原式 `1.0 - 1.0*sin` 的值域是 **[0.0, 2.0]**，取整后只可能是 **0/1/2**
    —— 看似有位移，但**前后帧的差异全被"上移"吃掉**，实测互相关得到
    `dy 全为 0`（整段动画僵硬不动）。

    改成 `-1.0*sin`（值域 ±1.0，围绕 0）后取整得到 **−1/0/+1**，
    头部才真的在呼吸。**判据**：与静止参考帧做整数互相关，
    位移档位必须多于 1 个。
    """
    A = p["amp"]; t = i / n
    phase = 2 * math.pi * t
    # 围绕 0 摆动（±1.0 → 取整后 −1/0/+1，头部真的会动）
    f = transform(b, p, 0.0,
                  -1.0 * A * math.sin(phase),
                  0.3 * A * math.sin(phase))
    # 全程闭眼（在变换后应用，跟随呼吸位移）
    return blink_by_blend(f, p, 1.0)


def f_stare(b, p, i, n):
    """凝视：极轻微的呼吸起伏（"盯着你看"的感觉），偶尔眨眼。"""
    A = p["amp"]; t = i / n
    phase = 2 * math.pi * t
    # 先极微呼吸
    f = transform(b, p, 0.0, -0.8 * A * math.sin(phase),
                  0.15 * A * math.sin(phase))
    # 再眨眼
    if i in (n // 3,):
        f = blink_by_blend(f, p, 1.0)
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
    _load_closed(p, name)
    return p, Image.open(base_path).convert("RGBA") if base_path.is_file() else cv


def _load_closed(p: dict, name: str) -> None:
    """加载"闭眼立绘"并**按与底图完全相同的映射**建成闭眼底图。

    为什么必须是"同源同映射"：`blink_by_blend` 是**逐像素混合**两张底图，
    两者任何一个像素对不上（缩放比、裁切原点、画布尺寸差一点），
    混合出来的脸就会**重影**。所以这里复用同一套 `_map`，只换源图。

    没有素材时 `p["_closed"] = None` → `blink_by_blend` 原样返回 → 不眨眼。
    这是刻意设计，理由见 `docs/agent/blink-solutions.md`。
    """
    cand = p.get("src_closed")
    if not cand:
        p["_closed"] = None
        return
    cand = Path(cand)
    if not cand.is_file():
        print(f"  ⚠️ 闭眼素材不存在: {cand} → 本轮不眨眼")
        p["_closed"] = None
        return

    m = p.get("_map") or {}
    if not m:
        p["_closed"] = None
        return
    x0, y0, scale, ox = m["x0"], m["y0"], m["scale"], m["ox"]
    cw, CH = m["canvas"]

    im = Image.open(cand)
    im = im.convert("RGBA") if im.mode != "RGBA" else im
    if p.get("keyer", "alpha") != "alpha":
        im = cutout({**p, "_src": cand})
    bx0, by0, bx1, by1 = tight_bbox(im)
    fig = im.crop((bx0, by0, bx1 + 1, by1 + 1))
    w, h = m["fig"]
    s = fig.resize((w, h), Image.LANCZOS)
    sa = np.array(s).astype(np.float32)
    sa[:, :, 3] = np.where(sa[:, :, 3] < p.get("alpha_floor", 6.0), 0.0, sa[:, :, 3])
    s = Image.fromarray(sa.astype(np.uint8), "RGBA")
    cv = Image.new("RGBA", (cw, CH), (0, 0, 0, 0))
    cv.alpha_composite(s, ((cw - w) // 2, 0))
    p["_closed"] = cv
    print(f"  闭眼底图已加载: {cand.name} → {cv.size}")


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
    logger_kept: list = []          # 被保留的"ANIMS 之外"的动画名
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
    # ── 保留 ANIMS 之外的"外部动画"（如手工添加的 wander）──
    #
    # ⚠️ 缺陷：manifest 原先**只**由 ANIMS 生成，于是重跑生成器会把
    #    ANIMS 之外的动画从 manifest 里**静默删掉**，而它们的帧目录还在。
    #    后果不是"多占点磁盘" —— 加载器 `AnimationController.load_pet`
    #    是按 `manifest["animations"]` 逐个加载的（`animation/controller.py:63`），
    #    **不在 manifest 里 = 永远不加载**，那 20 帧等于白生成。
    #
    #    实测已存在这个状态：`xbya/wander/` 有 20 帧，但它的 manifest 里
    #    没有 `wander` —— 正是某次重跑留下的孤儿。
    #
    #    判据：manifest 是"加载清单"，不是"生成清单"。凡磁盘上存在且
    #    形如帧目录的动画，都要在清单里，否则用户看到的是"生成了却没生效"。
    if dest.is_dir():
        for d in sorted(dest.iterdir()):
            if not d.is_dir() or d.name in man["animations"]:
                continue
            if not list(d.glob("frame_*.png")):
                continue          # 空目录/非帧目录不算动画
            man["animations"][d.name] = {"fps": 12, "loop": True}
            logger_kept.append(d.name)
    if not args.verify:
        (dest / "manifest.json").write_text(
            json.dumps(man, ensure_ascii=False, indent=2), encoding="utf-8")
    if logger_kept:
        print(f"  保留 ANIMS 之外的动画（磁盘上已有帧）: {', '.join(logger_kept)}")
    print(f"  共 {total} 帧  manifest.size = {man['size']}")
    if args.verify:
        new = sha_tree(dest)
        same = sum(1 for k in old if old.get(k) == new.get(k))
        print(f"  ★ 回归比对：{same}/{len(old)} 帧逐字节相同")
        diff = [k for k in old if old.get(k) != new.get(k)]
        for k in diff[:8]:
            print(f"    ✘ 不同: {k}")
        return 0 if same == len(old) else 1
    # ⚠️ `--out` 允许指向仓库**之外**（测试/对比生成会这么用），
    #    此时 `relative_to(ROOT)` 会抛 ValueError 让整个生成**在最后一步崩掉** ——
    #    帧其实已经全部写好了，只有收尾这行打印失败（实测踩到）。
    #    所以这里必须容错：能相对就相对，不能就打印绝对路径。
    try:
        shown = dest.relative_to(ROOT)
    except ValueError:
        shown = dest
    print(f"  → {shown}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
