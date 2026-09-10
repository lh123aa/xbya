"""
生成 exe 图标：assets/pet/icon.ico
从 cat_base.png 裁切/缩放为多尺寸 PNG 打包进 ICO 容器（纯 PySide6，无 Pillow）
用法: python tools/make_icon.py
"""
import os
import struct
import io
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QImage, QPainter, QColor, QPixmap
from PySide6.QtCore import Qt

# 图标目标尺寸（Windows 标准集合）
SIZES = [256, 128, 64, 48, 32, 16]

# 圆形遮罩：背景白底+圆角裁切，猫居中


def load_square(img: QImage) -> QImage:
    """居中裁切为正方形"""
    side = min(img.width(), img.height())
    x = (img.width() - side) // 2
    y = (img.height() - side) // 2
    return img.copy(x, y, side, side)


def round_corners(img: QImage, radius_ratio: float = 0.18, bg: QColor = QColor(255, 255, 255, 0)):
    """圆角处理（透明背景上绘制圆角矩形遮罩）"""
    out = QImage(img.size(), QImage.Format_ARGB32)
    out.fill(Qt.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setRenderHint(QPainter.SmoothPixmapTransform, True)
    r = int(min(img.width(), img.height()) * radius_ratio)
    # 圆角矩形路径
    from PySide6.QtGui import QPainterPath
    from PySide6.QtCore import QRectF
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, img.width(), img.height()), r, r)
    p.setClipPath(path)
    p.drawImage(0, 0, img)
    p.end()
    return out


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    src = os.path.join(PROJECT_ROOT, "assets", "pet", "cat_base.png")
    img = QPixmap(src).toImage().convertToFormat(QImage.Format_ARGB32)
    sq = load_square(img)
    sq = round_corners(sq)
    # 调整位深标记
    sq = sq.convertToFormat(QImage.Format_ARGB32)

    pngs = []
    for size in SIZES:
        target = sq.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        from PySide6.QtCore import QBuffer, QIODevice
        ba = QBuffer()
        ba.open(QIODevice.ReadWrite)
        target.save(ba, "PNG")
        data_bytes = bytes(ba.data())
        pngs.append((size, data_bytes))
        print(f"  {size}px: {len(data_bytes)} bytes")

    # 组装 ICO 容器
    header = struct.pack("<HHH", 0, 1, len(pngs))
    entries = b""
    offset = 6 + 16 * len(pngs)
    data = b""
    for i, (size, png) in enumerate(pngs):
        b_byte = 0 if size >= 256 else size
        entries += struct.pack(
            "<BBBBHHII", b_byte, b_byte, 0, 0, 1, 32, len(png), offset)
        data += png
        offset += len(png)

    out_path = os.path.join(PROJECT_ROOT, "assets", "pet", "icon.ico")
    with open(out_path, "wb") as f:
        f.write(header + entries + data)
    print(f"已生成: {out_path} ({len(header + entries + data)} bytes)")


if __name__ == "__main__":
    main()
