"""路由能力接口定义（Service Definition）

DSH 能力 Seam 三角的 Definition 角色：
- Definition: 本模块（RouterService）
- Provider:   agent/providers/router/{rule,llm,hybrid}_router.py
- Consumer:   agent/pipeline.py

意图路由的职责：把自然语言文本映射为结构化的 AgentCommand。

关键设计：
- route() 必须快速返回（规则实现 <10ms）
- 低置信度由调用方（HybridRouter）决定是否降级到 LLM
- 置信度语义：>=0.5 表示"有把握直接执行"
"""

from abc import abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List

from core.kernel.service import Service


#: 低置信度阈值：低于此值应转 LLM 路由
DEFAULT_CONFIDENCE_THRESHOLD = 0.5


@dataclass(slots=True)
class IntentDef:
    """意图定义

    Attributes:
        action: 对应的工具名
        keywords: 关键词及权重 [(词, 权重), ...]
        base_confidence: 命中后的基础置信度
        description: 意图描述（供 LLM 路由复用）
        category: 确认语分类（search/read/write/delete/system/default）
    """

    action: str
    keywords: List[tuple] = field(default_factory=list)
    base_confidence: float = 0.6
    description: str = ""
    category: str = "default"

    def keyword_weight(self, text: str) -> float:
        """计算该意图在文本中的关键词得分"""
        score = 0.0
        for word, weight in self.keywords:
            if word in text:
                score += weight
        return score


class RouterService(Service):
    """路由能力接口

    实现者必须保证：
    - route() 是纯函数（同样输入产生同样输出，不依赖外部状态）
    - 不应抛异常；无法识别时返回 action="chat" 的低置信度命令
    """

    capability_name = "router"

    @abstractmethod
    def route(self, text: str, context: Dict[str, Any] = None) -> "Any":
        """把文本路由为 AgentCommand

        Args:
            text: 用户输入文本
            context: 上下文（对话历史、实体栈等），可为 None

        Returns:
            AgentCommand；无法识别时 action="chat"、confidence 很低
        """

    @abstractmethod
    def supported_actions(self) -> List[str]:
        """返回本路由支持的 action 列表"""

    @abstractmethod
    def describe_intents(self) -> List[Dict[str, Any]]:
        """导出意图描述（供 LLM 路由复用，避免两处维护词表）"""
