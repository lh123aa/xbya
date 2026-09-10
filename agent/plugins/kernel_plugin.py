"""kernel 插件：工具注册表

最小插件，只提供 `tool_registry` 服务。
工具集插件（file/system/productivity/browser）都依赖它往同一个注册表里塞工具。
"""

from typing import Optional, Callable

from agent.plugins import SVC_REGISTRY
from agent.tools.registry import ToolRegistry


def setup(ctx, **config) -> Optional[Callable[[], None]]:
    """提供工具注册表"""
    registry = ToolRegistry()
    return ctx.provide(SVC_REGISTRY, registry)
