"""混合规划器（HybridPlanner）

DSH 能力 Seam 三角的 Provider：
- Definition: agent/seams/planner.py
- Provider:   本模块（规则优先 → LLM 兜底）
- Consumer:   agent/pipeline.py

## 分工与路由层的 rule→llm 完全一致

    文本 ──▶ TemplatePlanner（<1ms，确定）
              ├── 命中 → 直接用（绝大多数多步指令）
              └── 未命中 ──▶ can_plan() 预判
                              ├── 不像多步 → 放弃（省下一次 LLM 调用）
                              └── 像多步 ──▶ LLMPlanner（1~3s）
                                              ├── 成功 → 用
                                              └── 失败 → 放弃（调用方退回单步/闲聊）

## 为什么预判不能省

规划器的调用点在管线的路由之后，而**每一句话**都会走到那里。
若不加预判，连"你好呀"都要花一次 LLM 调用 —— 与设计原则 5
「本地规则优先，85% 请求走规则」直接冲突。预判复用规则路由的多步标记词表，
保证"什么算多步"在两层里是同一个定义。
"""

import logging
from typing import Any, Dict, List, Optional

from agent.providers.planner.llm_planner import LLMPlanner
from agent.providers.planner.template_planner import TemplatePlanner
from agent.seams.planner import Plan, PlannerService

logger = logging.getLogger(__name__)


class HybridPlanner(PlannerService):
    """混合规划器：规则优先，规则拆不出来且确实像多步才转 LLM

    用法：
        planner = HybridPlanner(rule=TemplatePlanner(...), llm=LLMPlanner(...))
        plan = planner.plan("先找到合同然后再挪到归档")   # 规则命中，<1ms
    """

    capability_name = "planner"
    provider_name = "hybrid"

    def __init__(
        self,
        rule: Optional[TemplatePlanner] = None,
        llm: Optional[LLMPlanner] = None,
        enabled: bool = True,
        require_multi_step_hint: bool = True,
    ) -> None:
        """
        Args:
            rule: 规则规划器；None 表示不启用规则路径
            llm: LLM 规划器；None 表示不启用 LLM 路径
            enabled: 总开关（False 时 plan() 恒返回 None，退回单步行为）
            require_multi_step_hint: 是否要求 LLM 路径先过 `can_plan()` 预判
        """
        self._rule = rule
        self._llm = llm
        self._enabled = bool(enabled)
        self._require_hint = bool(require_multi_step_hint)

        self._stats: Dict[str, int] = {
            "by_rule": 0,        # 规则命中次数
            "by_llm": 0,         # LLM 成功次数
            "llm_attempts": 0,   # 实际发起 LLM 调用的次数
            "llm_skipped": 0,    # 被预判挡下的次数（省下的调用）
        }

    # ══════════════════════════════════════════════
    #  PlannerService
    # ══════════════════════════════════════════════

    def plan(
        self,
        text: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> Optional[Plan]:
        """规则优先，必要时转 LLM"""
        if not self._enabled:
            return None

        ctx = context or {}

        plan = self._try_rule(text, ctx)
        if plan is not None:
            self._stats["by_rule"] += 1
            return plan

        return self._try_llm(text, ctx)

    def can_plan(self, text: str) -> bool:
        """规则能拆，或 LLM 认为像多步，即返回 True"""
        if not self._enabled:
            return False
        if self._rule is not None and self._rule.can_plan(text):
            return True
        return self._llm is not None and self._llm.can_plan(text)

    def describe_recipes(self) -> List[Dict[str, Any]]:
        """转发规则规划器的配方表"""
        if self._rule is None:
            return []
        return self._rule.describe_recipes()

    # ══════════════════════════════════════════════
    #  内部
    # ══════════════════════════════════════════════

    def _try_rule(self, text: str, ctx: Dict[str, Any]) -> Optional[Plan]:
        """规则路径（异常不影响 LLM 兜底）"""
        if self._rule is None:
            return None
        try:
            return self._rule.plan(text, ctx)
        except Exception as e:
            logger.warning("[planner/hybrid] 规则规划失败: %s", e)
            return None

    def _try_llm(self, text: str, ctx: Dict[str, Any]) -> Optional[Plan]:
        """LLM 路径（先过预判）"""
        if self._llm is None:
            return None

        if self._require_hint:
            try:
                worth = self._llm.can_plan(text)
            except Exception as e:               # 预判实现异常 → 当作不值得
                logger.debug("[planner/hybrid] can_plan 失败: %s", e)
                worth = False
            if not worth:
                self._stats["llm_skipped"] += 1
                logger.debug("[planner/hybrid] 不像多步，跳过 LLM 规划: %r",
                             str(text)[:30])
                return None

        self._stats["llm_attempts"] += 1
        try:
            plan = self._llm.plan(text, ctx)
        except Exception as e:
            logger.warning("[planner/hybrid] LLM 规划失败: %s", e)
            return None

        if plan is not None:
            self._stats["by_llm"] += 1
        return plan

    # ══════════════════════════════════════════════
    #  状态
    # ══════════════════════════════════════════════

    def set_enabled(self, enabled: bool) -> None:
        """开关规划能力（配置热更新）"""
        self._enabled = bool(enabled)
        logger.info("[planner/hybrid] enabled → %s", self._enabled)

    def stats(self) -> Dict[str, Any]:
        """规划统计（供 /status 与验收证据）"""
        return {
            "enabled": self._enabled,
            "rule": self._rule is not None,
            "llm": self._llm is not None,
            **self._stats,
        }

    def reset_stats(self) -> None:
        """重置统计"""
        for key in self._stats:
            self._stats[key] = 0

    def __repr__(self) -> str:
        return (
            f"<HybridPlanner enabled={self._enabled} "
            f"rule={self._rule is not None} llm={self._llm is not None}>"
        )
