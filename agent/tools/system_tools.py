"""系统工具集

五个系统级工具：
- system_info  查看 CPU / 内存 / 电量 / 磁盘
- clipboard    读写剪贴板
- open_app     用默认程序打开文件/文件夹/应用
- screenshot   截取屏幕并保存
- run_command  执行系统命令（高风险，需确认 + 危险模式拦截）

线程约束：这些工具运行在执行器线程池，**不得触碰 Qt 对象**。
剪贴板与截图走 Win32 API（ctypes），避免跨线程使用 Qt GUI 资源。
"""

import ctypes
import logging
import os
import subprocess
import time
from ctypes import wintypes
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent.seams.safety import SafetyService, SecurityError
from agent.tools.base import BaseTool, ToolResult

logger = logging.getLogger(__name__)

#: 命令危险模式（命中即拒绝执行，不可通过确认放行）
DANGEROUS_PATTERNS = [
    "format ", "format.com", "diskpart", "mkfs",
    "rm -rf /", "rm -rf /*", "del /f /s /q c:\\", "del /s /q c:\\",
    "rd /s /q c:\\", "rmdir /s /q c:\\",
    "reg delete hklm", "reg delete hkcr",
    "shutdown /s", "shutdown -s", "shutdown /r",
    "bcdedit", "bootrec", "cipher /w",
    "> /dev/sda", "dd if=/dev/zero",
    "vssadmin delete", "wevtutil cl",
]

#: 允许执行的最大命令长度
MAX_COMMAND_LEN = 500

#: 截图保存目录（相对白名单根）
SCREENSHOT_DIRNAME = "Screenshots"


def _screenshot_dir(guard: SafetyService) -> Path:
    """截图保存目录：优先用白名单里的图片目录，其次桌面"""
    roots = {p.name.lower(): p for p in guard.whitelist_roots()}
    base = roots.get("pictures") or roots.get("desktop")
    if base is None:
        base = Path.home()
    target = base / SCREENSHOT_DIRNAME
    return target


# ══════════════════════════════════════════════════════
#  1. system_info
# ══════════════════════════════════════════════════════

class SystemInfoTool(BaseTool):
    """查询系统状态（CPU / 内存 / 电量 / 磁盘）"""

    name = "system_info"
    description = "查询电脑状态：CPU 占用、内存占用、电量、磁盘占用"
    risk_level = "low"
    timeout = 10
    params_schema = {
        "type": "object",
        "properties": {
            "metric": {
                "type": "string",
                "enum": ["all", "cpu", "memory", "battery", "disk"],
                "description": "要查询的指标，默认 all",
            },
        },
    }

    #: 指标别名 → 规范名（含中文口语）
    METRIC_ALIASES = {
        "all": "all", "全部": "all", "所有": "all", "整体": "all", "状态": "all",
        "cpu": "cpu", "处理器": "cpu", "中央处理器": "cpu",
        "memory": "memory", "内存": "memory", "运存": "memory",
        "battery": "battery", "电量": "battery", "电池": "battery",
        "disk": "disk", "磁盘": "disk", "硬盘": "disk", "存储": "disk",
    }

    def execute(self, params: Dict[str, Any]) -> ToolResult:
        raw = str(params.get("metric") or "all").strip().lower()
        # 未识别的指标回退为 all（而不是什么都不查）
        metric = self.METRIC_ALIASES.get(raw, "all")

        try:
            import psutil
        except ImportError:
            return ToolResult.fail("我这边缺少查询系统状态的能力呢", emotion="sad")

        data: Dict[str, Any] = {}
        parts: List[str] = []

        def want(name: str) -> bool:
            return metric in ("all", name)

        if want("cpu"):
            try:
                pct = psutil.cpu_percent(interval=0.1)
                data["cpu_percent"] = round(pct, 1)
                data["cpu_count"] = psutil.cpu_count()
                parts.append(f"CPU 用了 {pct:.0f}%")
            except Exception as e:
                logger.debug("[system_info] CPU 读取失败: %s", e)

        if want("memory"):
            try:
                vm = psutil.virtual_memory()
                data["memory_percent"] = round(vm.percent, 1)
                data["memory_total_gb"] = round(vm.total / 1024 ** 3, 1)
                data["memory_used_gb"] = round(vm.used / 1024 ** 3, 1)
                parts.append(f"内存用了 {vm.percent:.0f}%")
            except Exception as e:
                logger.debug("[system_info] 内存读取失败: %s", e)

        if want("battery"):
            try:
                bat = psutil.sensors_battery()
                if bat is not None:
                    data["battery_percent"] = round(bat.percent)
                    data["battery_plugged"] = bool(bat.power_plugged)
                    state = "充电中" if bat.power_plugged else "用电池"
                    parts.append(f"电量 {bat.percent:.0f}%（{state}）")
            except Exception as e:
                logger.debug("[system_info] 电量读取失败: %s", e)

        if want("disk"):
            try:
                usage = psutil.disk_usage(str(Path.home().anchor or "/"))
                data["disk_percent"] = round(usage.percent, 1)
                data["disk_free_gb"] = round(usage.free / 1024 ** 3, 1)
                parts.append(f"磁盘用了 {usage.percent:.0f}%")
            except Exception as e:
                logger.debug("[system_info] 磁盘读取失败: %s", e)

        if not parts:
            return ToolResult.fail("这次没读到系统信息呢", emotion="sad")

        return ToolResult.ok(
            data=data,
            summary="，".join(parts),
            count=len(data),
            emotion="talk",
        )


# ══════════════════════════════════════════════════════
#  2. clipboard
# ══════════════════════════════════════════════════════

class ClipboardTool(BaseTool):
    """读写系统剪贴板（Win32 API，线程安全）"""

    name = "clipboard"
    description = "读取或写入剪贴板文本"
    risk_level = "low"
    timeout = 10
    params_schema = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["get", "set"], "description": "读取或写入"},
            "text": {"type": "string", "description": "action=set 时要写入的文本"},
        },
        "required": ["action"],
    }

    #: CF_UNICODETEXT
    CF_UNICODETEXT = 13
    GMEM_MOVEABLE = 0x0002

    #: 已配置原型的 win32 模块缓存（避免每次调用重复设置）
    _win32_cache: Optional[tuple] = None

    @classmethod
    def _win32(cls) -> tuple:
        """取得已正确声明原型的 (user32, kernel32)

        必须显式设置 restype/argtypes：ctypes 默认把返回值当 c_int，
        在 64 位下会把 HANDLE/指针截断成 32 位，随后解引用即访问违例。
        """
        if cls._win32_cache is not None:
            return cls._win32_cache

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        # ── user32 ──
        user32.OpenClipboard.argtypes = [wintypes.HWND]
        user32.OpenClipboard.restype = wintypes.BOOL
        user32.CloseClipboard.argtypes = []
        user32.CloseClipboard.restype = wintypes.BOOL
        user32.EmptyClipboard.argtypes = []
        user32.EmptyClipboard.restype = wintypes.BOOL
        user32.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
        user32.IsClipboardFormatAvailable.restype = wintypes.BOOL
        user32.GetClipboardData.argtypes = [wintypes.UINT]
        user32.GetClipboardData.restype = ctypes.c_void_p      # ← 关键
        user32.SetClipboardData.argtypes = [wintypes.UINT, ctypes.c_void_p]
        user32.SetClipboardData.restype = ctypes.c_void_p

        # ── kernel32 ──
        kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
        kernel32.GlobalAlloc.restype = ctypes.c_void_p        # ← 关键
        kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
        kernel32.GlobalLock.restype = ctypes.c_void_p         # ← 关键
        kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
        kernel32.GlobalUnlock.restype = wintypes.BOOL
        kernel32.GlobalFree.argtypes = [ctypes.c_void_p]
        kernel32.GlobalFree.restype = ctypes.c_void_p

        cls._win32_cache = (user32, kernel32)
        return cls._win32_cache

    def execute(self, params: Dict[str, Any]) -> ToolResult:
        action = str(params.get("action") or "get").lower()

        if os.name != "nt":
            return ToolResult.fail("剪贴板功能目前只支持 Windows 呢", emotion="sad")

        if action == "get":
            return self._get()
        if action == "set":
            return self._set(str(params.get("text") or ""))
        return ToolResult.fail(f"不认识的剪贴板操作：{action}", emotion="think")

    # ── 读 ──

    def _get(self) -> ToolResult:
        try:
            user32, kernel32 = self._win32()

            if not user32.OpenClipboard(None):
                return ToolResult.fail("剪贴板被别的程序占着呢，稍后再试~", emotion="sad")
            try:
                if not user32.IsClipboardFormatAvailable(self.CF_UNICODETEXT):
                    return ToolResult.ok(data={"text": ""}, summary="剪贴板现在是空的呢")

                handle = user32.GetClipboardData(self.CF_UNICODETEXT)
                if not handle:
                    return ToolResult.fail("没读到剪贴板内容呢", emotion="sad")

                ptr = kernel32.GlobalLock(handle)
                if not ptr:
                    return ToolResult.fail("没读到剪贴板内容呢", emotion="sad")
                try:
                    text = ctypes.c_wchar_p(ptr).value or ""
                finally:
                    kernel32.GlobalUnlock(handle)
            finally:
                user32.CloseClipboard()

            preview = text.strip().replace("\n", " ")[:40]
            return ToolResult.ok(
                data={"text": text},
                summary=f"剪贴板里是：{preview}" if preview else "剪贴板是空的呢",
                emotion="talk",
            )
        except Exception as e:
            logger.warning("[clipboard] 读取失败: %s", e)
            return ToolResult.fail("读剪贴板出错了呢", emotion="sad")

    # ── 写 ──

    def _set(self, text: str) -> ToolResult:
        if not text:
            return ToolResult.fail("你想复制什么内容呀？", emotion="think")

        try:
            user32, kernel32 = self._win32()

            buf_size = (len(text) + 1) * ctypes.sizeof(ctypes.c_wchar)
            handle = kernel32.GlobalAlloc(self.GMEM_MOVEABLE, buf_size)
            if not handle:
                return ToolResult.fail("复制失败了，内存不够呢", emotion="sad")

            ptr = kernel32.GlobalLock(handle)
            if not ptr:
                kernel32.GlobalFree(handle)
                return ToolResult.fail("复制失败了", emotion="sad")
            try:
                ctypes.memmove(ptr, ctypes.create_unicode_buffer(text), buf_size)
            finally:
                kernel32.GlobalUnlock(handle)

            if not user32.OpenClipboard(None):
                kernel32.GlobalFree(handle)
                return ToolResult.fail("剪贴板被占着呢，稍后再试~", emotion="sad")
            try:
                user32.EmptyClipboard()
                if not user32.SetClipboardData(self.CF_UNICODETEXT, handle):
                    kernel32.GlobalFree(handle)
                    return ToolResult.fail("复制失败了", emotion="sad")
                # SetClipboardData 成功后所有权移交系统，不能再 Free
            finally:
                user32.CloseClipboard()

            preview = text.strip().replace("\n", " ")[:30]
            return ToolResult.ok(
                data={"text": text},
                summary=f"已经复制好啦：{preview}",
                emotion="happy",
            )
        except Exception as e:
            logger.warning("[clipboard] 写入失败: %s", e)
            return ToolResult.fail("复制出错了呢", emotion="sad")


# ══════════════════════════════════════════════════════
#  3. open_app
# ══════════════════════════════════════════════════════

class OpenAppTool(BaseTool):
    """用系统默认程序打开文件、文件夹或应用"""

    name = "open_app"
    description = "用默认程序打开一个文件、文件夹或已安装的应用"
    risk_level = "medium"
    timeout = 15
    params_schema = {
        "type": "object",
        "properties": {
            "target": {"type": "string", "description": "文件路径、文件夹路径或应用名"},
        },
        "required": ["target"],
    }

    #: 允许直接启动的常用应用（避免任意命令注入）
    KNOWN_APPS = {
        "记事本": "notepad.exe", "notepad": "notepad.exe",
        "计算器": "calc.exe", "calc": "calc.exe", "calculator": "calc.exe",
        "画图": "mspaint.exe", "mspaint": "mspaint.exe", "paint": "mspaint.exe",
        "任务管理器": "taskmgr.exe", "taskmgr": "taskmgr.exe",
        "资源管理器": "explorer.exe", "explorer": "explorer.exe",
        "命令提示符": "cmd.exe", "cmd": "cmd.exe",
        "浏览器": "explorer.exe https://www.bing.com",
    }

    def __init__(self, guard: SafetyService) -> None:
        self._guard = guard

    def execute(self, params: Dict[str, Any]) -> ToolResult:
        target = str(params.get("target") or "").strip()
        if not target:
            return ToolResult.fail("你想打开什么呀？", emotion="think")

        # 1. 已知应用名
        app = self.KNOWN_APPS.get(target.lower())
        if app:
            return self._spawn(app, target)

        # 2. 路径形式 → 必须经白名单校验
        try:
            path = Path(target).expanduser()
            if path.is_absolute() or os.sep in target or "/" in target:
                safe = self._guard.validate_path(str(path))
                if not safe.exists():
                    return ToolResult.fail(f"没找到「{safe.name}」呢", emotion="sad")
                return self._open_path(safe)
        except (SecurityError,) as e:
            return ToolResult.fail(getattr(e, "reason", str(e)), emotion="surprised")
        except Exception as e:
            logger.debug("[open_app] 路径解析失败: %s", e)

        # 3. 白名单目录内的裸文件名 → 让用户用 file_read 更合适
        return ToolResult.fail(
            f"我没找到叫「{target}」的应用呢，试试说完整路径或者文件名？",
            emotion="think",
        )

    def _open_path(self, path: Path) -> ToolResult:
        try:
            os.startfile(str(path))  # noqa: S606 — Windows 默认程序打开
        except AttributeError:
            subprocess.Popen(["xdg-open", str(path)])
        except OSError as e:
            return ToolResult.fail(f"打不开这个了：{e}", emotion="sad")

        kind = "文件夹" if path.is_dir() else "文件"
        return ToolResult.ok(
            data={"target": str(path), "name": path.name},
            summary=f"已经帮你打开{kind}「{path.name}」啦",
            emotion="happy",
        )

    def _spawn(self, command: str, display: str) -> ToolResult:
        try:
            subprocess.Popen(command, shell=False)
        except FileNotFoundError:
            return ToolResult.fail(f"这台电脑上好像没装「{display}」呢", emotion="sad")
        except OSError as e:
            return ToolResult.fail(f"启动失败了：{e}", emotion="sad")

        return ToolResult.ok(
            data={"target": command, "name": display},
            summary=f"已经帮你打开「{display}」啦",
            emotion="happy",
        )


# ══════════════════════════════════════════════════════
#  4. screenshot
# ══════════════════════════════════════════════════════

class ScreenshotTool(BaseTool):
    """截取屏幕并保存到图片目录

    用 Win32 GDI 实现（ctypes），避免在非 UI 线程使用 Qt GUI 资源。
    """

    name = "screenshot"
    description = "截取当前屏幕并保存为图片"
    risk_level = "medium"
    timeout = 20
    params_schema = {
        "type": "object",
        "properties": {
            "filename": {"type": "string", "description": "保存的文件名（可省略）"},
        },
    }

    SRCCOPY = 0x00CC0020
    DIB_RGB_COLORS = 0

    def __init__(self, guard: SafetyService) -> None:
        self._guard = guard

    def execute(self, params: Dict[str, Any]) -> ToolResult:
        if os.name != "nt":
            return ToolResult.fail("截图功能目前只支持 Windows 呢", emotion="sad")

        try:
            out_dir = _screenshot_dir(self._guard)
            out_dir.mkdir(parents=True, exist_ok=True)
            # 目录本身也要过白名单（Pictures/Desktop 已在白名单内）
            self._guard.validate_path(str(out_dir))

            name = str(params.get("filename") or "").strip()
            if not name:
                name = f"截图_{time.strftime('%Y%m%d_%H%M%S')}.png"
            if not name.lower().endswith((".png", ".jpg", ".jpeg", ".bmp")):
                name += ".png"
            # 只取文件名，防止通过 filename 指定任意路径
            target = out_dir / Path(name).name
            self._guard.validate_path(str(target))

            self._capture(str(target))
        except SecurityError as e:
            return ToolResult.fail(getattr(e, "reason", str(e)), emotion="surprised")
        except Exception as e:
            logger.warning("[screenshot] 截图失败: %s", e)
            return ToolResult.fail(f"截图没成功呢：{e}", emotion="sad")

        try:
            size = target.stat().st_size
        except OSError:
            size = 0

        return ToolResult.ok(
            data={"path": str(target), "name": target.name, "size": size},
            summary=f"截图已经存好啦：{target.name}",
            emotion="happy",
        )

    def _capture(self, out_path: str) -> None:
        """用 GDI 抓屏并写成 PNG（经 PIL 编码）"""
        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32

        width = user32.GetSystemMetrics(0)    # SM_CXSCREEN
        height = user32.GetSystemMetrics(1)   # SM_CYSCREEN
        if width <= 0 or height <= 0:
            raise RuntimeError("拿不到屏幕尺寸")

        hdc_screen = user32.GetDC(0)
        if not hdc_screen:
            raise RuntimeError("取不到屏幕句柄")
        try:
            hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
            hbitmap = gdi32.CreateCompatibleBitmap(hdc_screen, width, height)
            if not hdc_mem or not hbitmap:
                raise RuntimeError("创建绘图缓冲失败")
            try:
                gdi32.SelectObject(hdc_mem, hbitmap)
                if not gdi32.BitBlt(hdc_mem, 0, 0, width, height,
                                    hdc_screen, 0, 0, self.SRCCOPY):
                    raise RuntimeError("抓屏失败")

                # 取出位图数据 → 交给 PIL 编码
                buffer_size = width * height * 4

                class BITMAPINFOHEADER(ctypes.Structure):
                    _fields_ = [
                        ("biSize", wintypes.DWORD),
                        ("biWidth", wintypes.LONG),
                        ("biHeight", wintypes.LONG),
                        ("biPlanes", wintypes.WORD),
                        ("biBitCount", wintypes.WORD),
                        ("biCompression", wintypes.DWORD),
                        ("biSizeImage", wintypes.DWORD),
                        ("biXPelsPerMeter", wintypes.LONG),
                        ("biYPelsPerMeter", wintypes.LONG),
                        ("biClrUsed", wintypes.DWORD),
                        ("biClrImportant", wintypes.DWORD),
                    ]

                class BITMAPINFO(ctypes.Structure):
                    _fields_ = [("bmiHeader", BITMAPINFOHEADER),
                                ("bmiColors", wintypes.DWORD * 3)]

                bmi = BITMAPINFO()
                bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
                bmi.bmiHeader.biWidth = width
                bmi.bmiHeader.biHeight = -height      # 负数 = 自上而下
                bmi.bmiHeader.biPlanes = 1
                bmi.bmiHeader.biBitCount = 32
                bmi.bmiHeader.biCompression = 0        # BI_RGB

                buf = ctypes.create_string_buffer(buffer_size)
                gdi32.GetDIBits(hdc_mem, hbitmap, 0, height, buf,
                                ctypes.byref(bmi), self.DIB_RGB_COLORS)

                self._encode(buf.raw, width, height, out_path)
            finally:
                gdi32.DeleteObject(hbitmap)
                gdi32.DeleteDC(hdc_mem)
        finally:
            user32.ReleaseDC(0, hdc_screen)

    @staticmethod
    def _encode(raw: bytes, width: int, height: int, out_path: str) -> None:
        """把 BGRA 原始数据编码为 PNG"""
        try:
            from PIL import Image
        except ImportError as e:
            raise RuntimeError("需要 Pillow 才能保存截图") from e

        img = Image.frombuffer("RGBA", (width, height), raw, "raw", "BGRA", 0, 1)
        img.convert("RGB").save(out_path, "PNG")


# ══════════════════════════════════════════════════════
#  5. run_command
# ══════════════════════════════════════════════════════

class RunCommandTool(BaseTool):
    """执行系统命令（高风险）

    两道关卡：
    1. 危险模式黑名单（不可通过确认放行）
    2. 高风险确认（由安全守卫在管线层处理）
    """

    name = "run_command"
    description = "执行一条系统命令并返回输出"
    risk_level = "high"
    timeout = 30
    params_schema = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "要执行的命令"},
            "cwd": {"type": "string", "description": "工作目录（可选，需在白名单内）"},
        },
        "required": ["command"],
    }

    def __init__(self, guard: SafetyService, max_output: int = 4000) -> None:
        self._guard = guard
        self._max_output = max(200, max_output)

    def execute(self, params: Dict[str, Any]) -> ToolResult:
        command = str(params.get("command") or "").strip()
        if not command:
            return ToolResult.fail("你想执行什么命令呀？", emotion="think")
        if len(command) > MAX_COMMAND_LEN:
            return ToolResult.fail("命令太长了，我不敢执行呢", emotion="surprised")

        # 第 1 关：危险模式（硬拒绝）
        lowered = command.lower()
        for pattern in DANGEROUS_PATTERNS:
            if pattern in lowered:
                raise SecurityError(
                    "run_command",
                    f"「{pattern}」这种命令太危险了，我不能执行哦",
                )

        # 工作目录必须在白名单内
        cwd: Optional[str] = None
        if params.get("cwd"):
            try:
                cwd = str(self._guard.validate_path(str(params["cwd"])))
            except SecurityError as e:
                return ToolResult.fail(getattr(e, "reason", str(e)), emotion="surprised")

        try:
            proc = subprocess.run(
                command,
                shell=True,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                encoding="utf-8",
                errors="replace",
            )
        except subprocess.TimeoutExpired:
            return ToolResult.fail(
                f"这条命令跑了超过 {self.timeout} 秒，我先停下了", emotion="sad"
            )
        except Exception as e:
            logger.warning("[run_command] 执行失败: %s", e)
            return ToolResult.fail(f"命令没跑起来呢：{e}", emotion="sad")

        stdout = (proc.stdout or "").strip()
        stderr = (proc.stderr or "").strip()
        truncated = len(stdout) > self._max_output
        if truncated:
            stdout = stdout[: self._max_output] + "…"

        self._guard.audit(
            self.name,
            {"command": command},
            success=(proc.returncode == 0),
            detail=f"exit={proc.returncode}",
        )

        if proc.returncode != 0 and not stdout:
            # 即使失败也带上 data，便于调用方检查退出码/错误输出
            return ToolResult.fail(
                stderr[:200] or f"命令返回了错误码 {proc.returncode}",
                emotion="sad",
                data={
                    "returncode": proc.returncode,
                    "stdout": stdout,
                    "stderr": stderr[: self._max_output],
                },
            )

        preview = (stdout or stderr).replace("\n", " ")[:60]
        return ToolResult.ok(
            data={
                "returncode": proc.returncode,
                "stdout": stdout,
                "stderr": stderr[:self._max_output],
            },
            summary=f"命令执行完了（退出码 {proc.returncode}）：{preview}" if preview
                    else f"命令执行完了（退出码 {proc.returncode}）",
            truncated=truncated,
            emotion="happy" if proc.returncode == 0 else "talk",
        )


# ══════════════════════════════════════════════════════
#  注册辅助
# ══════════════════════════════════════════════════════

def all_system_tools(guard: SafetyService) -> List[BaseTool]:
    """构造全部系统工具实例"""
    return [
        SystemInfoTool(),
        ClipboardTool(),
        OpenAppTool(guard),
        ScreenshotTool(guard),
        RunCommandTool(guard),
    ]
