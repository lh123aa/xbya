"""
形象生成接口模块
定义Avatar引擎的抽象基类
"""

from abc import ABC, abstractmethod
from typing import Optional, Dict, Any


class AvatarEngine(ABC):
    """形象生成引擎抽象基类"""
    
    @abstractmethod
    def generate(self, photo_path: str) -> Optional[str]:
        """
        从照片生成可动形象
        
        Args:
            photo_path: 照片路径
            
        Returns:
            生成的形象文件路径，失败返回None
        """
        pass
    
    @abstractmethod
    def animate(self, avatar_path: str, action: str) -> Optional[bytes]:
        """
        生成动画帧
        
        Args:
            avatar_path: 形象文件路径
            action: 动作名称（如idle, listen, think, remind）
            
        Returns:
            动画数据，失败返回None
        """
        pass
    
    @abstractmethod
    def get_supported_actions(self) -> list[str]:
        """
        获取支持的动作列表
        
        Returns:
            支持的动作列表
        """
        pass
    
    @abstractmethod
    def is_available(self) -> bool:
        """
        检查引擎是否可用
        
        Returns:
            是否可用
        """
        pass


class NullAvatar(AvatarEngine):
    """空实现Avatar（用于低档模式或插件未加载时）"""
    
    def generate(self, photo_path: str) -> Optional[str]:
        """空实现"""
        return None
    
    def animate(self, avatar_path: str, action: str) -> Optional[bytes]:
        """空实现"""
        return None
    
    def get_supported_actions(self) -> list[str]:
        """返回默认动作列表"""
        return ["idle", "listen", "think", "remind"]
    
    def is_available(self) -> bool:
        """返回False"""
        return False
