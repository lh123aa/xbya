"""Agent 层消息协议

定义语音层与执行层之间的通信格式：
- AgentCommand  Voice → Agent（用户意图）
- AgentResult   Agent → Voice（执行结果）

设计原则：
- 纯数据类，无行为（便于序列化、日志、测试）
- request_id 贯穿全链路，用于匹配请求与响应
- emotion 字段直接驱动动效（复用 services/emotion_analyzer 的词表）
"""

import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class CommandStatus(str, Enum):
    """命令执行状态"""

    PENDING = "pending"            # 已提交，等待执行
    RUNNING = "running"            # 执行中
    SUCCESS = "success"            # 执行成功
    ERROR = "error"                # 执行失败
    NEEDS_CONFIRM = "needs_confirm"  # 需要用户确认
    CANCELLED = "cancelled"        # 被取消（打断/超时）
    REJECTED = "rejected"          # 被安全守卫拒绝


class RiskLevel(str, Enum):
    """操作风险等级"""

    LOW = "low"            # 自动执行
    MEDIUM = "medium"      # 单次确认
    HIGH = "high"          # 双重确认
    CRITICAL = "critical"  # 直接拒绝


def new_request_id() -> str:
    """生成新的请求 ID（短 ID，便于日志阅读）"""
    return uuid.uuid4().hex[:12]


@dataclass(slots=True)
class AgentCommand:
    """语音层 → Agent 层的命令

    Attributes:
        action: 工具名（如 "file_search"）；"chat" 表示走闲聊，不执行工具
        params: 工具参数
        raw_text: 用户原始语音文本（用于日志与兜底）
        request_id: 请求唯一标识（贯穿全链路）
        confidence: 意图识别置信度 0.0~1.0
        source: 命令来源（"voice" / "click" / "hotkey"）
    """

    action: str
    params: Dict[str, Any] = field(default_factory=dict)
    raw_text: str = ""
    request_id: str = field(default_factory=new_request_id)
    confidence: float = 1.0
    source: str = "voice"

    def is_chat(self) -> bool:
        """是否为闲聊（不走工具执行）"""
        return self.action == "chat"

    def __str__(self) -> str:
        return f"[{self.request_id}] {self.action}({self.params}) conf={self.confidence:.2f}"


@dataclass(slots=True)
class AgentResult:
    """Agent 层 → 语音层的结果

    Attributes:
        request_id: 对应命令的请求 ID
        status: 执行状态
        summary: 人类可读摘要（直接用于 TTS 播报）
        data: 结构化数据（供 UI 展示）
        emotion: 情绪标签，驱动动效（happy/sad/think/surprise/love/calm/talk）
        tool_name: 实际执行的工具名
        error: 错误信息（status=error 时）
        follow_up: 建议的后续操作（如 ["打开第一个", "全部删除"]）
        elapsed_ms: 执行耗时（毫秒）
    """

    request_id: str
    status: CommandStatus = CommandStatus.SUCCESS
    summary: str = ""
    data: Any = None
    emotion: str = "talk"
    tool_name: str = ""
    error: str = ""
    follow_up: List[str] = field(default_factory=list)
    elapsed_ms: float = 0.0

    @property
    def ok(self) -> bool:
        """是否成功"""
        return self.status == CommandStatus.SUCCESS

    @property
    def needs_confirm(self) -> bool:
        """是否需要用户确认"""
        return self.status == CommandStatus.NEEDS_CONFIRM

    @classmethod
    def success(
        cls,
        request_id: str,
        summary: str,
        **kwargs: Any,
    ) -> "AgentResult":
        """构造成功结果"""
        return cls(
            request_id=request_id,
            status=CommandStatus.SUCCESS,
            summary=summary,
            **kwargs,
        )

    @classmethod
    def failure(
        cls,
        request_id: str,
        error: str,
        summary: str = "",
        **kwargs: Any,
    ) -> "AgentResult":
        """构造失败结果"""
        return cls(
            request_id=request_id,
            status=CommandStatus.ERROR,
            summary=summary or error,
            error=error,
            emotion="sad",
            **kwargs,
        )

    @classmethod
    def confirm(
        cls,
        request_id: str,
        question: str,
        **kwargs: Any,
    ) -> "AgentResult":
        """构造待确认结果"""
        return cls(
            request_id=request_id,
            status=CommandStatus.NEEDS_CONFIRM,
            summary=question,
            emotion="think",
            **kwargs,
        )

    @classmethod
    def rejected(
        cls,
        request_id: str,
        reason: str,
        **kwargs: Any,
    ) -> "AgentResult":
        """构造被拒绝结果"""
        return cls(
            request_id=request_id,
            status=CommandStatus.REJECTED,
            summary=reason,
            emotion="surprise",
            **kwargs,
        )

    def __str__(self) -> str:
        return f"[{self.request_id}] {self.status.value}: {self.summary[:40]}"
