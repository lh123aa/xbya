"""安全守卫测试 — R1「误操作损坏文件」核心防护验证

覆盖：
- 路径白名单（7 类攻击）
- 批量操作限制
- 三级风险评估
- 确认状态管理（请求/响应/超时/记忆）
- 审计日志

本测试是 R1 风险的验收证据，必须 100% 通过。
"""

import os
import sys
import time
from pathlib import Path

import pytest

project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from agent.providers.safety.basic_guard import BasicGuard, MAX_BATCH_SIZE
from agent.seams.safety import PathNotAllowed, SafetyVerdict, SecurityError


# ══════════════════════════════════════════════════════
#  Fixtures
# ══════════════════════════════════════════════════════

@pytest.fixture
def sandbox(tmp_path):
    """白名单沙箱目录"""
    d = tmp_path / "sandbox"
    d.mkdir()
    return d


@pytest.fixture
def outside(tmp_path):
    """白名单外的目录"""
    d = tmp_path / "outside"
    d.mkdir()
    return d


@pytest.fixture
def guard(sandbox, tmp_path):
    """以 sandbox 为唯一白名单的守卫"""
    g = BasicGuard(
        whitelist=[str(sandbox)],
        audit_db=str(tmp_path / "audit.db"),
    )
    yield g
    g.close()


@pytest.fixture
def guard_no_audit(sandbox):
    """不启用审计的守卫（用于纯逻辑测试）"""
    g = BasicGuard(whitelist=[str(sandbox)], audit_enabled=False)
    yield g
    g.close()


# ══════════════════════════════════════════════════════
#  路径白名单（R1 核心）
# ══════════════════════════════════════════════════════

class TestPathWhitelist:
    """路径白名单：7 类攻击必须全部被拒绝"""

    def test_valid_path_inside_whitelist(self, guard, sandbox):
        """白名单内路径通过校验"""
        f = sandbox / "test.txt"
        f.write_text("hello")
        result = guard.validate_path(str(f))
        assert result == f.resolve()

    def test_whitelist_root_itself_allowed(self, guard, sandbox):
        """白名单根目录本身允许"""
        assert guard.validate_path(str(sandbox)) == sandbox.resolve()

    def test_tilde_expansion(self, guard_no_audit, tmp_path, monkeypatch):
        """~ 展开（用临时 home 验证）"""
        fake_home = tmp_path / "home"
        (fake_home / "Desktop").mkdir(parents=True)
        g = BasicGuard(whitelist=[str(fake_home / "Desktop")], audit_enabled=False)
        try:
            f = fake_home / "Desktop" / "a.txt"
            f.write_text("x")
            # 显式绝对路径应通过
            assert g.validate_path(str(f)) == f.resolve()
        finally:
            g.close()

    # ── 攻击 1：目录穿越 ──

    def test_directory_traversal_blocked(self, guard, sandbox):
        """目录穿越（..）必须被拒绝"""
        attack = str(sandbox / ".." / ".." / "Windows" / "System32" / "evil.dll")
        with pytest.raises(PathNotAllowed):
            guard.validate_path(attack)

    def test_nested_traversal_blocked(self, guard, sandbox, outside):
        """多层穿越到白名单外目录必须被拒绝"""
        target = outside / "secret.txt"
        target.write_text("secret")
        attack = str(sandbox / ".." / "outside" / "secret.txt")
        with pytest.raises(PathNotAllowed):
            guard.validate_path(attack)

    # ── 攻击 2：白名单外绝对路径 ──

    def test_outside_absolute_path_blocked(self, guard, outside):
        """白名单外绝对路径必须被拒绝"""
        f = outside / "file.txt"
        f.write_text("x")
        with pytest.raises(PathNotAllowed):
            guard.validate_path(str(f))

    def test_system_path_blocked(self, guard):
        """系统路径必须被拒绝"""
        system_path = "C:/Windows/System32/cmd.exe" if os.name == "nt" else "/etc/passwd"
        with pytest.raises(PathNotAllowed):
            guard.validate_path(system_path)

    # ── 攻击 3：符号链接逃逸 ──

    def test_symlink_escape_blocked(self, guard, sandbox, outside):
        """符号链接指向白名单外必须被拒绝"""
        secret = outside / "secret.txt"
        secret.write_text("secret")
        link = sandbox / "sneaky_link"

        try:
            link.symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            pytest.skip("当前环境不支持创建符号链接（需管理员或开发者模式）")

        with pytest.raises(PathNotAllowed):
            guard.validate_path(str(link / "secret.txt"))

    # ── 攻击 4：UNC 路径 ──

    def test_unc_path_blocked(self, guard):
        """UNC 路径必须被拒绝"""
        with pytest.raises(PathNotAllowed):
            guard.validate_path("\\\\server\\share\\file.txt")

    def test_unc_forward_slash_blocked(self, guard):
        """正斜杠形式的 UNC 路径必须被拒绝"""
        with pytest.raises(PathNotAllowed):
            guard.validate_path("//server/share/file.txt")

    # ── 攻击 5：空字节注入 ──

    def test_null_byte_blocked(self, guard, sandbox):
        """空字节注入必须被拒绝"""
        with pytest.raises(PathNotAllowed):
            guard.validate_path(str(sandbox / "a\x00.txt"))

    # ── 攻击 6：空路径 ──

    def test_empty_path_blocked(self, guard):
        """空路径必须被拒绝"""
        with pytest.raises(PathNotAllowed):
            guard.validate_path("")
        with pytest.raises(PathNotAllowed):
            guard.validate_path("   ")
        with pytest.raises(PathNotAllowed):
            guard.validate_path(None)

    # ── 攻击 7：引号包裹绕过 ──

    def test_quoted_path_still_validated(self, guard, outside):
        """带引号的路径仍要经过校验（不能靠引号绕过）"""
        f = outside / "file.txt"
        f.write_text("x")
        with pytest.raises(PathNotAllowed):
            guard.validate_path(f'"{f}"')

    # ── 大小写 ──

    def test_case_insensitive_allowed(self, guard, sandbox):
        """Windows 大小写不敏感：同一路径的不同大小写应视为白名单内"""
        f = sandbox / "Test.TXT"
        f.write_text("x")
        upper = str(f).upper()
        # 校验通过（归一化后比较）
        assert guard.validate_path(upper) is not None

    def test_case_insensitive_rejection(self, guard, outside):
        """大小写变化不能绕过白名单拒绝"""
        f = outside / "file.txt"
        f.write_text("x")
        with pytest.raises(PathNotAllowed):
            guard.validate_path(str(f).upper())


class TestWhitelistHelpers:
    """白名单辅助接口"""

    def test_is_allowed_path_true(self, guard, sandbox):
        """白名单内返回 True（不抛异常）"""
        assert guard.is_allowed_path(sandbox / "a.txt") is True

    def test_is_allowed_path_false(self, guard, outside):
        """白名单外返回 False（不抛异常）"""
        assert guard.is_allowed_path(outside / "a.txt") is False

    def test_prefix_confusion_not_allowed(self, guard, tmp_path):
        """相似前缀目录不能被误判为白名单内（sandbox_evil vs sandbox）"""
        evil = tmp_path / "sandbox_evil"
        evil.mkdir()
        assert guard.is_allowed_path(evil / "a.txt") is False

    def test_whitelist_roots_returns_copy(self, guard):
        """whitelist_roots 返回副本（防止外部修改）"""
        roots = guard.whitelist_roots()
        roots.append(Path("C:/evil"))
        assert len(guard.whitelist_roots()) == 1

    def test_colloquial_drive_form_is_path_like(self, guard):
        """口语盘符必须被当作路径 → 才会进入白名单校验并留痕

        语音说「打开 C 盘 Windows 文件夹」时 ASR 稳定输出这种写法。
        旧实现把它当**裸名字**放行，白名单预校验根本没介入：
        既没有可读的拒绝，也没有审计记录（语音回环验证实测"审计 0 条拒绝"）。
        """
        for raw in ("C盘", "C盘Windows", "C盘Windows文件夹", "C 盘 Windows"):
            assert guard._looks_like_path(raw) is True, raw

    def test_colloquial_drive_form_refused(self, guard):
        """口语盘符走完整校验链路：拒绝 + 理由可读

        审计由**管线**在收到拒绝后写入（`BasicGuard.check` 只做判定不写审计），
        所以这里只断言"守卫真的拒了"——它一旦拒，管线那条 `audit(..., rejected)`
        才会被走到。修复前连这一步都到不了。
        """
        verdict = guard.check("file_read", {"target": "C盘Windows文件夹"})
        assert verdict.rejected is True
        assert verdict.reason

    def test_filename_with_drive_alias_is_not_path_like(self, guard):
        """带扩展名的合法文件名不能被误判成路径而拒掉（如 C盘说明.txt）"""
        assert guard._looks_like_path("C盘说明.txt") is False
        assert guard.check("file_read", {"target": "C盘说明.txt"}).rejected is False


class TestBatchValidation:
    """批量路径校验"""

    def test_batch_valid(self, guard, sandbox):
        """批量校验全部合法"""
        files = []
        for i in range(3):
            f = sandbox / f"f{i}.txt"
            f.write_text("x")
            files.append(str(f))
        result = guard.validate_paths(files)
        assert len(result) == 3

    def test_batch_one_invalid_fails_all(self, guard, sandbox, outside):
        """批量中任一非法则整体失败（快速失败）"""
        good = sandbox / "good.txt"
        good.write_text("x")
        bad = outside / "bad.txt"
        bad.write_text("x")

        with pytest.raises(PathNotAllowed):
            guard.validate_paths([str(good), str(bad)])

    def test_batch_size_limit(self, guard, sandbox):
        """批量数量超限被拒绝"""
        files = [str(sandbox / f"f{i}.txt") for i in range(MAX_BATCH_SIZE + 1)]
        with pytest.raises(SecurityError) as exc:
            guard.validate_paths(files)
        assert str(MAX_BATCH_SIZE) in str(exc.value)

    def test_batch_not_a_list(self, guard):
        """非列表输入被拒绝"""
        with pytest.raises(PathNotAllowed):
            guard.validate_paths("not a list")


# ══════════════════════════════════════════════════════
#  风险评估
# ══════════════════════════════════════════════════════

class TestRiskAssessment:
    """三级风险评估"""

    def test_low_risk_auto_allowed(self, guard, sandbox):
        """low 风险自动放行"""
        v = guard.check("file_search", {"dir": [str(sandbox)]})
        assert v.allowed is True
        assert v.confirm_needed is False
        assert v.risk == "low"

    def test_medium_risk_needs_confirm(self, guard, sandbox):
        """medium 风险需要确认"""
        f = sandbox / "a.txt"
        f.write_text("x")
        v = guard.check("file_rename", {"source": str(f), "target": str(sandbox / "b.txt")})
        assert v.confirm_needed is True
        assert v.require_double is False
        assert v.risk == "medium"

    def test_high_risk_needs_double_confirm(self, guard, sandbox):
        """high 风险需要双重确认"""
        f = sandbox / "a.txt"
        f.write_text("x")
        v = guard.check("file_delete", {"targets": [str(f)]})
        assert v.confirm_needed is True
        assert v.require_double is True
        assert v.risk == "high"

    def test_critical_risk_rejected(self, guard):
        """critical 风险直接拒绝（不提供确认通道）"""
        v = guard.check("format_disk", {})
        assert v.allowed is False
        assert v.confirm_needed is False
        assert v.rejected is True
        assert v.reason != ""

    def test_unknown_action_defaults_medium(self, guard):
        """未知操作按 medium 保守处理"""
        assert guard.risk_of("totally_unknown_action") == "medium"

    def test_path_outside_whitelist_rejected_in_check(self, guard, outside):
        """check 阶段就拦截白名单外路径"""
        f = outside / "a.txt"
        f.write_text("x")
        v = guard.check("file_delete", {"targets": [str(f)]})
        assert v.allowed is False
        assert v.rejected is True
        assert "不能动" in v.reason or "只能操作" in v.reason

    def test_traversal_rejected_in_check(self, guard, sandbox):
        """check 阶段拦截目录穿越"""
        v = guard.check("file_delete", {"targets": [str(sandbox / ".." / ".." / "x")]})
        assert v.allowed is False
        assert v.rejected is True

    def test_preview_generated(self, guard, sandbox):
        """操作预览包含目标文件名"""
        f = sandbox / "screenshot.png"
        f.write_text("x")
        v = guard.check("file_delete", {"targets": [str(f)]})
        assert "screenshot.png" in v.preview

    def test_preview_truncates_many_targets(self, guard, sandbox):
        """目标过多时预览折叠"""
        files = []
        for i in range(6):
            f = sandbox / f"f{i}.txt"
            f.write_text("x")
            files.append(str(f))
        v = guard.check("file_delete", {"targets": files})
        assert "6 个" in v.preview


class TestSafetyVerdict:
    """SafetyVerdict 数据结构"""

    def test_rejected_property(self):
        """rejected 属性：拒绝且无确认通道"""
        assert SafetyVerdict(allowed=False, confirm_needed=False).rejected is True
        assert SafetyVerdict(allowed=False, confirm_needed=True).rejected is False
        assert SafetyVerdict(allowed=True).rejected is False

    def test_defaults(self):
        """默认值合理"""
        v = SafetyVerdict()
        assert v.allowed is True
        assert v.risk == "low"
        assert v.confirm_needed is False


# ══════════════════════════════════════════════════════
#  确认状态管理
# ══════════════════════════════════════════════════════

class TestConfirmFlow:
    """确认流程"""

    def test_request_confirm_creates_pending(self, guard, sandbox):
        """请求确认创建待确认项"""
        f = sandbox / "a.txt"
        f.write_text("x")
        question = guard.request_confirm("r1", "file_delete", {"targets": [str(f)]})
        assert question
        assert guard.pending_count() == 1
        assert guard.get_pending("r1") is not None

    def test_delete_question_mentions_recycle_bin(self, guard, sandbox):
        """删除确认文案提到回收站"""
        f = sandbox / "a.txt"
        f.write_text("x")
        question = guard.request_confirm("r1", "file_delete", {"targets": [str(f)]})
        assert "回收站" in question

    def test_resolve_approved(self, guard, sandbox):
        """批准后 is_confirmed 为真"""
        f = sandbox / "a.txt"
        f.write_text("x")
        guard.request_confirm("r1", "file_delete", {"targets": [str(f)]})
        assert guard.resolve_confirm("r1", approved=True) is True
        assert guard.is_confirmed("r1") is True

    def test_resolve_rejected(self, guard, sandbox):
        """拒绝后 is_confirmed 为假"""
        f = sandbox / "a.txt"
        f.write_text("x")
        guard.request_confirm("r1", "file_delete", {"targets": [str(f)]})
        guard.resolve_confirm("r1", approved=False)
        assert guard.is_confirmed("r1") is False

    def test_resolve_unknown_returns_false(self, guard):
        """无匹配待确认项时返回 False（不抛异常）"""
        assert guard.resolve_confirm("nonexistent", approved=True) is False

    def test_consume_confirm_clears_state(self, guard, sandbox):
        """consume 读取并清除，避免状态泄漏"""
        f = sandbox / "a.txt"
        f.write_text("x")
        guard.request_confirm("r1", "file_delete", {"targets": [str(f)]})
        guard.resolve_confirm("r1", approved=True)
        assert guard.consume_confirm("r1") is True
        assert guard.consume_confirm("r1") is False

    def test_pending_removed_after_resolve(self, guard, sandbox):
        """批准后待确认项被移除"""
        f = sandbox / "a.txt"
        f.write_text("x")
        guard.request_confirm("r1", "file_delete", {"targets": [str(f)]})
        guard.resolve_confirm("r1", approved=True)
        assert guard.pending_count() == 0

    def test_confirm_timeout(self, guard, sandbox):
        """超时的待确认项被清理"""
        f = sandbox / "a.txt"
        f.write_text("x")
        guard.request_confirm("r1", "file_delete", {"targets": [str(f)]})
        # 模拟超时
        guard._pending["r1"].created_at -= (guard._confirm_timeout + 1)
        expired = guard.expire_check()
        assert "r1" in expired
        assert guard.pending_count() == 0

    def test_resolve_after_timeout_returns_false(self, guard, sandbox):
        """超时后再响应返回 False"""
        f = sandbox / "a.txt"
        f.write_text("x")
        guard.request_confirm("r1", "file_delete", {"targets": [str(f)]})
        guard._pending["r1"].created_at -= (guard._confirm_timeout + 1)
        assert guard.resolve_confirm("r1", approved=True) is False


class TestConfirmMemory:
    """确认记忆（避免重复询问）"""

    def test_approved_action_is_remembered(self, guard, sandbox):
        """批准后被记住，同操作同目录免确认"""
        f = sandbox / "a.txt"
        f.write_text("x")
        guard.request_confirm("r1", "file_delete", {"targets": [str(f)]})
        guard.resolve_confirm("r1", approved=True)

        # 同目录下的另一次删除 → 无需确认
        f2 = sandbox / "b.txt"
        f2.write_text("x")
        v = guard.check("file_delete", {"targets": [str(f2)]})
        assert v.confirm_needed is False
        assert v.allowed is True

    def test_rejected_action_not_remembered(self, guard, sandbox):
        """拒绝不被记住（下次仍要问）"""
        f = sandbox / "a.txt"
        f.write_text("x")
        guard.request_confirm("r1", "file_delete", {"targets": [str(f)]})
        guard.resolve_confirm("r1", approved=False)

        v = guard.check("file_delete", {"targets": [str(f)]})
        assert v.confirm_needed is True

    def test_memory_expires(self, guard, sandbox):
        """记忆过期后重新询问"""
        f = sandbox / "a.txt"
        f.write_text("x")
        guard.request_confirm("r1", "file_delete", {"targets": [str(f)]})
        guard.resolve_confirm("r1", approved=True)

        # 让记忆过期
        for key in list(guard._remembered.keys()):
            guard._remembered[key] = time.time() - 1

        v = guard.check("file_delete", {"targets": [str(f)]})
        assert v.confirm_needed is True

    def test_memory_disabled(self, sandbox):
        """关闭记忆后每次都问"""
        g = BasicGuard(
            whitelist=[str(sandbox)],
            audit_enabled=False,
            remember_choices=False,
        )
        try:
            f = sandbox / "a.txt"
            f.write_text("x")
            g.request_confirm("r1", "file_delete", {"targets": [str(f)]})
            g.resolve_confirm("r1", approved=True)
            v = g.check("file_delete", {"targets": [str(f)]})
            assert v.confirm_needed is True
        finally:
            g.close()

    def test_clear_remembered(self, guard, sandbox):
        """清空记忆"""
        f = sandbox / "a.txt"
        f.write_text("x")
        guard.request_confirm("r1", "file_delete", {"targets": [str(f)]})
        guard.resolve_confirm("r1", approved=True)
        guard.clear_remembered()
        v = guard.check("file_delete", {"targets": [str(f)]})
        assert v.confirm_needed is True

    def test_different_directory_not_remembered(self, guard, sandbox, tmp_path):
        """不同目录不被记忆覆盖"""
        sub = sandbox / "sub"
        sub.mkdir()

        f1 = sandbox / "a.txt"
        f1.write_text("x")
        guard.request_confirm("r1", "file_delete", {"targets": [str(f1)]})
        guard.resolve_confirm("r1", approved=True)

        f2 = sub / "b.txt"
        f2.write_text("x")
        v = guard.check("file_delete", {"targets": [str(f2)]})
        assert v.confirm_needed is True


# ══════════════════════════════════════════════════════
#  审计日志
# ══════════════════════════════════════════════════════

class TestAuditLog:
    """审计日志"""

    def test_audit_records_entry(self, guard):
        """审计记录写入并可查询"""
        guard.audit("file_rename", {"source": "a", "target": "b"}, success=True)
        rows = guard.query_audit()
        assert len(rows) == 1
        assert rows[0]["action"] == "file_rename"
        assert rows[0]["success"] is True

    def test_audit_records_failure(self, guard):
        """失败操作也记录"""
        guard.audit("file_delete", {"targets": ["x"]}, success=False, detail="权限不足")
        rows = guard.query_audit()
        assert rows[0]["success"] is False
        assert rows[0]["detail"] == "权限不足"

    def test_audit_ordered_desc(self, guard):
        """审计按时间倒序"""
        guard.audit("first", {}, success=True)
        time.sleep(0.01)
        guard.audit("second", {}, success=True)
        rows = guard.query_audit()
        assert rows[0]["action"] == "second"

    def test_audit_limit(self, guard):
        """查询数量限制"""
        for i in range(10):
            guard.audit(f"action_{i}", {}, success=True)
        assert len(guard.query_audit(limit=3)) == 3

    def test_audit_disabled(self, guard_no_audit):
        """关闭审计时不记录"""
        guard_no_audit.audit("file_delete", {}, success=True)
        assert guard_no_audit.query_audit() == []

    def test_audit_cleanup(self, guard):
        """清理过期审计记录"""
        guard.audit("old_action", {}, success=True)
        # 把时间戳改到 31 天前
        guard._db.execute("UPDATE audit SET ts = ?", (time.time() - 31 * 86400,))
        guard._db.commit()

        guard.audit("new_action", {}, success=True)
        removed = guard.cleanup_audit(retention_days=30)
        assert removed == 1
        rows = guard.query_audit()
        assert len(rows) == 1
        assert rows[0]["action"] == "new_action"

    def test_audit_survives_bad_params(self, guard):
        """参数不可序列化时不抛异常"""
        guard.audit("weird", {"obj": object()}, success=True)
        assert len(guard.query_audit()) == 1


class TestGuardLifecycle:
    """守卫生命周期"""

    def test_close_releases_resources(self, sandbox):
        """close 释放数据库连接"""
        g = BasicGuard(whitelist=[str(sandbox)], audit_db=":memory:")
        g.audit("x", {}, success=True)
        g.close()
        assert g._db is None
        assert g.query_audit() == []

    def test_default_whitelist_four_dirs(self):
        """默认白名单为四个常用目录"""
        g = BasicGuard(audit_enabled=False)
        try:
            roots = g.whitelist_roots()
            assert len(roots) == 4
            names = {p.name for p in roots}
            assert names == {"Desktop", "Documents", "Downloads", "Pictures"}
        finally:
            g.close()

    def test_invalid_whitelist_entry_skipped(self):
        """非法白名单项被跳过（不崩溃）"""
        g = BasicGuard(whitelist=["", None, "C:/Windows"], audit_enabled=False)
        try:
            assert isinstance(g.whitelist_roots(), list)
        finally:
            g.close()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
