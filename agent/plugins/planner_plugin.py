"""planner 插件：多步任务规划（P3 / D5）

按 `agent.planner.provider` 装配三档规划器之一：

| provider | 装配结果 |
|----------|---------|
| `template` | TemplatePlanner（规则配方，<1ms） |
| `llm` | LLMPlanner（LLM 分解；无 LLM 时降级为规则） |
| `hybrid`（默认） | HybridPlanner（规则优先，必要时转 LLM） |

## 关键设计：规划器**不缓存**工具表

一开始想让本插件依赖 4 个工具插件，好在 `setup()` 里拿到完整工具清单。
但那样会破坏「摘掉一组工具 = 删清单一行」—— 依赖一旦声明，
把 `system_tools` 从清单里删掉就会直接报 MissingDependencyError。

改由**每次调用**从管线注入实时工具表（`context["available_actions"]`），
插件只依赖 `kernel`。这其实比快照更正确：

- 插件按 Kahn 稳定排序加载，`planner` 只依赖 `kernel` 时会**早于**工具插件，
  快照必然是残缺的 —— 顺序耦合本身就是个坑
- 管线比插件更清楚"此刻有哪些工具"（工具集可能被热插拔改变）

于是规划器的工具白名单永远是当下的真相，而"工具表为空则放行"的宽松兜底
仍由管线在提交前用真实注册表核对。
"""

import logging
from typing import Callable, Optional

from agent.plugins import SVC_CONFIG, SVC_LLM_ONCE, SVC_PLANNER
from agent.providers.planner import HybridPlanner, LLMPlanner, TemplatePlanner
from agent.seams.planner import MAX_PLAN_STEPS

logger = logging.getLogger(__name__)


def _build_llm_planner(
    ctx,
    max_steps: int,
    recipes: list,
    llm_timeout: float,
) -> Optional[LLMPlanner]:
    """构造 LLM 规划器；无 LLM 可用时返回 None（调用方降级为规则）

    Args:
        recipes: 规则配方描述，作为提示词里的 few-shot 参考
        llm_timeout: 单次 LLM 调用等待上限（防止把调用线程冻住）
    """
    llm_once = ctx.use_or(SVC_LLM_ONCE)
    if llm_once is None:
        return None
    return LLMPlanner(
        llm_call=llm_once, recipes=recipes,
        max_steps=max_steps, llm_timeout=llm_timeout,
    )


def setup(ctx, **config) -> Optional[Callable[[], None]]:
    """装配规划器并注册为 `planner` 服务"""
    cfg = ctx.use(SVC_CONFIG)

    if not getattr(cfg, "planner_enabled", True):
        logger.info("[planner_plugin] 规划能力已关闭（agent.planner.enabled=false）")
        return None

    provider = str(getattr(cfg, "planner_provider", "hybrid") or "hybrid").lower()
    max_steps = int(getattr(cfg, "planner_max_steps", MAX_PLAN_STEPS) or MAX_PLAN_STEPS)
    llm_timeout = float(getattr(cfg, "planner_llm_timeout", 20.0) or 20.0)

    # available_actions 一律留空：工具白名单由管线每次调用注入（见模块 docstring）
    rule = TemplatePlanner(max_steps=max_steps)
    # LLM 规划器带上规则配方作参考，降低拆错概率
    llm = _build_llm_planner(ctx, max_steps, rule.describe_recipes(), llm_timeout)

    if provider == "template":
        planner = rule
    elif provider == "llm":
        if llm is None:
            logger.warning("[planner_plugin] provider=llm 但未提供 LLM，降级为规则规划")
            planner = rule
        else:
            planner = llm
    else:
        if provider != "hybrid":
            logger.warning("[planner_plugin] 未知 provider=%r，按 hybrid 处理", provider)
        planner = HybridPlanner(rule=rule, llm=llm)

    ctx.provide(SVC_PLANNER, planner)
    logger.info("[planner_plugin] 装配完成 (provider=%s → %s, max_steps=%d)",
                provider, type(planner).__name__, max_steps)
    return None            # 规划器无后台资源，无需清理
