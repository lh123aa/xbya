"""插件加载器测试

覆盖 core/kernel/loader.py：
- PluginSpec 构造与校验
- 配置解析（含路径提取、enabled 过滤、重复 id）
- 依赖拓扑排序（正常/缺失/环）
- 加载与卸载（成功/模块缺失/入口缺失/入口抛异常/回滚）
"""

import sys
from pathlib import Path

import pytest

project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from core.kernel import (
    CircularDependencyError,
    ConfigError,
    Context,
    MissingDependencyError,
)
from core.kernel.loader import DEFAULT_CONFIG_PATH, PluginLoader, PluginSpec


# ══════════════════════════════════════════════════
#  PluginSpec
# ══════════════════════════════════════════════════

class TestPluginSpec:
    """PluginSpec 构造与校验"""

    def test_minimal(self):
        """最小必填字段"""
        spec = PluginSpec.from_dict({"id": "a", "module": "m"})
        assert spec.id == "a"
        assert spec.module == "m"
        assert spec.entry == "setup"       # 默认入口
        assert spec.enabled is True
        assert spec.depends_on == []
        assert spec.config == {}

    def test_full(self):
        """完整字段"""
        spec = PluginSpec.from_dict({
            "id": "a", "module": "m", "entry": "boot",
            "config": {"k": 1}, "enabled": False, "depends_on": ["b"],
        })
        assert spec.entry == "boot"
        assert spec.config == {"k": 1}
        assert spec.enabled is False
        assert spec.depends_on == ["b"]

    def test_depends_on_string_coerced(self):
        """depends_on 支持单个字符串写法"""
        spec = PluginSpec.from_dict({"id": "a", "module": "m", "depends_on": "b"})
        assert spec.depends_on == ["b"]

    def test_entry_none_raises(self):
        """entry 显式为 None → 报错"""
        with pytest.raises(ConfigError):
            PluginSpec.from_dict({"id": "a", "module": "m", "entry": None})

    def test_entry_empty_raises(self):
        """entry 显式为空字符串 → 报错（不静默回退）"""
        with pytest.raises(ConfigError):
            PluginSpec.from_dict({"id": "a", "module": "m", "entry": "  "})

    def test_config_none_becomes_empty_dict(self):
        """config 为 None → 空字典"""
        spec = PluginSpec.from_dict({"id": "a", "module": "m", "config": None})
        assert spec.config == {}

    def test_config_wrong_type_raises(self):
        """config 类型错误 → 报错"""
        with pytest.raises(ConfigError):
            PluginSpec.from_dict({"id": "a", "module": "m", "config": []})

    @pytest.mark.parametrize("raw,keyword", [
        ("not a dict", "字典"),
        ({}, "id"),
        ({"id": "a"}, "module"),
        ({"id": "a", "module": "m", "depends_on": 1}, "depends_on"),
    ])
    def test_invalid_raises(self, raw, keyword):
        """非法配置抛出 ConfigError"""
        with pytest.raises(ConfigError) as exc:
            PluginSpec.from_dict(raw)
        assert keyword in str(exc.value)

    def test_repr(self):
        """repr 含 id 与入口"""
        spec = PluginSpec(id="a", module="m", entry="boot")
        assert "a" in repr(spec) and "boot" in repr(spec)


# ══════════════════════════════════════════════════
#  配置解析
# ══════════════════════════════════════════════════

class TestParseConfig:
    """parse_config"""

    def test_parse_nested(self):
        """从嵌套配置里取插件清单"""
        loader = PluginLoader()
        cfg = {"agent": {"plugins": [
            {"id": "a", "module": "m1"},
            {"id": "b", "module": "m2"},
        ]}}
        specs = loader.parse_config(cfg)
        assert [s.id for s in specs] == ["a", "b"]

    def test_parse_flat_list(self):
        """直接传列表"""
        loader = PluginLoader()
        specs = loader.parse_config([{"id": "a", "module": "m"}])
        assert len(specs) == 1

    def test_disabled_filtered(self):
        """enabled=False 被过滤"""
        loader = PluginLoader()
        specs = loader.parse_config([
            {"id": "a", "module": "m"},
            {"id": "b", "module": "m", "enabled": False},
        ])
        assert [s.id for s in specs] == ["a"]

    def test_missing_path_returns_empty(self):
        """配置里没有插件段 → 空列表"""
        assert PluginLoader().parse_config({}) == []
        assert PluginLoader().parse_config({"agent": {}}) == []

    def test_none_returns_empty(self):
        """None → 空列表"""
        assert PluginLoader().parse_config(None) == []

    def test_none_value_returns_empty(self):
        """插件段为 None → 空列表"""
        assert PluginLoader().parse_config({"agent": {"plugins": None}}) == []

    def test_wrong_type_raises(self):
        """配置类型错误"""
        with pytest.raises(ConfigError):
            PluginLoader().parse_config("not a config")
        with pytest.raises(ConfigError):
            PluginLoader().parse_config({"agent": {"plugins": "not a list"}})

    def test_duplicate_id_raises(self):
        """重复 id 报错"""
        with pytest.raises(ConfigError) as exc:
            PluginLoader().parse_config([
                {"id": "a", "module": "m"},
                {"id": "a", "module": "m"},
            ])
        assert "重复" in str(exc.value)

    def test_custom_config_path(self):
        """自定义配置路径"""
        loader = PluginLoader(config_path="my.custom.path")
        specs = loader.parse_config({"my": {"custom": {"path": [
            {"id": "a", "module": "m"},
        ]}}})
        assert len(specs) == 1

    def test_default_config_path_constant(self):
        """默认路径常量"""
        assert DEFAULT_CONFIG_PATH == "agent.plugins"


# ══════════════════════════════════════════════════
#  拓扑排序
# ══════════════════════════════════════════════════

class TestResolveOrder:
    """resolve_order"""

    def test_no_deps_keeps_order(self):
        """无依赖时保持原顺序"""
        specs = [PluginSpec(id=i, module="m") for i in ("a", "b", "c")]
        ordered = PluginLoader().resolve_order(specs)
        assert [s.id for s in ordered] == ["a", "b", "c"]

    def test_linear_chain(self):
        """链式依赖：依赖项在前"""
        specs = [
            PluginSpec(id="c", module="m", depends_on=["b"]),
            PluginSpec(id="b", module="m", depends_on=["a"]),
            PluginSpec(id="a", module="m"),
        ]
        ids = [s.id for s in PluginLoader().resolve_order(specs)]
        assert ids.index("a") < ids.index("b") < ids.index("c")

    def test_diamond(self):
        """菱形依赖"""
        specs = [
            PluginSpec(id="d", module="m", depends_on=["b", "c"]),
            PluginSpec(id="b", module="m", depends_on=["a"]),
            PluginSpec(id="c", module="m", depends_on=["a"]),
            PluginSpec(id="a", module="m"),
        ]
        ids = [s.id for s in PluginLoader().resolve_order(specs)]
        assert ids[0] == "a"
        assert ids[-1] == "d"

    def test_duplicate_deps_ok(self):
        """重复声明同一依赖不导致入度错误"""
        specs = [
            PluginSpec(id="b", module="m", depends_on=["a", "a"]),
            PluginSpec(id="a", module="m"),
        ]
        ids = [s.id for s in PluginLoader().resolve_order(specs)]
        assert ids == ["a", "b"]

    def test_missing_dep_raises(self):
        """依赖不存在 → MissingDependencyError"""
        specs = [PluginSpec(id="a", module="m", depends_on=["ghost"])]
        with pytest.raises(MissingDependencyError) as exc:
            PluginLoader().resolve_order(specs)
        assert exc.value.plugin_id == "a"
        assert exc.value.missing == "ghost"

    def test_circular_raises(self):
        """环 → CircularDependencyError（含环路径）"""
        specs = [
            PluginSpec(id="a", module="m", depends_on=["b"]),
            PluginSpec(id="b", module="m", depends_on=["a"]),
        ]
        with pytest.raises(CircularDependencyError) as exc:
            PluginLoader().resolve_order(specs)
        assert set(exc.value.cycle[:2]) == {"a", "b"}

    def test_three_node_cycle(self):
        """三节点环"""
        specs = [
            PluginSpec(id="a", module="m", depends_on=["c"]),
            PluginSpec(id="b", module="m", depends_on=["a"]),
            PluginSpec(id="c", module="m", depends_on=["b"]),
        ]
        with pytest.raises(CircularDependencyError):
            PluginLoader().resolve_order(specs)

    def test_empty(self):
        """空列表"""
        assert PluginLoader().resolve_order([]) == []


# ══════════════════════════════════════════════════
#  加载 / 卸载
# ══════════════════════════════════════════════════

class FakePluginPackage:
    """临时插件包：每个测试用唯一包名，避免 sys.modules 缓存串味"""

    def __init__(self, tmp_path: Path):
        # 唯一包名：Python 会缓存同名包，复用会导致 __path__ 指向别的 tmp 目录
        self.name = "fake_plugins_" + tmp_path.name.replace("-", "_").replace(".", "_")
        self.dir = tmp_path / self.name
        self.dir.mkdir()
        (self.dir / "__init__.py").write_text("", encoding="utf-8")

    def write(self, mod_name: str, body: str) -> str:
        """写一个插件模块，返回完整模块路径"""
        (self.dir / f"{mod_name}.py").write_text(body, encoding="utf-8")
        return f"{self.name}.{mod_name}"


@pytest.fixture
def plugins(tmp_path, monkeypatch):
    """构造临时插件包并加入 sys.path"""
    pkg = FakePluginPackage(tmp_path)
    monkeypatch.syspath_prepend(str(tmp_path))
    yield pkg
    # 清理本次导入的模块，避免污染后续测试
    for mod in list(sys.modules):
        if mod == pkg.name or mod.startswith(pkg.name + "."):
            sys.modules.pop(mod, None)


class TestLoadUnload:
    """load / load_all / unload"""

    def test_load_success(self, plugins):
        """正常加载：入口被调用，注册进 ctx"""
        mod = plugins.write("p_ok", (
            "def setup(ctx, **cfg):\n"
            "    ctx.provide('svc', cfg.get('value', 'default'))\n"
        ))
        ctx = Context()
        loader = PluginLoader()
        spec = PluginSpec(id="ok", module=mod, config={"value": "hello"})
        assert loader.load(spec, ctx) is True
        assert ctx.use("svc") == "hello"
        assert loader.is_loaded("ok")
        assert loader.count() == 1

    def test_load_missing_module(self):
        """模块不存在 → 失败但不抛异常"""
        ctx = Context()
        loader = PluginLoader()
        spec = PluginSpec(id="ghost", module="no.such.module")
        assert loader.load(spec, ctx) is False
        assert not loader.is_loaded("ghost")

    def test_load_missing_entry(self, plugins):
        """入口函数不存在 → 失败"""
        mod = plugins.write("p_noentry", "x = 1\n")
        ctx = Context()
        loader = PluginLoader()
        assert loader.load(PluginSpec(id="noentry", module=mod, entry="setup"), ctx) is False

    def test_load_non_callable_entry(self, plugins):
        """入口不是函数 → 失败"""
        mod = plugins.write("p_badentry", "setup = 42\n")
        ctx = Context()
        loader = PluginLoader()
        assert loader.load(PluginSpec(id="badentry", module=mod), ctx) is False

    def test_load_entry_raises_rolls_back(self, plugins):
        """入口抛异常 → 回滚该插件的部分注册"""
        mod = plugins.write("p_boom", (
            "def setup(ctx, **cfg):\n"
            "    ctx.provide('half', 1)\n"
            "    raise RuntimeError('boom')\n"
        ))
        ctx = Context()
        loader = PluginLoader()
        assert loader.load(PluginSpec(id="boom", module=mod), ctx) is False
        assert ctx.has("half") is False, "失败的插件不应留下半成品注册"
        assert not loader.is_loaded("boom")

    def test_load_returns_disposer(self, plugins):
        """入口返回的 disposer 在卸载时被调用"""
        mod = plugins.write("p_disposer", (
            "def setup(ctx, **cfg):\n"
            "    ctx.provide('svc', 1)\n"
            "    ctx.emit('plugin.ready')\n"
            "    return lambda: ctx.emit('plugin.bye')\n"
        ))
        events = []
        ctx = Context()
        ctx.on("plugin.ready", lambda e: events.append("ready"))
        ctx.on("plugin.bye", lambda e: events.append("bye"))

        loader = PluginLoader()
        assert loader.load(PluginSpec(id="d", module=mod), ctx) is True
        assert events == ["ready"]
        assert ctx.use("svc") == 1

        assert loader.unload("d", ctx) is True
        assert events == ["ready", "bye"]
        assert ctx.has("svc") is False

    def test_unload_disposer_exception_reported(self, plugins):
        """卸载时 disposer 抛异常 → unload 返回 False"""
        mod = plugins.write("p_badbye", (
            "def setup(ctx, **cfg):\n"
            "    return lambda: (_ for _ in ()).throw(RuntimeError('bye boom'))\n"
        ))
        ctx = Context()
        loader = PluginLoader()
        loader.load(PluginSpec(id="b", module=mod), ctx)
        assert loader.unload("b", ctx) is False

    def test_load_all_partial_failure(self, plugins):
        """单个失败不影响其他"""
        mod_a = plugins.write("p_a", "def setup(ctx, **cfg):\n    ctx.provide('a', 1)\n")
        mod_c = plugins.write("p_c", "def setup(ctx, **cfg):\n    ctx.provide('c', 3)\n")
        ctx = Context()
        loader = PluginLoader()
        specs = [
            PluginSpec(id="a", module=mod_a),
            PluginSpec(id="bad", module="no.such.module"),
            PluginSpec(id="c", module=mod_c),
        ]
        ok, failed = loader.load_all(specs, ctx)
        assert ok == ["a", "c"]
        assert failed == ["bad"]
        assert ctx.use("a") == 1 and ctx.use("c") == 3

    def test_unload_unknown_returns_true(self):
        """卸载未加载的插件返回 True（视为已达成）"""
        assert PluginLoader().unload("never") is True

    def test_unload_all(self, plugins):
        """批量卸载"""
        mod = plugins.write("p_x", "def setup(ctx, **cfg):\n    pass\n")
        ctx = Context()
        loader = PluginLoader()
        loader.load_all([
            PluginSpec(id="x1", module=mod),
            PluginSpec(id="x2", module=mod),
        ], ctx)
        assert loader.count() == 2
        assert loader.unload_all() == 2
        assert loader.count() == 0

    def test_loaded_ids_and_spec_of(self, plugins):
        """查询接口"""
        mod = plugins.write("p_q", "def setup(ctx, **cfg):\n    pass\n")
        ctx = Context()
        loader = PluginLoader()
        spec = PluginSpec(id="q", module=mod)
        loader.load(spec, ctx)
        assert loader.loaded_ids() == ["q"]
        assert loader.spec_of("q") is spec
        assert loader.spec_of("nope") is None

    def test_repr(self):
        """repr 显示加载数"""
        assert "0" in repr(PluginLoader())

    def test_end_to_end_parse_sort_load(self, plugins):
        """完整流程：解析 → 排序 → 加载"""
        mod_base = plugins.write("p_base", "def setup(ctx, **cfg):\n    ctx.provide('base', 1)\n")
        mod_top = plugins.write("p_top", (
            "def setup(ctx, **cfg):\n"
            "    assert ctx.use('base') == 1\n"    # 依赖必须已加载
            "    ctx.provide('top', 2)\n"
        ))
        cfg = {"agent": {"plugins": [
            {"id": "top", "module": mod_top, "depends_on": ["base"]},
            {"id": "base", "module": mod_base},
        ]}}
        ctx = Context()
        loader = PluginLoader()
        specs = loader.parse_config(cfg)
        ordered = loader.resolve_order(specs)
        assert [s.id for s in ordered] == ["base", "top"]
        ok, failed = loader.load_all(ordered, ctx)
        assert failed == []
        assert ctx.use("top") == 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
