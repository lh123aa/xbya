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
        non_idle = ["happy", "stare", "dance",
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


class TestStuckInterruptFlag:
    """回归：打断标志必须在新一次语音处理时清除。

    实测故障：`app._interrupt_requested` 由"点击宠物/按 Ctrl+Alt+D"置 True，
    而当时唯一的清除点位于 `_speak_sentences` 的播放循环内部。若置位时**没有**
    正在播放，标志就永远停在 True —— 之后每段语音都在 ASR 之后撞上
    "ASR后检测到打断，跳过后续处理" 被丢弃。

    用户看到的现象：麦克风有触发、ASR 有结果、但**永远没有任何互动**。
    """

    def _make_win(self, qapp):
        from ui.pet_window import PetWindow
        return PetWindow()

    def test_pipeline_clears_stale_interrupt_flag(self, qapp, monkeypatch):
        """带着"陈旧的打断标志"进管线，标志应被清掉且继续往下走"""
        win = self._make_win(qapp)

        class FakeApp:
            def __init__(self):
                self._interrupt_requested = True   # ← 陈旧的打断标志
                self.transcribed = False
                self.chatted = False

            def transcribe(self, path):
                self.transcribed = True
                return "你好呀"

            def chat_stream(self, text, context=None):
                self.chatted = True
                return []

            def synthesize(self, s):
                return None

        app = FakeApp()
        win.app = app
        # FakeApp 没有 agent_stack → `_agent_enabled` 属性自动为 False，
        # 走纯 LLM 分支，避开 Agent 事件（该属性只读，不能赋值）

        win._voice_pipeline("dummy.wav")

        assert app.transcribed is True, "ASR 应该被执行"
        assert app._interrupt_requested is False, \
            "新一次语音处理必须先清除陈旧的打断标志"
        assert app.chatted is True, \
            "陈旧标志不该阻断后续链路（这正是'永远没互动'的根因）"

    def test_interrupt_during_asr_still_honored(self, qapp, monkeypatch):
        """反方向：ASR **期间**新产生的打断仍必须生效（不能一清了之）"""
        win = self._make_win(qapp)

        class FakeApp:
            def __init__(self):
                self._interrupt_requested = False
                self.chatted = False

            def transcribe(self, path):
                # 模拟"识别过程中用户按了打断"
                self._interrupt_requested = True
                return "你好呀"

            def chat_stream(self, text, context=None):
                self.chatted = True
                return []

        app = FakeApp()
        win.app = app
        # 同上：无 agent_stack，自动走纯 LLM 分支

        win._voice_pipeline("dummy.wav")

        assert app.chatted is False, \
            "识别期间产生的打断必须仍然阻断 LLM（这条保护不能被上面那条破坏）"


class TestEchoGuardTiming:
    """回归：回声屏蔽必须在**播放真正结束之后**才计时。

    实测故障：`_on_agent_result` 里把播报丢进后台线程后**立刻**设
    `_echo_cooldown_until`。而播报是异步的 —— 她念完 5~8 秒时冷却早过期了，
    于是**她自己的 TTS 被麦克风收回去当作用户的下一句**，表现为
    "自言自语"、并且把用户真正的语音挤掉。

    修法：把"设冷却"放进播放线程的 finally（`_arm_echo_guard`），
    并让**所有**播报路径都走它（ack / result / refine / confirm / llm）。
    """

    def test_guard_armed_after_playback_not_before(self, qapp, monkeypatch):
        """冷却必须在 _speak_sentences **返回之后**才被设置"""
        from ui.pet_window import PetWindow
        win = PetWindow()

        order = []

        def fake_speak(sentences):
            order.append("speak_start")
            import time as _t
            _t.sleep(0.15)            # 模拟播放耗时
            order.append("speak_end")

        monkeypatch.setattr(win, "_speak_sentences", fake_speak)
        win.app = None                 # 无 config_manager → 用默认冷却
        win._echo_tail_until = 0.0
        win._echo_cooldown_until = 0.0

        win._on_agent_result({"summary": "你好呀", "emotion": "happy"})

        # 等后台播放线程跑完
        import time as _t
        for _ in range(50):
            if "speak_end" in order:
                break
            _t.sleep(0.02)

        assert order[:2] == ["speak_start", "speak_end"], f"播放未完成: {order}"
        assert win._echo_tail_until > 0, "播放结束后必须挂上残响屏蔽"
        # 暂停机制已挡住播报期间的回声，冷却期只需覆盖残响（约 2 秒）
        assert win._echo_cooldown_until >= win._echo_tail_until, \
            "冷却应 ≥ 残响窗口"

    def test_all_playback_paths_arm_guard(self):
        """结构判据：所有播报路径都必须调用 _arm_echo_guard

        这条防的是"新加一条播报路径但忘了挂回声屏蔽" —— 实测已经漏过两次
        （`_on_agent_ack` 与 `_on_agent_refine`）。
        """
        import inspect
        from ui import pet_window as mod

        src = inspect.getsource(mod.PetWindow)
        # 每个"启动播报线程"的路径，都应出现 _arm_echo_guard
        for path_name in ("_on_agent_ack", "_on_agent_result",
                          "_on_agent_refine", "_on_agent_confirm"):
            fn = getattr(mod.PetWindow, path_name)
            body = inspect.getsource(fn)
            assert "_arm_echo_guard" in body, \
                f"{path_name} 播报后没有挂回声屏蔽（会导致她把自言自语当成用户说话）"


class TestVoiceMonitor:
    """常驻监听集成测试（不启动真实麦克风：注入FakeMic）"""

    def test_speak_sentences_must_resume_mic(self):
        """结构判据：_speak_sentences 的 finally 必须恢复麦克风

        这条防的是"暂停了麦克风但提前 return / 异常时漏掉 resume"——
        实测已经踩过（用户感知"完全没反应"）。
        """
        import inspect
        from ui import pet_window as mod

        src = inspect.getsource(mod.PetWindow._speak_sentences)
        # 必须在 finally 块中调用 resume_listening
        assert "finally:" in src, "_speak_sentences 没有 finally 兜底"
        assert "resume_listening" in src, "_speak_sentences 没有恢复麦克风"

    def test_speak_generation_guard_exists(self):
        """结构判据：恢复麦克风前必须校验播报代数

        根因（实测）：一次交互触发 ack→result→refine 三次播报，
        每次都在 finally 起一个 1.5s 延迟恢复线程。没有代数守卫时，
        先起的线程会在后续播报**还在说话时**打开麦克风 →
        拾到 TTS 回声 → 假录音 + 冷却 → 用户真实说话被挡掉。
        """
        import inspect
        from ui import pet_window as mod

        src = inspect.getsource(mod.PetWindow._speak_sentences)
        assert "_speak_generation" in src, \
            "_speak_sentences 缺少播报代数守卫（会导致提前恢复麦克风拾到回声）"
        assert "_my_generation" in src, "缺少本次代数快照"

    def test_mic_resume_clears_cooldown(self):
        """恢复监听必须清掉触发冷却

        否则"播报期间已彻底暂停"之后仍留着冷却窗口，
        用户在这段时间说话会被静默忽略（感知："她听不见我说话了"）。
        """
        import time as _t
        from services.microphone_service import MicrophoneService

        mic = MicrophoneService()
        mic._cooldown_until = _t.time() + 60.0   # 假装还有很长的冷却
        mic.pause_listening()
        mic.resume_listening()
        assert mic._cooldown_until == 0.0, \
            "resume_listening 必须清掉触发冷却，否则用户说话会被忽略"


class TestProcessingWatchdog:
    """处理态看门狗：ASR/LLM/TTS 任一阶段卡死时不能永久锁死。

    为什么必须有：`_processing=True` 期间所有新语音只入队不处理。
    若某个阶段挂死（ASR 推理线程卡住、网络请求不返回），
    标志会永久为 True → 用户感知是"她彻底不理我了"，
    而日志上只看到语音在正常入队 —— 典型的静默失败。
    """

    def test_watchdog_not_triggered_when_idle(self, pet_window):
        """空闲时看门狗不应做任何事"""
        pet_window._processing = False
        pet_window._processing_since = 0.0
        assert pet_window._recover_stuck_processing() is False

    def test_watchdog_not_triggered_within_limit(self, pet_window):
        """未超时不应解锁（否则正常的长任务会被误杀）"""
        import time as _t
        pet_window._processing = True
        pet_window._processing_since = _t.time() - 5.0     # 才 5 秒
        assert pet_window._recover_stuck_processing() is False
        assert pet_window._processing is True, "未超时不该解锁"

    def test_watchdog_unlocks_after_timeout(self, pet_window):
        """★ 核心：超过上限必须强制解锁，否则永久失聪"""
        import time as _t
        pet_window._processing = True
        pet_window._processing_since = (
            _t.time() - (pet_window._processing_watchdog_sec + 1))
        assert pet_window._recover_stuck_processing() is True
        assert pet_window._processing is False, "超时后必须解锁"
        assert pet_window._processing_since == 0.0, "解锁后应归零起点"

    def test_watchdog_also_clears_agent_busy(self, pet_window):
        """看门狗要同时解开 _agent_busy（两道闸可能一起卡住）"""
        import time as _t
        pet_window._processing = True
        pet_window._agent_busy = True
        pet_window._agent_busy_since = _t.time() - 999
        pet_window._processing_since = (
            _t.time() - (pet_window._processing_watchdog_sec + 1))
        assert pet_window._recover_stuck_processing() is True
        assert pet_window._agent_busy is False, "_agent_busy 也必须被解开"

    def test_start_pipeline_records_timestamp(self):
        """_start_pipeline 必须记录看门狗起点（否则看门狗永远不触发）"""
        import inspect
        from ui import pet_window as mod

        src = inspect.getsource(mod.PetWindow._start_pipeline)
        assert "_processing_since" in src, \
            "_start_pipeline 没有记录看门狗起点 → 卡死后永远不会解锁"

    def test_tick_calls_watchdog(self):
        """结构判据：tick 里必须真的调用看门狗（写了不调用等于没有）"""
        import inspect
        from ui import pet_window as mod

        src = inspect.getsource(mod.PetWindow)
        assert "self._recover_stuck_processing()" in src, \
            "看门狗方法存在但从未被调用"


class TestImmediateListenFeedback:
    """语音起始的即时反馈：解决"ASR 慢导致的看起来没反应"。

    ASR 在本机 8 核 CPU 上要 3~5 秒。这期间若界面毫无变化，
    用户会把"慢"感知成"没听见"。做法是在**检测到声音的瞬间**
    就切 listen 动效 + 弹气泡，把"有没有反应"与"反应内容"解耦。
    """

    def test_listening_feedback_method_exists(self):
        from ui.pet_window import PetWindow
        assert hasattr(PetWindow, "_show_listening_feedback"), \
            "缺少语音起始的即时反馈方法"

    def test_voice_start_triggers_feedback(self):
        """on_start 回调里必须调用即时反馈"""
        import inspect
        from ui import pet_window as mod

        src = inspect.getsource(mod.PetWindow.start_voice_monitor)
        assert "_show_listening_feedback" in src, \
            "检测到语音起始时没有给即时反馈（用户会以为没听见）"

    def test_feedback_debounced(self, pet_window):
        """连续语音不应反复刷新气泡（防抖）"""
        calls = []
        pet_window.show_bubble = lambda *a, **k: calls.append(a)
        # 连续调用两次：第二次应被防抖挡住
        pet_window._show_listening_feedback()
        pet_window._show_listening_feedback()
        assert len(calls) == 1, f"防抖失效，弹了 {len(calls)} 次气泡"

    def test_feedback_suppressed_while_speaking(self):
        """播报期间的气泡属于宠物自己说的话，不能被提示顶掉"""
        import inspect
        from ui import pet_window as mod

        src = inspect.getsource(mod.PetWindow.start_voice_monitor)
        assert "self._speaking" in src, \
            "即时反馈没有避开播报期（会顶掉宠物自己的字幕）"

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
        win._is_snapped_state = False  # 确保不在贴边状态

        press = QMouseEvent(
            QEvent.Type.MouseButtonPress,
            QPointF(20.0, 30.0), QPointF(100.0, 100.0),
            Qt.LeftButton, Qt.LeftButton, Qt.NoModifier,
        )
        win.mousePressEvent(press)
        assert win.dragging is True
        # 新拖拽模型：记录鼠标全局位置 + 窗口原始位置
        assert win._drag_mouse_start == QPoint(100, 100)
        assert win._drag_win_start == QPoint(start_x, start_y)

        move = QMouseEvent(
            QEvent.Type.MouseMove,
            QPointF(70.0, 80.0), QPointF(150.0, 120.0),
            Qt.NoButton, Qt.LeftButton, Qt.NoModifier,
        )
        win.mouseMoveEvent(move)
        # 新模型：窗口位置 = 窗口原始位置 + (当前鼠标全局 - 按下时鼠标全局)
        expected_x = start_x + (150 - 100)
        expected_y = start_y + (120 - 100)
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
        # 直接设置贴边标志（不再依赖位置判断）
        win._is_snapped_state = True

        press = QMouseEvent(
            QEvent.Type.MouseButtonPress,
            QPointF(20.0, 30.0), QPointF(100.0, 100.0),
            Qt.LeftButton, Qt.LeftButton, Qt.NoModifier,
        )
        win.mousePressEvent(press)
        assert win.dragging is False, "贴边点击应走弹出分支，不进入拖拽"
        assert win._is_snapped_state is False, "unsnap 应重置贴边标志"


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

    def test_apply_settings_keeps_sprite_aspect_for_tall_canvas(self, qapp, tmp_path):
        """非方形画布下保存设置，窗口必须保住精灵宽高比（不能压成正方形）。

        回归的是一个写死的 `setFixedSize(size + 100, size + 100)`：
        全身立绘画布 128×288，被塞进 227×227 的窗口后 ——
          · 角色从 y=0 画起，**脚被窗口底裁掉**
          · 44px 字幕条落在 y[186..221]，**重新压回角色身上**
        （正是 `_relayout_pet` 修掉的那个现象，从"保存设置"这条路径复发）

        判据用结构不变量而不是像素：画布必须**整体**落在字幕区之上。
        """
        import yaml
        from core.config_manager import ConfigManager
        from ui.pet_window import PetWindow, SUBTITLE_AREA

        cfg_path = tmp_path / "config.yaml"
        cfg_path.write_text(yaml.safe_dump({
            "voice": {"listening": False, "hotkey_enabled": False},
            "system": {"autostart": False},
            "ui": {"always_on_top": True, "pet_size": 127},
        }))
        cm = ConfigManager(str(cfg_path))

        class FakeApp:
            config_manager = cm
            hotkey_manager = None

        win = PetWindow()
        win.app = FakeApp()
        win.load_pet("xbya")                         # 真实资源：画布 128×288
        cw, ch = win.anim_controller.get_size()
        assert (cw, ch) == (128, 288), f"xbya 画布应为 128×288，实为 {cw}×{ch}"

        win._pet_size = 300                          # 造出"尺寸变了"的差异
        win.apply_settings()

        assert win.width() == 127 + 100, f"窗口宽应随 pet_size，实为 {win.width()}"
        expected_h = max(1, int(round(127 * ch / cw))) + 100
        assert win.height() == expected_h, (
            f"窗口高必须按精灵宽高比 {ch}/{cw} 算（应 {expected_h}），"
            f"实为 {win.height()} —— 写死正方形会把脚裁掉")

        # 结构不变量：画布整体在字幕区之上
        assert win.pet_y + ch <= win.height() - SUBTITLE_AREA, (
            f"画布底 {win.pet_y + ch} 越过了字幕区上沿 "
            f"{win.height() - SUBTITLE_AREA}：字幕会压住角色")

    def test_apply_settings_square_sprite_unchanged(self, qapp, tmp_path):
        """方形画布（旧角色）行为不变 —— 宽高都等于 pet_size + 100。

        这条是给上面那条改动加的反方向保护：按比例算高之后，
        128×128 的 cat 仍必须得到 227×227，不能多出 1px。
        """
        import yaml
        from core.config_manager import ConfigManager
        from ui.pet_window import PetWindow

        cfg_path = tmp_path / "config.yaml"
        cfg_path.write_text(yaml.safe_dump({
            "voice": {"listening": False, "hotkey_enabled": False},
            "system": {"autostart": False},
            "ui": {"always_on_top": True, "pet_size": 127},
        }))
        cm = ConfigManager(str(cfg_path))

        class FakeApp:
            config_manager = cm
            hotkey_manager = None

        win = PetWindow()
        win.app = FakeApp()
        win.load_pet("cat")                          # 真实资源：画布 128×128
        assert win.anim_controller.get_size() == (128, 128)

        win._pet_size = 300
        win.apply_settings()
        assert (win.width(), win.height()) == (227, 227), (
            f"方形画布应仍是 227×227，实为 {win.width()}×{win.height()}")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
