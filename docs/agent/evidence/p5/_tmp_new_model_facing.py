# -*- coding: utf-8 -*-
r"""确认 `xbya_from_photo.vrm` 的**正面朝哪边** —— 决定 `ui.vrm_yaw_deg` 该填多少。

判据（两条，互相印证）：
  1. **眼骨位置**：VRM 里 `leftEye`/`rightEye` 的 translation.z。
     眼睛长在脸的前面，所以 z 的符号就是"正面方向"。
  2. **脸网格顶点的 Z 分布**：脸（鼻、唇）凸向正面。

为什么要这么认真：`ui.vrm_yaw_deg` 填错就是背身。
前面已经因为"照规范推断"错过一次（VRM 0.x 与 1.0 正面相反），
所以这次不推规范，**直接读这个模型自己的数据**。
"""
import json
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
raw = (ROOT / "assets" / "vrm" / "xbya_from_photo.vrm").read_bytes()
clen, ctype = struct.unpack_from("<II", raw, 12)
g = json.loads(raw[20:20 + clen].decode("utf-8"))
nodes = g["nodes"]
ext = g["extensions"]["VRMC_vrm"]
hb = {i["bone"]: i["node"] for i in ext["humanoid"]["humanBones"]}

print("=" * 74)
print("判据一：眼骨与头骨的 translation（眼睛在脸前面）")
print("=" * 74)
print()
eye_z = []
for b in ("head", "neck", "leftEye", "rightEye"):
    if b in hb:
        t = nodes[hb[b]].get("translation")
        print(f"  {b:10s} translation = {t}")
        if b.endswith("Eye") and t:
            eye_z.append(t[2])

print()
if eye_z:
    avg = sum(eye_z) / len(eye_z)
    print(f"  两只眼睛的平均 z = {avg:+.3f}")
    print(f"  ⇒ 眼睛在 **{'+Z' if avg > 0 else '−Z'}** 一侧 ⇒ "
          f"**正面朝 {'+Z' if avg > 0 else '−Z'}**")
    front = "+Z" if avg > 0 else "-Z"
else:
    front = "?"
    print("  没找到眼骨，无法用判据一")

print()
print("=" * 74)
print("判据二：脸网格顶点的 Z 分布")
print("=" * 74)
print()
got = False
for n in nodes:
    nm = (n.get("name") or "").lower()
    if nm in ("face", "head") and "mesh" in n:
        mesh = g["meshes"][n["mesh"]]
        for p in mesh["primitives"]:
            ai = p["attributes"].get("POSITION")
            if ai is None:
                continue
            acc = g["accessors"][ai]
            mn, mx = acc.get("min"), acc.get("max")
            print(f"  「{n.get('name')}」POSITION min={mn} max={mx}")
            zc = (mn[2] + mx[2]) / 2
            print(f"     Z 中心 = {zc:+.4f} ⇒ 该网格主体在 "
                  f"**{'+Z' if zc > 0 else '−Z'}** 侧")
            got = True
            break
    if got:
        break

print()
print("=" * 74)
print("结论与建议值")
print("=" * 74)
print()
print("viewer 的相机在 **+Z** 侧朝原点看。要让模型的**正面**朝向相机，")
print("模型的正面对必须指向 +Z。")
print()
if front == "+Z":
    print("  本模型正面已朝 **+Z** ⇒ 相机能直接看到脸 ⇒ **yaw 应为 0**")
    print("  （若填 180 就会转成背身）")
    suggested = 0
elif front == "-Z":
    print("  本模型正面朝 **−Z** ⇒ 需要绕 Y 转 180° 才朝向相机 ⇒ **yaw 应为 180**")
    suggested = 180
else:
    print("  判不出正面方向，需人工确认。")
    suggested = None

print()
print(f"  `ui.vrm_yaw_deg: {suggested}`" if suggested is not None else "")
print()
print("⚠️ 边界：这两条判据读的都是**静止姿态**的数据。")
print("   若模型的网格顶点被烘进了别的旋转（glTF 允许节点带 rotation），")
print("   最终朝向还要叠加那层 —— 本脚本没有逐层做坐标变换。")
print("   所以最后的确认仍然要看实际渲染结果。")
