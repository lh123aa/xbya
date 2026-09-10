"""
事件总线测试
"""

import pytest
from datetime import datetime

import sys
from pathlib import Path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from core.event_bus import EventBus, Event, EventType


class TestEventBus:
    """事件总线测试类"""
    
    def setup_method(self):
        """测试前设置"""
        self.event_bus = EventBus()
    
    def test_subscribe_and_publish(self):
        """测试订阅和发布事件"""
        received_events = []
        
        def handler(event):
            received_events.append(event)
        
        # 订阅事件
        self.event_bus.subscribe(EventType.APP_START, handler)
        
        # 发布事件
        self.event_bus.emit(EventType.APP_START, {"test": "data"})
        
        # 验证事件被接收
        assert len(received_events) == 1
        assert received_events[0].event_type == EventType.APP_START
        assert received_events[0].data["test"] == "data"
    
    def test_unsubscribe(self):
        """测试取消订阅"""
        received_events = []
        
        def handler(event):
            received_events.append(event)
        
        # 订阅事件
        self.event_bus.subscribe(EventType.APP_START, handler)
        
        # 取消订阅
        result = self.event_bus.unsubscribe(EventType.APP_START, handler)
        assert result is True
        
        # 发布事件
        self.event_bus.emit(EventType.APP_START)
        
        # 验证事件未被接收
        assert len(received_events) == 0
    
    def test_multiple_subscribers(self):
        """测试多个订阅者"""
        received_events_1 = []
        received_events_2 = []
        
        def handler_1(event):
            received_events_1.append(event)
        
        def handler_2(event):
            received_events_2.append(event)
        
        # 订阅事件
        self.event_bus.subscribe(EventType.VOICE_WAKE_UP, handler_1)
        self.event_bus.subscribe(EventType.VOICE_WAKE_UP, handler_2)
        
        # 发布事件
        self.event_bus.emit(EventType.VOICE_WAKE_UP)
        
        # 验证两个订阅者都收到事件
        assert len(received_events_1) == 1
        assert len(received_events_2) == 1
    
    def test_event_history(self):
        """测试事件历史"""
        # 发布多个事件
        self.event_bus.emit(EventType.APP_START)
        self.event_bus.emit(EventType.VOICE_WAKE_UP)
        self.event_bus.emit(EventType.AI_THINKING)
        
        # 获取历史
        history = self.event_bus.get_history()
        assert len(history) == 3
        
        # 按类型过滤
        voice_history = self.event_bus.get_history(EventType.VOICE_WAKE_UP)
        assert len(voice_history) == 1
    
    def test_clear_history(self):
        """测试清空历史"""
        # 发布事件
        self.event_bus.emit(EventType.APP_START)
        
        # 清空历史
        self.event_bus.clear_history()
        
        # 验证历史为空
        history = self.event_bus.get_history()
        assert len(history) == 0
    
    def test_subscriber_count(self):
        """测试订阅者数量"""
        def handler_1(event):
            pass
        
        def handler_2(event):
            pass
        
        # 订阅事件
        self.event_bus.subscribe(EventType.APP_START, handler_1)
        self.event_bus.subscribe(EventType.APP_START, handler_2)
        
        # 获取订阅者数量
        count = self.event_bus.get_subscriber_count(EventType.APP_START)
        assert count == 2
    
    def test_event_data(self):
        """测试事件数据"""
        received_data = None
        
        def handler(event):
            nonlocal received_data
            received_data = event.data
        
        # 订阅事件
        self.event_bus.subscribe(EventType.FILE_DETECTED, handler)
        
        # 发布带数据的事件
        self.event_bus.emit(EventType.FILE_DETECTED, {
            "file_path": "/path/to/file.txt",
            "file_type": "document"
        })
        
        # 验证数据
        assert received_data is not None
        assert received_data["file_path"] == "/path/to/file.txt"
        assert received_data["file_type"] == "document"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
