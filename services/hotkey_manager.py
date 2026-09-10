# -*- coding: utf-8 -*-
"""全局热键管理：Windows RegisterHotKey + Qt 原生事件过滤器"""
import ctypes
from ctypes import wintypes
import logging
from typing import Optional, Callable

from PySide6.QtCore import QAbstractNativeEventFilter, Qt

logger = logging.getLogger(__name__)

MOD_ALT = 0x1
MOD_CONTROL = 0x2
MOD_SHIFT = 0x4
MOD_WIN = 0x8
WM_HOTKEY = 0x0312
# 使用随机 ID 避免与其他应用冲突（含自身历史进程残留）
HOTKEY_ID = 0x8001

SPECIAL_KEYS = {
    "F1": 0x70, "F2": 0x71, "F3": 0x72, "F4": 0x73, "F5": 0x74,
    "F6": 0x75, "F7": 0x76, "F8": 0x77, "F9": 0x78, "F10": 0x79,
    "F11": 0x7A, "F12": 0x7B,
    "ESC": 0x1B, "SPACE": 0x20,
}

# Qt key → Windows vk 映射（供 parse_key_event 用）
_QT_KEY_TO_VK = {
    Qt.Key_F1: 0x70, Qt.Key_F2: 0x71, Qt.Key_F3: 0x72, Qt.Key_F4: 0x73,
    Qt.Key_F5: 0x74, Qt.Key_F6: 0x75, Qt.Key_F7: 0x76, Qt.Key_F8: 0x77,
    Qt.Key_F9: 0x78, Qt.Key_F10: 0x79, Qt.Key_F11: 0x7A, Qt.Key_F12: 0x7B,
    Qt.Key_Escape: 0x1B, Qt.Key_Space: 0x20,
}
# Qt 修饰键 → (Windows MOD 标志, 显示名)
_QT_MOD_INFO = {
    Qt.ControlModifier: (MOD_CONTROL, "Ctrl"),
    Qt.AltModifier: (MOD_ALT, "Alt"),
    Qt.ShiftModifier: (MOD_SHIFT, "Shift"),
    Qt.MetaModifier: (MOD_WIN, "Win"),
}


def _resolve_key(key: int) -> Optional[tuple]:
    """Qt key → (vkcode, 显示文本)。支持功能键、字母、数字、常见符号。失败返回None。

    思路：Qt 对可打印字符（字母/数字/符号）的 key 值通常等于其 ASCII 码
    （如 '\\'=0x5C, '0'=0x30）；据此把 [0x20, 0x7E] 范围内的键当作可打印字符处理，
    从而自由设置特殊符号（\\ / 数字 等）。
    """
    # 功能键映射
    if key in _QT_KEY_TO_VK:
        vk = _QT_KEY_TO_VK[key]
        name = {v: k for k, v in SPECIAL_KEYS.items()}[vk]
        return (vk, name)
    # 可打印 ASCII 字符（字母/数字/符号）
    if 0x20 <= int(key) <= 0x7E:
        ch = chr(int(key))
        # 单独空格不显示为可见键（用 SPACE）
        if ch == " ":
            return (0x20, "SPACE")
        return (ord(ch), ch)
    # 其他特殊键（方向键等）暂不支持
    return None


def parse_key_event(key: int, modifiers) -> Optional[str]:
    """Qt key + modifiers → "Ctrl+\\\\" / "F5" / "0" 等文本；无可接受键→None

    规则宽松：支持单键（字母/数字/符号/功能键）与组合键，自由设置。
    """
    # 排除纯修饰键按下
    if key in (Qt.Key_Control, Qt.Key_Alt, Qt.Key_Shift, Qt.Key_Meta):
        return None
    names = []
    for qt_mod, (_flag, name) in _QT_MOD_INFO.items():
        if modifiers & qt_mod:
            names.append(name)
    resolved = _resolve_key(int(key))
    if resolved is None:
        return None
    vk, key_name = resolved
    combo = "+".join(names + [key_name]) if names else key_name
    return combo


def _load_user32():
    try:
        return ctypes.windll.user32
    except AttributeError:
        return None


user32 = _load_user32()


class HotkeyManager(QAbstractNativeEventFilter):
    """全局热键管理器（单一实例，由App持有）"""

    def __init__(self):
        super().__init__()
        self._registered = False
        self._combo = ""
        self._callback: Optional[Callable[[], None]] = None
        # 支持多热键：hotkey_id -> (combo, callback)。主热键兼容旧的 register/set_on_hotkey。
        self._multi: dict = {}
        self._multi_next_id = 0x8010

    def register_extra(self, combo: str, callback: Callable[[], None]) -> bool:
        """额外注册一个热键并绑定回调（每个热键独立 ID）。用于桌宠静音等附加功能。"""
        parsed = HotkeyManager.parse_combo(combo)
        if parsed is None or user32 is None:
            logger.warning(f"无效热键组合或非Windows: '{combo}'")
            return False
        mods, vk = parsed
        hotkey_id = self._multi_next_id
        self._multi_next_id += 1
        logger.info(f"[热键] RegisterHotKey(extra): id=0x{hotkey_id:04X}, mods={mods}, vk=0x{vk:04X}, combo='{combo}'")
        try:
            if not user32.RegisterHotKey(None, hotkey_id, mods, vk):
                logger.warning(f"额外热键注册失败（可能被占用）: '{combo}' (mods={mods}, vk=0x{vk:04X})")
                return False
        except Exception as e:
            logger.error(f"RegisterHotKey异常: {e}")
            return False
        self._multi[hotkey_id] = (combo, callback)
        logger.info(f"额外全局热键已注册: '{combo}' → id=0x{hotkey_id:04X}")
        return True

    @staticmethod
    def parse_combo(combo: str) -> Optional[tuple]:
        """'Ctrl+Alt+M' / 'Ctrl+\\\\' / '0' → (modifiers, vkcode)；无效返回None

        注意：反斜杠在YAML/JSON中可能被转义为 '\\\\'，这里统一处理。
        """
        combo = (combo or "").strip()
        if not combo:
            return None
        mods = 0
        # 先处理反斜杠转义：YAML可能存为 '\\' 或 '\\\\'，统一还原为单个 '\'
        # split("+") 前先替换，避免反斜杠干扰
        normalized = combo.replace("\\\\", "\x00BACKSLASH\x00")  # 临时占位
        parts = [p.strip().upper() for p in normalized.split("+")]
        # 还原占位符
        parts = [p.replace("\x00BACKSLASH\x00", "\\") for p in parts]
        key = parts[-1]
        for p in parts[:-1]:
            if p in ("CTRL", "CONTROL"):
                mods |= MOD_CONTROL
            elif p == "ALT":
                mods |= MOD_ALT
            elif p == "SHIFT":
                mods |= MOD_SHIFT
            elif p in ("WIN", "META"):
                mods |= MOD_WIN
            else:
                logger.warning(f"[热键] 未知修饰键: '{p}' (原始combo='{combo}')")
                return None
        if key in SPECIAL_KEYS:
            return (mods, SPECIAL_KEYS[key])
        if key == "SPACE":
            return (mods, 0x20)
        # 单个可打印 ASCII 字符（字母/数字/符号/反斜杠）→ 用其 ASCII 码作 vk
        if len(key) == 1 and 0x20 <= ord(key) <= 0x7E:
            logger.info(f"[热键] 解析combo='{combo}' → mods={mods}, vk=0x{ord(key):04X} ('{key}')")
            return (mods, ord(key))
        logger.warning(f"[热键] 无法解析combo中的键: '{key}' (原始='{combo}')")
        return None

    @staticmethod
    def combo_to_text(mod_vk: tuple) -> str:
        mods, vk = mod_vk
        parts = []
        if mods & MOD_CONTROL:
            parts.append("Ctrl")
        if mods & MOD_ALT:
            parts.append("Alt")
        if mods & MOD_SHIFT:
            parts.append("Shift")
        if mods & MOD_WIN:
            parts.append("Win")
        key_map = {v: k for k, v in SPECIAL_KEYS.items()}
        if vk in key_map:
            parts.append(key_map[vk])
        elif 0x20 <= vk <= 0x7E:
            # 可打印字符（含符号/数字/字母），空格显示为 SPACE
            parts.append("SPACE" if vk == 0x20 else chr(vk))
        else:
            parts.append("KEY_%04X" % vk)
        return "+".join(parts)

    def register(self, combo: str) -> bool:
        parsed = HotkeyManager.parse_combo(combo)
        if parsed is None:
            logger.warning(f"无效热键组合: '{combo}'")
            return False
        mods, vk = parsed
        if user32 is None:
            logger.warning("非Windows平台，不支持全局热键")
            return False
        logger.info(f"[热键] RegisterHotKey: id=0x{HOTKEY_ID:04X}, mods={mods}, vk=0x{vk:04X}, combo='{combo}'")
        try:
            ok = user32.RegisterHotKey(None, HOTKEY_ID, mods, vk)
        except Exception as e:
            logger.error(f"RegisterHotKey异常: {e}")
            return False
        if not ok:
            logger.warning(f"热键注册失败（可能被占用）: '{combo}' (mods={mods}, vk=0x{vk:04X})")
            return False
        self._registered = True
        self._combo = combo
        logger.info(f"全局热键已注册: '{combo}' → id=0x{HOTKEY_ID:04X}")
        return True

    def unregister(self) -> None:
        if self._registered:
            try:
                if user32 is not None:
                    user32.UnregisterHotKey(None, HOTKEY_ID)
                    logger.info(f"[热键] 注销主热键 id=0x{HOTKEY_ID:04X}")
            except Exception as e:
                logger.error(f"注销热键失败: {e}")
            self._registered = False
            self._callback = None  # ★ 同时清除回调，避免残留
        # 注销所有额外热键
        for hotkey_id in list(self._multi.keys()):
            try:
                if user32 is not None:
                    user32.UnregisterHotKey(None, hotkey_id)
                    combo = self._multi[hotkey_id][0] if hotkey_id in self._multi else "?"
                    logger.info(f"[热键] 注销额外热键 '{combo}' id=0x{hotkey_id:04X}")
            except Exception as e:
                logger.error(f"注销额外热键失败: {e}")
        if self._multi:
            self._multi.clear()
            logger.info("额外全局热键已注销")

    def is_registered(self) -> bool:
        return self._registered

    def set_on_hotkey(self, callback: Callable[[], None]) -> None:
        self._callback = callback

    @staticmethod
    def _message_address(message):
        """把 nativeEventFilter 的 message 参数统一成整数地址

        不同调用方传参形态不同：
        - PySide6 运行时：shiboken VoidPtr 或 int 地址
        - 单元测试：ctypes 指针对象

        Returns:
            整数地址；无法解析时返回 None
        """
        if isinstance(message, int):
            return message
        try:
            addr = ctypes.cast(message, ctypes.c_void_p)
            if addr and addr.value:
                return addr.value
        except Exception:
            pass
        try:
            return int(message)
        except Exception:
            return None

    def nativeEventFilter(self, eventType: bytes, message) -> tuple:
        try:
            # PySide6 windows_generic_MSG: eventType 可能有尾部 \x00，宽松匹配
            if b"windows_generic_MSG" in eventType:
                addr = self._message_address(message)
                if addr is None:
                    logger.warning("[热键] 无法解析 message 指针: %r", type(message))
                    return False, 0
                msg_ptr = ctypes.cast(addr, ctypes.POINTER(wintypes.MSG)).contents
                if msg_ptr.message == WM_HOTKEY:
                    hk_id = msg_ptr.wParam
                    logger.info(f"[热键] 收到 WM_HOTKEY: id=0x{hk_id:04X} (主热键id=0x{HOTKEY_ID:04X}, 额外={list(self._multi.keys())})")
                    # 主热键
                    if hk_id == HOTKEY_ID:
                        if self._callback:
                            logger.info("[热键] ✓ 触发主热键回调")
                            self._callback()
                        else:
                            logger.warning("[热键] ✗ 主热键触发但回调为None! (可能rebind后未set_on_hotkey)")
                        return True, 0
                    # 额外热键
                    entry = self._multi.get(hk_id)
                    if entry:
                        combo, cb = entry
                        logger.info(f"[热键] ✓ 触发额外热键: '{combo}' (id=0x{hk_id:04X})")
                        if cb:
                            cb()
                        else:
                            logger.warning(f"[热键] ✗ 额外热键 '{combo}' 回调为None!")
                        return True, 0
                    logger.warning(f"[热键] 未识别的热键ID: 0x{hk_id:04X}")
            else:
                # 非 Windows 消息类型，静默跳过（不刷日志）
                pass
        except Exception as e:
            logger.error(f"[热键] nativeEventFilter异常: {type(e).__name__}: {e}")
        return False, 0
