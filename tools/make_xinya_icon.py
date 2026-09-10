#!/usr/bin/env python3
"""用 Pillow 生成多尺寸 ICO —— 像素风欣雅头像"""
import os
from pathlib import Path
from PIL import Image, ImageDraw

def create_xinya_icon(output_path: str):
    """绘制像素欣雅并导出多尺寸 ICO"""
    # 32x32 像素矩阵
    PX = 8  # 每个像素格 8px → 画布 256x256

    canvas_size = 32
    img = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # 颜色定义
    C = {
        "h": (139, 90, 43),     # 棕色头发
        "s": (255, 220, 177),   # 肤色
        "w": (255, 255, 255),   # 白色衬衫/鞋子
        "b": (30, 30, 30),      # 黑色裙子/眼睛
        "p": (255, 180, 180),   # 粉红腮红
        "m": (180, 130, 70),    # 深棕头发阴影
    }

    # 像素矩阵（32x32）
    rows = [
        "................................",
        "...........hhhhhh...............",
        "..........hhhhhhhhh.............",
        ".........hhhhhhhhhhh............",
        "........hhhhhhhhhhhhh...........",
        ".......hhhhhhhhhhhhhh...........",
        "......hhhhhhhhhhhhhhh...........",
        ".....hhhhhhhhhhhhhhhh...........",
        ".....hhhbbhhhhhhhbbhhh..........",
        ".....hhhhhhhhhhhhhhhhh..........",
        "....hhhhhhhhhhhhhhhhhh..........",
        "....hhhhhhhhhhhhhhhhhh..........",
        "....hhhhhhhhhhhhhhhhhh..........",
        "....hhhhhhhhhhhhhhhhh...........",
        ".....hhhhhhhhhhhhhhhh...........",
        ".....hhhhhhhhpppphhhhh..........",
        "......hhhhhhhhhhhhhhh...........",
        ".......hhhhhhhhhhhhh............",
        "........hhhhhhhhhh..............",
        ".........hhhhhhh................",
        "...........hhh..................",
        ".................................",
        "............wwwwww..............",
        "...........wwwwwwww.............",
        "..........wwwwwwwwww............",
        "..........wwwwwwwwww............",
        "..........wwwwwwwwww............",
        "...........wwwwwwww.............",
        "............bbbbbb..............",
        ".............bbbb...............",
        ".............wwww...............",
        ".................................",
    ]

    for y, row in enumerate(rows):
        for x, ch in enumerate(row):
            if ch == ".":
                continue
            color = C.get(ch)
            if color:
                draw.point((x, y), fill=color + (255,))

    # 放大到 256x256（最近邻插值保持像素风）
    img_256 = img.resize((256, 256), Image.NEAREST)

    # 保存多尺寸 ICO（16/32/48/256）
    img_256.save(
        output_path,
        format="ICO",
        sizes=[(16, 16), (32, 32), (48, 48), (256, 256)],
    )
    size_kb = os.path.getsize(output_path) // 1024
    print(f"Generated: {output_path} ({size_kb} KB)")

if __name__ == "__main__":
    output = Path(__file__).resolve().parent.parent / "assets" / "pet" / "icon.ico"
    create_xinya_icon(str(output))
