"""
语音识别接口模块
定义ASR（自动语音识别）引擎的抽象基类
"""

from abc import ABC, abstractmethod
from typing import Optional


class ASREngine(ABC):
    """语音识别引擎抽象基类"""
    
    @abstractmethod
    def transcribe(self, audio_path: str) -> Optional[str]:
        """
        将音频文件转为文本
        
        Args:
            audio_path: 音频文件路径
            
        Returns:
            识别出的文本，失败返回None
        """
        pass
    
    @abstractmethod
    def transcribe_stream(self, audio_stream) -> Optional[str]:
        """
        实时流转文本识别
        
        Args:
            audio_stream: 音频流对象
            
        Returns:
            识别出的文本，失败返回None
        """
        pass
    
    @abstractmethod
    def get_supported_formats(self) -> list[str]:
        """
        获取支持的音频格式
        
        Returns:
            支持的音频格式列表
        """
        pass


class NullASR(ASREngine):
    """空实现ASR（用于低档模式或插件未加载时）"""
    
    def transcribe(self, audio_path: str) -> Optional[str]:
        """空实现"""
        return None
    
    def transcribe_stream(self, audio_stream) -> Optional[str]:
        """空实现"""
        return None
    
    def get_supported_formats(self) -> list[str]:
        """返回空列表"""
        return []
