"""执行能力接口定义（Service Definition）

DSH 能力 Seam 三角的 Definition 角色：
- Definition: 本模块（ExecutorService）
- Provider:   agent/providers/executor/thread_pool.py
- Consumer:   agent/pipeline.py

职责：把（工具名, 参数）异步执行并回调结果，支持取消与超时。
"""

from abc import abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

from core.kernel.service import Service


@dataclass(slots=True)
class TaskHandle:
    """已提交任务的句柄

    Attributes:
        task_id: 任务唯一标识
        request_id: 对应的用户请求 ID
        action: 工具名
        params: 工具参数
        cancelled: 是否已被取消
    """

    task_id: str
    request_id: str
    action: str
    params: Dict[str, Any] = field(default_factory=dict)
    cancelled: bool = False


#: 任务完成回调签名：(task_handle, tool_result) -> None
TaskCallback = Callable[[TaskHandle, Any], None]


class ExecutorService(Service):
    """执行能力接口

    实现者约定：
    - submit() 立即返回（不阻塞调用线程）
    - 任务完成后调用 callback(handle, ToolResult)
    - 执行线程内不得触碰 Qt UI（由回调方通过信号中转）
    - cancel() 为尽力而为：已在执行的任务不保证立即中断
    """

    capability_name = "executor"

    @abstractmethod
    def submit(
        self,
        request_id: str,
        action: str,
        params: Dict[str, Any],
        callback: TaskCallback,
    ) -> str:
        """提交任务

        Args:
            request_id: 用户请求 ID
            action: 工具名
            params: 工具参数
            callback: 完成回调

        Returns:
            task_id；无法提交时返回空串
        """

    @abstractmethod
    def cancel(self, task_id: str) -> bool:
        """取消任务（尽力而为）

        Returns:
            True=任务被标记取消或已不存在；False=无法取消
        """

    @abstractmethod
    def cancel_by_request(self, request_id: str) -> int:
        """取消某请求下的全部任务，返回取消数量"""

    @abstractmethod
    def pending_count(self) -> int:
        """当前未完成的任务数"""

    @abstractmethod
    def shutdown(self, wait: bool = False) -> None:
        """关闭执行器"""
