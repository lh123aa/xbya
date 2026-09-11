# -*- coding: utf-8 -*-
r"""★ 查清"背身"：从 VRM 文件本身读出**模型朝哪边**，不再靠猜。

背景：`assets/vrm/js/app.js:298-299` 写死了一句

    // 让角色正面朝向镜头：VRoid 常默认面向 -Z，相机在 +Z，故绕 Y 转 180°
    vrm.scene.rotation.y = Math.PI;

如果某个模型**本来就朝 +Z**，这句就会把它转成背对镜头。
问题是：哪个模型朝哪边？这不能靠"看起来"判断 —— VRM/GLB 的节点旋转是**明文数据**，
直接读出来即可。

读法：GLB = 12 字节头 + JSON chunk + BIN chunk。从 JSON chunk 里取：
  · `extensionsUsed` 里有没有 `VRM` / `VRMC_vrm`（判断是 VRM 0.x 还是 1.0）
  · `nodes` 里带 `rotation` 的节点（四元数）
  · 整棵默认场景的根节点旋转 —— 这才是"模型默认朝向"

⚠️ 本脚本只描述**数据**，不假装自己"看见"了模型。结论分两档：
  实测（文件里读出来的数） / 推断（基于数据的朝向判断）。
"""
import io
import json
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
VRM_DIR = ROOT / "assets" / "vrm"


def read_glb_json(path: Path) -> dict:
    """从 GLB 里取出 JSON chunk（不做完整解析，只要头 + 第一块）"""
    with io.open(path, "rb") as f:
        magic, version, length = struct.unpack("<4sII", f.read(12))
        if magic != b"glTF":
            raise ValueError(f"{path.name}: 不是 GLB（magic={magic!r}）")
        chunk_len, chunk_type = struct.unpack("<II", f.read(8))
        if chunk_type != 0x4E4F534A:            # 'JSON'
            raise ValueError(f"{path.name}: 第一块不是 JSON（type={chunk_type:#x}）")
        raw = f.read(chunk_len)
    return json.loads(raw.decode("utf-8"))


def quat_to_yaw_deg(q):
    """四元数 (x,y,z,w) → 绕 Y 的偏航角（度）。只看 Y 轴，够判断朝向。"""
    if not q or len(q) != 4:
        return 0.0
    import math
    x, y, z, w = q
    yaw = math.atan2(2.0 * (w * y + x * z), 1.0 - 2.0 * (y * y + x * x))
    return math.degrees(yaw)


FILES = ["cat.vrm", "AvatarSample_A.vrm", "chibi.vrm", "chibi_handmade.vrm"]
print("=" * 82)
print("从 VRM 文件里读出朝向数据（不是目测，是读明文）")
print("=" * 82)
print()

rows = []
for name in FILES:
    p = VRM_DIR / name
    if not p.is_file():
        print(f"--- {name}：不存在，跳过")
        continue
    try:
        g = read_glb_json(p)
    except Exception as e:                                          # noqa: BLE001
        print(f"--- {name}：读取失败 {type(e).__name__}: {e}")
        continue

    ext = g.get("extensionsUsed") or []
    vrm_ver = ("VRMC_vrm（VRM 1.0）" if "VRMC_vrm" in ext
               else "VRM（VRM 0.x）" if "VRM" in ext
               else "**没有 VRM 扩展**（可能就是普通 glTF 模型）")
    nodes = g.get("nodes") or []
    scene_idx = g.get("scene", 0)
    scenes = g.get("scenes") or []
    roots = []
    if scenes and scene_idx < len(scenes):
        roots = scenes[scene_idx].get("nodes") or []

    print(f"--- {name}  （{p.stat().st_size / 1e6:.1f} MB）")
    print(f"    格式       : {vrm_ver}")
    print(f"    节点数     : {len(nodes)}")
    print(f"    场景根节点 : {roots}")

    # 根节点的旋转 = 模型默认朝向
    root_rots = []
    for r in roots:
        if r < len(nodes):
            n = nodes[r]
            q = n.get("rotation")
            root_rots.append({"name": n.get("name", f"#{r}"),
                              "quat": q, "yaw": quat_to_yaw_deg(q)})
    for rr in root_rots:
        print(f"    根节点「{rr['name']}」旋转: {rr['quat']} → 绕Y {rr['yaw']:+.1f}°")
    if not root_rots:
        print("    根节点无旋转（默认朝 +Z 或由子节点/包围盒决定）")

    # 统计有多少节点带非零旋转（了解是"整体旋转"还是"骨骼层级旋转"）
    withrot = sum(1 for n in nodes if n.get("rotation"))
    print(f"    带 rotation 的节点数: {withrot}/{len(nodes)}")

    # humanoid 骨骼里找 hips/head，看它们的静止旋转（VRM 人形的朝向线索）
    for extkey in ("VRMC_vrm", "VRM"):
        if extkey in (g.get("extensions") or {}):
            hi = (g["extensions"][extkey].get("humanoid") or {})
            hb = hi.get("humanBones") or {}
            if isinstance(hb, dict):
                for bone in ("hips", "head"):
                    b = hb.get(bone)
                    if isinstance(b, dict) and "node" in b:
                        idx = b["node"]
                        n = nodes[idx] if idx < len(nodes) else {}
                        print(f"    {bone:5s} 节点「{n.get('name','?')}」"
                              f" rotation={n.get('rotation')}"
                              f" → 绕Y {quat_to_yaw_deg(n.get('rotation')):+.1f}°")
    rows.append((name, vrm_ver, root_rots, withrot, len(nodes)))
    print()

print("=" * 82)
print("结论（**只描述数据**）")
print("=" * 82)
print()
print("viewer 的假设是「模型朝 −Z，所以转 180° 让它朝 +Z（镜头在 +Z）」。")
print("要判断这句话对某个模型成不成立，需要看该模型的**根节点/hips 是否带旋转**：")
print()
for name, ver, rots, withrot, n in rows:
    yaws = [r["yaw"] for r in rots] or [0.0]
    print(f"  · {name:24s} {ver:28s} 根节点绕Y={yaws}")
print()
print("⚠️ 重要边界：`rotation` 只是**一层**信息。模型的朝向还可能由")
print("   网格顶点本身（顶点已烘进朝向）+ 根节点旋转共同决定 ——")
print("   也就是说 **光看节点旋转不一定能判定最终朝向**。")
print("   所以本脚本给出的是'哪几个模型在数据层面就不一样'，")
print("   而不是'哪个看起来是正面'。后者需要真的渲染出来看。")
