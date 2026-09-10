"""Agent 层收尾覆盖测试（P1 验收）

补齐最后一小批「可达但未被既有用例走到」的分支。
原则：只测真实可达路径，不做无意义的 mock 堆砌；
确实不可达的防御分支在源码里以 `# pragma: no cover` 标注。
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from unittest import mock

import pytest

project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from agent.bootstrap import AgentConfig
from agent.message import RiskLevel
from agent.pipeline import AgentPipeline
from agent.providers.executor.thread_pool import ThreadPoolExecutorProvider
from agent.providers.router.rule_router import RuleRouter
from agent.providers.safety.basic_guard import BasicGuard
from agent.providers.summarizer.template_sum import TemplateSummarizer
from agent.seams.safety import SecurityError
from agent.seams.summarizer import params_of
from agent.tools.base import BaseTool, ToolResult
from agent.tools.browser_tools import is_safe_url
from agent.tools.file_tools import (
    MAX_SEARCH_RESULTS,
    FileDeleteTool,
    FileListTool,
    FileReadTool,
    FileRenameTool,
    FileSearchTool,
    all_file_tools,
)
from agent.tools.productivity_tools import CalculateTool, cn_to_number
from agent.tools.registry import ToolRegistry
from agent.tools.system_tools import ClipboardTool, ScreenshotTool
from agent.tracker import EntityTracker
from core.kernel.events import EventBus, EventTypes


# ══════════════════════════════════════════════════
#  Fixtures
# ══════════════════════════════════════════════════

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


@pytest.fixture
def registry(guard):
    reg = ToolRegistry()
    for t in all_file_tools(guard):
        reg.register(t)
    return reg


# ══════════════════════════════════════════════════
#  bootstrap：审计库路径解析失败
# ══════════════════════════════════════════════════

class TestBootstrapAuditFallback:
    """Path 构造失败 → audit_db 退化为 None，装配不崩"""

    def test_audit_db_path_failure(self):
        class _Cm:
            """所有配置项都取默认值（audit_db 为空 → 触发默认路径构造）"""

            def get(self, key, default=None):
                return default

        with mock.patch("agent.bootstrap.Path", side_effect=RuntimeError("path boom")):
            cfg = AgentConfig.from_config_manager(_Cm())

        assert cfg.audit_db is None, "路径构造失败时 audit_db 应为 None"
        assert cfg.enabled is True, "其余字段仍按默认值装配"

    def test_audit_db_default_path_when_ok(self):
        """正常情况：默认落在 data/agent_audit.db"""

        class _Cm:
            def get(self, key, default=None):
                return default

        cfg = AgentConfig.from_config_manager(_Cm())
        assert cfg.audit_db is not None
        assert cfg.audit_db.endswith(os.path.join("data", "agent_audit.db"))


# ══════════════════════════════════════════════════
#  管线：待确认项精确匹配 / 无追踪器兜底
# ══════════════════════════════════════════════════

class TestPipelineInternals:
    """_find_pending 与 _update_tracker 的边界"""

    @pytest.fixture
    def pipeline(self, guard, registry):
        bus = EventBus()
        ex = ThreadPoolExecutorProvider(registry, pool_size=2, timeout=5)
        p = AgentPipeline(bus, RuleRouter(), guard, ex, registry, ack_cache=None)
        p.start()
        yield p
        p.stop()
        ex.shutdown(wait=True)

    def test_find_pending_exact_request_id(self, pipeline, sandbox):
        """request_id 精确命中时直接返回该待确认项（不发兜底扫描）"""
        (sandbox / "shot.png").write_text("x")

        pipeline.handle_text("删除桌面上的截图", "rid-exact")
        deadline = time.time() + 5
        while pipeline._safety.pending_count() == 0 and time.time() < deadline:
            time.sleep(0.02)

        pending = pipeline._find_pending("rid-exact")
        assert pending is not None, "精确 request_id 应命中待确认项"
        assert pending.request_id == "rid-exact"

    def test_find_pending_falls_back_to_any(self, pipeline, sandbox):
        """request_id 不匹配时兜底取任意一条待确认项"""
        (sandbox / "shot.png").write_text("x")

        pipeline.handle_text("删除桌面上的截图", "rid-real")
        deadline = time.time() + 5
        while pipeline._safety.pending_count() == 0 and time.time() < deadline:
            time.sleep(0.02)

        other = pipeline._find_pending("rid-unknown")
        assert other is not None, "应兜底取到待确认项"
        assert other.request_id == "rid-real"

    def test_update_tracker_without_tracker(self, pipeline):
        """tracker 为 None 时直接返回（防御分支）"""
        pipeline._tracker = None
        result = ToolResult.ok(data=[{"name": "a.txt"}], summary="ok", count=1)
        assert pipeline._update_tracker("file_search", result) is None

    def test_handle_text_records_real_latency(self, pipeline):
        """回归：直接调用 handle_text 也必须记录真实耗时

        此前只有语音入口 `_on_speech` 记 t0，直接调用时 elapsed_ms 恒为 0，
        导致 stats() 的 result_p50/p95 失真、验收指标无法度量。
        """
        got: list = []
        dispose = pipeline._bus.on(EventTypes.FEEDBACK_RESULT, lambda e: got.append(e.data))
        try:
            pipeline.handle_text("看看电脑状态", "lat-1")
            deadline = time.time() + 5
            while not got and time.time() < deadline:
                time.sleep(0.02)
        finally:
            dispose()

        assert got, "应有结果事件"
        assert got[0]["elapsed_ms"] > 0, "elapsed_ms 不应为 0"
        assert pipeline.stats()["result_p50_ms"] > 0


# ══════════════════════════════════════════════════
#  执行器：registry 抛异常被兜底
# ══════════════════════════════════════════════════

class TestExecutorErrorPath:
    """工具注册表本身抛异常时，任务以失败结果收尾而非静默消失"""

    def test_registry_exception_becomes_failure(self):
        class _BoomRegistry:
            def execute(self, action, params):
                raise RuntimeError("registry 炸了")

        ex = ThreadPoolExecutorProvider(_BoomRegistry(), pool_size=1, timeout=5)
        got = []
        try:
            ex.submit("rid", "file_search", {}, lambda h, r: got.append(r))
            deadline = time.time() + 5
            while not got and time.time() < deadline:
                time.sleep(0.02)
        finally:
            ex.shutdown(wait=True)

        assert got, "异常也必须回调（否则前端永远等不到结果）"
        assert got[0].success is False
        assert "执行出错了" in got[0].error or "执行出错了" in (got[0].summary or "")


# ══════════════════════════════════════════════════
#  规则路由：引号命令 / URL 直取
# ══════════════════════════════════════════════════

class TestRuleRouterLastBranches:
    """run_command 与 web_open 的最后一个提取出口"""

    def test_run_command_quoted_fallback(self):
        """无「执行命令」等提示词时，抽取引号包裹的独立命令段"""
        r = RuleRouter()
        norm = "「帮我」"
        raw = "在 cmd 里跑一下 「ipconfig -all」 看看"
        out = r._extract_run_command_params(norm, raw, {})
        assert out == {"command": "ipconfig -all"}

    def test_run_command_quoted_too_short_ignored(self):
        """引号内不足 2 字符 → 不认为是命令"""
        r = RuleRouter()
        assert r._extract_run_command_params("", "跑一下「a」", {}) == {}

    def test_web_open_direct_url(self):
        """裸域名/URL 直接抽取，不走「打开 xxx」兜底"""
        r = RuleRouter()
        out = r._extract_web_open_params("打开某站", "打开 github.com 看看", {})
        assert out.get("url", "").startswith("github.com")

    def test_web_open_plain_text_fallback(self):
        """无 URL 形态时退回「打开 xxx」的文本目标"""
        r = RuleRouter()
        out = r._extract_web_open_params("打开百度首页", "打开百度首页", {})
        assert out.get("url")


# ══════════════════════════════════════════════════
#  安全守卫：close 容错
# ══════════════════════════════════════════════════

class TestGuardCloseResilience:
    """审计库 close 抛异常不得阻断清理"""

    def test_close_tolerates_db_error(self, sandbox):
        g = BasicGuard(whitelist=[str(sandbox)], audit_enabled=False)

        class _BadDb:
            def close(self):
                raise RuntimeError("sqlite 已经关了")

        g._db = _BadDb()
        g.close()   # 不应抛异常
        assert g._db is None


# ══════════════════════════════════════════════════
#  摘要器：模板异常兜底 / 默认参数钩子
# ══════════════════════════════════════════════════

class TestSummarizerFallbacks:
    """模板摘要内部异常 → 退回工具原文案"""

    def test_template_exception_falls_back(self, monkeypatch):
        s = TemplateSummarizer()
        result = ToolResult.ok(data=[{"name": "a"}], summary="原始文案", count=1)

        monkeypatch.setattr(
            s, "_summarize", mock.Mock(side_effect=RuntimeError("模板炸了"))
        )
        assert s.summarize("file_search", result) == "原始文案"

    def test_template_exception_without_summary(self, monkeypatch):
        """连 summary 都为空时返回空串，不抛异常"""
        s = TemplateSummarizer()
        result = ToolResult.ok(data=[], summary="", count=0)
        monkeypatch.setattr(
            s, "_summarize", mock.Mock(side_effect=RuntimeError("boom"))
        )
        assert s.summarize("file_search", result) == ""

    def test_params_of_default_is_empty(self):
        """seam 默认实现：不含原始参数"""
        result = ToolResult.ok(data=[], summary="ok", count=0)
        assert params_of(result) == {}


# ══════════════════════════════════════════════════
#  浏览器工具：无 host 的畸形 URL
# ══════════════════════════════════════════════════

class TestBrowserUrlGuards:
    def test_url_without_host_rejected(self):
        """http 协议但没有主机名 → 拒绝"""
        assert is_safe_url("http:///etc/passwd") is False
        assert is_safe_url("http://") is False

    def test_normal_url_allowed(self):
        assert is_safe_url("https://example.com/a") is True


# ══════════════════════════════════════════════════
#  文件工具：扫描边界与异常出口
# ══════════════════════════════════════════════════

class TestFileToolEdges:

    def test_search_budget_exhausted(self, guard, sandbox, monkeypatch):
        """扫描预算耗尽 → 在循环顶部收手，不返回半截结果"""
        import agent.tools.file_tools as ft

        (sandbox / "a.txt").write_text("x")
        monkeypatch.setattr(ft, "SCAN_BUDGET", -1)   # deadline 立刻过期

        tool = FileSearchTool(guard)
        res = tool.execute({"pattern": "*", "dirs": [str(sandbox)]})
        # 预算耗尽 → 没有扫到任何东西
        assert res.success is True
        assert res.count == 0

    def test_search_yesterday_excludes_today(self, guard, sandbox):
        """end_ts 上界生效：今天新建的文件不属于「昨天」"""
        (sandbox / "today.txt").write_text("x")

        tool = FileSearchTool(guard)
        res = tool.execute(
            {"pattern": "*", "dirs": [str(sandbox)], "time_range": "yesterday"}
        )
        assert res.count == 0, "今天的文件不应落入昨天的范围"

    def test_list_skips_entry_that_raises_oserror(self, guard, sandbox, monkeypatch):
        """遍历中单个目录项报 OSError → 跳过该项继续"""
        class _BadEntry:
            name = "broken"
            path = "broken"

            def is_dir(self, follow_symlinks=False):
                raise OSError("项已消失")

            def is_file(self, follow_symlinks=False):
                # 排序键用 is_file 分流：返回 False 才不会在排序阶段就炸
                return False

            def stat(self):
                raise OSError("项已消失")

        monkeypatch.setattr(os, "scandir", lambda d: iter([_BadEntry()]))

        tool = FileListTool(guard)
        res = tool.execute({"dirs": [str(sandbox)]})

        # 坏项被跳过 → 视为空目录，而不是抛异常
        assert res.success is True
        assert res.count == 0

    def test_read_stat_oserror(self, guard, sandbox, monkeypatch):
        """stat 失败 → 友好失败提示"""
        f = sandbox / "doc.txt"
        f.write_text("hello")

        tool = FileReadTool(guard)
        # 绕开解析阶段（解析本身也要 stat），直接让最终取值时 stat 失败
        monkeypatch.setattr(tool, "_resolve_targets", lambda t: ([f], None))
        monkeypatch.setattr(Path, "stat", mock.Mock(side_effect=OSError("盘符掉了")))

        res = tool.execute({"target": str(f)})
        assert res.success is False
        assert "读不到" in res.summary

    def test_rename_dst_revalidation_failure(self, guard, sandbox, monkeypatch):
        """目标路径二次校验被拒 → 不执行改名"""
        f = sandbox / "a.txt"
        f.write_text("x")

        tool = FileRenameTool(guard)
        real = guard.validate_path

        def flaky(p):
            # 解析阶段放行，只有落盘前的复核（目标文件）被拒
            if str(p).endswith("b.txt"):
                raise SecurityError("file_rename", "目标越界了")
            return real(p)

        # 绕开 _safe_path 的首轮校验，专门验证落盘前的二次复核
        monkeypatch.setattr(tool, "_safe_path", lambda p: Path(p).expanduser())
        monkeypatch.setattr(guard, "validate_path", flaky)

        res = tool.execute({"source": str(f), "target": str(f.parent / "b.txt")})
        assert res.success is False
        assert "目标越界了" in res.summary
        assert f.exists(), "校验失败时原文件必须原样保留"
        assert not (f.parent / "b.txt").exists()

    def test_looks_like_explicit_path_isabs_error(self, guard):
        """os.path.isabs 抛异常 → 退回分隔符判断"""
        tool = FileDeleteTool(guard)
        with mock.patch(
            "agent.tools.file_tools.os.path.isabs", side_effect=RuntimeError("boom")
        ):
            assert tool._looks_like_explicit_path("C:/tmp/x.txt") is True
            assert tool._looks_like_explicit_path("plain.txt") is False


# ══════════════════════════════════════════════════
#  计算工具：结果规整的兜底
# ══════════════════════════════════════════════════

class TestCalculateRoundFallback:
    """_round 遇到异常浮点对象 → 原样返回，不影响主流程"""

    def test_round_returns_value_on_error(self):
        class _WeirdFloat(float):
            def is_integer(self):
                raise RuntimeError("这个 float 不太正常")

        value = _WeirdFloat(2.0)
        assert CalculateTool._round(value) is value

    def test_round_normal_cases(self):
        assert CalculateTool._round(4.0) == 4
        assert isinstance(CalculateTool._round(4.0), int)
        assert CalculateTool._round(1 / 3) == round(1 / 3, 6)
        assert CalculateTool._round("abc") == "abc"


# ══════════════════════════════════════════════════
#  工具注册表：失败计数
# ══════════════════════════════════════════════════

class TestRegistryStats:
    def test_failing_tool_increments_fail_stats(self):
        class _FailTool(BaseTool):
            name = "always_fail"
            description = "永远失败"
            risk_level = "low"
            params_schema = {"type": "object", "properties": {}}

            def execute(self, params):
                return ToolResult.fail("就是不行呢", emotion="sad")

        reg = ToolRegistry()
        reg.register(_FailTool())
        res = reg.execute("always_fail", {})

        assert res.success is False
        assert reg.stats()["always_fail"]["fail"] == 1
        assert reg.stats()["always_fail"]["ok"] == 0


# ══════════════════════════════════════════════════
#  系统工具：剪贴板空句柄 / 截图越界
# ══════════════════════════════════════════════════

class TestClipboardGuards:
    """ctypes 原型被缓存，直接注入假 win32 对"""

    @pytest.fixture
    def fake_win32(self, monkeypatch):
        def build(get_data_result, lock_result):
            class _User32:
                @staticmethod
                def OpenClipboard(hwnd):
                    return 1

                @staticmethod
                def CloseClipboard():
                    return 1

                @staticmethod
                def IsClipboardFormatAvailable(fmt):
                    return 1

                @staticmethod
                def GetClipboardData(fmt):
                    return get_data_result

            class _Kernel32:
                @staticmethod
                def GlobalLock(handle):
                    return lock_result

                @staticmethod
                def GlobalUnlock(handle):
                    return 1

            monkeypatch.setattr(ClipboardTool, "_win32_cache", (_User32, _Kernel32), raising=False)

        return build

    def test_get_null_handle(self, fake_win32):
        """GetClipboardData 返回空 → 友好失败"""
        fake_win32(0, None)
        res = ClipboardTool().execute({"action": "get"})
        assert res.success is False
        assert "剪贴板" in res.summary

    def test_get_globallock_failure(self, fake_win32):
        """GlobalLock 失败 → 友好失败（不解引用空指针）"""
        fake_win32(12345, None)
        res = ClipboardTool().execute({"action": "get"})
        assert res.success is False
        assert "剪贴板" in res.summary


class TestScreenshotGuard:

    def test_screenshot_rejected_outside_whitelist(self, sandbox):
        """保存目录不在白名单 → SecurityError 分支，返回友好拒绝"""

        class _StrictGuard:
            def whitelist_roots(self):
                return [sandbox]

            def validate_path(self, p):
                raise SecurityError("path_check", "不在允许的目录里")

        res = ScreenshotTool(_StrictGuard()).execute({})
        assert res.success is False
        assert "不在允许的目录里" in res.summary


# ══════════════════════════════════════════════════
#  追踪器：序数越界 / 无追踪数据
# ══════════════════════════════════════════════════

class TestTrackerEdges:

    def test_ordinal_out_of_range_returns_empty(self):
        t = EntityTracker()
        t.push_files([{"name": "a.txt", "path": "a.txt"}])
        assert t.resolve_ordinal("第 9 个") == []

    def test_resolve_on_empty_stack(self):
        t = EntityTracker()
        assert t.resolve("那个文件") == []
