"""
语音服务测试
"""

import pytest
import sys
from pathlib import Path

# 添加项目根目录到系统路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


class TestVoiceService:
    """语音服务测试类"""
    
    def test_import_voice_service(self):
        """测试导入语音服务模块"""
        from services.voice_service import VoiceService, get_voice_service
        
        assert VoiceService is not None
        assert get_voice_service is not None
    
    def test_voice_service_initialization(self):
        """测试语音服务初始化"""
        from services.voice_service import VoiceService
        
        service = VoiceService()
        assert service.asr is None
        assert service.tts is None
        assert service.recording is False


class TestFileService:
    """文件服务测试类"""
    
    def test_import_file_service(self):
        """测试导入文件服务模块"""
        from services.file_service import FileService, get_file_service
        
        assert FileService is not None
        assert get_file_service is not None
    
    def test_file_service_initialization(self):
        """测试文件服务初始化"""
        from services.file_service import FileService
        
        service = FileService()
        assert service.file_monitor is None
        assert service.embedding is None
        assert service.vector_db is None
        assert service.watch_dirs == []


class TestAIService:
    """AI服务测试类"""
    
    def test_import_ai_service(self):
        """测试导入AI服务模块"""
        from services.ai_service import AIService, get_ai_service
        
        assert AIService is not None
        assert get_ai_service is not None
    
    def test_ai_service_initialization(self):
        """测试AI服务初始化"""
        from services.ai_service import AIService
        
        service = AIService()
        assert service.llm is None
        assert service.conversation_history == []
        assert service.system_prompt is not None
    
    def test_process_command(self):
        """测试处理命令"""
        from services.ai_service import AIService
        
        service = AIService()
        
        # 测试文件操作命令
        result = service.process_command("打开文件")
        assert result["success"] is True
        assert result["action"] == "open_file"
        
        result = service.process_command("删除文件")
        assert result["success"] is True
        assert result["action"] == "delete_file"
        
        result = service.process_command("查找文件")
        assert result["success"] is True
        assert result["action"] == "search_file"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
