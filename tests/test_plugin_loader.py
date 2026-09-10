"""
插件加载器测试
"""

import pytest
import sys
from pathlib import Path

# 添加项目根目录到系统路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from core.plugin_loader import PluginLoader


class TestPluginLoader:
    """插件加载器测试类"""
    
    def setup_method(self):
        """测试前设置"""
        self.loader = PluginLoader(str(project_root / "plugins"))
    
    def test_scan_plugins(self):
        """测试扫描插件"""
        plugins = self.loader.scan()
        assert isinstance(plugins, list)
        # 至少应该有一些插件（即使只是示例）
        print(f"发现 {len(plugins)} 个插件")
    
    def test_load_nonexistent_plugin(self):
        """测试加载不存在的插件"""
        result = self.loader.load("nonexistent_plugin")
        assert result is None
    
    def test_get_info_nonexistent(self):
        """测试获取不存在插件的信息"""
        result = self.loader.get_info("nonexistent_plugin")
        assert result is None
    
    def test_load_by_interface_null_object(self):
        """测试通过接口加载空对象"""
        # 测试加载ASREngine的空对象
        asr = self.loader.load_by_interface("ASREngine")
        assert asr is not None
        assert hasattr(asr, 'transcribe')
        
        # 测试加载TTSEngine的空对象
        tts = self.loader.load_by_interface("TTSEngine")
        assert tts is not None
        assert hasattr(tts, 'speak')
        
        # 测试加载LLMEngine的空对象
        llm = self.loader.load_by_interface("LLMEngine")
        assert llm is not None
        assert hasattr(llm, 'chat')
    
    def test_plugin_interface_mapping(self):
        """测试插件接口映射"""
        from interfaces.asr import ASREngine
        from interfaces.tts import TTSEngine
        from interfaces.llm import LLMEngine
        from interfaces.embedding import EmbeddingEngine
        from interfaces.vector_db import VectorDBEngine
        from interfaces.voiceprint import VoiceprintEngine
        from interfaces.file_monitor import FileMonitorEngine
        from interfaces.avatar import AvatarEngine
        
        # 验证接口类存在
        assert ASREngine is not None
        assert TTSEngine is not None
        assert LLMEngine is not None
        assert EmbeddingEngine is not None
        assert VectorDBEngine is not None
        assert VoiceprintEngine is not None
        assert FileMonitorEngine is not None
        assert AvatarEngine is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
