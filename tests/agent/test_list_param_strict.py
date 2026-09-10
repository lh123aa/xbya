"""P4-B2：收列表参数收到字符串时的契约（缺陷 17b）

## 缺陷是什么

`file_search(dirs="Downloads")` 这类调用里，`dirs` 声明是**数组**，
但字符串在 Python 里**可迭代** —— `for item in "Downloads"` 会把 D/o/w/n/l/o/a/d/s
逐个拿去解析，一个都匹配不上，于是落到 `_resolve_dirs` 末尾的
`return result or self._default_dirs()`，**静默回退成四个目录**。

后果不是"报错"，而是"范围悄悄变大"：
- `file_search(dirs="Downloads", pattern="*.txt")` 实测命中从 **1 个变成 3 个**
  （多出来的是 Desktop 里的），而这些路径正是多步规划里
  `file_delete` 步骤的输入（`${s1.paths}`）—— 用户没点名的目录里的文件进了待删列表。
- `file_move(dest="不存在的目录")` → 回退四目录 → 取 `dirs[0]` = **Desktop**
  → 文件被挪到桌面**并报成功**。

第二处比第一处更危险：它是**写操作**，且结果与用户的要求完全不同。

## 契约（按 `p4-plan.md` 风险条 R4：「显式拒绝 > 静默转换」）

| `dirs` 入参 | 行为 |
|-------------|------|
| 列表（可识别） | 用这些目录 |
| 列表（全部认不出） | **拒绝**（抛 `ParamError`），不回退默认目录 |
| 字符串（含逗号串） | **拒绝**：期望数组，并给出可照抄的写法 |
| `None` / `[]` | 未指定 → 默认四目录（唯一允许的兜底） |

`targets` 同理，且**额外**保证：字符串既不被拆开、也不被当成单元素列表
（"把一句话拆成多个待删目标"是最危险的宽松转换）。
"""

from pathlib import Path

import pytest

from agent.pipeline import AgentPipeline
from agent.providers.safety.basic_guard import BasicGuard
from agent.tools.base import BaseTool, ParamError, ToolResult
from agent.tools.file_tools import (
    FileDeleteTool,
    FileListTool,
    FileMoveTool,
    FileSearchTool,
)
from agent.tools.registry import ToolRegistry


@pytest.fixture
def sandbox_roots(tmp_path):
    """四个目录，内容刻意不同 —— 让"范围被扩大"能用数字看出来"""
    roots = []
    for name, files in (("Desktop", ["a.txt", "b.txt"]),
                        ("Documents", []),
                        ("Downloads", ["c.txt"]),
                        ("Pictures", [])):
        d = tmp_path / name
        d.mkdir()
        roots.append(d)
        for f in files:
            (d / f).write_text("x", encoding="utf-8")
    return roots


@pytest.fixture
def guard(sandbox_roots):
    g = BasicGuard(whitelist=[str(p) for p in sandbox_roots], audit_enabled=False)
    yield g
    g.close()


@pytest.fixture
def reg(guard):
    r = ToolRegistry()
    r.register(FileSearchTool(guard))
    r.register(FileListTool(guard))
    r.register(FileMoveTool(guard))
    r.register(FileDeleteTool(guard))
    return r


def _names(result) -> list:
    """从 file_search 的结果里取文件名（execute 直接返回 list）"""
    data = result.data
    if not isinstance(data, list):
        return []
    return sorted(Path(x["path"]).name if isinstance(x, dict) else Path(x).name
                  for x in data)


# ══════════════════════════════════════════════════
#  一、四态：list / str / None / 逗号串
# ══════════════════════════════════════════════════

class TestDirsFourStates:
    """`dirs` 的四种输入各自有确定行为"""

    def test_list_is_honoured(self, guard, sandbox_roots):
        """① 列表：只搜点名的目录"""
        tool = FileSearchTool(guard)
        got = tool._resolve_dirs(["Downloads"])
        assert [p.name for p in got] == ["Downloads"]

    def test_none_means_unspecified(self, guard, sandbox_roots):
        """③ None / 空列表：未指定 → 默认四目录（唯一允许的兜底）"""
        tool = FileSearchTool(guard)
        assert len(tool._resolve_dirs(None)) == 4
        assert len(tool._resolve_dirs([])) == 4

    def test_all_blank_list_is_rejected(self, guard):
        """全是空串的列表 = "给了但没给内容" → 拒绝（不是"未指定"）"""
        with pytest.raises(ParamError):
            FileSearchTool(guard)._resolve_dirs([""])

    def test_none_and_list_differ_in_scope(self, guard):
        """None（未指定）与 ["Downloads"]（点名）**必须**给出不同范围

        这是缺陷的核心对照：改动前字符串版和 None 版走同一个出口，
        于是"用户点名了"和"用户没说"不可区分。
        """
        tool = FileSearchTool(guard)
        assert len(tool._resolve_dirs(None)) != len(tool._resolve_dirs(["Downloads"]))

    @pytest.mark.parametrize("bad", ["Downloads", "Desktop", "下载",
                                     "Desktop,Documents", "Desktop, Documents"])
    def test_string_is_rejected(self, guard, bad):
        """② 字符串（含逗号串）：明确拒绝，且文案给出可照抄的写法"""
        tool = FileSearchTool(guard)
        with pytest.raises(ParamError) as ei:
            tool._resolve_dirs(bad)
        msg = str(ei.value)
        assert "期望数组" in msg
        assert "收到字符串" in msg
        assert f'["{bad}"]' in msg, "必须给出「写成数组」的具体写法"

    def test_comma_string_is_not_split(self, guard):
        """④ 逗号串**不拆**：绝不把 "Desktop,Documents" 变成两个目录"""
        tool = FileSearchTool(guard)
        with pytest.raises(ParamError):
            tool._resolve_dirs("Desktop,Documents")

    def test_fully_unknown_list_is_rejected(self, guard):
        """列表全部认不出时拒绝，不回退默认目录"""
        tool = FileSearchTool(guard)
        with pytest.raises(ParamError) as ei:
            tool._resolve_dirs(["不存在的目录"])
        assert "不认识" in str(ei.value)

    def test_partially_known_list_keeps_known(self, guard, sandbox_roots):
        """列表部分可识别 → 用可识别的（非法项记日志跳过）"""
        tool = FileSearchTool(guard)
        outside = sandbox_roots[0].parent / "outside"
        outside.mkdir(exist_ok=True)
        got = tool._resolve_dirs([str(outside), "Downloads"])
        assert [p.name for p in got] == ["Downloads"]

    def test_duplicate_entry_counts_as_unknown(self, guard):
        """重复出现同一目录 → 第二次被计入"没认出来"（去重分支）

        这条同时钉住一个细节：目录**去重**后若还能认出至少一个，就仍然可用；
        `unknown` 只用于日志，不影响结果。
        """
        tool = FileSearchTool(guard)
        got = tool._resolve_dirs(["Downloads", "Downloads"])
        assert [p.name for p in got] == ["Downloads"]

    def test_whitelisted_but_missing_dir_is_unknown(self, tmp_path):
        """白名单里有、但磁盘上不存在的目录 → 计入 unknown（不至于当成可用目录）"""
        real = tmp_path / "Downloads"
        real.mkdir()
        ghost = tmp_path / "Ghost"
        g = BasicGuard(whitelist=[str(real), str(ghost)], audit_enabled=False)
        try:
            got = FileSearchTool(g)._resolve_dirs(["Ghost", "Downloads"])
            assert [p.name for p in got] == ["Downloads"]
        finally:
            g.close()

    def test_all_missing_dirs_is_rejected(self, tmp_path):
        """白名单里存在但盘上都没有 → 拒绝（不是默默用空范围）"""
        ghost = tmp_path / "Ghost"
        g = BasicGuard(whitelist=[str(ghost)], audit_enabled=False)
        try:
            with pytest.raises(ParamError):
                FileSearchTool(g)._resolve_dirs(["Ghost"])
        finally:
            g.close()


class TestFileListRejectsBadDirs:
    """`file_list` 与 `file_search` 必须走同一条拒绝路径（两个工具都要覆盖）

    两种拒绝**文案不同，且这是有意的**，测试要把这个区别钉住：

    | 情形 | 拦在哪 | 用户听到的 |
    |------|--------|-----------|
    | `dirs` 是字符串 | `validate_params`（类型层，`execute` 还没跑） | 「这个指令我还没完全理解，换个说法再试试好吗？」—— 类型错属于"上游产出的参数不对"，不该把技术报文念给用户 |
    | `dirs` 是列表但认不出 | `_dirs_or_error`（工具层，类型是对的、名字不认识） | 「「X」这个位置我没听懂哦，我只能操作：…」—— 用户确实说了个位置，得告诉他哪里不对 |
    """

    def test_list_string_dirs_rejected_by_validation(self, reg):
        """字符串 → 类型层拒绝，技术原文留在 error 里"""
        r = reg.execute("file_list", {"dirs": "Downloads"})
        assert r.success is False
        assert "期望数组" in r.error
        assert '["Downloads"]' in r.error
        assert "完全理解" in r.summary, "类型错走通用文案（不念技术报文）"

    def test_list_unknown_dirs_rejected_with_readable_reason(self, reg):
        """列表但认不出 → 工具层拒绝，文案点明是哪个位置"""
        r = reg.execute("file_list", {"dirs": ["不存在的目录"]})
        assert r.success is False
        assert "没听懂" in r.summary
        assert "不存在的目录" in r.summary

    def test_list_valid_dirs_works(self, reg, sandbox_roots):
        r = reg.execute("file_list", {"dirs": ["Downloads"]})
        assert r.success is True


# ══════════════════════════════════════════════════
#  二、范围不再被静默扩大（用数字证明）
# ══════════════════════════════════════════════════

class TestScopeIsNotWidened:
    """改动前字符串版会把 Desktop 的文件也搜进来，成为删除步骤的输入"""

    def test_string_dirs_no_longer_widens_search(self, guard):
        """字符串 `dirs` 现在被拒绝，**不再**返回别的目录里的文件"""
        tool = FileSearchTool(guard)
        r = tool.execute({"dirs": "Downloads", "pattern": "*.txt"})
        assert r.success is False
        assert _names(r) == [], "拒绝时不得返回任何文件"

    def test_list_dirs_scope_is_exact(self, guard):
        """点名 Downloads 就只搜 Downloads（对照：改动前字符串版会给 3 个）"""
        tool = FileSearchTool(guard)
        r = tool.execute({"dirs": ["Downloads"], "pattern": "*.txt"})
        assert r.success is True
        assert _names(r) == ["c.txt"]

    def test_registry_reports_actionable_message(self, reg):
        """经注册表时给的是"能照做"的文案，而不是含糊的失败"""
        r = reg.execute("file_search", {"dirs": "Downloads", "pattern": "*"})
        assert r.success is False
        assert "期望数组" in r.error
        assert '["Downloads"]' in r.error

    def test_delete_with_string_dirs_deletes_nothing(self, reg, sandbox_roots):
        """`file_delete(dirs=<str>, pattern=...)` 一个都不许删"""
        before = sorted(p.name for p in sandbox_roots[0].iterdir())
        r = reg.execute("file_delete", {"dirs": "Downloads", "pattern": "*.txt"})
        assert r.success is False
        after = sorted(p.name for p in sandbox_roots[0].iterdir())
        assert after == before == ["a.txt", "b.txt"]


# ══════════════════════════════════════════════════
#  三、file_move 的目标目录绝不被替换
# ══════════════════════════════════════════════════

class TestMoveDestIsNeverSubstituted:
    """实测过的最危险一条：认不出的目标目录会静默变成 Desktop（`dirs[0]`）"""

    @pytest.mark.parametrize("dest", ["不存在的目录", "下载", "软件归档"])
    def test_unknown_dest_is_rejected_and_file_untouched(self, guard, sandbox_roots, dest):
        src = sandbox_roots[2] / "c.txt"          # 源在 Downloads
        r = FileMoveTool(guard).execute({"source": str(src), "dest": dest})
        assert r.success is False
        assert src.exists(), "拒绝时绝不能移动文件"
        # 关键：绝不允许它出现在 Desktop
        assert not (sandbox_roots[0] / "c.txt").exists()

    def test_known_dest_still_works(self, guard, sandbox_roots):
        """正常的英文目录名照常工作（不能因为收紧而把功能弄坏）"""
        src = sandbox_roots[2] / "c.txt"
        r = FileMoveTool(guard).execute({"source": str(src), "dest": "Documents"})
        assert r.success is True
        assert (sandbox_roots[1] / "c.txt").exists()

    def test_guard_rejects_out_of_whitelist_dest(self, guard, sandbox_roots):
        """白名单外的绝对路径由安全守卫拦下（这条一直是好的，防回退）"""
        src = sandbox_roots[2] / "c.txt"
        r = FileMoveTool(guard).execute(
            {"source": str(src), "dest": r"C:\Windows\System32"})
        assert r.success is False
        assert src.exists()


# ══════════════════════════════════════════════════
#  四、targets：字符串既不拆开也不当单元素
# ══════════════════════════════════════════════════

class TestDeleteTargetsStrict:
    """`targets` 是删除目标：最不能宽松转换的字段"""

    def test_string_targets_is_ignored_not_split(self, guard, sandbox_roots):
        """`_collect_targets` 收字符串 → 空列表（整条忽略），绝不逐字符拆"""
        tool = FileDeleteTool(guard)
        raw = "C:\\x\\a.txt"
        assert tool._collect_targets({"targets": raw}) == []
        assert tool._collect_targets({"targets": [raw]}) == [raw]

    def test_string_targets_rejected_by_validation(self, reg):
        """经注册表时一律拒绝（不猜用户想把一个字符串当成什么）"""
        r = reg.execute("file_delete", {"targets": "C:\\x\\a.txt"})
        assert r.success is False
        assert "期望数组" in r.error
        assert r'["C:\x\a.txt"]' in r.error

    def test_list_targets_still_works(self, guard, sandbox_roots):
        """正常的列表照常（防"收紧到不能干活"）"""
        f = sandbox_roots[0] / "a.txt"
        raw = FileDeleteTool(guard)._collect_targets({"targets": [str(f)]})
        assert raw == [str(f)]

    def test_unparseable_dirs_with_pattern_raises_tool_error(self, guard):
        """`dirs` 认不出 + 有 pattern → 抛 ToolError，而不是默认四目录照删"""
        from agent.tools.base import ToolError
        tool = FileDeleteTool(guard)
        with pytest.raises(ToolError) as ei:
            tool._collect_targets({"dirs": ["不存在"], "pattern": "*.txt"})
        assert "没听懂" in ei.value.user_message

    def test_execute_turns_tool_error_into_readable_failure(self, guard):
        """`execute` 把 ToolError 转成用户能看懂的一句话"""
        r = FileDeleteTool(guard).execute(
            {"dirs": ["不存在"], "pattern": "*.txt"})
        assert r.success is False
        assert "没听懂" in r.summary


# ══════════════════════════════════════════════════
#  五、参数校验集中在 BaseTool.validate_params
# ══════════════════════════════════════════════════

class _ArrayTool(BaseTool):
    """最小工具：一个数组字段，用来单测集中化的校验"""

    name = "array_probe"
    params_schema = {
        "type": "object",
        "properties": {"items": {"type": "array", "items": {"type": "string"}}},
    }

    def execute(self, params):                     # pragma: no cover - 用不到
        return ToolResult.ok(params)


class TestCentralValidation:
    """校验只在 `validate_params` 一处（p4-plan.md 的验收条件之一）"""

    def test_string_for_array_field_rejected(self):
        with pytest.raises(ParamError) as ei:
            _ArrayTool().validate_params({"items": "Downloads"})
        assert "期望数组" in str(ei.value)
        assert '["Downloads"]' in str(ei.value)

    def test_comma_string_rejected_not_split(self):
        """逗号串是同一条分支 —— 不拆"""
        with pytest.raises(ParamError):
            _ArrayTool().validate_params({"items": "a,b,c"})

    def test_real_list_accepted(self):
        _ArrayTool().validate_params({"items": ["a", "b"]})

    def test_error_names_the_field(self):
        with pytest.raises(ParamError) as ei:
            _ArrayTool().validate_params({"items": "x"})
        assert ei.value.field == "items"
        assert ei.value.tool_name == "array_probe"

    def test_empty_string_still_rejected(self):
        """空字符串也拒绝：它不是"未提供"（未提供是 None / 不传这个键）"""
        with pytest.raises(ParamError):
            _ArrayTool().validate_params({"items": ""})

    def test_explicit_none_is_treated_as_absent(self):
        """显式 null 仍视为"没填"（回归保护：function calling 常见写法）"""
        _ArrayTool().validate_params({"items": None})


# ══════════════════════════════════════════════════
#  六、预览与执行必须说同一件事
# ══════════════════════════════════════════════════

class TestPreviewMatchesExecution:
    """`preview()` 不做类型校验，而 `execute` 会 —— 两者不一致就是"确认流程撒谎"

    实测（改动前）：`file_delete(dirs="Downloads", pattern="*.txt")` 的预览是
    "找到 3 个文件（c.txt、a.txt、b.txt）"，而执行直接报类型错、一个都不删。
    """

    @pytest.fixture
    def pipe(self, reg, guard):
        """只要 `_registry` 就能测 `_tool_preview`

        真的 `AgentPipeline` 需要 bus/router/executor 等五个依赖，
        而 `_tool_preview` 只读 `self._registry` —— 用最小替身把测试钉在
        **被测的那一个方法**上，避免因为无关依赖变化而变红。
        """
        class _Probe:
            _registry = reg

        _Probe._tool_preview = AgentPipeline._tool_preview
        return _Probe()

    def test_invalid_params_give_no_preview(self, pipe):
        """参数不合法 → 不给预览（与"执行必然失败"这件事一致）"""
        assert pipe._tool_preview(
            "file_delete", {"dirs": "Downloads", "pattern": "*.txt"}) == ""

    def test_valid_params_still_give_preview(self, pipe, sandbox_roots):
        """合法参数 → 正常预览（反方向验证：不是"永远返回空串"）"""
        got = pipe._tool_preview(
            "file_delete", {"dirs": ["Downloads"], "pattern": "*.txt"})
        assert "c.txt" in got

    def test_preview_scope_matches_execute_scope(self, pipe, sandbox_roots):
        """预览里报的文件数 = 真正会被删的文件数（同一份参数）"""
        preview = pipe._tool_preview(
            "file_delete", {"dirs": ["Desktop"], "pattern": "*.txt"})
        assert "2 个文件" in preview, preview

    def test_preview_exception_is_swallowed(self, pipe, reg, monkeypatch):
        """工具自身 preview 抛异常 → 空串，不影响确认流程（回归保护）"""
        tool = reg.get("file_delete")

        def boom(params):
            raise RuntimeError("boom")

        monkeypatch.setattr(tool, "preview", boom)
        assert pipe._tool_preview("file_delete", {"targets": []}) == ""
