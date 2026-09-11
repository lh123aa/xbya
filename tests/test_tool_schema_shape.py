"""工具 schema 形制归一（P5-B1 / D16）

## 守的是什么

D16 登记的事故：项目里有两种都叫"llm schema"的东西，**名字一样、形制不同** ——
`LLMRouter.TOOL_SCHEMAS` 是**扁平**的，`ToolRegistry.to_llm_schemas()` 是**已包好**的。
两个 LLM 插件原先都写 `[{"type": "function", "function": t} for t in tools]`，
于是把注册表 schema 递进去会得到：

    {"type": "function", "function": {"type": "function", "function": {...}}}

⇒ 请求体里**根本没有 `function.name`**。端点的反应是 400 或空 tool_calls，
插件返回 `None`，`route_with_tools` 把 `None` 读成"模型这次用文字回答" ——
**整条 LLM 路由兜底静默失效**，日志里只有一个状态码。

P4-B4 的免费模型矩阵探针正是这么踩的：**六个免费模型的第 ③ 项被这个形制问题
误判成"全都不支持 function calling"**（错在测量侧，不在模型侧）。

## 这个文件钉住的三条语义

1. **两种形制都收** —— 剥掉多余层，而不是靠巧合让 `function.name` 恰好存在；
2. **畸形一律显式报错**，绝不静默变出一个"没有名字的工具"
   （空名字正是静默失败的载体：请求照样发、端点照样 400、上层照样只看得到 `None`）；
3. **整批拒绝，不做部分保留** —— 少一个工具会让模型"看不见"某个能力，而它不会告诉你。

`plugins/` 不在覆盖率口径内，所以这个文件的价值不是凑覆盖率，而是**把形制钉死**。
"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pytest

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from plugins.llm.tool_schemas import (  # noqa: E402
    ToolSchemaError,
    to_openai_tools,
    unwrap,
)

#: 扁平（`LLMRouter.TOOL_SCHEMAS` 那种）
FLAT = {"name": "file_search", "description": "搜索",
        "parameters": {"type": "object", "properties": {"pattern": {"type": "string"}}}}

#: 已包好（`ToolRegistry.to_llm_schemas()` 那种）
WRAPPED = {"type": "function", "function": dict(FLAT)}


class TestBothShapesAccepted:
    """两种形制都要能收 —— 这是 D16 的正面要求"""

    def test_flat_is_wrapped(self):
        out = to_openai_tools([FLAT])
        assert out == [{"type": "function", "function": FLAT}]

    def test_already_wrapped_passes_through_unchanged(self):
        """已包好的**不该被再包一层**（多包一层就是原始 bug 的形态）"""
        out = to_openai_tools([WRAPPED])
        assert out == [{"type": "function", "function": FLAT}]
        assert out[0]["function"]["name"] == "file_search"

    def test_both_shapes_in_one_batch(self):
        """混用也允许 —— 逐条判定，不假设整批同形制"""
        out = to_openai_tools([FLAT, WRAPPED])
        assert [t["function"]["name"] for t in out] == ["file_search", "file_search"]

    def test_double_wrapped_is_unwrapped(self):
        """被包两层（有人手滑又包了一次）也剥到只剩一层"""
        double = {"type": "function", "function": WRAPPED}
        out = to_openai_tools([double])
        assert out == [{"type": "function", "function": FLAT}]

    def test_empty_list(self):
        assert to_openai_tools([]) == []

    def test_unwrap_returns_flat_shape(self):
        assert unwrap(WRAPPED) == FLAT
        assert unwrap(FLAT) == FLAT


class TestMalformedRejectedExplicitly:
    """畸形**必须显式报错** —— 这一侧以前是完全缺的（静默变匿名工具）"""

    def test_missing_name_rejected(self):
        with pytest.raises(ToolSchemaError) as ei:
            to_openai_tools([{"description": "没有名字"}])
        assert "name" in str(ei.value)

    def test_name_not_string_rejected(self):
        with pytest.raises(ToolSchemaError):
            to_openai_tools([{"name": 123}])

    def test_empty_name_rejected(self):
        """空串尤其危险：它会让请求体看起来"有名字"但在端点上等同没有"""
        with pytest.raises(ToolSchemaError):
            to_openai_tools([{"name": "   "}])

    def test_not_a_dict_rejected(self):
        with pytest.raises(ToolSchemaError):
            to_openai_tools(["file_search"])

    def test_nested_without_name_rejected(self):
        """套娃且内层没名字 —— 这正是原始 bug 的输入形态，必须被拦"""
        with pytest.raises(ToolSchemaError):
            to_openai_tools([{"type": "function", "function": {"description": "x"}}])

    def test_conflicting_names_rejected(self):
        """两层都有名字且不一致 → **不许猜**（猜错=调用用户没要求的工具）"""
        with pytest.raises(ToolSchemaError) as ei:
            to_openai_tools([{"name": "a", "function": {"name": "b"}}])
        assert "不一致" in str(ei.value)

    def test_mixed_shape_rejected(self):
        """外层有名字、内层没有 → 形制混用，报错而不是挑一个"""
        with pytest.raises(ToolSchemaError):
            to_openai_tools([{"name": "a", "function": {"description": "x"}}])

    def test_whole_batch_rejected_not_partial(self):
        """**批内一条坏 → 整批拒绝**。部分保留会让模型看不见某个能力且不报错"""
        with pytest.raises(ToolSchemaError) as ei:
            to_openai_tools([FLAT, {"no_name": 1}, WRAPPED])
        assert "1/3" in str(ei.value), f"要说清坏了几条，实际：{ei.value}"

    def test_error_message_points_at_the_shape_confusion(self):
        """报错要说**可照做**的话：指出这是 D16 那种形制混淆"""
        with pytest.raises(ToolSchemaError) as ei:
            to_openai_tools([{"type": "function", "function": {"parameters": {}}}])
        assert "to_llm_schemas" in str(ei.value) or "形制" in str(ei.value)

    def test_tool_schema_error_is_value_error(self):
        """继承 ValueError：调用方只写 `except Exception` 也能兜住，不会带崩应用"""
        assert issubclass(ToolSchemaError, ValueError)


class TestRealRegistrySchemasAreAccepted:
    """真注册表的输出必须被收下 —— 这是 D16 的实际事故场景"""

    def _registry(self):
        from agent.tools.registry import ToolRegistry
        from agent.tools.file_tools import FileSearchTool, FileMoveTool
        from agent.providers.safety.basic_guard import BasicGuard

        guard = BasicGuard()
        reg = ToolRegistry()
        reg.register(FileSearchTool(guard))
        reg.register(FileMoveTool(guard))
        return reg

    def test_registry_output_is_accepted(self):
        """**这条就是 P4-B4 探针踩的那个坑**：注册表 schema 直接喂进来 """
        schemas = self._registry().to_llm_schemas()
        assert len(schemas) == 2
        out = to_openai_tools(schemas)
        assert [t["function"]["name"] for t in out] == ["file_search", "file_move"]

    def test_registry_output_has_no_nested_function(self):
        """归一之后**不许再出现嵌套的 function** —— 那就是原始 bug 的指纹"""
        out = to_openai_tools(self._registry().to_llm_schemas())
        for t in out:
            assert set(t) == {"type", "function"}
            assert "function" not in t["function"], (
                "又包了一层：这正是 D16 的静默失败形态"
            )
            assert t["function"]["name"]


class TestPluginsUseTheSingleConvergencePoint:
    """两个插件必须**都**走这个模块 —— 多写一份判据就会漂移"""

    @pytest.mark.parametrize("modname", [
        "plugins.llm.openai_api.plugin",
        "plugins.llm.openrouter.plugin",
    ])
    def test_plugin_imports_the_helper(self, modname):
        import importlib

        mod = importlib.import_module(modname)
        assert hasattr(mod, "to_openai_tools"), (
            f"{modname} 没有引入 to_openai_tools —— 形制归一在这里会缺失"
        )

    @pytest.mark.parametrize("path", [
        "plugins/llm/openai_api/plugin.py",
        "plugins/llm/openrouter/plugin.py",
    ])
    def test_no_hand_rolled_wrapping_left(self, path):
        """源码里**不许**再留下手写的包一层 —— 那正是 D16 的成因

        用源码文本断言（而不是行为断言）是有意的：这条检查防的是"以后又有人
        顺手写一句 `[{"type": "function", "function": t} for t in tools]`"，
        而那种写法在行为上**可能**恰好能跑（输入恰是扁平时），所以行为测试抓不住它。
        """
        src = (project_root / path).read_text(encoding="utf-8")
        bad = '"function": t for t'
        assert bad not in src, f"{path} 里还有手写的包一层：{bad}"

    def test_chat_with_tools_signature_documents_both_shapes(self):
        """两个插件的 `chat_with_tools` docstring 都要写明"两种形制都收"

        D16 的长期方案是"让类型来约束"，在类型还没到位之前，
        **docstring 就是契约的载体** —— 它必须说清这件事，否则下一个人还会接错。
        """
        from plugins.llm.openai_api.plugin import OpenAIApiLLM
        from plugins.llm.openrouter.plugin import UniversalLLM

        for cls in (OpenAIApiLLM, UniversalLLM):
            doc = inspect.getdoc(cls.chat_with_tools) or ""
            assert "两种形制都收" in doc, f"{cls.__name__}.chat_with_tools 没写明形制"
            assert "ToolSchemaError" in doc, f"{cls.__name__} 没写明会显式报错"


class TestOutgoingPayloadHasRealNames:
    """真正发出去的请求体里必须有 `function.name`

    前面那组测的是归一函数本身；这一组测的是**插件的出口**。
    分开是有意的：归一函数再对，插件忘了调用它，事故照样发生 ——
    而"忘了调用"在行为上完全测不出来（除非去看发出去的报文）。
    """

    @staticmethod
    def _capture(monkeypatch):
        """把 `requests.post` 换成捕获器，返回捕获到的 payload 列表

        两个插件都是**调用点才 `import requests`**（openrouter 在函数体内 import），
        所以必须打**全局** `requests.post`，不能打模块属性 —— 打模块属性会
        "补丁打上了但没生效"，测试于是变成空转（第一版就是这么假绿的）。
        """
        import json as _json
        from unittest.mock import MagicMock

        import requests as _requests

        captured = []

        def fake_post(url, headers=None, json=None, timeout=None, **kw):  # noqa: A002
            captured.append(json)
            r = MagicMock()
            r.status_code = 200
            r.json.return_value = {"choices": [{"message": {
                "tool_calls": [{"function": {"name": "file_search",
                                             "arguments": _json.dumps({"pattern": "*a*"})}}]}}]}
            return r

        monkeypatch.setattr(_requests, "post", fake_post)
        return captured

    def _registry_schemas(self):
        from agent.tools.registry import ToolRegistry
        from agent.tools.file_tools import FileSearchTool
        from agent.providers.safety.basic_guard import BasicGuard

        reg = ToolRegistry()
        reg.register(FileSearchTool(BasicGuard()))
        return reg.to_llm_schemas()

    def test_openai_api_sends_named_tools(self, monkeypatch):
        from plugins.llm.openai_api.plugin import OpenAIApiLLM

        captured = self._capture(monkeypatch)
        llm = OpenAIApiLLM(api_key="k" * 24, model="m", base_url="http://example.invalid")
        out = llm.chat_with_tools("sys", "找文件", self._registry_schemas())

        assert len(captured) == 1, "应当只发一次请求"
        sent = captured[0]["tools"]
        assert [t["function"]["name"] for t in sent] == ["file_search"], (
            f"发出去的报文里名字不对：{sent}"
        )
        assert "function" not in sent[0]["function"], "又包了一层（D16 的指纹）"
        assert out == {"name": "file_search", "arguments": {"pattern": "*a*"}}

    def test_openrouter_sends_named_tools(self, monkeypatch):
        from plugins.llm.openrouter.plugin import UniversalLLM

        captured = self._capture(monkeypatch)
        llm = UniversalLLM(api_key="k" * 24, model="m", base_url="http://example.invalid")
        out = llm.chat_with_tools("sys", "找文件", self._registry_schemas())

        assert len(captured) == 1
        sent = captured[0]["tools"]
        assert [t["function"]["name"] for t in sent] == ["file_search"]
        assert out == {"name": "file_search", "arguments": {"pattern": "*a*"}}

    def test_plugin_raises_on_malformed_instead_of_sending_anonymous(self, monkeypatch):
        """畸形工具在**发请求之前**就被拒 —— 不许把匿名工具发到网络上

        这是 D16 修复的实质：以前它会照发，换回 400/空 tool_calls，
        然后被上层读成"模型没选工具"。
        """
        from plugins.llm.openai_api.plugin import OpenAIApiLLM

        captured = self._capture(monkeypatch)
        llm = OpenAIApiLLM(api_key="k" * 24, model="m", base_url="http://example.invalid")
        with pytest.raises(ToolSchemaError):
            llm.chat_with_tools("sys", "找文件", [{"description": "匿名"}])
        assert captured == [], "畸形 schema 竟然发出去了"
