"""
插件加载器模块
负责扫描、加载和管理插件
"""

import os
import sys
import importlib
import logging
from typing import Dict, Any, Optional, List, Type
from pathlib import Path

from interfaces.asr import ASREngine, NullASR
from interfaces.tts import TTSEngine, NullTTS
from interfaces.llm import LLMEngine, NullLLM
from interfaces.embedding import EmbeddingEngine, NullEmbedding
from interfaces.vector_db import VectorDBEngine, NullVectorDB
from interfaces.voiceprint import VoiceprintEngine, NullVoiceprint
from interfaces.file_monitor import FileMonitorEngine, NullFileMonitor
from interfaces.avatar import AvatarEngine, NullAvatar

logger = logging.getLogger(__name__)


# 接口映射
INTERFACE_MAP = {
    "ASREngine": ASREngine,
    "TTSEngine": TTSEngine,
    "LLMEngine": LLMEngine,
    "EmbeddingEngine": EmbeddingEngine,
    "VectorDBEngine": VectorDBEngine,
    "VoiceprintEngine": VoiceprintEngine,
    "FileMonitorEngine": FileMonitorEngine,
    "AvatarEngine": AvatarEngine,
}

# 空对象映射
NULL_OBJECT_MAP = {
    "ASREngine": NullASR,
    "TTSEngine": NullTTS,
    "LLMEngine": NullLLM,
    "EmbeddingEngine": NullEmbedding,
    "VectorDBEngine": NullVectorDB,
    "VoiceprintEngine": NullVoiceprint,
    "FileMonitorEngine": NullFileMonitor,
    "AvatarEngine": NullAvatar,
}


class PluginInfo:
    """插件信息"""
    
    def __init__(self, name: str, version: str, interface: str, 
                 plugin_class: Type, dependencies: List[str]):
        self.name = name
        self.version = version
        self.interface = interface
        self.plugin_class = plugin_class
        self.dependencies = dependencies
        self.instance = None
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "name": self.name,
            "version": self.version,
            "interface": self.interface,
            "dependencies": self.dependencies
        }


class PluginLoader:
    """插件加载器"""
    
    def __init__(self, plugins_dir: str = "plugins"):
        """
        初始化插件加载器
        
        Args:
            plugins_dir: 插件目录路径
        """
        self.plugins_dir = Path(plugins_dir)
        self.plugins: Dict[str, PluginInfo] = {}
        self.loaded_plugins: Dict[str, Any] = {}
        
        # 确保插件目录存在
        self.plugins_dir.mkdir(parents=True, exist_ok=True)
    
    def scan(self) -> List[Dict[str, Any]]:
        """
        扫描插件目录，获取所有可用插件
        
        Returns:
            插件信息列表
        """
        plugin_list = []
        
        # 遍历插件类型目录
        for plugin_type_dir in self.plugins_dir.iterdir():
            if not plugin_type_dir.is_dir():
                continue
            
            # 遍历具体插件目录
            for plugin_dir in plugin_type_dir.iterdir():
                if not plugin_dir.is_dir():
                    continue
                
                plugin_file = plugin_dir / "plugin.py"
                if plugin_file.exists():
                    try:
                        info = self._load_plugin_info(plugin_dir)
                        if info:
                            self.plugins[info.name] = info
                            plugin_list.append(info.to_dict())
                            logger.info(f"发现插件: {info.name} v{info.version}")
                    except Exception as e:
                        logger.error(f"加载插件信息失败 {plugin_dir}: {e}")
        
        return plugin_list
    
    def _load_plugin_info(self, plugin_dir: Path) -> Optional[PluginInfo]:
        """
        加载插件信息
        
        Args:
            plugin_dir: 插件目录
            
        Returns:
            插件信息，失败返回None
        """
        plugin_file = plugin_dir / "plugin.py"
        
        # 动态导入插件模块
        module_name = f"plugins.{plugin_dir.parent.name}.{plugin_dir.name}.plugin"
        spec = importlib.util.spec_from_file_location(module_name, plugin_file)
        
        if spec is None or spec.loader is None:
            return None
        
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        
        try:
            spec.loader.exec_module(module)
        except Exception as e:
            logger.error(f"执行插件模块失败 {plugin_file}: {e}")
            return None
        
        # 获取注册信息
        if not hasattr(module, 'register'):
            logger.error(f"插件 {plugin_dir.name} 缺少 register() 函数")
            return None
        
        register_info = module.register()
        
        # 验证必要字段
        required_fields = ["name", "version", "interface", "class"]
        for field in required_fields:
            if field not in register_info:
                logger.error(f"插件 {plugin_dir.name} 缺少必要字段: {field}")
                return None
        
        # 获取插件类
        plugin_class_name = register_info["class"]
        if not hasattr(module, plugin_class_name):
            logger.error(f"插件 {plugin_dir.name} 中找不到类: {plugin_class_name}")
            return None
        
        plugin_class = getattr(module, plugin_class_name)
        
        return PluginInfo(
            name=register_info["name"],
            version=register_info["version"],
            interface=register_info["interface"],
            plugin_class=plugin_class,
            dependencies=register_info.get("dependencies", [])
        )
    
    def load(self, plugin_name: str, params: dict = None) -> Optional[Any]:
        """
        加载指定插件
        
        Args:
            plugin_name: 插件名称
            params: 插件初始化参数
            
        Returns:
            插件实例，失败返回None
        """
        if plugin_name not in self.plugins:
            logger.error(f"插件不存在: {plugin_name}")
            return None
        
        # 检查是否已加载
        if plugin_name in self.loaded_plugins:
            return self.loaded_plugins[plugin_name]
        
        plugin_info = self.plugins[plugin_name]
        
        try:
            # 创建插件实例，传入参数
            if params:
                instance = plugin_info.plugin_class(**params)
            else:
                instance = plugin_info.plugin_class()
            plugin_info.instance = instance
            self.loaded_plugins[plugin_name] = instance
            
            logger.info(f"插件加载成功: {plugin_name}")
            return instance
            
        except Exception as e:
            logger.error(f"加载插件失败 {plugin_name}: {e}")
            return None
    
    def load_by_interface(self, interface_name: str) -> Any:
        """
        根据接口类型加载插件（使用空对象模式）
        
        Args:
            interface_name: 接口名称
            
        Returns:
            插件实例或空对象
        """
        # 查找匹配接口的插件
        for plugin_info in self.plugins.values():
            if plugin_info.interface == interface_name:
                instance = self.load(plugin_info.name)
                if instance:
                    return instance
        
        # 如果没有找到或加载失败，返回空对象
        if interface_name in NULL_OBJECT_MAP:
            logger.warning(f"未找到接口 {interface_name} 的实现，使用空对象")
            return NULL_OBJECT_MAP[interface_name]()
        
        logger.error(f"未知的接口类型: {interface_name}")
        return None
    
    def get_info(self, plugin_name: str) -> Optional[Dict[str, Any]]:
        """
        获取插件信息
        
        Args:
            plugin_name: 插件名称
            
        Returns:
            插件信息字典
        """
        if plugin_name in self.plugins:
            return self.plugins[plugin_name].to_dict()
        return None
    
    def get_loaded_plugins(self) -> List[str]:
        """
        获取已加载的插件列表
        
        Returns:
            已加载的插件名称列表
        """
        return list(self.loaded_plugins.keys())
    
    def unload(self, plugin_name: str) -> bool:
        """
        卸载插件
        
        Args:
            plugin_name: 插件名称
            
        Returns:
            是否卸载成功
        """
        if plugin_name in self.loaded_plugins:
            del self.loaded_plugins[plugin_name]
            if plugin_name in self.plugins:
                self.plugins[plugin_name].instance = None
            logger.info(f"插件已卸载: {plugin_name}")
            return True
        return False
    
    def reload(self, plugin_name: str) -> Optional[Any]:
        """
        重新加载插件
        
        Args:
            plugin_name: 插件名称
            
        Returns:
            插件实例，失败返回None
        """
        self.unload(plugin_name)
        return self.load(plugin_name)


# 全局插件加载器实例
_plugin_loader: Optional[PluginLoader] = None


def get_plugin_loader(plugins_dir: str = "plugins") -> PluginLoader:
    """
    获取插件加载器单例
    
    Args:
        plugins_dir: 插件目录路径
        
    Returns:
        插件加载器实例
    """
    global _plugin_loader
    
    if _plugin_loader is None:
        _plugin_loader = PluginLoader(plugins_dir)
    
    return _plugin_loader
