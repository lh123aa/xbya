# -*- coding: utf-8 -*-
r"""两种构图对照：**取到胸口（方画布 128×128）** vs **整张立绘（竖画布 128×280）**。

## 为什么值得重算这一遍

上一轮把 xinya2 定成 `crop_frac=0.405`（取到胸口），理由写在
`_tmp_make_frames2.py` 的注释里：「全身的话脸只剩 20px」。

那个结论**只在"画布必须是 128×128 正方形"的前提下成立** ——
把 1157px 高的全身塞进 128px 高，缩放比 0.111，头（约 270px）只剩 30px。

但画布**不必是正方形**：`AnimationController` 的尺寸直接读
`manifest.json` 的 `size`，`_relayout_pet()` 也是按 `get_size()` 算的。
若按**宽度**适配（画布 128×280），缩放比 = 128/529 = 0.242，
头 ≈ 270×0.242 ≈ 65px —— 与取到胸口的 69px **几乎一样大**，
但整个角色都在画面里。

所以本脚本把两种构图都按**真实窗口**渲染出来，用同一套代码路径
（真 `PetWindow` + 真 `paintEvent` + 真 `_draw_subtitle`），出图再决定。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
OUT = ROOT / "docs" / "agent" / "evidence" / "p5"
sys.path.insert(0, str(ROOT))

import numpy as np                                              # noqa: E402
from PIL import Image                                           # noqa: E402
from PySide6.QtCore import Qt                                   # noqa: E402
from PySide6.QtGui import QImage, QColor                        # noqa: E402

BUBBLE = "好的，我先把桌面上的截图整理一下，然后告诉你有几张"
CANVAS_W = 128
SUB_AREA = 44

app = None
from PySide6.QtWidgets import QApplication                       # noqa: E402
app = QApplication.instance() or QApplication([])
from ui.pet_window import PetWindow                              # noqa: E402


def pil_to_qimage(im: Image.Image) -> QImage:
    im = im.convert("RGBA")
    data = im.tobytes("raw", "RGBA")
    q = QImage(data, im.width, im.height, QImage.Format.Format_RGBA8888)
    return q.copy()          # 脱离 data 生命周期


def build_full_body(cut: Image.Image) -> Image.Image:
    """整张立绘按**宽度**适配到 128，画布高度按比例取（贴底）。"""
    a = np.asarray(cut)
    ys, xs = np.where(a[:, :, 3] > 64)
    x0, y0, x1, y1 = int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())
    fig = cut.crop((x0, y0, x1 + 1, y1 + 1))
    fw, fh = fig.size
    scale = CANVAS_W / fw
    w, h = CANVAS_W, max(1, int(round(fh * scale)))
    s = fig.resize((w, h), Image.LANCZOS)
    cv = Image.new("RGBA", (CANVAS_W, h), (0, 0, 0, 0))
    cv.alpha_composite(s, (0, 0))
    return cv


def render_face(base: Image.Image, label: str):
    """把 base 当作精灵喂给**真** PetWindow，渲染出窗口自身。"""
    cw, ch = base.size
    W, H = cw + 100, ch + 100

    win = PetWindow()
    win.load_pet("xinya2")                 # 只为把 clips/状态机建起来
    ctrl = win.anim_controller
    ctrl.width, ctrl.height = cw, ch
    qimg = pil_to_qimage(base)
    ctrl.composite = lambda: qimg          # 替换掉真实合成，喂入要评估的底图
    win.setFixedSize(W, H)
    win._relayout_pet()                    # ★ 真实布局函数，不复制它的算法
    win.bubble_text = BUBBLE
    win.bubble_timer = 99999

    img = QImage(W, H, QImage.Format.Format_ARGB32)
    img.fill(QColor(0, 0, 0, 0))
    win.render(img)                        # ★ 真实 paintEvent（含阴影与字幕）

    # 量：角色包围盒 vs 字幕条
    flat = img.convertToFormat(QImage.Format.Format_RGBA8888)
    arr = np.frombuffer(flat.constBits(), dtype=np.uint8)
    a = arr.reshape(H, flat.bytesPerLine())[:, : W * 4].reshape(H, W, 4)
    op = a[:, :, 3] > 16
    reg = op[win.pet_y:win.pet_y + ch, win.pet_x:win.pet_x + cw]
    ys, xs = np.where(reg)
    bx0, bx1 = win.pet_x + int(xs.min()), win.pet_x + int(xs.max())
    by0, by1 = win.pet_y + int(ys.min()), win.pet_y + int(ys.max())
    sub_y = H - SUB_AREA + 2
    head_px = None
    info = dict(label=label, canvas=(cw, ch), window=(W, H), pet=(win.pet_x, win.pet_y),
                char=(bx0, bx1, by0, by1), char_wh=(bx1 - bx0 + 1, by1 - by0 + 1),
                sub_y=sub_y, overlap=max(0, by1 - sub_y + 1), gap=sub_y - by1 - 1,
                cx_off=(bx0 + bx1) / 2 - (W - 1) / 2)
    win.deleteLater()
    return img, info


def checker(img: QImage, tile=12) -> QImage:
    W, H = img.width(), img.height()
    c = QImage(W, H, QImage.Format.Format_RGB32)
    from PySide6.QtGui import QPainter
    p = QPainter(c)
    for yy in range(0, H, tile):
        for xx in range(0, W, tile):
            p.fillRect(xx, yy, tile, tile,
                       QColor(58, 58, 66) if ((xx // tile + yy // tile) % 2)
                       else QColor(74, 74, 84))
    p.drawImage(0, 0, img)
    p.end()
    return c


def main() -> int:
    cut = Image.open(OUT / "_new_cutout.png").convert("RGBA")
    print(f"抠图源 _new_cutout.png  {cut.size[0]}×{cut.size[1]}")

    chest = Image.open(OUT / "_base_xinya2.png").convert("RGBA")
    full = build_full_body(cut)
    print(f"  取到胸口底图 : {chest.size[0]}×{chest.size[1]}")
    print(f"  整张立绘底图 : {full.size[0]}×{full.size[1]}")
    full.save(OUT / "_base_fullbody.png")

    items = [("A_取到胸口_方画布", chest), ("B_整张立绘_竖画布", full)]
    rendered = []
    for label, base in items:
        img, info = render_face(base, label)
        rendered.append((label, img, info))
        print()
        print(f"[{label}]")
        print(f"  画布 {info['canvas'][0]}×{info['canvas'][1]}   "
              f"窗口 {info['window'][0]}×{info['window'][1]}")
        print(f"  角色包围盒 x[{info['char'][0]}..{info['char'][1]}] "
              f"y[{info['char'][2]}..{info['char'][3]}]  "
              f"{info['char_wh'][0]}×{info['char_wh'][1]}")
        print(f"  字幕条上沿 y={info['sub_y']}   重叠 {info['overlap']}px   "
              f"空隙 {info['gap']}px   水平偏差 {info['cx_off']:+.1f}px")

    # 侧栏文字高度
    try:
        from PIL import ImageFont
        font = ImageFont.load_default()
    except Exception:
        font = None

    Z = 2
    pad = 16
    label_h = 26
    total_w = sum(img.width() * Z + pad for _, img, _ in rendered) + pad
    max_h = max(img.height() * Z for _, img, _ in rendered)
    sheet = Image.new("RGB", (total_w, max_h + label_h * 2 + pad),
                      (238, 239, 244))
    from PIL import ImageDraw
    d = ImageDraw.Draw(sheet)
    d.text((pad, 5), "两种构图（棋盘=透明；已按真实 PetWindow 渲染，含阴影与字幕）",
           fill=(24, 24, 34))
    x = pad
    for label, img, info in rendered:
        c = checker(img)
        c = c.scaled(img.width() * Z, img.height() * Z,
                     Qt.AspectRatioMode.IgnoreAspectRatio,
                     Qt.TransformationMode.FastTransformation)
        # QImage → PIL
        c2 = c.convertToFormat(QImage.Format.Format_RGBA8888)
        buf = c2.constBits()
        pim = Image.frombytes("RGBA", (c2.width(), c2.height()), bytes(buf))
        sheet.paste(pim.convert("RGB"), (x, label_h))
        d.text((x + 3, label_h + img.height() * Z + 4),
               f"{label}  画布{info['canvas'][0]}×{info['canvas'][1]}  "
               f"窗口{info['window'][0]}×{info['window'][1]}  "
               f"重叠{info['overlap']}px", fill=(60, 60, 80))
        x += img.width() * Z + pad
    out = OUT / "_compose_side_by_side.png"
    sheet.save(out)
    print()
    print(f"对照图: {out.name}  {sheet.size[0]}×{sheet.size[1]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
