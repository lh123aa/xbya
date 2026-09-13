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
    # 模拟控件保存组合键（capture逻辑独立在控件/对话框类中，这里测保存与显示）
    widget.finish_capture("Ctrl+Alt+M")
    assert widget.text().endswith("Ctrl+Alt+M")


# ---------- 页签结构：标题改名 + 性能页并入系统信息页 ----------

def test_window_title_is_system_settings(qapp, cfg, monkeypatch):
    """标题应为「系统设置」，不再是「欣雅设置」"""
    monkeypatch.setattr("ui.settings_dialog.requests", None)
    from ui.settings_dialog import SettingsDialog
    dlg = SettingsDialog(cfg)
    assert dlg.windowTitle() == "系统设置"


def test_performance_tab_removed(qapp, cfg, monkeypatch):
    """原「⚡ 性能」页必须已被删除，不能只改名留着"""
    monkeypatch.setattr("ui.settings_dialog.requests", None)
    from ui.settings_dialog import SettingsDialog
    dlg = SettingsDialog(cfg)
    tabs = [dlg.tabs.tabText(i) for i in range(dlg.tabs.count())]
    assert not any("性能" in t for t in tabs), f"仍有性能页: {tabs}"
    # 五个页签，顺序固定
    assert tabs == ["🎤 语音", "🧠 AI 大脑", "🎀 外观", "⌨️ 快捷键", "ℹ️ 系统信息"]


def test_info_tab_contains_perf_controls(qapp, cfg, monkeypatch):
    """性能控件必须**真的在系统信息页里**（不是仅仅还存在）"""
    monkeypatch.setattr("ui.settings_dialog.requests", None)
    from ui.settings_dialog import SettingsDialog
    dlg = SettingsDialog(cfg)

    assert hasattr(dlg, "perf_combo2"), "性能档位控件丢失"
    assert hasattr(dlg, "_perf_plugin_label"), "插件状态控件丢失"

    # 结构判据：从控件往上走到页签，必须是「系统信息」那一页
    info_widget = None
    for i in range(dlg.tabs.count()):
        if "系统信息" in dlg.tabs.tabText(i):
            info_widget = dlg.tabs.widget(i)
    assert info_widget is not None, "找不到系统信息页"

    def ancestors(w):
        seen = []
        cur = w
        while cur is not None:
            seen.append(cur)
            cur = cur.parentWidget()
        return seen

    assert info_widget in ancestors(dlg.perf_combo2), \
        "性能档位控件不在系统信息页内"
    assert info_widget in ancestors(dlg._perf_plugin_label), \
        "插件状态控件不在系统信息页内"


def test_apply_still_writes_performance_mode(qapp, cfg, monkeypatch):
    """控件搬家后，保存链路不能被切断：apply 仍须写入性能档位"""
    monkeypatch.setattr("ui.settings_dialog.requests", None)
    from ui.settings_dialog import SettingsDialog
    dlg = SettingsDialog(cfg)
    dlg.perf_combo2.setCurrentText("high")
    assert dlg.apply_to_config() is True
    assert cfg.get("system.performance_mode") == "high"


def test_perf_widgets_load_from_config(qapp, cfg, monkeypatch):
    """合并后仍要正确回读配置（档位不能总是显示第一项）"""
    monkeypatch.setattr("ui.settings_dialog.requests", None)
    cfg.set("system.performance_mode", "medium")
    from ui.settings_dialog import SettingsDialog
    dlg = SettingsDialog(cfg)
    assert dlg.perf_combo2.currentText() == "medium"
