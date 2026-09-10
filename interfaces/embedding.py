"""
向量嵌入接口模块
定义Embedding引擎的抽象基类
"""

from abc import ABC, abstractmethod
from typing import List, Optional


class EmbeddingEngine(ABC):
    """向量嵌入引擎抽象基类"""
    
    @abstractmethod
    def embed(self, text: str) -> Optional[List[float]]:
        """
        将文本转为向量
        
        Args:
            text: 输入文本
            
        Returns:
            向量表示，失败返回None
        """
        pass
    
    @abstractmethod
    def embed_batch(self, texts: List[str]) -> Optional[List[List[float]]]:
        """
        批量文本转向量
        
        Args:
            texts: 输入文本列表
            
        Returns:
            向量列表，失败返回None
        """
        pass
    
    @abstractmethod
    def get_embedding_dimension(self) -> int:
        """
        获取向量维度
        
        Returns:
            向量维度
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


class NullEmbedding(EmbeddingEngine):
    """空实现Embedding（用于低档模式或插件未加载时）"""
    
    def embed(self, text: str) -> Optional[List[float]]:
        """空实现"""
        return None
    
    def embed_batch(self, texts: List[str]) -> Optional[List[List[float]]]:
        """空实现"""
        return None
    
    def get_embedding_dimension(self) -> int:
        """返回默认维度"""
        return 384
    
    def is_available(self) -> bool:
        """返回False"""
        return False
