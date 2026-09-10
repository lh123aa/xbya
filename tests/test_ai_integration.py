"""
AI集成测试
"""

import pytest
import sys
from pathlib import Path

# 添加项目根目录到系统路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


class TestOllamaLLM:
    """Ollama LLM测试类"""
    
    def test_import_ollama_plugin(self):
        """测试导入Ollama插件"""
        from plugins.llm.ollama.plugin import OllamaLLM
        
        assert OllamaLLM is not None
    
    def test_ollama_initialization(self):
        """测试Ollama初始化"""
        from plugins.llm.ollama.plugin import OllamaLLM
        
        # 尝试初始化（可能失败，因为Ollama服务可能未运行）
        llm = OllamaLLM()
        assert llm.model is not None
        assert llm.base_url is not None
    
    def test_ollama_model_info(self):
        """测试获取模型信息"""
        from plugins.llm.ollama.plugin import OllamaLLM
        
        llm = OllamaLLM()
        info = llm.get_model_info()
        
        assert "name" in info
        assert "base_url" in info
        assert "available" in info


class TestFasterWhisperASR:
    """Faster Whisper ASR测试类"""
    
    def test_import_faster_whisper_plugin(self):
        """测试导入Faster Whisper插件"""
        from plugins.asr.faster_whisper.plugin import FasterWhisperASR
        
        assert FasterWhisperASR is not None
    
    def test_faster_whisper_initialization(self):
        """测试Faster Whisper初始化"""
        from plugins.asr.faster_whisper.plugin import FasterWhisperASR
        
        # 尝试初始化（可能失败，因为模型可能未下载）
        asr = FasterWhisperASR()
        assert asr.model_size is not None
        assert asr.device is not None
    
    def test_faster_whisper_supported_formats(self):
        """测试支持的音频格式"""
        from plugins.asr.faster_whisper.plugin import FasterWhisperASR
        
        asr = FasterWhisperASR()
        formats = asr.get_supported_formats()
        
        assert isinstance(formats, list)
        assert "wav" in formats
        assert "mp3" in formats


class TestEdgeTTS:
    """Edge TTS测试类"""
    
    def test_import_edge_tts_plugin(self):
        """测试导入Edge TTS插件"""
        from plugins.tts.edge_tts.plugin import EdgeTTS
        
        assert EdgeTTS is not None
    
    def test_edge_tts_initialization(self):
        """测试Edge TTS初始化"""
        from plugins.tts.edge_tts.plugin import EdgeTTS
        
        tts = EdgeTTS()
        assert tts.voice is not None
    
    def test_edge_tts_set_voice(self):
        """测试设置语音"""
        from plugins.tts.edge_tts.plugin import EdgeTTS
        
        tts = EdgeTTS()
        result = tts.set_voice("zh-CN-YunxiNeural")
        assert result is True
        assert tts.voice == "zh-CN-YunxiNeural"


class TestWatchfilesMonitor:
    """Watchfiles文件监控测试类"""
    
    def test_import_watchfiles_plugin(self):
        """测试导入Watchfiles插件"""
        from plugins.file_monitor.watchfiles.plugin import WatchfilesMonitor
        
        assert WatchfilesMonitor is not None
    
    def test_watchfiles_initialization(self):
        """测试Watchfiles初始化"""
        from plugins.file_monitor.watchfiles.plugin import WatchfilesMonitor
        
        monitor = WatchfilesMonitor()
        assert monitor._running is False
        assert monitor._watch_dirs == []
    
    def test_watchfiles_watched_dirs(self):
        """测试监控目录管理"""
        from plugins.file_monitor.watchfiles.plugin import WatchfilesMonitor
        
        monitor = WatchfilesMonitor()
        
        # 添加监控目录
        monitor._watch_dirs.append("/test/dir")
        assert "/test/dir" in monitor.get_watched_dirs()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

class TestPetName:
    def test_set_pet_name_injects_persona(self):
        """set_pet_name 更新人设：名字注入 system_prompt"""
        from plugins.llm.ollama.plugin import OllamaLLM
        llm = OllamaLLM.__new__(OllamaLLM)
        llm.system_prompt = None
        llm.set_pet_name("欣雅")
        assert "欣雅" in llm.system_prompt

    def test_set_pet_name_empty_fallback(self):
        """空名字回退默认欣雅"""
        from plugins.llm.ollama.plugin import OllamaLLM
        llm = OllamaLLM.__new__(OllamaLLM)
        llm.set_pet_name("")
        assert "欣雅" in llm.system_prompt
