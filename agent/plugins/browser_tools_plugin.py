"""browser_tools 插件：3 个浏览器工具（打开/搜索/读正文）

搜索引擎由 `agent.tools.search_engine` 决定（bing / baidu / google ...）。
开关：`agent.tools.browser`
"""

import logging
from typing import Callable, Optional

from agent.plugins import SVC_CONFIG, register_tools
from agent.tools.browser_tools import all_browser_tools

logger = logging.getLogger(__name__)


def setup(ctx, **config) -> Optional[Callable[[], None]]:
    cfg = ctx.use(SVC_CONFIG)
    if not cfg.tools_browser:
        logger.info("[browser_tools_plugin] 已按配置关闭")
        return None

    tools = all_browser_tools(engine=config.get("engine") or cfg.search_engine)
    logger.info("[browser_tools_plugin] 注册 %d 个工具（engine=%s）",
                len(tools), config.get("engine") or cfg.search_engine)
    return register_tools(ctx, tools)
