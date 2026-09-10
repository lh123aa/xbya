"""
Watchfiles文件监控插件
实现文件系统监控功能
"""

import logging
import threading
from typing import Optional, Callable, List

from interfaces.file_monitor import FileMonitorEngine

logger = logging.getLogger(__name__)


class WatchfilesMonitor(FileMonitorEngine):
    """Watchfiles文件监控实现"""
    
    def __init__(self, **kwargs):
        """初始化Watchfiles文件监控"""
        self._running = False
        self._watch_dirs: List[str] = []
        self._callback: Optional[Callable[[str, str], None]] = None
        self._recursive = kwargs.get("recursive", True)
        self._thread: Optional[threading.Thread] = None
        self._watcher = None
        
        self._available = False
        self._check_dependency()
    
    def _check_dependency(self) -> bool:
        """检查依赖"""
        try:
            from watchfiles import watch
            self._available = True
            logger.info("Watchfiles依赖检查通过")
            return True
        except ImportError:
            logger.warning("watchfiles未安装，请运行: pip install watchfiles")
            return False
    
    def start(self, watch_dirs: List[str], callback: Callable[[str, str], None], 
              recursive: bool = True) -> bool:
        """
        启动文件监控
        
        Args:
            watch_dirs: 监控的目录列表
            callback: 回调函数，参数为(事件类型, 文件路径)
            recursive: 是否递归监控子目录
            
        Returns:
            是否启动成功
        """
        if not self._available:
            logger.error("Watchfiles不可用")
            return False
        
        if self._running:
            logger.warning("文件监控已在运行")
            return False
        
        try:
            self._watch_dirs = watch_dirs
            self._callback = callback
            self._recursive = recursive
            self._running = True
            
            # 启动监控线程
            self._thread = threading.Thread(
                target=self._monitor_loop,
                daemon=True
            )
            self._thread.start()
            
            logger.info(f"文件监控已启动，监控目录: {watch_dirs}")
            return True
            
        except Exception as e:
            logger.error(f"启动文件监控失败: {e}")
            self._running = False
            return False
    
    def _monitor_loop(self) -> None:
        """监控循环"""
        try:
            from watchfiles import watch, Change
            
            # 监控文件变化
            for changes in watch(*self._watch_dirs, recursive=self._recursive):
                if not self._running:
                    break
                
                for change_type, path in changes:
                    # 映射变化类型
                    event_type = "modify"
                    if change_type == Change.added:
                        event_type = "create"
                    elif change_type == Change.deleted:
                        event_type = "delete"
                    elif change_type == Change.modified:
                        event_type = "modify"
                    
                    # 调用回调函数
                    if self._callback:
                        try:
                            self._callback(event_type, path)
                        except Exception as e:
                            logger.error(f"回调函数执行失败: {e}")
                
        except Exception as e:
            logger.error(f"文件监控循环异常: {e}")
        finally:
            self._running = False
    
    def stop(self) -> bool:
        """
        停止文件监控
        
        Returns:
            是否停止成功
        """
        if not self._running:
            return True
        
        try:
            self._running = False
            
            # 等待线程结束
            if self._thread and self._thread.is_alive():
                self._thread.join(timeout=5)
            
            logger.info("文件监控已停止")
            return True
            
        except Exception as e:
            logger.error(f"停止文件监控失败: {e}")
            return False
    
    def is_running(self) -> bool:
        """
        检查监控是否运行中
        
        Returns:
            是否运行中
        """
        return self._running
    
    def add_watch_dir(self, dir_path: str) -> bool:
        """
        添加监控目录
        
        Args:
            dir_path: 目录路径
            
        Returns:
            是否添加成功
        """
        if dir_path not in self._watch_dirs:
            self._watch_dirs.append(dir_path)
            logger.info(f"添加监控目录: {dir_path}")
            
            # 如果正在运行，需要重启监控
            if self._running:
                self.stop()
                self.start(self._watch_dirs, self._callback, self._recursive)
            
            return True
        return False
    
    def remove_watch_dir(self, dir_path: str) -> bool:
        """
        移除监控目录
        
        Args:
            dir_path: 目录路径
            
        Returns:
            是否移除成功
        """
        if dir_path in self._watch_dirs:
            self._watch_dirs.remove(dir_path)
            logger.info(f"移除监控目录: {dir_path}")
            
            # 如果正在运行，需要重启监控
            if self._running:
                self.stop()
                if self._watch_dirs:
                    self.start(self._watch_dirs, self._callback, self._recursive)
            
            return True
        return False
    
    def get_watched_dirs(self) -> List[str]:
        """
        获取监控目录列表
        
        Returns:
            监控目录列表
        """
        return self._watch_dirs.copy()


def register():
    return {
        "name": "watchfiles",
        "version": "1.0.0",
        "interface": "FileMonitorEngine",
        "class": "WatchfilesMonitor",
        "dependencies": ["watchfiles"]
    }
