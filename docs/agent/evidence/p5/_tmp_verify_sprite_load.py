# -*- coding: utf-8 -*-
r"""离线验证：**动画控制器能不能真的加载这套新精灵图**，并合成出非空帧。

为什么不用"看一眼程序起来了"当验证：桌宠窗口很小，动画有没有加载失败
（clip 为空 → 显示空白）、有没有拿到错帧，肉眼在桌面上不一定分辨得出。
这里直接问 `AnimationController` 要答案：
  · 10 个动画各自加载到几帧（期望与 manifest 一致）
  · 各个状态切换后合成的帧是否非空（空帧 = 界面上是一片透明）
  · 帧与帧之间是否真的不同（相同 = 动画没在动）
"""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))

from PySide6.QtGui import QGuiApplication, QImage      # noqa: E402

app = QGuiApplication.instance() or QGuiApplication([])

from animation.controller import AnimationController    # noqa: E402

EXPECT = {
    "idle": 24, "talk": 20, "listen": 20, "think": 20, "happy": 20,
    "sad": 20, "sleep": 24, "stare": 16, "dance": 24, "wander": 20,
}

ctrl = AnimationController()
ok = ctrl.load_pet("xinya")
print(f"load_pet('xinya') -> {ok}")
print(f"动画数: {len(ctrl.clips)}   画布: {ctrl.get_size()}")
print()

fails = []
print("  动画     帧数  期望  加载  合成帧非空  帧间有变化")
print("  " + "-" * 58)
for name, exp in EXPECT.items():
    # 注意顺序：`AnimationClip` 是**懒加载** —— __init__ 不读盘，帧在
    # `AnimationLayer.set_clip()` 里才真正载入。所以必须**先切状态**再查帧数，
    # 反过来查会全是 0（第一版探针就是这么误报的，不是产品的缺陷）。
    ctrl.set_state(name)
    ctrl.update(0.0)
    f1 = ctrl.composite()
    ctrl.update(0.12)
    f2 = ctrl.composite()

    clip = ctrl.clips.get(name)
    n = len(clip.frames) if clip else 0
    loaded = clip is not None and clip._loaded
    if n != exp:
        fails.append(f"{name}: 帧数 {n} != 期望 {exp}")
    nonempty = (not f1.isNull()) and f1.width() == 128 and f1.height() == 128
    differs = f1 != f2
    if not nonempty:
        fails.append(f"{name}: 合成帧为空或尺寸不对")
    print(f"  {name:<8} {n:>4}  {exp:>4}   {'OK' if loaded else 'FAIL':<4}  "
          f"{'OK' if nonempty else 'FAIL':<10}  {'YES' if differs else '(same)'}")

print()
print("整段动画是否真的在动（比相邻帧更有意义）:")
print("  相邻帧 12fps 下本就该几乎一样，所以判据用**整段取样的唯一帧数**")
print()
print("  动画     帧数  唯一帧  不透明像素范围      结论")
print("  " + "-" * 62)
for name, exp in EXPECT.items():
    ctrl.set_state(name)
    seen = []
    counts = []
    for k in range(exp):
        ctrl.base_layer.time = k / 12.0
        ctrl.expr_layer.time = k / 12.0
        ctrl.overlay_layer.time = k / 12.0
        ctrl.current_state = name
        ctrl._invalidate_cache()
        img = ctrl.composite()
        seen.append(bytes(img.constBits()))
        c = 0
        for y in range(img.height()):
            for x in range(img.width()):
                if (img.pixel(x, y) >> 24) & 0xFF > 16:
                    c += 1
        counts.append(c)
    uniq = len(set(seen))
    rng = f"{min(counts)}~{max(counts)}"
    if uniq < 3:
        fails.append(f"{name}: 整段只有 {uniq} 个不同帧 —— 看起来是静的")
        verdict = "几乎静止"
    else:
        verdict = f"有 {uniq} 个不同帧"
    print(f"  {name:<8} {exp:>4}  {uniq:>5}   {rng:<18} {verdict}")

print()
ctrl.set_state("idle")
ctrl.update(0.0)
img = ctrl.composite()
cnt = 0
for y in range(img.height()):
    for x in range(img.width()):
        if (img.pixel(x, y) >> 24) & 0xFF > 16:
            cnt += 1
print(f"idle 首帧不透明像素: {cnt} / {img.width()*img.height()}"
      f"  ({cnt / (img.width()*img.height()):.1%})")
if cnt < 1000:
    fails.append(f"idle 首帧几乎全透明（{cnt} 像素）")

print()
if fails:
    print("发现问题:")
    for f in fails:
        print(f"  ✘ {f}")
    raise SystemExit(1)
print("全部通过：10 个动画都加载到预期帧数，合成帧非空且帧间有变化。")
