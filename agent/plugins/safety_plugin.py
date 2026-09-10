"""safety 插件：安全守卫

Provider 由配置 `agent.safety.provider` 选择（当前只有 basic）。
白名单留空时交给 `BasicGuard` 用默认四目录（桌面/文档/下载/图片）。
"""

import logging
from typing import Callable, Optional

from agent.plugins import SVC_CONFIG, SVC_SAFETY
from agent.providers.safety.basic_guard import BasicGuard

logger = logging.getLogger(__name__)


def setup(ctx, **config) -> Optional[Callable[[], None]]:
    """构造并注册安全守卫"""
    cfg = ctx.use(SVC_CONFIG)
    provider = (config.get("provider") or getattr(cfg, "safety_provider", "basic") or "basic").lower()

    if provider != "basic":
        logger.warning("[safety_plugin] 未知 provider '%s'，回退 basic", provider)

    safety = BasicGuard(
        whitelist=cfg.path_whitelist or None,
        audit_db=cfg.audit_db,
        audit_enabled=cfg.audit_enabled,
        remember_choices=cfg.remember_choices,
        confirm_timeout=cfg.confirm_timeout,
    )
    ctx.provide(SVC_SAFETY, safety)
    logger.info("[safety_plugin] 白名单: %s", [str(p) for p in safety.whitelist_roots()])
    return safety.close
