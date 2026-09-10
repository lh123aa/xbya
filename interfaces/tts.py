"""
语音合成接口模块
定义TTS（文本转语音）引擎的抽象基类
"""

from abc import ABC, abstractmethod
from typing import Optional


class TTSEngine(ABC):
    """语音合成引擎抽象基类"""
    
    @abstractmethod
    def speak(self, text: str) -> Optional[bytes]:
        """
        将文本转为语音音频
        
        Args:
            text: 要转换的文本
            
        Returns:
            音频数据（字节格式），失败返回None
        """
        pass
    
    @abstractmethod
    def speak_to_file(self, text: str, output_path: str) -> bool:
        """
        将文本转为语音并保存到文件
        
        Args:
            text: 要转换的文本
            output_path: 输出文件路径
            
        Returns:
            是否成功
        """
        pass
    
    @abstractmethod
    def get_available_voices(self) -> list[str]:
        """
        获取可用的语音列表
        
        Returns:
            可用语音名称列表
        """
        pass
    
    @abstractmethod
    def set_voice(self, voice_name: str) -> bool:
        """
        设置语音
        
        Args:
            voice_name: 语音名称
            
        Returns:
            是否设置成功
        """
        pass


class NullTTS(TTSEngine):
    """空实现TTS（用于低档模式或插件未加载时）"""
    
    def speak(self, text: str) -> Optional[bytes]:
        """空实现"""
        return None
    
    def speak_to_file(self, text: str, output_path: str) -> bool:
        """空实现"""
        return False
    
    def get_available_voices(self) -> list[str]:
        """返回空列表"""
        return []
    
    def set_voice(self, voice_name: str) -> bool:
        """空实现"""
        return False
    
    def is_available(self) -> bool:
        """返回False"""
        return False
