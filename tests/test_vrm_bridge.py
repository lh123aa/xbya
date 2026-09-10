# -*- coding: utf-8 -*-
"""
ui.vrm_bridge 单元测试（无真实 WebEngine）

测试策略：
- 假 view（FakeView/FakePage 模拟 QWebEngineView，记录 setWebChannel 调用）
- 使用真实 QWebChannel（不启动页面/事件循环，仅对象注册）
- 覆盖：指令缓存与重放、event 解析、信号发射、就绪超时防护
"""

import json
import sys
from pathlib import Path

import pytest

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from ui.vrm_bridge import VrmBridge


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


class FakePage:
    """模拟 QWebEngineView.page()：记录 setWebChannel 调用"""

    def __init__(self):
        self.web_channel = None
        self.set_web_channel_calls = []

    def setWebChannel(self, channel, version=0):
        self.set_web_channel_calls.append((channel, version))
        self.web_channel = channel


class FakeView:
    """模拟 QWebEngineView"""

    def __init__(self):
        self.page_obj = FakePage()

    def page(self):
        return self.page_obj


@pytest.fixture
def make_bridge(qapp):
    """构造 VrmBridge（FakeView + 真实 QWebChannel）

    收尾很重要（D7）：桥持有真实 QWebChannel 与一个**运行中的超时 QTimer**。
    若不在用例结束时显式销毁，这些对象只能等解释器 GC 在任意时刻回收；
    测试会话里约 30 个桥同时挂着 30s 定时器，一旦某个已析构对象的定时器/队列事件
    在后续用例的嵌套 QEventLoop 里被投递，就会触发访问违例（0xC0000005）。
    这里改为确定性地 close + deleteLater + 冲刷事件队列。
    """
    created = []

    def _make(timeout_ms=30000):
        view = FakeView()
        bridge = VrmBridge(view, timeout_ms=timeout_ms)
        created.append(bridge)
        return bridge, view

    yield _make

    for bridge in created:
        try:
            bridge._timer.stop()
        except Exception:
            pass
        try:
            bridge.deleteLater()
        except Exception:
            pass
    try:
        qapp.processEvents()
    except Exception:
        pass


def echo_emits(bridge):
    emitted = []
    bridge.command.connect(emitted.append)
    return emitted


class TestChannelSetup:
    """QWebChannel 服务端注册与超时定时器配置"""

    def test_registers_petbridge_object(self, make_bridge):
        bridge, view = make_bridge()
        registered = bridge._channel.registeredObjects()
        assert "petBridge" in registered, "必须注册 petBridge 对象（bridge.js 契约）"
        assert registered["petBridge"] is bridge

    def test_channel_attached_to_page(self, make_bridge):
        bridge, view = make_bridge()
        assert view.page_obj.web_channel is bridge._channel
        assert view.page_obj.set_web_channel_calls == [(bridge._channel, 0)], \
            "setWebChannel 应被调用一次"

    def test_timeout_timer_starts_single_shot(self, make_bridge):
        bridge, _ = make_bridge(timeout_ms=30000)
        assert bridge._timer.isSingleShot() is True
        assert bridge._timer.interval() == 30000
        assert bridge._timer.isActive() is True

    def test_initial_not_ready(self, make_bridge):
        bridge, _ = make_bridge()
        assert bridge.is_ready is False
        assert bridge.timed_out is False


class TestSendCache:
    """未就绪缓存（FIFO 上限 16）"""

    def test_send_before_ready_caches(self, make_bridge):
        bridge, _ = make_bridge()
        emitted = echo_emits(bridge)
        bridge.send("set_state", state="happy")
        assert emitted == [], "未就绪时不应直接发射 command"
        assert len(bridge._cache) == 1
        assert json.loads(bridge._cache[0]) == {"type": "set_state", "state": "happy"}

    def test_wrappers_before_ready_cached(self, make_bridge):
        bridge, _ = make_bridge()
        emitted = echo_emits(bridge)
        bridge.set_state("talking")
        bridge.set_emotion("bored")
        bridge.lip_sync(True)
        bridge.play_action("hello")
        bridge.ping()
        assert emitted == []
        assert len(bridge._cache) == 5

    def test_cache_replay_on_ready_in_order(self, make_bridge):
        bridge, _ = make_bridge()
        emitted = echo_emits(bridge)
        bridge.send("set_state", state="idle")
        bridge.send("lip_sync", on=True)
        bridge.send("play_action", action="pet")
        bridge.reportEvent(json.dumps({"type": "bridge_connected"}))
        assert [json.loads(p) for p in emitted] == [
            {"type": "set_state", "state": "idle"},
            {"type": "lip_sync", "on": True},
            {"type": "play_action", "action": "pet"},
        ], "ready 后应按入队顺序重放"
        assert len(bridge._cache) == 0

    def test_cache_overflow_drops_oldest(self, make_bridge):
        bridge, _ = make_bridge()
        for i in range(20):
            bridge.send("set_state", state="s%d" % i)
        assert len(bridge._cache) == 16, "缓存上限 16"
        first = json.loads(bridge._cache[0])
        assert first["state"] == "s4", "溢出时应丢弃最旧指令（s0..s3 被丢弃）"
        last = json.loads(bridge._cache[-1])
        assert last["state"] == "s19"

    def test_send_after_ready_emits_immediately(self, make_bridge):
        bridge, _ = make_bridge()
        bridge.reportEvent(json.dumps({"type": "bridge_connected"}))
        emitted = echo_emits(bridge)
        bridge.send("ping")
        assert emitted == ['{"type": "ping"}']


class TestSemanticWrappers:
    """set_state/set_emotion/lip_sync/play_action 载荷与 bridge.js 契约一致"""

    def _ready(self, bridge):
        bridge.reportEvent(json.dumps({"type": "bridge_connected"}))

    def test_set_state_payload(self, make_bridge):
        bridge, _ = make_bridge()
        self._ready(bridge)
        emitted = echo_emits(bridge)
        bridge.set_state("happy")
        assert json.loads(emitted[0]) == {"type": "set_state", "state": "happy"}

    def test_set_emotion_payload(self, make_bridge):
        bridge, _ = make_bridge()
        self._ready(bridge)
        emitted = echo_emits(bridge)
        bridge.set_emotion("lonely")
        assert json.loads(emitted[0]) == {"type": "set_emotion", "emotion": "lonely"}

    def test_lip_sync_payload(self, make_bridge):
        bridge, _ = make_bridge()
        self._ready(bridge)
        emitted = echo_emits(bridge)
        bridge.lip_sync(True)
        assert json.loads(emitted[0]) == {"type": "lip_sync", "on": True}
        bridge.lip_sync(False)
        assert json.loads(emitted[1]) == {"type": "lip_sync", "on": False}

    def test_play_action_payload(self, make_bridge):
        bridge, _ = make_bridge()
        self._ready(bridge)
        emitted = echo_emits(bridge)
        bridge.play_action("hit_tail")
        assert json.loads(emitted[0]) == {"type": "play_action", "action": "hit_tail"}


class TestEventParsing:
    """JS -> Python 事件解析（bridge.js reportEvent)"""

    def test_body_part_clicked(self, make_bridge):
        bridge, _ = make_bridge()
        hits = []
        bridge.bodyPartClicked.connect(hits.append)
        bridge.reportEvent(json.dumps({"type": "body_part", "part": "head"}))
        bridge.reportEvent(json.dumps({"type": "body_part", "part": "tail"}))
        assert hits == ["head", "tail"]

    def test_body_part_missing_key_ignored(self, make_bridge):
        bridge, _ = make_bridge()
        hits = []
        bridge.bodyPartClicked.connect(hits.append)
        bridge.reportEvent(json.dumps({"type": "body_part"}))
        assert hits == []

    def test_invalid_json_ignored(self, make_bridge):
        bridge, _ = make_bridge()
        emitted = echo_emits(bridge)
        hits = []
        bridge.bodyPartClicked.connect(hits.append)
        bridge.reportEvent("not-a-json{{{")
        bridge.reportEvent(json.dumps(["array", "payload"]))
        bridge.reportEvent("")
        bridge.reportEvent(123)
        bridge.reportEvent(json.dumps({"no_type": "x"}))
        assert hits == []
        assert emitted == []
        # 真实触发路径：非法载荷不破坏解析，合法 body_part 仍正常上报
        bridge.reportEvent(json.dumps({"type": "body_part", "part": "ear"}))
        assert hits == ["ear"]

    def test_bridge_ready_slot_send_does_not_jump_cache(self, make_bridge):
        """I1: bridgeReady 槽内 send() 不得插队到缓存重放之前"""
        bridge, _ = make_bridge()
        emitted = echo_emits(bridge)
        bridge.send("set_state", state="idle")
        bridge.bridgeReady.connect(lambda: bridge.send("set_state", state="happy"))
        bridge.reportEvent(json.dumps({"type": "bridge_connected"}))
        assert [json.loads(p) for p in emitted] == [
            {"type": "set_state", "state": "idle"},
            {"type": "set_state", "state": "happy"},
        ], "JS 收到顺序应为：缓存重放先，bridgeReady 槽内指令后"

    def test_bridge_connected_emits_ready_replays_remains_ready(self, make_bridge):
        bridge, _ = make_bridge()
        ready = []
        bridge.bridgeReady.connect(lambda: ready.append(1))
        bridge.send("set_state", state="idle")
        bridge.reportEvent(json.dumps({"type": "bridge_connected"}))
        bridge.reportEvent(json.dumps({"type": "bridge_connected"}))

        assert ready == [1], "bridge_connected 只应触发一次 bridgeReady"
        assert bridge.is_ready is True
        assert len(bridge._cache) == 0
        assert bridge._timer.isActive() is False, "就绪后超时定时器应停止"


class TestTimeout:
    """就绪超时防护（Task 1 遗留边界：加载失败后 JS 回调永不触发）"""

    def test_timeout_emits_error_and_clears_cache(self, make_bridge):
        bridge, _ = make_bridge()
        errors = []
        bridge.bridgeError.connect(errors.append)
        bridge.send("set_state", state="idle")
        bridge.send("play_action", action="hello")
        bridge._on_timeout()

        assert errors == ["bridge timeout"]
        assert len(bridge._cache) == 0, "超时后应清空缓存"
        assert bridge.is_ready is False
        assert bridge.timed_out is True
        assert bridge._timer.isActive() is False

    def test_timeout_after_ready_is_noop(self, make_bridge):
        bridge, _ = make_bridge()
        errors = []
        bridge.bridgeError.connect(errors.append)
        bridge.reportEvent(json.dumps({"type": "bridge_connected"}))
        bridge._on_timeout()
        assert errors == []
        assert bridge.is_ready is True
        assert bridge.timed_out is False

    def test_ready_after_timeout_recovers(self, make_bridge):
        """I2: 超时后迟到 ready 可恢复，但 timed_out 保持 True（可区分）"""
        bridge, _ = make_bridge()
        errors = []
        bridge.bridgeError.connect(errors.append)
        bridge._on_timeout()
        assert bridge.timed_out is True
        assert bridge.is_ready is False

        # 超时后仍未就绪：新指令照常入缓存
        emitted = echo_emits(bridge)
        bridge.send("set_state", state="sad")

        ready = []
        bridge.bridgeReady.connect(lambda: ready.append(1))
        bridge.reportEvent(json.dumps({"type": "bridge_connected"}))
        assert ready == [1], "迟到的 bridge_connected 仍应标记就绪"
        assert errors == ["bridge timeout"]
        assert bridge.is_ready is True
        assert bridge.timed_out is True, "超时状态应保持：Task 4 可区分'正常就绪'与'超时后恢复'"
        assert [json.loads(p) for p in emitted] == [
            {"type": "set_state", "state": "sad"},
        ], "迟到就绪仍应重放超时后新入缓存指令"

    def test_real_timer_fires_via_event_loop(self, qapp, make_bridge):
        from PySide6.QtCore import QEventLoop, QTimer

        bridge, _ = make_bridge(timeout_ms=50)
        errors = []
        bridge.bridgeError.connect(errors.append)

        loop = QEventLoop()
        timeout = QTimer()
        timeout.setSingleShot(True)
        timeout.setInterval(2000)
        timeout.timeout.connect(loop.quit)
        bridge.bridgeError.connect(loop.quit)
        timeout.start()
        loop.exec()
        timeout.stop()

        assert errors == ["bridge timeout"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
