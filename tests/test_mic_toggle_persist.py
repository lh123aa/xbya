"""麦克风开关回归用例（D33）。

用户报告：「麦克风关了为啥还能听到声音」——她还听得见并回应。

根因（两处，都是"开关只改内存、闸门缺失"）：
1. `_toggle_mic(False)` 只设 `_monitor_enabled=False`，**不写 `config.voice.listening`**。
   之后任何一次「保存设置」（`apply_settings` 按 `voice.listening` 无条件重启监听）
   或**下一次启动**，都会把麦克风重新打开，而菜单仍显示"已关闭"。
2. `_on_speech_captured` **没有检查 `_monitor_enabled`** —— 只要底层流因为任何原因
   还活着，捕获到的语音就会被正常处理并回应。
"""
import pytest
from PySide6.QtWidgets import QApplication


class FakeConfigManager:
    """最小配置管理器：只实现 get/set，记录写入。"""

    def __init__(self, initial=None):
        self.data = dict(initial or {})
        self.writes = []

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.writes.append((key, value))
        self.data[key] = value


class FakeApp:
    def __init__(self, cm=None):
        self.config_manager = cm


@pytest.fixture
def win(qapp):
    from ui.pet_window import PetWindow
    w = PetWindow()
    yield w
    try:
        w.stop_voice_monitor()
    except Exception:
        pass
    w.close()
    w.deleteLater()


# ---------- 1. 关麦必须落盘 ----------

def test_toggle_mic_off_persists_to_config(win, monkeypatch):
    """★ 主缺陷①：菜单关麦必须写回 config.voice.listening=false。

    否则下一次 apply_settings / 重启会按配置里残留的 true 把麦克风打开。
    """
    cm = FakeConfigManager({"voice.listening": True})
    win.app = FakeApp(cm)
    win._monitor_mic = None                      # 不碰真实设备
    monkeypatch.setattr(win, "stop_voice_monitor", lambda: None)
    monkeypatch.setattr(win, "start_voice_monitor", lambda: True)

    win._toggle_mic(False)

    assert cm.get("voice.listening") is False, \
        "关麦必须落盘，否则重启/保存设置会把它重新打开"
    assert ("voice.listening", False) in cm.writes
    assert win._monitor_enabled is False


def test_toggle_mic_on_persists_to_config(win, monkeypatch):
    """开麦同样落盘（反方向保护）。"""
    cm = FakeConfigManager({"voice.listening": False})
    win.app = FakeApp(cm)
    win._monitor_mic = None
    monkeypatch.setattr(win, "stop_voice_monitor", lambda: None)
    monkeypatch.setattr(win, "start_voice_monitor", lambda: True)

    win._toggle_mic(True)

    assert cm.get("voice.listening") is True
    assert ("voice.listening", True) in cm.writes


def test_toggle_mic_no_redundant_write(win, monkeypatch):
    """值没变时不重复落盘（避免无谓写配置）。"""
    cm = FakeConfigManager({"voice.listening": False})
    win.app = FakeApp(cm)
    win._monitor_mic = None
    monkeypatch.setattr(win, "stop_voice_monitor", lambda: None)

    win._toggle_mic(False)

    assert cm.writes == [], f"值未变化不该写配置，实际写了 {cm.writes}"


def test_persist_failure_does_not_break_toggle(win, monkeypatch):
    """配置写失败只告警，开关本身仍要生效（不能因为落盘失败就弄坏开关）。"""
    class Boom(FakeConfigManager):
        def set(self, key, value):
            raise RuntimeError("disk full")

    cm = Boom({"voice.listening": True})
    win.app = FakeApp(cm)
    win._monitor_mic = None
    monkeypatch.setattr(win, "stop_voice_monitor", lambda: None)

    win._toggle_mic(False)                        # 不应抛异常

    assert win._monitor_enabled is False, "落盘失败也必须把开关关掉"


def test_toggle_mic_without_config_manager(win, monkeypatch):
    """没有 config_manager 时不崩（单测/降级路径）。"""
    win.app = FakeApp(None)
    win._monitor_mic = None
    monkeypatch.setattr(win, "stop_voice_monitor", lambda: None)
    win._toggle_mic(False)
    assert win._monitor_enabled is False


# ---------- 2. 关麦后捕获的语音必须丢弃 ----------

def test_speech_captured_dropped_when_mic_off(win, monkeypatch):
    """★ 主缺陷②：麦克风已关闭时，捕获到的语音不得进入处理链路。"""
    deleted = []
    processed = []
    monkeypatch.setattr(win, "_delete_wav", lambda p: deleted.append(p))
    monkeypatch.setattr(win, "_start_pipeline", lambda p: processed.append(p))

    win._monitor_enabled = False
    win._on_speech_captured("/tmp/fake.wav")

    assert deleted == ["/tmp/fake.wav"], "关麦时捕获的音频必须被丢弃"
    assert processed == [], "关麦时绝不能进入处理链路（否则用户感知是还能听见）"
    assert win._pending_wavs == [], "也不该进待处理队列"


def test_speech_captured_processed_when_mic_on(win, monkeypatch):
    """反方向保护：开麦时正常处理（确认没把闸门修过头）。"""
    deleted = []
    processed = []
    monkeypatch.setattr(win, "_delete_wav", lambda p: deleted.append(p))
    monkeypatch.setattr(win, "_start_pipeline", lambda p: processed.append(p))
    monkeypatch.setattr(win, "_speaking", False, raising=False)
    monkeypatch.setattr(win, "_processing", False, raising=False)
    monkeypatch.setattr(win, "_cooldown_until", 0.0, raising=False)

    win._monitor_enabled = True
    win._on_speech_captured("/tmp/fake.wav")

    assert deleted == [], f"开麦时不该丢弃，实际删了 {deleted}"
    assert processed == ["/tmp/fake.wav"], "开麦时应正常进入处理"


def test_mic_off_drops_even_while_speaking(win, monkeypatch):
    """关麦时即使是"播报期间捕获"也不能入队 —— 入队等于以后还会回应。"""
    monkeypatch.setattr(win, "_delete_wav", lambda p: None)
    monkeypatch.setattr(win, "_speaking", True, raising=False)

    win._monitor_enabled = False
    win._on_speech_captured("/tmp/fake.wav")

    assert win._pending_wavs == [], "关麦时不得入待处理队列"


# ---------- 3. 热键路径与菜单一致 ----------

def test_hotkey_toggle_goes_through_same_path(win, monkeypatch):
    """★ 热键切换必须与菜单同一条路径（都落盘），否则两条路行为不一致。"""
    cm = FakeConfigManager({"voice.listening": True})
    win.app = FakeApp(cm)
    win._monitor_mic = None
    monkeypatch.setattr(win, "stop_voice_monitor", lambda: None)
    monkeypatch.setattr(win, "start_voice_monitor", lambda: True)

    # 当前"监听中" → 热键应关掉，并落盘
    win._monitor_enabled = True
    win.toggle_voice_monitor()

    assert win._monitor_enabled is False
    assert cm.get("voice.listening") is False, \
        "热键关麦也必须落盘（与菜单行为一致）"
