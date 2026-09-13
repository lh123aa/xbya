"""混合路由（Service Provider，默认实现）

三级降级链（对应 AGENTS.md 决策「本地规则优先 — 85% 请求走规则，15% 走 LLM 兜底」）：

    rule（<10ms）
      ├─ 置信度 >= 阈值 → 直接返回              ← 85% 走这里
      └─ 置信度 <  阈值 → llm（1~3s）
                          ├─ 成功 → 返回
                          └─ 失败/不可用 → chat  ← 最后兜底，走原 LLM 闲聊

延迟加权：0.85×10ms + 0.15×2000ms ≈ 308ms
"""

import logging
from typing import Any, Dict, List, Optional

from agent.message import AgentCommand
from agent.seams.router import DEFAULT_CONFIDENCE_THRESHOLD, RouterService

logger = logging.getLogger(__name__)


class HybridRouter(RouterService):
    """规则优先 + LLM 兜底

    用法：
        router = HybridRouter(
            rule=RuleRouter(),
            llm=LLMRouter(llm_call=ai.route_with_tools),   # 可为 None
            threshold=0.5,
        )
    """

    capability_name = "router"
    provider_name = "hybrid"

    def __init__(
        self,
        rule: RouterService,
        llm: Optional[RouterService] = None,
        threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
        llm_enabled: bool = True,
    ) -> None:
        """
        Args:
            rule: 规则路由（必备）
            llm: LLM 路由；None 时全部由规则决定
            threshold: 置信度阈值，低于此值才转 LLM
            llm_enabled: 是否允许调用 LLM（对应配置 agent.router.llm.enabled）
        """
        self._rule = rule
        self._llm = llm
        self._threshold = max(0.0, min(1.0, threshold))
        self._llm_enabled = llm_enabled
        self._rule_hits = 0
        self._llm_hits = 0
        self._fallback_hits = 0

    # ── 主入口 ──

    def route(self, text: str, context: Dict[str, Any] = None, **kwargs) -> AgentCommand:
        context = context or {}
        # RuleRouter 需要 has_pending_confirm；兼容调用方传入位置参数
        if "has_pending_confirm" not in context and kwargs:
            context.update(kwargs)

        rule_cmd = self._route_rule(text, context)

        if rule_cmd.confidence >= self._threshold:
            self._rule_hits += 1
            logger.debug("[hybrid] 规则命中 (%.2f): %s", rule_cmd.confidence, rule_cmd.action)
            return rule_cmd

        # 规则没把握，但需要 LLM 才能救
        if not self._can_use_llm():
            self._fallback_hits += 1
            # ⚠️ **低置信的"动作类"意图不能直接执行**（根因修复）。
            #
            # 原实现这里无条件 `return rule_cmd` —— 于是 LLM 不可用时，
            # 一个 0.33 置信度的猜测会被**当成指令执行**：
            #     今天有什么好吃的 -> file_list(dirs=[...])  conf=0.33
            # 用户明明在闲聊，却收到"哎呀，刚才那个「Desktop」我没太听明白呢"。
            #
            # 判据：`chat` 是**安全兜底**（不产生副作用），
            # 而文件/系统类动作**有副作用**。没把握时宁可当闲聊，
            # 也不能拿用户的文件去赌一个猜测。
            # 反过来，若规则判出来的本来就是 chat，保持原样即可。
            if rule_cmd.action != "chat":
                logger.info(
                    "[hybrid] 规则低置信 %.2f 且 LLM 不可用 → 放弃动作 %s，转闲聊"
                    "（低置信的动作类意图不执行，避免误操作）",
                    rule_cmd.confidence, rule_cmd.action,
                )
                rule_cmd.action = "chat"
                rule_cmd.params = {}
                rule_cmd.source = "hybrid.low_conf_chat"
            else:
                logger.debug("[hybrid] 规则低置信 %.2f，LLM 不可用 → 保持闲聊",
                             rule_cmd.confidence)
            return rule_cmd

        llm_cmd = self._route_llm(text, context)
        if llm_cmd is not None and llm_cmd.action != "chat":
            self._llm_hits += 1
            logger.info("[hybrid] LLM 兜底成功: %s", llm_cmd.action)
            return llm_cmd

        # LLM 也判不出来 → 闲聊
        self._fallback_hits += 1
        logger.debug("[hybrid] LLM 未能识别，转闲聊")
        rule_cmd.action = "chat"
        rule_cmd.confidence = max(rule_cmd.confidence, 0.3)
        rule_cmd.source = "hybrid.fallback"
        return rule_cmd

    def _route_rule(self, text: str, context: Dict[str, Any]) -> AgentCommand:
        """调用规则路由（异常隔离）"""
        try:
            return self._rule.route(text, context)
        except Exception as e:
            logger.error("[hybrid] 规则路由异常: %s", e, exc_info=True)
            return AgentCommand(action="chat", raw_text=text or "", confidence=0.0)

    def _route_llm(self, text: str, context: Dict[str, Any]) -> Optional[AgentCommand]:
        """调用 LLM 路由（异常隔离，失败返回 None）"""
        try:
            return self._llm.route(text, context)
        except Exception as e:
            logger.warning("[hybrid] LLM 路由失败: %s", e)
            return None

    def _can_use_llm(self) -> bool:
        """LLM 路由是否可用"""
        if not self._llm_enabled or self._llm is None:
            return False
        # LLMRouter 暴露 available；其他实现视为可用
        available = getattr(self._llm, "available", True)
        return bool(available)

    # ── 接口实现 ──

    def supported_actions(self) -> List[str]:
        actions = set(self._rule.supported_actions())
        if self._llm is not None:
            actions |= set(self._llm.supported_actions())
        return sorted(actions)

    def describe_intents(self) -> List[Dict[str, Any]]:
        """合并两级的意图描述"""
        descs = list(self._rule.describe_intents())
        if self._llm is not None:
            known = {d["action"] for d in descs}
            descs += [d for d in self._llm.describe_intents() if d["action"] not in known]
        return descs

    # ── 状态 ──

    def set_llm_enabled(self, enabled: bool) -> None:
        """开关 LLM 兜底（对应配置热更新）"""
        self._llm_enabled = bool(enabled)
        logger.info("[hybrid] LLM 兜底 → %s", self._llm_enabled)

    def stats(self) -> Dict[str, Any]:
        """分流统计（用于验收：确认 LLM 调用比例符合预期）"""
        total = self._rule_hits + self._llm_hits + self._fallback_hits
        return {
            "rule_hits": self._rule_hits,
            "llm_hits": self._llm_hits,
            "fallback_hits": self._fallback_hits,
            "llm_ratio": round(self._llm_hits / total, 3) if total else 0.0,
            "threshold": self._threshold,
            "llm_enabled": self._llm_enabled,
        }

    def __repr__(self) -> str:
        return (
            f"<HybridRouter threshold={self._threshold} "
            f"llm={'on' if self._can_use_llm() else 'off'}>"
        )
