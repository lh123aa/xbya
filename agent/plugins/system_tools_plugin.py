"""system_tools 插件：5 个系统工具（系统状态/剪贴板/打开应用/截图/执行命令）

开关：`agent.tools.system`
"""

import logging
from typing import Callable, Optional

from agent.plugins import SVC_CONFIG, SVC_SAFETY, register_tools
from agent.tools.system_tools import all_system_tools

logger = logging.getLogger(__name__)


def setup(ctx, **config) -> Optional[Callable[[], None]]:
    cfg = ctx.use(SVC_CONFIG)
    if not cfg.tools_system:
        logger.info("[system_tools_plugin] 已按配置关闭")
        return None

    tools = all_system_tools(ctx.use(SVC_SAFETY))
    logger.info("[system_tools_plugin] 注册 %d 个工具", len(tools))
    return register_tools(ctx, tools)
