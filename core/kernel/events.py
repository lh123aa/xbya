"""类型化事件总线

DSH Cordis 事件系统的 Python 简体版：
- 类型化事件（字符串常量表）
- 订阅返回 disposer（注册即副作用）
- handler 异常隔离（单个失败不影响其他）

线程安全说明：本模块不做加锁，约定由调用方保证单线程发射。
Qt 场景下跨线程通信应通过 Qt Signal 中转，不要直接跨线程 emit。
"""

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List

logger = logging.getLogger(__name__)


class EventTypes:
    """事件类型常量表

    命名约定：<域>.<动作>，域为语音层/Agent层/反馈层。
    """

    # ── 语音层 → Agent 层 ──
    SPEECH_RECOGNIZED = "speech.recognized"    # {text, wav_path, request_id}
    SPEECH_INTERRUPTED = "speech.interrupted"  # {request_id}
    SPEECH_MUTED = "speech.muted"              # {muted: bool}

    # ── Agent 层内部 ──
    INTENT_RESOLVED = "intent.resolved"        # {request_id, command, confidence}
    TASK_SUBMITTED = "task.submitted"          # {request_id, task_id}
    TASK_PROGRESS = "task.progress"            # {task_id, percent, message}
    TASK_COMPLETED = "task.completed"          # {task_id, result}
    TASK_FAILED = "task.failed"                # {task_id, error, request_id}
    TASK_CANCELLED = "task.cancelled"          # {task_id}

    # ── 多步计划（P3 / D5）──
    # 注意：负载字段不可命名为 `source` —— emit() 的第二个形参就是 source，
    # 同名关键字会绑定到该形参而不进入 data。故计划来源用 `plan_source`。
    PLAN_STARTED = "plan.started"              # {request_id, steps, plan_source, total}
    PLAN_STEP_DONE = "plan.step.done"          # {request_id, step_id, action, index, total}
    PLAN_FINISHED = "plan.finished"            # {request_id, status, done, total, summary}

    # ── Agent 层 → 反馈层 ──
    FEEDBACK_ACK = "feedback.ack"              # {request_id, text, category}
    FEEDBACK_RESULT = "feedback.result"        # {request_id, summary, emotion}
    FEEDBACK_CONFIRM = "feedback.confirm"      # {request_id, question, risk}
    FEEDBACK_REFINE = "feedback.refine"        # {request_id, summary, previous, action}
                                               # LLM 润色后的补播（P2-2 异步摘要）

    # ── 定时提醒到点（不来自语音请求，由调度线程发出）──
    REMINDER_DUE = "reminder.due"              # {reminder_id, what, text, request_id}


@dataclass(slots=True)
class Event:
    """事件对象

    Attributes:
        type: 事件类型（EventTypes 中的常量）
        data: 事件负载
        timestamp: 发射时间戳（自动填充）
        source: 事件来源标识（可选）
    """

    type: str
    data: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    source: str = ""


class EventBus:
    """类型化事件总线

    用法：
        bus = EventBus()
        dispose = bus.on("my.event", lambda e: print(e.data))
        bus.emit("my.event", k="v")
        dispose()   # 取消订阅
    """

    def __init__(self) -> None:
        self._handlers: Dict[str, List[Callable[[Event], None]]] = {}
        self._any_handlers: List[Callable[[Event], None]] = []

    # ── 订阅 ──

    def on(self, event_type: str, handler: Callable[[Event], None]) -> Callable[[], None]:
        """订阅事件

        Args:
            event_type: 事件类型
            handler: 处理函数，接收 Event 对象

        Returns:
            取消订阅的函数（幂等）
        """
        self._handlers.setdefault(event_type, []).append(handler)

        def dispose() -> None:
            lst = self._handlers.get(event_type)
            if lst and handler in lst:
                lst.remove(handler)

        return dispose

    def once(self, event_type: str, handler: Callable[[Event], None]) -> Callable[[], None]:
        """一次性订阅：首次触发后自动取消"""
        disposer: List[Callable[[], None]] = []

        def wrapper(event: Event) -> None:
            if disposer:
                disposer[0]()
            handler(event)

        disposer.append(self.on(event_type, wrapper))
        return disposer[0]

    def on_any(self, handler: Callable[[Event], None]) -> Callable[[], None]:
        """通配订阅：接收所有类型的事件"""
        self._any_handlers.append(handler)

        def dispose() -> None:
            if handler in self._any_handlers:
                self._any_handlers.remove(handler)

        return dispose

    def off(self, event_type: str, handler: Callable[[Event], None]) -> bool:
        """按类型取消订阅

        Returns:
            True=成功移除；False=未找到（不抛异常）
        """
        lst = self._handlers.get(event_type)
        if lst and handler in lst:
            lst.remove(handler)
            return True
        return False

    # ── 发射 ──

    def emit(self, event_type: str, source: str = "", **data: Any) -> None:
        """发射事件

        单个 handler 抛异常会被捕获并记录日志，不影响其他 handler。
        使用列表副本迭代，允许 handler 内部修改订阅关系。

        Args:
            event_type: 事件类型
            source: 事件来源标识
            **data: 事件负载
        """
        event = Event(type=event_type, data=data, source=source)

        for handler in list(self._handlers.get(event_type, [])):
            try:
                handler(event)
            except Exception as e:
                logger.error("[events] handler 异常 (type=%s): %s", event_type, e, exc_info=True)

        for handler in list(self._any_handlers):
            try:
                handler(event)
            except Exception as e:
                logger.error("[events] on_any handler 异常 (type=%s): %s", event_type, e, exc_info=True)

    # ── 内省与清理 ──

    def handler_count(self, event_type: str) -> int:
        """指定事件的订阅者数量"""
        return len(self._handlers.get(event_type, []))

    def any_handler_count(self) -> int:
        """通配订阅者数量"""
        return len(self._any_handlers)

    def event_types(self) -> List[str]:
        """当前有订阅者的事件类型列表"""
        return list(self._handlers.keys())

    def clear(self) -> None:
        """清空所有订阅（含通配）"""
        self._handlers.clear()
        self._any_handlers.clear()
