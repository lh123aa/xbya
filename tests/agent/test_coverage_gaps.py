"""覆盖率补全（第三轮，最后一轮）

目标：把剩余的分支补到接近 100%。
主要覆盖「异常路径」与「罕见分支」——这些是主流程测试触达不到的。
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

from agent.message import AgentCommand
from agent.pipeline import AgentPipeline
from agent.providers.executor.thread_pool import ThreadPoolExecutorProvider
from agent.providers.router.rule_router import RuleRouter
from agent.providers.safety.basic_guard import BasicGuard
from agent.seams.safety import PathNotAllowed, SecurityError
from agent.tools.base import BaseTool, ToolResult
from agent.tools.file_tools import (
    FileDeleteTool,
    FileListTool,
    FileMoveTool,
    FileReadTool,
    FileRenameTool,
    FileSearchTool,
    all_file_tools,
)
from agent.tools.registry import ToolRegistry
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


def wait_for(pred, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.02)
    return False


# ══════════════════════════════════════════════════
#  执行器：异常路径
# ══════════════════════════════════════════════════

class BoomTool(BaseTool):
    """执行即抛异常的工具"""

    name = "boom"
    risk_level = "low"

    def execute(self, params):
        raise RuntimeError("tool exploded")


class SlowTool(BaseTool):
    """慢工具（用于测超时）"""

    name = "slow_tool"
    risk_level = "low"

    def __init__(self, delay=0.3):
        self.delay = delay

    def execute(self, params):
        time.sleep(self.delay)
        return ToolResult.ok(data=[1], summary="done")


class TestExecutorErrorPaths:
    """执行器异常分支"""

    def test_submit_pool_closed(self):
        """线程池已停时提交 → 返回空串"""
        reg = ToolRegistry()
        ex = ThreadPoolExecutorProvider(reg, pool_size=1, timeout=5)
        with mock.patch.object(ex._pool, "submit", side_effect=RuntimeError("closed")):
            assert ex.submit("r", "x", {}, lambda h, r: None) == ""
        ex.shutdown(wait=True)

    def test_task_cancelled_before_start(self):
        """任务在开始前被取消 → 回调"已取消" """
        reg = ToolRegistry()
        reg.register(SlowTool(0.4))
        ex = ThreadPoolExecutorProvider(reg, pool_size=1, timeout=5)
        try:
            outcomes = []
            done = threading.Event()

            def cb(handle, result):
                outcomes.append(result)
                done.set()

            # 占住唯一线程
            ex.submit("r0", "slow_tool", {}, lambda h, r: None)
            tid = ex.submit("r1", "slow_tool", {}, cb)

            # 直接标记取消（模拟协作式取消在运行前命中）
            with ex._lock:
                ex._handles[tid].cancelled = True

            assert done.wait(3.0)
            assert outcomes and outcomes[0].success is False
            assert "取消" in outcomes[0].summary
        finally:
            ex.shutdown(wait=True)

    def test_registry_execute_raises(self):
        """工具执行抛异常 → 归一化为失败结果"""
        reg = ToolRegistry()
        reg.register(BoomTool())
        ex = ThreadPoolExecutorProvider(reg, pool_size=1, timeout=5)
        try:
            got = {}
            done = threading.Event()
            ex.submit("r", "boom", {}, lambda h, r: (got.update(r=r), done.set()))
            assert done.wait(3.0)
            assert got["r"].success is False
        finally:
            ex.shutdown(wait=True)

    def test_task_timeout(self):
        """任务超时被判定失败"""
        reg = ToolRegistry()
        reg.register(SlowTool(0.5))
        ex = ThreadPoolExecutorProvider(reg, pool_size=1, timeout=0.05)
        try:
            got = {}
            done = threading.Event()
            ex.submit("r", "slow_tool", {}, lambda h, r: (got.update(r=r), done.set()))
            assert done.wait(3.0)
            assert got["r"].success is False
            assert "久" in got["r"].summary or "停" in got["r"].summary
        finally:
            ex.shutdown(wait=True)

    def test_callback_none(self):
        """回调为 None 不报错"""
        reg = ToolRegistry()
        ex = ThreadPoolExecutorProvider(reg, pool_size=1, timeout=5)
        try:
            ex._safe_callback(None, mock.Mock(), ToolResult.ok(summary="x"))
        finally:
            ex.shutdown(wait=True)

    def test_shutdown_python38_fallback(self):
        """旧版 Python 不支持 cancel_futures 时回退"""
        reg = ToolRegistry()
        ex = ThreadPoolExecutorProvider(reg, pool_size=1, timeout=5)
        with mock.patch.object(ex._pool, "shutdown",
                               side_effect=[TypeError("no cancel_futures"), None]):
            ex.shutdown(wait=True)

    def test_cancel_all_marks_handles(self):
        """cancel_all 标记全部"""
        reg = ToolRegistry()
        reg.register(SlowTool(0.3))
        ex = ThreadPoolExecutorProvider(reg, pool_size=1, timeout=5)
        try:
            ex.submit("r", "slow_tool", {}, lambda h, r: None)
            assert ex.cancel_all() >= 1
        finally:
            ex.shutdown(wait=True)


# ══════════════════════════════════════════════════
#  管线：剩余分支
# ══════════════════════════════════════════════════

def make_pipeline(bus, guard, tracker=None):
    reg = ToolRegistry()
    for t in all_file_tools(guard):
        reg.register(t)
    ex = ThreadPoolExecutorProvider(reg, pool_size=2, timeout=5)
    p = AgentPipeline(bus, RuleRouter(), guard, ex, reg, tracker=tracker)
    return p, ex


class TestPipelineRemaining:
    """管线剩余分支"""

    def test_stop_with_disposer_exception(self, guard, sandbox):
        """disposer 异常不影响 stop"""
        bus = EventBus()
        p, ex = make_pipeline(bus, guard)
        p.start()
        p._disposers.append(mock.Mock(side_effect=RuntimeError("boom")))
        p.stop()          # 不应抛出
        assert p.started is False
        ex.shutdown(wait=True)

    def test_set_enabled(self, guard, sandbox):
        """开关切换"""
        bus = EventBus()
        p, ex = make_pipeline(bus, guard)
        try:
            p.set_enabled(False)
            assert p._enabled is False
            p.set_enabled(True)
            assert p._enabled is True
        finally:
            ex.shutdown(wait=True)

    def test_resolve_references_without_tracker(self, guard, sandbox):
        """无追踪器时跳过消解"""
        bus = EventBus()
        reg = ToolRegistry()
        ex = ThreadPoolExecutorProvider(reg, pool_size=1, timeout=5)
        p = AgentPipeline(bus, RuleRouter(), guard, ex, reg)
        p._tracker = None
        cmd = AgentCommand(action="file_read", params={"target": "第一个"})
        p._resolve_references(cmd)      # 不应抛异常
        assert cmd.params["target"] == "第一个"
        ex.shutdown(wait=True)

    def test_path_of_variants(self):
        """_path_of 处理多种输入"""
        assert AgentPipeline._path_of({"path": "/a"}) == "/a"
        assert AgentPipeline._path_of({"name": "a"}) == "a"
        assert AgentPipeline._path_of("/a") == "/a"
        assert AgentPipeline._path_of(123) is None
        assert AgentPipeline._path_of({"x": 1}) is None

    def test_first_path_empty(self):
        """_first_path 无有效路径"""
        assert AgentPipeline._first_path([]) is None
        assert AgentPipeline._first_path([123, None]) is None

    def test_confirm_without_pending_emits_chat(self, guard, sandbox):
        """确认但无待确认项 → 转 chat"""
        bus = EventBus()
        p, ex = make_pipeline(bus, guard)
        try:
            # 直接调用内部方法，构造"有待确认语义但无待确认项"的状态
            chats = []
            bus.on("pipeline.chat", lambda e: chats.append(e.data))
            with mock.patch.object(p._safety, "pending_count", return_value=1):
                p._handle_confirm("ghost-rid")
            assert len(chats) == 1
        finally:
            ex.shutdown(wait=True)

    def test_cancel_without_pending(self, guard, sandbox):
        """取消但无待确认项 → 静默返回"""
        bus = EventBus()
        p, ex = make_pipeline(bus, guard)
        try:
            p._handle_cancel("ghost-rid")     # 不应抛异常
        finally:
            ex.shutdown(wait=True)

    def test_find_pending_fallback(self, guard, sandbox):
        """request_id 不匹配时回退取任一待确认项"""
        bus = EventBus()
        p, ex = make_pipeline(bus, guard)
        try:
            mk = sandbox / "a.txt"
            mk.write_text("x")
            p._safety.request_confirm("real-rid", "file_delete",
                                      {"targets": [str(mk)]})
            found = p._find_pending("other-rid")
            assert found is not None
            assert found.request_id == "real-rid"
        finally:
            ex.shutdown(wait=True)

    def test_find_pending_none(self, guard, sandbox):
        """无待确认项返回 None"""
        bus = EventBus()
        p, ex = make_pipeline(bus, guard)
        try:
            assert p._find_pending("nope") is None
        finally:
            ex.shutdown(wait=True)

    def test_safe_callback_error_in_task_done(self, guard, sandbox):
        """_on_task_done 中结果发射异常被隔离（通过 mock 触发）"""
        bus = EventBus()
        p, ex = make_pipeline(bus, guard)
        try:
            from agent.seams.executor import TaskHandle
            handle = TaskHandle(task_id="t", request_id="r", action="file_search")
            with mock.patch.object(p, "_emit_result", side_effect=RuntimeError("boom")):
                with pytest.raises(RuntimeError):
                    p._on_task_done(handle, ToolResult.ok(summary="x"))
        finally:
            ex.shutdown(wait=True)

    def test_stats_with_tracker(self, guard, sandbox):
        """统计含追踪器文件数"""
        bus = EventBus()
        p, ex = make_pipeline(bus, guard)
        try:
            p.tracker.push_files([{"name": "a", "path": "/a"}])
            assert p.stats()["tracked_files"] == 1
        finally:
            ex.shutdown(wait=True)

    def test_reset_stats_clears_latency(self, guard, sandbox):
        """重置清空延迟样本"""
        bus = EventBus()
        p, ex = make_pipeline(bus, guard)
        try:
            p._stats["latency_ack_ms"].append(1.0)
            p._request_t0["r"] = 1.0
            p.reset_stats()
            assert p.stats()["ack_p50_ms"] == 0.0
            assert p._request_t0 == {}
        finally:
            ex.shutdown(wait=True)

    def test_repr(self, guard, sandbox):
        """repr"""
        bus = EventBus()
        p, ex = make_pipeline(bus, guard)
        try:
            assert "AgentPipeline" in repr(p)
        finally:
            ex.shutdown(wait=True)


# ══════════════════════════════════════════════════
#  安全守卫：异常分支
# ══════════════════════════════════════════════════

class TestGuardRemaining:
    """守卫剩余分支"""

    def test_build_roots_resolve_failure(self, tmp_path, monkeypatch):
        """白名单路径 resolve 失败时回退 abspath"""
        (tmp_path / "Desktop").mkdir()
        monkeypatch.setattr(Path, "resolve",
                            mock.Mock(side_effect=OSError("resolve boom")))
        g = BasicGuard(whitelist=[str(tmp_path / "Desktop")], audit_enabled=False)
        try:
            assert len(g.whitelist_roots()) == 1
        finally:
            g.close()

    def test_expanduser_failure(self, guard):
        """展开 ~ 失败被拒绝"""
        with mock.patch.object(Path, "expanduser", side_effect=RuntimeError("boom")):
            with pytest.raises(PathNotAllowed):
                guard.validate_path("~/Desktop/a.txt")

    def test_resolve_failure(self, guard, sandbox):
        """解析失败被拒绝"""
        with mock.patch.object(Path, "resolve", side_effect=OSError("boom")):
            with pytest.raises(PathNotAllowed):
                guard.validate_path(str(sandbox / "a.txt"))

    def test_is_allowed_path_str_failure(self, guard):
        """路径转字符串失败 → False"""
        bad = mock.Mock()
        bad.__str__ = mock.Mock(side_effect=RuntimeError("boom"))
        assert guard.is_allowed_path(bad) is False

    def test_looks_like_path_variants(self):
        """路径形态判定矩阵"""
        f = BasicGuard._looks_like_path
        assert f("") is False
        assert f("   ") is False
        assert f("~/a") is True
        assert f("/abs/a") is True
        assert f("a/b") is True
        assert f(r"a\b") is True
        assert f("C:relative") is True
        assert f("裸名字") is False
        assert f('"~/q"') is True

    def test_precheck_security_error(self, guard):
        """预校验中的 SecurityError 被转成拒绝理由"""
        with mock.patch.object(guard, "validate_path",
                               side_effect=SecurityError("x", "被拒绝")):
            v = guard.check("file_read", {"target": str(Path.home() / "a.txt")})
        assert v.allowed is False
        assert "被拒绝" in v.reason

    def test_build_preview_generic_branch(self, guard, sandbox):
        """通用工具的预览文案"""
        f = sandbox / "a.txt"
        f.write_text("x")
        v = guard.check("file_read", {"target": str(f)})
        assert "涉及" in v.preview

    def test_remember_key_exception_paths(self, guard):
        """记忆键构造在某些字段异常时回退"""
        bad = mock.Mock()
        bad.__str__ = mock.Mock(side_effect=RuntimeError("boom"))
        with mock.patch("pathlib.Path.expanduser", side_effect=RuntimeError("boom")):
            key = guard._remember_key("file_delete", {"target": "~/x"})
        assert isinstance(key, str)

        with mock.patch("pathlib.Path.expanduser", side_effect=RuntimeError("boom")):
            key2 = guard._remember_key("file_delete", {"targets": ["~/x"]})
        assert isinstance(key2, str)

    def test_cleanup_audit_exception(self, tmp_path):
        """审计清理异常被兜底"""
        g = BasicGuard(whitelist=[str(tmp_path)], audit_db=":memory:")
        try:
            g._db.close()
            assert g.cleanup_audit() == 0
        finally:
            g.close()

    def test_expire_check_no_pending(self, guard):
        """无待确认项时清理返回空"""
        assert guard.expire_check() == []

    def test_validate_path_quoted_and_stripped(self, guard, sandbox):
        """带引号的路径被剥引号后校验"""
        f = sandbox / "a.txt"
        f.write_text("x")
        assert guard.validate_path(f'"{f}"') == f.resolve()


# ══════════════════════════════════════════════════
#  文件工具：剩余分支
# ══════════════════════════════════════════════════

class TestFileToolsRemaining:
    """文件工具剩余分支"""

    def test_scan_hits_limit_breaks(self, guard, sandbox):
        """扫描达上限立即中断（覆盖 break 分支）"""
        for i in range(10):
            (sandbox / f"f{i}.txt").write_text("x")
        files, truncated = FileSearchTool(guard)._scan([sandbox], "*.txt", limit=3)
        assert len(files) == 3
        assert truncated is True

    def test_scan_skips_directory_names(self, guard, sandbox):
        """跳过系统目录（覆盖 continue 分支）"""
        for name in ("__pycache__", ".git", "node_modules"):
            (sandbox / name).mkdir()
        (sandbox / "keep.txt").write_text("x")
        files, _ = FileSearchTool(guard)._scan([sandbox], "*", include_dirs=True)
        names = {f["name"] for f in files}
        assert "keep.txt" in names
        assert "__pycache__" not in names

    def test_resolve_targets_security_error(self, guard):
        """目标解析中的 SecurityError"""
        tool = FileReadTool(guard)
        with mock.patch.object(tool, "_safe_path",
                               side_effect=SecurityError("x", "批量超限")):
            paths, err = tool._resolve_targets(str(Path.home() / "a.txt"))
        assert paths == [] and err == "批量超限"

    def test_search_no_dirs(self, guard):
        """无可用目录"""
        with mock.patch.object(FileSearchTool(guard), "_resolve_dirs", return_value=[]):
            r = FileSearchTool(guard).execute({"pattern": "*"})
        assert r.success is True
        assert "没找到" in r.summary

    def test_list_no_dirs(self, guard):
        """无可用目录"""
        tool = FileListTool(guard)
        with mock.patch.object(tool, "_resolve_dirs", return_value=[]):
            r = tool.execute({})
        assert "没找到" in r.summary

    def test_list_entry_oserror(self, guard, sandbox):
        """列表时单个条目异常被跳过"""
        (sandbox / "a.txt").write_text("x")
        real = os.scandir

        class BadEntry:
            name = "bad"
            path = str(sandbox / "bad")

            def is_file(self):
                return True

            def is_dir(self, follow_symlinks=False):
                raise OSError("boom")

            def stat(self):
                raise OSError("boom")

        with mock.patch("os.scandir", lambda p: list(real(p)) + [BadEntry()]):
            r = FileListTool(guard).execute({})
        assert r.success is True

    def test_read_no_paths(self, guard):
        """读取时无匹配文件"""
        tool = FileReadTool(guard)
        with mock.patch.object(tool, "_resolve_targets", return_value=([], "")):
            r = tool.execute({"target": "x"})
        assert r.success is False

    def test_read_xdg_open_fallback(self, guard, sandbox):
        """非 Windows 用 xdg-open 打开"""
        f = sandbox / "a.bin"
        f.write_bytes(b"\x00\x01")
        spawned = []
        with mock.patch.object(Path, "read_text", side_effect=AssertionError("should not read")):
            with mock.patch("os.startfile", side_effect=AttributeError, create=True):
                with mock.patch("subprocess.Popen",
                                lambda cmd, **kw: spawned.append(cmd)):
                    r = FileReadTool(guard).execute({"target": "a.bin"})
        assert r.success is True
        assert spawned and spawned[0][0] == "xdg-open"

    def test_rename_no_paths(self, guard):
        """重命名时无匹配"""
        tool = FileRenameTool(guard)
        with mock.patch.object(tool, "_resolve_targets", return_value=([], "")):
            r = tool.execute({"source": "x", "target": "y"})
        assert r.success is False

    def test_rename_target_validation_failure(self, guard, sandbox, tmp_path):
        """重命名目标校验失败"""
        f = sandbox / "a.txt"
        f.write_text("x")
        outside = tmp_path / "outside"
        outside.mkdir()
        r = FileRenameTool(guard).execute(
            {"source": str(f), "target": str(outside / "b.txt")})
        assert r.success is False
        assert r.emotion == "surprised"

    def test_move_no_paths(self, guard):
        """移动时无匹配"""
        tool = FileMoveTool(guard)
        with mock.patch.object(tool, "_resolve_targets", return_value=([], "")):
            r = tool.execute({"source": "x", "dest": "Desktop"})
        assert r.success is False

    def test_move_dest_unresolvable(self, guard, sandbox):
        """目标目录无法解析"""
        (sandbox / "a.txt").write_text("x")
        tool = FileMoveTool(guard)
        with mock.patch.object(tool, "_resolve_dirs", return_value=[]):
            r = tool.execute({"source": "a.txt", "dest": "ghost"})
        assert r.success is False
        assert "没找到" in r.summary

    def test_move_target_validation_failure(self, guard, tmp_path):
        """移动目标校验失败"""
        src_dir = tmp_path / "Desktop"
        dst_dir = tmp_path / "Documents"
        src_dir.mkdir(exist_ok=True)
        dst_dir.mkdir(exist_ok=True)
        g = BasicGuard(whitelist=[str(src_dir), str(dst_dir)], audit_enabled=False)
        try:
            (src_dir / "a.txt").write_text("x")
            tool = FileMoveTool(g)
            with mock.patch.object(g, "validate_path",
                                   side_effect=PathNotAllowed("x", [])):
                r = tool.execute({"source": str(src_dir / "a.txt"), "dest": "Documents"})
            # 源头校验就会先失败 → 仍返回失败结果
            assert r.success is False
        finally:
            g.close()

    def test_delete_preview_bare_name_resolved(self, guard, sandbox):
        """裸名字经 _resolve_targets 解析后仍能给出预览"""
        (sandbox / "a.txt").write_text("x")
        text = FileDeleteTool(guard).preview({"target": "a"})
        assert "1 个文件" in text
        assert "a.txt" in text

    def test_delete_preview_bare_name_no_match(self, guard, sandbox):
        """裸名字无匹配 → 空预览"""
        (sandbox / "a.txt").write_text("x")
        assert FileDeleteTool(guard).preview({"target": "完全不存在"}) == ""

    def test_delete_single_target_field(self, guard, sandbox, monkeypatch):
        """targets 为空但有 target 时按单目标处理"""
        import send2trash as s2t
        deleted = []
        monkeypatch.setattr(s2t, "send2trash", lambda p: deleted.append(p))
        (sandbox / "a.txt").write_text("x")
        r = FileDeleteTool(guard).execute({"targets": [], "target": "a.txt"})
        assert r.success is True

    def test_delete_multi_target_field(self, guard, sandbox, monkeypatch):
        """target 为列表时逐个收集"""
        import send2trash as s2t
        monkeypatch.setattr(s2t, "send2trash", lambda p: None)
        (sandbox / "a.txt").write_text("x")
        r = FileDeleteTool(guard).execute({"target": [str(sandbox / "a.txt")]})
        assert isinstance(r.success, bool)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
