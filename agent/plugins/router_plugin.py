"""router 插件：意图路由

Provider 由 `agent.router.provider` 选择：

- `rule`   ：只用规则（最快，零成本）
- `llm`    ：优先 LLM（用阈值 1.0 让规则永不满足，必走 LLM）
- `hybrid` ：规则优先 + LLM 兜底（默认）

未知取值降级为 `rule` 并告警 —— 配置写错不会让装配失败。
"""

import logging
from typing import Callable, Optional

from agent.plugins import SVC_CONFIG, SVC_LLM_ROUTE, SVC_ROUTER
from agent.providers.router.hybrid_router import HybridRouter
from agent.providers.router.llm_router import LLMRouter
from agent.providers.router.rule_router import RuleRouter

logger = logging.getLogger(__name__)


def build(config, llm_route_call=None):
    """按配置构造路由（供插件与测试共用）"""
    provider = (config.router_provider or "hybrid").lower()
    rule = RuleRouter(confidence_threshold=config.confidence_threshold)

    if provider == "rule":
        return rule

    if provider == "llm":
        llm_only = LLMRouter(llm_call=llm_route_call, timeout=config.llm_router_timeout)
        return HybridRouter(
            rule=rule,
            llm=llm_only,
            threshold=1.0,
            llm_enabled=config.llm_router_enabled,
        )

    if provider == "hybrid":
        llm = LLMRouter(llm_call=llm_route_call, timeout=config.llm_router_timeout)
        return HybridRouter(
            rule=rule,
            llm=llm,
            threshold=config.confidence_threshold,
            llm_enabled=config.llm_router_enabled,
        )

    logger.warning("[router_plugin] provider %r 未实现，降级为 rule", provider)
    return rule


def setup(ctx, **config) -> Optional[Callable[[], None]]:
    """构造并注册路由器"""
    cfg = ctx.use(SVC_CONFIG)
    router = build(cfg, ctx.use_or(SVC_LLM_ROUTE))
    ctx.provide(SVC_ROUTER, router)
    logger.info("[router_plugin] 就绪: %s", type(router).__name__)
    return None
