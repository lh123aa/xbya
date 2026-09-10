"""
事件总线模块
负责系统内模块间的事件通信
"""

import logging
from typing import Callable, Dict, List, Any, Optional
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

logger = logging.getLogger(__name__)


class EventType(Enum):
    """事件类型"""
    # 系统事件
    APP_START = "app.start"
    APP_STOP = "app.stop"
    CONFIG_CHANGE = "config.change"
    
    # 语音事件
    VOICE_WAKE_UP = "voice.wake_up"
    VOICE_RECORD_START = "voice.record_start"
    VOICE_RECORD_STOP = "voice.record_stop"
    VOICE_RECOGNIZED = "voice.recognized"
    VOICE_RESPONSE = "voice.response"
    
    # AI事件
    AI_THINKING = "ai.thinking"
    AI_RESPONSE = "ai.response"
    AI_ERROR = "ai.error"
    
    # 文件事件
    FILE_DETECTED = "file.detected"
    FILE_REMINDER = "file.reminder"
    FILE_OPERATION = "file.operation"
    
    # UI事件
    UI_STATE_CHANGE = "ui.state_change"
    UI_POSITION_CHANGE = "ui.position_change"
    UI_ANIMATION = "ui.animation"
    
    # 安全事件
    SECURITY_VERIFY = "security.verify"
    SECURITY_CONFIRM = "security.confirm"
    SECURITY_DENY = "security.deny"


@dataclass
class Event:
    """事件对象"""
    event_type: EventType
    data: Dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.now)
    source: Optional[str] = None


class EventBus:
    """事件总线"""
    
    def __init__(self):
        self._handlers: Dict[EventType, List[Callable]] = {}
        self._event_history: List[Event] = []
        self._max_history_size = 1000
    
    def subscribe(self, event_type: EventType, handler: Callable[[Event], None]) -> None:
        """
        订阅事件
        
        Args:
            event_type: 事件类型
            handler: 事件处理函数
        """
        if event_type not in self._handlers:
            self._handlers[event_type] = []
        
        self._handlers[event_type].append(handler)
        logger.debug(f"订阅事件: {event_type.value}")
    
    def unsubscribe(self, event_type: EventType, handler: Callable[[Event], None]) -> bool:
        """
        取消订阅
        
        Args:
            event_type: 事件类型
            handler: 事件处理函数
            
        Returns:
            是否取消成功
        """
        if event_type in self._handlers:
            try:
                self._handlers[event_type].remove(handler)
                logger.debug(f"取消订阅: {event_type.value}")
                return True
            except ValueError:
                pass
        return False
    
    def publish(self, event: Event) -> None:
        """
        发布事件
        
        Args:
            event: 事件对象
        """
        # 记录事件历史
        self._event_history.append(event)
        if len(self._event_history) > self._max_history_size:
            self._event_history = self._event_history[-self._max_history_size:]
        
        # 通知所有订阅者
        if event.event_type in self._handlers:
            for handler in self._handlers[event.event_type]:
                try:
                    handler(event)
                except Exception as e:
                    logger.error(f"事件处理错误 {event.event_type.value}: {e}")
        
        logger.debug(f"发布事件: {event.event_type.value}")
    
    def emit(self, event_type: EventType, data: Dict[str, Any] = None, 
             source: Optional[str] = None) -> None:
        """
        发送事件（快捷方法）
        
        Args:
            event_type: 事件类型
            data: 事件数据
            source: 事件来源
        """
        event = Event(
            event_type=event_type,
            data=data or {},
            source=source
        )
        self.publish(event)
    
    def get_history(self, event_type: Optional[EventType] = None, 
                   limit: int = 100) -> List[Event]:
        """
        获取事件历史
        
        Args:
            event_type: 事件类型过滤
            limit: 返回数量限制
            
        Returns:
            事件列表
        """
        if event_type:
            filtered = [e for e in self._event_history if e.event_type == event_type]
        else:
            filtered = self._event_history
        
        return filtered[-limit:]
    
    def clear_history(self) -> None:
        """清空事件历史"""
        self._event_history.clear()
    
    def get_subscriber_count(self, event_type: EventType) -> int:
        """
        获取事件订阅者数量
        
        Args:
            event_type: 事件类型
            
        Returns:
            订阅者数量
        """
        return len(self._handlers.get(event_type, []))


# 全局事件总线实例
_event_bus: Optional[EventBus] = None


def get_event_bus() -> EventBus:
    """
    获取事件总线单例
    
    Returns:
        事件总线实例
    """
    global _event_bus
    
    if _event_bus is None:
        _event_bus = EventBus()
    
    return _event_bus
