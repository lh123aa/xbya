"""安全能力接口定义（Service Definition）

DSH 能力 Seam 三角的 Definition 角色：
- Definition: 本模块（SafetyService）
- Provider:   agent/providers/safety/basic_guard.py
- Consumer:   agent/tools/*（工具执行前调用 check 与 validate_path）

换一套安全策略 = 换 Provider，工具层代码不动。
"""

from abc import abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

from core.kernel.service import Service


class SecurityError(Exception):
    """安全策略拒绝执行

    Attributes:
        action: 被拒绝的操作
        reason: 拒绝原因（面向用户）
    """

    def __init__(self, action: str, reason: str) -> None:
        self.action = action
        self.reason = reason
        super().__init__(f"安全拒绝 [{action}]: {reason}")


class PathNotAllowed(SecurityError):
    """路径不在允许范围内

    Attributes:
        path: 被拒绝的路径
        allowed: 允许的根目录列表
    """

    def __init__(self, path: str, allowed: List[Path] = None) -> None:
        self.path = path
        self.allowed = allowed or []
        roots = "、".join(str(p) for p in self.allowed) if self.allowed else "(未配置)"
        super().__init__(
            "path_check",
            f"这个位置我不能动哦，只能操作：{roots}",
        )


@dataclass(slots=True)
class SafetyVerdict:
    """安全检查结论

    Attributes:
        allowed: 是否允许直接执行（不经确认）
        risk: 风险等级（low/medium/high/critical）
        confirm_needed: 是否需要用户确认
        require_double: 是否需要双重确认
        reason: 拒绝原因（allowed=False 且非确认场景时）
        preview: 操作预览文案（展示给用户）
    """

    allowed: bool = True
    risk: str = "low"
    confirm_needed: bool = False
    require_double: bool = False
    reason: str = ""
    preview: str = ""

    @property
    def rejected(self) -> bool:
        """是否被硬拒绝（不可通过确认放行）"""
        return not self.allowed and not self.confirm_needed


class SafetyService(Service):
    """安全能力接口

    职责：
    1. 路径校验（白名单 + 规范化 + 符号链接解析）
    2. 操作风险评估（三级风险 + 确认策略）
    3. 确认状态管理（请求/响应/超时）
    4. 审计日志（所有写操作留痕）
    """

    capability_name = "safety"

    @abstractmethod
    def check(self, action: str, params: Dict[str, Any]) -> SafetyVerdict:
        """评估操作风险

        Args:
            action: 工具名
            params: 工具参数

        Returns:
            SafetyVerdict
        """

    @abstractmethod
    def validate_path(self, path: str) -> Path:
        """校验路径并返回规范化后的绝对路径

        Args:
            path: 待校验路径（支持 ~ 展开与相对路径）

        Returns:
            规范化后的绝对路径

        Raises:
            PathNotAllowed: 路径不在白名单内
        """

    @abstractmethod
    def validate_paths(self, paths: List[str]) -> List[Path]:
        """批量校验路径（任一失败则整体失败）"""

    @abstractmethod
    def request_confirm(
        self,
        request_id: str,
        action: str,
        params: Dict[str, Any],
        preview: str = "",
    ) -> str:
        """登记待确认操作

        Args:
            request_id: 请求 ID
            action: 工具名
            params: 工具参数
            preview: 调用方（通常由工具 preview()）提供的操作预览；
                     为空时由守卫自行从 params 生成

        Returns:
            给用户看的确认文案
        """

    @abstractmethod
    def resolve_confirm(self, request_id: str, approved: bool) -> bool:
        """处理用户的确认响应

        Returns:
            True=找到了对应的待确认项
        """

    @abstractmethod
    def is_confirmed(self, request_id: str) -> bool:
        """该请求是否已获确认（或无需确认）"""

    @abstractmethod
    def audit(
        self,
        action: str,
        params: Dict[str, Any],
        success: bool,
        detail: str = "",
    ) -> None:
        """记录审计日志"""

    @abstractmethod
    def is_allowed_path(self, path: Path) -> bool:
        """纯判断：路径是否白名单内（不抛异常）"""

    @abstractmethod
    def whitelist_roots(self) -> List[Path]:
        """返回当前白名单根目录列表"""
