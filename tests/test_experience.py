"""
体验优化测试
"""

import pytest
import sys
from pathlib import Path

# 添加项目根目录到系统路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


class TestEnthusiasmService:
    """热情度服务测试类"""
    
    def test_import_enthusiasm_service(self):
        """测试导入热情度服务"""
        from services.enthusiasm_service import EnthusiasmManager, EnthusiasmLevel
        
        assert EnthusiasmManager is not None
        assert EnthusiasmLevel.MEDIUM.value == 3
    
    def test_enthusiasm_manager_initialization(self):
        """测试热情度管理器初始化"""
        from services.enthusiasm_service import EnthusiasmManager, EnthusiasmLevel
        
        manager = EnthusiasmManager(EnthusiasmLevel.HIGH)
        assert manager.level == EnthusiasmLevel.HIGH
    
    def test_enthusiasm_phrases(self):
        """测试热情度短语"""
        from services.enthusiasm_service import EnthusiasmManager, EnthusiasmLevel
        
        manager = EnthusiasmManager(EnthusiasmLevel.MEDIUM)
        phrase = manager.get_phrase()
        assert phrase is not None
        assert len(phrase) > 0
    
    def test_file_reminder(self):
        """测试文件提醒"""
        from services.enthusiasm_service import EnthusiasmManager, EnthusiasmLevel
        
        manager = EnthusiasmManager(EnthusiasmLevel.HIGH)
        reminder = manager.get_file_reminder("test.txt")
        assert "test.txt" in reminder
    
    def test_search_result(self):
        """测试搜索结果"""
        from services.enthusiasm_service import EnthusiasmManager, EnthusiasmLevel
        
        manager = EnthusiasmManager(EnthusiasmLevel.MEDIUM)
        result = manager.get_search_result(5)
        assert "5" in result


class TestDataExportService:
    """数据导出服务测试类"""
    
    def test_import_data_export_service(self):
        """测试导入数据导出服务"""
        from services.data_export_service import DataExportService
        
        assert DataExportService is not None
    
    def test_data_export_service_initialization(self):
        """测试数据导出服务初始化"""
        from services.data_export_service import DataExportService
        
        service = DataExportService()
        assert service.export_dir.exists()
    
    def test_get_data_summary(self):
        """测试获取数据摘要"""
        from services.data_export_service import DataExportService
        
        service = DataExportService()
        summary = service.get_data_summary()
        
        assert "config_exists" in summary
        assert "database_exists" in summary
        assert "voiceprint_exists" in summary


class TestThreeDModeSwitch:
    """三档模式切换测试类"""
    
    def test_config_performance_modes(self, tmp_path):
        """测试配置性能模式

        ⚠️ 必须用**临时配置文件**，不能碰真实 config.yaml。
        根因（实测踩到）：这里原先用 `get_config_manager()` 单例，
        而 `set_performance_mode("high")` 会**立即落盘**到真实 config.yaml，
        且本用例不还原 —— 于是跑一次测试就把用户的
        `system.performance_mode` 从 `low` 改成 `high`，
        连带把 `plugins.asr.params.model_size` 从 `base` 改成 `small`
        （高档在无 CUDA 的机器上回退 small），
        **ASR 每次识别慢 3 倍**，而所有日志看起来都正常。
        """
        from core.config_manager import ConfigManager

        cfg_path = tmp_path / "cfg.yaml"
        cfg_path.write_text("system:\n  performance_mode: low\n", encoding="utf-8")
        config_manager = ConfigManager(str(cfg_path))

        # 测试低档模式
        config_manager.set_performance_mode("low")
        assert config_manager.get_performance_mode() == "low"

        # 测试中档模式
        config_manager.set_performance_mode("medium")
        assert config_manager.get_performance_mode() == "medium"

        # 测试高档模式
        config_manager.set_performance_mode("high")
        assert config_manager.get_performance_mode() == "high"
    
    def test_hardware_detector_recommendation(self):
        """测试硬件检测器推荐"""
        from core.hardware_detector import get_hardware_detector
        
        detector = get_hardware_detector()
        info = detector.detect()
        
        assert info.recommended_mode in ["low", "medium", "high"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
