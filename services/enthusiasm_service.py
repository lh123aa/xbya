"""
热情度语音调节模块
负责根据热情度级别调整语音播报的语调和内容
"""

import logging
import random
from typing import Optional, Dict, Any, List
from enum import IntEnum

logger = logging.getLogger(__name__)


class EnthusiasmLevel(IntEnum):
    """热情度级别"""
    MINIMAL = 1      # 最小化
    LOW = 2          # 低
    MEDIUM = 3       # 中等
    HIGH = 4         # 高
    MAXIMUM = 5      # 最大化


class EnthusiasmManager:
    """热情度管理器"""
    
    def __init__(self, level: EnthusiasmLevel = EnthusiasmLevel.MEDIUM):
        """
        初始化热情度管理器
        
        Args:
            level: 热情度级别
        """
        self.level = level
        self._phrases: Dict[EnthusiasmLevel, List[str]] = {
            EnthusiasmLevel.MINIMAL: [
                "检测到新文件。",
                "文件已索引。",
                "操作完成。",
            ],
            EnthusiasmLevel.LOW: [
                "检测到新文件。",
                "文件已索引。",
                "需要帮忙吗？",
            ],
            EnthusiasmLevel.MEDIUM: [
                "主人，检测到新文件！",
                "文件已索引完成。",
                "有什么可以帮你的吗？",
            ],
            EnthusiasmLevel.HIGH: [
                "主人主人！我发现了新文件！",
                "文件索引完成啦！",
                "我随时准备帮助你！",
            ],
            EnthusiasmLevel.MAXIMUM: [
                "哇！主人主人！我发现了超棒的新文件！",
                "太棒了！文件索引完成！",
                "我超级超级想帮助你！",
            ],
        }
        
        logger.info(f"热情度管理器初始化: 级别={level.value}")
    
    def set_level(self, level: EnthusiasmLevel) -> None:
        """
        设置热情度级别
        
        Args:
            level: 热情度级别
        """
        self.level = level
        logger.info(f"热情度级别已设置: {level.value}")
    
    def get_level(self) -> EnthusiasmLevel:
        """获取当前热情度级别"""
        return self.level
    
    def get_phrase(self, context: str = "default") -> str:
        """
        获取随机短语
        
        Args:
            context: 上下文类型
            
        Returns:
            随机短语
        """
        phrases = self._phrases.get(self.level, self._phrases[EnthusiasmLevel.MEDIUM])
        return random.choice(phrases)
    
    def get_enthusiastic_greeting(self) -> str:
        """获取热情的问候语"""
        greetings = {
            EnthusiasmLevel.MINIMAL: "你好。",
            EnthusiasmLevel.LOW: "你好。",
            EnthusiasmLevel.MEDIUM: "你好，主人。",
            EnthusiasmLevel.HIGH: "你好呀，主人！",
            EnthusiasmLevel.MAXIMUM: "哇！主人你好呀！",
        }
        return greetings.get(self.level, "你好，主人。")
    
    def get_file_reminder(self, file_name: str) -> str:
        """
        获取文件提醒语
        
        Args:
            file_name: 文件名称
            
        Returns:
            提醒语
        """
        reminders = {
            EnthusiasmLevel.MINIMAL: f"检测到新文件：{file_name}。",
            EnthusiasmLevel.LOW: f"检测到新文件：{file_name}。",
            EnthusiasmLevel.MEDIUM: f"主人，检测到新文件 '{file_name}'，需要我帮你处理吗？",
            EnthusiasmLevel.HIGH: f"主人主人！我发现了新文件 '{file_name}'！要我帮你看看吗？",
            EnthusiasmLevel.MAXIMUM: f"哇！主人主人！我发现了一个超棒的新文件 '{file_name}'！要我帮你仔细看看吗？",
        }
        return reminders.get(self.level, f"检测到新文件：{file_name}。")
    
    def get_search_result(self, count: int) -> str:
        """
        获取搜索结果提示
        
        Args:
            count: 结果数量
            
        Returns:
            提示语
        """
        if count == 0:
            return "没有找到相关文件。"
        
        prompts = {
            EnthusiasmLevel.MINIMAL: f"找到 {count} 个文件。",
            EnthusiasmLevel.LOW: f"找到 {count} 个文件。",
            EnthusiasmLevel.MEDIUM: f"主人，找到 {count} 个相关文件。",
            EnthusiasmLevel.HIGH: f"主人！找到 {count} 个相关文件！",
            EnthusiasmLevel.MAXIMUM: f"哇！主人！找到 {count} 个相关文件！太棒了！",
        }
        return prompts.get(self.level, f"找到 {count} 个文件。")
    
    def get_error_message(self, error: str) -> str:
        """
        获取错误提示
        
        Args:
            error: 错误描述
            
        Returns:
            提示语
        """
        messages = {
            EnthusiasmLevel.MINIMAL: f"错误：{error}",
            EnthusiasmLevel.LOW: f"出现错误：{error}",
            EnthusiasmLevel.MEDIUM: f"抱歉，出现错误：{error}",
            EnthusiasmLevel.HIGH: f"抱歉主人，出现错误：{error}",
            EnthusiasmLevel.MAXIMUM: f"呜呜，抱歉主人，出现错误：{error}",
        }
        return messages.get(self.level, f"错误：{error}")
    
    def get_confirmation(self, action: str) -> str:
        """
        获取确认提示
        
        Args:
            action: 操作描述
            
        Returns:
            提示语
        """
        prompts = {
            EnthusiasmLevel.MINIMAL: f"确认执行：{action}？",
            EnthusiasmLevel.LOW: f"确认执行：{action}？",
            EnthusiasmLevel.MEDIUM: f"主人，确认执行 '{action}' 吗？",
            EnthusiasmLevel.HIGH: f"主人，确认执行 '{action}' 吗？",
            EnthusiasmLevel.MAXIMUM: f"主人主人，确认执行 '{action}' 吗？",
        }
        return prompts.get(self.level, f"确认执行：{action}？")
    
    def get_success_message(self, action: str) -> str:
        """
        获取成功提示
        
        Args:
            action: 操作描述
            
        Returns:
            提示语
        """
        messages = {
            EnthusiasmLevel.MINIMAL: f"{action}完成。",
            EnthusiasmLevel.LOW: f"{action}完成。",
            EnthusiasmLevel.MEDIUM: f"主人，{action}完成！",
            EnthusiasmLevel.HIGH: f"主人！{action}完成！",
            EnthusiasmLevel.MAXIMUM: f"哇！主人！{action}完成！太棒了！",
        }
        return messages.get(self.level, f"{action}完成。")


# 全局热情度管理器实例
_enthusiasm_manager: Optional[EnthusiasmManager] = None


def get_enthusiasm_manager(level: int = 3) -> EnthusiasmManager:
    """
    获取热情度管理器单例
    
    Args:
        level: 热情度级别（1-5）
        
    Returns:
        热情度管理器实例
    """
    global _enthusiasm_manager
    
    if _enthusiasm_manager is None:
        try:
            enthusiasm_level = EnthusiasmLevel(level)
        except ValueError:
            enthusiasm_level = EnthusiasmLevel.MEDIUM
        
        _enthusiasm_manager = EnthusiasmManager(enthusiasm_level)
    
    return _enthusiasm_manager
