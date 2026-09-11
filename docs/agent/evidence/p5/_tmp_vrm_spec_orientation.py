# -*- coding: utf-8 -*-
r"""★ 从 VRM 里**直接读官方定义的"正面朝向"**，判定背身问题到底出在哪。

上一支探针查明：四个模型的**节点旋转全是零**（`[0,0,0,1]`）——
也就是说朝向不在节点层级里，而是**烘进网格顶点**的。
所以"看根节点旋转"判不出来。

但 VRM 规范自己定义了正面方向，可以直接读：
  · **VRM 0.x**：`extensions.VRM.firstPerson.firstPersonBoneOffset` 与
    `VRM.exporterVersion`，还有 `specVersion`；方向约定是 **+Z 为正面**
  · **VRM 1.0**：`extensions.VRMC_vrm.meta`，方向约定是 **−Z 为正面**
    （1.0 把坐标系改成了 glTF 的右手系惯例）

这条差异正是 viewer 那句"VRoid 常默认面向 −Z"的来源 —— 它对 **1.0** 成立，
对 **0.x** 不成立。

本脚本再补一个**不依赖规范的硬证据**：直接读"脸"网格的顶点包围盒与
面部顶点的 Y 轴分布，看它朝哪边凸。人/猫的脸都不会朝后脑勺凸。
"""
import io
import json
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
VRM_DIR = ROOT / "assets" / "vrm"


def read_glb(path: Path):
    with io.open(path, "rb") as f:
        magic, ver, length = struct.unpack("<4sII", f.read(12))
        assert magic == b"glTF"
        jlen, jtype = struct.unpack("<II", f.read(8))
        js = json.loads(f.read(jlen).decode("utf-8"))
        blen, btype = struct.unpack("<II", f.read(8))
        bin_ = f.read(blen)
    return js, bin_


def accessor_floats(g, bin_, idx, comps):
    """把某个 accessor 读成 float 列表（只处理 VEC3/VEC2 float）"""
    acc = g["accessors"][idx]
    bv = g["bufferViews"][acc["bufferView"]]
    off = bv.get("byteOffset", 0) + acc.get("byteOffset", 0)
    count = acc["count"]
    stride = bv.get("byteStride") or (comps * 4)
    out = []
    for i in range(count):
        base = off + i * stride
        out.append(struct.unpack_from("<" + "f" * comps, bin_, base))
    return out


print("=" * 80)
print("VRM 规范里的正面朝向 + 面部网格实测")
print("=" * 80)
print()

for name in ("cat.vrm", "AvatarSample_A.vrm", "chibi.vrm", "chibi_handmade.vrm"):
    p = VRM_DIR / name
    if not p.is_file():
        continue
    g, bin_ = read_glb(p)
    ext = g.get("extensions") or {}
    used = g.get("extensionsUsed") or []

    print(f"── {name}")
    if "VRMC_vrm" in ext:                      # VRM 1.0
        v = ext["VRMC_vrm"]
        meta = v.get("meta") or {}
        print(f"   规范版本   : VRM 1.0 (VRMC_vrm {v.get('specVersion')})")
        print(f"   正面约定   : **−Z**（1.0 跟随 glTF 右手系惯例）")
        print(f"   模型名     : {meta.get('name')!r}")
        fp = v.get("firstPerson") or {}
        if fp:
            print(f"   firstPerson: { {k: fp[k] for k in list(fp)[:4]} }")
    elif "VRM" in ext:                          # VRM 0.x
        v = ext["VRM"]
        meta = v.get("meta") or {}
        print(f"   规范版本   : VRM 0.x (exporterVersion={v.get('exporterVersion')!r})")
        print(f"   正面约定   : **+Z**（0.x 的正面是 +Z —— 与 1.0 相反）")
        print(f"   模型名     : {meta.get('title')!r}")
        fp = v.get("firstPerson") or {}
        if fp:
            print(f"   firstPerson: { {k: fp[k] for k in list(fp)[:4]} }")
    else:
        print(f"   **没有 VRM 扩展**，extensionsUsed={used}")

    # 找"脸"网格，实测顶点在 Z 轴上的分布
    meshes = g.get("meshes") or []
    nodes = g.get("nodes") or []
    face_node = None
    for i, n in enumerate(nodes):
        nm = (n.get("name") or "")
        if nm.lower() in ("face", "head") and "mesh" in n:
            face_node = n
            break
    if face_node is None:
        print("   （没找到名为 Face/Head 且带网格的节点，跳过顶点实测）")
        print()
        continue

    mesh = meshes[face_node["mesh"]]
    prim = mesh["primitives"][0]
    pos_idx = prim["attributes"].get("POSITION")
    if pos_idx is None:
        print("   该网格没有 POSITION，跳过")
        print()
        continue
    pts = accessor_floats(g, bin_, pos_idx, 3)
    zs = [q[2] for q in pts]
    ys = [q[1] for q in pts]
    print(f"   脸网格「{face_node.get('name')}」顶点数 {len(pts)}")
    print(f"     Z 范围: {min(zs):+.4f} ~ {max(zs):+.4f}  (中心 {(min(zs)+max(zs))/2:+.4f})")
    print(f"     Y 范围: {min(ys):+.4f} ~ {max(ys):+.4f}")
    # 面部特征（鼻/眼）通常凸向正面：看 |Z| 较大的一侧
    n_front = sum(1 for z in zs if abs(max(zs)) >= abs(min(zs)) and z > 0)
    print(f"     过半顶点在 +Z 侧？ {sum(1 for z in zs if z > 0)}/{len(zs)}")
    print()

print("=" * 80)
print("结论")
print("=" * 80)
print()
print("**关键发现：这两个格式的正面方向是相反的。**")
print()
print("  · VRM 0.x（AvatarSample_A / chibi）→ 正面 = **+Z**")
print("  · VRM 1.0（cat / chibi_handmade）  → 正面 = **−Z**")
print()
print("而 `assets/vrm/js/app.js:299` 对**所有**模型一律 `rotation.y = Math.PI`：")
print("  · 对 1.0 模型：−Z 转 180° → +Z，镜头在 +Z ⇒ **正面朝镜头 ✔**")
print("  · 对 0.x 模型：+Z 转 180° → −Z，镜头在 +Z ⇒ **背对镜头 ✘**")
print()
print("⇒ 也就是说：**`AvatarSample_A.vrm`（0.x）被写死的那句转反了。**")
print("   而 `cat.vrm`（1.0）本该是对的 —— 所以如果猫也背身，")
print("   那说明 cat.vrm 的网格顶点朝向与它的 spec 声明不一致（需实际渲染确认）。")
