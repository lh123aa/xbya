"""插件上下文

DSH Cordis Context 的 Python 简体版，是插件系统的核心抽象。

提供的四种能力：
1. provide / use  —— 能力注册与获取（Seam 的 Provider/Consumer 连接点）
2. fork            —— 作用域隔离（子上下文注册不影响父）
3. on / emit       —— 事件代理（订阅随上下文自动清理）
4. dispose         —— 逆序清理所有注册项（幂等）

设计约束：
- 不做加锁，由调用方保证单线程使用（Qt 场景通过 Signal 中转）
- dispose 后对象不可再用（除 disposed 查询外）
- fork 深度上限 10，超过仅告警不阻断
"""

import logging
from typing import Any, Callable, Dict, List, Optional

from core.kernel.events import EventBus
from core.kernel.registry import Registry
from core.kernel.service import ServiceNotFound

logger = logging.getLogger(__name__)

# fork 深度告警阈值（防止误用导致的上下文链过深）
_MAX_FORK_DEPTH = 10


class Context:
    """插件上下文

    用法：
        ctx = Context()
        ctx.provide("tts", my_tts)              # 注册能力
        tts = ctx.use("tts")                    # 获取能力

        child = ctx.fork()                      # 派生子作用域
        child.provide("temp", obj)              # 仅子可见
        child.dispose()                         # 只清理子注册项

        dispose = ctx.on("some.event", handler) # 订阅（dispose 时自动清理）
        ctx.emit("some.event", k="v")

        ctx.dispose()                           # 清理全部
    """

    def __init__(self, parent: Optional["Context"] = None, _depth: int = 0) -> None:
        """
        Args:
            parent: 父上下文（use 未命中时向上查找）
            _depth: 内部使用，fork 深度计数
        """
        self._parent = parent
        self._depth = _depth
        self._services: Dict[str, Any] = {}
        self._registries: Dict[str, Registry] = {}
        self._disposers: List[Callable[[], None]] = []
        self._events = EventBus()
        self._disposed = False

        if _depth > _MAX_FORK_DEPTH:
            logger.warning("[context] fork 深度 %d 超过阈值 %d，可能存在设计问题", _depth, _MAX_FORK_DEPTH)

    # ══════════════════════════════════════════════
    #  能力注册与获取
    # ══════════════════════════════════════════════

    def provide(self, name: str, impl: Any) -> Callable[[], None]:
        """注册能力实现

        Args:
            name: 服务名（如 "router" / "safety"）
            impl: 实现对象

        Returns:
            注销函数（仅当该名字仍指向本实现时才移除）

        Raises:
            ValueError: 服务名为空
        """
        if not name:
            raise ValueError("服务名不能为空")

        if name in self._services:
            logger.warning("[context] 覆盖已注册服务: %s", name)

        self._services[name] = impl

        def dispose() -> None:
            if self._services.get(name) is impl:
                self._services.pop(name, None)

        self._disposers.append(dispose)
        return dispose

    def use(self, name: str) -> Any:
        """获取能力实现

        查找顺序：本地 → 父级链 → 抛异常

        Args:
            name: 服务名

        Returns:
            实现对象

        Raises:
            ServiceNotFound: 全链未找到
        """
        if name in self._services:
            return self._services[name]
        if self._parent is not None:
            return self._parent.use(name)
        raise ServiceNotFound(name)

    def has(self, name: str) -> bool:
        """检查服务是否可用（含父级链）"""
        if name in self._services:
            return True
        if self._parent is not None:
            return self._parent.has(name)
        return False

    def use_or(self, name: str, default: Any = None) -> Any:
        """获取能力，未注册时返回默认值（不抛异常）"""
        try:
            return self.use(name)
        except ServiceNotFound:
            return default

    # ══════════════════════════════════════════════
    #  作用域隔离
    # ══════════════════════════════════════════════

    def fork(self) -> "Context":
        """派生子上下文

        子上下文特性：
        - 注册的项对父不可见
        - 可读取父及祖先注册的服务
        - dispose 只清理自己的注册项
        """
        return Context(parent=self, _depth=self._depth + 1)

    # ══════════════════════════════════════════════
    #  事件
    # ══════════════════════════════════════════════

    def on(self, event_type: str, handler: Callable) -> Callable[[], None]:
        """订阅事件（订阅随 dispose 自动清理）

        Returns:
            取消订阅函数
        """
        dispose = self._events.on(event_type, handler)
        self._disposers.append(dispose)
        return dispose

    def once(self, event_type: str, handler: Callable) -> Callable[[], None]:
        """一次性订阅事件"""
        dispose = self._events.once(event_type, handler)
        self._disposers.append(dispose)
        return dispose

    def emit(self, event_type: str, source: str = "", **data: Any) -> None:
        """发射事件（handler 异常隔离）"""
        self._events.emit(event_type, source=source, **data)

    @property
    def events(self) -> EventBus:
        """底层事件总线（供跨上下文共享使用）"""
        return self._events

    # ══════════════════════════════════════════════
    #  具名注册表
    # ══════════════════════════════════════════════

    def registry(self, kind: str) -> Registry:
        """获取（或创建）具名注册表

        注册表随上下文生命周期管理，无需手动清理。

        Args:
            kind: 注册表类型标识（如 "tool" / "provider"）

        Returns:
            该类型的注册表实例
        """
        if kind not in self._registries:
            self._registries[kind] = Registry(kind=kind)
        return self._registries[kind]

    def has_registry(self, kind: str) -> bool:
        """该类型的注册表是否已创建"""
        return kind in self._registries

    # ══════════════════════════════════════════════
    #  注册标记（供插件框架实现"整包卸载"）
    # ══════════════════════════════════════════════

    def mark(self) -> int:
        """记录当前注册位置

        与 reclaim() 配合，用于取出"某段代码期间新增的注册"。

        Returns:
            标记值（当前 disposer 数量）
        """
        return len(self._disposers)

    def reclaim(self, mark: int) -> List[Callable[[], None]]:
        """取出 mark 之后新增的 disposer，并从上下文摘除

        摘除是必要的：这些注册的所有权移交给调用方（如插件加载器），
        由它决定何时执行；这样上下文的 dispose 不会重复执行它们。

        Args:
            mark: 之前 mark() 的返回值

        Returns:
            新增的 disposer 列表（按注册顺序）
        """
        if mark < 0:
            mark = 0
        if mark >= len(self._disposers):
            return []

        owned = self._disposers[mark:]
        del self._disposers[mark:]
        return owned

    # ══════════════════════════════════════════════
    #  清理
    # ══════════════════════════════════════════════

    def dispose(self) -> None:
        """逆序清理所有注册项

        - 清理顺序为注册的逆序
        - 单个 disposer 抛异常被捕获，不中断其余清理
        - 幂等：重复调用无副作用
        """
        if self._disposed:
            return
        self._disposed = True

        for dispose in reversed(self._disposers):
            try:
                dispose()
            except Exception as e:
                logger.error("[context] disposer 异常: %s", e, exc_info=True)

        self._disposers.clear()
        self._services.clear()
        self._registries.clear()
        self._events.clear()

    @property
    def disposed(self) -> bool:
        """是否已清理"""
        return self._disposed

    @property
    def depth(self) -> int:
        """fork 深度（根为 0）"""
        return self._depth

    def __repr__(self) -> str:
        state = "disposed" if self._disposed else "active"
        return (
            f"<Context depth={self._depth} {state} "
            f"services={len(self._services)} registries={len(self._registries)}>"
        )
