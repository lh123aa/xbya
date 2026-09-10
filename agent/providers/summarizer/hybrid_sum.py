"""混合摘要器（Service Provider，默认实现）

分流策略（对应 AGENTS.md 决策「结果生成：模板 + LLM 混合」）：

    简单结果（条目少、未截断、成功）→ 模板（<1ms，零成本）
    复杂结果（条目多 / 截断 / 需解释）→ LLM 润色（1~3s，更自然）

权衡：85% 的结果是"找到 N 个文件"这种简单形态，用模板即可；
把 LLM 调用省下来，既降延迟又省配额。
"""

import logging
from typing import Optional

from agent.seams.summarizer import SummarizerService, result_items
from agent.tools.base import ToolResult

logger = logging.getLogger(__name__)

#: 条目数超过此值视为"复杂结果"，交给 LLM
DEFAULT_TEMPLATE_THRESHOLD = 3

#: 需要 LLM 解释的工具（成败原因不是一眼能看懂的）
_ALWAYS_LLM_ACTIONS = {"web_search", "web_read", "translate"}


class HybridSummarizer(SummarizerService):
    """模板 + LLM 混合摘要

    用法：
        s = HybridSummarizer(
            template=TemplateSummarizer(),
            llm=LLMSummarizer(llm_call=app.chat_once, fallback=template),
            threshold=3,
        )
    """

    capability_name = "summarizer"
    provider_name = "hybrid"

    def __init__(
        self,
        template: SummarizerService,
        llm: Optional[SummarizerService] = None,
        threshold: int = DEFAULT_TEMPLATE_THRESHOLD,
    ) -> None:
        """
        Args:
            template: 模板摘要器（必备，作为兜底）
            llm: LLM 摘要器；None 则全部走模板
            threshold: 条目数阈值，超过则用 LLM
        """
        self._template = template
        self._llm = llm
        self._threshold = max(1, threshold)
        self._template_hits = 0
        self._llm_hits = 0

    def summarize(self, action: str, result: ToolResult) -> str:
        if self._should_use_llm(action, result):
            try:
                text = self._llm.summarize(action, result)
                if text:
                    self._llm_hits += 1
                    return text
            except Exception as e:
                logger.warning("[summarizer] LLM 摘要失败，回退模板: %s", e)

        self._template_hits += 1
        try:
            return self._template.summarize(action, result)
        except Exception as e:
            logger.warning("[summarizer] 模板摘要失败: %s", e)
            return result.summary or ""

    def _should_use_llm(self, action: str, result: ToolResult) -> bool:
        """判断是否值得走 LLM"""
        if self._llm is None:
            return False
        # 失败结果需要"说清原因 + 安慰"，模板做不好
        if not result.success:
            return True
        # 结果被截断 → 需要说明"只显示了一部分"
        if result.truncated:
            return True
        # 需要解释语义的工具
        if action in _ALWAYS_LLM_ACTIONS:
            return True
        # 条目多 → 模板拼出来太啰嗦
        if len(result_items(result)) > self._threshold:
            return True
        return False

    # ── P2-2 异步摘要 ──

    def should_refine(self, action: str, result: ToolResult) -> bool:
        """复杂结果值得事后润色；简单结果直接跳过以免白花一次 LLM 调用"""
        return self._should_use_llm(action, result)

    def fast_summary(self, action: str, result: ToolResult) -> str:
        """立即播报用：只走模板，绝不阻塞在 LLM 上"""
        try:
            return self._template.summarize(action, result)
        except Exception as e:
            logger.warning("[summarizer] 模板摘要失败: %s", e)
            return result.summary or ""

    def refine(self, action: str, result: ToolResult) -> str:
        """强制走 LLM 产出润色文本（无可改善时返回空串）

        与 summarize() 的区别：summarize 会在 LLM 失败时回退模板；
        这里失败就返回空串 —— 调用方已经播过模板文案，无需再来一遍。

        注意 LLMSummarizer 内部自带 fallback，因此"LLM 失败"表现为
        "返回的文本与模板一致"，这里显式检出并归为空串。
        """
        if self._llm is None:
            return ""
        try:
            text = self._llm.summarize(action, result)
        except Exception as e:
            logger.warning("[summarizer] 异步润色失败: %s", e)
            return ""
        if not text:
            return ""

        try:
            baseline = self.fast_summary(action, result)
        except Exception:
            baseline = ""
        if baseline and text == baseline:
            return ""          # 没改善（含 LLM 失败回退模板的情形）

        self._llm_hits += 1
        return text

    def stats(self) -> dict:
        """分流统计（用于验收：确认简单结果确实走了模板）"""
        total = self._template_hits + self._llm_hits
        return {
            "template_hits": self._template_hits,
            "llm_hits": self._llm_hits,
            "llm_ratio": round(self._llm_hits / total, 3) if total else 0.0,
        }

    def __repr__(self) -> str:
        return (
            f"<HybridSummarizer threshold={self._threshold} "
            f"llm={'on' if self._llm is not None else 'off'}>"
        )
