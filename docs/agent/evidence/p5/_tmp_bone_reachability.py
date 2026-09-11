# -*- coding: utf-8 -*-
r"""查清"骨骼为什么找不到"：模型里到底有没有可用的人形骨骼节点。

现象：我把 `humanBones` 从字典改成数组（合 1.0 规范）之后，
`three-vrm` 反而报「15 根必需骨骼全都不存在」并**整个模型加载失败**。
改之前是"静默忽略、动画不生效"，改之后是"直接加载失败" ——
说明骨骼本身就有问题，之前只是被静默吞掉了。

要查的：
  1. `skins` 有没有？`joints` 里是不是那些骨骼节点？
  2. 骨骼节点是否**真的在节点树里可达**（three-vrm 是用 `getNode`/名字去找的）
  3. 网格有没有 `skin` 绑定（没有就是"死"网格，不跟骨骼动）
"""
import json
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]


def load(path):
    raw = Path(path).read_bytes()
    clen, _ = struct.unpack_from("<II", raw, 12)
    return json.loads(raw[20:20 + clen].decode("utf-8"))


for name in ("xiaoyi_from_photo.vrm", "cat.vrm"):
    p = ROOT / "assets" / "vrm" / name
    if not p.is_file():
        continue
    g = load(p)
    nodes = g.get("nodes") or []
    print("=" * 78)
    print(f"{name}")
    print("=" * 78)
    print(f"顶层键: {sorted(g.keys())}")
    print(f"节点数: {len(nodes)}")
    print(f"meshes: {len(g.get('meshes') or [])}   skins: {len(g.get('skins') or [])}")

    sk = (g.get("skins") or [{}])[0]
    if g.get("skins"):
        print(f"  skins[0]: joints={len(sk.get('joints') or [])} "
              f"skeleton={sk.get('skeleton')} "
              f"inverseBindMatrices={'有' if 'inverseBindMatrices' in sk else '**无**'}")

    with_mesh = [i for i, n in enumerate(nodes) if "mesh" in n]
    with_skin = [i for i, n in enumerate(nodes) if "skin" in n]
    with_kids = [i for i, n in enumerate(nodes) if n.get("children")]
    dead = [i for i, n in enumerate(nodes)
            if "mesh" not in n and not n.get("children")]
    print(f"  带 mesh 的节点: {len(with_mesh)}   其中带 skin 的: {len(with_skin)}")
    print(f"  带 children 的节点: {len(with_kids)}")
    print(f"  **既无 mesh 也无 children 的节点: {len(dead)}**")

    # 根节点：不在任何 children 列表里的那些
    child_of = set()
    for n in nodes:
        for c in (n.get("children") or []):
            child_of.add(c)
    roots = [i for i in range(len(nodes)) if i not in child_of]
    print(f"  根节点索引: {roots}")
    print(f"  场景 scene.nodes: {(g.get('scenes') or [{}])[0].get('nodes')}")

    # 关键：humanBones 指向的节点，在树里可达吗？
    ext = (g.get("extensions") or {})
    hb = None
    for key in ("VRMC_vrm", "VRM"):
        if key in ext:
            hb = (ext[key].get("humanoid") or {}).get("humanBones")
            break
    if isinstance(hb, list):
        idxs = [e.get("node") for e in hb if isinstance(e, dict)]
    elif isinstance(hb, dict):
        idxs = [e.get("node") for e in hb.values() if isinstance(e, dict)]
    else:
        idxs = []
    print(f"  humanBones 指向 {len(idxs)} 个节点")
    reachable = set()
    stack = list(roots)
    while stack:
        i = stack.pop()
        if i in reachable:
            continue
        reachable.add(i)
        for c in (nodes[i].get("children") or []):
            stack.append(c)
    unreachable = [i for i in idxs if i not in reachable]
    print(f"  **从根节点可达的节点总数: {len(reachable)}**")
    print(f"  **humanBones 里不可达的节点: {len(unreachable)}** "
          f"{unreachable[:10] if unreachable else ''}")
    if unreachable:
        print(f"     → 这就是 three-vrm 报「骨骼不存在」的原因：")
        print(f"       它按节点树找骨骼，找不到就等于没有。")
    print()
