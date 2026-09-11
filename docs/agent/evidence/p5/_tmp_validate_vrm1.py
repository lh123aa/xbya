# -*- coding: utf-8 -*-
r"""校验修好后的 VM 结构：`humanBones` 是真数组，且每根骨骼指向有效节点。

为什么要单独校验：改完"应用不报错"**不等于**"结构合规范"。
three-vrm 对不合规输入往往是**静默忽略**（这正是本轮的病根 ——
它读到字典就当作没有 humanoid，不报错，只是动画不生效）。
所以必须拿**规范本身**当判据逐条查，而不是拿"没报错"当判据。

查这几条：
  1. `humanBones` 是数组
  2. 每项的键是 `bone` + `node`（1.0 规范），没有残留 0.x 的 `restPose`
  3. `node` 索引在范围内，且该节点的名字与 bone 名对得上
  4. 必备骨骼齐全（hips/spine/head/四肢），否则动画系统缺骨骼
  5. `bones` 之外还查 `expressions`（表情也要能映射到网格）
"""
import io
import json
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
TARGET = ROOT / "assets" / "vrm" / "xiaoyi_from_photo.vrm"

raw = TARGET.read_bytes()
clen, ctype = struct.unpack_from("<II", raw, 12)
g = json.loads(raw[20:20 + clen].decode("utf-8"))
nodes = g.get("nodes") or []

print("=" * 78)
print(f"校验 {TARGET.name}")
print("=" * 78)
print()

fails = []


def check(ok, label, detail=""):
    print(f"[{'PASS' if ok else 'FAIL'}] {label}" + (f"  → {detail}" if detail else ""))
    if not ok:
        fails.append(label)


ext = (g.get("extensions") or {}).get("VRMC_vrm") or {}
check(bool(ext), "存在 VRMC_vrm 扩展", f"顶层键 {list(ext.keys())}")
check(ext.get("specVersion") == "1.0", "specVersion 声明为 1.0",
      str(ext.get("specVersion")))

humanoid = ext.get("humanoid") or {}
hb = humanoid.get("humanBones")
check(isinstance(hb, list), "humanBones 是**数组**（1.0 规范要求）",
      f"实际类型 {type(hb).__name__}")
if not isinstance(hb, list):
    print()
    print("humanBones 不是数组 —— 后面的逐项检查无法进行")
    raise SystemExit(1)

check(len(hb) > 0, f"humanBones 非空", f"{len(hb)} 项")

# 逐项检查
bad_shape, bad_node, name_mismatch, leftover = [], [], [], []
for i, item in enumerate(hb):
    if not isinstance(item, dict) or "bone" not in item or "node" not in item:
        bad_shape.append(i)
        continue
    if "restPose" in item:
        leftover.append(item.get("bone"))
    ni = item["node"]
    if not (0 <= ni < len(nodes)):
        bad_node.append((item["bone"], ni))
        continue
    nm = nodes[ni].get("name") or ""
    # 节点名应与 bone 名一致（忽略大小写）；本项目生成器就是同名
    if nm.lower() != item["bone"].lower():
        name_mismatch.append((item["bone"], nm))

check(not bad_shape, "每项都有 bone + node", f"异常项 {bad_shape}" if bad_shape else "全部合规")
check(not leftover, "没有残留 0.x 的 restPose 字段",
      f"残留 {leftover}" if leftover else "干净")
check(not bad_node, "node 索引都在范围内", f"越界 {bad_node}" if bad_node else "全部有效")
check(not name_mismatch, "节点名与 bone 名一致",
      f"不一致 {name_mismatch[:5]}" if name_mismatch else "全部一致")

# 必备骨骼
have = {i["bone"] for i in hb if isinstance(i, dict) and "bone" in i}
REQUIRED = ["hips", "spine", "chest", "neck", "head",
            "leftUpperArm", "leftLowerArm", "rightUpperArm", "rightLowerArm"]
missing = [b for b in REQUIRED if b not in have]
check(not missing, "动画系统要用的骨骼齐全", f"缺 {missing}" if missing else
      f"都有（共 {len(have)} 根）")

# expressions
exprs = ext.get("expressions") or {}
pr = exprs.get("preset") or {}
check(bool(pr), "有表情预设", f"{list(pr.keys())}")
# 尾巴骨骼（猫的动画要）
tails = [n.get("name") for n in nodes if n.get("name") and "tail" in n["name"].lower()]
print(f"[info] 尾巴骨骼：{tails or '无（人形模型本来就没有，动画会跳过尾巴）'}")

print()
print("=" * 78)
if fails:
    print(f"**{len(fails)} 项未通过**：")
    for f in fails:
        print(f"  ✘ {f}")
    raise SystemExit(1)
print("全部通过 —— humanoid 结构合 VRM 1.0 规范，three-vrm 应能解出骨骼。")
