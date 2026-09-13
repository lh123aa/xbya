# -*- coding: utf-8 -*-
r"""把 `gen_chibi_vrm.py` 生成的模型**换成照片里量出来的颜色**。

为什么不直接改生成器：那是仓库里既有的工具，有它自己的用途
（`chibi_handmade.vrm` 就是它产出的）。这里做**后处理**，不动它。

做法：GLB 的材质是**明文 JSON**，`baseColorFactor` 就是颜色。
  GLB 结构 = 12 字节头 + 若干 chunk，每块 = [长度(4) 类型(4) 数据]
  · 改 JSON 里各材质的 `baseColorFactor`
  · JSON chunk 按 4 字节对齐补**空格**（0x20）
  · 重新算总长度
BIN chunk 原样搬运 —— 几何不动，只换颜色。

⚠️ 边界：换色**不会**改变形状。生成的是"几何拼出来的 Q 版"，
   不会变成照片里那个人的样子。颜色与发型轮廓可以贴近，五官是符号化的。
"""
import io
import json
import struct
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GEN = ROOT / "tools" / "gen_chibi_vrm.py"


def read_glb(path: Path):
    raw = path.read_bytes()
    magic, ver, total = struct.unpack_from("<4sII", raw, 0)
    assert magic == b"glTF", f"{path}: 不是 GLB"
    off = 12
    chunks = []
    while off < total:
        clen, ctype = struct.unpack_from("<II", raw, off)
        data = raw[off + 8: off + 8 + clen]
        chunks.append([ctype, bytearray(data)])
        off += 8 + clen
    return chunks


def write_glb(path: Path, chunks) -> int:
    out = bytearray()
    body = bytearray()
    for ctype, data in chunks:
        pad = (4 - (len(data) % 4)) % 4
        # JSON chunk 用空格补，BIN chunk 用 0 补（glTF 规范要求）
        data = data + (b" " * pad if ctype == 0x4E4F534A else b"\x00" * pad)
        body += struct.pack("<II", len(data), ctype) + data
    total = 12 + len(body)
    out += struct.pack("<4sII", b"glTF", 2, total)
    out += body
    path.write_bytes(out)
    return total


def hex_to_lin(h: str, alpha: float = 1.0):
    """sRGB hex → glTF 的 linear 值。

    ⚠️ 这一步不能省：glTF 的 baseColorFactor 是**线性空间**，
    直接把 sRGB 的 0~1 值填进去会**明显偏亮**（例如 #D5B7A6 会变成近乎白色）。
    """
    h = h.lstrip("#")
    srgb = [int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4)]

    def to_lin(c):
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    return [round(to_lin(c), 6) for c in srgb] + [alpha]


# ── 照片里量出来的颜色（source: _tmp_avatar_segment.py）──
#
# hair 的值要说明一下：分割时"暗色最大连通块"**吃进了背景树的暗部**，
# 所以量出来偏黑（#1A1A13）。照片里她确实是长黑发，但真发色带一点棕暖调。
# 这里取一个**在量与观察之间折中**的值，并如实标注这一点 —— 不假装它是纯量出来的。
PALETTE = {
    "skin":   "#D5B7A6",     # 量出：脸框内肤色像素中位色
    "hair":   "#241E1B",     # 量出 #1A1A13，但含背景树污染 ⇒ 略提亮加暖
    "shirt":  "#D8D8CE",     # 量出：白裙主体（上衣与裙子同色，照片里是连体白裙）
    "skirt":  "#D8D8CE",
    "eye":    "#4A3226",     # 照片里瞳色偏深棕（眼睛太小，未单独量，按观察取）
    "pupil":  "#120C0A",
    "blush":  "#E8A79C",     # 腮红：照片里是自然的淡红，取淡
    "ribbon": "#C9C4B8",     # 原版是粉红蝴蝶结，照片里没有 ⇒ 改成与裙同系的浅色
}


def fix_vrm1_humanbones(g: dict) -> tuple:
    """把 `humanBones` 从**字典**改成 VRM 1.0 规范要求的**数组**。

    为什么必须改（实测出来的两个后果）：
      · `three-vrm` 解析器按 1.0 规范读**数组**，读到字典时 humanoid 为空 ⇒
        **动画系统拿不到骨骼**，模型保持静止（表现是"姿势诡异/不动"）
      · 应用侧 `fitCamera` 想用 head 骨骼定位也拿不到 ⇒ 取景算错

    生成器 `gen_chibi_vrm.py` 写的是 0.x 风格（`{"head": {"node": 5}}`），
    但同一份文件里 `specVersion` 声明的是 `1.0` —— **声明与结构不一致**。

    Args:
        g: GLB 的 JSON 部分（原地修改）

    Returns:
        (改动条数, 说明)
    """
    ext = (g.get("extensions") or {}).get("VRMC_vrm") or {}
    humanoid = ext.get("humanoid")
    if not isinstance(humanoid, dict):
        return 0, "没有 VRMC_vrm.humanoid，跳过"
    hb = humanoid.get("humanBones")
    if isinstance(hb, list):
        return 0, "已经是数组，无需改"
    if not isinstance(hb, dict):
        return 0, f"humanBones 类型意外（{type(hb).__name__}），跳过"

    arr = []
    for bone, spec in hb.items():
        if not isinstance(spec, dict) or "node" not in spec:
            continue
        item = {"bone": bone, "node": spec["node"]}
        # restPose 在 1.0 里不是 humanBones 的字段（那是 0.x 的写法），
        # 但多带的键不影响解析器；为了不丢信息，把它挪到节点的 translation/rotation
        # 之外保留在自定义键下会让校验器报警，故这里**直接丢弃**，
        # 因为节点的 translation 本来就带着同样的静止姿态。
        arr.append(item)
    humanoid["humanBones"] = arr
    return len(arr), f"字典 → 数组，共 {len(arr)} 根骨骼"


def main() -> int:
    out = ROOT / "assets" / "vrm" / "xbya_from_photo.vrm"
    tmp = ROOT / "assets" / "vrm" / "_tmp_base.vrm"

    print("=" * 74)
    print("① 先让既有生成器产出基底模型")
    print("=" * 74)
    p = subprocess.run([sys.executable, str(GEN), str(tmp)],
                       cwd=str(ROOT), capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    print((p.stdout or "").strip()[-300:])
    if not tmp.is_file():
        print(f"✘ 生成失败（退出码 {p.returncode}）")
        print((p.stderr or "")[-500:])
        return 1
    print(f"  基底 {tmp.name}: {tmp.stat().st_size} bytes")

    print()
    print("=" * 74)
    print("② 把材质颜色换成照片量出来的值")
    print("=" * 74)
    chunks = read_glb(tmp)
    ji = next(i for i, (t, _) in enumerate(chunks) if t == 0x4E4F534A)
    g = json.loads(bytes(chunks[ji][1]).decode("utf-8"))
    mats = g.get("materials") or []
    print(f"  找到 {len(mats)} 个材质")
    print()
    print("  | 材质 | 原色（sRGB 近似） | 新色 |")
    print("  |------|------------------|------|")
    for m in mats:
        name = m.get("name")
        if name not in PALETTE:
            print(f"  | {name} | （不在调色板里，保持原样） | — |")
            continue
        pbr = m.setdefault("pbrMetallicRoughness", {})
        old = pbr.get("baseColorFactor")
        alpha = old[3] if old and len(old) == 4 else 1.0
        new = hex_to_lin(PALETTE[name], alpha)
        pbr["baseColorFactor"] = new
        print(f"  | {name} | {old} | `{PALETTE[name]}` → {new[:3]} |")
    chunks[ji][1] = bytearray(json.dumps(g, separators=(",", ":")).encode("utf-8"))

    print()
    print("=" * 74)
    print("③ 修 VRM 1.0 的 humanBones 结构（字典 → 数组）")
    print("=" * 74)
    n_fixed, why = fix_vrm1_humanbones(g)
    print(f"  {why}")
    chunks[ji][1] = bytearray(json.dumps(g, separators=(",", ":")).encode("utf-8"))

    size = write_glb(out, chunks)
    print()
    print(f"③ 写出 {out.relative_to(ROOT)}（{size} bytes）")

    # 回读校验
    back = read_glb(out)
    jb = next(d for t, d in back if t == 0x4E4F534A)
    g2 = json.loads(bytes(jb).decode("utf-8"))
    got = {m["name"]: m["pbrMetallicRoughness"]["baseColorFactor"]
           for m in g2["materials"] if m["name"] in PALETTE}
    ok = all(got[n][:3] == hex_to_lin(PALETTE[n])[:3] for n in got)
    print(f"   回读校验：{len(got)} 个材质颜色已替换，一致？{'✔ 是' if ok else '✘ 否'}")
    print(f"   几何未动：BIN chunk {'✔ 保留' if any(t != 0x4E4F534A for t, _ in back) else '✘ 丢了'}")

    tmp.unlink(missing_ok=True)
    print()
    print("=" * 74)
    print("下一步：把 config.yaml 的 ui.vrm_model 指向它，重启应用看效果")
    print("=" * 74)
    print(f"  ui.vrm_model: assets/vrm/{out.name}")
    print()
    print("⚠️ 形状是几何拼出来的 Q 版，**不会**长得像照片里那个人。")
    print("   能贴近的是：配色（肤色/发色/裙色）与发型轮廓（黑长直）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
