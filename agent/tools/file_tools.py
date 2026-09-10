"""文件工具集

六个文件操作工具，全部消费 SafetyService 做路径校验：
- file_search  搜索文件
- file_list    列出目录内容
- file_read    读取/打开文件
- file_rename  重命名
- file_move    移动
- file_delete  删除（强制走回收站，代码中不存在永久删除分支）

安全契约（R1 防护）：
1. 所有路径先经 guard.validate_path() 校验，白名单外一律拒绝
2. 所有写操作（rename/move/delete）记录审计日志
3. file_delete 只有 send2trash 一条路径，`permanent=True` 直接抛错
"""

import logging
import os
import re
import shutil
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent.seams.safety import PathNotAllowed, SafetyService, SecurityError
from agent.tools.base import BaseTool, ParamError, ToolError, ToolResult

logger = logging.getLogger(__name__)

# ── 结果数量上限 ──
MAX_SEARCH_RESULTS = 50
MAX_LIST_RESULTS = 100

# ── 文件大小上限 ──
MAX_READ_BYTES = 10 * 1024 * 1024      # 10 MB
MAX_READ_CHARS = 10000                 # 返回文本字符上限

# ── 扫描预算（秒），防止在超大目录上卡住 ──
SCAN_BUDGET = 2.0

# ── 判定为文本文件的扩展名 ──
TEXT_EXTENSIONS = {
    ".txt", ".md", ".json", ".yaml", ".yml", ".xml", ".csv", ".log",
    ".py", ".js", ".ts", ".java", ".c", ".cpp", ".h", ".cs", ".go",
    ".rs", ".rb", ".php", ".sh", ".bat", ".ps1", ".sql", ".ini", ".cfg",
    ".html", ".css", ".toml",
}

# ── 需要跳过扫描的目录 ──
SKIP_DIR_NAMES = {
    "__pycache__", ".git", ".svn", ".idea", ".vscode",
    "node_modules", "$RECYCLE.BIN", "System Volume Information",
}


def _human_size(num_bytes: int) -> str:
    """字节数 → 人类可读（1.2 MB）"""
    size = float(num_bytes)
    # 循环只处理 B/KB/MB，GB 作为**兜底**返回 —— 这样每一行都可达，
    # 不需要死代码，也就不需要覆盖率屏蔽注释去盖住它。
    # （原写法把 GB 也放进循环并在末尾留一个"逻辑上不可达"的 return，
    #   那个 return 只能靠屏蔽注释盖住，等于用屏蔽换覆盖率 —— 项目不允许。）
    #
    # 注：这里刻意不写出那个屏蔽指令的字面量。写出它会让"数一数全项目有几处屏蔽"
    # 的 grep 把**这行注释**也算进去（实测就这么多数出 6 处、文档只登记 5 处），
    # 于是审计被自己的说明文字骗了。屏蔽指令只在真正需要它的地方出现。
    for unit in ("B", "KB", "MB"):
        if size < 1024:
            if unit == "B":
                return f"{int(size)} B"
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def _time_range_start(time_range: str) -> Optional[float]:
    """把时间范围标记转成起始时间戳（None 表示不限制）"""
    now = datetime.now()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)

    mapping = {
        "today": today,
        "yesterday": today - timedelta(days=1),
        "last_2_days": today - timedelta(days=2),
        "last_week": today - timedelta(days=7),
        "this_week": today - timedelta(days=today.weekday()),
    }
    dt = mapping.get(time_range)
    return dt.timestamp() if dt else None


def _time_range_end(time_range: str) -> Optional[float]:
    """时间范围结束时间戳（仅 yesterday 需要上界）"""
    now = datetime.now()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if time_range == "yesterday":
        return today.timestamp()
    return None


class _FileToolBase(BaseTool):
    """文件工具公共基类

    统一持有安全守卫，提供目标解析与目录扫描辅助。
    """

    risk_level = "low"
    timeout = 30

    def __init__(self, guard: SafetyService) -> None:
        """
        Args:
            guard: 安全守卫（提供路径校验与白名单）
        """
        self._guard = guard

    # ── 路径与目录 ──

    @staticmethod
    def _exists(path) -> bool:
        """安全的存在性判断

        注意：Python 3.12 的 Path.exists() 只吞掉 ENOENT/ENOTDIR 等"预期"错误，
        其余 OSError（权限、IO 故障）会重新抛出。工具层不应因此崩溃，
        故统一用本方法把任何文件系统错误都视为"不存在"。
        """
        try:
            return Path(path).exists()
        except (OSError, ValueError):
            return False

    def _safe_path(self, raw: str) -> Path:
        """校验并返回安全路径"""
        return self._guard.validate_path(raw)

    def _default_dirs(self) -> List[Path]:
        """默认搜索目录（白名单根目录中实际存在的）"""
        return [p for p in self._guard.whitelist_roots() if self._exists(p)]

    def _resolve_dirs(self, dir_names: Optional[List[str]]) -> List[Path]:
        """把目录名（Desktop/Documents/...）或路径解析为存在的目录列表

        **契约（P4-B2 起）：只有"未指定"才允许兜底**

        | 入参 | 行为 |
        |------|------|
        | `None` / `[]` / 全空串 | 未指定 → 默认四目录（唯一允许的兜底） |
        | **字符串**（含逗号串） | 抛 `ParamError`：期望数组 |
        | 列表，但**一个都认不出来** | 抛 `ParamError`：明确拒绝，**不再**回退默认目录 |
        | 列表，部分可识别 | 用可识别的那些（非法项记日志） |

        为什么必须把兜底收窄到只剩"未指定"：`None`（没说）与"说了但我不认识"
        原先共用同一个出口，于是**写操作的目标会被静默换掉**。
        实测（`tools/_tmp_b2_controlled.py`）：
        `file_move(dest="不存在的目录")` → 回退四目录 → 取 `dirs[0]` = **Desktop**，
        文件被挪到桌面**并报成功**。用户听到的是"我没听懂"，发生的是"文件去别处了"。

        字符串那条更隐蔽：字符串可迭代，`"Downloads"` 会被逐字符拆成
        D/o/w/n/l/o/a/d/s 去解析，一个都匹配不上 → 同样落到兜底出口 →
        `file_search(dirs="Downloads")` 变成**搜索四个目录**（实测命中 1 个 → 3 个）。
        """
        # 未指定 → 默认四目录
        if not dir_names:
            return self._default_dirs()

        # 字符串 → 显式拒绝（哪怕它看起来像"只有一个值"）
        if isinstance(dir_names, str):
            raise ParamError(
                self.name,
                f'期望数组，收到字符串 {dir_names!r}；即使只有一个值也请写成数组，'
                f'例如 ["{dir_names}"]',
                "dirs",
            )

        roots = {p.name.lower(): p for p in self._guard.whitelist_roots()}
        result: List[Path] = []
        unknown: List[str] = []

        for item in dir_names:
            if not item:
                continue
            key = str(item).strip().lower()
            if key in roots:
                p = roots[key]
            else:
                try:
                    p = self._safe_path(str(item))
                except (PathNotAllowed, SecurityError):
                    unknown.append(str(item))
                    continue
            if self._exists(p) and p.is_dir() and p not in result:
                result.append(p)
            else:
                unknown.append(str(item))

        if result:
            if unknown:
                logger.warning("[file_tools] 跳过无法识别的目录: %s", unknown)
            return result

        available = "、".join(p.name for p in self._guard.whitelist_roots())
        raise ParamError(
            self.name,
            f"不认识这些目录：{'、'.join(unknown) or '(空)'}；可用的目录是：{available}",
            "dirs",
        )

    def _dirs_or_error(self, raw: Any) -> tuple:
        """解析目录；非法时返回 `(None, 面向用户的说明)`

        工具层不该把 `ParamError` 的技术文案念给用户（"参数错误 [file_search].dirs:
        期望数组…"），但也**绝不能**把异常漏出去 —— 漏出去会被注册表记成"执行异常"，
        用户听到一句无从照做的技术话。所以这里把技术原因写日志、
        把一句能照做的话交给用户。
        """
        try:
            return self._resolve_dirs(raw), None
        except ParamError as e:
            logger.warning("[file_tools] 目录解析失败: %s", e)
            names = "、".join(p.name for p in self._guard.whitelist_roots())
            # 把入参渲染成人话：调用方常传单元素列表（file_move 就是这样），
            # 直接把 `['不存在的目录']` 的 repr 念给用户会很怪。
            if isinstance(raw, (list, tuple)):
                shown = "、".join(str(x) for x in raw)
            else:
                shown = str(raw)
            return None, f"「{shown}」这个位置我没听懂哦，我只能操作：{names}"

    # ── 文件信息 ──

    @staticmethod
    def _file_info(path: Path) -> Dict[str, Any]:
        """收集文件元信息"""
        try:
            stat = path.stat()
            mtime = stat.st_mtime
            size = stat.st_size
        except OSError:
            mtime = 0.0
            size = 0

        return {
            "name": path.name,
            "path": str(path),
            "size": size,
            "size_text": _human_size(size),
            "mtime": mtime,
            "mtime_text": (
                datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M") if mtime else "未知"
            ),
        }

    # ── 扫描 ──

    def _scan(
        self,
        dirs: List[Path],
        pattern: str = "*",
        time_range: Optional[str] = None,
        limit: int = MAX_SEARCH_RESULTS,
        include_dirs: bool = False,
    ) -> tuple:
        """在目录中扫描匹配文件

        Args:
            dirs: 待扫描目录
            pattern: glob 模式（文件名匹配，大小写不敏感）
            time_range: 时间范围标记
            limit: 结果上限
            include_dirs: 是否包含子目录

        Returns:
            (文件信息列表, 是否被截断)
        """
        import fnmatch

        pat = (pattern or "*").lower()
        start_ts = _time_range_start(time_range) if time_range else None
        end_ts = _time_range_end(time_range) if time_range else None

        results: List[Dict[str, Any]] = []
        truncated = False
        deadline = time.time() + SCAN_BUDGET

        for root_dir in dirs:
            if not self._exists(root_dir):
                continue
            stack = [root_dir]
            while stack:
                if time.time() > deadline or len(results) >= limit:
                    truncated = len(results) >= limit
                    break
                current = stack.pop()
                try:
                    entries = list(os.scandir(current))
                except (PermissionError, OSError):
                    continue

                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            if entry.name in SKIP_DIR_NAMES:
                                continue
                            if include_dirs and fnmatch.fnmatch(entry.name.lower(), pat):
                                results.append(self._file_info(Path(entry.path)))
                            stack.append(Path(entry.path))
                            continue

                        if not entry.is_file(follow_symlinks=False):
                            continue
                        if not fnmatch.fnmatch(entry.name.lower(), pat):
                            continue

                        if start_ts or end_ts:
                            try:
                                mtime = entry.stat().st_mtime
                            except OSError:
                                continue
                            if start_ts and mtime < start_ts:
                                continue
                            if end_ts and mtime >= end_ts:
                                continue

                        results.append(self._file_info(Path(entry.path)))
                        if len(results) >= limit:
                            truncated = True
                            break
                    except OSError:
                        continue

                if truncated:
                    break
            if truncated:
                break

        results.sort(key=lambda x: x["mtime"], reverse=True)
        return results, truncated

    # ── 目标解析（支持模式、名称、序号占位）──

    def _resolve_targets(self, target: str) -> tuple:
        """把 target 解析为具体文件路径列表

        支持三种形式：
        - 绝对/相对路径 → 直接校验
        - 含通配符的模式（*报告*）→ 搜索
        - 纯文件名 → 在白名单目录中按名搜索

        Returns:
            (路径列表, 错误文案)
        """
        if not target or not str(target).strip():
            return [], "你想操作哪个文件呀？"

        raw = str(target).strip()

        # 形式 1：绝对路径
        try:
            p = Path(raw).expanduser()
            if p.is_absolute():
                safe = self._safe_path(str(p))
                if self._exists(safe):
                    return [safe], ""
                return [], f"咦，没找到「{safe.name}」这个文件呢"
        except PathNotAllowed as e:
            return [], e.reason
        except SecurityError as e:
            return [], e.reason

        # 形式 2/3：模式或文件名 → 搜索
        pattern = raw if any(c in raw for c in "*?") else f"*{raw}*"
        if "." in raw and not any(c in raw for c in "*?"):
            pattern = raw  # 带扩展名时精确匹配优先

        matches, _ = self._scan(self._default_dirs(), pattern=pattern, limit=MAX_SEARCH_RESULTS)

        if not matches:
            return [], f"咦，没找到「{raw}」呢，是不是名字记错了？"

        return [Path(m["path"]) for m in matches], ""


# ══════════════════════════════════════════════════════
#  1. file_search
# ══════════════════════════════════════════════════════

class FileSearchTool(_FileToolBase):
    """搜索文件

    支持按文件名模式与时间范围搜索，结果按修改时间倒序。
    """

    name = "file_search"
    description = "在常用目录中搜索文件，支持文件名模式（如 *合同*、*.pdf）与时间范围"
    risk_level = "low"
    params_schema = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "文件名 glob 模式，如 *合同*"},
            "dirs": {
                "type": "array",
                "items": {"type": "string"},
                "description": "搜索目录（Desktop/Documents/Downloads/Pictures），默认全部",
            },
            "time_range": {
                "type": "string",
                "enum": ["today", "yesterday", "last_2_days", "last_week", "this_week"],
                "description": "时间范围过滤",
            },
        },
        "required": ["pattern"],
    }

    def execute(self, params: Dict[str, Any]) -> ToolResult:
        pattern = str(params.get("pattern") or "*")
        dirs, err = self._dirs_or_error(params.get("dirs"))
        time_range = params.get("time_range")

        if err:
            return ToolResult.fail(err, emotion="think")
        if not dirs:
            return ToolResult.empty("这些文件夹我都没找到呢")

        files, truncated = self._scan(
            dirs, pattern=pattern, time_range=time_range, limit=MAX_SEARCH_RESULTS
        )

        if not files:
            desc = f"「{pattern}」" if pattern not in ("*", "") else "文件"
            return ToolResult.empty(f"没找到{desc}相关的文件呢")

        summary = f"找到 {len(files)} 个文件"
        if truncated:
            summary += f"（只显示前 {MAX_SEARCH_RESULTS} 个）"

        return ToolResult.ok(
            data=files,
            summary=summary,
            truncated=truncated,
            emotion="happy",
        )


# ══════════════════════════════════════════════════════
#  2. file_list
# ══════════════════════════════════════════════════════

class FileListTool(_FileToolBase):
    """列出目录内容"""

    name = "file_list"
    description = "列出常用目录下的文件与子目录"
    risk_level = "low"
    params_schema = {
        "type": "object",
        "properties": {
            "dirs": {
                "type": "array",
                "items": {"type": "string"},
                "description": "要列出的目录，默认桌面",
            },
            "include_dirs": {
                "type": "boolean",
                "description": "是否包含子目录",
            },
        },
    }

    def execute(self, params: Dict[str, Any]) -> ToolResult:
        dirs, err = self._dirs_or_error(params.get("dirs"))
        include_dirs = bool(params.get("include_dirs", True))

        if err:
            return ToolResult.fail(err, emotion="think")
        if not dirs:
            return ToolResult.empty("这些文件夹我都没找到呢")

        all_items: List[Dict[str, Any]] = []
        truncated = False

        for d in dirs:
            try:
                entries = sorted(
                    os.scandir(d),
                    key=lambda e: e.stat().st_mtime if e.is_file() else 0,
                    reverse=True,
                )
            except (PermissionError, OSError):
                continue

            for entry in entries:
                try:
                    is_dir = entry.is_dir(follow_symlinks=False)
                    if is_dir and not include_dirs:
                        continue
                    if is_dir and entry.name in SKIP_DIR_NAMES:
                        continue
                    info = self._file_info(Path(entry.path))
                    info["is_dir"] = is_dir
                    all_items.append(info)
                    if len(all_items) >= MAX_LIST_RESULTS:
                        truncated = True
                        break
                except OSError:
                    continue
            if truncated:
                break

        if not all_items:
            names = "、".join(d.name for d in dirs)
            return ToolResult.empty(f"{names} 里是空的呢")

        summary = f"共 {len(all_items)} 项"
        if truncated:
            summary += f"（只显示前 {MAX_LIST_RESULTS} 项）"

        return ToolResult.ok(data=all_items, summary=summary, truncated=truncated)


# ══════════════════════════════════════════════════════
#  3. file_read
# ══════════════════════════════════════════════════════

class FileReadTool(_FileToolBase):
    """读取文件内容（文本）或用默认程序打开"""

    name = "file_read"
    description = "读取文本文件内容；非文本文件用系统默认程序打开"
    risk_level = "low"
    params_schema = {
        "type": "object",
        "properties": {
            "target": {"type": "string", "description": "文件名、路径或名称模式"},
        },
        "required": ["target"],
    }

    def execute(self, params: Dict[str, Any]) -> ToolResult:
        target = params.get("target")
        paths, err = self._resolve_targets(target)
        if err:
            return ToolResult.fail(err, emotion="sad")
        if not paths:
            return ToolResult.fail("没找到要打开的文件", emotion="sad")

        path = paths[0]

        # 多匹配时提示（由摘要器/管线决定是否让用户选择）
        if len(paths) > 1:
            names = "、".join(p.name for p in paths[:3])
            return ToolResult.ok(
                data={"candidates": [self._file_info(p) for p in paths]},
                summary=f"找到 {len(paths)} 个匹配：{names}，要打开哪个？",
                emotion="think",
            )

        try:
            size = path.stat().st_size
        except OSError as e:
            return ToolResult.fail(f"读不到这个文件：{e}", emotion="sad")

        if size > MAX_READ_BYTES:
            return ToolResult.fail(
                f"这个文件有 {_human_size(size)}，太大了，我读不完呢",
                emotion="sad",
            )

        suffix = path.suffix.lower()

        # 文本文件 → 返回内容
        if suffix in TEXT_EXTENSIONS:
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
            except OSError as e:
                return ToolResult.fail(f"读不到这个文件：{e}", emotion="sad")

            truncated = len(content) > MAX_READ_CHARS
            shown = content[:MAX_READ_CHARS] if truncated else content
            summary = f"「{path.name}」共 {len(content)} 字"
            if truncated:
                summary += f"，先给你看前 {MAX_READ_CHARS} 字"

            return ToolResult.ok(
                data={"name": path.name, "path": str(path), "content": shown},
                summary=summary,
                truncated=truncated,
                emotion="talk",
            )

        # 非文本 → 用默认程序打开
        try:
            os.startfile(str(path))  # noqa: S606 — Windows 默认程序打开
        except AttributeError:
            import subprocess
            subprocess.Popen(["xdg-open", str(path)])
        except OSError as e:
            return ToolResult.fail(f"打不开这个文件：{e}", emotion="sad")

        return ToolResult.ok(
            data=self._file_info(path),
            summary=f"已经帮你打开「{path.name}」啦",
            emotion="happy",
        )


# ══════════════════════════════════════════════════════
#  4. file_rename
# ══════════════════════════════════════════════════════

class FileRenameTool(_FileToolBase):
    """重命名文件（目标已存在时报错，不覆盖）"""

    name = "file_rename"
    description = "重命名文件；目标名已存在时不会覆盖"
    risk_level = "medium"
    params_schema = {
        "type": "object",
        "properties": {
            "source": {"type": "string", "description": "原文件名或路径"},
            "target": {"type": "string", "description": "新名称（不含路径）或完整新路径"},
        },
        "required": ["source", "target"],
    }

    def execute(self, params: Dict[str, Any]) -> ToolResult:
        source_raw = params.get("source")
        target_raw = params.get("target")

        if not target_raw or not str(target_raw).strip():
            return ToolResult.fail("你想把它改成什么名字呀？", emotion="think")

        paths, err = self._resolve_targets(source_raw)
        if err:
            return ToolResult.fail(err, emotion="sad")
        if not paths:
            return ToolResult.fail("没找到要改名的文件", emotion="sad")

        if len(paths) > 1:
            names = "、".join(p.name for p in paths[:3])
            return ToolResult.ok(
                data={"candidates": [self._file_info(p) for p in paths]},
                summary=f"找到 {len(paths)} 个匹配：{names}，要改哪个？",
                emotion="think",
            )

        src = paths[0]
        target_str = str(target_raw).strip()

        # 目标是完整路径还是纯文件名
        tp = Path(target_str).expanduser()
        if tp.is_absolute() or os.sep in target_str or "/" in target_str:
            try:
                dst = self._safe_path(target_str)
            except (PathNotAllowed, SecurityError) as e:
                return ToolResult.fail(getattr(e, "reason", str(e)), emotion="surprised")
        else:
            # 纯名称：只改文件名，保留原目录与扩展名（若新名无扩展名）
            name = target_str
            if not Path(name).suffix and src.suffix:
                name = name + src.suffix
            dst = src.parent / name

        if dst == src:
            return ToolResult.ok(
                data=self._file_info(src),
                summary=f"新名字和原来一样呀，就不用改啦",
                emotion="talk",
            )

        if self._exists(dst):
            return ToolResult.fail(
                f"已经有个叫「{dst.name}」的文件了，换个名字吧？",
                emotion="surprised",
            )

        # 再次校验目标路径（防止 parent 拼接后越界）
        try:
            self._guard.validate_path(str(dst))
        except (PathNotAllowed, SecurityError) as e:
            return ToolResult.fail(getattr(e, "reason", str(e)), emotion="surprised")

        try:
            src.rename(dst)
        except OSError as e:
            return ToolResult.fail(f"改名失败了：{e}", emotion="sad")

        self._guard.audit(
            self.name,
            {"source": str(src), "target": str(dst)},
            success=True,
        )

        return ToolResult.ok(
            data={"old": src.name, "new": dst.name, "path": str(dst)},
            summary=f"「{src.name}」已经改名叫「{dst.name}」啦",
            emotion="happy",
        )


# ══════════════════════════════════════════════════════
#  5. file_move
# ══════════════════════════════════════════════════════

class FileMoveTool(_FileToolBase):
    """移动文件到指定目录"""

    name = "file_move"
    description = "把文件移动到另一个常用目录"
    risk_level = "medium"
    params_schema = {
        "type": "object",
        "properties": {
            "source": {"type": "string", "description": "原文件名或路径"},
            "dest": {"type": "string", "description": "目标目录（Desktop/Documents/Downloads/Pictures）"},
        },
        "required": ["source", "dest"],
    }

    def execute(self, params: Dict[str, Any]) -> ToolResult:
        source_raw = params.get("source")
        dest_raw = params.get("dest")

        if not dest_raw or not str(dest_raw).strip():
            return ToolResult.fail("你想把它移到哪个文件夹呀？", emotion="think")

        paths, err = self._resolve_targets(source_raw)
        if err:
            return ToolResult.fail(err, emotion="sad")
        if not paths:
            return ToolResult.fail("没找到要移动的文件", emotion="sad")

        if len(paths) > 1:
            names = "、".join(p.name for p in paths[:3])
            return ToolResult.ok(
                data={"candidates": [self._file_info(p) for p in paths]},
                summary=f"找到 {len(paths)} 个匹配：{names}，要移哪个？",
                emotion="think",
            )

        src = paths[0]
        # 目标目录必须**解析得出来**。原先这里 `_resolve_dirs` 认不出就回退四目录，
        # 于是取 `dirs[0]`（Desktop）→ 文件被挪到桌面还报成功（P4-B2 实测）。
        # 现在认不出就明确拒绝，绝不替用户选一个目录。
        dirs, derr = self._dirs_or_error([str(dest_raw)])
        if derr:
            return ToolResult.fail(derr, emotion="think")
        if not dirs:
            return ToolResult.fail(f"没找到「{dest_raw}」这个文件夹呢", emotion="sad")

        dest_dir = dirs[0]
        dst = dest_dir / src.name

        if dst == src:
            return ToolResult.ok(
                data=self._file_info(src),
                summary=f"「{src.name}」本来就在{dest_dir.name}里呀",
                emotion="talk",
            )

        if self._exists(dst):
            return ToolResult.fail(
                f"{dest_dir.name} 里已经有个「{src.name}」了",
                emotion="surprised",
            )

        try:
            self._guard.validate_path(str(dst))
        except (PathNotAllowed, SecurityError) as e:
            return ToolResult.fail(getattr(e, "reason", str(e)), emotion="surprised")

        try:
            shutil.move(str(src), str(dst))
        except OSError as e:
            return ToolResult.fail(f"移动失败了：{e}", emotion="sad")

        self._guard.audit(
            self.name,
            {"source": str(src), "dest": str(dst)},
            success=True,
        )

        return ToolResult.ok(
            data={"name": src.name, "from": str(src), "to": str(dst)},
            summary=f"「{src.name}」已经移到 {dest_dir.name} 啦",
            emotion="happy",
        )


# ══════════════════════════════════════════════════════
#  6. file_delete（强制回收站）
# ══════════════════════════════════════════════════════

class FileDeleteTool(_FileToolBase):
    """删除文件到回收站

    安全契约：
    - permanent=True 直接抛 SecurityError（不存在永久删除分支）
    - 所有路径先经白名单校验
    - 单次删除数量上限由守卫的 MAX_BATCH_SIZE 控制
    """

    name = "file_delete"
    description = "把文件移到回收站（可从回收站恢复）"
    risk_level = "high"
    params_schema = {
        "type": "object",
        "properties": {
            "targets": {
                "type": "array",
                "items": {"type": "string"},
                "description": "要删除的文件路径列表",
            },
            "pattern": {"type": "string", "description": "按模式匹配（配合 dirs 使用）"},
            "dirs": {
                "type": "array",
                "items": {"type": "string"},
                "description": "在哪些目录中匹配",
            },
        },
    }

    def _collect_targets(self, params: Dict[str, Any]) -> List[str]:
        """解析出本次要删除的原始目标列表（不产生副作用）

        三种来源（优先级从高到低）：
        1. targets：显式路径列表
        2. pattern（+dirs）：按模式匹配
        3. target：单个名称/路径

        **列表参数收到字符串时整条忽略、绝不拆开**（P4-B2）：
        `targets="C:\\a.txt"` 不会被拆成 `["C", ":", "\\", ...]`，也不会被当成
        `["C:\\a.txt"]` —— 「把一句话拆成多个待删目标」是最危险的宽松转换，
        宁可什么都不删（并让 `validate_params` 报类型错）也不猜。
        实测 `_collect_targets({"targets": "C:\\x\\a.txt"}) == []`。

        Raises:
            ToolError: `dirs` 无法解析时（用户能看懂的说法放在 `user_message`）
        """
        raw: List[str] = []

        explicit = params.get("targets")
        if isinstance(explicit, list):
            raw.extend([str(t) for t in explicit if t])

        pattern = params.get("pattern")
        if pattern:
            dirs, derr = self._dirs_or_error(params.get("dirs"))
            if derr:
                # 无法确定搜索范围时**绝不能**默认四目录后照常删 —— 那会把
                # "我没听懂你要在哪儿删"变成"我把四个目录里的都删了"。
                raise ToolError(self.name, derr, user_message=derr)
            matched, _ = self._scan(dirs, pattern=str(pattern), limit=MAX_SEARCH_RESULTS)
            raw.extend([m["path"] for m in matched])

        single = params.get("target")
        if single and not raw:
            resolved, _err = self._resolve_targets(single)
            raw.extend([str(p) for p in resolved])

        # 去重保序
        seen = set()
        unique: List[str] = []
        for t in raw:
            if t not in seen:
                seen.add(t)
                unique.append(t)
        return unique

    def preview(self, params: Dict[str, Any]) -> str:
        """删除预览：报告匹配到的文件数量与名称（只读，不产生副作用）"""
        if params.get("permanent"):
            return ""

        try:
            raw = self._collect_targets(params)
        except Exception as e:
            logger.debug("[file_delete] 预览失败: %s", e)
            return ""

        if not raw:
            return ""

        existing: List[Path] = []
        for r in raw:
            try:
                if not self._looks_like_explicit_path(r):
                    continue  # 裸名字由工具在白名单内解析，预览阶段不猜
                p = self._guard.validate_path(r)
                if self._exists(p):
                    existing.append(p)
            except Exception:
                continue

        # 模式匹配来源的目标已是绝对路径，直接用
        if not existing:
            existing = [Path(r) for r in raw if self._exists(r)]

        if not existing:
            return ""

        names = "、".join(p.name for p in existing[:3])
        if len(existing) > 3:
            names += f" 等 {len(existing)} 个"
        total_bytes = 0
        for p in existing:
            try:
                total_bytes += p.stat().st_size
            except OSError:
                pass
        size_text = f"，共 {_human_size(total_bytes)}" if total_bytes else ""
        return f"找到 {len(existing)} 个文件（{names}{size_text}）"

    @staticmethod
    def _looks_like_explicit_path(raw: str) -> bool:
        """是否为需要白名单校验的路径形式"""
        s = str(raw)
        if s.startswith("~"):
            return True
        try:
            if os.path.isabs(s):
                return True
        except Exception:
            pass
        return "/" in s or "\\" in s

    def execute(self, params: Dict[str, Any]) -> ToolResult:
        # ── 硬约束：不接受永久删除 ──
        if params.get("permanent"):
            raise SecurityError("permanent_delete", "永久删除已禁用，只能移到回收站")

        try:
            raw_targets = self._collect_targets(params)
        except ToolError as e:
            # `user_message` 才是念给用户听的那句（技术原文已被注册表留在 error 里）
            return ToolResult.fail(e.user_message, emotion="think")

        if not raw_targets:
            return ToolResult.fail("你没说要删哪些文件呀", emotion="think")

        # ── 路径校验（白名单 + 批量上限）──
        try:
            safe_paths = self._guard.validate_paths(raw_targets)
        except PathNotAllowed as e:
            return ToolResult.fail(e.reason, emotion="surprised")
        except SecurityError as e:
            return ToolResult.fail(e.reason, emotion="surprised")

        existing = [p for p in safe_paths if self._exists(p)]
        missing = len(safe_paths) - len(existing)

        if not existing:
            return ToolResult.fail("这些文件都不在了呢", emotion="sad")

        # ── 执行：只走回收站 ──
        from send2trash import send2trash

        deleted: List[Dict[str, Any]] = []
        failed: List[str] = []

        for p in existing:
            try:
                info = self._file_info(p)
                send2trash(str(p))
                deleted.append(info)
            except Exception as e:
                logger.warning("[file_delete] 删除失败 %s: %s", p, e)
                failed.append(p.name)

        self._guard.audit(
            self.name,
            {"targets": [str(p) for p in existing]},
            success=bool(deleted),
            detail=f"成功 {len(deleted)}，失败 {len(failed)}",
        )

        if not deleted:
            return ToolResult.fail("删除失败了，可能是权限不够呢", emotion="sad")

        summary = f"已经把 {len(deleted)} 个文件移到回收站啦"
        if missing:
            summary += f"（{missing} 个没找到）"
        if failed:
            summary += f"，{len(failed)} 个没删成功"

        return ToolResult.ok(
            data={"deleted": deleted, "failed": failed, "missing": missing},
            summary=summary,
            count=len(deleted),
            emotion="happy" if not failed else "talk",
        )


# ══════════════════════════════════════════════════════
#  注册辅助
# ══════════════════════════════════════════════════════

def all_file_tools(guard: SafetyService) -> List[BaseTool]:
    """构造全部文件工具实例

    Args:
        guard: 安全守卫

    Returns:
        工具实例列表（顺序即推荐注册顺序）
    """
    return [
        FileSearchTool(guard),
        FileListTool(guard),
        FileReadTool(guard),
        FileRenameTool(guard),
        FileMoveTool(guard),
        FileDeleteTool(guard),
    ]
