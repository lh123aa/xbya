"""文件工具测试

覆盖：
- 工具框架（BaseTool / ToolResult / 参数校验）
- 工具注册表（注册/执行/统计）
- 6 个文件工具的正常、边界、错误场景
- R1 安全契约：删除必须走回收站（含源码审查）
"""

import os
import re
import sys
import time
from pathlib import Path

import pytest

project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from agent.providers.safety.basic_guard import BasicGuard
from agent.seams.safety import SecurityError
from agent.tools.base import BaseTool, ParamError, ToolResult
from agent.tools.file_tools import (
    MAX_LIST_RESULTS,
    MAX_READ_BYTES,
    MAX_READ_CHARS,
    MAX_SEARCH_RESULTS,
    FileDeleteTool,
    FileListTool,
    FileMoveTool,
    FileReadTool,
    FileRenameTool,
    FileSearchTool,
    all_file_tools,
)
from agent.tools.registry import ToolRegistry


# ══════════════════════════════════════════════════════
#  Fixtures
# ══════════════════════════════════════════════════════

@pytest.fixture
def sandbox(tmp_path):
    """白名单沙箱目录（模拟"桌面"）"""
    d = tmp_path / "Desktop"
    d.mkdir()
    return d


@pytest.fixture
def guard(sandbox):
    """以 sandbox 为白名单的守卫"""
    g = BasicGuard(whitelist=[str(sandbox)], audit_enabled=True, audit_db=":memory:")
    yield g
    g.close()


@pytest.fixture
def search_tool(guard):
    return FileSearchTool(guard)


@pytest.fixture
def list_tool(guard):
    return FileListTool(guard)


@pytest.fixture
def read_tool(guard):
    return FileReadTool(guard)


@pytest.fixture
def rename_tool(guard):
    return FileRenameTool(guard)


@pytest.fixture
def move_tool(guard):
    return FileMoveTool(guard)


@pytest.fixture
def delete_tool(guard):
    return FileDeleteTool(guard)


def make_file(dirpath: Path, name: str, content: str = "x", mtime_offset: float = 0) -> Path:
    """创建测试文件（可指定相对当前时间的偏移秒数）"""
    f = dirpath / name
    f.write_text(content, encoding="utf-8")
    if mtime_offset:
        ts = time.time() + mtime_offset
        os.utime(f, (ts, ts))
    return f


# ══════════════════════════════════════════════════════
#  工具框架
# ══════════════════════════════════════════════════════

class TestToolResult:
    """ToolResult 数据结构"""

    def test_ok_auto_count_list(self):
        """ok() 自动从列表推导条目数"""
        r = ToolResult.ok(data=[1, 2, 3], summary="三个")
        assert r.success is True
        assert r.count == 3

    def test_ok_auto_count_scalar(self):
        """标量数据的 count 为 1"""
        assert ToolResult.ok(data="x").count == 1

    def test_ok_none_count_zero(self):
        """None 数据 count 为 0"""
        assert ToolResult.ok(data=None).count == 0

    def test_ok_explicit_count(self):
        """显式 count 覆盖自动推导"""
        assert ToolResult.ok(data=[1, 2], count=99).count == 99

    def test_fail(self):
        """失败结果"""
        r = ToolResult.fail("出错了", emotion="sad")
        assert r.success is False
        assert r.error == "出错了"
        assert r.summary == "出错了"
        assert r.emotion == "sad"

    def test_fail_custom_summary(self):
        """失败可自定义用户文案"""
        r = ToolResult.fail("内部错误", summary="用户看到的文案")
        assert r.error == "内部错误"
        assert r.summary == "用户看到的文案"

    def test_empty(self):
        """空结果：成功但无数据"""
        r = ToolResult.empty("没有找到")
        assert r.success is True
        assert r.count == 0
        assert r.data == []


class TestBaseToolValidation:
    """参数校验"""

    def test_required_param_missing(self):
        """缺少必填参数抛 ParamError"""
        class T(BaseTool):
            name = "t"
            params_schema = {
                "type": "object",
                "properties": {"a": {"type": "string"}},
                "required": ["a"],
            }

            def execute(self, params):
                return ToolResult.ok()

        with pytest.raises(ParamError) as exc:
            T().validate_params({})
        assert exc.value.field == "a"

    def test_type_mismatch(self):
        """类型不符抛 ParamError"""
        class T(BaseTool):
            name = "t"
            params_schema = {
                "type": "object",
                "properties": {"n": {"type": "integer"}},
            }

            def execute(self, params):
                return ToolResult.ok()

        with pytest.raises(ParamError):
            T().validate_params({"n": "not a number"})

    def test_bool_not_accepted_as_integer(self):
        """布尔值不被当作整数接受"""
        class T(BaseTool):
            name = "t"
            params_schema = {"type": "object", "properties": {"n": {"type": "integer"}}}

            def execute(self, params):
                return ToolResult.ok()

        with pytest.raises(ParamError):
            T().validate_params({"n": True})

    def test_enum_validation(self):
        """枚举取值校验"""
        class T(BaseTool):
            name = "t"
            params_schema = {
                "type": "object",
                "properties": {"m": {"type": "string", "enum": ["a", "b"]}},
            }

            def execute(self, params):
                return ToolResult.ok()

        T().validate_params({"m": "a"})
        with pytest.raises(ParamError):
            T().validate_params({"m": "c"})

    def test_array_constraints(self):
        """数组长度约束"""
        class T(BaseTool):
            name = "t"
            params_schema = {
                "type": "object",
                "properties": {
                    "items": {"type": "array", "maxItems": 2},
                },
            }

            def execute(self, params):
                return ToolResult.ok()

        T().validate_params({"items": [1, 2]})
        with pytest.raises(ParamError):
            T().validate_params({"items": [1, 2, 3]})

    def test_unknown_field_allowed(self):
        """未声明字段放行（宽松策略）"""
        class T(BaseTool):
            name = "t"
            params_schema = {"type": "object", "properties": {"a": {"type": "string"}}}

            def execute(self, params):
                return ToolResult.ok()

        T().validate_params({"a": "x", "extra": 123})

    def test_no_schema_skips_validation(self):
        """无 schema 时跳过校验"""
        class T(BaseTool):
            name = "t"

            def execute(self, params):
                return ToolResult.ok()

        T().validate_params({"anything": 1})


class TestBaseToolMeta:
    """工具元信息"""

    def test_name_auto_derived(self):
        """工具名从类名自动推导"""
        class MyGreatTool(BaseTool):
            def execute(self, params):
                return ToolResult.ok()

        assert MyGreatTool.name == "my_great"

    def test_llm_schema_export(self):
        """导出 LLM function calling schema"""
        class T(BaseTool):
            name = "my_tool"
            description = "描述"
            params_schema = {"type": "object", "properties": {"a": {"type": "string"}}}

            def execute(self, params):
                return ToolResult.ok()

        schema = T().to_llm_schema()
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "my_tool"
        assert schema["function"]["description"] == "描述"

    def test_confirm_required_by_risk(self):
        """按风险等级判定是否需要确认"""
        class Low(BaseTool):
            name = "low"
            risk_level = "low"

            def execute(self, params):
                return ToolResult.ok()

        class High(BaseTool):
            name = "high"
            risk_level = "high"

            def execute(self, params):
                return ToolResult.ok()

        assert Low().confirm_required() is False
        assert High().confirm_required() is True

    def test_repr(self):
        """repr 含工具名与风险"""
        class T(BaseTool):
            name = "t"
            risk_level = "medium"

            def execute(self, params):
                return ToolResult.ok()

        assert "t" in repr(T())


# ══════════════════════════════════════════════════════
#  工具注册表
# ══════════════════════════════════════════════════════

class TestToolRegistry:
    """工具注册表"""

    def test_register_and_get(self, search_tool):
        """注册与获取"""
        reg = ToolRegistry()
        reg.register(search_tool)
        assert reg.get("file_search") is search_tool
        assert reg.has("file_search") is True
        assert len(reg) == 1

    def test_register_unnamed_raises(self):
        """无名工具注册失败"""
        from types import SimpleNamespace

        reg = ToolRegistry()
        with pytest.raises(ValueError):
            reg.register(SimpleNamespace(name=""))

    def test_disposer(self, search_tool):
        """注销函数生效"""
        reg = ToolRegistry()
        dispose = reg.register(search_tool)
        dispose()
        assert reg.has("file_search") is False

    def test_execute_unknown_tool(self):
        """执行未注册工具返回失败结果"""
        reg = ToolRegistry()
        result = reg.execute("nonexistent", {})
        assert result.success is False
        assert "nonexistent" in result.summary

    def test_execute_happy_path(self, search_tool, sandbox):
        """正常执行"""
        make_file(sandbox, "合同.pdf")
        reg = ToolRegistry()
        reg.register(search_tool)
        result = reg.execute("file_search", {"pattern": "*合同*"})
        assert result.success is True
        assert result.count == 1

    def test_execute_param_error_handled(self, search_tool):
        """参数错误被归一化为失败结果（不抛异常）"""
        reg = ToolRegistry()
        reg.register(search_tool)
        result = reg.execute("file_search", {})   # 缺必填 pattern
        assert result.success is False

    def test_execute_tool_exception_handled(self, guard):
        """工具抛异常被捕获并归一化"""
        class BoomTool(BaseTool):
            name = "boom"

            def execute(self, params):
                raise RuntimeError("炸了")

        reg = ToolRegistry()
        reg.register(BoomTool())
        result = reg.execute("boom", {})
        assert result.success is False
        assert "炸了" in result.summary

    def test_stats_tracking(self, search_tool, sandbox):
        """执行统计"""
        make_file(sandbox, "a.txt")
        reg = ToolRegistry()
        reg.register(search_tool)
        reg.execute("file_search", {"pattern": "*.txt"})
        reg.execute("file_search", {})   # 参数错误
        stats = reg.stats()["file_search"]
        assert stats["ok"] == 1
        assert stats["fail"] == 1

    def test_success_rate(self, search_tool, sandbox):
        """成功率计算"""
        make_file(sandbox, "a.txt")
        reg = ToolRegistry()
        reg.register(search_tool)
        assert reg.success_rate() == 1.0   # 无执行
        reg.execute("file_search", {"pattern": "*.txt"})
        assert reg.success_rate() == 1.0

    def test_llm_schemas(self, guard):
        """导出全部工具的 LLM schema"""
        reg = ToolRegistry()
        for t in all_file_tools(guard):
            reg.register(t)
        schemas = reg.to_llm_schemas()
        assert len(schemas) == 6
        names = {s["function"]["name"] for s in schemas}
        assert "file_search" in names
        assert "file_delete" in names

    def test_llm_schemas_filtered(self, guard):
        """按名过滤导出"""
        reg = ToolRegistry()
        for t in all_file_tools(guard):
            reg.register(t)
        schemas = reg.to_llm_schemas(["file_search"])
        assert len(schemas) == 1

    def test_risk_levels(self, guard):
        """风险等级查询"""
        reg = ToolRegistry()
        for t in all_file_tools(guard):
            reg.register(t)
        levels = reg.risk_levels()
        assert levels["file_search"] == "low"
        assert levels["file_delete"] == "high"
        assert levels["file_rename"] == "medium"

    def test_all_file_tools_count(self, guard):
        """all_file_tools 返回 6 个工具"""
        assert len(all_file_tools(guard)) == 6


# ══════════════════════════════════════════════════════
#  file_search
# ══════════════════════════════════════════════════════

class TestFileSearch:
    """文件搜索"""

    def test_find_by_pattern(self, search_tool, sandbox):
        """按模式搜索"""
        make_file(sandbox, "合同2024.pdf")
        make_file(sandbox, "合同草案.docx")
        make_file(sandbox, "无关文件.txt")

        r = search_tool.execute({"pattern": "*合同*"})
        assert r.success is True
        assert r.count == 2

    def test_find_by_extension(self, search_tool, sandbox):
        """按扩展名搜索"""
        make_file(sandbox, "a.pdf")
        make_file(sandbox, "b.pdf")
        make_file(sandbox, "c.txt")

        r = search_tool.execute({"pattern": "*.pdf"})
        assert r.count == 2

    def test_no_match_returns_empty_success(self, search_tool, sandbox):
        """无匹配返回成功但空结果"""
        make_file(sandbox, "a.txt")
        r = search_tool.execute({"pattern": "*不存在*"})
        assert r.success is True
        assert r.count == 0
        assert "没找到" in r.summary

    def test_truncation_at_limit(self, search_tool, sandbox):
        """结果超上限被截断并标记"""
        for i in range(MAX_SEARCH_RESULTS + 10):
            make_file(sandbox, f"test_{i:03d}.txt")

        r = search_tool.execute({"pattern": "*.txt"})
        assert r.truncated is True
        assert r.count == MAX_SEARCH_RESULTS
        assert "只显示前" in r.summary

    def test_results_sorted_by_mtime_desc(self, search_tool, sandbox):
        """结果按修改时间倒序"""
        make_file(sandbox, "old.txt", mtime_offset=-3600)
        make_file(sandbox, "new.txt", mtime_offset=0)
        r = search_tool.execute({"pattern": "*.txt"})
        assert r.data[0]["name"] == "new.txt"

    def test_time_range_filter(self, search_tool, sandbox):
        """时间范围过滤"""
        make_file(sandbox, "recent.txt", mtime_offset=-3600)       # 1 小时前
        make_file(sandbox, "ancient.txt", mtime_offset=-8 * 86400)  # 8 天前

        r = search_tool.execute({"pattern": "*.txt", "time_range": "last_week"})
        names = {f["name"] for f in r.data}
        assert "recent.txt" in names
        assert "ancient.txt" not in names

    def test_subdirectory_recursion(self, search_tool, sandbox):
        """递归搜索子目录"""
        sub = sandbox / "sub"
        sub.mkdir()
        make_file(sub, "deep.txt")
        r = search_tool.execute({"pattern": "*.txt"})
        assert r.count == 1

    def test_skips_system_dirs(self, search_tool, sandbox):
        """跳过 __pycache__ 等目录"""
        pycache = sandbox / "__pycache__"
        pycache.mkdir()
        make_file(pycache, "cached.pyc")
        make_file(sandbox, "real.txt")

        r = search_tool.execute({"pattern": "*"})
        names = {f["name"] for f in r.data}
        assert "cached.pyc" not in names

    def test_file_info_fields(self, search_tool, sandbox):
        """返回信息含必要字段"""
        make_file(sandbox, "a.txt", "hello")
        r = search_tool.execute({"pattern": "*.txt"})
        info = r.data[0]
        for key in ("name", "path", "size", "size_text", "mtime", "mtime_text"):
            assert key in info


# ══════════════════════════════════════════════════════
#  file_list
# ══════════════════════════════════════════════════════

class TestFileList:
    """列出目录"""

    def test_list_files(self, list_tool, sandbox):
        """列出文件"""
        make_file(sandbox, "a.txt")
        make_file(sandbox, "b.txt")
        r = list_tool.execute({})
        assert r.success is True
        assert r.count == 2

    def test_list_includes_subdirs(self, list_tool, sandbox):
        """默认包含子目录"""
        (sandbox / "folder").mkdir()
        make_file(sandbox, "a.txt")
        r = list_tool.execute({"include_dirs": True})
        assert r.count == 2

    def test_list_excludes_subdirs(self, list_tool, sandbox):
        """可排除子目录"""
        (sandbox / "folder").mkdir()
        make_file(sandbox, "a.txt")
        r = list_tool.execute({"include_dirs": False})
        assert r.count == 1

    def test_empty_dir(self, list_tool, sandbox):
        """空目录提示"""
        r = list_tool.execute({})
        assert r.count == 0
        assert "空" in r.summary

    def test_is_dir_flag(self, list_tool, sandbox):
        """目录项带 is_dir 标记"""
        (sandbox / "folder").mkdir()
        make_file(sandbox, "a.txt")
        r = list_tool.execute({})
        flags = {i["name"]: i["is_dir"] for i in r.data}
        assert flags["folder"] is True
        assert flags["a.txt"] is False

    def test_truncation(self, list_tool, sandbox):
        """超上限截断"""
        for i in range(MAX_LIST_RESULTS + 5):
            make_file(sandbox, f"f{i:03d}.txt")
        r = list_tool.execute({})
        assert r.truncated is True
        assert r.count == MAX_LIST_RESULTS

    def test_skips_system_dirs(self, list_tool, sandbox):
        """跳过系统目录"""
        (sandbox / "__pycache__").mkdir()
        make_file(sandbox, "a.txt")
        r = list_tool.execute({})
        assert "__pycache__" not in {i["name"] for i in r.data}


# ══════════════════════════════════════════════════════
#  file_read
# ══════════════════════════════════════════════════════

class TestFileRead:
    """读取文件"""

    def test_read_text_file(self, read_tool, sandbox):
        """读取文本文件内容"""
        make_file(sandbox, "note.txt", "你好世界")
        r = read_tool.execute({"target": "note.txt"})
        assert r.success is True
        assert r.data["content"] == "你好世界"

    def test_read_by_absolute_path(self, read_tool, sandbox):
        """绝对路径读取"""
        f = make_file(sandbox, "note.txt", "内容")
        r = read_tool.execute({"target": str(f)})
        assert r.success is True

    def test_read_identical_name_multiple_matches(self, read_tool, sandbox):
        """多个匹配时返回候选列表（让用户选择）"""
        sub = sandbox / "sub"
        sub.mkdir()
        make_file(sandbox, "报告.txt", "A")
        make_file(sub, "报告.txt", "B")

        r = read_tool.execute({"target": "报告"})
        assert r.success is True
        assert r.emotion == "think"
        assert "要打开哪个" in r.summary

    def test_read_too_large_rejected(self, read_tool, sandbox):
        """超大文件被拒绝"""
        big = sandbox / "big.txt"
        big.write_bytes(b"x" * (MAX_READ_BYTES + 1))
        r = read_tool.execute({"target": "big.txt"})
        assert r.success is False
        assert "太大" in r.summary

    def test_read_truncates_long_content(self, read_tool, sandbox):
        """超长内容被截断并标记"""
        make_file(sandbox, "long.txt", "字" * (MAX_READ_CHARS + 100))
        r = read_tool.execute({"target": "long.txt"})
        assert r.truncated is True
        assert len(r.data["content"]) == MAX_READ_CHARS

    def test_read_missing_file(self, read_tool, sandbox):
        """文件不存在给出友好提示"""
        r = read_tool.execute({"target": "不存在.txt"})
        assert r.success is False
        assert "没找到" in r.summary

    def test_read_empty_target(self, read_tool):
        """空目标提示用户"""
        r = read_tool.execute({"target": ""})
        assert r.success is False
        assert "哪个文件" in r.summary

    def test_read_outside_whitelist_rejected(self, read_tool, tmp_path):
        """白名单外路径被拒绝"""
        outside = tmp_path / "outside"
        outside.mkdir()
        f = make_file(outside, "secret.txt", "secret")
        r = read_tool.execute({"target": str(f)})
        assert r.success is False
        assert "不能动" in r.summary or "只能操作" in r.summary


# ══════════════════════════════════════════════════════
#  file_rename
# ══════════════════════════════════════════════════════

class TestFileRename:
    """重命名"""

    def test_rename_success(self, rename_tool, sandbox):
        """重命名成功"""
        make_file(sandbox, "a.txt", "内容")
        r = rename_tool.execute({"source": "a.txt", "target": "b"})
        assert r.success is True
        assert not (sandbox / "a.txt").exists()
        assert (sandbox / "b.txt").exists()

    def test_rename_preserves_extension(self, rename_tool, sandbox):
        """新名无扩展名时保留原扩展名"""
        make_file(sandbox, "报告.docx", "x")
        rename_tool.execute({"source": "报告.docx", "target": "年终总结"})
        assert (sandbox / "年终总结.docx").exists()

    def test_rename_explicit_extension(self, rename_tool, sandbox):
        """新名含扩展名时按原样使用"""
        make_file(sandbox, "a.txt", "x")
        rename_tool.execute({"source": "a.txt", "target": "b.md"})
        assert (sandbox / "b.md").exists()

    def test_rename_no_overwrite(self, rename_tool, sandbox):
        """目标已存在时不覆盖"""
        make_file(sandbox, "a.txt", "AAA")
        make_file(sandbox, "b.txt", "BBB")

        r = rename_tool.execute({"source": "a.txt", "target": "b.txt"})
        assert r.success is False
        assert (sandbox / "b.txt").read_text() == "BBB"   # 原内容未变
        assert (sandbox / "a.txt").read_text() == "AAA"   # 源文件仍在

    def test_rename_same_name_noop(self, rename_tool, sandbox):
        """新旧同名时不做操作"""
        make_file(sandbox, "a.txt", "x")
        r = rename_tool.execute({"source": "a.txt", "target": "a.txt"})
        assert r.success is True
        assert "一样" in r.summary

    def test_rename_missing_source(self, rename_tool, sandbox):
        """源文件不存在"""
        r = rename_tool.execute({"source": "不存在.txt", "target": "新名"})
        assert r.success is False

    def test_rename_empty_target(self, rename_tool, sandbox):
        """空目标名提示用户"""
        make_file(sandbox, "a.txt")
        r = rename_tool.execute({"source": "a.txt", "target": ""})
        assert r.success is False
        assert "什么名字" in r.summary

    def test_rename_audited(self, rename_tool, sandbox, guard):
        """重命名被记入审计日志"""
        make_file(sandbox, "a.txt")
        rename_tool.execute({"source": "a.txt", "target": "b.txt"})
        rows = guard.query_audit()
        assert any(r["action"] == "file_rename" for r in rows)

    def test_rename_chinese_name(self, rename_tool, sandbox):
        """中文名重命名"""
        make_file(sandbox, "旧文件.txt", "x")
        r = rename_tool.execute({"source": "旧文件.txt", "target": "新文件"})
        assert r.success is True
        assert (sandbox / "新文件.txt").exists()


# ══════════════════════════════════════════════════════
#  file_move
# ══════════════════════════════════════════════════════

class TestFileMove:
    """移动文件"""

    def test_move_success(self, guard, tmp_path):
        """移动到另一个白名单目录"""
        src_dir = tmp_path / "Desktop"
        dst_dir = tmp_path / "Documents"
        src_dir.mkdir(exist_ok=True)
        dst_dir.mkdir(exist_ok=True)

        g = BasicGuard(whitelist=[str(src_dir), str(dst_dir)], audit_enabled=False)
        try:
            make_file(src_dir, "a.txt", "内容")
            tool = FileMoveTool(g)
            r = tool.execute({"source": str(src_dir / "a.txt"), "dest": "Documents"})
            assert r.success is True
            assert (dst_dir / "a.txt").exists()
            assert not (src_dir / "a.txt").exists()
        finally:
            g.close()

    def test_move_no_overwrite(self, guard, tmp_path):
        """目标目录已有同名文件时报错"""
        src_dir = tmp_path / "Desktop"
        dst_dir = tmp_path / "Documents"
        src_dir.mkdir(exist_ok=True)
        dst_dir.mkdir(exist_ok=True)

        g = BasicGuard(whitelist=[str(src_dir), str(dst_dir)], audit_enabled=False)
        try:
            make_file(src_dir, "a.txt", "SRC")
            make_file(dst_dir, "a.txt", "DST")
            tool = FileMoveTool(g)
            r = tool.execute({"source": str(src_dir / "a.txt"), "dest": "Documents"})
            assert r.success is False
            assert (dst_dir / "a.txt").read_text() == "DST"
        finally:
            g.close()

    def test_move_missing_dest(self, move_tool, sandbox):
        """空目标目录提示用户"""
        make_file(sandbox, "a.txt")
        r = move_tool.execute({"source": "a.txt", "dest": ""})
        assert r.success is False
        assert "哪个文件夹" in r.summary

    def test_move_unknown_dest(self, move_tool, sandbox):
        """目标目录不存在"""
        make_file(sandbox, "a.txt")
        r = move_tool.execute({"source": "a.txt", "dest": "Desktop"})
        # sandbox 本身就是白名单根，移动到自身 → 视为已在目标位置
        assert r.success is True


# ══════════════════════════════════════════════════════
#  file_delete（R1 安全契约）
# ══════════════════════════════════════════════════════

class TestFileDeleteSafety:
    """删除安全契约 — R1 核心验证"""

    def test_permanent_delete_rejected(self, delete_tool, sandbox):
        """永久删除请求必须被拒绝"""
        make_file(sandbox, "a.txt")
        with pytest.raises(SecurityError) as exc:
            delete_tool.execute({"targets": [str(sandbox / "a.txt")], "permanent": True})
        assert "永久删除" in str(exc.value)
        assert (sandbox / "a.txt").exists()   # 文件未被删除

    def test_uses_send2trash(self, delete_tool, sandbox, monkeypatch):
        """删除调用 send2trash（而非永久删除）"""
        import send2trash as s2t

        called = []
        monkeypatch.setattr(s2t, "send2trash", lambda p: called.append(p))

        make_file(sandbox, "a.txt")
        r = delete_tool.execute({"targets": [str(sandbox / "a.txt")]})
        assert r.success is True
        assert len(called) == 1

    def test_source_has_no_permanent_delete(self):
        """源码审查：file_delete 中不得出现永久删除调用"""
        src_path = project_root / "agent" / "tools" / "file_tools.py"
        source = src_path.read_text(encoding="utf-8")

        # 提取 FileDeleteTool 类体
        match = re.search(
            r"class FileDeleteTool.*?(?=\nclass |\n# ═|\Z)",
            source,
            re.DOTALL,
        )
        assert match, "未找到 FileDeleteTool 类"
        body = match.group(0)

        forbidden = ["os.remove", "os.unlink", ".unlink(", "shutil.rmtree", "os.rmdir"]
        found = [f for f in forbidden if f in body]
        assert not found, f"file_delete 中禁止出现永久删除调用: {found}"

    def test_delete_outside_whitelist_rejected(self, delete_tool, tmp_path):
        """白名单外路径删除被拒绝"""
        outside = tmp_path / "outside"
        outside.mkdir()
        f = make_file(outside, "secret.txt")

        r = delete_tool.execute({"targets": [str(f)]})
        assert r.success is False
        assert f.exists()   # 文件仍在

    def test_delete_traversal_rejected(self, delete_tool, sandbox, tmp_path):
        """目录穿越删除被拒绝"""
        outside = tmp_path / "outside"
        outside.mkdir()
        f = make_file(outside, "secret.txt")

        attack = str(sandbox / ".." / "outside" / "secret.txt")
        r = delete_tool.execute({"targets": [attack]})
        assert r.success is False
        assert f.exists()

    def test_delete_batch_limit(self, delete_tool, sandbox):
        """批量删除超上限被拒绝"""
        from agent.providers.safety.basic_guard import MAX_BATCH_SIZE

        targets = [str(sandbox / f"f{i}.txt") for i in range(MAX_BATCH_SIZE + 1)]
        r = delete_tool.execute({"targets": targets})
        assert r.success is False
        assert str(MAX_BATCH_SIZE) in r.summary

    def test_delete_audited(self, delete_tool, sandbox, guard, monkeypatch):
        """删除被记入审计日志"""
        import send2trash as s2t

        monkeypatch.setattr(s2t, "send2trash", lambda p: None)
        make_file(sandbox, "a.txt")
        delete_tool.execute({"targets": [str(sandbox / "a.txt")]})

        rows = guard.query_audit()
        assert any(r["action"] == "file_delete" for r in rows)

    def test_delete_no_targets(self, delete_tool):
        """无目标时提示用户"""
        r = delete_tool.execute({})
        assert r.success is False
        assert "哪些文件" in r.summary

    @pytest.mark.parametrize("bad_item", [["a.txt"], {"p": "a.txt"}, 123, 1.5, True])
    def test_delete_non_string_target_item_rejected(self, delete_tool, sandbox,
                                                    bad_item, monkeypatch):
        """`targets` 里的非字符串项必须**整条拒绝**，绝不 `str()` 之后照删（P5-A2 / D18）

        原先 `raw.extend([str(t) for t in explicit if t])` 会把 `[["C:\\a.txt"]]` 变成
        字符串 `"['C:\\\\a.txt']"` —— 一个"看起来像列表的路径"。危险不在于它通常会删不到
        （那种假路径落在白名单外，被守卫拦下，用户听到的是"权限不够"，**真原因永不出现**），
        而在于若 `str(t)` 恰好拼出一个**白名单内且真实存在**的名字，它就会安静地删掉那个文件。
        凡"位置决定后果"的输入一律在入口拒绝。
        """
        import send2trash as s2t

        deleted = []
        monkeypatch.setattr(s2t, "send2trash", lambda p: deleted.append(p))
        make_file(sandbox, "a.txt")

        r = delete_tool.execute({"targets": [bad_item]})
        assert r.success is False
        assert deleted == [], f"{bad_item!r} 竟然触发了删除：{deleted}"
        assert "没看懂" in r.summary or "哪些文件" in r.summary

    def test_delete_non_string_item_error_names_the_problem(self, delete_tool, sandbox):
        """报错要说出**真正的**原因，而不是让守卫去报"权限不够"

        这是本条修复的核心价值：同一个畸形输入，修前用户听到的是
        「删除失败了，可能是权限不够呢」（指向权限），修后是
        「你要删的是哪些文件呀？我没看懂这一串。」—— 指向**输入**。
        """
        r = delete_tool.execute({"targets": [[str(sandbox / "a.txt")]]})
        assert r.success is False
        assert "权限" not in r.summary

    def test_delete_preview_also_refuses_non_string_items(self, delete_tool, sandbox):
        """预览与执行结论一致：都不对畸形 target 给出"找到 N 个文件"的假承诺"""
        make_file(sandbox, "a.txt")
        assert delete_tool.preview({"targets": [[str(sandbox / "a.txt")]]}) == ""
        assert delete_tool.preview({"targets": [str(sandbox / "a.txt")]}) != ""

    def test_delete_still_accepts_plain_string_items(self, delete_tool, sandbox,
                                                    monkeypatch):
        """反方向：正常字符串项一步不受影响（防"修到不能删"）"""
        import send2trash as s2t

        deleted = []
        monkeypatch.setattr(s2t, "send2trash", lambda p: deleted.append(p))
        a = make_file(sandbox, "a.txt")
        b = make_file(sandbox, "b.txt")

        r = delete_tool.execute({"targets": [str(a), str(b)]})
        assert r.success is True and len(deleted) == 2

    def test_delete_ignores_falsy_items_including_empty_string(self, delete_tool, sandbox,
                                                              monkeypatch):
        """空串 / `None` 这类"空项"仍按**缺省**处理（不算畸形）

        `False` 会走畸形分支（它是非字符串值）；空串与 `None` 是"没填"，
        与 `if t` 的旧行为一致 —— 这条用来钉住"我只收紧了非字符串，没有顺手改空值语义"。
        """
        import send2trash as s2t

        deleted = []
        monkeypatch.setattr(s2t, "send2trash", lambda p: deleted.append(p))
        a = make_file(sandbox, "a.txt")

        r = delete_tool.execute({"targets": [str(a), "", None]})
        assert r.success is True and deleted == [str(a)]

    def test_delete_missing_file(self, delete_tool, sandbox):
        """目标不存在"""
        r = delete_tool.execute({"targets": [str(sandbox / "不存在.txt")]})
        assert r.success is False

    def test_delete_by_pattern(self, delete_tool, sandbox, monkeypatch):
        """按模式删除"""
        import send2trash as s2t

        deleted = []
        monkeypatch.setattr(s2t, "send2trash", lambda p: deleted.append(p))

        for i in range(3):
            make_file(sandbox, f"截图_{i}.png")
        make_file(sandbox, "文档.txt")

        r = delete_tool.execute({"pattern": "*截图*"})
        assert r.success is True
        assert r.count == 3
        assert len(deleted) == 3

    def test_delete_duplicate_targets_deduped(self, delete_tool, sandbox, monkeypatch):
        """重复目标被去重"""
        import send2trash as s2t

        deleted = []
        monkeypatch.setattr(s2t, "send2trash", lambda p: deleted.append(p))

        f = make_file(sandbox, "a.txt")
        delete_tool.execute({"targets": [str(f), str(f), str(f)]})
        assert len(deleted) == 1

    def test_delete_partial_failure_reported(self, delete_tool, sandbox, monkeypatch):
        """部分失败时在摘要中体现"""
        import send2trash as s2t

        def flaky(p):
            if "bad" in str(p):
                raise OSError("权限不足")

        monkeypatch.setattr(s2t, "send2trash", flaky)

        make_file(sandbox, "good.txt")
        make_file(sandbox, "bad.txt")

        r = delete_tool.execute({
            "targets": [str(sandbox / "good.txt"), str(sandbox / "bad.txt")]
        })
        assert r.success is True
        assert r.count == 1
        assert "没删成功" in r.summary


class TestFileDeleteRealTrash:
    """真实回收站验证（集成测试）"""

    def test_real_trash_removal(self, guard, sandbox):
        """真实调用 send2trash 后文件从原位置消失"""
        f = make_file(sandbox, "to_trash_测试文件.txt", "content")
        tool = FileDeleteTool(guard)

        r = tool.execute({"targets": [str(f)]})
        assert r.success is True
        assert not f.exists(), "文件应已移入回收站"

        # 注：回收站内容需人工确认（见验收清单场景 2）


# ══════════════════════════════════════════════════════
#  跨工具协作
# ══════════════════════════════════════════════════════

class TestToolInteraction:
    """工具组合场景"""

    def test_search_then_delete(self, guard, sandbox, monkeypatch):
        """搜索 → 删除 组合流程"""
        import send2trash as s2t

        deleted = []
        monkeypatch.setattr(s2t, "send2trash", lambda p: deleted.append(p))

        for i in range(3):
            make_file(sandbox, f"temp_{i}.tmp")
        make_file(sandbox, "keep.txt")

        reg = ToolRegistry()
        for t in all_file_tools(guard):
            reg.register(t)

        found = reg.execute("file_search", {"pattern": "*.tmp"})
        assert found.count == 3

        targets = [f["path"] for f in found.data]
        removed = reg.execute("file_delete", {"targets": targets})
        assert removed.success is True
        assert len(deleted) == 3
        assert (sandbox / "keep.txt").exists()

    def test_search_then_rename(self, guard, sandbox):
        """搜索 → 重命名 组合流程"""
        make_file(sandbox, "草稿_报告.docx")

        reg = ToolRegistry()
        for t in all_file_tools(guard):
            reg.register(t)

        found = reg.execute("file_search", {"pattern": "*草稿*"})
        assert found.count == 1

        renamed = reg.execute("file_rename", {
            "source": found.data[0]["path"],
            "target": "正式报告",
        })
        assert renamed.success is True
        assert (sandbox / "正式报告.docx").exists()

    def test_registry_success_rate_across_tools(self, guard, sandbox):
        """跨工具统计成功率"""
        make_file(sandbox, "a.txt")
        reg = ToolRegistry()
        for t in all_file_tools(guard):
            reg.register(t)

        reg.execute("file_search", {"pattern": "*.txt"})
        reg.execute("file_list", {})
        reg.execute("file_read", {"target": "a.txt"})

        assert reg.success_rate() == 1.0
        assert len(reg.stats()) == 6


def test_human_size_covers_every_unit():
    """`_human_size` 的每一档都要被真正断言到（不接受 pragma 屏蔽）

    原实现在循环里连 GB 一起处理，末尾留一个"逻辑上不可达"的 return，
    并用 `# pragma: no cover` 屏蔽 —— 那等于用屏蔽换覆盖率，项目不允许。
    现已重构成"循环处理 B/KB/MB + GB 兜底"，每一行都可达。
    """
    from agent.tools.file_tools import _human_size

    assert _human_size(0) == "0 B"
    assert _human_size(500) == "500 B"
    assert _human_size(2048) == "2.0 KB"
    assert _human_size(5 * 1024 ** 2) == "5.0 MB"
    assert _human_size(3 * 1024 ** 3) == "3.0 GB"
    assert _human_size(2 * 1024 ** 4) == "2048.0 GB"   # TB 也走 GB 兜底


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
