# -*- coding: utf-8 -*-
"""换形象辅助工具 —— 把 SOP 里最容易出错的几步变成一条命令。

## 为什么需要它

换形象的流程本身不复杂，但**有三处"不报错却出问题"的坑**，
靠人肉记 SOP 必然踩：

  1. **五官坐标标不准** —— 眼睛框偏差几个像素，眨眼就看不见或盖到眉毛。
     SOP 教人"用网格图目测"，而实测证明目测会连错两版。
     本工具直接生成**带标尺的放大复核图**，并支持"画框回看"。
  2. **manifest 与帧目录不一致** —— 加载器按 `manifest["animations"]`
     逐个加载，不在清单里的动画**永远不加载**（帧白生成）。
     本工具做一致性体检。
  3. **立绘只存在会话缓存里** —— `.dsh/attachments/` 一清就再也跑不起来。
     本工具检查仓库内是否留有副本。

## 子命令

    python tools/avatar_helper.py init <角色名> <立绘路径>
        在仓库内留副本、打印可粘贴的 profile 模板

    python tools/avatar_helper.py ruler <角色名>
        生成带标尺的放大复核图，用于读五官坐标

    python tools/avatar_helper.py check <角色名>
        体检：manifest 一致性 / 帧数 / 尺寸 / 源图副本

    python tools/avatar_helper.py preview <角色名> [动画] [帧号]
        把某一帧放大并叠上五官框，复核标注是否正确

    python tools/avatar_helper.py list
        列出所有角色与 profile 的对应关系
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SPRITES = ROOT / "resources" / "sprites"
SOURCE = ROOT / "resources" / "source"

#: 生成器里的 ANIMS（帧数）—— 用于体检时核对"帧数是否达标"。
#: 不 import 生成器（它有副作用且依赖 numpy/PIL），直接读源文本解析，
#: 避免"为了检查而拉起整个图像栈"。
def _anims_from_generator() -> dict:
    src = (ROOT / "tools" / "make_pet_sprites.py").read_text(encoding="utf-8")
    i = src.index("ANIMS = {")
    j = src.index("}", i)
    out = {}
    # 形如：  "idle": (24, f_idle), "talk": (20, f_talk),
    # 同一条里可能有多个，按 `"name": (` 切
    import re
    for m in re.finditer(r'"(\w+)"\s*:\s*\(\s*(\d+)', src[i:j]):
        out[m.group(1)] = int(m.group(2))
    return out


def _local_src_map() -> dict:
    """解析 LOCAL_SRC：角色名 -> 仓库内源图文件名。

    体检"源图副本"必须用它 —— 目录里的副本**不一定叫 `<角色名>_source`**：
    `xbya` 的源图实际叫 `avatar_new.webp`。第一版按名字猜，
    于是把有源图的 xbya 误报成"无法重新生成"。
    """
    src = (ROOT / "tools" / "make_pet_sprites.py").read_text(encoding="utf-8")
    i = src.index("LOCAL_SRC = {")
    j = src.index("}", i)
    out = {}
    import re
    for m in re.finditer(r'"(\w+)"\s*:\s*ROOT\s*/\s*"resources"\s*/\s*"source"\s*/\s*"([^"]+)"',
                         src[i:j]):
        out[m.group(1)] = m.group(2)
    return out


def _load_profiles() -> dict:
    """从生成器源文本里解析 PROFILES 的键（不执行它）。"""
    src = (ROOT / "tools" / "make_pet_sprites.py").read_text(encoding="utf-8")
    i = src.index("PROFILES: dict[str, dict] = {")
    # 取到下一行的顶层 }
    j = src.index("\n}", i)
    body = src[i:j]
    names = []
    depth = 0
    for line in body.splitlines():
        s = line.strip()
        if s.startswith('"') and s.endswith(": {") and depth == 1:
            names.append(s.split('"')[1])
        depth += line.count("{") - line.count("}")
    return names


def cmd_list(_args) -> int:
    profiles = _load_profiles()
    print()
    print("  角色目录                 profile 存在?   帧数合计")
    print("  " + "-" * 56)
    for d in sorted(p for p in SPRITES.iterdir() if p.is_dir()):
        total = sum(len(list(sub.glob("frame_*.png")))
                    for sub in d.iterdir() if sub.is_dir())
        has = "✓" if d.name in profiles else "✗ 无 profile（无法重新生成）"
        print(f"  {d.name:<24} {has:<14} {total:>6}")
    print()
    print(f"  生成器里可用的 profile: {', '.join(profiles) or '(无)'}")
    extra = [d.name for d in SPRITES.iterdir()
             if d.is_dir() and d.name not in profiles]
    if extra:
        print()
        print(f"  ⚠️  {', '.join(extra)} 有帧目录但没有 profile ——")
        print("      它们**不能再生成**，只能沿用现有帧。")
    return 0


def cmd_init(args) -> int:
    name, src = args.name, Path(args.image)
    if not src.is_file():
        print(f"  ✗ 找不到立绘: {src}")
        return 1
    SOURCE.mkdir(parents=True, exist_ok=True)
    ext = src.suffix.lower()
    dst = SOURCE / f"{name}_source{ext}"
    if dst.exists() and not args.force:
        print(f"  ! 已存在 {dst.relative_to(ROOT)}（加 --force 覆盖）")
    else:
        dst.write_bytes(src.read_bytes())
        print(f"  ✓ 仓库内副本: {dst.relative_to(ROOT)}  "
              f"({dst.stat().st_size} 字节)")

    # 图片信息
    try:
        from PIL import Image
        import numpy as np
        im = Image.open(dst)
        print(f"    尺寸 {im.size[0]}×{im.size[1]}  模式 {im.mode}")
        if im.mode in ("RGBA", "LA"):
            a = np.asarray(im.convert("RGBA"))[:, :, 3]
            uniq = len(np.unique(a))
            trans = float((a == 0).mean()) * 100
            opaque = float((a == 255).mean()) * 100
            semi = 100 - trans - opaque
            print(f"    alpha: {uniq} 个取值 / 全透明 {trans:.1f}% / "
                  f"不透明 {opaque:.1f}% / 半透明 {semi:.2f}%")
            if semi > 0.1:
                print("    → **有真实半透明边** ⇒ profile 用 keyer=\"alpha\"，"
                      "绝不要重抠")
            else:
                print("    → 几乎没有半透明边，可能是二值化的 alpha")
        else:
            print(f"    ⚠️ 没有 alpha 通道（模式 {im.mode}）"
                  f" ⇒ profile 需要 keyer 抠图（rembg）")
    except Exception as e:
        print(f"    (读图失败: {e})")

    print()
    print("  ── 把下面这段粘进 tools/make_pet_sprites.py 的 PROFILES ──")
    print(f'    "{name}": {{')
    print(f'        "src": r"{src}",')
    print(f'        "mode": "full",          # full=按宽度适配整张 | crop=先裁')
    print(f'        "canvas_w": 128,')
    print(f'        "bottom_margin": 6,      # 全身留白，防脚被裁')
    print(f'        "lm_space": "src",       # 坐标写在原图坐标系')
    print(f'        "keyer": "alpha",        # 有 alpha 就用它，不重抠')
    print(f'        "alpha_floor": 6.0,      # 缩放后低于此 alpha 清零')
    print(f'        "lm_src": {{')
    print(f'            "eye_l": (0, 0, 0, 0),   # 左眼框，见 ruler 复核')
    print(f'            "eye_r": (0, 0, 0, 0),')
    print(f'            "mouth": (0, 0, 0, 0),')
    print(f'            "neck_y": 0,')
    print(f'        }},')
    print(f'        "skin": (238, 205, 196),')
    print(f'        "line": (40, 30, 34),')
    print(f'        "amp": 1.0,')
    print(f'    }},')
    print()
    print("  并在 LOCAL_SRC 里注册仓库内副本：")
    print(f'    "{name}": ROOT / "resources" / "source" / "{dst.name}",')
    print()
    print("  下一步：")
    print(f"    python tools/avatar_helper.py ruler {name}")
    return 0


def cmd_ruler(args) -> int:
    """生成带标尺的放大图，供人读坐标。"""
    name = args.name
    # 先按 LOCAL_SRC 的真实映射找（`xbya` 的源图叫 avatar_new.webp，
    # 用 `<name>_source*` 去猜会找不到）
    src = None
    lsm = _local_src_map()
    if lsm.get(name):
        p = SOURCE / lsm[name]
        if p.is_file():
            src = p
    if src is None:
        cands = [c for c in SOURCE.glob(f"{name}_source*") if c.is_file()]
        if cands:
            src = cands[0]
    if src is None:
        print(f"  ✗ 找不到 {name} 的源图")
        print(f"    LOCAL_SRC 里注册的是: {lsm.get(name, '(未注册)')}")
        print(f"    仓库内 .webp/.png: "
              f"{[p.name for p in SOURCE.iterdir() if p.suffix.lower() in ('.webp','.png')][:6]}")
        print(f"    也可以先跑: python tools/avatar_helper.py init {name} <立绘路径>")
        return 1
    print(f"  源图: {src.relative_to(ROOT)}")

    from PIL import Image, ImageDraw
    scale = args.scale
    im = Image.open(src).convert("RGBA")
    w, h = im.size
    big = im.resize((w * scale, h * scale), Image.LANCZOS)
    d = ImageDraw.Draw(big)

    step = args.step
    for x in range(0, w + 1, step):
        X = x * scale
        major = (x % (step * 5) == 0)
        d.line([(X, 0), (X, h * scale)],
               fill=(255, 0, 0, 200) if major else (255, 0, 0, 70), width=1)
        if major:
            d.text((X + 2, 2), str(x), fill=(255, 0, 0, 255))
    for y in range(0, h + 1, step):
        Y = y * scale
        major = (y % (step * 5) == 0)
        d.line([(0, Y), (w * scale, Y)],
               fill=(0, 128, 255, 200) if major else (0, 128, 255, 70), width=1)
        if major:
            d.text((2, Y + 2), str(y), fill=(0, 128, 255, 255))

    out = SOURCE / f"_{name}_ruler.png"
    big.save(out)
    print(f"  ✓ 复核图: {out.relative_to(ROOT)}")
    print(f"    原图 {w}×{h}，放大 {scale}x，网格每 {step}px"
          f"（粗线每 {step*5}px，标的是**原图坐标**）")
    print()
    print("  读坐标要点（实测踩过的坑）：")
    print("    · eye 框要包住**可见眼睛**：虹膜 + 眼白 + 上下睫毛 + 外眼角")
    print("    · eye 框**不要带眉毛** —— 带上会让闭眼补丁盖到眉毛，形成横带")
    print("    · mouth 框只包嘴唇，不含人中与下巴")
    print("    · neck_y 是头/身交界，头部动画绕它旋转")
    print()
    print(f"  标完用这个复核：python tools/avatar_helper.py preview {name} "
          f"<动画> <帧号>")
    return 0


def cmd_preview(args) -> int:
    """放大某一帧并叠上五官框，复核标注。"""
    name = args.name
    d = SPRITES / name / args.anim
    frames = sorted(d.glob("frame_*.png"))
    if not frames:
        print(f"  ✗ 没有帧: {d}")
        return 1
    idx = max(0, min(args.frame - 1, len(frames) - 1))
    f = frames[idx]
    print(f"  帧: {f.relative_to(ROOT)}")

    from PIL import Image, ImageDraw
    im = Image.open(f).convert("RGBA")
    scale = args.scale
    w, h = im.size
    big = im.resize((w * scale, h * scale), Image.NEAREST)
    d2 = ImageDraw.Draw(big)

    # 叠上 _lm（画布坐标）里的框 —— 若能从缓存取到
    lm = None
    try:
        from tools.make_pet_sprites import PROFILES, prepare
        if name in PROFILES:
            _p, _base = prepare(name, False)
            lm = _p.get("_lm")
    except Exception as e:
        print(f"  (取不到五官框: {e})")

    if lm:
        colors = {"eye_l": (255, 0, 0), "eye_r": (255, 128, 0),
                  "mouth": (0, 200, 0)}
        for tag, col in colors.items():
            if tag not in lm:
                continue
            x0, y0, x1, y1 = lm[tag]
            d2.rectangle([x0 * scale, y0 * scale,
                          (x1 + 1) * scale - 1, (y1 + 1) * scale - 1],
                         outline=col, width=2)
            d2.text((x0 * scale + 2, y0 * scale + 2), tag, fill=col)
            print(f"    {tag:<7} 画布 ({x0}, {y0}, {x1}, {y1})  "
                  f"{x1-x0+1}×{y1-y0+1}")
        if "neck_y" in lm:
            ny = lm["neck_y"]
            d2.line([(0, ny * scale), (w * scale, ny * scale)],
                    fill=(0, 128, 255), width=2)
            print(f"    neck_y  画布 {ny}")

    out = SOURCE / f"_{name}_check.png"
    big.save(out)
    print(f"  ✓ 复核图: {out.relative_to(ROOT)}（{w*scale}×{h*scale}）")
    return 0


def cmd_check(args) -> int:
    """体检：manifest 一致性 / 帧数 / 尺寸 / 源图。"""
    name = args.name
    d = SPRITES / name
    ok = True
    print()
    print(f"  体检: {name}")
    print("  " + "=" * 60)

    if not d.is_dir():
        print(f"  ✗ 没有目录 {d.relative_to(ROOT)}")
        return 1

    dirs = sorted(x.name for x in d.iterdir() if x.is_dir())
    mf = d / "manifest.json"
    if not mf.is_file():
        print("  ✗ 没有 manifest.json —— 加载器会退回默认尺寸，动画全部缺失")
        return 1
    man = json.loads(mf.read_text(encoding="utf-8"))
    anims = man.get("animations", {})

    # 1. 一致性
    only_dir = [x for x in dirs if x not in anims]
    only_man = [x for x in anims if x not in dirs]
    print(f"  帧目录 {len(dirs)} 个 / manifest {len(anims)} 个")
    if only_dir:
        ok = False
        print(f"  ✗ 有帧但**不在 manifest**（永远不会被加载，帧白生成）: {only_dir}")
    if only_man:
        ok = False
        print(f"  ✗ manifest 里有但**没有目录**（加载时报错/跳过）: {only_man}")
    if not only_dir and not only_man:
        print("  ✓ manifest 与帧目录一致")

    # 2. 帧数是否达标
    expect = _anims_from_generator()
    for a in sorted(anims):
        n = len(list((d / a).glob("frame_*.png"))) if (d / a).is_dir() else 0
        want = expect.get(a)
        if want is None:
            tag = "(ANIMS 之外，自定义)"
        elif n == want:
            tag = "✓"
        else:
            tag = f"✗ 期望 {want}"
            ok = False
        print(f"    {a:<8} {n:>3} 帧  {tag}")

    # 3. 尺寸
    size = man.get("size")
    frames = sorted((d / dirs[0]).glob("frame_*.png")) if dirs else []
    if frames:
        from PIL import Image
        real = Image.open(frames[0]).size
        if size and tuple(size) == real:
            print(f"  ✓ manifest.size {size} 与实际帧一致")
        else:
            ok = False
            print(f"  ✗ manifest.size {size} 与实际帧 {list(real)} **不一致**")

    # 4. 源图副本（用 LOCAL_SRC 的真实映射，不按名字猜）
    lsm = _local_src_map()
    registered = lsm.get(name)
    if registered:
        p = SOURCE / registered
        if p.is_file():
            print(f"  ✓ 仓库内源图: {p.name}（LOCAL_SRC 已注册）")
        else:
            ok = False
            print(f"  ✗ LOCAL_SRC 指向 {registered}，但文件**不存在** ——"
                  f" 该角色无法重新生成")
    else:
        # 没注册时，看看有没有同名的副本
        guess = list(SOURCE.glob(f"{name}_source*"))
        if guess:
            print(f"  ! 有 {guess[0].name}，但**没写进 LOCAL_SRC**"
                  f" —— 生成器不会用它（会退回 profile 里的 src 绝对路径）")
        else:
            print(f"  ! 仓库内没有该角色的源图副本 —— 无法重新生成"
                  f"（只能沿用现有帧）")

    # 5. profile
    if name not in _load_profiles():
        print(f"  ! 生成器里没有 {name} 的 profile —— 不能重新生成")
    else:
        print("  ✓ 生成器里有对应 profile")

    print("  " + "=" * 60)
    print(f"  结论: {'✓ 通过' if ok else '✗ 有问题（见上）'}")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="换形象辅助工具")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("list", help="列出角色与 profile")
    p.set_defaults(fn=cmd_list)

    p = sub.add_parser("init", help="留仓库内副本 + 打印 profile 模板")
    p.add_argument("name")
    p.add_argument("image")
    p.add_argument("--force", action="store_true")
    p.set_defaults(fn=cmd_init)

    p = sub.add_parser("ruler", help="生成带标尺的放大复核图")
    p.add_argument("name")
    p.add_argument("--scale", type=int, default=3)
    p.add_argument("--step", type=int, default=25)
    p.set_defaults(fn=cmd_ruler)

    p = sub.add_parser("preview", help="放大某帧并叠五官框")
    p.add_argument("name")
    p.add_argument("anim", nargs="?", default="idle")
    p.add_argument("frame", nargs="?", type=int, default=1)
    p.add_argument("--scale", type=int, default=3)
    p.set_defaults(fn=cmd_preview)

    p = sub.add_parser("check", help="体检")
    p.add_argument("name")
    p.set_defaults(fn=cmd_check)

    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
