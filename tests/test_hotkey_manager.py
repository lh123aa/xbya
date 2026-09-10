# -*- coding: utf-8 -*-
import ctypes
from ctypes import wintypes
import pytest
from services.hotkey_manager import HotkeyManager, parse_key_event


def test_parse_combo_ctrl_alt_m():
    mods, vk = HotkeyManager.parse_combo("Ctrl+Alt+M")
    assert mods & 0x2 and mods & 0x1
    assert vk == ord('M')


def test_parse_combo_f1():
    mods, vk = HotkeyManager.parse_combo("F1")
    assert mods == 0
    assert vk == 0x70


def test_parse_combo_invalid_empty():
    assert HotkeyManager.parse_combo("") is None
    assert HotkeyManager.parse_combo("Ctrl+") is None
    assert HotkeyManager.parse_combo("Space+X") is None


def test_parse_combo_shift_f12():
    mods, vk = HotkeyManager.parse_combo("Shift+F12")
    assert mods & 0x4
    assert vk == 0x7B


def test_combo_to_text_roundtrip():
    mods, vk = HotkeyManager.parse_combo("Ctrl+Alt+M")
    assert HotkeyManager.combo_to_text((mods, vk)) == "Ctrl+Alt+M"


def test_register_fail_when_occupied(monkeypatch):
    import services.hotkey_manager as hm
    class FakeUser32:
        @staticmethod
        def RegisterHotKey(hwnd, id, mods, vk):
            return 0
        @staticmethod
        def UnregisterHotKey(hwnd, id):
            return True
    monkeypatch.setattr(hm, "user32", FakeUser32())
    m = HotkeyManager()
    assert m.register("Ctrl+Alt+M") is False
    assert m.is_registered() is False


def test_register_success_and_unregister(monkeypatch):
    import services.hotkey_manager as hm
    class FakeUser32:
        @staticmethod
        def RegisterHotKey(hwnd, id, mods, vk):
            return 1
        @staticmethod
        def UnregisterHotKey(hwnd, id):
            return True
    monkeypatch.setattr(hm, "user32", FakeUser32())
    m = HotkeyManager()
    assert m.register("Ctrl+Alt+M") is True
    assert m.is_registered() is True
    m.unregister()
    assert m.is_registered() is False


def test_hotkey_callback_on_filter(monkeypatch):
    import services.hotkey_manager as hm
    class FakeUser32:
        @staticmethod
        def RegisterHotKey(hwnd, id, mods, vk):
            return 1
        @staticmethod
        def UnregisterHotKey(hwnd, id):
            return True
    monkeypatch.setattr(hm, "user32", FakeUser32())
    m = HotkeyManager()
    calls = []
    m.set_on_hotkey(lambda: calls.append(1))
    m.register("Ctrl+Alt+M")
    class MSG(ctypes.Structure):
        _fields_ = [("hwnd", wintypes.HWND), ("message", wintypes.UINT),
                    ("wParam", ctypes.c_size_t), ("lParam", ctypes.c_long)]
    msg = MSG()
    msg.message = 0x0312                                   # WM_HOTKEY
    msg.wParam = hm.HOTKEY_ID                              # 主热键 ID（引用常量，避免硬编码漂移）
    # PySide6 签名: nativeEventFilter(eventType: bytes, message: int|ptr) -> tuple
    result = m.nativeEventFilter(b"windows_generic_MSG", ctypes.pointer(msg))
    assert calls == [1]
    assert result == (True, 0)


def test_hotkey_unknown_id_ignored(monkeypatch):
    """未注册的热键 ID 不触发主回调"""
    import services.hotkey_manager as hm
    class FakeUser32:
        @staticmethod
        def RegisterHotKey(hwnd, id, mods, vk):
            return 1
        @staticmethod
        def UnregisterHotKey(hwnd, id):
            return True
    monkeypatch.setattr(hm, "user32", FakeUser32())
    m = HotkeyManager()
    calls = []
    m.set_on_hotkey(lambda: calls.append(1))
    m.register("Ctrl+Alt+M")

    class MSG(ctypes.Structure):
        _fields_ = [("hwnd", wintypes.HWND), ("message", wintypes.UINT),
                    ("wParam", ctypes.c_size_t), ("lParam", ctypes.c_long)]
    msg = MSG()
    msg.message = 0x0312
    msg.wParam = hm.HOTKEY_ID + 123                        # 未注册的 ID

    result = m.nativeEventFilter(b"windows_generic_MSG", ctypes.pointer(msg))
    assert calls == []
    assert result == (False, 0)


def test_hotkey_non_hotkey_message_ignored(monkeypatch):
    """非 WM_HOTKEY 消息被忽略"""
    import services.hotkey_manager as hm
    m = HotkeyManager()
    calls = []
    m.set_on_hotkey(lambda: calls.append(1))

    class MSG(ctypes.Structure):
        _fields_ = [("hwnd", wintypes.HWND), ("message", wintypes.UINT),
                    ("wParam", ctypes.c_size_t), ("lParam", ctypes.c_long)]
    msg = MSG()
    msg.message = 0x0100                                   # WM_KEYDOWN，非热键
    msg.wParam = hm.HOTKEY_ID

    result = m.nativeEventFilter(b"windows_generic_MSG", ctypes.pointer(msg))
    assert calls == []
    assert result == (False, 0)


def test_message_address_handles_int_and_pointer():
    """_message_address 兼容整数与 ctypes 指针两种形态"""
    class MSG(ctypes.Structure):
        _fields_ = [("hwnd", wintypes.HWND), ("message", wintypes.UINT),
                    ("wParam", ctypes.c_size_t), ("lParam", ctypes.c_long)]
    msg = MSG()

    ptr_addr = HotkeyManager._message_address(ctypes.pointer(msg))
    assert isinstance(ptr_addr, int) and ptr_addr > 0

    assert HotkeyManager._message_address(0x1234) == 0x1234

    assert HotkeyManager._message_address(object()) is None


def test_parse_key_event_f9():
    from PySide6.QtCore import Qt
    assert parse_key_event(Qt.Key_F9, Qt.NoModifier) == "F9"


def test_parse_key_event_ctrl_m():
    from PySide6.QtCore import Qt
    assert parse_key_event(Qt.Key_M, Qt.ControlModifier) == "Ctrl+M"


def test_parse_key_event_roundtrip_consistency():
    from PySide6.QtCore import Qt
    combo = parse_key_event(Qt.Key_M, Qt.ControlModifier | Qt.AltModifier)
    assert combo == "Ctrl+Alt+M"
    mods, vk = HotkeyManager.parse_combo(combo)
    assert HotkeyManager.combo_to_text((mods, vk)) == combo
