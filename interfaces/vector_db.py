"""
向量数据库接口模块
定义VectorDB引擎的抽象基类
"""

from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional


class VectorDBEngine(ABC):
    """向量数据库引擎抽象基类"""
    
    @abstractmethod
    def search(self, vector: List[float], top_k: int = 5) -> List[Dict[str, Any]]:
        """
        向量检索
        
        Args:
            vector: 查询向量
            top_k: 返回结果数量
            
        Returns:
            检索结果列表，包含id、metadata、score等
        """
        pass
    
    @abstractmethod
    def upsert(self, id: str, vector: List[float], metadata: Dict[str, Any]) -> bool:
        """
        插入或更新向量
        
        Args:
            id: 向量ID
            vector: 向量数据
            metadata: 元数据
            
        Returns:
            是否成功
        """
        pass
    
    @abstractmethod
    def delete(self, id: str) -> bool:
        """
        删除向量
        
        Args:
            id: 向量ID
            
        Returns:
            是否成功
        """
        pass
    
    @abstractmethod
    def count(self) -> int:
        """
        获取向量总数
        
        Returns:
            向量数量
        """
        pass
    
    @abstractmethod
    def is_available(self) -> bool:
        """
        检查数据库是否可用
        
        Returns:
            是否可用
        """
        pass


class NullVectorDB(VectorDBEngine):
    """空实现VectorDB（用于低档模式或插件未加载时）"""
    
    def search(self, vector: List[float], top_k: int = 5) -> List[Dict[str, Any]]:
        """返回空列表"""
        return []
    
    def upsert(self, id: str, vector: List[float], metadata: Dict[str, Any]) -> bool:
        """空实现"""
        return False
    
    def delete(self, id: str) -> bool:
        """空实现"""
        return False
    
    def count(self) -> int:
        """返回0"""
        return 0
    
    def is_available(self) -> bool:
        """返回False"""
        return False
