"""人工验收执行包：准备 / 查看 / 还原 / 清理（P4-A3）

## 这个脚本要解决的问题

`docs/agent/acceptance.md` §7.3 的 5 项验收（搜索 / 删除确认 / 打断 / 降级 / 安全边界）
一直挂着"需真机麦克风 + 人在场"。P4-A3 的目标不是把它自动化（自动化做不到 ——
麦克风采集、GUI 动效、人耳听感必须人来看），而是**把人的动作压到最小**：
只剩"照着念句子 + 看一眼现象 + 放一张截图"。

## 为什么必须先改白名单（而不是直接在真桌面上做）

`config.yaml` 里 `agent.safety.path_whitelist: []` = 用默认四目录，
也就是**用户真实的**桌面/文档/下载/图片。而"删除截图"这个场景里，
规则路由会把"截图"映射成 `*截图*` 通配符 —— 在真桌面上它**同时命中用户自己的截图**。
用户一旦确认，删掉的就不只是验收素材（虽然进回收站，但仍是误删）。

所以本脚本把白名单临时改成 `%TEMP%` 下的沙箱（沙箱里只放 `验收*` 开头的素材），
验收做完再一键还原。改动方式是**逐行替换 `path_whitelist:` 那一行**，
其余字节原样保留 —— 不用 YAML 解析再 dump，避免把用户的注释与格式改掉。
还原靠备份文件，不靠"再改回去"，所以即使中途出错也不会留下半个配置。

## 用法

    python tools/prepare_manual_acceptance.py init       # 建沙箱 + 改白名单 + 打印步骤
    python tools/prepare_manual_acceptance.py status     # 现在是不是沙箱模式
    python tools/prepare_manual_acceptance.py restore    # 还原 config.yaml（做完必跑）
    python tools/prepare_manual_acceptance.py cleanup    # 删沙箱（还原后再跑）

顺序：`init` → 启动欣雅（`启动欣雅.bat` 或 `python run.py`）→ 照
`docs/agent/manual-acceptance.md` 走一遍 → `restore` → `cleanup`
→ `python tools/check_manual_evidence.py`
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

CONFIG = PROJECT / "config.yaml"
BACKUP = PROJECT / "config.yaml.manual_backup"
STATE = PROJECT / "docs" / "agent" / "evidence" / "manual" / ".sandbox.json"

SANDBOX = Path(tempfile.gettempdir()) / "xiaoyi_manual"

#: 1x1 透明 PNG（最小合法 PNG）—— 素材要能被真实识图算作图片文件，但不必好看
_TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
    "YPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)

#: 沙箱素材：**全部以「验收」开头**，便于人工一眼分辨，也让预览列表干净
SEED_FILES = {
    "Desktop/验收截图_01.png": _TINY_PNG,
    "Desktop/验收截图_02.png": _TINY_PNG,
    "Desktop/验收合同_2025.pdf": b"%PDF-1.4\n% manual acceptance fixture\n%%EOF\n",
    "Desktop/验收报告.pdf": b"%PDF-1.4\n% manual acceptance fixture\n%%EOF\n",
    "Desktop/验收笔记.txt": "这是人工验收用的文本文件。\n".encode("utf-8"),
    "Documents/验收文档.txt": "文档目录里的验收素材。\n".encode("utf-8"),
    "Downloads/验收压缩包.zip": b"PK\x05\x06" + b"\x00" * 18,   # 空 zip
    "Pictures/验收图片_01.png": _TINY_PNG,
}

#: 白名单要指向的四个沙箱目录（与默认四目录同名，这样用户的说法不用变）
WHITELIST_DIRS = ["Desktop", "Documents", "Downloads", "Pictures"]


def _say(msg: str = "") -> None:
    print(msg)


def _whitelist_line_index(lines: list) -> int:
    """找到 `path_whitelist:` 那一行的下标；找不到返回 -1"""
    for i, ln in enumerate(lines):
        if re.match(r"^\s*path_whitelist\s*:", ln):
            return i
    return -1


def _read_config_text() -> str:
    if not CONFIG.exists():
        raise SystemExit(f"找不到 {CONFIG.name}")
    return CONFIG.read_text(encoding="utf-8")


def cmd_init(args) -> int:
    if BACKUP.exists() and not args.force:
        _say(f"[!] 已存在备份 {BACKUP.name} —— 说明上次验收没有还原干净。")
        _say(f"    先跑 `restore` 还原，或加 --force 覆盖备份重新开始。")
        return 1

    # ── 1. 建沙箱并播种素材 ──
    if SANDBOX.exists():
        shutil.rmtree(SANDBOX, ignore_errors=True)
    for rel in SEED_FILES:
        (SANDBOX / rel).parent.mkdir(parents=True, exist_ok=True)
    for rel, blob in SEED_FILES.items():
        (SANDBOX / rel).write_bytes(blob)
    _say(f"[1/4] 沙箱已建: {SANDBOX}")
    for rel in sorted(SEED_FILES):
        _say(f"        {rel}")

    # ── 2. 备份 config.yaml ──
    text = _read_config_text()
    BACKUP.write_text(text, encoding="utf-8")
    _say(f"[2/4] 已备份配置: {BACKUP.name}（还原靠它，不靠「再改回去」）")

    # ── 3. 逐行替换白名单（其余字节原样保留）──
    lines = text.splitlines(keepends=True)
    idx = _whitelist_line_index(lines)
    if idx < 0:
        BACKUP.unlink(missing_ok=True)
        _say("[x] 在 config.yaml 里找不到 `path_whitelist:` 行 —— 不敢乱改，已还原备份")
        return 1
    if '"' in lines[idx] and "xiaoyi_manual" in lines[idx]:
        _say("[!] 白名单看起来已经是沙箱模式了；继续只会重复覆盖。")
        BACKUP.unlink(missing_ok=True)
        return 1
    dirs = ", ".join(f'"{SANDBOX / d}"' for d in WHITELIST_DIRS)
    lines[idx] = f"    path_whitelist: [{dirs}]        # P4-A3 人工验收临时覆盖，用完 restore\n"
    CONFIG.write_text("".join(lines), encoding="utf-8")
    _say("[3/4] 白名单已临时改为沙箱四目录（其余配置字节未动）")

    # ── 4. 记录状态，供 status / check 用 ──
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps({
        "sandbox": str(SANDBOX), "dirs": WHITELIST_DIRS,
        "seeded": sorted(SEED_FILES),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    _say(f"[4/4] 状态已记录: {STATE.relative_to(PROJECT)}")

    _say()
    _say("=" * 74)
    _say("现在请启动欣雅（`启动欣雅.bat` 或 `python run.py`），然后打开")
    _say("docs/agent/manual-acceptance.md 照着做。第 1 句就念：")
    _say()
    _say("    找一下桌面上的 PDF 文件")
    _say()
    _say("做完务必回来跑：")
    _say("    python tools/prepare_manual_acceptance.py restore")
    _say("    python tools/prepare_manual_acceptance.py cleanup")
    _say("    python tools/check_manual_evidence.py")
    _say("=" * 74)
    return 0


def cmd_status(args) -> int:
    text = _read_config_text()
    lines = text.splitlines(keepends=True)
    idx = _whitelist_line_index(lines)
    active = idx >= 0 and "xiaoyi_manual" in lines[idx]
    _say(f"沙箱模式: {'开' if active else '关'}")
    _say(f"配置文件  : {CONFIG}")
    _say(f"备份存在  : {BACKUP.exists()}")
    _say(f"沙箱存在  : {SANDBOX.exists()}  ({SANDBOX})")
    if idx >= 0:
        # 只打印这一行的"形态"，不打印其它内容
        shown = lines[idx].strip()
        if len(shown) > 120:
            shown = shown[:117] + "..."
        _say(f"白名单行  : {shown}")
    return 0


def cmd_restore(args) -> int:
    if not BACKUP.exists():
        _say("[!] 没有备份文件 —— 要么没做过 init，要么已经还原过了。")
        _say("    若白名单仍是沙箱路径而备份丢了，请手动把 path_whitelist 改回 []。")
        return 1
    shutil.copyfile(BACKUP, CONFIG)
    BACKUP.unlink()
    _say(f"[ok] 已从备份还原 {CONFIG.name}，备份文件已删除")

    text = _read_config_text()
    idx = _whitelist_line_index(text.splitlines(keepends=True))
    _say(f"     现在的白名单行: {text.splitlines()[idx].strip() if idx >= 0 else '(找不到)'}")
    return 0


def cmd_cleanup(args) -> int:
    if SANDBOX.exists():
        shutil.rmtree(SANDBOX, ignore_errors=True)
        _say(f"[ok] 沙箱已删除: {SANDBOX}")
    else:
        _say(f"[--] 沙箱本来就不存在: {SANDBOX}")
    if STATE.exists():
        STATE.unlink()
        _say("[ok] 状态文件已删除")
    if BACKUP.exists():
        _say("[!] 注意：备份文件还在 —— 说明配置可能仍是沙箱模式，请先跑 restore")
    return 0


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="人工验收执行包（P4-A3）：准备沙箱环境 / 还原配置 / 清理")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_init = sub.add_parser("init", help="建沙箱 + 备份并临时改白名单")
    p_init.add_argument("--force", action="store_true", help="覆盖上次遗留的备份")
    p_init.set_defaults(func=cmd_init)
    sub.add_parser("status", help="查看当前是否沙箱模式").set_defaults(func=cmd_status)
    sub.add_parser("restore", help="从备份还原 config.yaml").set_defaults(func=cmd_restore)
    sub.add_parser("cleanup", help="删除沙箱与状态文件").set_defaults(func=cmd_cleanup)
    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
