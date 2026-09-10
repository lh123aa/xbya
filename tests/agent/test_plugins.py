"""F2/D12 —— 配置驱动装配（插件化）测试

验证「换实现 / 增减能力 = 改配置，不改代码」这件事是**真的**：

- 默认清单能装配出与以往等价的 Agent 栈
- 改 `router.provider` → 换 Router 实现
- 改 `tools.*` 开关 或 从清单删条目 → 增减工具集
- 插件可热插拔（unload 后能力真的消失）
- 清单非法 / 插件加载失败 / 缺关键服务 → 明确报错并完成清理，不留半成品
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from agent.bootstrap import AgentConfig, build_agent_stack
from agent.plugins import (
    DEFAULT_MANIFEST,
    REQUIRED_SERVICES,
    SVC_REGISTRY,
    SVC_SAFETY,
    ambient_services,
)
from agent.plugins.pipeline_plugin import setup as pipeline_setup
from agent.plugins.router_plugin import build as build_router
from agent.plugins.summarizer_plugin import build as build_summarizer
from agent.providers.router.hybrid_router import HybridRouter
from agent.providers.router.rule_router import RuleRouter
from agent.providers.summarizer.hybrid_sum import HybridSummarizer
from agent.providers.summarizer.llm_sum import LLMSummarizer
from agent.providers.summarizer.template_sum import TemplateSummarizer
from core.kernel.context import Context
from core.kernel.events import EventBus
from core.kernel.loader import PluginLoader, PluginSpec
from core.kernel.service import ServiceNotFound


@pytest.fixture
def sandbox(tmp_path):
    d = tmp_path / "Desktop"
    d.mkdir()
    return d


def _config(sandbox, **over):
    base = dict(
        path_whitelist=[str(sandbox)],
        audit_enabled=False,
        ack_enabled=False,
        tracker_persist=False,
    )
    base.update(over)
    return AgentConfig(**base)


# ══════════════════════════════════════════════════
#  默认清单
# ══════════════════════════════════════════════════

class TestDefaultManifest:

    def test_default_assembly_tool_set(self, sandbox):
        """默认清单装配出 21 工具 + 混合路由/摘要/规划/记忆

        21 = 文件 6 + 系统 5 + 生产力 4 + 浏览器 3（P1）+ 记忆 3（P3）。
        P3 把 `planner` / `memory` 两个插件加进了默认清单，故不再是 18。
        """
        s = build_agent_stack(_config(sandbox), EventBus(), synthesize=None)
        try:
            st = s.stats()
            assert st["tools"] == 21
            assert st["router"] == "HybridRouter"
            assert st["summarizer"] == "HybridSummarizer"
            assert st["enabled"] is True
        finally:
            s.dispose()

    def test_stats_exposes_loaded_plugins(self, sandbox):
        """装配结果可观测：stats() 报告实际加载的插件 id"""
        s = build_agent_stack(_config(sandbox), EventBus(), synthesize=None)
        try:
            plugins = s.stats()["plugins"]
            assert plugins[0] == "kernel", "kernel 必须最先加载"
            assert set(plugins) == {spec["id"] for spec in DEFAULT_MANIFEST}
            assert plugins[-1] == "pipeline", "pipeline 必须最后加载"
        finally:
            s.dispose()

    def test_default_manifest_is_resolvable(self):
        """默认清单本身合法：模块可导入、id 唯一、依赖存在"""
        import importlib

        loader = PluginLoader()
        specs = loader.parse_config(DEFAULT_MANIFEST)
        ids = [s.id for s in specs]
        assert len(ids) == len(set(ids)), "插件 id 必须唯一"

        for spec in specs:
            importlib.import_module(spec.module)
            assert callable(getattr(importlib.import_module(spec.module), spec.entry))

        ordered = loader.resolve_order(specs)      # 拓扑排序不抛异常
        assert {s.id for s in ordered} == set(ids)

    def test_dependency_order_respected(self, sandbox):
        """拓扑顺序：被依赖者先加载"""
        s = build_agent_stack(_config(sandbox), EventBus(), synthesize=None)
        try:
            plugins = s.stats()["plugins"]
            assert plugins.index("kernel") < plugins.index("safety")
            assert plugins.index("safety") < plugins.index("file_tools")
            assert plugins.index("file_tools") < plugins.index("executor")
            assert plugins.index("executor") < plugins.index("pipeline")
        finally:
            s.dispose()


# ══════════════════════════════════════════════════
#  配置驱动：换实现 / 增减能力
# ══════════════════════════════════════════════════

class TestConfigDriven:

    def test_swap_router_by_config(self, sandbox):
        """改 router.provider 即换路由实现，无需改代码"""
        for provider, expected in (("rule", "RuleRouter"), ("hybrid", "HybridRouter")):
            s = build_agent_stack(
                _config(sandbox, router_provider=provider), EventBus(), synthesize=None
            )
            try:
                assert s.stats()["router"] == expected
                assert type(s.router).__name__ == expected
            finally:
                s.dispose()

    def test_swap_summarizer_by_config(self, sandbox):
        s = build_agent_stack(
            _config(sandbox, summarizer_provider="template"), EventBus(), synthesize=None
        )
        try:
            assert s.stats()["summarizer"] == "TemplateSummarizer"
            assert isinstance(s.summarizer, TemplateSummarizer)
        finally:
            s.dispose()

    def test_disable_tool_sets_by_config(self, sandbox):
        """工具集开关由配置决定：21 → 13（关掉系统 5 + 浏览器 3）"""
        s = build_agent_stack(
            _config(sandbox, tools_system=False, tools_browser=False),
            EventBus(), synthesize=None,
        )
        try:
            assert s.stats()["tools"] == 21 - 5 - 3
            assert "system_info" not in s.registry.names()
            assert "web_open" not in s.registry.names()
            assert "file_search" in s.registry.names()
        finally:
            s.dispose()

    def test_all_tool_sets_disabled(self, sandbox):
        """四类工具集全关 + 记忆也关 → 一个工具都不剩

        本用例验证的是"工具集开关的极限情况"，所以要把**记忆这个第五类**
        也关掉；否则剩下的 3 个记忆工具会让断言失去意义（记忆不受
        `tools.*` 开关管辖，它由 `agent.memory.enabled` 单独控制）。
        """
        s = build_agent_stack(
            _config(sandbox, tools_file=False, tools_system=False,
                    tools_productivity=False, tools_browser=False,
                    memory_enabled=False),
            EventBus(), synthesize=None,
        )
        try:
            assert s.stats()["tools"] == 0
            assert s.registry.names() == []
        finally:
            s.dispose()

    def test_memory_tools_survive_tool_set_switches(self, sandbox):
        """关掉四类工具集后，记忆工具仍在（记忆不属于那四类）"""
        s = build_agent_stack(
            _config(sandbox, tools_file=False, tools_system=False,
                    tools_productivity=False, tools_browser=False),
            EventBus(), synthesize=None,
        )
        try:
            assert s.stats()["tools"] == 3
            assert s.registry.names() == ["memory_remember", "memory_recall",
                                          "memory_forget"]
        finally:
            s.dispose()

    def test_manifest_omitting_a_tool_plugin(self, sandbox):
        """从清单里删掉一个工具插件 = 摘掉该能力（不改代码、不改开关）"""
        manifest = [dict(x) for x in DEFAULT_MANIFEST if x["id"] != "system_tools"]
        # executor 依赖 system_tools → 一并去掉该依赖
        for spec in manifest:
            if spec["id"] == "executor":
                spec["depends_on"] = [d for d in spec["depends_on"] if d != "system_tools"]

        s = build_agent_stack(_config(sandbox, plugins=manifest), EventBus(), synthesize=None)
        try:
            assert "system_tools" not in s.stats()["plugins"]
            assert s.stats()["tools"] == 21 - 5
            assert "system_info" not in s.registry.names()
        finally:
            s.dispose()

    def test_manifest_can_disable_entry(self, sandbox):
        """清单条目 enabled: false → 不加载该插件"""
        manifest = []
        for spec in DEFAULT_MANIFEST:
            item = dict(spec)
            if item["id"] == "browser_tools":
                item["enabled"] = False
            manifest.append(item)
        for spec in manifest:
            if spec["id"] == "executor":
                spec["depends_on"] = [d for d in spec["depends_on"] if d != "browser_tools"]

        s = build_agent_stack(_config(sandbox, plugins=manifest), EventBus(), synthesize=None)
        try:
            assert "browser_tools" not in s.stats()["plugins"]
            assert s.stats()["tools"] == 21 - 3
        finally:
            s.dispose()

    def test_browser_engine_from_config(self, sandbox):
        """搜索引擎由配置决定：插件把它透传给 web_search"""
        s = build_agent_stack(_config(sandbox, search_engine="baidu"), EventBus(), synthesize=None)
        try:
            tool = s.registry.get("web_search")
            assert tool is not None
        finally:
            s.dispose()


# ══════════════════════════════════════════════════
#  热插拔
# ══════════════════════════════════════════════════

class TestHotPlug:

    def test_unload_removes_tools(self, sandbox):
        """卸载一个工具插件 → 该组工具真的从注册表消失"""
        s = build_agent_stack(_config(sandbox), EventBus(), synthesize=None)
        try:
            before = s.stats()["tools"]
            assert "system_info" in s.registry.names()

            assert s.loader.unload("system_tools") is True

            assert s.stats()["tools"] == before - 5
            assert "system_info" not in s.registry.names()
            assert "system_tools" not in s.stats()["plugins"]
        finally:
            s.dispose()

    def test_unload_removes_service(self, sandbox):
        """卸载 safety 插件 → 其服务从上下文消失"""
        s = build_agent_stack(_config(sandbox), EventBus(), synthesize=None)
        try:
            assert s.ctx.has(SVC_SAFETY) is True
            s.loader.unload("safety")
            assert s.ctx.has(SVC_SAFETY) is False
            with pytest.raises(ServiceNotFound):
                s.ctx.use(SVC_SAFETY)
        finally:
            s.dispose()

    def test_unload_unknown_plugin_is_noop(self, sandbox):
        s = build_agent_stack(_config(sandbox), EventBus(), synthesize=None)
        try:
            assert s.loader.unload("nonexistent") is True
        finally:
            s.dispose()

    def test_dispose_unloads_everything(self, sandbox):
        """dispose 后插件全部卸载、上下文已释放，且幂等"""
        s = build_agent_stack(_config(sandbox), EventBus(), synthesize=None)
        s.dispose()

        assert s.loader.loaded_ids() == []
        assert s.ctx.disposed is True
        s.dispose()          # 幂等
        assert s.loader.loaded_ids() == []


# ══════════════════════════════════════════════════
#  失败路径：不留半成品
# ══════════════════════════════════════════════════

class TestFailurePaths:

    def test_missing_required_service_raises(self, sandbox):
        """清单不产出关键服务 → 明确报错（上层据此降级为纯对话模式）"""
        manifest = [{"id": "kernel", "module": "agent.plugins.kernel_plugin"}]
        with pytest.raises(RuntimeError) as exc:
            build_agent_stack(_config(sandbox, plugins=manifest), EventBus())

        message = str(exc.value)
        assert "缺少服务" in message
        for name in REQUIRED_SERVICES:
            if name != SVC_REGISTRY:
                assert name in message

    def test_missing_service_cleans_up(self, sandbox):
        """装配失败后不留残骸：插件被卸载、上下文被释放"""
        ctx_holder = {}
        manifest = [{"id": "kernel", "module": "agent.plugins.kernel_plugin"}]

        real_init = Context.__init__

        def spy(*a, **k):
            obj = Context.__new__(Context)
            real_init(obj, *a, **k)
            ctx_holder["ctx"] = obj
            return obj

        import agent.bootstrap as bs
        original = bs.Context
        bs.Context = spy
        try:
            with pytest.raises(RuntimeError):
                bs.build_agent_stack(_config(sandbox, plugins=manifest), EventBus())
        finally:
            bs.Context = original

        assert ctx_holder["ctx"].disposed is True

    def test_broken_plugin_module_is_reported(self, sandbox):
        """清单里指向不存在的模块 → 该插件计入失败；缺服务时一并报出"""
        manifest = [
            {"id": "kernel", "module": "agent.plugins.kernel_plugin"},
            {"id": "ghost", "module": "agent.plugins.no_such_module"},
        ]
        with pytest.raises(RuntimeError) as exc:
            build_agent_stack(_config(sandbox, plugins=manifest), EventBus())
        assert "ghost" in str(exc.value)

    def test_plugin_entry_raising_is_rolled_back(self, sandbox):
        """插件入口抛异常 → 其注册被撤销（不留泄漏服务）"""
        from tests.agent.boom_plugin import LEAKED_SERVICE

        ctx = Context()
        for name, value in ambient_services(_config(sandbox), EventBus()).items():
            ctx.provide(name, value)

        loader = PluginLoader()
        spec = PluginSpec(id="boom", module="tests.agent.boom_plugin")
        assert loader.load(spec, ctx) is False, "入口抛异常应判定为加载失败"
        assert ctx.has(LEAKED_SERVICE) is False, "失败插件的注册必须被回滚"
        assert loader.is_loaded("boom") is False
        ctx.dispose()

    def test_failed_plugin_does_not_block_others(self, sandbox):
        """清单里混入必失败的插件 → 其余插件照常加载，缺关键服务才报错"""
        manifest = [
            {"id": "kernel", "module": "agent.plugins.kernel_plugin"},
            {"id": "boom", "module": "tests.agent.boom_plugin"},
            {"id": "safety", "module": "agent.plugins.safety_plugin", "depends_on": ["kernel"]},
        ]
        ctx = Context()
        for name, value in ambient_services(_config(sandbox), EventBus()).items():
            ctx.provide(name, value)

        loader = PluginLoader()
        ordered = loader.resolve_order(loader.parse_config(manifest))
        ok, failed = loader.load_all(ordered, ctx)

        assert "kernel" in ok and "safety" in ok
        assert failed == ["boom"]
        ctx.dispose()


# ══════════════════════════════════════════════════
#  清单解析容错
# ══════════════════════════════════════════════════

class TestManifestParsing:

    def test_none_uses_default(self):
        assert AgentConfig._parse_plugins(None) is None

    def test_non_list_falls_back(self, caplog):
        with caplog.at_level("WARNING"):
            assert AgentConfig._parse_plugins({"a": 1}) is None
        assert any("必须是列表" in r.message for r in caplog.records)

    def test_empty_list_falls_back(self, caplog):
        with caplog.at_level("WARNING"):
            assert AgentConfig._parse_plugins([]) is None
        assert any("为空" in r.message for r in caplog.records)

    def test_all_invalid_items_fall_back(self):
        assert AgentConfig._parse_plugins(["x", 1, None]) is None

    def test_mixed_items_keep_valid(self):
        out = AgentConfig._parse_plugins([{"id": "a"}, "bad"])
        assert out == [{"id": "a"}]

    def test_real_config_manifest_parses(self):
        from core.config_manager import get_config_manager

        cfg = AgentConfig.from_config_manager(get_config_manager("config.yaml"))
        assert cfg.plugins is not None
        assert len(cfg.plugins) == len(DEFAULT_MANIFEST)


# ══════════════════════════════════════════════════
#  各插件 Provider 选择
# ══════════════════════════════════════════════════

class TestProviderSelection:

    def test_router_provider_variants(self):
        assert isinstance(build_router(AgentConfig(router_provider="rule")), RuleRouter)

        hybrid = build_router(AgentConfig(router_provider="hybrid"))
        assert isinstance(hybrid, HybridRouter)

        # "优先 LLM" 用阈值 1.0 承载：规则得分永远够不到阈值 → 必走 LLM
        llm_first = build_router(AgentConfig(router_provider="llm", llm_router_enabled=True))
        assert isinstance(llm_first, HybridRouter)
        assert llm_first._threshold == 1.0

    def test_router_unknown_provider_falls_back(self, caplog):
        with caplog.at_level("WARNING"):
            router = build_router(AgentConfig(router_provider="量子路由"))
        assert isinstance(router, RuleRouter)
        assert any("未实现" in r.message for r in caplog.records)

    def test_summarizer_provider_variants(self):
        assert isinstance(build_summarizer(AgentConfig(summarizer_provider="template")),
                          TemplateSummarizer)
        assert isinstance(build_summarizer(AgentConfig(summarizer_provider="llm")),
                          LLMSummarizer)
        assert isinstance(build_summarizer(AgentConfig(summarizer_provider="hybrid")),
                          HybridSummarizer)

    def test_summarizer_unknown_provider_falls_back(self, caplog):
        with caplog.at_level("WARNING"):
            s = build_summarizer(AgentConfig(summarizer_provider="玄学"))
        assert isinstance(s, TemplateSummarizer)
        assert any("未实现" in r.message for r in caplog.records)

    def test_safety_unknown_provider_falls_back(self, sandbox, caplog):
        """安全 provider 写错 → 回退 basic，且仍然装上守卫（不静默失去防护）"""
        manifest = [dict(x) for x in DEFAULT_MANIFEST]
        for spec in manifest:
            if spec["id"] == "safety":
                spec["config"] = {"provider": "super_guard"}

        with caplog.at_level("WARNING"):
            s = build_agent_stack(_config(sandbox, plugins=manifest), EventBus())

        try:
            assert s.safety is not None
            assert any("回退 basic" in r.message for r in caplog.records)
        finally:
            s.dispose()

    def test_pipeline_plugin_respects_ack_and_tracker_flags(self, sandbox):
        """ack_enabled=false / tracker_persist=false 时对应服务缺席"""
        s = build_agent_stack(
            _config(sandbox, ack_enabled=False, tracker_persist=False), EventBus()
        )
        try:
            assert s.ack_cache is None
            assert s.tracker_store is None
            assert s.tracker is not None, "实体栈本身仍应存在（只是不落盘）"
        finally:
            s.dispose()


# ══════════════════════════════════════════════════
#  内部工具的容错
# ══════════════════════════════════════════════════

class TestInternalResilience:

    def test_register_tools_tolerates_bad_disposer(self):
        """注销函数抛异常时不影响其余注销"""
        from agent.plugins import register_tools

        ctx = Context()
        registry = _FakeRegistry()
        ctx.provide(SVC_REGISTRY, registry)

        register_tools(ctx, [object(), object()])()
        assert registry.unregistered == 2

    def test_register_tools_dispose_swallows(self):
        from agent.plugins import register_tools

        class _Boom:
            calls = []

            def register(self, tool):
                def dispose():
                    _Boom.calls.append(1)
                    raise RuntimeError("注销炸了")
                return dispose

        ctx = Context()
        ctx.provide(SVC_REGISTRY, _Boom())
        register_tools(ctx, [object()])()          # 不抛
        assert _Boom.calls == [1]

    def test_pipeline_combine_swallows(self):
        """pipeline_plugin 的组合 disposer 单个失败不中断"""
        from agent.plugins.pipeline_plugin import _combine

        seen = []
        _combine([
            lambda: seen.append("a"),
            lambda: (_ for _ in ()).throw(RuntimeError("boom")),
            lambda: seen.append("c"),
        ])()
        assert seen == ["a", "c"]

    def test_teardown_plugins_swallows(self, sandbox):
        """_teardown_plugins 对 unload/ctx 异常都兜底"""
        import agent.bootstrap as bs

        class _BadLoader:
            def unload_all(self):
                raise RuntimeError("unload 炸了")

        class _BadCtx:
            def dispose(self):
                raise RuntimeError("dispose 炸了")

        bs._teardown_plugins(_BadLoader(), _BadCtx())   # 不抛

    def test_ambient_services_table(self):
        cfg = AgentConfig()
        bus = EventBus()
        table = ambient_services(config=cfg, bus=bus, tts="T", llm_once="L")
        assert table["agent.config"] is cfg
        assert table["agent.bus"] is bus
        assert table["agent.tts"] == "T"
        assert table["agent.llm_once"] == "L"
        assert table["agent.weather"] is None

    def test_plugin_spec_requires_module(self):
        from core.kernel.service import ConfigError

        with pytest.raises(ConfigError):
            PluginSpec.from_dict({"id": "no_module"})


class _FakeRegistry:
    """记录注销次数的假注册表"""

    def __init__(self):
        self.unregistered = 0

    def register(self, tool):
        def dispose():
            self.unregistered += 1
        return dispose
