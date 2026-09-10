"""Agent 执行层

语音交互（Voice Layer）与任务执行（Agent Layer）的解耦实现。

核心概念：
- AgentCommand / AgentResult   两层之间的消息协议
- Pipeline                     并行管线协调器
- ToolRegistry                 工具注册表
- SafetyService                安全守卫（能力 Seam）
- RouterService                意图路由（能力 Seam）

设计原则：
- 与 Voice Layer 只通过事件总线通信
- 能力可替换（换 Provider 即换实现）
- 所有注册可逆（注册返回 disposer）
"""

from agent.message import (
    AgentCommand,
    AgentResult,
    CommandStatus,
    RiskLevel,
    new_request_id,
)

__all__ = [
    "AgentCommand",
    "AgentResult",
    "CommandStatus",
    "RiskLevel",
    "new_request_id",
]
