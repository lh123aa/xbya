"""P3-B —— 长期记忆的**契约与接线**测试

Provider 层（embedder / recall_store / hybrid_memory / memory_tools）的测试在
`test_memory_store.py` 与 `test_memory_tools.py` 里；本文件覆盖它没管的三块，
即由装配方（AGENTS.md 里的 Definition / Consumer 两侧）负责的部分：

1. **seam 契约本身**（`agent/seams/memory.py`）—— Provider 不必覆写的可选方法、
   `MemoryItem` 的持久化协议、只有接口才能验证的默认行为
2. **memory 插件**（`agent/plugins/memory_plugin.py`）—— 嵌入器选择与清理顺序
3. **管线接入**（`agent/pipeline.py`）—— 情景记忆写入、偏好提示注入、失效降级
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from agent.message import AgentCommand
from agent.pipeline import AgentPipeline
from agent.plugins import SVC_CONFIG, SVC_EMBEDDER, SVC_MEMORY, SVC_REGISTRY
from agent.plugins.memory_plugin import setup as memory_plugin_setup
from agent.providers.executor.thread_pool import ThreadPoolExecutorProvider
from agent.providers.memory import HashingEmbedder, HybridMemory, RecallStore
from agent.providers.router.rule_router import RuleRouter
from agent.providers.safety.basic_guard import BasicGuard
from agent.seams.embedder import (
    EmbedderService,
    cosine,
    dot,
    normalize,
    pack,
    unpack,
)
from agent.seams.memory import (
    DEFAULT_HINT_LIMIT,
    DEFAULT_RECALL_LIMIT,
    MAX_MEMORY_TEXT,
    MemoryItem,
    MemoryKind,
    MemoryService,
    coerce_kind,
    format_recall,
    is_sensitive,
    new_item_id,
    sensitive_reason,
    truncate_text,
)
from agent.tools.base import BaseTool, ToolResult
from agent.tools.registry import ToolRegistry
from core.kernel.context import Context
from core.kernel.events import EventBus, EventTypes


# ══════════════════════════════════════════════════
#  1. seam：MemoryItem 的持久化协议
# ══════════════════════════════════════════════════

class TestMemoryItem:

    def test_defaults_and_kind_coercion(self):
        assert MemoryItem(text="x").kind is MemoryKind.FACT
        assert MemoryItem(text="x", kind="episode").kind is MemoryKind.EPISODE
        assert MemoryItem(text="x", kind=MemoryKind.ENTITY).kind is MemoryKind.ENTITY
        # 不认识的类别收敛为 fact，而不是抛异常
        assert MemoryItem(text="x", kind="nonsense").kind is MemoryKind.FACT
        assert MemoryItem(text="x", kind=None).kind is MemoryKind.FACT

    def test_coerce_kind_accepts_enum_str_and_keywords(self):
        assert coerce_kind(MemoryKind.FACT) is MemoryKind.FACT
        assert coerce_kind(" FACT ") is MemoryKind.FACT
        assert coerce_kind(123) is MemoryKind.FACT
        assert coerce_kind(None) is MemoryKind.FACT

    def test_text_normalized_and_truncated(self):
        assert MemoryItem(text="  喜欢   用\nChrome  ").text == "喜欢 用 Chrome"
        assert len(MemoryItem(text="长" * (MAX_MEMORY_TEXT + 50)).text) == MAX_MEMORY_TEXT

    def test_non_dict_metadata_coerced(self):
        assert MemoryItem(text="x", metadata=None).metadata == {}
        assert MemoryItem(text="x", metadata=["a"]).metadata == {}
        assert MemoryItem(text="x", metadata={"k": 1}).metadata == {"k": 1}

    def test_str(self):
        assert str(MemoryItem(text="喜欢用 Chrome")) == "[fact] 喜欢用 Chrome"

    def test_roundtrip(self):
        item = MemoryItem(
            text="喜欢用 Chrome", kind=MemoryKind.FACT, item_id="m1",
            metadata={"src": "voice"}, created_at=1.5, last_used_at=2.5, use_count=3,
        )
        data = item.to_dict()
        assert data["kind"] == "fact" and data["item_id"] == "m1"
        back = MemoryItem.from_dict(data)
        assert back.text == item.text and back.kind is item.kind
        assert back.metadata == item.metadata and back.use_count == 3

    @pytest.mark.parametrize("bad", [
        None, "text", 42, [], {}, {"text": ""}, {"text": "   "}, {"text": 123},
        {"no_text": "x"},
    ])
    def test_from_dict_rejects_unusable(self, bad):
        assert MemoryItem.from_dict(bad) is None

    def test_from_dict_tolerates_bad_numeric_and_metadata(self):
        """数值/元数据字段损坏时退回默认值，而不是丢掉整条记忆"""
        item = MemoryItem.from_dict({
            "text": "x", "created_at": "not-a-number", "last_used_at": None,
            "use_count": "abc", "metadata": "not-a-dict",
        })
        assert item is not None
        assert item.created_at == 0.0 and item.use_count == 0
        assert item.metadata == {}

    def test_from_dict_kind_fallback(self):
        assert MemoryItem.from_dict({"text": "x", "kind": "??"}).kind is MemoryKind.FACT


# ══════════════════════════════════════════════════
#  2. seam：可选方法的默认实现
# ══════════════════════════════════════════════════

class _MinimalMemory(MemoryService):
    """只实现必需方法的记忆（验证可选方法的默认行为）"""

    capability_name = "memory"

    def __init__(self):
        self._items = []

    def remember(self, text, kind=MemoryKind.FACT, metadata=None):
        item = MemoryItem(text=text, kind=kind, item_id=new_item_id())
        self._items.append(item)
        return item.item_id

    def recall(self, query, kind=None, limit=DEFAULT_RECALL_LIMIT):
        return [it for it in self._items if query in it.text][:limit]

    def forget(self, item_id):
        before = len(self._items)
        self._items = [it for it in self._items if it.item_id != item_id]
        return len(self._items) < before

    def count(self, kind=None):
        if kind is None:
            return len(self._items)
        return sum(1 for it in self._items if it.kind is coerce_kind(kind))


class TestMemoryServiceDefaults:

    def test_optional_methods_are_conservative(self):
        """未覆写可选项的 Provider 必须是"能跑但什么都不做"，而不是报错"""
        mem = _MinimalMemory()
        assert mem.recall_text("查询") == ""
        assert mem.record_episode("找文件", "找到 3 个") is None
        assert mem.hint_for("帮我打开浏览器") == ""
        assert mem.forget_all() == 0
        assert mem.stats() == {}
        assert mem.close() is None
        assert mem.capability_name == "memory"

    def test_required_methods_work_on_minimal_impl(self):
        mem = _MinimalMemory()
        item_id = mem.remember("喜欢用 Chrome")
        assert item_id and mem.count() == 1
        assert [it.text for it in mem.recall("Chrome")] == ["喜欢用 Chrome"]
        assert mem.forget(item_id) is True
        assert mem.forget("nope") is False
        assert mem.count(kind=MemoryKind.FACT) == 0

    def test_cannot_instantiate_bare_interface(self):
        with pytest.raises(TypeError):
            MemoryService()          # 内核禁止直接实例化能力接口


# ══════════════════════════════════════════════════
#  2.5 seam：EmbedderService 的默认实现与向量工具
# ══════════════════════════════════════════════════

class _FixedEmbedder(EmbedderService):
    """可控嵌入器：用它可以精确造出"抛异常"与"返回空列表"两种失败"""

    capability_name = "embedder"

    def __init__(self, dim=4, mode="ok"):
        self._dim = dim
        self._mode = mode

    @property
    def dim(self):
        return self._dim

    def embed(self, texts):
        if self._mode == "raise":
            raise RuntimeError("嵌入炸了")
        if self._mode == "empty":
            return []
        return [normalize([1.0] * self._dim) for _ in texts]


class TestEmbedderSeam:

    def test_embed_one_happy(self):
        vector = _FixedEmbedder().embed_one("x")
        assert len(vector) == 4 and any(vector)

    def test_embed_one_on_failure_returns_zero_vector(self):
        """嵌入失败不能抛出去：调用方据此跳过向量检索，而不是整条链路失败"""
        assert _FixedEmbedder(mode="raise").embed_one("x") == [0.0] * 4
        assert _FixedEmbedder(mode="empty").embed_one("x") == [0.0] * 4
        assert _FixedEmbedder().embed_one("x") != [0.0] * 4

    def test_defaults(self):
        emb = _FixedEmbedder()
        assert emb.ready() is True            # 纯本地实现始终可用
        assert emb.describe() == "_FixedEmbedder(dim=4)"
        assert emb.close() is None
        assert emb.capability_name == "embedder"

    def test_normalize_zero_vector(self):
        assert normalize([0.0, 0.0]) == [0.0, 0.0]

    def test_dot_and_cosine(self):
        assert dot([1.0, 0.0], [1.0, 0.0]) == 1.0
        assert cosine([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
        assert cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)
        # 零向量没有方向，相似度定义为 0（而不是 NaN / 除零）
        assert cosine([0.0, 0.0], [1.0, 1.0]) == 0.0

    def test_pack_unpack_roundtrip(self):
        assert unpack(pack([1.5, 2.5, 3.5]), 3) == [1.5, 2.5, 3.5]

    def test_unpack_tolerates_bad_input(self):
        assert unpack(None, 2) == [0.0, 0.0]
        assert unpack("not bytes", 3) == [0.0, 0.0, 0.0]
        # 字节数不是 4 的倍数 → frombytes 失败 → 零向量
        assert unpack(b"\x01", 2) == [0.0, 0.0]
        # dim 为负/0 也要给出可用的空向量，而不是崩
        assert unpack(None, 0) == []
        assert unpack(None, -5) == []

    def test_unpack_resizes_to_requested_dim(self):
        blob = pack([1.0, 2.0, 3.0])
        assert unpack(blob, 5) == [1.0, 2.0, 3.0, 0.0, 0.0]   # 补零
        assert unpack(blob, 2) == [1.0, 2.0]                   # 截断


# ══════════════════════════════════════════════════
#  3. seam：敏感判据与共用工具
# ══════════════════════════════════════════════════

class TestSensitiveJudgement:

    @pytest.mark.parametrize("text,reason", [
        ("我的密码是 abc123", "密码"),
        ("password = hunter2", "密码"),
        ("帮我忘记密码怎么办", "密码"),
        ("访问令牌已过期", "密码"),
        ("验证码 1234", "密码"),
        ("api_key=sk-abcdefghijklmnopqrst", "密钥"),
        ("access_token: xyz", "密钥"),
        ("身份证 110101199003078515", "身份证号"),
        ("卡号 6222021234567890123", "银行卡号"),
        ("AKIAIOSFODNN7EXAMPLE", "Access Key ID"),
        ("ghp_" + "a" * 30, "GitHub Token"),
    ])
    def test_flagged(self, text, reason):
        assert is_sensitive(text) is True
        # 理由必须具体：只写"敏感"无法帮用户理解为什么没记住
        assert sensitive_reason(text) == reason

    @pytest.mark.parametrize("text", [
        "我喜欢用 Chrome", "passwordless 登录", "", None,
        "把下载目录的安装包挪到软件归档", "帮我找一下合同",
    ])
    def test_not_flagged(self, text):
        assert is_sensitive(text) is False
        assert sensitive_reason(text) == ""

    def test_truncate_text(self):
        assert truncate_text(None) == ""
        assert truncate_text("  a \n b  ") == "a b"
        assert len(truncate_text("x" * 100, limit=10)) == 10

    def test_format_recall(self):
        assert format_recall([]) == ""
        items = [MemoryItem(text="喜欢用 Chrome"), MemoryItem(text="常用目录是 D 盘"),
                 MemoryItem(text="第三条")]
        assert format_recall(items) == "我记得你提过：喜欢用 Chrome、常用目录是 D 盘、第三条"
        assert "第三条" not in format_recall(items, limit=2)
        # limit 非法时至少拼一条，而不是返回空串
        assert format_recall(items, limit=0) == "我记得你提过：喜欢用 Chrome"
        assert format_recall([MemoryItem(text="")]) == ""

    def test_new_item_id_unique(self):
        ids = {new_item_id() for _ in range(50)}
        assert len(ids) == 50
        assert all(i.startswith("m") for i in ids)

    def test_default_limits_sane(self):
        assert DEFAULT_RECALL_LIMIT >= 1
        assert DEFAULT_HINT_LIMIT >= 1


# ══════════════════════════════════════════════════
#  4. memory 插件
# ══════════════════════════════════════════════════

def _cfg(**over):
    base = dict(
        memory_enabled=True, memory_store=":memory:", memory_embedder="hashing",
        memory_st_model="all-MiniLM-L6-v2", memory_dim=64, memory_max_items=10,
        memory_vector_backend="python", memory_min_similarity=0.25,
        memory_episodes=True, memory_hints=True,
    )
    base.update(over)
    return type("Cfg", (), base)()


def _ctx(cfg=None):
    ctx = Context()
    ctx.provide(SVC_CONFIG, cfg or _cfg())
    # register_tools 需要 tool_registry
    from agent.plugins.kernel_plugin import setup as kernel_setup

    kernel_setup(ctx)
    return ctx


class TestMemoryPlugin:

    def test_default_hashing_assembly(self):
        ctx = _ctx()
        dispose = memory_plugin_setup(ctx)
        try:
            embedder = ctx.use(SVC_EMBEDDER)
            memory = ctx.use(SVC_MEMORY)
            assert isinstance(embedder, HashingEmbedder) and embedder.dim == 64
            assert isinstance(memory, HybridMemory)
            assert ctx.use(SVC_REGISTRY).names() == [
                "memory_remember", "memory_recall", "memory_forget"]
        finally:
            dispose()

    def test_dispose_removes_tools_and_closes_store(self):
        ctx = _ctx()
        dispose = memory_plugin_setup(ctx)
        memory = ctx.use(SVC_MEMORY)
        memory.remember("喜欢用 Chrome")
        assert memory.count() == 1
        dispose()
        assert ctx.use(SVC_REGISTRY).names() == []
        # 关闭之后查询仍必须可用（返回空值）—— 记忆是"坏了也不能拖垮主流程"的能力
        assert isinstance(memory.count(), int)
        assert isinstance(memory.recall("Chrome"), list)

    def test_disabled_provides_nothing(self):
        ctx = _ctx(_cfg(memory_enabled=False))
        assert memory_plugin_setup(ctx) is None
        assert not ctx.has(SVC_MEMORY) and not ctx.has(SVC_EMBEDDER)

    @pytest.mark.parametrize("alias", ["st", "sentence_transformers",
                                       "sentence-transformers", "ST"])
    def test_st_embedder_branch(self, alias):
        """st 分支：只要构造成功即可（不真的加载模型，加载是懒的）"""
        from agent.providers.memory import SentenceTransformerEmbedder

        ctx = _ctx(_cfg(memory_embedder=alias))
        dispose = memory_plugin_setup(ctx)
        try:
            embedder = ctx.use(SVC_EMBEDDER)
            assert isinstance(embedder, SentenceTransformerEmbedder)
        finally:
            dispose()

    def test_unknown_embedder_falls_back_to_hashing(self):
        ctx = _ctx(_cfg(memory_embedder="nonsense"))
        dispose = memory_plugin_setup(ctx)
        try:
            assert isinstance(ctx.use(SVC_EMBEDDER), HashingEmbedder)
        finally:
            dispose()

    def test_empty_option_values_use_defaults(self):
        """配置里留空（None/""）时收敛到默认值，而不是崩在 int()/float()"""
        ctx = _ctx(_cfg(memory_store=None, memory_embedder=None, memory_dim=None,
                        memory_max_items=None, memory_vector_backend=None,
                        memory_min_similarity=None, memory_st_model=None))
        dispose = memory_plugin_setup(ctx)
        try:
            assert isinstance(ctx.use(SVC_MEMORY), HybridMemory)
        finally:
            dispose()


# ══════════════════════════════════════════════════
#  5. 管线接入
# ══════════════════════════════════════════════════

class _SpyMemory(MemoryService):
    """记录调用的记忆替身"""

    capability_name = "memory"

    def __init__(self, hint="", raise_on=None):
        self.episodes = []
        self.hints = []
        self._hint = hint
        self._raise_on = raise_on or set()

    def remember(self, text, kind=MemoryKind.FACT, metadata=None):
        return "m1"

    def recall(self, query, kind=None, limit=DEFAULT_RECALL_LIMIT):
        return []

    def forget(self, item_id):
        return False

    def count(self, kind=None):
        return 0

    def record_episode(self, action, summary, params=None, success=True):
        if "episode" in self._raise_on:
            raise RuntimeError("记忆炸了")
        self.episodes.append((action, summary, success))
        return "e1"

    def hint_for(self, text):
        if "hint" in self._raise_on:
            raise RuntimeError("提示炸了")
        self.hints.append(text)
        return self._hint


class _EchoTool(BaseTool):
    name = "file_search"
    description = "搜索"
    risk_level = "low"

    def __init__(self):
        self.calls = 0

    def execute(self, params):
        self.calls += 1
        return ToolResult.ok(data=[], summary="找到 2 个文件", count=2)


@pytest.fixture
def pipe_factory(tmp_path):
    made = []

    def _make(memory=None, tool=None):
        reg = ToolRegistry()
        tool = tool or _EchoTool()
        reg.register(tool)
        guard = BasicGuard(whitelist=[str(tmp_path)], audit_db=None,
                           audit_enabled=False, remember_choices=False)
        ex = ThreadPoolExecutorProvider(reg, pool_size=1, timeout=10)
        bus = EventBus()
        evts: list = []
        bus.on_any(lambda e: evts.append((e.type, dict(e.data))))
        pipe = AgentPipeline(
            bus=bus, router=RuleRouter(), safety=guard, executor=ex, registry=reg,
            ack_cache=None, summarizer=None, memory=memory,
        )
        pipe.start()
        made.append((pipe, ex))
        return pipe, tool, bus, evts

    yield _make

    for pipe, ex in made:
        try:
            pipe.stop()
        finally:
            ex.shutdown()


def _wait(pred, timeout=5.0):
    import time
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.01)
    return False


class TestPipelineMemoryWiring:

    def test_successful_tool_call_records_episode(self, pipe_factory):
        memory = _SpyMemory()
        pipe, tool, bus, evts = pipe_factory(memory)
        pipe.handle_text("找一下压缩包")
        _wait(lambda: memory.episodes)
        # 记的是可播报的动作短语，不是工具名 file_search
        assert memory.episodes == [("找文件", "找到 2 个文件", True)]

    def test_failed_tool_call_is_not_recorded(self, pipe_factory):
        class FailTool(_EchoTool):
            def execute(self, params):
                return ToolResult.fail("搜不动")

        memory = _SpyMemory()
        pipe, tool, bus, evts = pipe_factory(memory, tool=FailTool())
        pipe.handle_text("找一下压缩包")
        _wait(lambda: any(t == EventTypes.FEEDBACK_RESULT for t, _ in evts))
        assert memory.episodes == []

    def test_unknown_action_falls_back_to_tool_name(self, pipe_factory):
        """未登记动作短语的工具退回工具名（难看但可读，好过沉默）"""
        class WeirdTool(_EchoTool):
            name = "weird_tool"

        memory = _SpyMemory()
        pipe, tool, bus, evts = pipe_factory(memory, tool=WeirdTool())
        pipe.handle_text("找一下压缩包")     # 路由会落到 chat，故直接调用内部方法
        pipe._record_episode("weird_tool", ToolResult.ok(summary="好了"))
        assert memory.episodes == [("weird_tool", "好了", True)]

    def test_episode_failure_does_not_break_result(self, pipe_factory):
        """记忆层炸了不能影响 feedback.result —— 记忆是增强不是依赖"""
        memory = _SpyMemory(raise_on={"episode"})
        pipe, tool, bus, evts = pipe_factory(memory)
        pipe.handle_text("找一下压缩包")
        assert _wait(lambda: any(t == EventTypes.FEEDBACK_RESULT for t, _ in evts))
        assert tool.calls == 1

    def test_hint_injected_into_route_context(self, pipe_factory):
        memory = _SpyMemory(hint="你之前说过：喜欢用 Chrome")
        pipe, tool, bus, evts = pipe_factory(memory)
        seen = {}
        original = pipe._router.route

        def spy(text, context=None):
            seen.update(context or {})
            return original(text, context)

        pipe._router.route = spy
        pipe.handle_text("找一下压缩包")
        assert seen["memory_hint"] == "你之前说过：喜欢用 Chrome"
        assert memory.hints == ["找一下压缩包"]

    def test_hint_failure_is_swallowed(self, pipe_factory):
        memory = _SpyMemory(raise_on={"hint"})
        pipe, tool, bus, evts = pipe_factory(memory)
        seen = {}
        original = pipe._router.route

        def spy(text, context=None):
            seen.update(context or {})
            return original(text, context)

        pipe._router.route = spy
        assert pipe.handle_text("找一下压缩包") is not None
        assert seen["memory_hint"] == ""

    def test_no_memory_is_transparent(self, pipe_factory):
        """未装记忆时与 P2 行为一致：不记情景、提示为空串"""
        pipe, tool, bus, evts = pipe_factory(None)
        assert pipe.memory is None
        assert pipe._memory_hint("任意") == ""
        pipe._record_episode("file_search", ToolResult.ok(summary="x"))
        pipe.handle_text("找一下压缩包")
        assert _wait(lambda: tool.calls == 1)

    def test_accessors_and_stats(self, pipe_factory):
        memory = _SpyMemory()
        pipe, tool, bus, evts = pipe_factory(memory)
        assert pipe.memory is memory
        pipe.set_memory(None)
        assert pipe.memory is None
        pipe.set_memory(memory)
        assert pipe.memory is memory

    def test_plan_context_carries_hint(self, pipe_factory):
        """多步规划上下文也带偏好提示（LLM 规划器据此贴合用户习惯）"""
        memory = _SpyMemory(hint="你之前说过：安装包放软件归档")
        pipe, tool, bus, evts = pipe_factory(memory)

        captured = {}

        class SpyPlanner:
            capability_name = "planner"

            def plan(self, text, context=None):
                captured.update(context or {})
                return None

            def can_plan(self, text):
                return True

        pipe.set_planner(SpyPlanner())
        pipe.handle_text("先找到压缩包然后再挪到归档")
        assert captured.get("memory_hint") == "你之前说过：安装包放软件归档"

    def test_real_memory_end_to_end(self, pipe_factory, tmp_path):
        """真实 HybridMemory：一次检索 → 情景入库 → 下一次能召回"""
        embedder = HashingEmbedder(dim=64)
        store = RecallStore(path=":memory:", embedder=embedder,
                            vector_backend="python")
        memory = HybridMemory(store, embedder=embedder)
        pipe, tool, bus, evts = pipe_factory(memory)
        try:
            pipe.handle_text("找一下压缩包")
            assert _wait(lambda: memory.count(MemoryKind.EPISODE) == 1)

            episodes = memory.recall("找文件", kind=MemoryKind.EPISODE)
            assert episodes and "找到 2 个文件" in episodes[0].text

            # 偏好类记忆能作为提示回给路由
            memory.remember("我要找压缩包", kind=MemoryKind.FACT)
            assert pipe._memory_hint("我要找压缩包") != ""
        finally:
            memory.close()
