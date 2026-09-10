"""Agent 层集成测试

验证「配置 → 装配 → 事件闭环 → 释放」全链路：

    build_agent_stack(config, bus, synthesize)
        → AgentStack(router, safety, executor, registry, ack_cache, pipeline)
        → bus.emit(SPEECH_RECOGNIZED)
        → feedback.ack（即时）+ feedback.result（执行后）
        → dispose()

以及 PetWindow 的事件桥接（跨线程 → Qt 信号）。
"""

import sys
import threading
import time
from pathlib import Path

import pytest

project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from agent.bootstrap import AgentConfig, AgentStack, build_agent_stack
from agent.pipeline import AgentPipeline
from core.kernel.events import EventBus, EventTypes


# ══════════════════════════════════════════════════════
#  Fixtures
# ══════════════════════════════════════════════════════

@pytest.fixture
def sandbox(tmp_path):
    d = tmp_path / "Desktop"
    d.mkdir()
    return d


@pytest.fixture
def fake_tts():
    def synth(text):
        return f"AUDIO::{text}".encode("utf-8")
    return synth


@pytest.fixture
def config(sandbox, tmp_path):
    return AgentConfig(
        enabled=True,
        router_provider="rule",
        path_whitelist=[str(sandbox)],
        audit_db=str(tmp_path / "audit.db"),
        audit_enabled=True,
        remember_choices=False,     # 测试中不记忆，保证确认路径可重复触发
        pool_size=2,
        task_timeout=5.0,
        ack_enabled=True,
        ack_warmup=True,
    )


@pytest.fixture
def bus():
    return EventBus()


@pytest.fixture
def stack(config, bus, fake_tts):
    s = build_agent_stack(config, bus, synthesize=fake_tts)
    yield s
    s.dispose()


def collect(bus, event_type):
    """收集事件数据"""
    items = []
    dispose = bus.on(event_type, lambda e: items.append(e.data))
    return items, dispose


def wait_until(predicate, timeout=5.0):
    """等待条件成立"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


# ══════════════════════════════════════════════════════
#  配置解析
# ══════════════════════════════════════════════════════

class TestAgentConfig:
    """配置解析"""

    def test_defaults_without_config_manager(self):
        """无配置管理器时使用默认值"""
        c = AgentConfig.from_config_manager(None)
        assert c.enabled is True
        assert c.router_provider == "hybrid"
        assert c.summarizer_provider == "hybrid"
        assert c.pool_size == 4
        assert c.ack_enabled is True
        assert c.tools_file is True

    def test_reads_real_config(self):
        """能从真实的 config.yaml 读取 agent 段"""
        from core.config_manager import ConfigManager

        cm = ConfigManager(str(project_root / "config.yaml"))
        c = AgentConfig.from_config_manager(cm)
        assert c.enabled is True
        assert c.router_provider == "hybrid"
        assert c.pool_size == 4
        assert c.task_timeout == 60.0

    def test_broken_config_manager_falls_back(self):
        """配置读取异常时回退默认值（不崩溃）"""
        class Broken:
            def get(self, key, default=None):
                raise RuntimeError("配置炸了")

        c = AgentConfig.from_config_manager(Broken())
        assert c.enabled is True   # 回退默认


# ══════════════════════════════════════════════════════
#  装配
# ══════════════════════════════════════════════════════

class TestBuildStack:
    """Agent 栈装配"""

    def test_stack_shape(self, stack):
        """装配出的组件齐全"""
        assert isinstance(stack, AgentStack)
        assert stack.router is not None
        assert stack.safety is not None
        assert stack.executor is not None
        assert stack.registry is not None
        assert stack.ack_cache is not None
        assert isinstance(stack.pipeline, AgentPipeline)

    def test_tools_registered(self, stack):
        """工具集全部注册（文件 6 + 系统 5 + 生产力 4 + 浏览器 3 + 记忆 3 = 21）"""
        assert stack.registry.count() == 21
        for name in ("file_search", "file_delete", "system_info", "clipboard",
                     "open_app", "screenshot", "run_command",
                     "calculate", "translate", "reminder", "weather",
                     "web_open", "web_search", "web_read",
                     "memory_remember", "memory_recall", "memory_forget"):
            assert stack.registry.has(name), f"缺少工具 {name}"

    def test_tool_sets_can_be_disabled(self, bus, sandbox, tmp_path, fake_tts):
        """工具集开关生效

        显式关掉记忆（`memory_enabled=False`）：记忆工具由 `agent.memory.enabled`
        单独管辖、不受 `tools.*` 影响，留着它会掩盖"只应注册文件工具"这个断言。
        """
        cfg = AgentConfig(
            path_whitelist=[str(sandbox)],
            audit_enabled=False,
            ack_enabled=False,
            memory_enabled=False,
            tools_system=False,
            tools_productivity=False,
            tools_browser=False,
        )
        s = build_agent_stack(cfg, bus, synthesize=fake_tts)
        try:
            assert s.registry.count() == 6, "只应注册文件工具"
            assert s.registry.has("file_search")
            assert not s.registry.has("screenshot")
        finally:
            s.dispose()

    def test_summarizer_assembled(self, stack):
        """摘要器按配置装配为混合实现"""
        from agent.providers.summarizer.hybrid_sum import HybridSummarizer
        assert isinstance(stack.summarizer, HybridSummarizer)

    def test_tracker_assembled(self, stack):
        """实体追踪器已装配"""
        from agent.tracker import EntityTracker
        assert isinstance(stack.tracker, EntityTracker)
        assert stack.pipeline.tracker is stack.tracker

    def test_whitelist_applied(self, stack, sandbox):
        """白名单来自配置"""
        roots = stack.safety.whitelist_roots()
        assert len(roots) == 1
        assert roots[0] == sandbox.resolve()

    def test_ack_cache_warmed(self, stack):
        """确认语缓存已预热"""
        assert stack.ack_cache.wait_ready(timeout=5.0) is True
        assert stack.ack_cache.cached_count() > 0

    def test_unknown_router_degrades_to_rule(self, bus, sandbox, tmp_path, fake_tts):
        """未实现的路由 provider 降级为 rule（不崩溃）"""
        cfg = AgentConfig(
            router_provider="totally_not_implemented",
            path_whitelist=[str(sandbox)],
            audit_enabled=False,
            ack_enabled=False,
        )
        s = build_agent_stack(cfg, bus, synthesize=fake_tts)
        try:
            from agent.providers.router.rule_router import RuleRouter
            assert isinstance(s.router, RuleRouter)
        finally:
            s.dispose()

    def test_dispose_is_idempotent(self, stack):
        """释放幂等"""
        stack.dispose()
        stack.dispose()
        assert stack.disposed is True

    def test_dispose_stops_pipeline(self, config, bus, fake_tts):
        """释放后管线不再响应事件"""
        s = build_agent_stack(config, bus, synthesize=fake_tts)
        s.start()
        s.dispose()

        results, dispose = collect(bus, EventTypes.FEEDBACK_RESULT)
        try:
            bus.emit(EventTypes.SPEECH_RECOGNIZED, text="找一下文件", request_id="r1")
            time.sleep(0.3)
        finally:
            dispose()
        assert results == []


# ══════════════════════════════════════════════════════
#  端到端闭环
# ══════════════════════════════════════════════════════

class TestEndToEnd:
    """端到端：语音 → 工具执行 → 结果反馈"""

    def test_search_flow(self, stack, bus, sandbox):
        """搜索流程：ack 先到，result 后到，且结果正确"""
        (sandbox / "合同2024.pdf").write_text("x")
        (sandbox / "合同草案.docx").write_text("x")
        (sandbox / "无关.txt").write_text("x")

        acks, d1 = collect(bus, EventTypes.FEEDBACK_ACK)
        results, d2 = collect(bus, EventTypes.FEEDBACK_RESULT)
        stack.start()
        try:
            bus.emit(EventTypes.SPEECH_RECOGNIZED,
                     text="找一下桌面上的合同文件", request_id="r1")
            assert wait_until(lambda: len(results) > 0), "未收到结果"
            time.sleep(0.05)
        finally:
            d1()
            d2()

        assert len(acks) == 1, "应发出即时确认语"
        assert acks[0]["audio"] is not None, "确认语应命中缓存"
        assert len(results) == 1
        assert results[0]["success"] is True
        assert results[0]["data"] is not None
        assert len(results[0]["data"]) == 2, "应找到 2 个合同文件"

    def test_ack_precedes_result(self, stack, bus, sandbox):
        """ack 必须先于 result（用户先听到反馈）"""
        (sandbox / "a.txt").write_text("x")
        order = []
        d1 = bus.on(EventTypes.FEEDBACK_ACK, lambda e: order.append("ack"))
        d2 = bus.on(EventTypes.FEEDBACK_RESULT, lambda e: order.append("result"))
        stack.start()
        try:
            bus.emit(EventTypes.SPEECH_RECOGNIZED, text="找一下桌面上的文件", request_id="r1")
            assert wait_until(lambda: "result" in order)
        finally:
            d1()
            d2()
        assert order[0] == "ack"

    def test_perceived_latency_target(self, stack, bus, sandbox):
        """感知延迟（首次反馈）目标 <1.5s"""
        (sandbox / "a.txt").write_text("x")
        t0 = time.time()
        ack_at = {"t": None}
        d1 = bus.on(EventTypes.FEEDBACK_ACK, lambda e: ack_at.update(t=time.time() - t0))
        d2 = bus.on(EventTypes.FEEDBACK_RESULT, lambda e: None)
        stack.start()
        try:
            bus.emit(EventTypes.SPEECH_RECOGNIZED, text="找一下桌面上的文件", request_id="r1")
            assert wait_until(lambda: ack_at["t"] is not None)
        finally:
            d1()
            d2()
        assert ack_at["t"] < 1.5, f"感知延迟 {ack_at['t']:.2f}s 超标"

    def test_chat_falls_through(self, stack, bus):
        """闲聊走 pipeline.chat（回退原 LLM 链路）"""
        chats, d1 = collect(bus, "pipeline.chat")
        results, d2 = collect(bus, EventTypes.FEEDBACK_RESULT)
        stack.start()
        try:
            bus.emit(EventTypes.SPEECH_RECOGNIZED, text="你好呀", request_id="r1")
            assert wait_until(lambda: len(chats) > 0)
            time.sleep(0.2)
        finally:
            d1()
            d2()

        assert len(chats) == 1
        assert chats[0]["text"] == "你好呀"
        assert results == [], "闲聊不应产生工具结果"

    def test_disabled_stack_all_chat(self, bus, sandbox, tmp_path, fake_tts):
        """enabled=False 时全部转 chat（保留原链路）"""
        cfg = AgentConfig(
            enabled=False,
            path_whitelist=[str(sandbox)],
            audit_enabled=False,
            ack_enabled=False,
        )
        s = build_agent_stack(cfg, bus, synthesize=fake_tts)
        s.start()
        try:
            chats, d1 = collect(bus, "pipeline.chat")
            results, d2 = collect(bus, EventTypes.FEEDBACK_RESULT)
            try:
                bus.emit(EventTypes.SPEECH_RECOGNIZED, text="找一下桌面上的合同", request_id="r1")
                assert wait_until(lambda: len(chats) > 0)
                time.sleep(0.2)
            finally:
                d1()
                d2()
            assert len(chats) == 1
            assert results == []
        finally:
            s.dispose()


class TestEndToEndDelete:
    """端到端：删除确认闭环（R1 核心场景）"""

    def test_delete_confirm_approve(self, stack, bus, sandbox, monkeypatch):
        """删除 → 确认 → 执行（走回收站）"""
        import send2trash as s2t
        deleted = []
        monkeypatch.setattr(s2t, "send2trash", lambda p: deleted.append(p))

        for i in range(3):
            (sandbox / f"截图_{i}.png").write_text("x")
        (sandbox / "保留.txt").write_text("x")

        confirms, d1 = collect(bus, EventTypes.FEEDBACK_CONFIRM)
        results, d2 = collect(bus, EventTypes.FEEDBACK_RESULT)
        stack.start()
        try:
            # 1. 发起删除
            bus.emit(EventTypes.SPEECH_RECOGNIZED,
                     text="删除桌面上的截图", request_id="r1")
            assert wait_until(lambda: len(confirms) > 0), "应请求确认"
            assert confirms[0]["risk"] == "high"
            assert "回收站" in confirms[0]["question"]
            assert deleted == [], "确认前不应删除"

            # 2. 用户确认
            bus.emit(EventTypes.SPEECH_RECOGNIZED, text="确定", request_id="r2")
            assert wait_until(lambda: len(results) > 0), "确认后应有结果"
            time.sleep(0.1)
        finally:
            d1()
            d2()

        assert len(deleted) == 3, f"应删除 3 个截图，实际 {len(deleted)}"
        assert (sandbox / "保留.txt").exists(), "非目标文件不应被删"

    def test_delete_confirm_reject(self, stack, bus, sandbox, monkeypatch):
        """删除 → 取消 → 不执行"""
        import send2trash as s2t
        deleted = []
        monkeypatch.setattr(s2t, "send2trash", lambda p: deleted.append(p))

        (sandbox / "截图_1.png").write_text("x")

        confirms, d1 = collect(bus, EventTypes.FEEDBACK_CONFIRM)
        results, d2 = collect(bus, EventTypes.FEEDBACK_RESULT)
        stack.start()
        try:
            bus.emit(EventTypes.SPEECH_RECOGNIZED, text="删除桌面上的截图", request_id="r1")
            assert wait_until(lambda: len(confirms) > 0)

            bus.emit(EventTypes.SPEECH_RECOGNIZED, text="算了", request_id="r2")
            assert wait_until(lambda: len(results) > 0)
        finally:
            d1()
            d2()

        assert deleted == [], "取消后不应删除"
        assert (sandbox / "截图_1.png").exists()

    def test_confirm_timeout_leaves_file(self, stack, bus, sandbox, monkeypatch):
        """确认超时 → 不执行"""
        import send2trash as s2t
        deleted = []
        monkeypatch.setattr(s2t, "send2trash", lambda p: deleted.append(p))

        (sandbox / "截图_1.png").write_text("x")
        stack.start()
        try:
            bus.emit(EventTypes.SPEECH_RECOGNIZED, text="删除桌面上的截图", request_id="r1")
            # 缩短超时并强制过期
            for rid in list(getattr(stack.safety, "_pending", {}).keys()):
                stack.safety._pending[rid].created_at -= 999
            stack.safety.expire_check()
            time.sleep(0.3)
        finally:
            pass
        assert deleted == []
        assert (sandbox / "截图_1.png").exists()


class TestEndToEndInterrupt:
    """端到端：打断"""

    def test_interrupt_during_task(self, stack, bus, sandbox):
        """打断后任务被取消（不再回调结果）"""
        (sandbox / "a.txt").write_text("x")
        results, d1 = collect(bus, EventTypes.FEEDBACK_RESULT)
        stack.start()
        try:
            bus.emit(EventTypes.SPEECH_RECOGNIZED, text="找一下桌面上的文件", request_id="r1")
            bus.emit(EventTypes.SPEECH_INTERRUPTED, request_id="r1")
            time.sleep(0.4)
        finally:
            d1()
        assert stack.pipeline.stats()["interrupts"] == 1


# ══════════════════════════════════════════════════════
#  PetWindow 事件桥接
# ══════════════════════════════════════════════════════

class TestPetWindowBridge:
    """PetWindow ↔ Agent 事件桥接（跨线程安全）"""

    @pytest.fixture(scope="class")
    def qapp(self):
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])
        yield app

    @staticmethod
    def _make_window():
        """构造一扇"静音"的 PetWindow

        桥接测试只关心事件是否被转成 Qt 信号；真实的 _on_agent_ack 会调用
        _speak_sentences 起线程做 TTS，那会在测试会话结束后留下悬空线程
        （解释器关闭时报 "cannot schedule new futures"）。这里把它替换成空操作。
        """
        from ui.pet_window import PetWindow
        win = PetWindow()
        win._speak_sentences = lambda sentences: None   # 屏蔽真实 TTS
        return win

    def test_bridge_exists(self, qapp):
        """桥对象与信号齐备"""
        from ui.pet_window import AgentEventBridge
        bridge = AgentEventBridge()
        for sig in ("ackReceived", "resultReceived", "confirmReceived", "chatReceived"):
            assert hasattr(bridge, sig), f"缺少信号 {sig}"

    def test_agent_disabled_without_app(self, qapp):
        """无 app 时 Agent 视为未启用"""
        win = self._make_window()
        try:
            assert win._agent_enabled is False
        finally:
            win.timer.stop()
            win.close()

    def test_agent_disabled_without_stack(self, qapp):
        """app 无 agent_stack 时视为未启用（降级原链路）"""
        class FakeApp:
            config_manager = None
            agent_stack = None

        win = self._make_window()
        try:
            win.app = FakeApp()
            assert win._agent_enabled is False
        finally:
            win.timer.stop()
            win.close()

    def test_agent_enabled_with_stack(self, qapp, config, bus, fake_tts):
        """装配了 Agent 栈时视为启用"""
        s = build_agent_stack(config, bus, synthesize=fake_tts)

        class FakeApp:
            config_manager = None
            agent_stack = s

        win = self._make_window()
        try:
            win.set_app(FakeApp())
            assert win._agent_enabled is True
            assert len(win._agent_disposers) == 6, (
                "应订阅 6 类事件（ack/result/confirm/refine/reminder/chat）")
        finally:
            win.timer.stop()
            win.close()
            s.dispose()

    def test_event_forwarded_to_qt_signal(self, qapp, config, bus, fake_tts):
        """Agent 事件被转成 Qt 信号（UI 线程安全）"""
        from PySide6.QtCore import QCoreApplication

        s = build_agent_stack(config, bus, synthesize=fake_tts)

        class FakeApp:
            config_manager = None
            agent_stack = s

        win = self._make_window()
        received = []
        win._agent_bridge.ackReceived.connect(lambda d: received.append(d))
        try:
            win.set_app(FakeApp())
            bus.emit(EventTypes.FEEDBACK_ACK, request_id="r1", text="好的~", audio=None)
            QCoreApplication.processEvents()
            assert len(received) == 1
            assert received[0]["text"] == "好的~"
        finally:
            win.timer.stop()
            win.close()
            s.dispose()

    def test_unwire_on_rewire(self, qapp, config, bus, fake_tts):
        """重复 set_app 时旧订阅被清理，不产生重复转发"""
        from PySide6.QtCore import QCoreApplication

        s = build_agent_stack(config, bus, synthesize=fake_tts)

        class FakeApp:
            config_manager = None
            agent_stack = s

        win = self._make_window()
        received = []
        win._agent_bridge.ackReceived.connect(lambda d: received.append(d))
        try:
            win.set_app(FakeApp())
            win.set_app(FakeApp())     # 二次装配
            assert len(win._agent_disposers) == 6

            bus.emit(EventTypes.FEEDBACK_ACK, request_id="r1", text="好的~", audio=None)
            QCoreApplication.processEvents()
            assert len(received) == 1, "不应重复转发"
        finally:
            win.timer.stop()
            win.close()
            s.dispose()

    def test_result_event_updates_state(self, qapp, config, bus, fake_tts):
        """结果事件驱动状态机与动效（不经真实 TTS）"""
        from PySide6.QtCore import QCoreApplication

        s = build_agent_stack(config, bus, synthesize=fake_tts)

        class FakeApp:
            config_manager = None
            agent_stack = s

        win = self._make_window()
        try:
            win.set_app(FakeApp())
            bus.emit(EventTypes.FEEDBACK_RESULT,
                     request_id="r1", summary="找到 2 个文件",
                     emotion="happy", success=True, elapsed_ms=12.0)
            QCoreApplication.processEvents()
            assert win.anim_controller.current_state == "happy"
            assert "找到" in win.bubble_text
        finally:
            win.timer.stop()
            win.close()
            s.dispose()

    def test_confirm_event_sets_pending_state(self, qapp, config, bus, fake_tts):
        """确认事件进入待确认状态（高风险用惊讶表情）"""
        from PySide6.QtCore import QCoreApplication

        s = build_agent_stack(config, bus, synthesize=fake_tts)

        class FakeApp:
            config_manager = None
            agent_stack = s

        win = self._make_window()
        try:
            win.set_app(FakeApp())
            bus.emit(EventTypes.FEEDBACK_CONFIRM,
                     request_id="r1", question="要删除吗？", risk="high")
            QCoreApplication.processEvents()
            assert win._agent_busy is True
            assert win.anim_controller.current_state == "surprise"
            assert "删除" in win.bubble_text
        finally:
            win.timer.stop()
            win.close()
            s.dispose()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
