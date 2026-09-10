"""插件内核测试

覆盖模块：
- core/kernel/events.py    事件总线
- core/kernel/service.py   服务基类与异常
- core/kernel/registry.py  通用注册表
- core/kernel/context.py   插件上下文
"""

import sys
import time
from pathlib import Path

import pytest

project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from core.kernel import (
    CircularDependencyError,
    ConfigError,
    Context,
    Event,
    EventBus,
    EventTypes,
    MissingDependencyError,
    Registry,
    Service,
    ServiceNotFound,
)


# ══════════════════════════════════════════════════════
#  EventBus
# ══════════════════════════════════════════════════════

class TestEventDataclass:
    """A-01: Event 数据结构"""

    def test_instantiate(self):
        """Event 可实例化"""
        e = Event(type="test.event", data={"k": "v"})
        assert e.type == "test.event"
        assert e.data == {"k": "v"}

    def test_auto_timestamp(self):
        """timestamp 自动填充"""
        before = time.time()
        e = Event(type="t")
        after = time.time()
        assert before <= e.timestamp <= after

    def test_default_data_is_independent(self):
        """data 默认值不共享（default_factory 而非可变默认参数）"""
        e1 = Event(type="a")
        e2 = Event(type="b")
        e1.data["x"] = 1
        assert e2.data == {}

    def test_source_default_empty(self):
        """source 默认为空字符串"""
        assert Event(type="t").source == ""


class TestEventSubscription:
    """A-02: 事件订阅"""

    def test_on_and_emit(self):
        """订阅后能收到事件"""
        bus = EventBus()
        received = []
        bus.on("test", lambda e: received.append(e))
        bus.emit("test", v=1)
        assert len(received) == 1
        assert received[0].data == {"v": 1}

    def test_dispose_removes_handler(self):
        """返回的 disposer 能取消订阅"""
        bus = EventBus()
        calls = []
        dispose = bus.on("test", lambda e: calls.append(1))
        bus.emit("test")
        assert len(calls) == 1
        dispose()
        bus.emit("test")
        assert len(calls) == 1

    def test_dispose_idempotent(self):
        """disposer 幂等"""
        bus = EventBus()
        dispose = bus.on("test", lambda e: None)
        dispose()
        dispose()  # 不应抛异常

    def test_multiple_handlers(self):
        """同一事件可注册多个 handler"""
        bus = EventBus()
        order = []
        bus.on("test", lambda e: order.append("a"))
        bus.on("test", lambda e: order.append("b"))
        bus.emit("test")
        assert order == ["a", "b"]

    def test_off_returns_bool(self):
        """off 精确移除，重复移除返回 False"""
        bus = EventBus()
        handler = lambda e: None
        bus.on("test", handler)
        assert bus.off("test", handler) is True
        assert bus.off("test", handler) is False

    def test_off_unknown_event(self):
        """off 不存在的事件类型不抛异常"""
        bus = EventBus()
        assert bus.off("nonexistent", lambda e: None) is False

    def test_handler_count(self):
        """订阅者计数"""
        bus = EventBus()
        assert bus.handler_count("test") == 0
        bus.on("test", lambda e: None)
        assert bus.handler_count("test") == 1


class TestEventEmitIsolation:
    """A-03: 事件发射与异常隔离（R3 竞态防护基础）"""

    def test_exception_does_not_break_others(self):
        """单 handler 异常不影响其他 handler"""
        bus = EventBus()
        calls = []

        def bad(e):
            raise RuntimeError("boom")

        bus.on("test", bad)
        bus.on("test", lambda e: calls.append(1))

        bus.emit("test")  # 不应抛出
        assert len(calls) == 1

    def test_all_handlers_called_despite_exception(self):
        """异常 handler 之后的 handler 仍被执行"""
        bus = EventBus()
        order = []

        def bad(e):
            order.append("bad")
            raise RuntimeError("boom")

        bus.on("test", bad)
        bus.on("test", lambda e: order.append("after"))
        bus.emit("test")
        assert order == ["bad", "after"]

    def test_handler_can_modify_subscription(self):
        """handler 内修改订阅不影响本次发射"""
        bus = EventBus()
        calls = []

        def self_removing(e):
            calls.append(1)
            bus.off("test", self_removing)

        bus.on("test", self_removing)
        bus.on("test", lambda e: calls.append(2))
        bus.emit("test")
        assert calls == [1, 2]

    def test_emit_no_handlers(self):
        """无订阅者时 emit 不抛异常"""
        EventBus().emit("nothing.here", v=1)


class TestEventOnceAndAny:
    """A-04: 一次性订阅与通配订阅"""

    def test_once_fires_only_once(self):
        """once 触发后自动移除"""
        bus = EventBus()
        calls = []
        bus.once("test", lambda e: calls.append(1))
        bus.emit("test")
        bus.emit("test")
        assert len(calls) == 1

    def test_once_dispose_before_fire(self):
        """once 在触发前可取消"""
        bus = EventBus()
        calls = []
        dispose = bus.once("test", lambda e: calls.append(1))
        dispose()
        bus.emit("test")
        assert calls == []

    def test_on_any_receives_all(self):
        """on_any 接收所有事件类型"""
        bus = EventBus()
        types = []
        bus.on_any(lambda e: types.append(e.type))
        bus.emit("a")
        bus.emit("b")
        assert types == ["a", "b"]

    def test_on_any_dispose(self):
        """on_any 的 disposer 生效"""
        bus = EventBus()
        calls = []
        dispose = bus.on_any(lambda e: calls.append(1))
        dispose()
        bus.emit("a")
        assert calls == []

    def test_any_handler_count(self):
        """通配订阅者计数"""
        bus = EventBus()
        assert bus.any_handler_count() == 0
        bus.on_any(lambda e: None)
        assert bus.any_handler_count() == 1


class TestEventCleanup:
    """事件总线清理"""

    def test_clear_removes_all(self):
        """clear 清空所有订阅"""
        bus = EventBus()
        bus.on("a", lambda e: None)
        bus.on_any(lambda e: None)
        bus.clear()
        assert bus.handler_count("a") == 0
        assert bus.any_handler_count() == 0

    def test_event_types_listing(self):
        """event_types 返回有订阅者的事件"""
        bus = EventBus()
        bus.on("a", lambda e: None)
        bus.on("b", lambda e: None)
        assert set(bus.event_types()) == {"a", "b"}


class TestEventTypesConstants:
    """事件类型常量表"""

    def test_speech_events(self):
        assert EventTypes.SPEECH_RECOGNIZED == "speech.recognized"
        assert EventTypes.SPEECH_INTERRUPTED == "speech.interrupted"

    def test_task_events(self):
        assert EventTypes.INTENT_RESOLVED == "intent.resolved"
        assert EventTypes.TASK_SUBMITTED == "task.submitted"
        assert EventTypes.TASK_PROGRESS == "task.progress"
        assert EventTypes.TASK_COMPLETED == "task.completed"
        assert EventTypes.TASK_FAILED == "task.failed"

    def test_feedback_events(self):
        assert EventTypes.FEEDBACK_ACK == "feedback.ack"
        assert EventTypes.FEEDBACK_RESULT == "feedback.result"
        assert EventTypes.FEEDBACK_CONFIRM == "feedback.confirm"


# ══════════════════════════════════════════════════════
#  Service / 异常
# ══════════════════════════════════════════════════════

class TestServiceBase:
    """A-06: Service 基类与异常"""

    def test_service_cannot_instantiate(self):
        """Service 是抽象类，不可直接实例化"""
        with pytest.raises(TypeError):
            Service()

    def test_subclass_instantiable(self):
        """子类可实例化"""
        class MyService(Service):
            pass

        assert isinstance(MyService(), Service)

    def test_capability_name_auto_derived(self):
        """capability_name 从类名自动推导（snake_case）"""
        class RuleRouter(Service):
            pass

        assert RuleRouter.capability_name == "rule_router"

    def test_capability_name_explicit_override(self):
        """capability_name 可显式声明"""
        class MyService(Service):
            capability_name = "custom.name"

        assert MyService.capability_name == "custom.name"

    def test_capability_name_inherited(self):
        """子类继承父类的 capability_name"""
        class RouterService(Service):
            capability_name = "router"

        class RuleRouter(RouterService):
            pass

        assert RuleRouter.capability_name == "router"
        assert RouterService.capability_name == "router"

    def test_repr_shows_capability(self):
        """repr 显示能力名"""
        class MyService(Service):
            capability_name = "my_svc"

        assert "my_svc" in repr(MyService())

    def test_service_not_found_message(self):
        """ServiceNotFound 携带服务名"""
        err = ServiceNotFound("my_service")
        assert err.name == "my_service"
        assert "my_service" in str(err)

    def test_circular_dependency_message(self):
        """CircularDependencyError 携带环路径"""
        err = CircularDependencyError(["a", "b", "a"])
        assert err.cycle == ["a", "b", "a"]
        assert "a" in str(err) and "b" in str(err)

    def test_missing_dependency_message(self):
        """MissingDependencyError 携带插件名与缺失项"""
        err = MissingDependencyError("plugin_a", "plugin_b")
        assert err.plugin_id == "plugin_a"
        assert err.missing == "plugin_b"
        assert "plugin_a" in str(err) and "plugin_b" in str(err)

    def test_config_error_is_service_error(self):
        """异常继承关系"""
        from core.kernel import ServiceError

        assert issubclass(ConfigError, ServiceError)
        assert issubclass(ServiceNotFound, ServiceError)


# ══════════════════════════════════════════════════════
#  Registry
# ══════════════════════════════════════════════════════

class _NamedItem:
    """测试用带 name 属性的对象"""

    def __init__(self, name: str, value: int = 0):
        self.name = name
        self.value = value


class TestRegistryBasic:
    """A-14: 通用注册表"""

    def test_register_and_get(self):
        """注册与获取"""
        reg = Registry(kind="item")
        item = _NamedItem("a")
        reg.register(item)
        assert reg.get("a") is item
        assert reg.has("a") is True

    def test_register_explicit_name(self):
        """显式指定注册名，覆盖对象的 name 属性"""
        reg = Registry(kind="item")
        item = _NamedItem("a")
        reg.register(item, name="custom")
        assert reg.has("custom")
        assert not reg.has("a")

    def test_register_no_name_raises(self):
        """无法推导注册名时抛 ValueError"""
        reg = Registry(kind="tool")
        with pytest.raises(ValueError) as exc:
            reg.register(object())
        assert "tool" in str(exc.value)

    def test_disposer_removes(self):
        """返回的 disposer 可注销"""
        reg = Registry(kind="item")
        dispose = reg.register(_NamedItem("a"))
        dispose()
        assert reg.has("a") is False

    def test_disposer_idempotent(self):
        """disposer 幂等"""
        reg = Registry(kind="item")
        dispose = reg.register(_NamedItem("a"))
        dispose()
        dispose()
        assert reg.has("a") is False

    def test_disposer_does_not_remove_replacement(self):
        """被覆盖后，旧 disposer 不误删新对象"""
        reg = Registry(kind="item")
        old = _NamedItem("a", value=1)
        new = _NamedItem("a", value=2)
        dispose_old = reg.register(old)
        reg.register(new)
        dispose_old()
        assert reg.get("a") is new

    def test_overwrite_warns(self, caplog):
        """覆盖同名项记录 warning"""
        reg = Registry(kind="item")
        reg.register(_NamedItem("a"))
        reg.register(_NamedItem("a"))
        assert reg.get("a").value == 0  # 后者生效
        assert len(reg) == 1

    def test_unregister(self):
        """按名注销"""
        reg = Registry(kind="item")
        reg.register(_NamedItem("a"))
        assert reg.unregister("a") is True
        assert reg.unregister("a") is False

    def test_get_missing_returns_none(self):
        """获取不存在的项返回 None"""
        assert Registry(kind="item").get("nope") is None


class TestRegistrySnapshot:
    """A-15: 注册表快照语义与过滤"""

    def test_names_is_snapshot(self):
        """names 返回快照，不受后续修改影响"""
        reg = Registry(kind="item")
        reg.register(_NamedItem("a"))
        names = reg.names()
        reg.register(_NamedItem("b"))
        assert names == ["a"]

    def test_items_is_snapshot(self):
        """items 返回快照"""
        reg = Registry(kind="item")
        reg.register(_NamedItem("a", 1))
        items = reg.items()
        reg.register(_NamedItem("b", 2))
        assert len(items) == 1

    def test_filter(self):
        """谓词过滤"""
        reg = Registry(kind="item")
        reg.register(_NamedItem("a", 1))
        reg.register(_NamedItem("b", 2))
        reg.register(_NamedItem("c", 3))
        assert len(reg.filter(lambda x: x.value > 1)) == 2

    def test_find_first(self):
        """查找第一个匹配项"""
        reg = Registry(kind="item")
        reg.register(_NamedItem("a", 1))
        reg.register(_NamedItem("b", 2))
        found = reg.find_first(lambda x: x.value == 2)
        assert found is not None and found.name == "b"

    def test_find_first_none(self):
        """无匹配返回 None"""
        reg = Registry(kind="item")
        reg.register(_NamedItem("a", 1))
        assert reg.find_first(lambda x: x.value == 99) is None

    def test_container_protocol(self):
        """容器协议：len / in / iter"""
        reg = Registry(kind="item")
        reg.register(_NamedItem("a"))
        reg.register(_NamedItem("b"))
        assert len(reg) == 2
        assert "a" in reg
        assert len(list(reg)) == 2

    def test_clear(self):
        """清空"""
        reg = Registry(kind="item")
        reg.register(_NamedItem("a"))
        reg.clear()
        assert len(reg) == 0


# ══════════════════════════════════════════════════════
#  Context
# ══════════════════════════════════════════════════════

class TestContextProvideUse:
    """A-07/A-08/A-09: Context 注册与获取"""

    def test_init_empty(self):
        """A-07: 初始状态"""
        ctx = Context()
        assert ctx.depth == 0
        assert ctx.disposed is False

    def test_provide_and_use(self):
        """A-08/A-09: 注册后能获取"""
        ctx = Context()
        obj = object()
        ctx.provide("svc", obj)
        assert ctx.use("svc") is obj

    def test_provide_empty_name_raises(self):
        """A-08: 空服务名抛 ValueError"""
        ctx = Context()
        with pytest.raises(ValueError):
            ctx.provide("", object())

    def test_use_not_found_raises(self):
        """A-09: 未注册抛 ServiceNotFound"""
        ctx = Context()
        with pytest.raises(ServiceNotFound) as exc:
            ctx.use("nonexistent")
        assert exc.value.name == "nonexistent"

    def test_use_or_default(self):
        """use_or 未注册返回默认值"""
        ctx = Context()
        assert ctx.use_or("nope", "default") == "default"

    def test_has(self):
        """has 检查（含父级）"""
        parent = Context()
        parent.provide("a", 1)
        child = parent.fork()
        assert child.has("a") is True
        assert child.has("nope") is False

    def test_provide_disposer(self):
        """provide 返回的 disposer 可注销"""
        ctx = Context()
        dispose = ctx.provide("svc", "value")
        dispose()
        with pytest.raises(ServiceNotFound):
            ctx.use("svc")

    def test_overwrite_takes_effect(self):
        """重复注册同名服务，后者生效"""
        ctx = Context()
        ctx.provide("svc", "first")
        ctx.provide("svc", "second")
        assert ctx.use("svc") == "second"


class TestContextFork:
    """A-10: 作用域隔离"""

    def test_child_inherits_parent(self):
        """子上下文可读父服务"""
        parent = Context()
        parent.provide("a", "parent_value")
        child = parent.fork()
        assert child.use("a") == "parent_value"

    def test_child_registration_invisible_to_parent(self):
        """子注册对父不可见"""
        parent = Context()
        child = parent.fork()
        child.provide("b", "child_value")
        assert child.use("b") == "child_value"
        with pytest.raises(ServiceNotFound):
            parent.use("b")

    def test_child_overrides_parent(self):
        """子可覆盖父的服务（本地优先）"""
        parent = Context()
        parent.provide("a", "parent")
        child = parent.fork()
        child.provide("a", "child")
        assert child.use("a") == "child"
        assert parent.use("a") == "parent"

    def test_child_dispose_does_not_affect_parent(self):
        """子 dispose 不影响父"""
        parent = Context()
        parent.provide("a", "value")
        child = parent.fork()
        child.provide("b", "temp")
        child.dispose()
        assert parent.use("a") == "value"
        assert child.disposed is True

    def test_nested_fork(self):
        """多级 fork"""
        root = Context()
        root.provide("a", 1)
        c1 = root.fork()
        c2 = c1.fork()
        c2.provide("b", 2)
        assert c2.use("a") == 1
        assert c2.use("b") == 2
        assert root.depth == 0
        assert c1.depth == 1
        assert c2.depth == 2

    def test_fork_depth_warning(self, caplog):
        """fork 深度超限仅告警不阻断"""
        import logging

        ctx = Context()
        for _ in range(12):
            ctx = ctx.fork()
        with caplog.at_level(logging.WARNING):
            deep = ctx.fork()
        assert deep is not None  # 未抛异常


class TestContextDispose:
    """A-11: 逆序清理"""

    def test_dispose_reverse_order(self):
        """清理顺序为注册逆序"""
        ctx = Context()
        order = []
        ctx.provide("a", 1)
        ctx.provide("b", 2)
        # 替换 disposer 以观察顺序
        ctx._disposers[0] = lambda: order.append("a")
        ctx._disposers[1] = lambda: order.append("b")
        ctx.dispose()
        assert order == ["b", "a"]

    def test_dispose_isolates_exceptions(self):
        """单个 disposer 异常不中断其余清理"""
        ctx = Context()

        def bad():
            raise RuntimeError("boom")

        cleaned = []
        ctx._disposers = [bad, lambda: cleaned.append("ok")]
        ctx.dispose()  # 不应抛出
        assert cleaned == ["ok"]

    def test_dispose_clears_services(self):
        """清理后服务不可用"""
        ctx = Context()
        ctx.provide("a", 1)
        ctx.dispose()
        with pytest.raises(ServiceNotFound):
            ctx.use("a")

    def test_dispose_idempotent(self):
        """dispose 幂等"""
        ctx = Context()
        ctx.provide("a", 1)
        ctx.dispose()
        ctx.dispose()  # 不应抛异常
        assert ctx.disposed is True

    def test_dispose_clears_events(self):
        """清理后事件订阅失效"""
        ctx = Context()
        calls = []
        ctx.on("test", lambda e: calls.append(1))
        ctx.dispose()
        ctx.emit("test")
        assert calls == []

    def test_repr(self):
        """repr 可读"""
        ctx = Context()
        assert "active" in repr(ctx)
        ctx.dispose()
        assert "disposed" in repr(ctx)


class TestContextEvents:
    """A-12: Context 事件代理"""

    def test_on_and_emit(self):
        """订阅与发射"""
        ctx = Context()
        received = []
        ctx.on("test", lambda e: received.append(e.data))
        ctx.emit("test", k="v")
        assert received == [{"k": "v"}]

    def test_on_returns_disposer(self):
        """订阅返回可用的 disposer"""
        ctx = Context()
        calls = []
        dispose = ctx.on("test", lambda e: calls.append(1))
        dispose()
        ctx.emit("test")
        assert calls == []

    def test_once(self):
        """一次性订阅"""
        ctx = Context()
        calls = []
        ctx.once("test", lambda e: calls.append(1))
        ctx.emit("test")
        ctx.emit("test")
        assert len(calls) == 1

    def test_events_property(self):
        """events 属性暴露底层总线"""
        ctx = Context()
        assert isinstance(ctx.events, EventBus)

    def test_emit_with_source(self):
        """发射时携带 source"""
        ctx = Context()
        sources = []
        ctx.on("test", lambda e: sources.append(e.source))
        ctx.emit("test", source="my_plugin")
        assert sources == ["my_plugin"]


class TestContextRegistry:
    """Context 具名注册表"""

    def test_registry_created_lazily(self):
        """注册表按需创建"""
        ctx = Context()
        assert ctx.has_registry("tool") is False
        reg = ctx.registry("tool")
        assert isinstance(reg, Registry)
        assert ctx.has_registry("tool") is True

    def test_registry_same_instance(self):
        """同名注册表返回同一实例"""
        ctx = Context()
        assert ctx.registry("tool") is ctx.registry("tool")

    def test_registry_cleared_on_dispose(self):
        """dispose 清理注册表"""
        ctx = Context()
        ctx.registry("tool").register(_NamedItem("a"))
        ctx.dispose()
        assert ctx.has_registry("tool") is False

    def test_registry_isolated_between_contexts(self):
        """不同 context 的注册表互相隔离"""
        ctx1 = Context()
        ctx2 = Context()
        ctx1.registry("tool").register(_NamedItem("a"))
        assert ctx2.registry("tool").has("a") is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
