"""
大语言模型接口模块
定义LLM引擎的抽象基类
"""

from abc import ABC, abstractmethod
from typing import Optional, List, Dict, Any


class LLMEngine(ABC):
    """大语言模型引擎抽象基类"""
    
    @abstractmethod
    def chat(self, prompt: str, context: List[Dict[str, Any]] = None) -> Optional[str]:
        """
        对话生成
        
        Args:
            prompt: 用户输入
            context: 对话上下文历史
            
        Returns:
            生成的回复文本，失败返回None
        """
        pass
    
    @abstractmethod
    def generate(self, prompt: str, max_tokens: int = 1024) -> Optional[str]:
        """
        文本生成
        
        Args:
            prompt: 提示词
            max_tokens: 最大生成token数
            
        Returns:
            生成的文本，失败返回None
        """
        pass
    
    @abstractmethod
    def is_available(self) -> bool:
        """
        检查模型是否可用
        
        Returns:
            是否可用
        """
        pass
    
    @abstractmethod
    def get_model_info(self) -> Dict[str, Any]:
        """
        获取模型信息
        
        Returns:
            模型信息字典
        """
        pass


class NullLLM(LLMEngine):
    """空实现LLM（用于低档模式或插件未加载时）"""
    
    def chat(self, prompt: str, context: List[Dict[str, Any]] = None) -> Optional[str]:
        """空实现"""
        return "抱歉，AI功能暂时不可用。"
    
    def generate(self, prompt: str, max_tokens: int = 1024) -> Optional[str]:
        """空实现"""
        return "抱歉，AI功能暂时不可用。"
    
    def is_available(self) -> bool:
        """返回False"""
        return False
    
    def get_model_info(self) -> Dict[str, Any]:
        """返回空信息"""
        return {"name": "null", "version": "1.0.0", "status": "unavailable"}
