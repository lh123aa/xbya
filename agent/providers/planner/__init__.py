"""规划能力 Provider 包（P3 / D5）

实现三档，由 `config.yaml` 的 `agent.planner.provider` 选择：

| provider | 模块 | 特点 |
|----------|------|------|
| `template` | `template_planner.py` | 规则配方，<1ms，结果确定，零成本 |
| `llm` | `llm_planner.py` | LLM 分解，1~3s，能处理需要语义理解的说法 |
| `hybrid`（默认） | `hybrid_planner.py` | 规则优先，未命中且确实像多步才转 LLM |

换实现 = 改配置一行，管线代码不动（设计原则 7）。
"""

from agent.providers.planner.hybrid_planner import HybridPlanner
from agent.providers.planner.llm_planner import LLMPlanner
from agent.providers.planner.template_planner import (
    DEFAULT_RECIPES,
    Recipe,
    TemplatePlanner,
)

__all__ = [
    "DEFAULT_RECIPES",
    "HybridPlanner",
    "LLMPlanner",
    "Recipe",
    "TemplatePlanner",
]
