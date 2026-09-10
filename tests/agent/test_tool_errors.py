"""工具错误契约测试（P3 收尾时新增）

覆盖三处由"真实 LLM 端到端验证"暴露出来的缺陷修复：

1. `BaseTool.validate_params` —— **显式 None 的可选字段应视为"未提供"**
   LLM（function calling）把可空参数写成 `{"time_range": null}` 是家常便饭；
   原先按类型错误拒绝，会让整条工具调用失败。实测：LLM 规划器的第 1 步因此报
   `参数错误 [file_search].time_range: 类型错误：期望 string，收到 NoneType`，
   把整单计划打断。
2. 执行器把 `ParamError` 映射成**可行动的**用户文案（原始报错只进日志）
3. 执行器把 `ToolError` 映射成它自带的 `user_message`
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from agent.providers.executor.thread_pool import ThreadPoolExecutorProvider
from agent.tools.base import BaseTool, ParamError, ToolError, ToolResult
from agent.tools.registry import ToolRegistry


class _SchemaTool(BaseTool):
    """带可选字段的工具（用于验证 None 语义）"""

    name = "schema_tool"
    description = "带可选字段"
    risk_level = "low"
    params_schema = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string"},
            "dirs": {"type": "array", "items": {"type": "string"}},
            "time_range": {"type": "string", "enum": ["today", "last_week"]},
            "limit": {"type": "integer"},
            "flag": {"type": "boolean"},
        },
        "required": ["pattern"],
    }

    def __init__(self):
        self.seen = None

    def execute(self, params):
        self.seen = dict(params)
        return ToolResult.ok(summary="好")


class TestExplicitNoneMeansAbsent:
    """显式 None 的可选字段 = 未提供（而不是类型错误）"""

    def test_none_on_optional_fields_is_accepted(self):
        tool = _SchemaTool()
        # LLM 常把可空参数填成 null；这必须放行
        tool.validate_params({
            "pattern": "*", "dirs": None, "time_range": None,
            "limit": None, "flag": None,
        })
        result = tool.execute({"pattern": "*", "time_range": None})
        assert result.success

    def test_none_on_required_field_still_rejected(self):
        tool = _SchemaTool()
        with pytest.raises(ParamError) as ei:
            tool.validate_params({"pattern": None})
        assert ei.value.field == "pattern"

    def test_missing_required_still_rejected(self):
        with pytest.raises(ParamError):
            _SchemaTool().validate_params({})

    def test_wrong_type_still_rejected(self):
        """放宽的是 None，不是类型约束本身"""
        with pytest.raises(ParamError) as ei:
            _SchemaTool().validate_params({"pattern": "*", "limit": "不是数字"})
        assert ei.value.field == "limit"

    def test_enum_still_enforced(self):
        with pytest.raises(ParamError):
            _SchemaTool().validate_params({"pattern": "*", "time_range": "上周"})

    def test_undeclared_field_still_lenient(self):
        _SchemaTool().validate_params({"pattern": "*", "whatever": 1})


class _ParamErrTool(BaseTool):
    name = "param_err_tool"
    description = "参数错工具"
    risk_level = "low"
    params_schema = {"type": "object", "properties": {"n": {"type": "integer"}}}

    def execute(self, params):
        raise ParamError(self.name, "类型错误：期望 integer，收到 str", "n")


class _UserErrTool(BaseTool):
    name = "user_err_tool"
    description = "带友好文案的工具"
    risk_level = "low"

    def execute(self, params):
        raise ToolError(self.name, "内部细节：连接超时",
                        user_message="网络好像不太顺，稍后再试试~")


class _BoomTool(BaseTool):
    name = "boom_tool"
    description = "裸异常工具"
    risk_level = "low"

    def execute(self, params):
        raise ValueError("底层炸了")


def _run(tool):
    """经真实注册表 + 真实线程池执行器跑一次，返回 ToolResult

    刻意走 `ToolRegistry.execute`（而不是直接调 tool.execute）：
    **异常的归一化发生在注册表那一层**，执行器的 except 分支只在注册表本身
    出问题时才命中 —— 这一点曾让我把修复放错层（写成死代码）。
    """
    reg = ToolRegistry()
    reg.register(tool)
    ex = ThreadPoolExecutorProvider(reg, pool_size=1, timeout=10)
    box: list = []
    try:
        ex.submit("r1", tool.name, {}, lambda h, r: box.append(r))
        import time
        end = time.time() + 8
        while not box and time.time() < end:
            time.sleep(0.01)
    finally:
        ex.shutdown()
    assert box, "执行器未回调"
    return box[0]


class TestErrorNormalization:
    """异常 → 用户文案 的归一化（发生在 ToolRegistry.execute）"""

    def test_param_error_from_execute_uses_generic_branch(self):
        """`ParamError` 只由 `validate_params` 抛出，没有工具在 `execute` 里抛它。

        本用例走的是"工具内部意外抛出 ParamError"的防御路径 —— 它落到通用分支
        （带「操作出错了：」前缀），而不是校验分支的可行动文案。此处固定该行为，
        避免以后误以为两处文案应当一致。
        """
        r = _run(_ParamErrTool())
        assert not r.success
        assert "操作出错了" in r.summary
        assert "类型错误" in r.error

    def test_param_validation_failure_is_friendly(self):
        """**真实路径**：`validate_params` 抛 ParamError → 可行动文案"""
        class _NeedsInt(BaseTool):
            name = "needs_int"
            description = "要整数"
            risk_level = "low"
            params_schema = {"type": "object",
                             "properties": {"n": {"type": "integer"}}}

            def execute(self, params):
                return ToolResult.ok(summary="好")

        r = _run_with(_NeedsInt(), {"n": "不是数字"})
        assert not r.success
        assert r.summary == "这个指令我还没完全理解，换个说法再试试好吗？"
        assert "needs_int" in r.error and "needs_int" not in r.summary
        assert r.emotion == "think"

    def test_tool_error_uses_user_message(self):
        r = _run(_UserErrTool())
        assert not r.success
        assert r.summary == "网络好像不太顺，稍后再试试~"
        # 技术原文留在 error 里，用户文案里没有它
        assert "内部细节" in r.error and "内部细节" not in r.summary

    def test_plain_exception_keeps_existing_behavior(self):
        r = _run(_BoomTool())
        assert not r.success
        assert "操作出错了" in r.summary
        assert "底层炸了" in r.error
        assert r.emotion == "sad"


def _run_with(tool, params):
    """直接经注册表执行（不经线程池），用于校验阶段失败的用例"""
    reg = ToolRegistry()
    reg.register(tool)
    return reg.execute(tool.name, params)
