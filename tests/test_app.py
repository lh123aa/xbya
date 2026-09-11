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

    # ── P4-B3：外部依赖状态可见 ──
    #
    # 这一组守的是两类"外部原因伪装成我们的 bug"：
    #   ① 配额 429 → 表现为"规划器偶尔拆不出计划"（缺陷 18）
    #   ② 换了个没有 chat_with_tools 的引擎 → 路由兜底**静默失效**（与 D14 同类）
    # 断言的对象是**真实存在的面** `app.get_status()`，不是想象出来的 `/status` 命令。

    def test_get_status_exposes_llm_capability_and_agent_summary(self):
        """状态里必须能看到 LLM 能力与 Agent 层摘要"""
        self.app.initialize()
        st = self.app.get_status()

        assert "llm" in st and "agent" in st, "状态里必须带 LLM 能力与 Agent 摘要"
        llm = st["llm"]
        assert llm["engine"], "真实配置下应当有 LLM 引擎"
        assert llm["tool_calling"] in (True, False)
        assert "quota" in llm
        agent = st["agent"]
        assert agent is not None and agent["tools"] > 0
        assert agent["safety_whitelist"], "白名单应当可见（安全边界要能查）"

    def test_llm_capability_flags_engine_without_tool_calling(self):
        """**关键回归**：引擎没有 chat_with_tools 时必须报 False，而不是含糊过去

        这正是"换引擎 → 路由兜底静默失效"能被发现的地方。
        """

        class _NoToolsLLM:
            model = "fake-no-tools"

            def is_available(self):
                return True

        self.app.plugins["llm"] = _NoToolsLLM()
        cap = self.app.llm_capability()
        assert cap["engine"] == "_NoToolsLLM"
        assert cap["tool_calling"] is False
        # 同一事实必须能从 get_status 看到（人查状态就能发现）
        assert self.app.get_status()["llm"]["tool_calling"] is False

    def test_llm_capability_without_engine(self):
        """没有 LLM 引擎时如实报 None，而不是假装可用"""
        self.app.plugins.pop("llm", None)
        self.app.plugins["llm"] = None
        cap = self.app.llm_capability()
        assert cap["engine"] is None
        assert cap["tool_calling"] is None

    def test_agent_setup_warns_when_engine_lacks_tool_calling(self, caplog):
        """装配时必须**告警**，否则就是又一次静默降级（D14 的教训）"""
        import logging as _logging

        class _NoToolsLLM:
            model = "fake-no-tools"

            def is_available(self):
                return True

        self.app.plugins["llm"] = _NoToolsLLM()
        with caplog.at_level(_logging.WARNING):
            self.app._setup_agent_layer()

        text = caplog.text
        assert "chat_with_tools" in text, "必须点名缺的是哪个方法"
        assert "LLM 路由兜底不可用" in text, "必须说清后果，而不是只说'引擎有问题'"

    def test_llm_capability_survives_broken_plugin(self):
        """插件自报异常不该炸掉状态查询（状态面必须永远能查）"""

        class _BrokenLLM:
            model = "broken"

            def is_available(self):
                raise RuntimeError("boom")

            def quota_stats(self):
                raise RuntimeError("boom")

        self.app.plugins["llm"] = _BrokenLLM()
        cap = self.app.llm_capability()
        assert cap["available"] is None, "自报异常时如实报 None，不假装可用"
        assert "error" in (cap["quota"] or {}), "配额查询异常要如实记录"

    def test_get_status_survives_broken_plugin_methods(self):
        """**回归保护**：`get_status()` 也必须扛住自报异常的插件

        原先 `get_status()` 里有一行**独立且无保护**的
        `plugin.is_available()`（插件状态循环里），而上面那条用例只调了
        `llm_capability()` —— 于是"插件自报异常时状态查询不得崩"这条验收条件
        对 `get_status()` **并不成立**：用**同款桩**调 `get_status()` 直接抛
        `RuntimeError: boom`（本轮写证据时实测发现）。
        状态面是排障入口，它自己不能成为新的故障点。
        """

        class _BrokenLLM:
            model = "broken"

            def is_available(self):
                raise RuntimeError("boom")

            def quota_stats(self):
                raise RuntimeError("boom")

        self.app.plugins["llm"] = _BrokenLLM()
        st = self.app.get_status()          # 不得抛异常

        entry = st["plugins"]["llm"]
        assert entry["available"] is None, "无法判断要报 None，不能假装可用"
        assert "RuntimeError" in entry["error"], "异常类型要如实记下来"
        assert st["llm"]["available"] is None

    def test_get_status_survives_hostile_plugin(self):
        """更敌意的桩：连**属性访问**都抛（`__getattr__` 抛非 AttributeError）

        `hasattr` 只吞 `AttributeError`，所以它在这种对象上会原样把异常抛出来。
        状态查询依然必须能查 —— 少给字段可以，整个查不出来不行。
        """

        class _HostileLLM:
            def __getattr__(self, name):
                raise RuntimeError("boom")

        self.app.plugins["llm"] = _HostileLLM()
        st = self.app.get_status()          # 不得抛异常
        assert st["plugins"]["llm"]["available"] is None
        assert st["llm"]["available"] is None
        assert "RuntimeError" in st["llm"]["error"]

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
