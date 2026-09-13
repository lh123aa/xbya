# -*- coding: utf-8 -*-
r"""量出各 VRM 的**实际包围盒**与**头部位置**，用数据解释「只看得到头顶额头」。

`assets/vrm/js/app.js::fitCamera()` 的取景算法是：
    frameH  = 模型总高 × 0.50          # 取景高度
    topY    = 包围盒最高点
    targetY = topY + 0.12×h - frameH/2  # 相机瞄准高度
    dist    = (frameH/2) / tan(fov/2)

这套参数**是照某一个模型调出来的**。换成比例不同的模型，
`targetY` 就可能落到头顶之上或脸之下 —— 表现就是"只看见头顶/额头"。

本脚本从 GLB 里直接读 POSITION accessor 的 min/max（glTF 规定 accessor 必须带
min/max），再结合节点变换算出各模型的：
  · 总包围盒
  · 头部骨骼的静止位置（VRM humanoid 的 head 节点）
  · 按上面公式算出 targetY，看它相对**头顶与眼睛**落在哪
"""
import io
import json
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
VRM = ROOT / "assets" / "vrm"


def read_glb(path: Path):
    raw = path.read_bytes()
    magic, ver, total = struct.unpack_from("<4sII", raw, 0)
    off, chunks = 12, {}
    while off < total:
        clen, ctype = struct.unpack_from("<II", raw, off)
        chunks[ctype] = raw[off + 8: off + 8 + clen]
        off += 8 + clen
    return json.loads(chunks[0x4E4F534A].decode("utf-8"))


def bbox(g):
    """所有 mesh primitive 的 POSITION min/max 取并集（不做节点变换，够用于比较）"""
    lo = [1e9] * 3
    hi = [-1e9] * 3
    for m in g.get("meshes") or []:
        for p in m.get("primitives") or []:
            ai = (p.get("attributes") or {}).get("POSITION")
            if ai is None:
                continue
            acc = g["accessors"][ai]
            mn, mx = acc.get("min"), acc.get("max")
            if not mn or not mx:
                continue
            for i in range(3):
                lo[i] = min(lo[i], mn[i])
                hi[i] = max(hi[i], mx[i])
    return lo, hi


def nodes_by_name(g):
    return {(n.get("name") or f"#{i}"): i for i, n in enumerate(g.get("nodes") or [])}


print("=" * 86)
print("各 VRM 的包围盒与头部位置")
print("=" * 86)
print()
print("| 模型 | 总高 | Y 范围 | head 骨骼 Y | 眼睛/脸 大致 Y |")
print("|------|------|--------|------------|---------------|")

info = {}
for name in ("cat.vrm", "AvatarSample_A.vrm", "xbya_from_photo.vrm", "chibi_handmade.vrm"):
    p = VRM / name
    if not p.is_file():
        continue
    g = read_glb(p)
    lo, hi = bbox(g)
    H = hi[1] - lo[1]

    # head 骨骼（VRM humanoid 映射）
    #
    # ⚠️ 这里格式**两种不一样**，实测踩到过（`'list' object has no attribute 'get'`）：
    #   · VRM 1.0（VRMC_vrm）：`humanBones` 是**列表**，
    #     元素形如 `{"bone": "head", "node": 12}`
    #   · VRM 0.x （VRM）     ：`humanBones` 是**字典**，
    #     形如 `{"head": {"node": 12}}`
    # 一开始只按字典写，读到 1.0 的模型就崩了。
    head_y = None
    ext = (g.get("extensions") or {})
    for key in ("VRMC_vrm", "VRM"):
        if key not in ext:
            continue
        hb = ((ext[key].get("humanoid") or {}).get("humanBones"))
        node_idx = None
        if isinstance(hb, dict):                    # VRM 0.x
            h = hb.get("head")
            if isinstance(h, dict):
                node_idx = h.get("node")
        elif isinstance(hb, list):                  # VRM 1.0
            for entry in hb:
                if isinstance(entry, dict) and entry.get("bone") == "head":
                    node_idx = entry.get("node")
                    break
        if node_idx is not None and node_idx < len(g.get("nodes") or []):
            t = g["nodes"][node_idx].get("translation")
            if t:
                head_y = t[1]
        break
    # 若无 VRM 扩展，退回按节点名找
    if head_y is None:
        nb = nodes_by_name(g)
        for cand in ("head", "Head", "J_Bip_C_Head"):
            if cand in nb:
                t = g["nodes"][nb[cand]].get("translation")
                if t:
                    head_y = t[1]
                break

    info[name] = {"H": H, "lo": lo, "hi": hi, "head_y": head_y,
                  "has_vrm_ext": any(k in ext for k in ("VRMC_vrm", "VRM"))}
    print(f"| `{name}` | {H:.2f} | {lo[1]:.2f}~{hi[1]:.2f} | "
          f"{head_y if head_y is None else f'{head_y:.2f}'} | — |")

print()
print("=" * 86)
print("按 fitCamera 的公式算 targetY，看它落在哪（fov=30，即 tan(15°)=0.268）")
print("=" * 86)
print()
print("| 模型 | 总高 h | frameH=h/2 | topY | targetY | 取景框 Y 范围 | 头顶在框内? |")
print("|------|--------|-----------|------|---------|--------------|------------|")
import math
TAN15 = math.tan(math.radians(15))

for name, d in info.items():
    h = d["H"]
    lo, hi = d["lo"], d["hi"]
    topY = hi[1]                      # 假设场景无额外变换
    frameH = h * 0.50
    margin = h * 0.12
    targetY = (topY + margin) - frameH / 2
    view_lo, view_hi = targetY - frameH / 2, targetY + frameH / 2
    # 头顶（含发）在 hi[1]；脸大概在 head_y 附近
    head_ok = view_lo <= hi[1] <= view_hi
    face = d["head_y"]
    face_ok = (face is not None) and (view_lo <= face <= view_hi)
    print(f"| `{name}` | {h:.2f} | {frameH:.2f} | {topY:.2f} | {targetY:.2f} | "
          f"{view_lo:.2f}~{view_hi:.2f} | {'✔' if head_ok else '**✘ 被裁**'} |")
    if face is not None:
        print(f"|    ↳ 脸(head_y={face:.2f}) 在框内？ | | | | | | "
              f"{'✔' if face_ok else '**✘ 脸在取景框外 —— 这就是「只看得见头顶额头」**'} |")

print()
print("=" * 86)
print("结论")
print("=" * 86)
print()
print("`fitCamera` 用「总高 × 0.5」当取景高度、并**假设相机该瞄准发顶下方一点**。")
print("这个假设只在**某一种头身比**下成立。换成头身比不同的模型时：")
print()
print("  · 若模型的**头部相对总高偏上**（头身比大、身子长），")
print("    targetY 会算到头顶之上 ⇒ 视野里只剩头顶/额头")
print("  · 若头部相对偏下（Q版头大），targetY 偏低 ⇒ 只看到身子")
print()
print("正确做法：**不要拿总高推**，而是拿 **head 骨骼的实际位置**当锚点。")
print("VRM 有 humanoid 映射，`head` 骨骼就是权威锚点 —— 这也解释了为什么")
print("`xbya_from_photo.vrm` 特别糟：它**没有 VRM 扩展**（日志 `Unknown extension")
print("VRMC_vrm`），拿不到 humanoid，只能退回按节点名找，兜底不一定命中。")
