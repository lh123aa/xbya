"""summarizer 插件：结果摘要

Provider 由 `agent.summarizer.provider` 选择：

- `template` ：只用模板（<1ms，零成本）
- `llm`      ：优先 LLM（失败回退模板）
- `hybrid`   ：简单结果模板 + 复杂结果 LLM（默认）

`async_refine` 由管线消费（先播模板、润色后补播），不属于摘要器自身配置。
"""

import logging
from typing import Callable, Optional

from agent.plugins import SVC_CONFIG, SVC_LLM_ONCE, SVC_SUMMARIZER
from agent.providers.summarizer.hybrid_sum import HybridSummarizer
from agent.providers.summarizer.llm_sum import LLMSummarizer
from agent.providers.summarizer.template_sum import TemplateSummarizer

logger = logging.getLogger(__name__)


def build(config, llm_once=None):
    """按配置构造摘要器（供插件与测试共用）"""
    provider = (config.summarizer_provider or "hybrid").lower()
    template = TemplateSummarizer()

    if provider == "template":
        return template

    llm = LLMSummarizer(llm_call=llm_once, fallback=template)

    if provider == "llm":
        return llm

    if provider == "hybrid":
        return HybridSummarizer(
            template=template,
            llm=llm,
            threshold=config.summarizer_threshold,
        )

    logger.warning("[summarizer_plugin] provider %r 未实现，降级为 template", provider)
    return template


def setup(ctx, **config) -> Optional[Callable[[], None]]:
    """构造并注册摘要器"""
    cfg = ctx.use(SVC_CONFIG)
    summarizer = build(cfg, ctx.use_or(SVC_LLM_ONCE))
    ctx.provide(SVC_SUMMARIZER, summarizer)
    logger.info("[summarizer_plugin] 就绪: %s", type(summarizer).__name__)
    return None
