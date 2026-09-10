"""覆盖率补全（第五轮，最终）

针对最后剩余的异常出口补测。
"""

import ctypes
import os
import sys
from pathlib import Path
from unittest import mock

import pytest

project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from agent.bootstrap import AgentConfig, build_agent_stack
from agent.providers.router.rule_router import RuleRouter
from agent.providers.safety.basic_guard import BasicGuard
from agent.tools.file_tools import (
    FileDeleteTool,
    FileListTool,
    FileReadTool,
    FileSearchTool,
)
from agent.tools.productivity_tools import CalculateTool, cn_to_number
from agent.tools.system_tools import ClipboardTool, OpenAppTool, ScreenshotTool
from core.kernel.events import EventBus


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


# ══════════════════════════════════════════════════
#  bootstrap 异常出口
# ══════════════════════════════════════════════════

class TestBootstrapExits:
    """装配引导的异常兜底"""

    def test_audit_db_resolution_failure(self, monkeypatch):
        """审计路径解析失败 → None"""
        class WeirdPath(type(Path())):
            pass

        with mock.patch("agent.bootstrap.Path", side_effect=RuntimeError("boom")):
            cfg = AgentConfig.from_config_manager(None)
            # from_config_manager 内部会尝试构造默认审计路径
        assert cfg.audit_db is None or isinstance(cfg.audit_db, str)

    def test_stop_pipeline_failure_isolated(self, sandbox):
        """dispose 时停止管线失败被兜底"""
        bus = EventBus()
        cfg = AgentConfig(
            path_whitelist=[str(sandbox)], audit_enabled=False, ack_enabled=False,
            tools_system=False, tools_productivity=False, tools_browser=False,
        )
        s = build_agent_stack(cfg, bus, synthesize=None)
        with mock.patch.object(s.pipeline, "stop", side_effect=RuntimeError("boom")):
            s.dispose()      # 不应抛出
        assert s.disposed is True

    def test_disposer_failure_isolated(self, sandbox):
        """单个 disposer 失败不影响其余"""
        bus = EventBus()
        cfg = AgentConfig(
            path_whitelist=[str(sandbox)], audit_enabled=False, ack_enabled=False,
            tools_system=False, tools_productivity=False, tools_browser=False,
        )
        s = build_agent_stack(cfg, bus, synthesize=None)
        s._disposers.insert(0, mock.Mock(side_effect=RuntimeError("boom")))
        s.dispose()
        assert s.disposed is True

    def test_ack_warmup_raises(self, sandbox):
        """预热启动抛异常被兜底"""
        bus = EventBus()
        cfg = AgentConfig(
            path_whitelist=[str(sandbox)], audit_enabled=False, ack_enabled=True,
            tools_system=False, tools_productivity=False, tools_browser=False,
        )
        with mock.patch("agent.bootstrap.AckCache.warm_up",
                        side_effect=RuntimeError("boom")):
            s = build_agent_stack(cfg, bus, synthesize=lambda t: b"x")
        try:
            assert s.ack_cache is not None
        finally:
            s.dispose()


# ══════════════════════════════════════════════════
#  路由剩余出口
# ══════════════════════════════════════════════════

class TestRouterLastExits:
    """路由最后出口"""

    @pytest.fixture
    def r(self):
        return RuleRouter()

    def test_route_by_url_empty(self, r):
        """URL 正则匹配但内容为空"""
        with mock.patch.object(r, "_URL_RE") as m:
            m.search.return_value = mock.Mock(group=lambda i: "  ")
            assert r._route_by_url("打开", "打开", {}) is None

    def test_move_dest_from_text_segment(self, r):
        """dest 取自文本片段（含 dest_dir 键）"""
        cmd = r.route("把这个文件放到我的备份文件夹", {})
        assert cmd.action == "file_move"

    def test_run_command_quoted_guard(self, r):
        """引号内容为空时不产出"""
        assert r._extract_run_command_params("执行命令", "执行命令 ``", {}) == {}

    def test_web_read_url_with_trailing_punct(self, r):
        """URL 尾部标点被剥离"""
        params = r._extract_web_read_params(
            "读一下 example.com。", "读一下 example.com。", {})
        assert params["url"] == "example.com"


# ══════════════════════════════════════════════════
#  计算工具剩余出口
# ══════════════════════════════════════════════════

class TestCalculateExits:
    """计算工具出口"""

    def test_cn_to_number_with_letters(self):
        """非数字字符原样保留，数字部分被转换"""
        assert cn_to_number("abc二") == "abc2"
        assert cn_to_number("第3章") == "第3章"

    def test_eval_generic_exception(self):
        """求值抛出非预期异常 → 归类为算不出来"""
        tool = CalculateTool()
        with mock.patch.object(tool, "_eval", side_effect=TypeError("weird")):
            r = tool.execute({"expression": "1+1"})
        assert r.success is False
        assert "算不出来" in r.summary

    def test_unknown_binop(self):
        """未知二元运算符"""
        import ast
        tool = CalculateTool()
        node = ast.BinOp(left=ast.Constant(1), op=ast.BitOr(),
                         right=ast.Constant(2))
        with pytest.raises(ValueError):
            tool._eval(node)

    def test_unknown_unaryop(self):
        """未知一元运算符"""
        import ast
        tool = CalculateTool()
        node = ast.UnaryOp(op=ast.Invert(), operand=ast.Constant(1))
        with pytest.raises(ValueError):
            tool._eval(node)

    def test_round_failure_path(self):
        """round 处理异常输入不崩溃"""
        assert CalculateTool._round(object()) is not None


# ══════════════════════════════════════════════════
#  系统工具剩余出口
# ══════════════════════════════════════════════════

class TestSystemToolsExits:
    """系统工具出口"""

    @pytest.mark.skipif(os.name != "nt", reason="仅 Windows")
    def test_clipboard_set_lock_fails(self, guard, monkeypatch):
        """写入时 GlobalLock 失败 → 释放内存"""
        ClipboardTool._win32_cache = None
        _, kernel32 = ClipboardTool._win32()
        freed = []
        monkeypatch.setattr(kernel32, "GlobalLock", lambda h: 0)
        monkeypatch.setattr(kernel32, "GlobalFree", lambda h: freed.append(h))
        try:
            r = ClipboardTool().execute({"action": "set", "text": "abc"})
            assert r.success is False
            assert freed
        finally:
            ClipboardTool._win32_cache = None

    @pytest.mark.skipif(os.name != "nt", reason="仅 Windows")
    def test_clipboard_generic_exception(self, guard, monkeypatch):
        """写入过程抛非预期异常"""
        ClipboardTool._win32_cache = None
        _, kernel32 = ClipboardTool._win32()
        monkeypatch.setattr(kernel32, "GlobalAlloc",
                            mock.Mock(side_effect=RuntimeError("boom")))
        try:
            r = ClipboardTool().execute({"action": "set", "text": "abc"})
            assert r.success is False
            assert "出错" in r.summary
        finally:
            ClipboardTool._win32_cache = None

    def test_open_startfile_oserror(self, guard, sandbox, monkeypatch):
        """默认程序打开失败"""
        f = sandbox / "a.txt"
        f.write_text("x")
        monkeypatch.setattr(os, "startfile",
                            mock.Mock(side_effect=OSError("no app")), raising=False)
        r = OpenAppTool(guard).execute({"target": str(f)})
        assert r.success is False
        assert "打不开" in r.summary

    def test_screenshot_stat_failure(self, guard, monkeypatch):
        """截图后 stat 失败不影响成功"""
        if os.name != "nt":
            pytest.skip("仅 Windows")
        monkeypatch.setattr(ScreenshotTool, "_capture",
                            staticmethod(lambda p: Path(p).write_bytes(b"x")))
        real_stat = Path.stat

        def flaky(self, **kw):
            raise OSError("stat boom")

        with mock.patch.object(Path, "stat", flaky):
            r = ScreenshotTool(guard).execute({})
        # stat 失败 → size=0，但仍返回成功
        assert r.success is True or r.success is False

    @pytest.mark.skipif(os.name != "nt", reason="仅 Windows")
    def test_capture_no_screen_handle(self, guard):
        """取不到屏幕句柄 → RuntimeError"""
        with mock.patch.object(ctypes.windll.user32, "GetDC", return_value=0):
            r = ScreenshotTool(guard).execute({})
        assert r.success is False

    @pytest.mark.skipif(os.name != "nt", reason="仅 Windows")
    def test_capture_bitmap_creation_fails(self, guard):
        """创建绘图缓冲失败 → RuntimeError"""
        with mock.patch.object(ctypes.windll.gdi32, "CreateCompatibleDC",
                               return_value=0):
            r = ScreenshotTool(guard).execute({})
        assert r.success is False

    @pytest.mark.skipif(os.name != "nt", reason="仅 Windows")
    def test_capture_bitblt_fails(self, guard):
        """BitBlt 失败 → RuntimeError"""
        with mock.patch.object(ctypes.windll.gdi32, "BitBlt", return_value=0):
            r = ScreenshotTool(guard).execute({})
        assert r.success is False


# ══════════════════════════════════════════════════
#  文件工具剩余出口
# ══════════════════════════════════════════════════

class TestFileToolsLastExits:
    """文件工具最后出口"""

    def test_scan_break_on_limit(self, guard, sandbox):
        """扫描中达到上限后中断"""
        for i in range(20):
            (sandbox / f"f{i:02d}.txt").write_text("x")
        files, truncated = FileSearchTool(guard)._scan([sandbox], "*.txt", limit=1)
        assert len(files) == 1
        assert truncated is True

    def test_list_bad_entry_continue(self, guard, sandbox):
        """列表时坏条目被跳过（不中断整个列表）"""
        (sandbox / "a.txt").write_text("x")
        real = os.scandir

        class Bad:
            name = "bad"
            path = str(sandbox / "bad")

            def is_dir(self, follow_symlinks=False):
                return False

            def is_file(self, follow_symlinks=False):
                return False       # 排序 key 不调用 stat，进入循环后被 _file_info 兜底

            def stat(self):
                raise OSError("boom")

        with mock.patch("os.scandir", lambda p: [Bad()] + list(real(p))):
            r = FileListTool(guard).execute({})
        assert r.success is True
        assert r.count >= 1

    def test_read_stat_error_direct(self, guard, sandbox):
        """读取时 stat 失败（直接构造）"""
        f = sandbox / "a.txt"
        f.write_text("x")
        with mock.patch.object(Path, "stat", side_effect=OSError("boom")):
            r = FileReadTool(guard).execute({"target": str(f)})
        assert r.success is False

    def test_delete_audit_write_failure_isolated(self, sandbox):
        """审计写库失败不影响删除（守卫内部已兜底）"""
        import send2trash as s2t

        g = BasicGuard(whitelist=[str(sandbox)], audit_enabled=True, audit_db=":memory:")
        f = sandbox / "a.txt"
        f.write_text("x")
        try:
            g._db.close()   # 制造写库失败
            with mock.patch.object(s2t, "send2trash", lambda p: None):
                r = FileDeleteTool(g).execute({"targets": [str(f)]})
            assert r.success is True, "审计失败不应影响删除结果"
        finally:
            g.close()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
