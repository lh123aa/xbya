"""
PySide6宠物窗口与状态机测试
"""

import pytest
import sys
import json
import time
from pathlib import Path
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt, QPoint, QPointF, QEvent
from PySide6.QtGui import QMouseEvent

# 添加项目根目录到系统路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from ui.state_machine import PetStateMachine
import ui.pet_window as pw
from ui.pet_window import PetWindow


@pytest.fixture(scope="module")
def qapp():
    """QApplication单例，只创建一次"""
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def pet_window(qapp):
    """创建并清理PetWindow"""
    win = PetWindow()
    yield win
    win.timer.stop()
    win.close()
    win.deleteLater()


class TestPetStateMachine:
    """状态机测试（纯Python逻辑，无QApplication依赖）"""

    def test_initial_state(self):
        """测试初始状态"""
        sm = PetStateMachine()
        assert sm.current_state == "idle"
        assert sm.state_time == 0.0
        assert sm.state_duration >= 8.0  # 随机 8~15 秒
        assert sm.state_duration <= 15.0
        assert sm.emotion == "neutral"

    def test_update_within_duration(self):
        """测试 duration 内不切换状态"""
        sm = PetStateMachine()
        assert sm.update(1.0) is None
        assert sm.current_state == "idle"
        assert sm.state_time == pytest.approx(1.0)

    def test_update_after_duration_transitions(self):
        """测试超过 duration 后切换状态"""
        sm = PetStateMachine()
        # 使用极大的 delta 确保超过 duration + cooldown
        sm._last_transition = 0.0  # 重置冷却
        new_state = sm.update(100.0)
        assert new_state is not None
        # 新状态在 idle 的可达状态集中（含新增状态）
        valid = set(sm.STATES["idle"]["next"]) | {"idle"}
        assert new_state in valid
        assert sm.current_state == new_state
        assert sm.state_time == 0.0

    def test_transition_resets_duration(self):
        """测试切换后重置计时器（各状态有独立范围）"""
        sm = PetStateMachine()
        sm._last_transition = 0.0
        sm.update(100.0)
        assert sm.state_time == 0.0
        # 新的 duration 应在对应状态的范围内（至少 > 2 秒）
        assert sm.state_duration >= 2.0

    def test_trigger_state(self):
        """测试强制触发状态"""
        sm = PetStateMachine()
        sm.trigger_state("happy")
        assert sm.current_state == "happy"
        assert sm.state_time == 0.0
        # duration 由 _get_state_duration 决定（happy: 3~6秒）
        assert 3.0 <= sm.state_duration <= 6.0

    def test_set_emotion(self):
        """测试设置情绪"""
        sm = PetStateMachine()
        assert sm.emotion == "neutral"
        sm.set_emotion("happy")
        assert sm.emotion == "happy"
        sm.set_emotion("sad")
        assert sm.emotion == "sad"
        sm.set_emotion("angry")
        assert sm.emotion == "angry"

    def test_all_states_can_reach_idle(self):
        """测试所有非idle状态最终能回到idle（通过多轮转移）"""
        non_idle = ["wander", "sleep", "happy", "stare", "dance",
                    "talk", "sad", "listen", "think", "angry", "surprise", "love"]
        for state in non_idle:
            sm = PetStateMachine()
            sm.trigger_state(state, duration=0.1)
            # 多次 update 直到找到 idle 或超限
            found_idle = False
            for _ in range(50):
                # 每轮重置冷却 + 确保超过duration
                sm._last_transition = 0.0
                sm.state_time = 100.0
                result = sm.update(0.01)
                if result == "idle":
                    found_idle = True
                    break
            assert found_idle, f"State {state} never reached idle in 50 steps"

    def test_emotion_does_not_mutate_shared_weights(self):
        """测试情绪加成不污染共享权重表"""
        original = list(sm_cls.STATES["idle"]["weight"])
        sm = PetStateMachine()
        sm.set_emotion("happy")
        sm._last_transition = 0.0
        sm.update(100.0)
        assert sm_cls.STATES["idle"]["weight"] == original

    def test_interaction_triggers(self):
        """测试交互事件触发正确状态"""
        sm = PetStateMachine()
        # 点击
        state = sm.on_click()
        assert state in ["pat", "poke", "happy", "surprise"]
        assert sm.current_state == state
        # 双击
        state = sm.on_double_click()
        assert state == "love"
        assert sm.emotion == "happy"
        # 打断
        state = sm.on_interrupt()
        assert state in ["angry", "surprise", "stare"]


# 保存类引用供测试用
sm_cls = PetStateMachine


class TestPetWindow:
    """PetWindow测试（需要QApplication）"""

    def test_pet_window_init(self, pet_window):
        """测试初始化"""
        win = pet_window
        assert win.bubble_text == ""
        assert win.bubble_timer == 0
        assert win.state_machine.current_state == "idle"
        assert win.dragging is False
        assert win.app is None
        assert win.width() == 300
        assert win.height() == 350

    def test_window_flags(self, pet_window):
        """测试窗口标志：无边框、置顶、工具窗口"""
        flags = pet_window.windowFlags()
        assert flags & Qt.FramelessWindowHint
        assert flags & Qt.WindowStaysOnTopHint
        assert flags & Qt.Tool

    def test_set_app(self, pet_window):
        """测试set_app"""
        app_mock = object()
        pet_window.set_app(app_mock)
        assert pet_window.app is app_mock

    def test_show_bubble(self, pet_window, monkeypatch):
        """测试显示气泡"""
        win = pet_window
        win.show_bubble("你好呀", 3000)
        assert win.bubble_text == "你好呀"
        assert win.bubble_timer == 3000

        # tick不消耗完气泡
        win.last_time = 1000.0
        monkeypatch.setattr(pw.time, "time", lambda: 1000.5)
        win.tick()
        assert win.bubble_text == "你好呀"

    def test_bubble_expires_after_duration(self, pet_window, monkeypatch):
        """测试气泡到期消失"""
        win = pet_window
        win.show_bubble("你好呀", 500)
        win.last_time = 1000.0
        monkeypatch.setattr(pw.time, "time", lambda: 1001.5)
        win.tick()
        assert win.bubble_timer <= 0
        assert win.bubble_text == ""

    def test_set_state_syncs_animation_and_state_machine(self, pet_window):
        """测试set_state同步状态机与动画控制器"""
        win = pet_window
        win.set_state("happy")
        assert win.state_machine.current_state == "happy"
        # duration 由 _get_state_duration 决定（happy: 3~6秒）
        assert 3.0 <= win.state_machine.state_duration <= 6.0
        assert win.anim_controller.current_state == "happy"

    def test_load_pet_updates_window_size(self, pet_window, tmp_path):
        """测试load_pet通过controller加载并按尺寸调整窗口"""
        win = pet_window
        pet_dir = tmp_path / "cat"
        pet_dir.mkdir()
        manifest = {"name": "测试猫", "size": [100, 120], "animations": {}}
        with open(pet_dir / "manifest.json", "w", encoding="utf-8") as f:
            json.dump(manifest, f)

        win.anim_controller.resource_path = str(tmp_path)
        win.load_pet("cat")

        assert win.anim_controller.pet_name == "cat"
        assert win.anim_controller.get_size() == (100, 120)
        assert win.width() == 200
        assert win.height() == 220


class TestVoiceInteraction:
    """语音交互测试"""

    def test_double_click_shows_love(self, pet_window):
        """双击触发 love 动效 + 爱心气泡"""
        win = pet_window
        win.app = object()
        win.mouseDoubleClickEvent(None)
        assert win.bubble_text == "❤️ 嘿嘿~"
        assert win.bubble_timer == 2500
        assert win.state_machine.current_state == "love"


class TestVoiceMonitor:
    """常驻监听集成测试（不启动真实麦克风：注入FakeMic）"""

    def test_start_voice_monitor_starts_listening(self, qapp, monkeypatch):
        from ui.pet_window import PetWindow
        win = PetWindow()
        started = {}

        class FakeMic:
            def __init__(self):
                self.started = False
                self.stop = False

            def is_available(self):
                return True

            def listen_standby(self, callback, on_start=None, **kw):
                self.started = True
                started["cb"] = callback
                return True

            def stop_listen_standby(self):
                self.stop = True

            def is_listening(self):
                return self.started

        monkeypatch.setattr("services.microphone_service._microphone_service", FakeMic())
        assert win.start_voice_monitor() is True
        assert started, "listen_standby应被调用"

    def test_should_ignore_short_text(self, qapp):
        """短文本应静默忽略"""
        from ui.pet_window import PetWindow
        win = PetWindow()
        assert win._should_ignore("嗯") is True
        assert win._should_ignore("") is True
        assert win._should_ignore(None) is True
        assert win._should_ignore("你好呀") is False

    def test_repeated_invalid_cooldown_after_3(self, qapp):
        """连续3次无效后进入5秒静默"""
        from ui.pet_window import PetWindow
        win = PetWindow()
        win._recent_invalid = 0
        win._echo_cooldown_until = 0.0
        for _ in range(3):
            win._handle_invalid()
        assert win._echo_cooldown_until > 0, "第3次无效后应设置静默时间戳"
        assert win._recent_invalid == 0, "计数应重置"

    def test_speech_captured_processing_guard(self, qapp):
        """处理中时忽略新语音"""
        from ui.pet_window import PetWindow
        win = PetWindow()
        win._processing = True
        called = []
        win._voice_pipeline = lambda p: called.append(1)
        win._on_speech_captured("/tmp/fake.wav")
        assert called == [], "处理中不应再触发管线"

    def test_speech_captured_echo_cooldown_suppressed(self, qapp):
        """TTS回声冷却期间捕获的说话应静默忽略，不启动管线"""
        from ui.pet_window import PetWindow
        win = PetWindow()
        win._echo_cooldown_until = time.time() + 10
        win._processing = False
        called = []
        win._voice_pipeline = lambda p: called.append(1)
        win._on_speech_captured("/tmp/fake_echo.wav")
        assert called == [], "冷却期间不应启动管线"
        assert win._processing is False, "冷却分支不应置processing"

    def test_short_text_when_transcribed_stays_silent(self, qapp, monkeypatch):
        """转写结果过短(如"嗯")时应静默：不弹气泡、不走LLM、不播报"""
        from ui.pet_window import PetWindow
        win = PetWindow()

        class FakeApp:
            def transcribe(self, wav_path):
                return "嗯"

            def chat(self, text, context=None):
                raise AssertionError("短文本不应调用LLM")

            def speak(self, text):
                raise AssertionError("短文本不应播报")

        invalid_calls = []
        win.app = FakeApp()
        monkeypatch.setattr(win, "_handle_invalid", lambda: invalid_calls.append(1))
        win._voice_pipeline("/tmp/fake.wav")
        assert invalid_calls == [1], "_handle_invalid应被调用"
        assert win.bubble_text == "", "不应弹气泡"
        assert win.bubble_timer == 0

    def test_drag_moves_window(self, pet_window):
        """仅拖拽回归：press→move窗口跟随→release，全程无单击/监听副作用

        注意：窗口必须先放在屏幕中部。贴边状态下点击会先触发"弹出"（_unsnap）
        并早退，不会进入拖拽分支——那是设计行为，不是缺陷。
        """
        win = pet_window
        geo = win._get_screen_geometry()
        # 居中放置，远离任何屏幕边缘
        start_x = geo.left() + max(60, (geo.width() - win.width()) // 2)
        start_y = geo.top() + max(60, (geo.height() - win.height()) // 2)
        win.move(start_x, start_y)
        assert win._is_edge_snapped() is False, "测试前置条件：窗口不应处于贴边状态"

        dpr = win.devicePixelRatioF()

        press = QMouseEvent(
            QEvent.Type.MouseButtonPress,
            QPointF(20.0, 30.0), QPointF(100.0, 100.0),
            Qt.LeftButton, Qt.LeftButton, Qt.NoModifier,
        )
        win.mousePressEvent(press)
        assert win.dragging is True
        # drag_offset 记录的是按下时的全局鼠标位置（move 时用 global - offset 推新窗口位置）
        assert win.drag_offset == QPoint(int(100 * dpr), int(100 * dpr))

        move = QMouseEvent(
            QEvent.Type.MouseMove,
            QPointF(70.0, 80.0), QPointF(150.0, 120.0),
            Qt.NoButton, Qt.LeftButton, Qt.NoModifier,
        )
        win.mouseMoveEvent(move)
        expected_x = int(150 * dpr) - win.drag_offset.x()
        expected_y = int(120 * dpr) - win.drag_offset.y()
        assert win.pos() == QPoint(expected_x, expected_y), "拖拽中窗口位置应跟随全局位置"
        assert win.dragging is True, "move事件不应结束拖拽"

        release = QMouseEvent(
            QEvent.Type.MouseButtonRelease,
            QPointF(70.0, 80.0), QPointF(150.0, 120.0),
            Qt.LeftButton, Qt.NoButton, Qt.NoModifier,
        )
        win.mouseReleaseEvent(release)
        assert win.dragging is False

    def test_click_when_edge_snapped_unsnaps_instead_of_dragging(self, pet_window):
        """贴边状态下按下应先弹出，而不是进入拖拽"""
        win = pet_window
        geo = win._get_screen_geometry()
        win.move(geo.left(), geo.top())
        assert win._is_edge_snapped() is True

        press = QMouseEvent(
            QEvent.Type.MouseButtonPress,
            QPointF(20.0, 30.0), QPointF(100.0, 100.0),
            Qt.LeftButton, Qt.LeftButton, Qt.NoModifier,
        )
        win.mousePressEvent(press)
        assert win.dragging is False, "贴边点击应走弹出分支，不进入拖拽"


class TestSettingsIntegration:
    """设置集成：右键菜单 + 热键 + 热更新"""

    def test_context_menu_has_settings(self, qapp):
        """右键菜单包含设置项"""
        from ui.pet_window import PetWindow
        win = PetWindow()
        # 不能真正弹菜单（阻塞）——检查方法存在
        assert hasattr(win, "open_settings")
        assert hasattr(win, "apply_settings")
        assert hasattr(win, "toggle_voice_monitor")

    def test_toggle_voice_monitor_stops_and_starts(self, qapp, monkeypatch):
        """toggle切换监听状态并气泡提示"""
        from ui.pet_window import PetWindow
        win = PetWindow()
        state = {"started": 0, "stopped": 0}

        class FakeMic:
            def listen_standby(self, *a, **k):
                state["started"] += 1
                return True

            def stop_listen_standby(self):
                state["stopped"] += 1

            def is_available(self):
                return True

        monkeypatch.setattr("services.microphone_service._microphone_service", FakeMic())
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

    def test_start_voice_monitor_listening_false_syncs_flag(self, qapp, tmp_path):
        """config voice.listening=false 时 start_voice_monitor 早退并同步 _monitor_enabled"""
        import yaml
        from types import SimpleNamespace
        from core.config_manager import ConfigManager
        from ui.pet_window import PetWindow

        p = tmp_path / "config.yaml"
        p.write_text(yaml.safe_dump({"voice": {"listening": False}}))
        win = PetWindow()
        win.app = SimpleNamespace(config_manager=ConfigManager(str(p)))
        assert win._monitor_enabled is True
        assert win.start_voice_monitor() is False
        assert win._monitor_enabled is False, "早退分支必须同步 _monitor_enabled"

    def test_apply_settings_hotkey_reregister(self, qapp, tmp_path):
        """保存后热键按新配置重注册（listening=false 同时同步 _monitor_enabled）"""
        import yaml
        from core.config_manager import ConfigManager
        from ui.pet_window import PetWindow

        cfg_path = tmp_path / "config.yaml"
        cfg_path.write_text(yaml.safe_dump({
            "voice": {"listening": False, "hotkey_enabled": True, "hotkey_toggle": "Ctrl+Alt+P"},
            "system": {"autostart": False},
            "ui": {"always_on_top": True, "pet_size": 300},
        }))
        cm = ConfigManager(str(cfg_path))

        calls = []

        class FakeHK:
            def __init__(self, *a, **k):
                pass

            def register(self, combo):
                calls.append(combo)
                return True

            def unregister(self):
                calls.append("unreg")

            def is_registered(self):
                return True

            def set_on_hotkey(self, cb):
                pass

        class FakeApp:
            config_manager = cm
            hotkey_manager = FakeHK()

        win = PetWindow()
        win.app = FakeApp()
        win.apply_settings()
        # 真实走 rebind 路径：先 unregister，再按配置中的 hotkey_toggle 重新 register
        assert calls == ["unreg", "Ctrl+Alt+P"], f"热键重注册序列错误: {calls}"
        # voice.listening=false 时监听应关闭且标志同步
        assert win._monitor_enabled is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
