"""能力（Service）基类与异常定义

DSH 能力 Seam 三角的 Python 实现：
- Service Definition（本模块 + 各 seams/*.py）= 接口声明
- Service Provider（各 providers/）= 具体实现
- Consumer（各 tools/）= 使用该能力的工具

设计约定：
- Service 子类只声明方法签名，不含实现
- 换实现 = 换 Provider 注册，不改 Consumer
"""

import re
from abc import ABC
from typing import Any, ClassVar, Protocol, runtime_checkable


class ServiceError(Exception):
    """服务相关错误基类"""


class ServiceNotFound(ServiceError):
    """请求的服务未注册

    Attributes:
        name: 未找到的服务名
    """

    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(f"服务未注册: {name}")


class ConfigError(ServiceError):
    """插件配置错误（缺字段/格式非法）"""


class CircularDependencyError(ServiceError):
    """插件依赖存在环

    Attributes:
        cycle: 环路径，如 ["a", "b", "a"]
    """

    def __init__(self, cycle: list) -> None:
        self.cycle = cycle
        super().__init__(f"检测到循环依赖: {' → '.join(cycle)}")


class MissingDependencyError(ServiceError):
    """依赖的插件不存在

    Attributes:
        plugin_id: 声明依赖的插件
        missing: 缺失的依赖项
    """

    def __init__(self, plugin_id: str, missing: str) -> None:
        self.plugin_id = plugin_id
        self.missing = missing
        super().__init__(f"插件 '{plugin_id}' 依赖的 '{missing}' 不存在")


@runtime_checkable
class Disposable(Protocol):
    """可清理对象协议

    ⚠️ **`ctx.provide()` 不会自动调用 `dispose()`**（实测：
    `provide` 后 `disposed == 0`，调用 provide 返回的 disposer 后仍是 0，
    `Context.dispose()` 后也是 0）。原先这里写的是"context 清理时会自动调用
    其 dispose()"，与实现不符 —— 照那句话写 Provider 的插件会**漏释放资源**。

    真正的两条释放路径：

    1. `ctx.provide()` 返回的 disposer 只做一件事：**把服务从上下文摘除**
       （让 `has()`/`use()` 立刻看不到它）。想同时释放资源，得自己在返回前包一层。
    2. **插件入口返回的 `dispose()` 会被 `PluginLoader` 收编**，
       在 `unload()` / 卸载时调用 —— 这是插件释放后台资源（线程池、连接、
       调度线程）的**正规姿势**。

    换句话说：`Disposable` 描述的是"这个对象有能力被清理"，
    而不是"注册即自动清理"。
    """

    def dispose(self) -> None:
        """释放资源"""
        ...


def _to_snake(name: str) -> str:
    """把类名转成 snake_case（RuleRouter → rule_router）"""
    s1 = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s1).lower()


class Service(ABC):
    """能力接口基类

    所有能力定义（RouterService / SafetyService / ExecutorService ...）
    都继承本类。本类不提供任何默认实现，仅作为类型标记与多态锚点。

    子类约定：
    - 所有公开方法必须有类型标注和 docstring
    - 不得在 __init__ 中做耗时操作（构造应廉价）
    - 不得持有 Qt 对象（跨线程不安全）
    - 建议显式声明 capability_name（未声明则从类名自动推导）

    Attributes:
        capability_name: 能力名称，用于日志、诊断与配置校验
    """

    #: 能力名称；子类未显式声明时从类名自动推导
    capability_name: ClassVar[str] = ""

    def __new__(cls, *args: Any, **kwargs: Any) -> "Service":
        if cls is Service:
            raise TypeError(
                "Service 是能力接口基类，不能直接实例化；请继承后实现具体能力"
            )
        return super().__new__(cls)

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if not cls.capability_name:
            cls.capability_name = _to_snake(cls.__name__)

    def __repr__(self) -> str:
        return f"<{type(self).__name__} capability={self.capability_name!r}>"
