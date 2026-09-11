# -*- coding: utf-8 -*-
r"""量准现有精灵图的规格 —— 然后才能说清"给我什么样的图能用"。

为什么要先量：用户问"什么样的图给你才能用"。这不能凭感觉回答，
因为约束来自**程序实际怎么加载这些帧**：

  · `ui.pet_size` 决定显示尺寸
  · 每帧 PNG 的实际像素尺寸决定我能放多大
  · 眼睛与嘴在帧里的**位置与大小**决定我能不能做"局部替换"式动画
    （这是 2D 精灵做眨眼/说话的通行做法：只改眼和嘴那块像素）

所以要量三样：帧尺寸、角色在帧里的包围盒、五官的可定位程度。
"""
import io
from collections import Counter
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[4]
SPR = ROOT / "resources" / "sprites" / "cat"

print("=" * 76)
print("现有精灵图规格")
print("=" * 76)
print()

sizes = Counter()
for anim_dir in sorted(SPR.iterdir()):
    if not anim_dir.is_dir():
        continue
    frames = sorted(anim_dir.glob("*.png"))
    if not frames:
        continue
    im = Image.open(frames[0])
    sizes[im.size] += 1
    print(f"  {anim_dir.name:8s} {len(frames):3d} 帧   首帧 {im.size[0]}x{im.size[1]}  "
          f"模式={im.mode}")

print()
print(f"帧尺寸分布：{dict(sizes)}")
common = sizes.most_common(1)[0][0]
print(f"⇒ 统一尺寸：{common[0]}x{common[1]}")

# ── 量一个 idle 帧里角色的包围盒（不透明区域）──
f = sorted((SPR / "idle").glob("*.png"))[0]
im = Image.open(f).convert("RGBA")
a = np.asarray(im)
alpha = a[:, :, 3]
ys, xs = np.where(alpha > 16)
print()
print(f"以 {f.name} 为例：")
print(f"  画布      {im.size[0]}x{im.size[1]}")
if len(xs):
    print(f"  角色包围盒 x[{xs.min()}..{xs.max()}] y[{ys.min()}..{ys.max()}]")
    print(f"           宽 {xs.max()-xs.min()+1} × 高 {ys.max()-ys.min()+1}")
    print(f"  角色占画布  宽 {(xs.max()-xs.min()+1)/im.size[0]:.0%} × "
          f"高 {(ys.max()-ys.min()+1)/im.size[1]:.0%}")
    # 头部大致在包围盒上 1/3
    h0, h1 = ys.min(), ys.max()
    head_bottom = h0 + (h1 - h0) // 3
    print(f"  头部区域（上 1/3）：y[{h0}..{head_bottom}]  ⇒ "
          f"高 {head_bottom-h0+1}px")
else:
    print("  全透明？")

# ── 检查帧间是否有"只有眼/嘴变化"的帧对（说明动画怎么做的）──
frames = sorted((SPR / "talk").glob("*.png"))[:6]
print()
print(f"检查 talk 动画的前 {len(frames)} 帧差异（判断动画是整体重画还是局部改）：")
if len(frames) >= 2:
    base = np.asarray(Image.open(frames[0]).convert("RGBA")).astype(int)
    for g in frames[1:]:
        cur = np.asarray(Image.open(g).convert("RGBA")).astype(int)
        if cur.shape != base.shape:
            print(f"  {g.name} 尺寸不同，跳过")
            continue
        diff = np.abs(cur - base).sum(axis=2)
        changed = diff > 24
        if changed.sum():
            ys2, xs2 = np.where(changed)
            print(f"  {g.name}: 变化像素 {changed.sum():5d}  "
                  f"范围 x[{xs2.min()}..{xs2.max()}] y[{ys2.min()}..{ys2.max()}]")
        else:
            print(f"  {g.name}: 与首帧完全相同")

print()
print("=" * 76)
print("结论：能用什么图、不能用什么图")
print("=" * 76)
print()
print("从上面的数据可以推出三条**硬约束**（不是我随意定的）：")
print()
print(f"1. 画布尺寸要一致 —— 程序按固定尺寸逐帧换图，")
print(f"   现有帧统一为 {common[0]}x{common[1]}，新角色最好同尺寸或等比例。")
print("2. 背景必须能干净抠除 —— 抠图后的 alpha 决定角色包围盒；")
print("   背景杂色会留下半透明脏边，缩到 127px 后边缘会发黑。")
print("3. 眼与嘴必须**位置固定、清晰可辨** ——")
print("   动画要么逐帧重画（需要多张），要么局部替换眼/嘴（需要一张底图 + 五官可定位）。")
