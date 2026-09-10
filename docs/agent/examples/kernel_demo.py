#!/usr/bin/env python
"""欣雅插件内核（`core/kernel`）可运行演示

用途：
    用一段自包含、带断言的脚本证明内核五项真实能力，作为 G0 关卡
    「示例可运行」一项的证据（见 `docs/agent/acceptance.md` §2.5 / §2.5 勘误）。

运行：
    python docs/agent/examples/kernel_demo.py

演示内容（5 节）：
    1. 事件总线 EventBus  —— on / emit / disposer 退订 / 通配 on_any / 多处理器 / 异常隔离
    2. Context            —— provide / use / use_or / has / ServiceNotFound / fork 作用域隔离
    3. Service 生命周期   —— Service 子类 + start() / dispose() + 注册返回 disposer 的热插拔
    4. 注册表 Registry    —— 注册 / 查找 / 快照 / 过滤 / 注销 / 覆盖保护
    5. 插件加载器         —— 临时目录里的两个假插件 + 拓扑排序 + 卸载与失败回滚

约定：
    - 只依赖标准库与 `core/kernel`，不 import 仓库中的任何业务代码
    - 假插件写在 `tempfile` 临时目录里，进程结束自动删除，不往仓库写插件
    - 每节都带真实断言；任一断言不成立则打印失败原因并以退出码 1 结束
    - 输出只用 ASCII 标记 + 中文，兼容 cp936 控制台
"""

import logging
import sys
import tempfile
import traceback
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

# ── 把仓库根目录加入 sys.path ──
# 本文件位于 <repo_root>/docs/agent/examples/kernel_demo.py，
# parents[3] 即仓库根目录（examples -> agent -> docs -> repo_root）。
# 这样无论从哪个工作目录运行都能 import core.kernel，且不硬编码盘符。
_REPO_ROOT: Path = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from core.kernel import (  # noqa: E402  （必须在 sys.path 调整之后导入）
    CircularDependencyError,
    ConfigError,
    Context,
    Disposable,
    Event,
    EventBus,
    EventTypes,
    MissingDependencyError,
    Registry,
    Service,
    ServiceError,
    ServiceNotFound,
)
from core.kernel.loader import PluginLoader, PluginSpec  # noqa: E402

# 演示中会故意制造 handler 异常、插件入口异常、重复插件 id 等错误路径，
# 内核会按设计把它们写进日志。这里压低内核日志级别，让 stdout 的分节输出保持可读
# （异常隔离 / 回滚等行为本身仍然由断言实际校验，不是"看不见就当没发生"）。
logging.getLogger("core.kernel").setLevel(logging.CRITICAL)


# ══════════════════════════════════════════════════════════════
#  断言记录器
# ══════════════════════════════════════════════════════════════

class Report:
    """断言与观察记录器

    Attributes:
        passed: 通过的断言条数
        failures: 失败断言（含所在节标题）列表
        notes: 运行中得到但不参与断言的观察项
    """

    def __init__(self) -> None:
        self.passed: int = 0
        self.failures: List[str] = []
        self.notes: List[str] = []
        self._section: str = ""

    @property
    def total(self) -> int:
        """断言总条数"""
        return self.passed + len(self.failures)

    def section(self, title: str) -> None:
        """打印分节标题"""
        self._section = title
        print()
        print("=" * 72)
        print(f"  {title}")
        print("=" * 72)

    def check(self, condition: bool, label: str) -> bool:
        """记录一条断言

        Args:
            condition: 断言是否成立
            label: 中文描述

        Returns:
            condition 的布尔值（便于串联使用）
        """
        if condition:
            self.passed += 1
            print(f"  [OK]   {label}")
        else:
            self.failures.append(f"[{self._section}] {label}")
            print(f"  [FAIL] {label}")
        return bool(condition)

    def note(self, text: str) -> None:
        """记录一条观察（不参与断言，结尾统一复述）"""
        self.notes.append(text)
        print(f"  [NOTE] {text}")

    def fail(self, text: str) -> None:
        """直接记录一条失败（用于节内未捕获异常）"""
        self.failures.append(f"[{self._section}] {text}")
        print(f"  [FAIL] {text}")


# ══════════════════════════════════════════════════════════════
#  第 1 节：事件总线
# ══════════════════════════════════════════════════════════════

def demo_event_bus(rep: Report) -> None:
    """事件总线：订阅（on/once/on_any）、发射（emit）、退订（disposer/off）、异常隔离"""
    rep.section("[1/5] 事件总线 EventBus：on / emit / disposer 退订 / on_any 通配 / 多处理器")

    bus = EventBus()
    counters: Dict[str, int] = {}
    received: List[Event] = []

    def bump(key: str) -> None:
        """计数器 +1"""
        counters[key] = counters.get(key, 0) + 1

    def on_progress(event: Event) -> None:
        """主处理器：记录事件对象"""
        bump("progress")
        received.append(event)

    dispose_progress: Callable[[], None] = bus.on(EventTypes.TASK_PROGRESS, on_progress)
    rep.check(bus.handler_count(EventTypes.TASK_PROGRESS) == 1, "on() 注册后 handler_count == 1")
    rep.check(
        EventTypes.TASK_PROGRESS in bus.event_types(),
        "event_types() 列出已订阅的事件名（事件名是字符串常量）",
    )

    # emit 的 source 形参与 **data 负载
    bus.emit(EventTypes.TASK_PROGRESS, source="demo", task_id="t1", percent=50)
    rep.check(counters.get("progress") == 1, "emit() 触发处理器一次")
    rep.check(
        len(received) == 1 and received[0].type == EventTypes.TASK_PROGRESS,
        "处理器收到的 Event.type 与订阅的事件名一致",
    )
    rep.check(received[0].source == "demo", "emit(source=...) 写入 Event.source")
    rep.check(
        received[0].data == {"task_id": "t1", "percent": 50},
        "emit(**data) 原样进入 Event.data（不吞字段）",
    )
    rep.check(received[0].timestamp > 0, "Event.timestamp 自动填充")

    # 多处理器：同一事件挂多个 handler，按注册顺序全部调用
    call_order: List[str] = []

    def handler_a(event: Event) -> None:
        call_order.append("a")

    def handler_b(event: Event) -> None:
        call_order.append("b")

    bus.on(EventTypes.TASK_PROGRESS, handler_a)
    bus.on(EventTypes.TASK_PROGRESS, handler_b)
    rep.check(bus.handler_count(EventTypes.TASK_PROGRESS) == 3, "同一事件可挂多个处理器（计数 3）")

    # 通配订阅：接收所有类型的事件
    any_types: List[str] = []

    def on_any(event: Event) -> None:
        any_types.append(event.type)

    dispose_any: Callable[[], None] = bus.on_any(on_any)
    rep.check(bus.any_handler_count() == 1, "on_any() 注册后 any_handler_count == 1")

    # 异常隔离：某个 handler 抛异常，不影响同一事件上的其他 handler
    def boom(event: Event) -> None:
        raise RuntimeError("故意抛出：演示 handler 异常隔离")

    bus.on(EventTypes.TASK_PROGRESS, boom)
    order_before = len(call_order)
    bus.emit(EventTypes.TASK_PROGRESS, task_id="t2")
    rep.check(len(call_order) - order_before == 2, "抛异常的处理器不影响同事件上的其他处理器（异常隔离）")
    rep.check(call_order == ["a", "b"], "多个处理器按注册顺序被调用")
    rep.check(len(received) == 2, "退订前的处理器每次 emit 都收到事件")

    bus.emit(EventTypes.FEEDBACK_ACK, text="好的")
    rep.check(any_types[-1] == EventTypes.FEEDBACK_ACK, "on_any 收到其它类型的事件（真通配，非同名订阅）")
    rep.check(counters.get("progress") == 2, "只订阅 task.progress 的处理器不会被别的事件触发")

    # 退订：disposer 执行后不再收到事件
    progress_before = counters["progress"]
    dispose_progress()
    rep.check(bus.handler_count(EventTypes.TASK_PROGRESS) == 3, "退订后 handler_count 减 1")
    bus.emit(EventTypes.TASK_PROGRESS, task_id="t3")
    rep.check(
        counters["progress"] == progress_before,
        "退订后处理器计数不再增加（disposer 真的摘掉了订阅）",
    )
    rep.check(len(received) == 2, "退订的处理器确实收不到后续事件")

    dispose_progress()  # 幂等
    rep.check(
        bus.handler_count(EventTypes.TASK_PROGRESS) == 3,
        "disposer 幂等：重复调用不会误删其他订阅（计数仍为 3）",
    )

    # once：只触发一次，随后自动退订
    once_seen: List[Event] = []

    def on_once(event: Event) -> None:
        once_seen.append(event)

    bus.once(EventTypes.TASK_COMPLETED, on_once)
    count_before_once = bus.handler_count(EventTypes.TASK_COMPLETED)
    bus.emit(EventTypes.TASK_COMPLETED, task_id="t9")
    bus.emit(EventTypes.TASK_COMPLETED, task_id="t9")
    rep.check(len(once_seen) == 1, "once() 只触发一次")
    rep.check(
        bus.handler_count(EventTypes.TASK_COMPLETED) == count_before_once - 1,
        "once() 触发后自动退订",
    )

    # off：按类型移除，找不到不抛异常
    bus.on("demo.custom", handler_a)
    rep.check(bus.off("demo.custom", handler_a) is True, "off() 移除已注册处理器返回 True")
    rep.check(bus.off("demo.custom", handler_a) is False, "off() 重复移除返回 False（不抛异常）")

    # 通配订阅也能用 disposer 撤销
    any_before = len(any_types)
    dispose_any()
    rep.check(bus.any_handler_count() == 0, "on_any 的 disposer 生效（通配计数归零）")
    bus.emit(EventTypes.FEEDBACK_ACK, text="再来一次")
    rep.check(len(any_types) == any_before, "退订后通配处理器不再收到任何事件")

    bus.clear()
    rep.check(
        bus.handler_count(EventTypes.TASK_PROGRESS) == 0 and bus.any_handler_count() == 0,
        "clear() 清空全部订阅（含通配）",
    )
    rep.check(bus.event_types() == [], "clear() 后 event_types() 为空")


# ══════════════════════════════════════════════════════════════
#  第 2 节：Context
# ══════════════════════════════════════════════════════════════

def demo_context(rep: Report) -> None:
    """Context：能力注册/获取、缺失异常、默认值、fork 作用域隔离、dispose 幂等"""
    rep.section("[2/5] Context：provide / use / use_or / has / ServiceNotFound / fork 隔离 / dispose")

    ctx = Context()

    class Greeter:
        """任意对象都能作为能力注册（内核不要求实现特定基类）"""

        def greet(self) -> str:
            """返回问候语"""
            return "你好，我是被 provide 的能力"

    greeter = Greeter()
    detach: Callable[[], None] = ctx.provide("greeter", greeter)
    rep.check(ctx.has("greeter") is True, "provide 后 has() 为 True")
    rep.check(ctx.use("greeter") is greeter, "use() 取回的是同一个对象（is 同一性）")
    rep.check(ctx.use_or("greeter") is greeter, "use_or() 命中时返回真实实现")
    rep.check(
        ctx.use_or("not_registered", "默认实现") == "默认实现",
        "use_or() 未注册时返回默认值（不抛异常）",
    )
    rep.check(ctx.has("not_registered") is False, "has() 对未注册名字返回 False")

    # 未注册服务：use() 抛 ServiceNotFound
    raised: Optional[BaseException] = None
    try:
        ctx.use("no_such_service")
    except ServiceNotFound as exc:
        raised = exc
    rep.check(isinstance(raised, ServiceNotFound), "use() 未注册服务抛 ServiceNotFound")
    rep.check(isinstance(raised, ServiceError), "ServiceNotFound 是 ServiceError 子类（可统一兜底）")
    rep.check(getattr(raised, "name", None) == "no_such_service", "异常携带缺失的服务名")
    rep.check("no_such_service" in str(raised), "异常消息包含服务名（日志可读）")

    # 空服务名直接拒绝
    empty_raised: Optional[BaseException] = None
    try:
        ctx.provide("", object())
    except ValueError as exc:
        empty_raised = exc
    rep.check(isinstance(empty_raised, ValueError), "provide 空服务名抛 ValueError")

    # fork：子上下文注册对父不可见，但可读父级服务
    child: Context = ctx.fork()
    child.provide("child_only", 42)
    rep.check(child.depth == 1 and ctx.depth == 0, "fork() 子上下文深度为 1，父仍为 0")
    rep.check(child.has("child_only") is True, "子上下文能取到自己注册的服务")
    rep.check(
        ctx.has("child_only") is False,
        "fork 里 provide 不影响父上下文（子注册对父不可见）",
    )
    rep.check(
        child.use("greeter") is greeter,
        "子上下文可读父级服务（use 沿父链查找）",
    )
    rep.check(child.use_or("child_only") == 42, "子上下文 use_or 命中自身注册")
    rep.check(child.disposed is False and ctx.disposed is False, "fork 后父子上下文都还可用")

    child.dispose()
    rep.check(child.disposed is True, "子上下文 dispose() 后 disposed 为 True")
    rep.check(ctx.has("greeter") is True, "child.dispose() 只清理自己的注册，父上下文不受影响")
    rep.check(ctx.disposed is False, "child.dispose() 不会连带清理父上下文")

    # provide 返回的 disposer：可撤销，且幂等
    detach()
    rep.check(ctx.has("greeter") is False, "provide 返回的 disposer 撤销了注册")
    detach()
    rep.check(ctx.has("greeter") is False, "disposer 幂等（重复调用不报错、不误删）")

    # 覆盖保护：旧 disposer 不会误删后来的覆盖者
    first_impl = object()
    second_impl = object()
    detach_first: Callable[[], None] = ctx.provide("slot", first_impl)
    ctx.provide("slot", second_impl)
    detach_first()
    rep.check(ctx.use("slot") is second_impl, "旧实现的 disposer 不会误删覆盖后的新实现")

    ctx.dispose()
    rep.check(ctx.disposed is True, "dispose() 后 disposed 为 True")
    rep.check(ctx.has("slot") is False, "dispose() 后服务表被清空（能力全部下线）")
    ctx.dispose()  # 幂等
    rep.check(ctx.disposed is True, "dispose() 幂等：重复调用无副作用")


# ══════════════════════════════════════════════════════════════
#  第 3 节：Service 生命周期
# ══════════════════════════════════════════════════════════════

def demo_service_lifecycle(rep: Report) -> None:
    """Service 基类约束、capability_name 推导、start()/dispose() 与"注册返回 disposer"的热插拔"""
    rep.section("[3/5] Service 生命周期：Service 子类 + start()/dispose() + disposer 热插拔")

    # Service 是接口基类，不能直接实例化
    base_raised: Optional[BaseException] = None
    try:
        Service()  # type: ignore[abstract]
    except TypeError as exc:
        base_raised = exc
    rep.check(isinstance(base_raised, TypeError), "直接实例化 Service 基类抛 TypeError")

    class DemoEchoService(Service):
        """演示用能力实现：start() 预热、dispose() 释放（满足 Disposable 协议）"""

        def __init__(self) -> None:
            self.started: int = 0
            self.disposed: int = 0

        def start(self) -> None:
            """启动：真实 Provider 在这里预热资源"""
            self.started += 1

        def dispose(self) -> None:
            """释放资源：真实 Provider 在这里关闭句柄/线程"""
            self.disposed += 1

    class NamedService(Service):
        """显式声明 capability_name 的子类"""

        capability_name = "custom_capability"

    rep.check(
        DemoEchoService.capability_name == "demo_echo_service",
        "capability_name 未声明时从类名自动推导（DemoEchoService -> demo_echo_service）",
    )
    rep.check(
        NamedService.capability_name == "custom_capability",
        "显式声明的 capability_name 不被覆盖",
    )

    ctx = Context()
    svc = DemoEchoService()
    svc.start()
    rep.check(svc.started == 1, "start() 被调用一次（生命周期起点）")
    rep.check(isinstance(svc, Service), "实现类是 Service 子类")
    rep.check(isinstance(svc, Disposable), "实现类满足 Disposable 协议（isinstance 检查通过）")
    rep.check(isinstance(svc, DemoEchoService), "多态：可按实现类型判断")

    detach_old: Callable[[], None] = ctx.provide("echo", svc)
    rep.check(ctx.use("echo") is svc, "能力按名注册后可取回（Seam 的 Provider/Consumer 连接点）")

    # 热插拔：同名 provide 覆盖 = 换 Provider，Consumer 代码一行不改
    class QuietEchoService(Service):
        """替换实现（不做任何预热）"""

        def __init__(self) -> None:
            self.running: bool = True

    replacement = QuietEchoService()
    detach_new: Callable[[], None] = ctx.provide("echo", replacement)
    rep.check(ctx.use("echo") is replacement, "同名 provide 覆盖实现（换 Provider 不改 Consumer）")

    detach_new()
    rep.check(ctx.has("echo") is False, "撤销新实现的注册后服务不可用（disposer 即热插拔开关）")
    detach_old()
    rep.check(
        ctx.has("echo") is False,
        "旧实现的 disposer 不会让被覆盖的旧实现复活",
    )

    svc.dispose()
    rep.check(svc.disposed == 1, "dispose() 释放资源被调用一次")

    # 观察项：Context.provide() 的 disposer 只做"从服务表摘除"，不会调用实现对象的 dispose()
    probe = DemoEchoService()
    probe_ctx = Context()
    probe_ctx.provide("probe", probe)
    probe_ctx.dispose()
    rep.note(
        f"Context.dispose() 之后 probe.disposed = {probe.disposed}：provide() 的 disposer 只把服务从表里摘除，"
        "不会调用实现对象的 dispose()。core/kernel/service.py 中 Disposable 的 docstring 声称"
        "「context 清理时会自动调用其 dispose()」，与实现不一致 —— 资源释放得由插件自己挂到上下文"
        "（例如插件入口返回 disposer，由 PluginLoader 收编，见第 5 节）。此处只报告，未改动内核。"
    )

    ctx.dispose()
    rep.check(ctx.disposed is True, "第 3 节的上下文已收尾")


# ══════════════════════════════════════════════════════════════
#  第 4 节：注册表
# ══════════════════════════════════════════════════════════════

def demo_registry(rep: Report) -> None:
    """Registry：注册/推导注册名、查找、快照、过滤、注销、覆盖保护、经 Context 取用"""
    rep.section("[4/5] 注册表 Registry：注册 / 查找 / 快照 / 过滤 / 注销 / 覆盖保护")

    class DemoTool:
        """演示对象：Registry 从 name 属性推导注册名"""

        def __init__(self, name: str, risk: str = "low") -> None:
            self.name: str = name
            self.risk: str = risk

    reg: Registry[DemoTool] = Registry(kind="工具")
    rep.check(len(reg) == 0 and reg.names() == [], "新建注册表为空")

    detach_search: Callable[[], None] = reg.register(DemoTool("file_search"))
    rep.check(reg.names() == ["file_search"], "register() 从 item.name 推导注册名")
    rep.check(reg.has("file_search") and "file_search" in reg, "has() / __contains__ 都能查注册名")
    found = reg.get("file_search")
    rep.check(
        found is not None and found.name == "file_search",
        "get() 按名取回对象",
    )
    rep.check(reg.get("nope") is None, "get() 未命中返回 None（不抛异常）")

    reg.register(DemoTool("file_delete", risk="high"))
    reg.register(DemoTool("anything"), name="custom_name")
    rep.check("custom_name" in reg.names(), "register(name=...) 允许显式指定注册名")
    rep.check(len(reg) == 3, "len() 反映注册项数量（3 项）")

    filtered = reg.filter(lambda tool: tool.risk == "high")
    rep.check(
        len(filtered) == 1 and filtered[0].name == "file_delete",
        "filter() 按谓词过滤（tool_registry 按风险筛选的现实用法）",
    )
    rep.check(
        reg.find_first(lambda tool: tool.name.endswith("delete")) is filtered[0],
        "find_first() 返回首个匹配对象",
    )

    snapshot = reg.names()
    snapshot.append("外部篡改")
    rep.check(
        reg.names() == ["file_search", "file_delete", "custom_name"],
        "names() 返回快照，外部修改影响不到注册表",
    )

    # 注意区分「注册名」与「对象自身的 name」：custom_name 这一项的注册名是
    # custom_name，但它对象的 name 属性是 anything。
    iterated: List[str] = []
    for tool in reg:
        iterated.append(tool.name)
        if tool.name == "file_search":
            reg.register(DemoTool("added_during_iteration"))
    rep.check(
        iterated == ["file_search", "file_delete", "anything"],
        "__iter__ 基于快照，遍历过程中注册新项不会破坏迭代",
    )
    rep.check(len(reg) == 4, "遍历中新增的项确实进了表（证明上面的迭代不是空转）")
    reg.unregister("added_during_iteration")
    rep.check(len(reg) == 3, "循环里临时注册的项已清理（回到 3 项）")

    rep.check(reg.unregister("file_search") is True, "unregister() 移除已注册项返回 True")
    rep.check(reg.unregister("file_search") is False, "unregister() 重复移除返回 False")
    detach_search()
    rep.check(len(reg) == 2, "已被 unregister 的项，其 disposer 调用是空操作（计数不再变化）")

    # 覆盖保护：旧 disposer 不会误删后来的覆盖者
    detach_old = reg.register(DemoTool("slot_v1"), name="slot")
    new_tool = DemoTool("slot_v2")
    reg.register(new_tool, name="slot")
    detach_old()
    rep.check(reg.get("slot") is new_tool, "旧 disposer 不会误删覆盖后的新对象")

    derived_raised: Optional[BaseException] = None
    try:
        reg.register(object())  # 既无 name 属性，也没显式指定
    except ValueError as exc:
        derived_raised = exc
    rep.check(isinstance(derived_raised, ValueError), "无法推导注册名时抛 ValueError")

    reg.clear()
    rep.check(len(reg) == 0, "clear() 清空注册表")

    # 经 Context 取用的注册表
    ctx = Context()
    tool_reg: Registry[DemoTool] = ctx.registry("tool")
    rep.check(ctx.registry("tool") is tool_reg, "ctx.registry(kind) 同类型返回同一实例")
    rep.check(
        ctx.has_registry("tool") and ctx.has_registry("provider") is False,
        "has_registry() 如实反映注册表是否已创建",
    )
    tool_reg.register(DemoTool("t1"))
    ctx.dispose()
    fresh = ctx.registry("tool")
    rep.note(
        f"ctx.dispose() 后 ctx.registry('tool') 返回的是新实例（同一实例={fresh is tool_reg}，"
        f"旧表仍有 {len(tool_reg)} 项）：dispose() 只是丢弃了注册表引用，不清空表内已注册项。"
        "此处只报告，未改动内核。"
    )
    rep.check(ctx.disposed is True, "第 4 节的上下文已收尾")


# ══════════════════════════════════════════════════════════════
#  第 5 节：插件加载器
# ══════════════════════════════════════════════════════════════

#: 假插件 A：被依赖方（在临时目录里生成）
_BASE_MODULE: str = "_kd_demo_base_plugin"
_APP_MODULE: str = "_kd_demo_app_plugin"
_BROKEN_MODULE: str = "_kd_demo_broken_plugin"

_BASE_PLUGIN_SRC: str = '''
"""演示用假插件 A（被依赖方）：注册 base_service"""

from typing import Any, Callable, Optional


def _append(path: str, line: str) -> None:
    """把一行写进加载日志"""
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(line + "\\n")


def setup(ctx: Any, *, log_path: str = "", **config: Any) -> Optional[Callable[[], None]]:
    """入口：注册能力 + 记录加载顺序，并返回自己的 disposer"""
    ctx.provide("base_service", {"name": "base", "ready": True})
    _append(log_path, "base_plugin.load")
    print("       - 假插件 base_plugin 已加载：注册 base_service")

    def dispose() -> None:
        """释放本插件占用的资源（由 PluginLoader 在 unload 时调用）"""
        _append(log_path, "base_plugin.dispose")
        print("       - 假插件 base_plugin 的入口 disposer 被调用")

    return dispose
'''

_APP_PLUGIN_SRC: str = '''
"""演示用假插件 B（依赖 A）：依赖 base_service，顺序错了就会直接失败"""

from typing import Any, Callable, Optional


def _append(path: str, line: str) -> None:
    """把一行写进加载日志"""
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(line + "\\n")


def setup(ctx: Any, *, log_path: str = "", **config: Any) -> Optional[Callable[[], None]]:
    """入口：先确认依赖已就绪，再注册 app_service"""
    if not ctx.has("base_service"):
        raise RuntimeError("拓扑顺序错误：base_service 尚未注册，app_plugin 却被先加载了")
    ctx.provide("app_service", {"name": "app", "dep": ctx.use("base_service")["name"]})
    _append(log_path, "app_plugin.load")
    print("       - 假插件 app_plugin 已加载：成功读到 base_service")
    return None
'''

_BROKEN_PLUGIN_SRC: str = '''
"""演示用假插件 C：入口故意抛异常，用来验证"失败不留半成品" """

from typing import Any


def setup(ctx: Any, **config: Any) -> None:
    """入口：先注册一个服务，再抛异常"""
    ctx.provide("half_baked_service", "半成品")
    raise RuntimeError("故意的入口异常（演示加载失败回滚）")
'''


def demo_plugin_loader(rep: Report) -> None:
    """PluginLoader：配置解析、Kahn 拓扑排序、加载两位假插件、卸载撤销、失败回滚"""
    rep.section("[5/5] 插件加载器 PluginLoader：配置解析 / 拓扑排序 / 加载 / 卸载 / 失败回滚")

    with tempfile.TemporaryDirectory(prefix="kernel_demo_plugins_") as tmp:
        tmp_dir = Path(tmp)
        log_path = tmp_dir / "load_order.txt"
        # 一次性写全三个假插件（避免"导入扫描后新增文件"带来的缓存干扰）
        (tmp_dir / f"{_BASE_MODULE}.py").write_text(_BASE_PLUGIN_SRC, encoding="utf-8")
        (tmp_dir / f"{_APP_MODULE}.py").write_text(_APP_PLUGIN_SRC, encoding="utf-8")
        (tmp_dir / f"{_BROKEN_MODULE}.py").write_text(_BROKEN_PLUGIN_SRC, encoding="utf-8")
        rep.note(f"假插件写在临时目录（运行结束自动删除，不进仓库）：{tmp_dir}")

        sys.path.insert(0, str(tmp))
        ctx = Context()
        try:
            loader = PluginLoader()

            # 配置里故意把"依赖者"排在"被依赖者"前面，验证 loader 会自己拓扑排序
            config: Dict[str, Any] = {
                "agent": {
                    "plugins": [
                        {
                            "id": "app_plugin",
                            "module": _APP_MODULE,
                            "depends_on": ["base_plugin"],
                            "config": {"log_path": str(log_path)},
                        },
                        {
                            "id": "base_plugin",
                            "module": _BASE_MODULE,
                            "config": {"log_path": str(log_path)},
                        },
                    ]
                }
            }

            specs = loader.parse_config(config)
            rep.check(
                [spec.id for spec in specs] == ["app_plugin", "base_plugin"],
                "parse_config() 保持配置书写顺序（app 在前）",
            )

            ordered = loader.resolve_order(specs)
            rep.check(
                [spec.id for spec in ordered] == ["base_plugin", "app_plugin"],
                "resolve_order() 拓扑排序把被依赖的 base_plugin 排到前面",
            )

            ok, failed = loader.load_all(ordered, ctx)
            rep.check(ok == ["base_plugin", "app_plugin"], f"load_all() 成功列表 = {ok}")
            rep.check(failed == [], "无插件加载失败")
            rep.check(
                ctx.has("base_service") and ctx.has("app_service"),
                "两个假插件注册的能力在共享上下文里都可用",
            )
            rep.check(
                ctx.use("app_service")["dep"] == "base",
                "app_plugin 读到的依赖确实是 base_service（顺序正确）",
            )
            rep.check(loader.count() == 2 and loader.is_loaded("base_plugin"), "loader 记录已加载 2 个插件")
            rep.check(
                loader.loaded_ids() == ["base_plugin", "app_plugin"],
                "loaded_ids() 反映真实加载顺序",
            )

            order_lines = log_path.read_text(encoding="utf-8").split()
            rep.check(
                order_lines == ["base_plugin.load", "app_plugin.load"],
                f"假插件自报的加载顺序 = {order_lines}（被依赖者先加载）",
            )

            # 依赖缺失 / 成环
            missing_raised: Optional[BaseException] = None
            try:
                loader.resolve_order(
                    [PluginSpec(id="x", module=_APP_MODULE, depends_on=["ghost_plugin"])]
                )
            except MissingDependencyError as exc:
                missing_raised = exc
            rep.check(isinstance(missing_raised, MissingDependencyError), "依赖不存在的插件抛 MissingDependencyError")
            rep.check(
                getattr(missing_raised, "missing", None) == "ghost_plugin",
                "异常携带缺失的依赖名",
            )

            cycle_raised: Optional[BaseException] = None
            try:
                loader.resolve_order(
                    [
                        PluginSpec(id="a", module=_BASE_MODULE, depends_on=["b"]),
                        PluginSpec(id="b", module=_BASE_MODULE, depends_on=["a"]),
                    ]
                )
            except CircularDependencyError as exc:
                cycle_raised = exc
            rep.check(isinstance(cycle_raised, CircularDependencyError), "依赖成环抛出 CircularDependencyError")
            rep.check(
                getattr(cycle_raised, "cycle", [])[:1] == getattr(cycle_raised, "cycle", [])[-1:],
                "环路径首尾闭合（报错信息可读）",
            )

            # 禁用插件被跳过 / 重复 id 被拒
            rep.check(
                loader.parse_config(
                    {"agent": {"plugins": [{"id": "off", "module": _BASE_MODULE, "enabled": False}]}}
                )
                == [],
                "enabled=false 的插件在解析阶段被跳过",
            )
            dup_raised: Optional[BaseException] = None
            try:
                loader.parse_config(
                    {"agent": {"plugins": [{"id": "dup", "module": _BASE_MODULE},
                                           {"id": "dup", "module": _BASE_MODULE}]}}
                )
            except ConfigError as exc:
                dup_raised = exc
            rep.check(isinstance(dup_raised, ConfigError), "重复插件 id 抛 ConfigError")

            # 入口抛异常 → 该插件记为失败，且半成品注册被回滚
            ok_broken, failed_broken = loader.load_all(
                [PluginSpec(id="broken_plugin", module=_BROKEN_MODULE)], ctx
            )
            rep.check(
                ok_broken == [] and failed_broken == ["broken_plugin"],
                "入口抛异常的插件记为失败（不向调用方抛异常）",
            )
            rep.check(
                ctx.has("half_baked_service") is False,
                "失败插件的半成品注册已被回滚（不留脏状态）",
            )

            # 卸载：撤销该插件收编的全部注册 + 调用入口返回的 disposer
            rep.check(loader.unload("base_plugin") is True, "unload() 返回 True")
            rep.check(ctx.has("base_service") is False, "卸载后 base_service 不可用")
            rep.check(
                ctx.has("app_service") is True,
                "卸载 base 不影响 app 自己的注册（撤销按插件归属，不是一刀切）",
            )
            after_unload = log_path.read_text(encoding="utf-8").split()
            rep.check(
                after_unload[-1] == "base_plugin.dispose",
                "入口函数返回的 disposer 被 loader 收编，卸载时确实被调用",
            )
            rep.check(loader.count() == 1, "卸载后 loader 计数为 1")
            rep.check(loader.is_loaded("base_plugin") is False, "is_loaded() 反映卸载结果")
            rep.check(loader.unload("not_loaded_plugin") is True, "unload 未加载的插件返回 True（幂等）")

            rep.check(loader.unload_all() == 1, "unload_all() 卸载剩余插件并返回数量")
            rep.check(ctx.has("app_service") is False, "全部卸载后插件能力无残留")
            rep.check(loader.count() == 0, "loader 状态清空")
        finally:
            ctx.dispose()
            if str(tmp) in sys.path:
                sys.path.remove(str(tmp))
            for name in (_BASE_MODULE, _APP_MODULE, _BROKEN_MODULE):
                sys.modules.pop(name, None)


# ══════════════════════════════════════════════════════════════
#  入口
# ══════════════════════════════════════════════════════════════

def _run_section(rep: Report, title: str, func: Callable[[Report], None]) -> None:
    """执行一节演示；节内未捕获的异常也记为失败，保证脚本以退出码 1 结束而不是抛栈

    Args:
        rep: 断言记录器
        title: 节标题（用于失败明细）
        func: 该节的演示函数
    """
    try:
        func(rep)
    except Exception as exc:  # 演示脚本：任何意外都要转成可读的失败，而不是中断后续小节
        rep.fail(f"{title} 抛出异常：{type(exc).__name__}: {exc}")
        traceback.print_exc()


def main() -> int:
    """运行全部演示

    Returns:
        0=全部断言通过；1=存在失败断言
    """
    print("欣雅插件内核 core/kernel 能力演示")
    print(f"仓库根目录：{_REPO_ROOT}")
    print(f"Python：{sys.version.split()[0]}    标准库 + core/kernel，无第三方依赖")
    print("说明：每节都做真实断言，任一断言不成立则退出码为 1。")

    rep = Report()
    _run_section(rep, "[1/5] 事件总线", demo_event_bus)
    _run_section(rep, "[2/5] Context", demo_context)
    _run_section(rep, "[3/5] Service 生命周期", demo_service_lifecycle)
    _run_section(rep, "[4/5] 注册表 Registry", demo_registry)
    _run_section(rep, "[5/5] 插件加载器", demo_plugin_loader)

    rep.section("[总结] 断言统计与观察")
    print(f"  通过 {rep.passed} 条，失败 {len(rep.failures)} 条")
    if rep.notes:
        print(f"  运行中的观察项（{len(rep.notes)} 条，未断言、未改内核）：")
        for index, item in enumerate(rep.notes, start=1):
            print(f"    {index}. {item}")

    if rep.failures:
        print("  失败明细：")
        for item in rep.failures:
            print(f"    - {item}")
        print(f"kernel_demo: {rep.passed}/{rep.total} 通过，失败 {len(rep.failures)} 条")
        return 1

    print(f"kernel_demo: {rep.passed}/{rep.total} 通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
