"""P4-B1 / D14：事件总线契约守护

## 守的是什么

项目里有**两个同名不同契约**的 EventBus：

| 类 | 订阅 | 发送 | 事件类型 |
|----|------|------|---------|
| `core.event_bus.EventBus` | `subscribe(EventType, h)` | `emit(EventType, data: dict)` | 枚举 |
| `core.kernel.events.EventBus` | `on(str, h) -> disposer` | `emit(str, **kwargs)` | 字符串 |

Agent 层只认后者。历史上 `core/app.py::_setup_agent_layer` 传的是前者，于是
`pipeline.start()` 里 `bus.on(...)` 抛 `AttributeError`，被那一层的 `except` 吞掉：

> **Agent 层在整个真实应用里一直是死的**（静默降级成纯对话），
> 而所有脚本级验收各自 new 内核总线，**全都测不出来**。

这与 P4-B3 处理的"引擎缺 `chat_with_tools` → 路由兜底静默失效"是同一类：
**降级可以接受，静默降级不可接受。**

## 本轮的处置边界（重要）

计划里 B1 的完整选项是"加适配层把两个总线统一"。那会动到受 G12 保护的语音管线，
且 `p4-plan.md` 把"是否接受该改动面"列为**人工决定**（能力边界表 §六）。
所以本轮只落地**低风险、高收益**的那一半：**守护测试 + 显式拒绝**，
把"接错就静默降级"这个洞永久堵上；适配层本身留待人工决定后再做。

## 反方向验证

`assert_kernel_bus` 必须**双向**成立：内核总线放行、旧总线拒绝 ——
只会拒绝的检查是空转（§16.4 的教训）。
"""

from __future__ import annotations

import pytest

from agent.bootstrap import AgentConfig, assert_kernel_bus, build_agent_stack
from core.event_bus import EventBus as LegacyEventBus
from core.kernel.events import EventBus as KernelEventBus


class TestAssertKernelBus:
    def test_kernel_bus_is_accepted(self):
        """反方向①：正确的总线必须放行（否则守护就成了拦路石）"""
        assert_kernel_bus(KernelEventBus())

    def test_legacy_bus_is_rejected(self):
        """反方向②：旧总线必须被拒"""
        with pytest.raises(TypeError):
            assert_kernel_bus(LegacyEventBus())

    def test_rejection_message_names_both_classes(self):
        """错误信息必须**点名两个类** —— 否则接线的人不知道换成哪一个"""
        with pytest.raises(TypeError) as ei:
            assert_kernel_bus(LegacyEventBus())
        msg = str(ei.value)
        assert "core.event_bus.EventBus" in msg
        assert "core.kernel.events.EventBus" in msg
        assert "subscribe" in msg, "要指出它像哪一个（旧总线有 subscribe）"

    def test_none_is_rejected(self):
        """None 也要拒绝，且说清该传什么"""
        with pytest.raises(TypeError) as ei:
            assert_kernel_bus(None)
        assert "core.kernel.events.EventBus" in str(ei.value)

    def test_missing_methods_are_named(self):
        """缺哪些方法要列出来（便于对着补）"""
        class Almost:
            def on(self, *a, **k): ...
            def emit(self, *a, **k): ...

        with pytest.raises(TypeError) as ei:
            assert_kernel_bus(Almost())
        assert "off" in str(ei.value)

    def test_duck_typed_kernel_like_bus_is_accepted(self):
        """只要契约齐就放行 —— 守护检查的是**契约**，不是具体类

        这条很重要：`AgentStack` 的测试替身不该被迫继承真实类。
        """
        class Stub:
            def on(self, *a, **k): ...
            def off(self, *a, **k): ...
            def emit(self, *a, **k): ...

        assert_kernel_bus(Stub())


class TestBuildAgentStackGuard:
    """守护接在装配入口上，而不只是一个可选的函数"""

    def test_wrong_bus_fails_fast(self):
        """传旧总线 → 立刻 TypeError，**绝不**静默降级成「能启动但没反应」"""
        with pytest.raises(TypeError) as ei:
            build_agent_stack(AgentConfig(), LegacyEventBus())
        assert "契约不符" in str(ei.value)

    def test_none_bus_fails_fast(self):
        with pytest.raises(TypeError):
            build_agent_stack(AgentConfig(), None)

    def test_correct_bus_still_assembles(self):
        """反方向：正确的总线照旧能装配起来（不能因为加守护把功能弄坏）"""
        stack = build_agent_stack(AgentConfig(), KernelEventBus())
        try:
            assert stack.registry is not None
            assert len(stack.registry.names()) > 0
        finally:
            stack.dispose()


class TestTwoContractsAreIncompatible:
    """把"两者不能互换"钉成事实，而不是注释里的说法

    将来若有人真要统一它们，这些断言会红，从而**强制**那次改动是有意的。
    """

    def test_legacy_has_subscribe_and_kernel_has_on(self):
        legacy, kernel = LegacyEventBus(), KernelEventBus()
        assert callable(getattr(legacy, "subscribe", None))
        assert not callable(getattr(legacy, "on", None)), (
            "旧总线若长出 on()，说明两个总线被合并了 —— 请重新评估 B1 的适配层"
        )
        assert callable(getattr(kernel, "on", None))
        assert not callable(getattr(kernel, "subscribe", None)), (
            "内核总线若长出 subscribe()，同上"
        )

    def test_kernel_on_returns_a_disposer(self):
        """内核契约的关键特征：`on` 返回 disposer（可热插拔，设计原则 4）

        ⚠️ handler 收的是**一个位置参数**（`Event` 对象），不是 `**kwargs`。
        我第一版测试按 `**kwargs` 写，直接红 —— 契约的细节只能从代码/实测拿，
        不能凭"看起来应该是这样"。
        """
        kernel = KernelEventBus()
        seen = []
        off = kernel.on("demo.event", lambda ev: seen.append(ev.data))
        assert callable(off), "on() 必须返回 disposer"
        kernel.emit("demo.event", value=1)
        off()
        kernel.emit("demo.event", value=2)
        assert seen == [{"value": 1}], "disposer 之后不该再收到事件"

    def test_kernel_emit_payload_and_source(self):
        """内核契约：`emit(type, source=..., **data)` → handler 收到 `Event`

        `source` 是 `emit` 的**第二个形参**，所以 `emit(t, source="x")`
        **不会**把 `source` 放进 `data` —— P3 就是在这里踩过（事件负载里没有
        计划来源，测试读 `started["source"]` 直接 KeyError，故负载字段改名
        `plan_source`）。这条断言把那件事钉住。
        """
        kernel = KernelEventBus()
        got = []
        kernel.on("demo.payload", lambda ev: got.append(ev))
        kernel.emit("demo.payload", source="rule", value=7)

        assert len(got) == 1
        ev = got[0]
        assert ev.type == "demo.payload"
        assert ev.data == {"value": 7}
        assert ev.source == "rule"
        assert "source" not in ev.data, (
            "source 是 emit 的形参、不是负载字段 —— 想放进负载必须换个名字"
        )
