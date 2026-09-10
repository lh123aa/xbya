"""内核收尾覆盖测试（P1 验收）

补齐最后一小批「可达但未被现有用例走到」的分支：
- Context.reclaim 的越界/负值钳制
- EventBus.on_any 处理器异常隔离
- PluginLoader._run_disposers 的异常容错
- PluginLoader._find_cycle 真正回报环路径
"""

from __future__ import annotations

from typing import Any, List

import pytest

from core.kernel.context import Context
from core.kernel.events import EventBus
from core.kernel.loader import PluginLoader, PluginSpec
from core.kernel.service import CircularDependencyError

# ══════════════════════════════════════════════
#  Context.reclaim 边界
# ══════════════════════════════════════════════


class TestReclaimBounds:
    """reclaim 的 mark 越界处理"""

    def test_negative_mark_clamps_to_zero(self):
        """mark < 0 → 钳到 0（回滚全部注册）"""
        ctx = Context()
        ctx.provide("a", object())
        ctx.provide("b", object())

        # 负值应当被钳到 0，等同于回滚所有注册
        owned = ctx.reclaim(-1)
        assert len(owned) >= 2, "负 mark 应当收编全部已注册项"
        for dispose in owned:
            dispose()

        assert ctx.use_or("a") is None
        assert ctx.use_or("b") is None

    def test_mark_beyond_length_returns_empty(self):
        """mark >= 长度 → 无可回收项，返回空列表"""
        ctx = Context()
        ctx.provide("x", object())

        assert ctx.reclaim(999) == []
        # 未回收 → 服务仍在
        assert ctx.use_or("x") is not None

    def test_normal_mark_only_collects_after(self):
        """正常 mark：只回收 mark 之后的注册"""
        ctx = Context()
        ctx.provide("keep", object())
        mark = ctx.mark()
        ctx.provide("drop", object())

        owned = ctx.reclaim(mark)
        for dispose in owned:
            dispose()

        assert ctx.use_or("keep") is not None
        assert ctx.use_or("drop") is None


# ══════════════════════════════════════════════
#  事件总线 on_any 异常隔离
# ══════════════════════════════════════════════


class TestOnAnyIsolation:
    """on_any 处理器抛异常不得影响其他订阅者"""

    def test_on_any_handler_exception_is_isolated(self, caplog):
        bus = EventBus()
        seen: List[Any] = []

        def boom(event):
            raise RuntimeError("any handler 炸了")

        bus.on_any(boom)
        bus.on_any(lambda e: seen.append(e.type))

        with caplog.at_level("ERROR"):
            bus.emit("demo.event", source="test")

        assert seen == ["demo.event"], "前一个 on_any 抛异常后，后一个仍须收到事件"
        assert any("on_any" in r.message for r in caplog.records)

    def test_typed_and_any_both_isolated(self):
        """typed handler 与 any handler 异常互不干扰"""
        bus = EventBus()
        seen: List[str] = []

        bus.on("t", lambda e: (_ for _ in ()).throw(ValueError("typed boom")))
        bus.on("t", lambda e: seen.append("typed-ok"))
        bus.on_any(lambda e: (_ for _ in ()).throw(ValueError("any boom")))
        bus.on_any(lambda e: seen.append("any-ok"))

        bus.emit("t")

        assert seen == ["typed-ok", "any-ok"]


# ══════════════════════════════════════════════
#  Loader disposer 容错
# ══════════════════════════════════════════════


class TestDisposerResilience:
    """卸载时单个 disposer 失败不中断其余"""

    def test_run_disposers_skips_failures(self, caplog):
        order: List[str] = []

        def ok_a():
            order.append("a")

        def bad():
            order.append("bad")
            raise RuntimeError("disposer 炸了")

        def ok_b():
            order.append("b")

        with caplog.at_level("DEBUG"):
            done = PluginLoader._run_disposers([ok_a, bad, ok_b])

        # 逆序执行，坏的跳过，好的都执行
        assert order == ["b", "bad", "a"]
        assert done == 2
        assert any("disposer" in r.message for r in caplog.records)

    def test_unload_with_failing_disposer_does_not_raise(self):
        """插件 disposer 抛异常时 unload 仍返回，且插件标记为已卸载"""
        ctx = Context()
        loader = PluginLoader()

        loader._loaded["p"] = PluginSpec(id="p", module="m")
        loader._disposers["p"] = [
            lambda: (_ for _ in ()).throw(RuntimeError("boom")),
        ]

        assert loader.is_loaded("p") is True
        loader.unload("p", ctx)   # 不应抛异常
        assert loader.is_loaded("p") is False


# ══════════════════════════════════════════════
#  环检测：真正回报环路径
# ══════════════════════════════════════════════


class TestCyclePathReporting:
    """_find_cycle 必须给出真实的闭合环路径"""

    def test_two_node_cycle_path_is_closed(self):
        specs = [
            PluginSpec(id="a", module="m", depends_on=["b"]),
            PluginSpec(id="b", module="m", depends_on=["a"]),
        ]
        with pytest.raises(CircularDependencyError) as exc:
            PluginLoader().resolve_order(specs)

        cycle = exc.value.cycle
        assert cycle[0] == cycle[-1], f"环路径必须闭合: {cycle}"
        assert set(cycle) == {"a", "b"}

    def test_three_node_cycle_path_is_closed(self):
        specs = [
            PluginSpec(id="a", module="m", depends_on=["c"]),
            PluginSpec(id="b", module="m", depends_on=["a"]),
            PluginSpec(id="c", module="m", depends_on=["b"]),
        ]
        with pytest.raises(CircularDependencyError) as exc:
            PluginLoader().resolve_order(specs)

        cycle = exc.value.cycle
        assert cycle[0] == cycle[-1], f"环路径必须闭合: {cycle}"
        assert set(cycle) == {"a", "b", "c"}
        assert len(cycle) == 4, "三节点环应当是 a→…→a 共 4 段"

    def test_self_cycle(self):
        """自依赖也当作环"""
        specs = [PluginSpec(id="solo", module="m", depends_on=["solo"])]
        with pytest.raises(CircularDependencyError) as exc:
            PluginLoader().resolve_order(specs)
        assert exc.value.cycle == ["solo", "solo"]
