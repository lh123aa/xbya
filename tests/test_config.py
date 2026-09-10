"""
配置管理器测试
"""

import pytest
import tempfile
import os
import yaml
from pathlib import Path

import sys
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from core.config_manager import ConfigManager


class TestConfigManager:
    """配置管理器测试类"""
    
    def setup_method(self):
        """测试前设置"""
        self.config_path = project_root / "config.yaml"
        self.manager = ConfigManager(str(self.config_path))
    
    def test_load_config(self):
        """测试加载配置"""
        config = self.manager.config
        assert config is not None
        assert "app" in config
        assert "system" in config
        assert "plugins" in config
    
    def test_get_performance_mode(self):
        """测试获取性能模式"""
        mode = self.manager.get_performance_mode()
        assert mode in ["low", "medium", "high"]
    
    def test_set_performance_mode(self):
        """测试设置性能模式"""
        # 保存原始模式
        original_mode = self.manager.get_performance_mode()
        
        try:
            # 测试设置为high
            self.manager.set_performance_mode("high")
            assert self.manager.get_performance_mode() == "high"
            
            # 测试设置为low
            self.manager.set_performance_mode("low")
            assert self.manager.get_performance_mode() == "low"
            
        finally:
            # 恢复原始模式
            self.manager.set_performance_mode(original_mode)
    
    def test_get_plugin_config(self):
        """测试获取插件配置"""
        asr_config = self.manager.get_plugin_config("asr")
        assert asr_config is not None
        assert "engine" in asr_config
        assert "params" in asr_config
    
    def test_get_ui_config(self):
        """测试获取UI配置"""
        ui_config = self.manager.get_ui_config()
        assert ui_config is not None
        assert "pet_size" in ui_config
    
    def test_get_voice_config(self):
        """测试获取语音配置"""
        voice_config = self.manager.get_voice_config()
        assert voice_config is not None
        assert "wake_word" in voice_config
    
    def test_invalid_performance_mode(self):
        """测试无效性能模式"""
        with pytest.raises(ValueError):
            self.manager.set_performance_mode("invalid")
    
    def test_default_config_creation(self):
        """测试默认配置创建"""
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.yaml') as f:
            temp_path = f.name
        
        try:
            # 删除临时文件
            os.unlink(temp_path)
            
            # 创建新的配置管理器
            manager = ConfigManager(temp_path)
            
            # 验证默认配置被创建
            assert os.path.exists(temp_path)
            assert manager.config is not None
            
        finally:
            if os.path.exists(temp_path):
                os.unlink(temp_path)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
