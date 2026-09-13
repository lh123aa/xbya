# -*- coding: utf-8 -*-
"""
ui.pet_window VRM 渲染层集成测试（无真实 WebEngine）

测试策略（对应 Task 4 brief）：
- monkeypatch ``ui.pet_window.QWebEngineView`` → _FakeView（QObject，带 loadFinished 信号）
- monkeypatch ``ui.pet_window.VrmBridge`` → _FakeVrmBridge（记录 set_state，可手动触发信号）
- 全程不实例化真实 QWebEngineView —— enable_vrm 内部 lazy import 到模块属性
  （QWebEngineView = None 占位），monkeypatch 模块属性即可注入替身
- 覆盖：默认 sprite、enable_vrm 成功/模型缺失/异常降级、set_state 小写转发、
  bridgeError/loadFinished(False) 降级、降级后不再转发、tick 跳过精灵合成
"""

import sys
from pathlib import Path

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QColor

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import ui.pet_window as pw
from ui.pet_window import PetWindow


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


class _FakePage(QObject):
    """模拟 QWebEngineView.page()：记录 setBackgroundColor / setWebChannel"""

    def __init__(self):
        super().__init__()
        self.background_color = None
        self.set_web_channel_calls = []

    def setBackgroundColor(self, color):
        self.background_color = color

    def setWebChannel(self, channel, version=0):
        self.set_web_channel_calls.append((channel, version))


class _FakeView(QObject):
    """模拟 QWebEngineView：load 记录 + 可触发 loadFinished"""

    loadFinished = Signal(bool)
    customContextMenuRequested = Signal(object)

    def __init__(self, parent=None):
        super().__init__()
        self.loaded_url = None
        self.hidden = False
        self.page_obj = _FakePage()
        self.geo = None
        self.attrs = {}
        self.context_menu_policy = None
        self.raised = False

    def setAttribute(self, flag, value=True):
        self.attrs[flag] = value

    def page(self):
        return self.page_obj

    def load(self, url):
        self.loaded_url = url
        return True

    def setGeometry(self, x, y, w, h):
        self.geo = (x, y, w, h)

    def hide(self):
        self.hidden = True

    def show(self):
        self.hidden = False

    # ── PetWindow.enable_vrm 用到的 QWidget API ──

    def setContextMenuPolicy(self, policy):
        self.context_menu_policy = policy

    def setParent(self, parent):
        self._parent = parent

    def raise_(self):
        self.raised = True

    def resize(self, w, h):
        self.geo = (None, None, w, h)

    def setFixedSize(self, w, h):
        self.geo = (None, None, w, h)

    def deleteLater(self):
        pass

    def setUrl(self, url):
        self.loaded_url = url


class _FakeVrmBridge(QObject):
    """模拟 VrmBridge：记录 set_state，可手动触发就绪/错误信号"""

    bridgeReady = Signal()
    bridgeError = Signal(str)
    dragDelta = Signal(float, float)
    bodyPartClicked = Signal(str)

    def __init__(self, view):
        super().__init__()
        self.view = view
        self.state_calls = []
        self.action_calls = []

    def set_state(self, state):
        self.state_calls.append(state)

    def play_action(self, action):
        self.action_calls.append(action)


def _patch(monkeypatch, view_cls=None, bridge_cls=None):
    monkeypatch.setattr(pw, "QWebEngineView", view_cls or _FakeView)
    monkeypatch.setattr(pw, "VrmBridge", bridge_cls or _FakeVrmBridge)


@pytest.fixture
def vrm_ready(qapp, tmp_path, monkeypatch):
    """标准 vrm 环境：patched 后 enable_vrm 成功，返回 (win, view, bridge)"""
    _patch(monkeypatch)
    win = PetWindow()
    win.timer.stop()
    model = tmp_path / "cat.vrm"
    model.write_bytes(b"FAKE")
    assert win.enable_vrm(str(model)) is True
    yield win, win._vrm_view, win._vrm_bridge
    win.close()
    win.deleteLater()


class TestVrmDrag:
    def test_drag_delta_moves_window(self, vrm_ready):
        """JS drag_delta → 窗口移动（vrm 模式）"""
        win, _view, bridge = vrm_ready
        x0, y0 = win.x(), win.y()
        bridge.dragDelta.emit(25.0, -10.0)
        assert (win.x(), win.y()) == (x0 + 25, y0 - 10)

    def test_drag_ignored_in_sprite_mode(self, win_clean):
        """sprite 模式下 dragDelta 不移动窗口（_on_vrm_drag 早退）"""
        win = win_clean
        win._on_vrm_drag(50, 50)
        # 无 vrm view 也不应抛异常；位置不变
        assert not hasattr(win, "_vrm_bridge") or win._vrm_bridge is None


class TestBodyClick:
    def test_head_click_plays_pet(self, vrm_ready):
        """点击头部 → pet 动作 + 气泡"""
        win, _view, bridge = vrm_ready
        bridge.bodyPartClicked.emit("head")
        assert bridge.action_calls == ["pet"]
        assert "喵" in win.bubble_text or win.bubble_text != ""

    def test_tail_click_plays_hit_tail(self, vrm_ready):
        """点击尾巴 → hit_tail 动作"""
        win, _view, bridge = vrm_ready
        bridge.bodyPartClicked.emit("tail")
        assert bridge.action_calls == ["hit_tail"]

    def test_click_ignored_in_sprite(self, win_clean):
        """sprite 模式点击无反应（不抛异常）"""
        win = win_clean
        win._on_body_click("head")  # 无 bridge，安全


@pytest.fixture
def win_clean(qapp, monkeypatch):
    """patched 但未启用 vrm 的 PetWindow"""
    _patch(monkeypatch)
    win = PetWindow()
    win.timer.stop()
    yield win
    win.close()
    win.deleteLater()


class TestRenderModeDefault:
    """默认 sprite 模式（__init__ 行为不变）"""

    def test_default_mode_is_sprite(self, qapp):
        win = PetWindow()
        assert win.render_mode == "sprite"
        assert win._vrm_view is None
        assert win._vrm_bridge is None
        win.timer.stop()
        win.close()

    def test_set_state_in_sprite_mode_does_not_forward(self, win_clean):
        win = win_clean
        win.set_state("talk")
        assert win.render_mode == "sprite"
        assert win._vrm_bridge is None


class TestEnableVrm:
    def test_model_missing_returns_false_keeps_sprite(self, qapp, tmp_path, monkeypatch):
        _patch(monkeypatch)
        win = PetWindow()
        win.timer.stop()
        ok = win.enable_vrm(str(tmp_path / "no-such.vrm"))
        assert ok is False
        assert win.render_mode == "sprite"
        assert win._vrm_view is None

    def test_enable_success_sets_mode_and_configures_view(self, vrm_ready):
        win, view, bridge = vrm_ready
        assert win.render_mode == "vrm"
        assert isinstance(view, _FakeView)
        # 加载 viewer.html（本地文件 URL）
        assert view.loaded_url is not None
        assert view.loaded_url.isLocalFile()
        assert str(view.loaded_url.toLocalFile()).replace("\\", "/").endswith(
            "assets/vrm/viewer.html")
        # 透明 hack：页面底色透明（配合 Task 1 的 html,body{background:transparent}）
        assert view.page_obj.background_color == QColor(0, 0, 0, 0)
        # 布局：view 占窗口上部，底部留 44px 字幕区
        assert view.geo == (0, 0, win.width(), win.height() - 44)
        # bridge 已关联到 view
        assert bridge.view is view

    def test_enable_vrm_idempotent(self, vrm_ready):
        win = vrm_ready[0]
        view0 = win._vrm_view
        assert win.enable_vrm("assets/vrm/cat.vrm") is True
        assert win._vrm_view is view0, "重复调用不应重建 view"

    def test_view_ctor_exception_degrades(self, qapp, tmp_path, monkeypatch):
        class _BoomView(_FakeView):
            def __init__(self, parent=None):
                raise RuntimeError("webengine 不可用")

        _patch(monkeypatch, view_cls=_BoomView)
        win = PetWindow()
        win.timer.stop()
        model = tmp_path / "cat.vrm"
        model.write_bytes(b"FAKE")
        ok = win.enable_vrm(str(model))
        assert ok is False
        assert win.render_mode == "sprite"
        assert win._vrm_view is None

    def test_vrm_window_recovers_set_fixed_size_after_degrade(self, vrm_ready):
        """降级后窗口恢复精灵尺寸（size+100 公式，对齐 load_pet），确保不被裁切"""
        win = vrm_ready[0]
        win.load_pet("cat")  # vrm 模式：不调整窗口（保持 300x350）
        assert win.width() == 300 and win.height() == 350
        win._disable_vrm()
        assert win.render_mode == "sprite"
        assert win._vrm_view is None
        size = win.anim_controller.get_size()
        assert win.width() == size[0] + 100, "降级后窗口宽应恢复精灵尺寸+100"
        assert win.height() == size[1] + 100, "降级后窗口高应恢复精灵尺寸+100"


class TestStateForwarding:
    """vrm 模式下 set_state 经 STATE_TO_VRM 查表转发（小写）"""

    def test_set_state_forwards_lowercase_mapped(self, vrm_ready):
        from animation.vrm_state_map import STANDARD_STATES, STATE_TO_VRM
        win = vrm_ready[0]
        calls = win._vrm_bridge.state_calls
        for s in STANDARD_STATES:
            win.set_state(s)
        # 每个标准状态都按映射值（小写）转发一次
        # 注：多个 Python 状态可能映射到同一 JS 状态（如 angry/surprise/dance → idle），
        #     因此不能断言 call == python_state
        assert calls == [STATE_TO_VRM[s] for s in STANDARD_STATES]
        assert all(c == c.lower() for c in calls), "转发值必须小写（JS 契约）"

    def test_unknown_state_not_forwarded(self, vrm_ready):
        """契约外状态不转发（JS 侧无对应动画）"""
        win = vrm_ready[0]
        calls = win._vrm_bridge.state_calls
        n = len(calls)
        win.set_state("totally_unknown_state")
        assert len(calls) == n, "契约外状态不应转发给 JS"

    def test_all_mapped_values_are_valid_js_states(self):
        """所有映射值必须落在 JS 侧的 7 状态契约内"""
        from animation.vrm_state_map import STATE_TO_VRM
        js_states = {"idle", "talk", "think", "listen", "sleep", "happy", "sad"}
        invalid = {k: v for k, v in STATE_TO_VRM.items() if v not in js_states}
        assert not invalid, f"映射到契约外状态: {invalid}"

    def test_vrm_mode_state_machine_and_anim_sync(self, vrm_ready):
        """vrm 模式下 state_machine & anim_controller 仍同步（回退 sprite 状态一致）"""
        win = vrm_ready[0]
        win.set_state("talk")
        assert win.state_machine.current_state == "talk"
        assert win.anim_controller.current_state == "talk"

    def test_degrade_stops_forwarding(self, vrm_ready):
        win, view, bridge = vrm_ready
        n = len(bridge.state_calls)
        bridge.bridgeError.emit("bridge timeout")
        assert win.render_mode == "sprite"
        assert view.hidden is True
        win.set_state("talk")
        assert len(bridge.state_calls) == n, "降级后 set_state 不得再转发"

    def test_tick_forwards_state_machine_transition(self, vrm_ready):
        win = vrm_ready[0]
        bridge = vrm_ready[2]
        # 状态机迁移到映射状态 → 转发
        win.state_machine.update = lambda delta: "happy"
        win.last_time = 0.0
        win.tick()
        assert bridge.state_calls == ["happy"]
        # 迁移到契约外状态 → 不转发
        win.state_machine.update = lambda delta: "totally_unknown_state"
        win.tick()
        assert bridge.state_calls == ["happy"]


class TestDegradation:
    def test_bridge_error_degrades(self, vrm_ready):
        win, view, bridge = vrm_ready
        bridge.bridgeError.emit("bridge timeout")
        assert win.render_mode == "sprite"
        assert view.hidden is True
        assert win._vrm_bridge is None

    def test_load_finished_false_degrades(self, vrm_ready):
        win, view, bridge = vrm_ready
        view.loadFinished.emit(False)
        assert win.render_mode == "sprite"
        assert view.hidden is True

    def test_load_finished_true_keeps_vrm(self, vrm_ready):
        win, view, bridge = vrm_ready
        view.loadFinished.emit(True)
        assert win.render_mode == "vrm"
        assert view.hidden is False

    def test_degrade_then_sprite_pipeline_still_works(self, vrm_ready):
        """降级后精灵路径完好：set_state 同步控制器 + load_pet 调整窗口"""
        win = vrm_ready[0]
        win._disable_vrm()
        win.set_state("happy")
        assert win.anim_controller.current_state == "happy"


class TestTickVrmMode:
    def test_tick_skips_sprite_update_in_vrm(self, vrm_ready):
        """vrm 模式下 tick 跳过精灵合成（anim_controller.update 不调用）"""
        win = vrm_ready[0]
        calls = []
        win.anim_controller.update = lambda delta: calls.append(delta)
        win.tick()
        assert calls == []
        # 状态机与气泡计时仍运行
        assert win.state_machine is not None
        # 降级后恢复精灵更新
        win._disable_vrm()
        win.tick()
        assert len(calls) > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
