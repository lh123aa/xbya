"""executor 插件：任务执行器（线程池）

池大小与单任务超时来自 `agent.executor.{pool_size,timeout}`。
依赖工具集插件先跑完（拓扑顺序），保证执行时注册表已装满工具。
"""

import logging
from typing import Callable, Optional

from agent.plugins import SVC_CONFIG, SVC_EXECUTOR, SVC_REGISTRY
from agent.providers.executor.thread_pool import ThreadPoolExecutorProvider

logger = logging.getLogger(__name__)


def setup(ctx, **config) -> Optional[Callable[[], None]]:
    """构造并注册执行器"""
    cfg = ctx.use(SVC_CONFIG)
    executor = ThreadPoolExecutorProvider(
        ctx.use(SVC_REGISTRY),
        pool_size=cfg.pool_size,
        timeout=cfg.task_timeout,
    )
    ctx.provide(SVC_EXECUTOR, executor)
    logger.info("[executor_plugin] 线程池就绪 (pool=%d, timeout=%.0fs)",
                cfg.pool_size, cfg.task_timeout)
    return lambda: executor.shutdown(wait=False)
