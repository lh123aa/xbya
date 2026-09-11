# -*- coding: utf-8 -*-
r"""改用**颜色分割**取发色/裙色 —— 不再按比例外推（外推全错了）。

前面试了两种定位方式，都不行：
  1. 手估相对坐标 → 全框错（`face` 框到头发、`arm` 框到树叶）
  2. 以检测到的人脸为锚点按比例外推 → **仍然错**
     （`hair_top` 跑到头顶上方的树冠、`hair_side` 跑到左侧背景树）

两次错的共同点：**都在"猜位置"**。位置猜错不会报错，只会安静地取出背景的颜色。

这次换成**让颜色自己说话**：
  · 头发 = 暗色像素（明度低）中**面积最大的连通块**
  · 白裙 = 高亮低饱和像素中**面积最大的连通块**
  · 区域只在"人可能的范围"内限制（图像中部），避免选到背景的暗树影

并且把分割结果**画出来存成图**，让人一眼能判断"机器找的这块到底是不是头发"。
"""
import os
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw

SRC = Path(os.environ["USERPROFILE"]) / ".dsh" / "attachments" / "v1" / "objects" / "55" \
      / "556fecf5ca2faa42dc6cb5ef8cca579dded31d7213778aba4f4267df25725394"
OUTDIR = Path(__file__).resolve().parents[4] / "docs" / "agent" / "evidence" / "p5"

bgr = cv2.imread(str(SRC))
H, W = bgr.shape[:2]
hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
h, s, v = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
print(f"源图 {W}x{H}")


def biggest_blob(mask, min_frac=0.001):
    """返回 mask 里面积最大的连通块（去掉小碎块），以及它的统计"""
    m = (mask.astype(np.uint8)) * 255
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8))
    n, labels, stats, _ = cv2.connectedComponentsWithStats(m, 8)
    if n <= 1:
        return None, 0
    areas = stats[1:, cv2.CC_STAT_AREA]
    i = int(np.argmax(areas)) + 1
    if areas[i - 1] < min_frac * W * H:
        return None, int(areas[i - 1])
    return (labels == i), int(areas[i - 1])


def med_of(region_mask):
    px = bgr[region_mask]
    b, g, r = np.median(px, axis=0)
    return int(r), int(g), int(b)


print()
print("=" * 76)
print("分割一：头发（暗色像素的最大连通块）")
print("=" * 76)
print()
# 头发：明度低。人像里头发是最暗的大块，但背景树影也暗 —— 用
# "暗 + 位于图像中部竖条"来排除边缘树影
mid = np.zeros((H, W), np.uint8)
mid[:, int(W * 0.18):int(W * 0.82)] = 1
hair_mask = (v < 90) & (s < 200) & (mid.astype(bool))
blob, area = biggest_blob(hair_mask)
if blob is None:
    print(f"  ✘ 没找到足够大的暗色块（最大 {area} px）")
    hair_rgb = None
else:
    hair_rgb = med_of(blob)
    ys, xs = np.where(blob)
    print(f"  ✔ 找到发块：面积 {area} px（占全图 {area/(W*H):.1%}）")
    print(f"    包围盒 x[{xs.min()}..{xs.max()}] y[{ys.min()}..{ys.max()}]")
    print(f"    中位色 = #{hair_rgb[0]:02X}{hair_rgb[1]:02X}{hair_rgb[2]:02X}  {hair_rgb}")

print()
print("=" * 76)
print("分割二：白裙（高亮低饱和的最大连通块）")
print("=" * 76)
print()
dress_mask = (v > 185) & (s < 45) & (mid.astype(bool))
blob_d, area_d = biggest_blob(dress_mask)
if blob_d is None:
    print(f"  ✘ 没找到足够大的白块（最大 {area_d} px）")
    dress_rgb = None
else:
    dress_rgb = med_of(blob_d)
    ys, xs = np.where(blob_d)
    print(f"  ✔ 找到裙块：面积 {area_d} px（占全图 {area_d/(W*H):.1%}）")
    print(f"    包围盒 x[{xs.min()}..{xs.max()}] y[{ys.min()}..{ys.max()}]")
    print(f"    中位色 = #{dress_rgb[0]:02X}{dress_rgb[1]:02X}{dress_rgb[2]:02X}  {dress_rgb}")

print()
print("=" * 76)
print("分割三：肤色（人脸框内的暖色块）")
print("=" * 76)
print()
# 人脸检测给的框作为"哪块是皮肤"的初始范围，再用色相筛出皮肤像素
gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
fx, fy, fw, fh = 702, 495, 396, 396          # 上一支脚本检测出的最大脸
face_box = np.zeros((H, W), bool)
face_box[fy:fy + fh, fx:fx + fw] = True
# 皮肤：色相在橙黄区间、饱和度中低、亮度中高
skin_mask = face_box & (h >= 5) & (h <= 30) & (s >= 20) & (s <= 130) & (v >= 120)
if skin_mask.sum() < 500:
    print(f"  ✘ 脸框内肤色像素太少（{skin_mask.sum()}）")
    skin_rgb = None
else:
    skin_rgb = med_of(skin_mask)
    print(f"  ✔ 肤色像素 {skin_mask.sum()} px")
    print(f"    中位色 = #{skin_rgb[0]:02X}{skin_rgb[1]:02X}{skin_rgb[2]:02X}  {skin_rgb}")

# 可视化
vis = bgr.copy()
overlay = vis.copy()
if blob is not None:
    overlay[blob] = (0, 0, 255)          # 蓝(BGR) 标出头发块
if blob_d is not None:
    overlay[blob_d] = (0, 255, 0)        # 绿 标出裙块
if skin_mask.sum() >= 500:
    overlay[skin_mask] = (255, 200, 0)   # 青 标出肤色
cv2.addWeighted(overlay, 0.45, vis, 0.55, 0, vis)
cv2.imwrite(str(OUTDIR / "_palette_segmentation.png"), vis)
print()
print(f"分割结果可视化：{OUTDIR / '_palette_segmentation.png'}")
print("  （红=头发块，绿=裙块，青=肤色像素；**请人工确认这几块是不是真的在人身上**）")

print()
print("=" * 76)
print("给建模用的建议值")
print("=" * 76)
print()


def hexs(rgb):
    return f"#{rgb[0]:02X}{rgb[1]:02X}{rgb[2]:02X}" if rgb else "（未取到）"


print(f"  skin  = {hexs(skin_rgb)}   ← 脸框内肤色像素中位色")
print(f"  hair  = {hexs(hair_rgb)}   ← 最大暗色块中位色")
print(f"  dress = {hexs(dress_rgb)}  ← 最大高亮低饱和块中位色")
print()
print("⚠️ 仍要人工过一眼可视化图。头发块有可能把**头发+眉毛+眼睛+暗影**")
print("   一起吃进去（都很暗），那样取到的会偏黑。这是已知局限。")
