"""插件内核

DSH Cordis 架构的 Python 简体版，提供四个核心抽象：

- Context     插件上下文（能力注册/作用域隔离/事件代理/清理）
- Service     能力接口基类（Seam 的 Definition 角色）
- Registry    通用对象注册表（注册即副作用）
- EventBus    类型化事件总线（handler 异常隔离）

用法：
    from core.kernel import Context, Service, Registry, EventBus, EventTypes

    ctx = Context()
    ctx.provide("my_service", MyService())
    svc = ctx.use("my_service")
    ctx.dispose()
"""

from core.kernel.context import Context
from core.kernel.events import Event, EventBus, EventTypes
from core.kernel.registry import Registry
from core.kernel.service import (
    CircularDependencyError,
    ConfigError,
    Disposable,
    MissingDependencyError,
    Service,
    ServiceError,
    ServiceNotFound,
)

__all__ = [
    # 核心抽象
    "Context",
    "Service",
    "Registry",
    "EventBus",
    "Event",
    "EventTypes",
    # 协议与异常
    "Disposable",
    "ServiceError",
    "ServiceNotFound",
    "ConfigError",
    "CircularDependencyError",
    "MissingDependencyError",
]
