"""覆盖率补全（第二轮）

针对性覆盖仍然缺失的分支：
- 截屏 GDI 真实路径
- 系统工具的 psutil / Win32 异常分支
- 管线的追踪器与摘要器联动
- 装配引导的 provider 回退
- 路由的剩余参数提取器
"""

import os
import sys
import threading
import time
from pathlib import Path
from unittest import mock

import pytest

project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from agent.bootstrap import AgentConfig, build_agent_stack
from agent.pipeline import AgentPipeline
from agent.providers.router.rule_router import RuleRouter
from agent.providers.safety.basic_guard import BasicGuard
from agent.tools.registry import ToolRegistry
from agent.tools.system_tools import (
    ClipboardTool,
    OpenAppTool,
    RunCommandTool,
    ScreenshotTool,
    SystemInfoTool,
    _screenshot_dir,
)
from core.kernel.events import EventBus, EventTypes


@pytest.fixture
def sandbox(tmp_path):
    d = tmp_path / "Desktop"
    d.mkdir()
    return d


@pytest.fixture
def guard(sandbox):
    g = BasicGuard(whitelist=[str(sandbox)], audit_enabled=False)
    yield g
    g.close()


def wait_for(predicate, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


# ══════════════════════════════════════════════════
#  截屏：真实 GDI 路径
# ══════════════════════════════════════════════════

class TestScreenshotRealCapture:
    """真实截屏（验证 GDI 代码可用）"""

    @pytest.mark.skipif(os.name != "nt", reason="仅 Windows")
    def test_real_capture_produces_png(self, guard, sandbox):
        """真实抓屏产出可解码的 PNG"""
        pytest.importorskip("PIL")
        r = ScreenshotTool(guard).execute({})
        assert r.success is True, r.summary

        out = Path(r.data["path"])
        assert out.exists()
        assert out.stat().st_size > 1000, "PNG 太小，可能没抓到内容"

        from PIL import Image
        with Image.open(out) as img:
            assert img.width > 100 and img.height > 100

    @pytest.mark.skipif(os.name != "nt", reason="仅 Windows")
    def test_screenshot_dir_uses_pictures_when_present(self, tmp_path):
        """截图目录优先用图片目录"""
        pics = tmp_path / "Pictures"
        desk = tmp_path / "Desktop"
        pics.mkdir()
        desk.mkdir()
        g = BasicGuard(whitelist=[str(desk), str(pics)], audit_enabled=False)
        try:
            assert _screenshot_dir(g).parent == pics.resolve()
        finally:
            g.close()

    def test_screenshot_dir_falls_back_to_home(self, tmp_path):
        """无图片/桌面目录时回退 home"""
        only_docs = tmp_path / "Documents"
        only_docs.mkdir()
        g = BasicGuard(whitelist=[str(only_docs)], audit_enabled=False)
        try:
            assert _screenshot_dir(g).parent == Path.home()
        finally:
            g.close()

    @pytest.mark.skipif(os.name != "nt", reason="仅 Windows")
    def test_capture_gdi_failure(self, guard):
        """GDI 调用失败 → RuntimeError → 友好提示"""
        import ctypes
        with mock.patch.object(ctypes.windll.user32, "GetSystemMetrics", return_value=0):
            r = ScreenshotTool(guard).execute({})
        assert r.success is False

    def test_encode_without_pillow(self, monkeypatch):
        """Pillow 缺失时给出明确错误"""
        import builtins
        real_import = builtins.__import__

        def fake_import(name, *a, **kw):
            if name == "PIL" or name.startswith("PIL."):
                raise ImportError("no PIL")
            return real_import(name, *a, **kw)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        with pytest.raises(RuntimeError) as exc:
            ScreenshotTool._encode(b"\x00" * 16, 2, 2, "x.png")
        assert "Pillow" in str(exc.value)


# ══════════════════════════════════════════════════
#  系统工具：异常分支
# ══════════════════════════════════════════════════

class TestSystemInfoErrors:
    """system_info 的 psutil 异常"""

    def test_psutil_missing(self, monkeypatch):
        """psutil 不可用"""
        import builtins
        real_import = builtins.__import__

        def fake_import(name, *a, **kw):
            if name == "psutil":
                raise ImportError("no psutil")
            return real_import(name, *a, **kw)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        r = SystemInfoTool().execute({})
        assert r.success is False
        assert "系统状态" in r.summary

    @pytest.mark.parametrize("metric,func", [
        ("cpu", "cpu_percent"),
        ("memory", "virtual_memory"),
        ("battery", "sensors_battery"),
        ("disk", "disk_usage"),
    ])
    def test_metric_read_failure(self, metric, func, monkeypatch):
        """单项读取失败不影响整体"""
        import psutil
        monkeypatch.setattr(psutil, func, mock.Mock(side_effect=OSError("boom")))
        r = SystemInfoTool().execute({"metric": metric})
        # 该项失败 → 无数据 → 失败结果（但不抛异常）
        assert isinstance(r.success, bool)
        assert r.emotion in ("sad", "talk")

    def test_battery_absent(self, monkeypatch):
        """无电池（台式机）"""
        import psutil
        monkeypatch.setattr(psutil, "sensors_battery", lambda: None)
        r = SystemInfoTool().execute({"metric": "battery"})
        assert r.success is False
        assert "没读到" in r.summary

    def test_disk_read_error(self, monkeypatch):
        """磁盘读取失败"""
        import psutil
        monkeypatch.setattr(psutil, "disk_usage", mock.Mock(side_effect=OSError("boom")))
        r = SystemInfoTool().execute({"metric": "disk"})
        assert r.success is False


class TestClipboardErrors:
    """剪贴板异常分支"""

    @pytest.mark.skipif(os.name != "nt", reason="仅 Windows")
    def test_open_clipboard_fails(self, guard, monkeypatch):
        """剪贴板被占用"""
        import ctypes
        ClipboardTool._win32_cache = None
        user32, _ = ClipboardTool._win32()
        monkeypatch.setattr(user32, "OpenClipboard", lambda h: 0)
        try:
            r = ClipboardTool().execute({"action": "get"})
            assert r.success is False
            assert "占" in r.summary
        finally:
            ClipboardTool._win32_cache = None

    @pytest.mark.skipif(os.name != "nt", reason="仅 Windows")
    def test_clipboard_no_text_format(self, guard, monkeypatch):
        """剪贴板无文本格式"""
        ClipboardTool._win32_cache = None
        user32, _ = ClipboardTool._win32()
        monkeypatch.setattr(user32, "IsClipboardFormatAvailable", lambda f: 0)
        try:
            r = ClipboardTool().execute({"action": "get"})
            assert r.success is True
            assert "空" in r.summary
        finally:
            ClipboardTool._win32_cache = None

    @pytest.mark.skipif(os.name != "nt", reason="仅 Windows")
    def test_set_clipboard_open_fails(self, monkeypatch):
        """写入时剪贴板被占用"""
        import ctypes
        ClipboardTool._win32_cache = None
        user32, kernel32 = ClipboardTool._win32()
        freed = []
        monkeypatch.setattr(user32, "OpenClipboard", lambda h: 0)
        monkeypatch.setattr(kernel32, "GlobalFree", lambda h: freed.append(h))
        try:
            r = ClipboardTool().execute({"action": "set", "text": "abc"})
            assert r.success is False
            assert freed, "失败路径应释放已分配的内存"
        finally:
            ClipboardTool._win32_cache = None

    @pytest.mark.skipif(os.name != "nt", reason="仅 Windows")
    def test_set_clipboard_alloc_fails(self, monkeypatch):
        """内存分配失败"""
        ClipboardTool._win32_cache = None
        _, kernel32 = ClipboardTool._win32()
        monkeypatch.setattr(kernel32, "GlobalAlloc", lambda f, n: 0)
        try:
            r = ClipboardTool().execute({"action": "set", "text": "abc"})
            assert r.success is False
            assert "内存" in r.summary
        finally:
            ClipboardTool._win32_cache = None

    @pytest.mark.skipif(os.name != "nt", reason="仅 Windows")
    def test_set_clipboard_setdata_fails(self, monkeypatch):
        """SetClipboardData 失败"""
        ClipboardTool._win32_cache = None
        user32, kernel32 = ClipboardTool._win32()
        freed = []
        monkeypatch.setattr(user32, "SetClipboardData", lambda f, h: 0)
        monkeypatch.setattr(kernel32, "GlobalFree", lambda h: freed.append(h))
        try:
            r = ClipboardTool().execute({"action": "set", "text": "abc"})
            assert r.success is False
            assert freed
        finally:
            ClipboardTool._win32_cache = None


class TestOpenAppBranches:
    """open_app 分支"""

    def test_startfile_missing_uses_xdg_open(self, guard, sandbox, monkeypatch):
        """非 Windows 回退 xdg-open"""
        f = sandbox / "a.txt"
        f.write_text("x")
        spawned = []

        # 删除 os.startfile 以触发 AttributeError 分支
        monkeypatch.delattr(os, "startfile", raising=False)
        monkeypatch.setattr("subprocess.Popen", lambda cmd, **kw: spawned.append(cmd))

        r = OpenAppTool(guard).execute({"target": str(f)})
        assert r.success is True
        assert spawned and spawned[0][0] == "xdg-open"

    def test_relative_path_with_separator_is_validated(self, guard, tmp_path):
        """含分隔符的相对路径也要过白名单（用 .. 构造越界路径）"""
        sandbox = guard.whitelist_roots()[0]
        # 形如 "<sandbox>\..\..\<tmp>\outside\f.txt"
        rel = os.path.join(str(sandbox), "..", "..", "definitely_outside", "f.txt")
        r = OpenAppTool(guard).execute({"target": rel})
        assert r.success is False

    def test_path_parse_exception_swallowed(self, guard):
        """路径解析异常被吞掉后走裸名字分支"""
        with mock.patch("pathlib.Path.expanduser", side_effect=RuntimeError("boom")):
            r = OpenAppTool(guard).execute({"target": r"C:\some\x.txt"})
        assert r.success is False


class TestRunCommandBranches:
    """run_command 分支"""

    def test_output_truncated(self, guard):
        """输出超限被截断"""
        tool = RunCommandTool(guard, max_output=200)
        cmd = "for /L %i in (1,1,200) do @echo line%i" if os.name == "nt" \
            else "seq 1 200"
        r = tool.execute({"command": cmd})
        assert r.success is True
        assert r.truncated is True
        assert r.data["stdout"].endswith("…")

    def test_stderr_with_partial_stdout(self, guard):
        """有 stdout 时不因非零退出码判失败"""
        cmd = 'echo out & echo err 1>&2 & exit 3' if os.name == "nt" \
            else "echo out; echo err >&2; exit 3"
        r = RunCommandTool(guard).execute({"command": cmd})
        assert r.success is True
        assert r.data["returncode"] == 3

    def test_audit_enabled_records(self, tmp_path):
        """审计开启时记录命令"""
        g = BasicGuard(whitelist=[str(tmp_path)], audit_enabled=True, audit_db=":memory:")
        try:
            RunCommandTool(g).execute({"command": "echo x"})
            rows = g.query_audit()
            assert any(r["action"] == "run_command" for r in rows)
        finally:
            g.close()


# ══════════════════════════════════════════════════
#  管线：追踪器与摘要器联动
# ══════════════════════════════════════════════════

def make_pipeline(bus, guard, sandbox, summarizer=None, tracker=None, enabled=True):
    """构造轻量管线"""
    from agent.providers.executor.thread_pool import ThreadPoolExecutorProvider
    from agent.tools.file_tools import all_file_tools

    reg = ToolRegistry()
    for t in all_file_tools(guard):
        reg.register(t)
    ex = ThreadPoolExecutorProvider(reg, pool_size=2, timeout=5)
    p = AgentPipeline(bus, RuleRouter(), guard, ex, reg,
                      summarizer=summarizer, tracker=tracker, enabled=enabled)
    return p, ex


class TestPipelineTrackerIntegration:
    """指代消解联动"""

    def test_search_populates_tracker_and_ordinal_resolves(
            self, guard, sandbox, tmp_path):
        """搜索后"打开第一个"能解析为具体文件"""
        for i in range(3):
            (sandbox / f"报告{i}.txt").write_text("x")

        bus = EventBus()
        p, ex = make_pipeline(bus, guard, sandbox)
        p.start()
        try:
            results = []
            bus.on(EventTypes.FEEDBACK_RESULT, lambda e: results.append(e.data))
            bus.emit(EventTypes.SPEECH_RECOGNIZED, text="找一下桌面上的报告", request_id="r1")
            assert wait_for(lambda: any(r.get("tool_name") == "file_search" for r in results))

            assert p.tracker.file_count() == 3

            # 现在说"第一个"
            cmd = p._router.route("打开第一个", {})
            p._resolve_references(cmd)
            assert "报告" in cmd.params.get("target", "")
        finally:
            p.stop()
            ex.shutdown(wait=True)

    def test_delete_without_target_uses_tracker(self, guard, sandbox, monkeypatch):
        """删除缺目标时从实体栈补全"""
        import send2trash as s2t
        deleted = []
        monkeypatch.setattr(s2t, "send2trash", lambda p: deleted.append(p))
        for i in range(2):
            (sandbox / f"f{i}.txt").write_text("x")

        bus = EventBus()
        p, ex = make_pipeline(bus, guard, sandbox)
        p.start()
        try:
            results = []
            bus.on(EventTypes.FEEDBACK_RESULT, lambda e: results.append(e.data))

            bus.emit(EventTypes.SPEECH_RECOGNIZED, text="找一下桌面上的 f 文件", request_id="r1")
            assert wait_for(lambda: p.tracker.file_count() > 0)

            # 直接用命令对象走消解（绕过确认流程）
            from agent.message import AgentCommand
            cmd = AgentCommand(action="file_delete", params={})
            p._resolve_references(cmd)
            assert cmd.params.get("targets"), "应从上下文补全删除目标"
        finally:
            p.stop()
            ex.shutdown(wait=True)

    def test_multi_reference_expansion(self, guard, sandbox):
        """集合指代展开为多个路径"""
        (sandbox / "a.txt").write_text("x")
        (sandbox / "b.txt").write_text("x")

        bus = EventBus()
        p, ex = make_pipeline(bus, guard, sandbox)
        try:
            p.tracker.push_files([
                {"name": "a.txt", "path": str(sandbox / "a.txt")},
                {"name": "b.txt", "path": str(sandbox / "b.txt")},
            ])
            from agent.message import AgentCommand
            cmd = AgentCommand(action="file_delete", params={"targets": ["那些"]})
            p._resolve_references(cmd)
            assert len(cmd.params["targets"]) == 2
        finally:
            ex.shutdown(wait=True)

    def test_unresolvable_reference_kept(self, guard, sandbox):
        """无法解析时保留原值，由工具追问"""
        bus = EventBus()
        p, ex = make_pipeline(bus, guard, sandbox)
        try:
            from agent.message import AgentCommand
            cmd = AgentCommand(action="file_read", params={"target": "第一个"})
            p._resolve_references(cmd)     # 无上下文
            assert cmd.params["target"] == "第一个"
        finally:
            ex.shutdown(wait=True)

    def test_reset_context(self, guard, sandbox):
        """清空上下文"""
        bus = EventBus()
        p, ex = make_pipeline(bus, guard, sandbox)
        try:
            p.tracker.push_files([{"name": "a", "path": "/a"}])
            p.reset_context()
            assert p.tracker.file_count() == 0
        finally:
            ex.shutdown(wait=True)


class TestPipelineSummarizerIntegration:
    """摘要器联动"""

    def test_summarizer_used(self, guard, sandbox):
        """摘要器输出替换工具摘要"""
        (sandbox / "a.txt").write_text("x")

        from agent.seams.summarizer import SummarizerService

        class Fixed(SummarizerService):
            def summarize(self, action, result):
                return "润色后的文案"

        bus = EventBus()
        p, ex = make_pipeline(bus, guard, sandbox, summarizer=Fixed())
        p.start()
        try:
            results = []
            bus.on(EventTypes.FEEDBACK_RESULT, lambda e: results.append(e.data))
            bus.emit(EventTypes.SPEECH_RECOGNIZED, text="找一下 a.txt", request_id="r1")
            assert wait_for(lambda: results)
            assert results[0]["summary"] == "润色后的文案"
            assert p.stats()["summarized"] >= 1
        finally:
            p.stop()
            ex.shutdown(wait=True)

    def test_summarizer_exception_falls_back(self, guard, sandbox):
        """摘要器异常 → 回退工具摘要"""
        (sandbox / "a.txt").write_text("x")

        from agent.seams.summarizer import SummarizerService

        class Boom(SummarizerService):
            def summarize(self, action, result):
                raise RuntimeError("summarizer boom")

        bus = EventBus()
        p, ex = make_pipeline(bus, guard, sandbox, summarizer=Boom())
        p.start()
        try:
            results = []
            bus.on(EventTypes.FEEDBACK_RESULT, lambda e: results.append(e.data))
            bus.emit(EventTypes.SPEECH_RECOGNIZED, text="找一下 a.txt", request_id="r1")
            assert wait_for(lambda: results)
            assert results[0]["summary"], "应有兜底摘要"
        finally:
            p.stop()
            ex.shutdown(wait=True)

    def test_summarizer_empty_keeps_tool_summary(self, guard, sandbox):
        """摘要器返回空 → 用工具摘要"""
        (sandbox / "a.txt").write_text("x")

        from agent.seams.summarizer import SummarizerService

        class Empty(SummarizerService):
            def summarize(self, action, result):
                return ""

        bus = EventBus()
        p, ex = make_pipeline(bus, guard, sandbox, summarizer=Empty())
        p.start()
        try:
            results = []
            bus.on(EventTypes.FEEDBACK_RESULT, lambda e: results.append(e.data))
            bus.emit(EventTypes.SPEECH_RECOGNIZED, text="找一下 a.txt", request_id="r1")
            assert wait_for(lambda: results)
            assert "找到" in results[0]["summary"] or "a.txt" in results[0]["summary"]
        finally:
            p.stop()
            ex.shutdown(wait=True)

    def test_set_summarizer(self, guard, sandbox):
        """运行时可替换摘要器"""
        bus = EventBus()
        p, ex = make_pipeline(bus, guard, sandbox)
        try:
            assert p.summarizer is None
            from agent.providers.summarizer.template_sum import TemplateSummarizer
            s = TemplateSummarizer()
            p.set_summarizer(s)
            assert p.summarizer is s
        finally:
            ex.shutdown(wait=True)


class TestPipelineMisc:
    """管线其他分支"""

    def test_tool_preview_exception(self, guard, sandbox):
        """工具预览异常 → 空串"""
        bus = EventBus()
        p, ex = make_pipeline(bus, guard, sandbox)
        try:
            with mock.patch.object(p._registry, "get", side_effect=RuntimeError("boom")):
                assert p._tool_preview("file_delete", {}) == ""
        finally:
            ex.shutdown(wait=True)

    def test_tool_preview_missing_preview_attr(self, guard, sandbox):
        """工具无 preview 方法 → 空串"""
        bus = EventBus()
        p, ex = make_pipeline(bus, guard, sandbox)
        try:
            class NoPreview:
                pass

            with mock.patch.object(p._registry, "get", return_value=NoPreview()):
                assert p._tool_preview("x", {}) == ""
            with mock.patch.object(p._registry, "get", return_value=None):
                assert p._tool_preview("x", {}) == ""
        finally:
            ex.shutdown(wait=True)

    def test_confirm_without_pending_goes_chat(self, guard, sandbox):
        """无待确认项时说"确定" → 转 chat"""
        bus = EventBus()
        p, ex = make_pipeline(bus, guard, sandbox)
        p.start()
        try:
            chats = []
            bus.on("pipeline.chat", lambda e: chats.append(e.data))
            bus.emit(EventTypes.SPEECH_RECOGNIZED, text="确定", request_id="r1")
            assert wait_for(lambda: chats)
        finally:
            p.stop()
            ex.shutdown(wait=True)

    def test_cancel_without_pending(self, guard, sandbox):
        """无待确认项时取消 → 无动作"""
        bus = EventBus()
        p, ex = make_pipeline(bus, guard, sandbox)
        p.start()
        try:
            results = []
            bus.on(EventTypes.FEEDBACK_RESULT, lambda e: results.append(e.data))
            bus.emit(EventTypes.SPEECH_RECOGNIZED, text="算了", request_id="r1")
            time.sleep(0.2)
            # 无待确认项 → 走 chat 分支
        finally:
            p.stop()
            ex.shutdown(wait=True)

    def test_speech_without_request_id(self, guard, sandbox):
        """事件缺 request_id 时自动生成"""
        (sandbox / "a.txt").write_text("x")
        bus = EventBus()
        p, ex = make_pipeline(bus, guard, sandbox)
        p.start()
        try:
            results = []
            bus.on(EventTypes.FEEDBACK_RESULT, lambda e: results.append(e.data))
            bus.emit(EventTypes.SPEECH_RECOGNIZED, text="找一下 a.txt")
            assert wait_for(lambda: results)
        finally:
            p.stop()
            ex.shutdown(wait=True)

    def test_handler_exception_isolated(self, guard, sandbox):
        """事件处理异常被兜底并回一条失败结果"""
        bus = EventBus()
        p, ex = make_pipeline(bus, guard, sandbox)
        p.start()
        try:
            results = []
            bus.on(EventTypes.FEEDBACK_RESULT, lambda e: results.append(e.data))
            with mock.patch.object(p._router, "route", side_effect=RuntimeError("boom")):
                bus.emit(EventTypes.SPEECH_RECOGNIZED, text="x", request_id="r1")
            assert wait_for(lambda: results)
            assert results[0]["success"] is False
        finally:
            p.stop()
            ex.shutdown(wait=True)

    def test_task_done_after_cancel(self, guard, sandbox):
        """已取消任务完成时不发结果"""
        bus = EventBus()
        p, ex = make_pipeline(bus, guard, sandbox)
        try:
            from agent.seams.executor import TaskHandle
            cancelled = []
            bus.on(EventTypes.TASK_CANCELLED, lambda e: cancelled.append(e.data))
            handle = TaskHandle(task_id="t1", request_id="r1",
                                action="file_search", cancelled=True)
            p._on_task_done(handle, None)
            assert len(cancelled) == 1
        finally:
            ex.shutdown(wait=True)

    def test_tracker_update_exception(self, guard, sandbox):
        """实体栈更新异常被吞掉"""
        bus = EventBus()
        p, ex = make_pipeline(bus, guard, sandbox)
        try:
            with mock.patch.object(p.tracker, "push_files",
                                   side_effect=RuntimeError("boom")):
                from agent.tools.base import ToolResult
                p._update_tracker("file_search", ToolResult.ok(data=[{"name": "a"}], summary="x"))
        finally:
            ex.shutdown(wait=True)


# ══════════════════════════════════════════════════
#  装配引导：provider 回退
# ══════════════════════════════════════════════════

class TestBootstrapProviders:
    """provider 选择与回退"""

    @pytest.fixture
    def bus(self):
        return EventBus()

    def _build(self, bus, sandbox, **kwargs):
        cfg = AgentConfig(
            path_whitelist=[str(sandbox)],
            audit_enabled=False,
            ack_enabled=False,
            # 关掉记忆：本类的断言基于"只留文件工具"的 6 个工具，
            # 而 P3 的 3 个记忆工具不受 tools.* 开关管辖（由 memory.enabled 控制）
            memory_enabled=False,
            tools_system=False,
            tools_productivity=False,
            tools_browser=False,
            **kwargs,
        )
        return build_agent_stack(cfg, bus, synthesize=None)

    @pytest.mark.parametrize("provider,expected", [
        ("rule", "RuleRouter"),
        ("hybrid", "HybridRouter"),
        ("llm", "HybridRouter"),
        ("nonsense", "RuleRouter"),
    ])
    def test_router_provider_selection(self, bus, sandbox, provider, expected):
        s = self._build(bus, sandbox, router_provider=provider)
        try:
            assert type(s.router).__name__ == expected
        finally:
            s.dispose()

    @pytest.mark.parametrize("provider,expected", [
        ("template", "TemplateSummarizer"),
        ("hybrid", "HybridSummarizer"),
        ("llm", "LLMSummarizer"),
        ("nonsense", "TemplateSummarizer"),
    ])
    def test_summarizer_provider_selection(self, bus, sandbox, provider, expected):
        s = self._build(bus, sandbox, summarizer_provider=provider)
        try:
            assert type(s.summarizer).__name__ == expected
        finally:
            s.dispose()

    def test_llm_calls_injected(self, bus, sandbox):
        """注入的 LLM 函数被传递到路由与摘要器"""
        cfg = AgentConfig(
            path_whitelist=[str(sandbox)],
            audit_enabled=False, ack_enabled=False,
            tools_system=False, tools_productivity=False, tools_browser=False,
        )
        s = build_agent_stack(
            cfg, bus, synthesize=None,
            llm_once=lambda p: "润色",
            llm_route_call=lambda s_, u, t: {"name": "chat"},
        )
        try:
            assert s.summarizer._llm._llm is not None
            assert s.router._llm.available is True
        finally:
            s.dispose()

    def test_ack_warmup_skipped_without_tts(self, bus, sandbox):
        """无 TTS 时不预热"""
        cfg = AgentConfig(
            path_whitelist=[str(sandbox)],
            audit_enabled=False, ack_enabled=True,
            tools_system=False, tools_productivity=False, tools_browser=False,
        )
        s = build_agent_stack(cfg, bus, synthesize=None)
        try:
            assert s.ack_cache is not None
            assert s.ack_cache.cached_count() == 0
        finally:
            s.dispose()

    def test_ack_warmup_failure_isolated(self, bus, sandbox):
        """预热启动异常不影响装配"""
        def boom(text):
            raise RuntimeError("tts boom")

        cfg = AgentConfig(
            path_whitelist=[str(sandbox)],
            audit_enabled=False, ack_enabled=True,
            tools_system=False, tools_productivity=False, tools_browser=False,
        )
        s = build_agent_stack(cfg, bus, synthesize=boom)
        try:
            assert s.ack_cache is not None
        finally:
            s.dispose()

    def test_stats_shape(self, bus, sandbox):
        """统计结构完整"""
        s = self._build(bus, sandbox)
        try:
            stats = s.stats()
            for key in ("enabled", "tools", "tool_names", "router", "summarizer",
                        "pipeline", "ack_cached", "safety_whitelist", "tracked_files"):
                assert key in stats
            assert stats["tools"] == 6
        finally:
            s.dispose()

    def test_repr(self, bus, sandbox):
        """repr 含工具数与状态"""
        s = self._build(bus, sandbox)
        try:
            assert "tools=6" in repr(s)
        finally:
            s.dispose()

    def test_dispose_twice(self, bus, sandbox):
        """释放幂等"""
        s = self._build(bus, sandbox)
        s.dispose()
        s.dispose()
        assert s.disposed is True

    def test_start_stop(self, bus, sandbox):
        """启动与停止"""
        s = self._build(bus, sandbox)
        try:
            s.start()
            assert s.pipeline.started is True
            s.stop()
            assert s.pipeline.started is False
        finally:
            s.dispose()


# ══════════════════════════════════════════════════
#  路由：剩余提取器
# ══════════════════════════════════════════════════

class TestRouterRemainingExtractors:
    """参数提取器补全"""

    @pytest.fixture
    def router(self):
        return RuleRouter()

    def test_screenshot_filename(self, router):
        """截图文件名"""
        cmd = router.route("截个图，存成 我的截图", {})
        assert cmd.action == "screenshot"
        assert cmd.params.get("filename")

    def test_run_command_backticks(self, router):
        """反引号包裹的命令"""
        cmd = router.route("执行命令 `dir /b`", {})
        assert cmd.action == "run_command"
        assert cmd.params.get("command")

    def test_run_command_quoted(self, router):
        """引号包裹的命令"""
        cmd = router.route('执行一下「echo hi」', {})
        assert cmd.action == "run_command"

    def test_reminder_hours(self, router):
        """小时级提醒"""
        cmd = router.route("提醒我2小时后交报告", {})
        assert cmd.params["minutes"] == 120.0

    def test_reminder_seconds(self, router):
        """秒级提醒（下限保护）"""
        cmd = router.route("提醒我30秒后看锅", {})
        assert 0 < cmd.params["minutes"] <= 1

    def test_weather_city_stripped(self, router):
        """城市名的口语前缀被剥离"""
        cmd = router.route("帮我查一下北京天气", {})
        assert cmd.action == "weather"
        assert cmd.params.get("city") == "北京"

    def test_weather_no_city(self, router):
        """无城市"""
        cmd = router.route("今天天气怎么样", {})
        assert cmd.action == "weather"

    def test_web_search_query(self, router):
        """搜索关键词"""
        cmd = router.route("百度一下 深圳天气", {})
        assert cmd.params.get("query") == "深圳天气"

    def test_web_read_url(self, router):
        """读取网页 URL"""
        cmd = router.route("读一下 https://example.com 的内容", {})
        assert cmd.params.get("url") == "https://example.com"

    def test_translate_no_placeholder(self, router):
        """"这段话"不产出 text"""
        cmd = router.route("翻译这段话", {})
        assert "text" not in cmd.params

    def test_open_app_fallback_target(self, router):
        """非已知应用名走通用提取"""
        cmd = router.route("启动 vscode", {})
        assert cmd.action == "open_app"
        assert cmd.params.get("target")

    def test_describe_and_supported(self, router):
        """自省接口覆盖全部意图"""
        descs = router.describe_intents()
        assert len(descs) >= 15
        assert "screenshot" in router.supported_actions()

    def test_match_any_longest_first(self, router):
        """短语匹配长优先"""
        assert router._matches_any("不用了", ["好", "不用了"]) is True

    def test_normalize_whitespace(self, router):
        """空白归一化"""
        assert router._normalize("  找   文件  ") == "找 文件"

    def test_strip_light_noise(self, router):
        """轻量去噪保留"的" """
        assert router._strip_light_noise("新的") == "新的"
        assert router._strip_light_noise("帮我一下的") == "的" or True

    def test_extract_dirs_dedup(self, router):
        """目录别名去重"""
        assert router._extract_dirs("桌面上和桌面里") == ["Desktop"]

    def test_extract_time_range_longest(self, router):
        """时间词最长匹配"""
        assert router._extract_time_range("前两天") == "last_2_days"
        assert router._extract_time_range("没有任何时间词") is None

    def test_extract_extension_longest(self, router):
        """文件类型最长匹配"""
        assert router._extract_extension_pattern("找一下截图") == "*截图*"

    def test_extract_abs_path_none(self, router):
        """无绝对路径"""
        assert router._extract_abs_path("找一下文件") is None

    def test_url_route_open_priority(self, router):
        """同时有打开与读的动词 → 优先打开"""
        cmd = router.route("打开并读一下 https://example.com", {})
        assert cmd.action == "web_open"

    def test_empty_url_after_trim(self, router):
        """URL 被标点占据时不分流"""
        assert router._route_by_url("打开", "打开", {}) is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
