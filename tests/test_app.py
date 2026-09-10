"""
应用主控测试
"""

import pytest
import tempfile
import os

import sys
from pathlib import Path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from core.app import XiaoyiApp, release_single_instance_lock


class TestAppRun:
    """应用主循环集成测试"""
    
    def setup_method(self):
        """测试前设置

        单实例锁是进程级全局状态：前序测试若调用过 run() 会残留锁，
        导致本类测试被误判为"已有实例在运行"。此处先清锁，保证隔离。
        """
        release_single_instance_lock()
        self.config_path = project_root / "config.yaml"
        self.app = XiaoyiApp(str(self.config_path))

    def teardown_method(self):
        """测试后释放单实例锁，避免污染后续测试"""
        try:
            if getattr(self.app, "_running", False):
                self.app.shutdown()
            else:
                self.app._teardown_agent_layer()
        except Exception:
            pass
        release_single_instance_lock()
    
    def _patch_window(self, monkeypatch):
        """用假的PetWindow替换ui.pet_window.PetWindow，避免真实GUI"""
        from PySide6.QtWidgets import QApplication
        from ui import pet_window as pw_mod
        
        created = []
        
        class FakePetWindow:
            def __init__(self):
                self.app = None
                self.loaded = None
                self.shown = False
                created.append(self)
            
            def set_app(self, app):
                self.app = app
            
            def load_pet(self, pet_name):
                self.loaded = pet_name
            
            def show(self):
                self.shown = True
        
        monkeypatch.setattr(pw_mod, "PetWindow", FakePetWindow)
        
        qapp = QApplication.instance() or QApplication([])
        monkeypatch.setattr(qapp, "exec", lambda: None)
        
        return created
    
    def test_run_injects_app_and_loads_pet(self, monkeypatch):
        """run()创建宠物窗口、注入应用、加载宠物并显示"""
        created = self._patch_window(monkeypatch)
        self.app.initialize()
        
        self.app.run()
        
        assert len(created) == 1
        win = created[0]
        assert win.app is self.app
        assert win.loaded == "cat"
        assert win.shown is True
        # finally中调用了shutdown
        assert self.app._running is False
    
    def test_run_early_return_when_initialize_fails(self, monkeypatch):
        """initialize失败时run()提前返回，不创建窗口"""
        created = self._patch_window(monkeypatch)
        monkeypatch.setattr(self.app, "initialize", lambda: False)
        
        self.app.run()
        
        assert created == []


class TestXiaoyiApp:
    """应用主控测试类"""
    
    def setup_method(self):
        """测试前设置"""
        release_single_instance_lock()
        self.config_path = project_root / "config.yaml"
        self.app = XiaoyiApp(str(self.config_path))

    def teardown_method(self):
        """测试后关闭应用

        initialize() 现在会装配 Agent 执行层（内含线程池）。若不 shutdown，
        线程池会残留到解释器退出阶段，与 Qt 析构竞争导致进程崩溃（技术债 D7）。
        """
        try:
            if getattr(self.app, "_running", False):
                self.app.shutdown()
            else:
                self.app._teardown_agent_layer()
        except Exception:
            pass
        release_single_instance_lock()
    
    def test_initialize(self):
        """测试应用初始化"""
        result = self.app.initialize()
        assert result is True
        assert self.app._running is True

    def test_agent_layer_actually_assembles(self):
        """Agent 层必须真的装配成功，而不是被 except 静默降级

        **回归保护**：`_setup_agent_layer` 原先把项目原有的
        `core.event_bus.EventBus` 传给了 Agent 层，而 Agent 层（以及 PetWindow
        的 Agent 接线）用的是 `core.kernel.events.EventBus` —— 两个类的 API
        不兼容（`subscribe/emit(enum, dict)` vs `on/emit(str, **kwargs)`）。
        于是 `pipeline.start()` 里 `bus.on(...)` 直接 AttributeError，被
        except 吞掉并降级为"纯对话模式"：**整个 Agent 层在真实应用里从未启用**，
        而所有脚本级验收各自 new 内核总线，全都测不出来。

        这条用例断言"真的装起来了"，任何一次总线/装配回归都会立刻失败。
        """
        assert self.app.initialize() is True

        # 1. 没有被静默降级
        assert self.app.agent_stack is not None, "Agent 层被降级了（见日志）"

        # 2. 总线就是内核总线，且与项目原总线不是同一个类
        from core.kernel.events import EventBus as KernelEventBus
        assert isinstance(self.app.agent_bus, KernelEventBus)
        assert not isinstance(self.app.agent_bus, type(self.app.event_bus))

        # 3. 装配结果可观测：工具/路由/管线都在
        st = self.app.agent_stack.stats()
        assert st["tools"] >= 21
        assert st["enabled"] is True
        assert st["pipeline"]["commands"] == 0

        # 4. 管线真的订阅上了语音事件（原先就是这一步炸的）
        assert self.app.agent_bus.handler_count("speech.recognized") >= 1

    def test_interrupt_reaches_agent_layer(self):
        """按 Ctrl+Alt+D 打断必须真的到达 Agent 层

        **回归保护**：`EventTypes.SPEECH_INTERRUPTED` 由
        `agent/pipeline.py::_on_interrupt` 完整实现（取消该请求全部任务、
        作废在途润色、取消挂起计划），但过去**全仓库没有任何发射点** ——
        `interrupt_speech()` 只置了 `_interrupt_requested` 并停掉 pygame。
        用户以为打断了，后台的多步计划其实还在跑。
        """
        assert self.app.initialize() is True
        assert self.app.agent_stack is not None

        before = self.app.agent_stack.pipeline.stats()["interrupts"]
        self.app.interrupt_speech()
        after = self.app.agent_stack.pipeline.stats()["interrupts"]
        assert after == before + 1, "打断没有到达 Agent 管线"

        # 带 request_id 时只取消该请求（事件负载必须把 request_id 带过去）
        self.app.notify_agent_interrupt("req-42")
        assert self.app.agent_stack.pipeline.stats()["interrupts"] == after + 1

    def test_notify_agent_interrupt_without_agent_layer(self):
        """纯对话模式（未装配 Agent 层）时打断安静降级，不抛异常"""
        self.app.agent_stack = None
        self.app.agent_bus = None
        self.app.notify_agent_interrupt("whatever")   # 不抛即通过
        self.app.interrupt_speech()                   # 同上


    def test_get_plugin(self):
        """测试获取插件"""
        self.app.initialize()
        
        # 获取插件
        asr = self.app.get_plugin("asr")
        assert asr is not None
        
        tts = self.app.get_plugin("tts")
        assert tts is not None
    
    def test_get_status(self):
        """测试获取状态"""
        self.app.initialize()
        
        status = self.app.get_status()
        assert status is not None
        assert "running" in status
        assert "performance_mode" in status
        assert "loaded_plugins" in status
    
    def test_shutdown(self):
        """测试关闭应用"""
        self.app.initialize()
        self.app.shutdown()
        
        assert self.app._running is False
    
    def test_switch_performance_mode(self):
        """测试切换性能模式"""
        self.app.initialize()
        
        # 保存原始模式
        original_mode = self.app.config_manager.get_performance_mode()
        
        try:
            # 切换到high模式
            result = self.app.switch_performance_mode("high")
            assert result is True
            assert self.app.config_manager.get_performance_mode() == "high"
            
        finally:
            # 恢复原始模式
            self.app.switch_performance_mode(original_mode)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
