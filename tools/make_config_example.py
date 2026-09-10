"""从 config.yaml 生成 config.example.yaml（**涂抹密钥 + 抹掉本机路径**）

## 为什么要有这个脚本

`config.yaml` 在 `.gitignore` 里（它含活的 API key），而仓库需要一个能照抄的模板。
手抄模板的问题：**要我先看到真实值**——那就会把密钥带进对话、日志、证据文件。
所以模板一律由脚本生成，脚本只做「读 → 抹 → 写」，密钥值既不打印也不落第二处。

## 抹什么

| 类别 | 规则 | 替换为 |
|------|------|--------|
| 密钥 | 键名命中 `api_key/token/secret/password/cookie`，或值以 `gsk_`/`sk-`/`Bearer ` 开头 | `''`（空串，让使用者自己填） |
| 本机路径 | 值里出现 `C:\\Users\\<用户名>\\…` | `C:\\Users\\<你的用户名>\\…` |

## 为什么不是"只保留示例片段"

`config.example.yaml` 曾经是手写的，结果**落后于实现**：顶层少了整个 `agent:` 段
（P0~P3 的全部 Agent 层配置）。模板落后比没有模板更坏——照着它配会得到半个功能。
所以这里采取"全量生成"：模板 = 真实配置去掉机密与本机信息。

用法：
    python tools/make_config_example.py          # 生成
    python tools/make_config_example.py --check  # 只检查：模板是否落后 / 是否含密钥（不写文件）
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]   # tools/ 的上一级 = 项目根
SRC = ROOT / "config.yaml"
DST = ROOT / "config.example.yaml"

SECRET_KEY_RE = re.compile(
    r"(api[_-]?key|apikey|access[_-]?token|token|secret|password|passwd|cookie)",
    re.IGNORECASE,
)
SECRET_VALUE_RE = re.compile(r"^(gsk_|sk-|Bearer\s)")
#: 本机绝对路径里的用户名（C:\Users\xxx\ 或 /home/xxx/）
HOMEDIR_RE = re.compile(r"([A-Za-z]:\\Users\\)([^\\\"']+)", re.IGNORECASE)
POSIX_HOME_RE = re.compile(r"(/home/)([^/\"']+)")

BLANK = "''"


def mask_line(line: str) -> tuple[str, int]:
    """返回 (处理后的行, 涂抹处数)。只处理 `key: value` 形态的行。"""
    raw = line.rstrip("\n")
    m = re.match(r"^(\s*-?\s*)([A-Za-z0-9_.\-]+)(\s*:\s*)(.*)$", raw)
    if not m:
        return line, 0
    indent, key, sep, rest = m.groups()
    if not rest or rest.startswith("#"):
        return line, 0

    # 拆「值」与「行尾注释」；值可能被引号包着
    val, comment = rest, ""
    cm = re.search(r"\s+#", rest)
    if cm:
        val, comment = rest[: cm.start()], rest[cm.start() :]
    bare = val.strip().strip("'\"")

    n = 0
    if bare and (SECRET_KEY_RE.search(key) or SECRET_VALUE_RE.match(bare)):
        return f"{indent}{key}{sep}{BLANK}{comment}\n", 1

    # 本机路径打码（密钥之外，用户名也不该进模板）
    newval, k = HOMEDIR_RE.subn(r"\1<你的用户名>\\", val)
    if k:
        n += k
    newval, k = POSIX_HOME_RE.subn(r"\1<your-user>/", newval)
    if k:
        n += k
    if n:
        return f"{indent}{key}{sep}{newval}{comment}\n", n
    return line, 0


def build(template_header_fn) -> tuple[str, int, int]:
    """返回 (模板全文, 密钥涂抹数, 路径涂抹数)"""
    text = SRC.read_text(encoding="utf-8")
    out, secrets, paths = [], 0, 0
    for line in text.splitlines(keepends=True):
        new, n = mask_line(line)
        out.append(new)
        if n:
            if SECRET_KEY_RE.search(line.split(":")[0]) or SECRET_VALUE_RE.search(line):
                secrets += n
            else:
                paths += n
    return template_header_fn(secrets, paths) + "".join(out), secrets, paths


def _leaks(text: str) -> list:
    return re.findall(
        r"(gsk_[A-Za-z0-9]{10,}|sk-or-v1-[A-Za-z0-9]{10,}|sk-[A-Za-z0-9]{20,})", text
    )


def current_user_leak(text: str) -> bool:
    return bool(HOMEDIR_RE.search(text))


def main() -> int:
    ap = argparse.ArgumentParser(description="生成/检查 config.example.yaml")
    ap.add_argument("--check", action="store_true",
                    help="只检查（不写文件）：模板是否含密钥/本机路径、是否落后于 config.yaml")
    args = ap.parse_args()

    if not SRC.exists():
        print(f"找不到 {SRC}", file=sys.stderr)
        return 2

    def header(secrets: int, paths: int) -> str:
        return (
            "# 欣雅配置模板 —— 由 tools/make_config_example.py 从 config.yaml 自动生成\n"
            "# 用法：复制成 config.yaml 后填入你自己的密钥\n"
            f"# 本次生成：{secrets} 处密钥已抹为空串、{paths} 处本机用户名已打码\n"
            "# 说明：config.yaml 在 .gitignore 里（含活的 key），本模板会入库，所以必须无机密。\n"
            "#      改完 config.yaml 后请重跑本脚本，避免模板落后于实现。\n\n"
        )

    text, secrets, paths = build(header)

    if args.check:
        # ⚠️ 这里必须检查**磁盘上现有的模板**，而不是刚 build 出来的文本 ——
        # 第一版写成了检查 `text`，于是"顶层键覆盖"永远报"齐全"，
        # 等于一条恒真的空断言（与验收阶段缺陷 20「假件复刻了它要检测的 bug」同类）。
        if not DST.exists():
            print(f"{DST.name} 不存在 ❌（先运行不带 --check 的命令生成）")
            return 1
        on_disk = DST.read_text(encoding="utf-8")
        ok = True
        leaked = _leaks(on_disk)
        print(f"模板密钥残留：{len(leaked)} 处" + ("" if leaked else " ✅"))
        ok &= not leaked
        if current_user_leak(on_disk):
            print("模板本机用户名残留：有 ❌")
            ok = False
        else:
            print("模板本机用户名残留：无 ✅")

        real = {ln.split(":")[0] for ln in SRC.read_text(encoding="utf-8").splitlines()
                if re.match(r"^[A-Za-z_]", ln)}
        tmpl = {ln.split(":")[0] for ln in on_disk.splitlines()
                if re.match(r"^[A-Za-z_]", ln)}
        missing = sorted(real - tmpl)
        print(f"模板顶层键覆盖：{'缺 ' + ', '.join(missing) if missing else '齐全 ✅'}"
              f"（config.yaml 有 {len(real)} 个顶层键，模板有 {len(tmpl)} 个）")
        ok &= not missing
        if missing:
            print(f"→ 模板落后于实现：请运行 python tools/make_config_example.py 重新生成")
        return 0 if ok else 1

    DST.write_text(text, encoding="utf-8")
    print(f"已生成 {DST.name}（密钥 {secrets} 处、本机路径 {paths} 处已处理）")
    leaked = _leaks(DST.read_text(encoding="utf-8"))
    print(f"自检：残留密钥模式 {len(leaked)} 处" + ("" if leaked else " ✅"))
    return 0 if not leaked else 1


if __name__ == "__main__":
    raise SystemExit(main())
