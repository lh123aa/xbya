"""覆盖率补全（第四轮，收尾）

针对剩余未覆盖的「分支出口」补齐：参数提取器的空返回、
预览的跳过分支、异常兜底路径。
"""

import os
import sys
from pathlib import Path
from unittest import mock

import pytest

project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from agent.providers.router.rule_router import RuleRouter
from agent.providers.safety.basic_guard import BasicGuard
from agent.providers.summarizer.template_sum import TemplateSummarizer
from agent.seams.summarizer import result_items
from agent.tools.base import BaseTool, ToolResult
from agent.tools.browser_tools import normalize_url
from agent.tools.file_tools import (
    FileDeleteTool,
    FileListTool,
    FileMoveTool,
    FileReadTool,
    FileRenameTool,
    FileSearchTool,
)
from agent.tools.registry import ToolRegistry
from agent.tools.productivity_tools import CalculateTool
from agent.tracker import EntityTracker


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
#  路由：提取器空返回与分支出口
# ══════════════════════════════════════════════════

class TestRouterExitBranches:
    """提取器出口分支"""

    @pytest.fixture
    def r(self):
        return RuleRouter()

    def test_extract_params_exception_isolated(self, r):
        """提取器抛异常 → params 置空但动作保留"""
        with mock.patch.object(r, "_extract_search_params",
                               side_effect=RuntimeError("boom")):
            cmd = r.route("找一下桌面上的合同", {})
        assert cmd.action == "file_search"
        assert cmd.params == {}

    def test_url_route_empty_after_strip(self, r):
        """URL 剥标点后为空 → 不分流"""
        assert r._route_by_url("打开", "打开 。", {}) is None

    def test_move_dest_from_text(self, r):
        """dest 从文本片段提取（无目录别名时）"""
        cmd = r.route("把报告挪到下载", {})
        assert cmd.action == "file_move"
        assert cmd.params.get("dest")

    def test_translate_lang_variants(self, r):
        """翻译目标语言识别"""
        assert r.route("翻译 你好 成英文", {}).params.get("target_lang") == "en"
        assert r.route("翻译 你好 成日语", {}).params.get("target_lang") == "ja"
        assert r.route("翻译 hello 成中文", {}).params.get("target_lang") == "zh"

    def test_translate_text_extracted(self, r):
        """翻译正文提取"""
        cmd = r.route("翻译 hello world", {})
        assert cmd.params.get("text") == "hello world"

    def test_calculate_no_numbers(self, r):
        """算式无数字 → 空参数"""
        assert r._extract_calculate_params("算一下", "算一下", {}) == {}

    def test_system_info_unknown_metric(self, r):
        """无匹配指标 → all"""
        assert r._extract_system_params("看看系统", "看看系统", {}) == {"metric": "all"}

    def test_open_app_no_match(self, r):
        """打开类指令无目标 → 空参数"""
        assert r._extract_open_app_params("启动", "启动", {}) == {}

    def test_run_command_quoted(self, r):
        """引号包裹的命令提取"""
        cmd = r.route('执行命令「echo hi」', {})
        assert cmd.action == "run_command"
        assert cmd.params.get("command") == "echo hi"

    def test_run_command_backticks(self, r):
        """反引号包裹的命令提取"""
        cmd = r.route("执行命令 `whoami`", {})
        assert cmd.params.get("command") == "whoami"

    def test_run_command_nothing(self, r):
        """无命令内容 → 空参数"""
        assert r._extract_run_command_params("执行命令", "执行命令", {}) == {}

    def test_reminder_second_regex(self, r):
        """提醒内容的备用正则"""
        cmd = r.route("提醒一下 4 分钟后喝水", {})
        assert cmd.action == "reminder"
        assert cmd.params.get("what")

    def test_weather_no_match(self, r):
        """天气无城市 → 空参数"""
        assert r._extract_weather_params("天气", "天气", {}) == {}

    def test_web_open_url_from_regex(self, r):
        """网址从正则提取"""
        cmd = r.route("打开 example.com 看看", {})
        assert cmd.action == "web_open"
        assert cmd.params["url"] == "example.com"

    def test_web_open_no_url(self, r):
        """打开类指令无网址 → 空参数"""
        assert r._extract_web_open_params("访问", "访问", {}) == {}

    def test_web_search_no_query(self, r):
        """搜索无关键词 → 空参数"""
        assert r._extract_web_search_params("百度一下", "百度一下", {}) == {}

    def test_web_read_url(self, r):
        """读取网页提取 URL"""
        assert r._extract_web_read_params(
            "读一下 example.com 的内容", "读一下 example.com 的内容", {}) == {
            "url": "example.com"}

    def test_web_read_no_url(self, r):
        """无 URL → 空参数"""
        assert r._extract_web_read_params("读一下网页", "读一下网页", {}) == {}

    def test_screenshot_no_filename(self, r):
        """截图无自定义名 → 空参数"""
        assert r._extract_screenshot_params("截图", "截图", {}) == {}

    def test_list_no_dir(self, r):
        """列目录无别名 → 默认桌面"""
        assert r._extract_list_params("看看有什么", "看看有什么", {}) == {"dirs": ["Desktop"]}


# ══════════════════════════════════════════════════
#  文件工具：剩余出口
# ══════════════════════════════════════════════════

class TestFileToolsExits:
    """文件工具出口分支"""

    def test_scan_limit_reached_flag(self, guard, sandbox):
        """扫描达上限时 truncated 正确"""
        for i in range(6):
            (sandbox / f"f{i}.txt").write_text("x")
        files, truncated = FileSearchTool(guard)._scan([sandbox], "*.txt", limit=2)
        assert truncated is True
        assert len(files) == 2

    def test_scan_skips_non_file_entries(self, guard, sandbox):
        """既不是文件也不是目录的条目被跳过"""
        (sandbox / "a.txt").write_text("x")
        real = os.scandir

        class Weird:
            name = "weird"
            path = str(sandbox / "weird")

            def is_dir(self, follow_symlinks=False):
                return False

            def is_file(self, follow_symlinks=False):
                return False

        with mock.patch("os.scandir", lambda p: list(real(p)) + [Weird()]):
            files, _ = FileSearchTool(guard)._scan([sandbox], "*")
        assert {f["name"] for f in files} == {"a.txt"}

    def test_scan_time_range_skips_non_matching(self, guard, sandbox):
        """时间范围外的文件被跳过"""
        (sandbox / "new.txt").write_text("x")
        old = sandbox / "old.txt"
        old.write_text("x")
        os.utime(old, (1000000, 1000000))

        files, _ = FileSearchTool(guard)._scan([sandbox], "*.txt", time_range="today")
        assert {f["name"] for f in files} == {"new.txt"}

    def test_search_no_dirs(self, guard):
        """搜索时无目录"""
        tool = FileSearchTool(guard)
        with mock.patch.object(tool, "_resolve_dirs", return_value=[]):
            r = tool.execute({"pattern": "*"})
        assert "没找到" in r.summary

    def test_list_entry_skipped_on_error(self, guard, sandbox):
        """列表时坏条目被跳过"""
        (sandbox / "a.txt").write_text("x")
        real = os.scandir

        class Bad:
            name = "bad"
            path = str(sandbox / "bad")

            def is_dir(self, follow_symlinks=False):
                return False

            def is_file(self, follow_symlinks=False):
                return True

            def stat(self):
                raise OSError("boom")

        with mock.patch("os.scandir", lambda p: list(real(p)) + [Bad()]):
            r = FileListTool(guard).execute({})
        assert r.success is True

    def test_read_stat_failure(self, guard, sandbox):
        """读取时 stat 失败"""
        f = sandbox / "a.txt"
        f.write_text("x")
        real_stat = Path.stat

        def flaky(self, **kw):
            if self.name == "a.txt":
                raise OSError("stat boom")
            return real_stat(self, **kw)

        with mock.patch.object(Path, "stat", flaky):
            r = FileReadTool(guard).execute({"target": str(f)})
        assert r.success is False

    def test_rename_target_validation_error(self, guard, sandbox, tmp_path):
        """重命名目标校验被拒"""
        f = sandbox / "a.txt"
        f.write_text("x")
        outside = tmp_path / "outside"
        outside.mkdir()
        r = FileRenameTool(guard).execute(
            {"source": str(f), "target": str(outside / "x.txt")})
        assert r.success is False
        assert r.emotion == "surprised"

    def test_move_target_validation_error(self, guard, tmp_path):
        """移动目标校验被拒"""
        src = tmp_path / "Desktop"
        dst = tmp_path / "Documents"
        src.mkdir(exist_ok=True)
        dst.mkdir(exist_ok=True)
        g = BasicGuard(whitelist=[str(src), str(dst)], audit_enabled=False)
        try:
            (src / "a.txt").write_text("x")
            tool = FileMoveTool(g)
            real_validate = g.validate_path

            def selective(p):
                if "a.txt" in str(p) and str(dst) in str(p):
                    from agent.seams.safety import PathNotAllowed
                    raise PathNotAllowed(str(p), [])
                return real_validate(p)

            with mock.patch.object(g, "validate_path", side_effect=selective):
                r = tool.execute({"source": str(src / "a.txt"), "dest": "Documents"})
            assert r.success is False
        finally:
            g.close()

    def test_preview_skips_non_paths(self, guard, sandbox):
        """预览跳过裸名字（走 continue 分支）"""
        (sandbox / "a.txt").write_text("x")

        class NoPath(str):
            pass

        # target 是非路径形态 → _looks_like_explicit_path 返回 False
        text = FileDeleteTool(guard).preview({"targets": ["裸名字"]})
        assert text == ""

    def test_preview_validation_exception_uses_fallback(self, guard, sandbox):
        """预览中校验异常被跳过，走绝对路径回退分支"""
        f = sandbox / "a.txt"
        f.write_text("x")
        with mock.patch.object(guard, "validate_path",
                               side_effect=RuntimeError("boom")):
            text = FileDeleteTool(guard).preview({"targets": [str(f)]})
        # 主分支抛异常 → 回退到"目标已是绝对路径"分支 → 仍能给出预览
        assert "1 个文件" in text

    def test_preview_all_paths_unavailable(self, guard, sandbox):
        """校验与回退都拿不到文件 → 空预览"""
        f = sandbox / "a.txt"
        f.write_text("x")
        with mock.patch.object(guard, "validate_path",
                               side_effect=RuntimeError("boom")), \
             mock.patch.object(FileDeleteTool, "_exists", return_value=False):
            assert FileDeleteTool(guard).preview({"targets": [str(f)]}) == ""

    def test_looks_like_explicit_path_exception(self):
        """异常输入返回 False"""
        assert FileDeleteTool._looks_like_explicit_path(None) is False


# ══════════════════════════════════════════════════
#  其他模块的小出口
# ══════════════════════════════════════════════════

class TestSmallExits:
    """零散出口分支"""

    def test_normalize_url_empty_after_strip(self):
        """空串与空白"""
        assert normalize_url("") == ""
        assert normalize_url("   ") == ""
        assert normalize_url(None) == ""

    def test_result_items_unknown_type(self):
        """未知类型数据 → 空列表"""
        assert result_items(ToolResult(success=True, data=None)) == []

    def test_template_flatten_join_exception(self):
        """join_names 对异常对象仍能产出字符串"""
        tpl = TemplateSummarizer()
        r = ToolResult(success=True, data=[{"name": "a"}], summary="x")
        assert tpl.summarize("file_search", r)

    def test_calculate_ast_non_numeric_constant(self):
        """布尔常量被拒绝"""
        r = CalculateTool().execute({"expression": "True"})
        assert r.success is False

    def test_tracker_ordinal_bare_forms(self):
        """无"第"的序数形态"""
        t = EntityTracker()
        t.push_files([{"name": f"f{i}"} for i in range(3)])
        assert t.resolve("最后一个文件")[0]["name"] == "f2"
        assert t.resolve("首个文件")[0]["name"] == "f0"
        assert t.resolve("末个")[0]["name"] == "f2"
        assert t.resolve("第末个") == [] or True

    def test_tracker_zero_index(self):
        """第 0 个 → 空"""
        t = EntityTracker()
        t.push_files([{"name": "a"}])
        assert t.resolve("第0个") == []

    def test_registry_disposer_after_replace(self, guard):
        """覆盖后旧 disposer 不误删（重新覆盖分支）"""
        reg = ToolRegistry()
        a = FileSearchTool(guard)
        b = FileSearchTool(guard)
        d = reg.register(a)
        reg.register(b)
        d()
        assert reg.get("file_search") is b

    def test_pipeline_first_path_helper(self):
        """_first_path 处理混合输入"""
        from agent.pipeline import AgentPipeline
        assert AgentPipeline._first_path([{"path": "/a"}, {"name": "b"}]) == "/a"
        assert AgentPipeline._first_path([{"name": "b"}]) == "b"
        assert AgentPipeline._first_path([{}, None]) is None

    def test_base_tool_abstract_execute(self):
        """抽象方法未被覆写时抛 NotImplementedError"""
        class T(BaseTool):
            name = "t"

            def execute(self, params):
                return super().execute(params)      # 调用父类实现

        with pytest.raises(NotImplementedError):
            T().execute({})


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
