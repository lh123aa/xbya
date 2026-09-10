"""P3 长期记忆测试（二）：记忆工具层

覆盖 `agent/tools/memory_tools.py` 的三个工具：

- `memory_remember` —— 记住（含敏感信息拒绝、空文本、服务缺失）
- `memory_recall`   —— 检索（有查询 / 无查询列最近 / 无命中 / limit 收敛）
- `memory_forget`   —— 删除（破坏性：risk=medium + preview 说明影响范围）

同时验证与 `ToolRegistry` 的协作（工具必须能被注册表正常校验与调用）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from agent.providers.memory.hashing_embedder import HashingEmbedder
from agent.providers.memory.hybrid_memory import HybridMemory
from agent.providers.memory.recall_store import RecallStore
from agent.seams.memory import (
    DEFAULT_RECALL_LIMIT,
    MemoryItem,
    MemoryKind,
    MemoryService,
    format_recall,
)
from agent.tools.base import BaseTool, ToolResult
from agent.tools.memory_tools import (
    MAX_RECALL_LIMIT,
    MemoryForgetTool,
    MemoryRecallTool,
    MemoryRememberTool,
    all_memory_tools,
)
from agent.tools.registry import ToolRegistry


# ══════════════════════════════════════════════════
#  测试替身
# ══════════════════════════════════════════════════


class BareMemory(MemoryService):
    """极简 MemoryService：只有 seam 必选方法，**没有** recent()/get() 补充能力

    用于验证工具层按能力探测（契约外的方法缺失时必须优雅退化）。
    """

    capability_name = "memory"

    def __init__(self) -> None:
        self.items: dict = {}
        self.counter = 0
        self.forgotten: list = []

    def remember(self, text, kind=MemoryKind.FACT, metadata=None):
        self.counter += 1
        item_id = f"b{self.counter}"
        self.items[item_id] = MemoryItem(text=text, kind=kind, item_id=item_id)
        return item_id

    def recall(self, query, kind=None, limit=DEFAULT_RECALL_LIMIT):
        return [it for it in self.items.values() if query in it.text][:limit]

    def forget(self, item_id):
        self.forgotten.append(item_id)
        return self.items.pop(item_id, None) is not None

    def count(self, kind=None):
        return len(self.items)


@pytest.fixture
def memory(tmp_path):
    """真实 HybridMemory（RecallStore + HashingEmbedder），库落在 tmp_path"""
    store = RecallStore(str(tmp_path / "tools.db"), embedder=HashingEmbedder())
    mem = HybridMemory(store)
    yield mem
    mem.close()


@pytest.fixture
def remember_tool(memory):
    return MemoryRememberTool(memory)


@pytest.fixture
def recall_tool(memory):
    return MemoryRecallTool(memory)


@pytest.fixture
def forget_tool(memory):
    return MemoryForgetTool(memory)


# ══════════════════════════════════════════════════
#  memory_remember
# ══════════════════════════════════════════════════


class TestMemoryRememberTool:

    def test_metadata(self, remember_tool):
        assert remember_tool.name == "memory_remember"
        assert remember_tool.risk_level == "low"
        assert remember_tool.description
        assert remember_tool.params_schema["required"] == ["text"]
        assert remember_tool.confirm_required() is False

    def test_remember_success(self, remember_tool, memory):
        result = remember_tool.execute({"text": "喜欢用 Chrome"})

        assert result.success is True
        assert result.summary == "记住啦：喜欢用 Chrome"
        assert result.data["text"] == "喜欢用 Chrome"
        assert result.data["kind"] == "fact"
        assert memory.get(result.data["item_id"]) is not None
        assert memory.count() == 1

    def test_remember_with_kind(self, remember_tool, memory):
        result = remember_tool.execute({"text": "上次找过合同", "kind": "episode"})

        assert result.success is True
        assert result.data["kind"] == "episode"
        assert memory.get(result.data["item_id"]).kind is MemoryKind.EPISODE

    def test_remember_unknown_kind_falls_back_to_fact(self, remember_tool, memory):
        result = remember_tool.execute({"text": "随便写点什么", "kind": "天书"})
        assert result.data["kind"] == "fact"

    def test_remember_records_source_metadata(self, remember_tool, memory):
        result = remember_tool.execute({"text": "来源标记"})
        assert memory.get(result.data["item_id"]).metadata == {"source": "memory_remember"}

    def test_remember_truncates_and_strips(self, remember_tool, memory):
        result = remember_tool.execute({"text": "  带空格的内容  "})
        assert result.data["text"] == "带空格的内容"

    def test_empty_text(self, remember_tool):
        result = remember_tool.execute({"text": "   "})
        assert result.success is False
        assert result.summary == "你想让我记住什么呀？"
        assert result.emotion == "think"

    def test_missing_text_key(self, remember_tool):
        assert remember_tool.execute({}).success is False

    def test_sensitive_rejected_with_reason(self, remember_tool, memory):
        """被敏感判据拒绝时要说清"哪一类"，但绝不回显原文"""
        result = remember_tool.execute({"text": "我的密码是 hunter2xxxx"})

        assert result.success is False
        assert result.summary == "这个我不方便记哦，里面有密码这类信息"
        assert "hunter2xxxx" not in result.summary
        assert memory.count() == 0

    @pytest.mark.parametrize(
        "text,label",
        [
            ("password: abc12345", "密码"),
            ("我的验证码是 8899", "密码"),
            ("api_key = sk-abcdefghijklmnopqrst", "密钥"),
            ("身份证 11010119900307123X", "身份证号"),
            ("卡号 6222021234567890123", "银行卡号"),
        ],
    )
    def test_sensitive_labels(self, remember_tool, text, label):
        result = remember_tool.execute({"text": text})
        assert result.success is False
        assert label in result.summary

    def test_without_memory_service(self):
        tool = MemoryRememberTool(None)
        assert tool.execute({"text": "记一下"}).summary == "我这边还没接上记忆能力呢"

    def test_empty_text_wins_over_missing_service(self):
        """先判"没内容"再判"没能力"：用户没给内容时那句提示更贴切"""
        tool = MemoryRememberTool(None)
        assert tool.execute({"text": ""}).summary == "你想让我记住什么呀？"

    def test_store_failure(self, memory, monkeypatch):
        monkeypatch.setattr(memory, "remember", lambda *a, **k: None)
        result = MemoryRememberTool(memory).execute({"text": "写不进去"})
        assert result.success is False
        assert "记不下来" in result.summary

    def test_works_with_bare_service(self):
        """只实现 seam 必选方法的实现也要能用"""
        memory = BareMemory()
        result = MemoryRememberTool(memory).execute({"text": "简单实现"})
        assert result.success is True
        assert memory.count() == 1


# ══════════════════════════════════════════════════
#  memory_recall
# ══════════════════════════════════════════════════


class TestMemoryRecallTool:

    def test_metadata(self, recall_tool):
        assert recall_tool.name == "memory_recall"
        assert recall_tool.risk_level == "low"
        assert recall_tool.description
        assert recall_tool.params_schema["properties"]["limit"]["maximum"] == MAX_RECALL_LIMIT
        assert "required" not in recall_tool.params_schema, "query 是可选的"

    def test_recall_with_query(self, recall_tool, memory):
        memory.remember("喜欢用 Chrome 浏览器")
        memory.remember("常用目录是 D 盘的合同文件夹")
        memory.remember("合同要发给张经理")

        result = recall_tool.execute({"query": "合同"})

        assert result.success is True
        assert result.summary.startswith("我记得 2 条：")
        assert result.count == len(result.data)
        assert all("合同" in row["text"] for row in result.data)

    def test_data_shape(self, recall_tool, memory):
        memory.remember("喜欢用 Chrome 浏览器")
        row = recall_tool.execute({"query": "浏览器"}).data[0]

        assert set(row) == {"text", "kind", "score"}
        assert row["kind"] == "fact"
        assert isinstance(row["score"], float)
        assert row["score"] > 0
        assert row["score"] == round(row["score"], 4)

    def test_summary_elides_long_lists(self, recall_tool, memory):
        for i in range(5):
            memory.remember(f"合同备份第{i}号文件")

        result = recall_tool.execute({"query": "合同", "limit": 5})
        assert result.summary.startswith("我记得 5 条：")
        assert "等 5 条" in result.summary
        assert len(result.data) == 5

    def test_no_result(self, recall_tool, memory):
        memory.remember("喜欢用 Chrome 浏览器")
        result = recall_tool.execute({"query": "宇航员登陆火星"})

        assert result.success is True, "查不到不是失败"
        assert result.summary == "我这边没有相关的记忆呢"
        assert result.data == []
        assert result.count == 0

    def test_empty_query_lists_recent(self, recall_tool, memory):
        memory.remember("第一条")
        memory.remember("第二条")

        result = recall_tool.execute({})
        assert result.success is True
        assert len(result.data) == 2
        assert result.data[0]["text"] == "第二条", "最新的在前"
        assert result.summary.startswith("我记得 2 条：")

    def test_empty_query_no_memory(self, recall_tool):
        assert recall_tool.execute({}).summary == "我这边没有相关的记忆呢"

    def test_empty_query_without_recent_support(self):
        """契约外能力（recent）缺失时退回"没有记忆"，而不是报错"""
        tool = MemoryRecallTool(BareMemory())
        result = tool.execute({})
        assert result.success is True
        assert result.summary == "我这边没有相关的记忆呢"

    def test_whitespace_query_is_treated_as_empty(self, recall_tool, memory):
        memory.remember("只有这一条")
        assert len(recall_tool.execute({"query": "   "}).data) == 1

    def test_kind_filter(self, recall_tool, memory):
        memory.remember("合同的偏好", kind=MemoryKind.FACT)
        memory.remember("合同的经历", kind=MemoryKind.EPISODE)

        result = recall_tool.execute({"query": "合同", "kind": "episode"})
        assert [row["kind"] for row in result.data] == ["episode"]

        bare = recall_tool.execute({"query": "合同"})
        assert len(bare.data) == 2

    def test_limit_applied(self, recall_tool, memory):
        for i in range(4):
            memory.remember(f"合同备份第{i}号文件")
        assert len(recall_tool.execute({"query": "合同", "limit": 2}).data) == 2

    def test_limit_parsing(self, recall_tool):
        assert recall_tool._limit({}) == DEFAULT_RECALL_LIMIT
        assert recall_tool._limit({"limit": 3}) == 3
        assert recall_tool._limit({"limit": 0}) == 1, "下限 1"
        assert recall_tool._limit({"limit": 999}) == MAX_RECALL_LIMIT, "上限封顶"
        assert recall_tool._limit({"limit": "不是数"}) == DEFAULT_RECALL_LIMIT

    def test_without_memory_service(self):
        tool = MemoryRecallTool(None)
        assert tool.execute({"query": "随便"}).summary == "我这边还没接上记忆能力呢"

    def test_works_with_bare_service(self):
        memory = BareMemory()
        memory.remember("合同的备份")
        result = MemoryRecallTool(memory).execute({"query": "合同"})

        assert result.success is True
        assert result.data[0]["text"] == "合同的备份"
        assert result.data[0]["score"] == 0.0, "简易实现没有分数，应为 0"

    def test_recall_marks_items_as_used(self, recall_tool, memory):
        item_id = memory.remember("喜欢用 Chrome 浏览器")
        recall_tool.execute({"query": "浏览器"})
        assert memory.get(item_id).use_count == 1


# ══════════════════════════════════════════════════
#  memory_forget
# ══════════════════════════════════════════════════


class TestMemoryForgetTool:

    def test_destructive_risk_and_confirmation(self, forget_tool):
        """破坏性操作：medium 让它走确认通道"""
        assert forget_tool.name == "memory_forget"
        assert forget_tool.risk_level == "medium"
        assert forget_tool.confirm_required() is True

    def test_schema_allows_either_argument(self, forget_tool):
        """不把 item_id 写死成 required，否则 {"all": true} 会被参数校验拒掉"""
        assert "required" not in forget_tool.params_schema
        assert set(forget_tool.params_schema["properties"]) == {"item_id", "all"}

    def test_forget_by_id(self, forget_tool, memory):
        item_id = memory.remember("会被忘掉的一条")
        result = forget_tool.execute({"item_id": item_id})

        assert result.success is True
        assert result.summary == "好的，忘掉「会被忘掉的一条」啦"
        assert result.data["item_id"] == item_id
        assert memory.count() == 0

    def test_forget_unknown_id(self, forget_tool):
        result = forget_tool.execute({"item_id": "不存在"})
        assert result.success is False
        assert result.summary == "我这儿没找到这条记忆呢"

    def test_forget_without_describe_support(self):
        memory = BareMemory()
        item_id = memory.remember("简单实现的一条")

        result = MemoryForgetTool(memory).execute({"item_id": item_id})
        assert result.success is True
        assert result.summary == "好的，这条记忆我忘掉了"

    def test_forget_all(self, forget_tool, memory):
        memory.remember("一")
        memory.remember("二")

        result = forget_tool.execute({"all": True})
        assert result.success is True
        assert result.summary == "好的，已经忘掉 2 条记忆啦"
        assert result.data["removed"] == 2
        assert memory.count() == 0

    def test_forget_all_when_empty(self, forget_tool):
        result = forget_tool.execute({"all": True})
        assert result.success is True
        assert result.summary == "记忆库本来就是空的呢"

    def test_forget_without_any_argument(self, forget_tool):
        result = forget_tool.execute({})
        assert result.success is False
        assert "要忘掉哪一条呀" in result.summary

    def test_blank_item_id_is_rejected(self, forget_tool):
        assert forget_tool.execute({"item_id": "   "}).success is False

    def test_without_memory_service(self):
        tool = MemoryForgetTool(None)
        assert tool.execute({"all": True}).summary == "我这边还没接上记忆能力呢"
        assert tool.execute({"item_id": "x"}).summary == "我这边还没接上记忆能力呢"

    # ── 预览（确认前展示影响范围）──

    def test_preview_single_known(self, forget_tool, memory):
        item_id = memory.remember("喜欢用 Chrome")
        assert forget_tool.preview({"item_id": item_id}) == "将删除这条记忆：「喜欢用 Chrome」"

    def test_preview_single_unknown(self, forget_tool):
        assert forget_tool.preview({"item_id": "abc"}) == "将删除这条记忆（编号 abc）"

    def test_preview_all_with_examples(self, forget_tool, memory):
        memory.remember("喜欢用 Chrome")
        memory.remember("常用目录是 D 盘")

        preview = forget_tool.preview({"all": True})
        assert preview.startswith("将删除全部 2 条记忆")
        assert "喜欢用 Chrome" in preview

    def test_preview_all_when_empty(self, forget_tool):
        assert forget_tool.preview({"all": True}) == "记忆库本来就是空的"

    def test_preview_all_without_recent_support(self):
        memory = BareMemory()
        memory.remember("简单实现的一条")
        preview = MemoryForgetTool(memory).preview({"all": True})
        assert preview == "将删除全部 1 条记忆", "拿不到样例时不该拼出空括号"

    def test_preview_all_without_usable_samples(self):
        """有 recent 但一条正文都取不到（全空文本）→ 同样不拼空括号"""
        memory = BareMemory()
        memory.remember("一条")
        memory.recent = lambda limit=3, kind=None: []

        assert MemoryForgetTool(memory).preview({"all": True}) == "将删除全部 1 条记忆"

    def test_preview_single_without_get_support(self):
        memory = BareMemory()
        item_id = memory.remember("简单实现的一条")
        preview = MemoryForgetTool(memory).preview({"item_id": item_id})
        assert preview == f"将删除这条记忆（编号 {item_id}）"

    def test_preview_without_arguments(self, forget_tool):
        assert forget_tool.preview({}) == ""

    def test_preview_without_memory_service(self):
        assert MemoryForgetTool(None).preview({"all": True}) == ""


# ══════════════════════════════════════════════════
#  工具集与注册表协作
# ══════════════════════════════════════════════════


class TestMemoryToolSet:

    def test_all_memory_tools(self, memory):
        tools = all_memory_tools(memory)
        assert [t.name for t in tools] == [
            "memory_remember", "memory_recall", "memory_forget",
        ]
        assert all(isinstance(t, BaseTool) for t in tools)

    def test_all_memory_tools_without_service(self):
        tools = all_memory_tools(None)
        assert len(tools) == 3
        for tool in tools:
            result = tool.execute({"text": "x", "query": "x", "item_id": "x"})
            assert result.success is False, f"{tool.name} 在服务缺失时应友好失败"
            assert isinstance(result, ToolResult)

    def test_llm_schema_export(self, memory):
        for tool in all_memory_tools(memory):
            schema = tool.to_llm_schema()
            assert schema["function"]["name"] == tool.name
            assert schema["function"]["parameters"]["type"] == "object"

    def test_registry_registration_and_call(self, memory):
        registry = ToolRegistry()
        for tool in all_memory_tools(memory):
            registry.register(tool)

        assert set(registry.names()) == {"memory_remember", "memory_recall", "memory_forget"}

        remembered = registry.execute("memory_remember", {"text": "喜欢用 Chrome 浏览器"})
        assert remembered.success is True
        item_id = remembered.data["item_id"]

        recalled = registry.execute("memory_recall", {"query": "浏览器"})
        assert recalled.success is True
        assert recalled.data[0]["text"] == "喜欢用 Chrome 浏览器"

        forgotten = registry.execute("memory_forget", {"item_id": item_id})
        assert forgotten.success is True
        assert registry.execute("memory_recall", {}).count == 0

    def test_registry_rejects_missing_required(self, memory):
        registry = ToolRegistry()
        registry.register(MemoryRememberTool(memory))
        result = registry.execute("memory_remember", {})
        assert result.success is False
        assert "参数错误" in result.error

    def test_registry_rejects_bad_limit_type(self, memory):
        registry = ToolRegistry()
        registry.register(MemoryRecallTool(memory))
        assert registry.execute("memory_recall", {"limit": "不是数"}).success is False

    def test_success_rate_after_mixed_calls(self, memory):
        registry = ToolRegistry()
        for tool in all_memory_tools(memory):
            registry.register(tool)

        registry.execute("memory_remember", {"text": "一条记忆"})
        registry.execute("memory_recall", {"query": "记忆"})
        registry.execute("memory_forget", {"item_id": "不存在"})

        stats = registry.stats()
        assert stats["memory_remember"]["ok"] == 1
        assert stats["memory_forget"]["fail"] == 1

    def test_unregister_removes_tool(self, memory):
        registry = ToolRegistry()
        disposers = [registry.register(t) for t in all_memory_tools(memory)]
        for dispose in disposers:
            dispose()
        assert registry.count() == 0


# ══════════════════════════════════════════════════
#  与 Provider 的端到端协作
# ══════════════════════════════════════════════════


class TestMemoryToolsEndToEnd:

    def test_chinese_round_trip_through_tools(self, memory):
        """中文存、中文查、中文删 —— 走完整工具层"""
        remember = MemoryRememberTool(memory)
        recall = MemoryRecallTool(memory)
        forget = MemoryForgetTool(memory)

        assert remember.execute({"text": "桌面上的合同要发给张经理"}).success is True
        assert remember.execute({"text": "喜欢用 Chrome 浏览器"}).success is True

        result = recall.execute({"query": "合同"})
        assert result.success is True
        assert len(result.data) == 1
        assert "合同" in result.data[0]["text"]

        assert forget.preview({"all": True}).startswith("将删除全部 2 条记忆")
        assert forget.execute({"all": True}).data["removed"] == 2
        assert recall.execute({"query": "合同"}).summary == "我这边没有相关的记忆呢"

    def test_recall_text_matches_tool_summary_style(self, memory):
        """HybridMemory.recall_text 与工具摘要都能直接播报"""
        memory.remember("喜欢用 Chrome 浏览器")
        assert format_recall(memory.recall("浏览器")) == memory.recall_text("浏览器")
        assert MemoryRecallTool(memory).execute({"query": "浏览器"}).summary.startswith(
            "我记得 1 条："
        )
