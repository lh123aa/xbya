"""
配置管理模块
负责加载、解析和管理系统配置
"""

import os
import yaml
import logging
from typing import Dict, Any, Optional
from pathlib import Path

logger = logging.getLogger(__name__)


class ConfigManager:
    """配置管理器"""
    
    # 默认配置
    DEFAULT_CONFIG = {
        "app": {
            "name": "小忆",
            "version": "1.0.0"
        },
        "system": {
            "performance_mode": "medium"  # low | medium | high
        },
        "plugins": {
            "asr": {
                "engine": "faster_whisper",
                "params": {
                    "model_size": "small",
                    "device": "cpu",
                    "compute_type": "int8"
                }
            },
            "tts": {
                "engine": "edge_tts",
                "params": {
                    "voice": "zh-CN-XiaoxiaoNeural"
                }
            },
            "llm": {
                "engine": "ollama",
                "params": {
                    "model": "qwen2.5:1.5b",
                    "base_url": "http://localhost:11434"
                }
            },
            "embedding": {
                "engine": "embed_anything",
                "params": {
                    "model": "BAAI/bge-small-zh"
                }
            },
            "vector_db": {
                "engine": "leann",
                "params": {
                    "storage_path": "./data/vectordb"
                }
            },
            "file_monitor": {
                "engine": "watchfiles",
                "params": {
                    "watch_dirs": ["~/Desktop", "~/Downloads"],
                    "recursive": True
                }
            },
            "voiceprint": {
                "engine": "3d_speaker",
                "params": {
                    "model_name": "ERes2Net",
                    "threshold": 0.7
                }
            },
            "avatar": {
                "engine": "liveportrait",
                "params": {
                    "resolution": 256,
                    "fps": 10
                }
            }
        },
        "ui": {
            "pet_size": 200,
            # 精灵图角色目录名（resources/sprites/<pet_sprite>/）。
            # 默认 "cat" 保持改动前行为；换成自己的角色只需改这一项。
            "pet_sprite": "cat",
            # 渲染模式：sprite（2D 序列帧）| vrm（3D 模型）。
            # 注意：以前这一项**从未被读取** —— PetWindow.render_mode 硬编码为 "sprite"，
            # 配置里写 vrm 只是碰巧因为 app.py 无条件调 enable_vrm() 才生效。
            # 现在 app.py 会按它决定是否启用 VRM，配置与行为一致。
            "render_mode": "sprite",
            "fps": {
                "low": 15,
                "medium": 30,
                "high": 60
            },
            "position": {
                "x": 100,
                "y": 100
            }
        },
        "voice": {
            "wake_word": "嘿欣雅",
            "enthusiasm_level": 3,  # 1-5
            "confirm_delete": True,
            "hotkey_enabled": True,
            "hotkey_toggle": "Ctrl+Alt+M",
            "hotkey_mute": "Ctrl+Alt+S",
            "hotkey_interrupt": "Ctrl+Alt+D"
        }
    }
    
    def __init__(self, config_path: str = "config.yaml"):
        """
        初始化配置管理器
        
        Args:
            config_path: 配置文件路径
        """
        self.config_path = Path(config_path)
        self.config: Dict[str, Any] = {}
        self._load_config()
    
    def _load_config(self) -> None:
        """加载配置文件"""
        try:
            if self.config_path.exists():
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    self.config = yaml.safe_load(f) or {}
                logger.info(f"配置文件加载成功: {self.config_path}")
                
                # 合并默认配置（确保所有键都存在）
                self._merge_config(self.DEFAULT_CONFIG, self.config)
            else:
                logger.warning(f"配置文件不存在: {self.config_path}，使用默认配置")
                self.config = self.DEFAULT_CONFIG.copy()
                self._save_config()  # 保存默认配置到文件
                
        except Exception as e:
            logger.error(f"加载配置文件失败: {e}")
            self.config = self.DEFAULT_CONFIG.copy()
    
    def _merge_config(self, default: Dict, target: Dict) -> None:
        """递归合并配置"""
        for key, value in default.items():
            if key not in target:
                target[key] = value
            elif isinstance(value, dict) and isinstance(target[key], dict):
                self._merge_config(value, target[key])
    
    def _save_config(self) -> None:
        """保存配置到文件"""
        try:
            # 确保目录存在
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            
            with open(self.config_path, 'w', encoding='utf-8') as f:
                yaml.dump(self.config, f, allow_unicode=True, default_flow_style=False)
            logger.info(f"配置文件保存成功: {self.config_path}")
            
        except Exception as e:
            logger.error(f"保存配置文件失败: {e}")
    
    def get(self, key: str, default: Any = None) -> Any:
        """
        获取配置值
        
        Args:
            key: 配置键（支持点号分隔，如'plugins.asr.engine'）
            default: 默认值
            
        Returns:
            配置值
        """
        keys = key.split('.')
        value = self.config
        
        for k in keys:
            if isinstance(value, dict) and k in value:
                value = value[k]
            else:
                return default
        
        return value
    
    def set(self, key: str, value: Any) -> None:
        """
        设置配置值
        
        Args:
            key: 配置键（支持点号分隔）
            value: 配置值
        """
        keys = key.split('.')
        config = self.config
        
        for k in keys[:-1]:
            if k not in config:
                config[k] = {}
            config = config[k]
        
        config[keys[-1]] = value
        self._save_config()
    
    def get_performance_mode(self) -> str:
        """
        获取当前性能模式
        
        Returns:
            性能模式：low, medium, high
        """
        return self.get("system.performance_mode", "medium")
    
    def set_performance_mode(self, mode: str) -> None:
        """
        设置性能模式
        
        Args:
            mode: 性能模式：low, medium, high
        """
        if mode not in ["low", "medium", "high"]:
            raise ValueError(f"无效的性能模式: {mode}")
        
        # 同模式跳过：防止重复调用时覆盖用户已自定义的模型配置
        if mode == self.get_performance_mode() and mode != "auto":
            logger.info(f"性能模式已为 {mode}，跳过应用（保留自定义模型配置）")
            return
        
        self.set("system.performance_mode", mode)
        
        # 根据模式调整插件参数
        self._apply_mode_settings(mode)
    
    def _apply_mode_settings(self, mode: str) -> None:
        """根据性能模式应用设置

        注意：只调整ASR模型、ASR设备、UI帧率这类资源敏感参数。
        **绝不修改 llm.engine / llm.params.model**——用户自定义的模型选择
        优先（历史上 low 模式禁用 LLM 曾导致桌宠核心对话功能被关闭）。

        档位→ASR 模型映射（本地 CPU 保守；高档若检测到可用 GPU 才尝试更大的模型）：
          low    -> tiny  (最快，最省资源)
          medium -> small (平衡)
          high   -> medium(更准，需较强者力/显存)
        """
        if mode == "low":
            # low：小模型求快，但保留 base 保证可识别度（不再用 tiny，避免"听不见"）
            self.set("plugins.asr.params.model_size", "base")
            self.set("plugins.asr.params.device", "cpu")
            self.set("ui.fps.current", 30)

        elif mode == "medium":
            self.set("plugins.asr.params.model_size", "small")
            self.set("plugins.asr.params.device", "cpu")
            self.set("ui.fps.current", 30)

        elif mode == "high":
            # 高档：根据硬件决定是否能用更大模型/GPU；否则回退小模型CPU
            # 保守：仍以 CPU 为主，用 medium 提升准确（实际加载时会按资源回退）
            self.set("plugins.asr.params.model_size", "medium")
            self.set("plugins.asr.params.device", "cpu")
            self.set("ui.fps.current", 60)
    
    def get_plugin_config(self, plugin_type: str) -> Dict[str, Any]:
        """
        获取插件配置
        
        Args:
            plugin_type: 插件类型（asr, tts, llm等）
            
        Returns:
            插件配置字典
        """
        return self.get(f"plugins.{plugin_type}", {})
    
    def get_ui_config(self) -> Dict[str, Any]:
        """
        获取UI配置
        
        Returns:
            UI配置字典
        """
        return self.get("ui", {})
    
    def get_voice_config(self) -> Dict[str, Any]:
        """
        获取语音配置
        
        Returns:
            语音配置字典
        """
        return self.get("voice", {})
    
    def reload(self) -> None:
        """重新加载配置"""
        self._load_config()


# 全局配置实例
_config_manager: Optional[ConfigManager] = None


def get_config_manager(config_path: str = "config.yaml") -> ConfigManager:
    """
    获取配置管理器单例
    
    Args:
        config_path: 配置文件路径
        
    Returns:
        配置管理器实例
    """
    global _config_manager
    
    if _config_manager is None:
        _config_manager = ConfigManager(config_path)
    
    return _config_manager
