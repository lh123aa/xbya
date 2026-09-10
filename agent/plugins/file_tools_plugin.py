"""file_tools 插件：6 个文件工具（搜索/列出/读取/重命名/移动/删除）

开关：`agent.tools.file`（在清单里删掉本插件同样有效）。
"""

import logging
from typing import Callable, Optional

from agent.plugins import SVC_CONFIG, SVC_SAFETY, register_tools
from agent.tools.file_tools import all_file_tools

logger = logging.getLogger(__name__)


def setup(ctx, **config) -> Optional[Callable[[], None]]:
    cfg = ctx.use(SVC_CONFIG)
    if not cfg.tools_file:
        logger.info("[file_tools_plugin] 已按配置关闭")
        return None

    tools = all_file_tools(ctx.use(SVC_SAFETY))
    logger.info("[file_tools_plugin] 注册 %d 个工具", len(tools))
    return register_tools(ctx, tools)
