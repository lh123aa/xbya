"""
文件监控接口模块
定义FileMonitor引擎的抽象基类
"""

from abc import ABC, abstractmethod
from typing import Callable, List, Optional, Dict, Any


class FileMonitorEngine(ABC):
    """文件监控引擎抽象基类"""
    
    @abstractmethod
    def start(self, watch_dirs: List[str], callback: Callable[[str, str], None], recursive: bool = True) -> bool:
        """
        启动文件监控
        
        Args:
            watch_dirs: 监控的目录列表
            callback: 回调函数，参数为(事件类型, 文件路径)
            recursive: 是否递归监控子目录
            
        Returns:
            是否启动成功
        """
        pass
    
    @abstractmethod
    def stop(self) -> bool:
        """
        停止文件监控
        
        Returns:
            是否停止成功
        """
        pass
    
    @abstractmethod
    def is_running(self) -> bool:
        """
        检查监控是否运行中
        
        Returns:
            是否运行中
        """
        pass
    
    @abstractmethod
    def add_watch_dir(self, dir_path: str) -> bool:
        """
        添加监控目录
        
        Args:
            dir_path: 目录路径
            
        Returns:
            是否添加成功
        """
        pass
    
    @abstractmethod
    def remove_watch_dir(self, dir_path: str) -> bool:
        """
        移除监控目录
        
        Args:
            dir_path: 目录路径
            
        Returns:
            是否移除成功
        """
        pass
    
    @abstractmethod
    def get_watched_dirs(self) -> List[str]:
        """
        获取监控目录列表
        
        Returns:
            监控目录列表
        """
        pass


class NullFileMonitor(FileMonitorEngine):
    """空实现FileMonitor（用于低档模式或插件未加载时）"""
    
    def start(self, watch_dirs: List[str], callback: Callable[[str, str], None], recursive: bool = True) -> bool:
        """空实现"""
        return False
    
    def stop(self) -> bool:
        """空实现"""
        return False
    
    def is_running(self) -> bool:
        """返回False"""
        return False
    
    def add_watch_dir(self, dir_path: str) -> bool:
        """空实现"""
        return False
    
    def remove_watch_dir(self, dir_path: str) -> bool:
        """空实现"""
        return False
    
    def get_watched_dirs(self) -> List[str]:
        """返回空列表"""
        return []
