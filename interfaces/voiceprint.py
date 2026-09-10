"""
声纹识别接口模块
定义Voiceprint引擎的抽象基类
"""

from abc import ABC, abstractmethod
from typing import Optional, Dict, Any


class VoiceprintEngine(ABC):
    """声纹识别引擎抽象基类"""
    
    @abstractmethod
    def enroll(self, audio_path: str, user_id: str) -> bool:
        """
        注册声纹
        
        Args:
            audio_path: 音频文件路径
            user_id: 用户ID
            
        Returns:
            是否注册成功
        """
        pass
    
    @abstractmethod
    def verify(self, audio_path: str, user_id: str) -> bool:
        """
        验证声纹
        
        Args:
            audio_path: 音频文件路径
            user_id: 用户ID
            
        Returns:
            验证是否通过
        """
        pass
    
    @abstractmethod
    def identify(self, audio_path: str) -> Optional[str]:
        """
        识别声纹（识别是谁）
        
        Args:
            audio_path: 音频文件路径
            
        Returns:
            识别出的用户ID，失败返回None
        """
        pass
    
    @abstractmethod
    def delete_user(self, user_id: str) -> bool:
        """
        删除用户声纹
        
        Args:
            user_id: 用户ID
            
        Returns:
            是否删除成功
        """
        pass
    
    @abstractmethod
    def is_enrolled(self, user_id: str) -> bool:
        """
        检查用户是否已注册声纹
        
        Args:
            user_id: 用户ID
            
        Returns:
            是否已注册
        """
        pass


class NullVoiceprint(VoiceprintEngine):
    """空实现Voiceprint（用于低档模式或插件未加载时）"""
    
    def enroll(self, audio_path: str, user_id: str) -> bool:
        """空实现"""
        return False
    
    def verify(self, audio_path: str, user_id: str) -> bool:
        """空实现"""
        return False
    
    def identify(self, audio_path: str) -> Optional[str]:
        """空实现"""
        return None
    
    def delete_user(self, user_id: str) -> bool:
        """空实现"""
        return False
    
    def is_enrolled(self, user_id: str) -> bool:
        """返回False"""
        return False
    
    def is_available(self) -> bool:
        """返回False"""
        return False
