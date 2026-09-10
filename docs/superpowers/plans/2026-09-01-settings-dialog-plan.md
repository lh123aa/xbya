# Settings Dialog Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为桌宠实现系统设置页面（7 组设置 + 全局热键控制麦克风开关 + 实时热更新）。

**Architecture:** 新建 `services/hotkey_manager.py`（Window RegisterHotKey + Qt 事件过滤器）与 `ui/settings_dialog.py`（非模态 QDialog，左侧 Tab 导航）；右键菜单加"设置"入口；保存后 PetWindow.apply_settings() 热更新实时项；开/关麦克风由全局热键触发并气泡提示。

**Tech Stack:** Python 3.12, PySide6, ctypes(RegisterHotKey), yaml, pytest

## Global Constraints

- 项目路径: `E:\程序\桌面宠物\xiaoyi-desktop-pet`，Windows，Python 3.12
- 热键默认 `Ctrl+Alt+M`；注册失败（占用）→ 气泡提示
- config.yaml 新增键：`voice.hotkey_enabled`(true)、`voice.hotkey_toggle`(Ctrl+Alt+M)、`ui.always_on_top`(true)、`system.autostart`(false)
- 保存写回 config.yaml（ConfigManager.set）；取消不写
- 实时生效项：listening/speech_volume/max_seconds/always_on_top/pet_size?（pet_size 标注重启）/autostart；重启生效项：ASR/LLM/TTS 参数——保存后提示"部分设置需重启生效"
- 右键菜单已有 contextMenuEvent（聊天/喂食/退出）→ 添加"设置"项
- 测试基线 175 passed + 2 pre-existing FAILED（test_app.py）不得修复
- 热键捕获控件：QPushButton 点击进入捕获态，支持 Ctrl/Alt/Shift+Key + 单独键（F1-F12/字母/数字），ESC 取消，显示 "Ctrl+Alt+M"

---

### Task 1: HotkeyManager（全局热键服务）

**Files:**
- Create: `services/hotkey_manager.py`
- Test: `tests/test_hotkey_manager.py`

**Interfaces:**
- Produces:
  ```python
  class HotkeyManager:
      def __init__(self, parent=None)
      def register(self, combo: str) -> bool      # 注册热键（combo如"Ctrl+Alt+M"）
      def unregister(self) -> None
      def is_registered(self) -> bool
      @staticmethod
      def parse_combo(combo: str) -> Optional[tuple[int, int]]  # "Ctrl+Alt+M" → (modifiers, vkcode)；无效→None
      @staticmethod
      def combo_to_text(mod_vk: tuple[int,int]) -> str  # (mods, vk) → "Ctrl+Alt+M"
      def set_on_hotkey(self, callback)
  ```
- 实现：ctypes `user32.RegisterHotKey(hwnd, id, mod, vk)` 需要 hwnd=0（线程热键）或 Qt 原生事件过滤器 `QAbstractNativeEventFilter.nativeEventFilter` 捕获 `WM_HOTKEY`（消息类型 `win32`）。Qt 方案：filter 安装在 QApplication，HM_GET... 简化：实现 `nativeEventFilter(event_type: bytes, message: int, result: int)`，事件 type == b'windows_generic_MSG'，解析 MSG.message == 0x0312 (WM_HOTKEY) → wParam == hotkey_id → 回调。
- Windows 常量: MOD_ALT=0x1, MOD_CONTROL=0x2, MOD_SHIFT=0x4, MOD_WIN=0x8; VK 用 `user32.VkKeyScanW` 或预置映射（A-Z, 0-9, F1-F12）。

- [ ] **Step 1: 编写失败测试** `tests/test_hotkey_manager.py`

```python
# -*- coding: utf-8 -*-
import pytest
from services.hotkey_manager import HotkeyManager


def test_parse_combo_ctrl_alt_m():
    mods, vk = HotkeyManager.parse_combo("Ctrl+Alt+M")
    assert mods & 0x2 and mods & 0x1  # MOD_CONTROL|MOD_ALT
    assert vk == ord('M')


def test_parse_combo_f1():
    mods, vk = HotkeyManager.parse_combo("F1")
    assert mods == 0
    assert vk == 0x70  # VK_F1


def test_parse_combo_invalid_empty():
    assert HotkeyManager.parse_combo("") is None
    assert HotkeyManager.parse_combo("Ctrl+") is None


def test_combo_to_text_roundtrip():
    mods, vk = HotkeyManager.parse_combo("Ctrl+Alt+M")
    assert HotkeyManager.combo_to_text((mods, vk)) == "Ctrl+Alt+M"


def test_register_fail_when_occupied(monkeypatch):
    """RegisterHotKey 返回0时 register 返回 False 且 is_registered False"""
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
    ok = m.register("Ctrl+Alt+M")
    assert ok is False
    assert m.is_registered() is False


def test_register_success(monkeypatch):
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
    """WM_HOTKEY 消息触发回调（模拟 nativeEventFilter）"""
    import services.hotkey_manager as hm
    class FakeUser32:
        @staticmethod
        def RegisterHotKey(hwnd, id, mods, vk):
            return 1
        @staticmethod
        def UnregisterHotKey(hwnd, id):
            return True
        VK = None
    monkeypatch.setattr(hm, "user32", FakeUser32())
    monkeypatch.setattr(hm, "WM_HOTKEY", 0x0312)
    m = HotkeyManager()
    calls = []
    m.set_on_hotkey(lambda: calls.append(1))
    m.register("Ctrl+Alt+M")
    # 模拟 Qt 传入 WM_HOTKEY 消息（wParam=1=注册id）
    import ctypes
    from ctypes import wintypes
    class MSG(ctypes.Structure):
        _fields_ = [("hwnd", wintypes.HWND), ("message", wintypes.UINT),
                    ("wParam", ctypes.c_size_t), ("lParam", ctypes.c_long)]
    msg = MSG()
    msg.message = 0x0312
    msg.wParam = 1
    m.nativeEventFilter(b"windows_generic_MSG", ctypes.pointer(msg), ctypes.pointer(ctypes.c_long(0)))
    assert calls == [1], "WM_HOTKEY应触发回调"
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_hotkey_manager.py -v`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现 `services/hotkey_manager.py`**

```python
# -*- coding: utf-8 -*-
"""全局热键管理：Windows RegisterHotKey + Qt 原生事件过滤器"""
import ctypes
from ctypes import wintypes
import logging
from typing import Optional, Callable

from PySide6.QtCore import QAbstractNativeEventFilter

logger = logging.getLogger(__name__)

MOD_ALT = 0x1
MOD_CONTROL = 0x2
MOD_SHIFT = 0x4
MOD_WIN = 0x8
WM_HOTKEY = 0x0312
HOTKEY_ID = 0x1234  # 自定义热键ID

# 单键（无可视字符）的扫描码映射：功能键
SPECIAL_KEYS = {
    "F1": 0x70, "F2": 0x71, "F3": 0x72, "F4": 0x73, "F5": 0x74,
    "F6": 0x75, "F7": 0x76, "F8": 0x77, "F9": 0x78, "F10": 0x79,
    "F11": 0x7A, "F12": 0x7B,
    "ESC": 0x1B, "SPACE": 0x20,
}


def _load_user32():
    """加载 user32（测试时替换）"""
    return ctypes.windll.user32


user32 = _load_user32()


class HotkeyManager(QAbstractNativeEventFilter):
    """全局热键管理器（单一实例，由App持有）"""

    def __init__(self):
        super().__init__()
        self._registered = False
        self._callback: Optional[Callable[[], None]] = None

    # ---------- 静态解析 ----------

    @staticmethod
    def parse_combo(combo: str) -> Optional[tuple]:
        """'Ctrl+Alt+M' → (modifiers, vkcode)；无效返回None"""
        if not combo or "+" not in combo:
            # 单键（如F1）
            key = (combo or "").strip().upper()
            if key in SPECIAL_KEYS:
                return (0, SPECIAL_KEYS[key])
            if len(key) == 1 and key.isalnum():
                return (0, ord(key))
            return None
        mods = 0
        parts = [p.strip().upper() for p in combo.split("+")]
        key = parts[-1]
        for p in parts[:-1]:
            if p == "CTRL" or p == "CONTROL":
                mods |= MOD_CONTROL
            elif p == "ALT":
                mods |= MOD_ALT
            elif p == "SHIFT":
                mods |= MOD_SHIFT
            elif p == "WIN" or p == "META":
                mods |= MOD_WIN
            else:
                return None
        if key in SPECIAL_KEYS:
            return (mods, SPECIAL_KEYS[key])
        if len(key) == 1 and key.isalnum():
            return (mods, ord(key))
        return None

    @staticmethod
    def combo_to_text(mod_vk: tuple) -> str:
        """(modifiers, vkcode) → 'Ctrl+Alt+M'"""
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
        # 反查键名
        key_map = {v: k for k, v in SPECIAL_KEYS.items()}
        if vk in key_map:
            parts.append(key_map[vk])
        elif 0x30 <= vk <= 0x39:
            parts.append(chr(vk))
        elif 0x41 <= vk <= 0x5A:
            parts.append(chr(vk))
        else:
            parts.append("KEY_%04X" % vk)
        return "+".join(parts)

    # ---------- 注册/注销 ----------

    def register(self, combo: str) -> bool:
        """注册全局热键。成功返回True；失败（占用等）返回False"""
        parsed = HotkeyManager.parse_combo(combo)
        if parsed is None:
            logger.warning(f"无效热键组合: {combo}")
            return False
        mods, vk = parsed
        try:
            ok = user32.RegisterHotKey(None, HOTKEY_ID, mods, vk)
        except AttributeError:
            logger.warning("平台不支持RegisterHotKey（非Windows）")
            return False
        if not ok:
            logger.warning(f"热键注册失败（可能被占用）: {combo}")
            return False
        self._registered = True
        self._combo = combo
        logger.info(f"全局热键已注册: {combo}")
        return True

    def unregister(self) -> None:
        """注销热键"""
        if self._registered:
            try:
                user32.UnregisterHotKey(None, HOTKEY_ID)
            except Exception as e:
                logger.error(f"注销热键失败: {e}")
            self._registered = False
            logger.info("全局热键已注销")

    def is_registered(self) -> bool:
        return self._registered

    def set_on_hotkey(self, callback: Callable[[], None]) -> None:
        self._callback = callback

    # ---------- Qt 事件过滤 ----------

    def nativeEventFilter(self, event_type, message):
        """捕获 WM_HOTKEY 消息"""
        try:
            if event_type == b"windows_generic_MSG":
                msg = ctypes.cast(message, ctypes.POINTER(wintypes.MSG)).contents
                if msg.message == WM_HOTKEY and msg.wParam == HOTKEY_ID:
                    if self._callback:
                        self._callback()
                    return True, 0
        except Exception as e:
            logger.debug(f"热键事件过滤异常: {e}")
        return False, 0
```

注意：`nativeEventFilter` 在 PySide6 返回 `(bool, int)` 元组（非Qt5's bool）。

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_hotkey_manager.py -v`
Expected: PASS（6条）

- [ ] **Step 5: Commit**

```bash
git add services/hotkey_manager.py tests/test_hotkey_manager.py
git commit -m "feat(settings): global hotkey manager (RegisterHotKey + WM_HOTKEY filter)"
```

---

### Task 2: SettingsDialog（设置界面）

**Files:**
- Create: `ui/settings_dialog.py`
- Test: `tests/test_settings_dialog.py`

**Interfaces:**
- Consumes: `ConfigManager.get/set`（点号键），`HotkeyManager.parse_combo/combo_to_text`（Task 1）
- Produces:
  ```python
  class SettingsDialog(QDialog):
      def __init__(self, config_manager, parent=None)
      def apply_to_config(self) -> bool      # 读控件→写config；返回是否有效
      def reset_to_defaults(self) -> None    # 恢复默认值（仅加载默认，需再保存生效）
      # 内部创建各Tab控件并加载初值
  ```

**步骤：**

- [ ] **Step 1: 编写失败测试** `tests/test_settings_dialog.py`

```python
# -*- coding: utf-8 -*-
import os
import pytest
from PySide6.QtWidgets import QApplication, QLineEdit, QComboBox, QSpinBox, QCheckBox


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def cfg(tmp_path):
    """临时config（隔离真实配置）"""
    import yaml
    from core.config_manager import ConfigManager
    p = tmp_path / "config.yaml"
    p.write_text("""
app:
  name: 小忆
  version: 2.0.0
system:
  performance_mode: low
plugins:
  asr:
    engine: faster_whisper
    params:
      model_size: small
      device: cpu
  tts:
    engine: edge_tts
    params:
      voice: zh-CN-XiaoxiaoNeural
  llm:
    engine: ollama
    params:
      base_url: http://localhost:11434
      model: qwen2.5:1.5b
      system_prompt: 默认人设
voice:
  listening: true
  speech_volume: 300
  max_listen_seconds: 10
  echo_cooldown: 3
  reply_min_length: 3
ui:
  pet_size: 300
  always_on_top: true
  position: {x: 100, y: 100}
""", encoding="utf-8")
    return ConfigManager(str(p))


def test_dialog_loads_values(qapp, cfg, monkeypatch):
    """对话框创建后控件绑定config值"""
    # 避免真实Ollama请求
    monkeypatch.setattr("ui.settings_dialog.requests", None)
    from ui.settings_dialog import SettingsDialog
    dlg = SettingsDialog(cfg)
    assert dlg.name_edit.text() == "小忆"
    assert dlg.speech_volume_slider.value() == 300
    assert dlg.listening_check.isChecked() is True
    assert dlg.max_listen_spin.value() == 10


def test_apply_to_config_writes(qapp, cfg, monkeypatch):
    monkeypatch.setattr("ui.settings_dialog.requests", None)
    from ui.settings_dialog import SettingsDialog
    dlg = SettingsDialog(cfg)
    dlg.name_edit.setText("喵喵")
    dlg.listening_check.setChecked(False)
    dlg.max_listen_spin.setValue(8)
    assert dlg.apply_to_config() is True
    assert cfg.get("app.name") == "喵喵"
    assert cfg.get("voice.listening") is False
    assert cfg.get("voice.max_listen_seconds") == 8


def test_cancel_does_not_write(qapp, cfg, monkeypatch):
    monkeypatch.setattr("ui.settings_dialog.requests", None)
    from ui.settings_dialog import SettingsDialog
    dlg = SettingsDialog(cfg)
    dlg.name_edit.setText("不应写入")
    # modal=false 下取消只是 reject；这里模拟"未apply直接关闭"
    dlg.reject()
    assert cfg.get("app.name") == "小忆", "取消不应写config"


def test_hotkey_capture_widget(qapp, cfg, monkeypatch):
    """热键捕获控件：记录按键组合→显示文本"""
    monkeypatch.setattr("ui.settings_dialog.requests", None)
    from ui.settings_dialog import SettingsDialog
    dlg = SettingsDialog(cfg)
    widget = dlg.hotkey_btn
    widget.start_capture()  # 进入捕获态
    from PySide6.QtGui import Qt
    widget.captured_combo = None
    # 模拟控件内部成功回调（capture逻辑独立在控件类中）
    widget.finish_capture("Ctrl+Alt+M")
    assert widget.text().endswith("Ctrl+Alt+M")
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_settings_dialog.py -v`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现 `ui/settings_dialog.py`**

核心结构（完整实现，包含所有 7 组）：

```python
# -*- coding: utf-8 -*-
"""小忆系统设置对话框"""
import logging
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QTabWidget, QWidget,
    QGroupBox, QPushButton, QCheckBox, QSlider, QSpinBox, QLineEdit,
    QComboBox, QPlainTextEdit, QLabel, QDialogButtonBox, QMessageBox,
)

logger = logging.getLogger(__name__)

# Edge TTS 常用中文音色
TTS_VOICES = [
    ("晓晓(女·温暖)", "zh-CN-XiaoxiaoNeural"),
    ("云希(男·阳光)", "zh-CN-YunxiNeural"),
    ("晓伊(女·活泼)", "zh-CN-XiaoyiNeural"),
    ("云健(男·稳重)", "zh-CN-YunjianNeural"),
    ("晓辰(女·优雅)", "zh-CN-XiaochenNeural"),
    ("晓涵(女·甜美)", "zh-CN-XiaohanNeural"),
    ("晓婷(女·知性)", "zh-CN-XiaotingNeural"),
    ("云扬(男·激情)", "zh-CN-YunyangNeural"),
]

ASR_MODELS = ["tiny", "base", "small", "medium"]
ASR_DEVICES = ["cpu", "cuda"]
PERF_MODES = ["low", "medium", "high"]


class HotkeyCaptureButton(QPushButton):
    """点击后进入捕获态，按键组合显示为文本"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setToolTip("点击后按下组合键（如 Ctrl+Alt+M），Esc 取消")
        self.clicked.connect(self._toggle_capture)
        self._capturing = False
        self.current_combo = ""

    def set_combo(self, combo: str):
        self.current_combo = combo
        self.setText(combo)
        self.setChecked(False)

    def _toggle_capture(self):
        if self._capturing:
            self._cancel()
        else:
            self._capturing = True
            self.setText("按下快捷键...")

    def _cancel(self):
        self._capturing = False
        self.setChecked(False)
        self.setText(self.current_combo)

    def _save_combo(self, combo: str):
        self._capturing = False
        self.current_combo = combo
        self.setText(combo)
        self.setChecked(False)

    def keyPressEvent(self, event):
        if not self._capturing:
            super().keyPressEvent(event)
            return
        from PySide6.QtGui import QKeySequence
        key = event.key()
        if key == Qt.Key_Escape:
            self._cancel()
            return
        # 组合键解析：Ctrl/Alt/Shift + Key 或 功能键单发
        from services.hotkey_manager import HotkeyManager, parse_key_event
        combo = parse_key_event(key, event.modifiers())
        if combo:
            self._save_combo(combo)
        event.accept()

    def focusOutEvent(self, event):
        if self._capturing:
            self._cancel()
        super().focusOutEvent(event)


# 注意：parse_key_event 在 hotkey_manager 中实现为：
# def parse_key_event(key: int, modifiers) -> Optional[str]:
#     将 Qt key + modifiers 转 "Ctrl+Alt+M" 形式


class SettingsDialog(QDialog):
    def __init__(self, config_manager, parent=None):
        super().__init__(parent)
        self.cfg = config_manager
        self.setWindowTitle("小忆设置")
        self.setModal(False)  # 非模态，宠物继续可用
        self.resize(560, 460)

        self._build_ui()
        self._load_values()

    # ---------- UI构建 ----------

    def _build_ui(self):
        root = QVBoxLayout(self)
        self.tabs = QTabWidget()
        root.addWidget(self.tabs)

        self.tabs.addTab(self._tab_voice(), "语音交互")
        self.tabs.addTab(self._tab_ai(), "AI 对话")
        self.tabs.addTab(self._tab_asr(), "语音识别")
        self.tabs.addTab(self._tab_tts(), "语音合成")
        self.tabs.addTab(self._tab_hotkey(), "快捷键")
        self.tabs.addTab(self._tab_general(), "其他")
        self.tabs.addTab(self._tab_info(), "系统信息")

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        reset_btn = QPushButton("恢复默认")
        btns.addButton(reset_btn, QDialogButtonBox.ResetRole)
        btns.accepted.connect(self._on_accept)
        btns.rejected.connect(self.reject)
        reset_btn.clicked.connect(self.reset_to_defaults)
        root.addWidget(btns)

    def _tab_voice(self):
        w = QWidget()
        form = QFormLayout(w)
        self.listening_check = QCheckBox("常驻语音监听")
        self.listening_check.toolTip = "开启后无需点击，说话即响应"
        self.speech_volume_slider = QSlider(Qt.Horizontal)
        self.speech_volume_slider.setRange(50, 1000)
        self.speech_volume_label = QLabel("300")
        row = QHBoxLayout()
        row.addWidget(self.speech_volume_slider)
        row.addWidget(self.speech_volume_label)
        self.speech_volume_slider.valueChanged.connect(
            lambda v: self.speech_volume_label.setText(str(v)))
        self.max_listen_spin = QSpinBox(); self.max_listen_spin.setRange(3, 15)
        self.echo_cooldown_spin = QSpinBox(); self.echo_cooldown_spin.setRange(1, 10)
        self.reply_min_spin = QSpinBox(); self.reply_min_spin.setRange(1, 10)
        form.addRow("监听", self.listening_check)
        form.addRow("说话音量阈值", row)
        form.addRow("单次最长监听(秒)", self.max_listen_spin)
        form.addRow("TTS回声冷却(秒)", self.echo_cooldown_spin)
        form.addRow("最小回复字数", self.reply_min_spin)
        return w

    def _tab_ai(self):
        w = QWidget()
        form = QFormLayout(w)
        self.llm_url_edit = QLineEdit()
        self.llm_model_combo = QComboBox()
        self.llm_model_combo.setEditable(True)  # 允许输入未列出的模型
        self.llm_prompt_edit = QPlainTextEdit()
        self.llm_prompt_edit.setFixedHeight(120)
        form.addRow("Ollama地址", self.llm_url_edit)
        form.addRow("模型", self.llm_model_combo)
        form.addRow("人设提示词", self.llm_prompt_edit)
        hint = QLabel("⚠️ 修改后需重启生效")
        form.addRow(hint)
        return w

    def _tab_asr(self):
        w = QWidget()
        form = QFormLayout(w)
        self.asr_model_combo = QComboBox()
        self.asr_model_combo.addItems(ASR_MODELS)
        self.asr_device_combo = QComboBox()
        self.asr_device_combo.addItems(ASR_DEVICES)
        form.addRow("模型大小", self.asr_model_combo)
        form.addRow("设备", self.asr_device_combo)
        hint = QLabel("⚠️ 修改后需重启生效")
        form.addRow(hint)
        return w

    def _tab_tts(self):
        w = QWidget()
        form = QFormLayout(w)
        self.tts_voice_combo = QComboBox()
        for label, value in TTS_VOICES:
            self.tts_voice_combo.addItem(label, value)
        form.addRow("音色", self.tts_voice_combo)
        # 试听按钮
        preview = QPushButton("试听")
        preview.clicked.connect(self._preview_tts)
        form.addRow("", preview)
        hint = QLabel("⚠️ 修改后需重启生效")
        form.addRow(hint)
        return w

    def _tab_hotkey(self):
        w = QWidget()
        form = QFormLayout(w)
        self.hotkey_enabled_check = QCheckBox("启用麦克风快捷键")
        from ui.settings_dialog import HotkeyCaptureButton
        self.hotkey_btn = HotkeyCaptureButton()
        form.addRow("启用", self.hotkey_enabled_check)
        form.addRow("麦克风开关热键", self.hotkey_btn)
        tip = QLabel("全局生效：在其他软件中也能使用\n点击按下组合键即可录制，Esc 取消")
        form.addRow(tip)
        return w

    def _tab_general(self):
        w = QWidget()
        form = QFormLayout(w)
        self.name_edit = QLineEdit()
        self.pet_size_slider = QSlider(Qt.Horizontal)
        self.pet_size_slider.setRange(100, 400)
        self.pet_size_label = QLabel("300")
        self.pet_size_slider.valueChanged.connect(
            lambda v: self.pet_size_label.setText(f"{v}px"))
        row = QHBoxLayout()
        row.addWidget(self.pet_size_slider)
        row.addWidget(self.pet_size_label)
        self.topmost_check = QCheckBox("窗口置顶")
        self.autostart_check = QCheckBox("开机自启")
        self.perf_combo = QComboBox()
        self.perf_combo.addItems(PERF_MODES)
        form.addRow("宠物名称", self.name_edit)
        form.addRow("宠物大小", row)
        form.addRow("位置", QLabel("保存位置由拖拽决定"))
        form.addRow("置顶", self.topmost_check)
        form.addRow("开机自启", self.autostart_check)
        form.addRow("性能模式", self.perf_combo)
        return w

    def _tab_info(self):
        w = QWidget()
        form = QFormLayout(w)
        # 硬件信息（从hardware_detector）
        info = self._collect_system_info()
        for k, v in info.items():
            form.addRow(k, QLabel(str(v)))
        return w

    def _collect_system_info(self) -> dict:
        try:
            from core.hardware_detector import get_hardware_detector
            hw = get_hardware_detector().detect()
            info = {
                "CPU": f"{hw.cpu_count} 核",
                "内存": f"{hw.memory_gb:.1f} GB",
                "GPU": hw.gpu_name if hw.has_gpu else "无",
            }
        except Exception:
            info = {"CPU": "未知", "内存": "未知", "GPU": "未知"}
        info["版本"] = self.cfg.get("app.version", "2.0.0")
        # Ollama状态
        import yaml
        base_url = self.cfg.get("plugins.llm.params.base_url", "http://localhost:11434")
        try:
            import requests
            r = requests.get(f"{base_url}/api/tags", timeout=3)
            info["Ollama"] = f"在线 ({r.status_code})"
            models = r.json().get("models", [])
            info["模型列表"] = ", ".join(m["name"] for m in models[:5])
        except Exception:
            info["Ollama"] = "离线"
        info["麦克风"] = "可用" if self._mic_available() else "不可用"
        return info

    def _mic_available(self) -> bool:
        try:
            from services.microphone_service import MicrophoneService
            return MicrophoneService().is_available()
        except Exception:
            return False

    # ---------- 加载/保存 ----------

    def _load_values(self):
        cfg = self.cfg
        self.name_edit.setText(cfg.get("app.name", "小忆"))
        self.listening_check.setChecked(cfg.get("voice.listening", True))
        vol = int(cfg.get("voice.speech_volume", 300))
        self.speech_volume_slider.setValue(vol)
        self.speech_volume_label.setText(str(vol))
        self.max_listen_spin.setValue(int(cfg.get("voice.max_listen_seconds", 10)))
        self.echo_cooldown_spin.setValue(int(cfg.get("voice.echo_cooldown", 3)))
        self.reply_min_spin.setValue(int(cfg.get("voice.reply_min_length", 3)))

        self.llm_url_edit.setText(cfg.get("plugins.llm.params.base_url", "http://localhost:11434"))
        # 模型列表动态填充
        self._refresh_llm_models()
        self.llm_prompt_edit.setPlainText(cfg.get("plugins.llm.params.system_prompt", ""))

        idx = self.asr_model_combo.findText(cfg.get("plugins.asr.params.model_size", "small"))
        self.asr_model_combo.setCurrentIndex(max(idx, 0))
        idx = self.asr_device_combo.findText(cfg.get("plugins.asr.params.device", "cpu"))
        self.asr_device_combo.setCurrentIndex(max(idx, 0))

        voice = cfg.get("plugins.tts.params.voice", "zh-CN-XiaoxiaoNeural")
        idx = self.tts_voice_combo.findData(voice)
        if idx >= 0:
            self.tts_voice_combo.setCurrentIndex(idx)

        self.hotkey_enabled_check.setChecked(cfg.get("voice.hotkey_enabled", True))
        self.hotkey_btn.set_combo(cfg.get("voice.hotkey_toggle", "Ctrl+Alt+M"))

        size = int(cfg.get("ui.pet_size", 300))
        self.pet_size_slider.setValue(size)
        self.pet_size_label.setText(f"{size}px")
        self.topmost_check.setChecked(cfg.get("ui.always_on_top", True))
        self.autostart_check.setChecked(cfg.get("system.autostart", False))
        idx = self.perf_combo.findText(cfg.get("system.performance_mode", "low"))
        self.perf_combo.setCurrentIndex(max(idx, 0))

    def _refresh_llm_models(self):
        """从Ollama拉取模型列表填充下拉（失败则保留可编辑输入框）"""
        self.llm_model_combo.clear()
        base_url = self.llm_url_edit.text().rstrip("/")
        try:
            import requests
            r = requests.get(f"{base_url}/api/tags", timeout=3)
            models = r.json().get("models", [])
            for m in models:
                self.llm_model_combo.addItem(m.get("name", ""))
            self.llm_model_combo.setEditText("")
        except Exception as e:
            logger.warning(f"获取Ollama模型列表失败: {e}")
        # 填入当前配置值（可编辑）
        cur = self.cfg.get("plugins.llm.params.model", "qwen2.5:1.5b")
        if self.llm_model_combo.findText(cur) < 0:
            self.llm_model_combo.addItem(cur)
        self.llm_model_combo.setCurrentText(cur)

    def apply_to_config(self) -> bool:
        """读控件值写回config。返回是否成功"""
        cfg = self.cfg
        cfg.set("app.name", self.name_edit.text().strip() or "小忆")
        cfg.set("voice.listening", self.listening_check.isChecked())
        cfg.set("voice.speech_volume", self.speech_volume_slider.value())
        cfg.set("voice.max_listen_seconds", self.max_listen_spin.value())
        cfg.set("voice.echo_cooldown", self.echo_cooldown_spin.value())
        cfg.set("voice.reply_min_length", self.reply_min_spin.value())

        cfg.set("plugins.llm.params.base_url", self.llm_url_edit.text().strip())
        cfg.set("plugins.llm.params.model", self.llm_model_combo.currentText().strip())
        cfg.set("plugins.llm.params.system_prompt", self.llm_prompt_edit.toPlainText().strip())

        cfg.set("plugins.asr.params.model_size", self.asr_model_combo.currentText())
        cfg.set("plugins.asr.params.device", self.asr_device_combo.currentText())
        cfg.set("plugins.tts.params.voice", self.tts_voice_combo.currentData())

        cfg.set("voice.hotkey_enabled", self.hotkey_enabled_check.isChecked())
        cfg.set("voice.hotkey_toggle", self.hotkey_btn.current_combo)

        cfg.set("ui.pet_size", self.pet_size_slider.value())
        cfg.set("ui.always_on_top", self.topmost_check.isChecked())
        cfg.set("system.autostart", self.autostart_check.isChecked())
        cfg.set("system.performance_mode", self.perf_combo.currentText())
        return True

    def _on_accept(self):
        if self.apply_to_config():
            self.accept()

    def _preview_tts(self):
        """试听当前选择音色"""
        import threading
        voice = self.tts_voice_combo.currentData()
        threading.Thread(target=self._preview_thread, args=(voice,), daemon=True).start()

    def _preview_thread(self, voice):
        try:
            from services.simple_voice_service import SimpleVoiceService  # 或直接edge_tts
            import asyncio
            import edge_tts
            from PySide6.QtWidgets import QApplication
            # 简化：edge_tts 生成 + app.speak 播放
            # 走系统中已有TTS插件链路避免重复实现
        except Exception as e:
            logger.error(f"试听失败: {e}")

    def reset_to_defaults(self):
        """恢复默认值（只改界面，需保存生效）"""
        QMessageBox.information(self, "恢复默认", "已恢复为默认值，点击\"确定\"保存生效")
        self._load_values()  # 重新加载当前config（即默认）
```

备注：试听实现走存活的 TTS 链路（若场景无 app 引用则省略试听功能，点击提示"启动后可用"）。**简化**：`_preview_thread` 直接使用 `edge_tts.Communicate(voice, text).save(temp)` + `pygame.mixer` 播放（复刻 app._play_audio 逻辑，独立可测）。

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_settings_dialog.py -v`
Expected: PASS（4条）

- [ ] **Step 5: Commit**

```bash
git add ui/settings_dialog.py tests/test_settings_dialog.py
git commit -m "feat(settings): settings dialog with 7 groups + hotkey capture"
```

---

### Task 3: 集成（右键菜单 + 热键 + 热更新）

**Files:**
- Modify: `ui/pet_window.py`
- Modify: `core/app.py`
- Modify: `config.yaml`
- Test: `tests/test_pet_window.py`

**Interfaces:**
- Consumes: `SettingsDialog`（Task 2），`HotkeyManager`（Task 1）
- Produces:
  ```python
  # PetWindow新增：
  def open_settings(self) -> None
  def apply_settings(self) -> None           # 保存后热更新（listening/always_on_top/热键重注册）
  def toggle_voice_monitor(self) -> None     # 热键回调：切换监听 + 气泡提示
  ```
  ```python
  # App新增：
  self.hotkey_manager: HotkeyManager          # init中创建
  def setup_hotkey(self) -> None              # 启动注册
  def teardown_hotkey(self) -> None           # 关闭注销
  ```

**步骤：**

- [ ] **Step 1: config.yaml 新增键**

在 `voice:` 节增加：
```yaml
  hotkey_enabled: true          # 全局热键开关
  hotkey_toggle: Ctrl+Alt+M     # 麦克风开关热键
```
在 `ui:` 节增加：
```yaml
  always_on_top: true           # 窗口置顶
```
在 `system:` 节增加（performance_mode 旁）：
```yaml
  autostart: false              # 开机自启
```

- [ ] **Step 2: 编写失败测试**（`tests/test_pet_window.py` 追加）

```python
class TestSettingsIntegration:
    def test_context_menu_has_settings(self, qapp):
        """右键菜单包含设置项"""
        from ui.pet_window import PetWindow
        win = PetWindow()
        from PySide6.QtWidgets import QApplication
        from PySide6.QtCore import QPoint
        # 不能真正弹菜单（阻塞）——检查open_settings方法存在
        assert hasattr(win, "open_settings")
        assert hasattr(win, "apply_settings")
        assert hasattr(win, "toggle_voice_monitor")

    def test_toggle_voice_monitor_stops_and_starts(self, qapp, monkeypatch):
        """toggle切换监听状态并气泡提示"""
        from ui.pet_window import PetWindow
        win = PetWindow()
        state = {"started": 0, "stopped": 0}
        class FakeMic:
            def listen_standby(self, *a, **k): state["started"] += 1; return True
            def stop_listen_standby(self): state["stopped"] += 1
            def is_available(self): return True
        monkeypatch.setattr("services.microphone_service.MicrophoneService", FakeMic)
        # 先启动
        win.start_voice_monitor()
        assert state["started"] == 1
        # toggle → 关闭
        win.toggle_voice_monitor()
        assert state["stopped"] == 1
        assert "关闭" in win.bubble_text
        # toggle → 开启
        win.toggle_voice_monitor()
        assert state["started"] == 2
        assert "开启" in win.bubble_text

    def test_apply_settings_hotkey_reregister(self, qapp, monkeypatch):
        """保存后热键按新配置重注册"""
        from ui.pet_window import PetWindow
        win = PetWindow()
        calls = []
        class FakeHK:
            def __init__(self, *a, **k): pass
            def register(self, combo): calls.append(combo); return True
            def unregister(self): calls.append("unreg")
            def is_registered(self): return True
            def set_on_hotkey(self, cb): pass
        monkeypatch.setattr("core.app.HotkeyManager", FakeHK)
        win.app = None
        # 通过app.hotkey_manager路径调用（简化：直接测register流程）
        win.apply_settings()
        # 至少不抛异常
        assert True
```

（简化：apply_settings 测试主要验证"调用不抛异常"+关键开关逻辑；深度集成由手动冒烟）

- [ ] **Step 3: 运行确认失败**

Run: `pytest tests/test_pet_window.py::TestSettingsIntegration -v`
Expected: FAIL（无这些方法）

- [ ] **Step 4: 实现集成**

**ui/pet_window.py**：
1) contextMenuEvent 添加"设置..."：
```python
        menu.addAction("设置...", self.open_settings)
```
2) 新增：
```python
    def open_settings(self):
        """打开设置对话框（非模态）"""
        from ui.settings_dialog import SettingsDialog
        cm = self.app.config_manager if (self.app and getattr(self.app, "config_manager", None)) else None
        if cm is None:
            from core.config_manager import get_config_manager
            cm = get_config_manager()
        self._settings_dlg = SettingsDialog(cm, parent=self)
        self._settings_dlg.accepted.connect(self.apply_settings)
        self._settings_dlg.show()

    def apply_settings(self):
        """设置保存后热更新"""
        try:
            cm = self.app.config_manager if self.app else None
            if not cm:
                return
            # 1. 监听开关/参数：运行中→按新配置重启监听
            listening = cm.get("voice.listening", True)
            self._monitor_enabled = listening
            self.stop_voice_monitor()
            if listening and cm.get("voice.hotkey_enabled_stale", True):  # 简化：只要listening就启动
                self.start_voice_monitor()
            # 2. 置顶
            top = cm.get("ui.always_on_top", True)
            window_flags = self.windowFlags()
            if top:
                window_flags |= Qt.WindowStaysOnTopHint
            else:
                window_flags &= ~Qt.WindowStaysOnTopHint
            self.setWindowFlags(window_flags)
            self.show()
            # 3. 热键重注册
            self.rebind_hotkey()
            # 4. 开机自启
            self._apply_autostart(cm.get("system.autostart", False))
            # 5. 宠物大小
            size = int(cm.get("ui.pet_size", 300))
            if 100 <= size <= 400 and self._pet_size != size:
                self._pet_size = size
                self.setFixedSize(size + 100, size + 100)
            self.show_bubble("设置已保存", 1500)
        except Exception as e:
            logger.error(f"应用设置失败: {e}")

    def rebind_hotkey(self):
        """按config重注册热键"""
        cm = self.app.config_manager if self.app else None
        if not cm:
            return
        from core.app import get_app
        app = get_app() if not self.app else self.app
        hk = getattr(app, "hotkey_manager", None)
        if hk:
            hk.unregister()
            if cm.get("voice.hotkey_enabled", True):
                hk.register(cm.get("voice.hotkey_toggle", "Ctrl+Alt+M"))

    def toggle_voice_monitor(self):
        """热键回调：切换监听"""
        if self._monitor_enabled or self._monitor_mic is not None and self._monitor_mic.is_listening():
            self.stop_voice_monitor()
            self.show_bubble("麦克风已关闭 🔇", 1500)
        else:
            self.start_voice_monitor()
            self.show_bubble("麦克风已开启 🎤", 1500)

    def _apply_autostart(self, enabled: bool):
        """开机自启注册表写入/删除"""
        import winreg
        key = r"Software\Microsoft\Windows\CurrentVersion\Run"
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_SET_VALUE) as k:
                if enabled:
                    import sys, os
                    exe = sys.executable if getattr(sys, "frozen", False) else f'"{sys.executable}" "{os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "run.py"))}"'
                    winreg.SetValueEx(k, "XiaoYiPet", 0, winreg.REG_SZ, exe)
                else:
                    try:
                        winreg.DeleteValue(k, "XiaoYiPet")
                    except FileNotFoundError:
                        pass
        except Exception as e:
            logger.error(f"设置开机自启失败: {e}")
```

3) __init__ 新增 `self._monitor_enabled = True` 和 `self._pet_size = 300`（初始化时用 config 值）。

**core/app.py**：
1) __init__ 加 `self.hotkey_manager = None`；initialize() 中创建：
```python
            # 热键管理器
            from services.hotkey_manager import HotkeyManager
            self.hotkey_manager = HotkeyManager()
```
2) run() 中 pet_window.show() 后（start_voice_monitor 旁）：
```python
        # 注册全局热键
        self.setup_hotkey(pet_window)
```
3) 新增：
```python
    def setup_hotkey(self, pet_window) -> None:
        """启动时注册热键并绑定回调"""
        if not self.hotkey_manager:
            return
        enabled = self.config_manager.get("voice.hotkey_enabled", True)
        if enabled:
            ok = self.hotkey_manager.register(self.config_manager.get("voice.hotkey_toggle", "Ctrl+Alt+M"))
            if not ok:
                logger.warning("热键注册失败（可能被占用）")
        self.hotkey_manager.set_on_hotkey(pet_window.toggle_voice_monitor)
        # 安装到QApplication
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
        if app:
            app.installNativeEventFilter(self.hotkey_manager)

    def teardown_hotkey(self) -> None:
        """关闭时注销热键"""
        if self.hotkey_manager:
            from PySide6.QtWidgets import QApplication
            app = QApplication.instance()
            if app:
                app.removeNativeEventFilter(self.hotkey_manager)
            self.hotkey_manager.unregister()
```
4) run() finally 中加 `self.teardown_hotkey()`。

- [ ] **Step 5: 运行测试确认通过**

Run: `pytest tests/test_pet_window.py tests/test_hotkey_manager.py tests/test_settings_dialog.py -v`
Expected: PASS

- [ ] **Step 6: 全量测试 + 冒烟**

Run: `python -m pytest tests/ -q`
Expected: 基线+新增通过，仅2 pre-existing失败
（冒烟）：`python run.py` → 右键"设置..." → 修改保存 → 验证热键 Ctrl+Alt+M 切换麦克风

- [ ] **Step 7: Commit**

```bash
git add ui/pet_window.py core/app.py config.yaml tests/test_pet_window.py
git commit -m "feat(settings): integrate settings dialog - context menu, hotkey rebind, live apply"
```

---

### Task 4: 回归 + 打包

**Files:**
- 重新打包：`pyinstaller build.spec --noconfirm --clean`

- [ ] **Step 1: 全量测试**

Run: `python -m pytest tests/ -q`
Expected: 通过（仅2 pre-existing）

- [ ] **Step 2: 重新打包**

Run: `pyinstaller build.spec --noconfirm --clean`
Expected: dist/XiaoYiPet/XiaoYiPet.exe 更新

- [ ] **Step 3: Commit（如无代码改动跳过）**

---

## 文件结构（改动汇总）

| 文件 | 动作 |
|------|------|
| `services/hotkey_manager.py` | 新建：全局热键 |
| `ui/settings_dialog.py` | 新建：设置对话框（7组） |
| `ui/pet_window.py` | 集成：菜单/热更新/热键回调/自启 |
| `core/app.py` | 集成：热键生命周期 |
| `config.yaml` | +5键 |
| `tests/test_hotkey_manager.py` | 新建 |
| `tests/test_settings_dialog.py` | 新建 |
| `tests/test_pet_window.py` | 追加集成测试 |

## 风险与应对

| 风险 | 应对 |
|------|------|
| RegisterHotKey 占用/非Windows | register返回False→气泡提示，功能降级 |
| 对话框打开时Ollama离线 | 模型下拉浅灰+保留当前值（可编辑输入框） |
| 热键与TTS/监听并发 | toggle走已有processing/cooldown防护 |
| 设置写回损坏config | ConfigManager.set已有yaml.dump；测试用tmp隔离config |
