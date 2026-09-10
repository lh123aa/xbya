"""
硬件检测器测试
"""

import pytest

import sys
from pathlib import Path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from core.hardware_detector import HardwareDetector


class TestHardwareDetector:
    """硬件检测器测试类"""
    
    def setup_method(self):
        """测试前设置"""
        self.detector = HardwareDetector()
    
    def test_detect_hardware(self):
        """测试检测硬件"""
        info = self.detector.detect()
        
        assert info is not None
        assert info.cpu_count > 0
        assert info.memory_gb > 0
        assert info.recommended_mode in ["low", "medium", "high"]
    
    def test_hardware_summary(self):
        """测试硬件摘要"""
        summary = self.detector.get_hardware_summary()
        
        assert summary is not None
        assert "CPU核心数" in summary
        assert "内存大小" in summary
        assert "推荐性能模式" in summary
    
    def test_recommend_mode(self):
        """测试推荐模式"""
        # 测试不同配置的推荐模式
        # 低配
        mode = self.detector._recommend_mode(4, False, None)
        assert mode == "low"
        
        # 中配
        mode = self.detector._recommend_mode(16, True, 4)
        assert mode == "medium"
        
        # 高配
        mode = self.detector._recommend_mode(32, True, 8)
        assert mode == "high"
    
    def test_default_hardware_info(self):
        """测试默认硬件信息"""
        info = self.detector._get_default_hardware_info()
        
        assert info is not None
        assert info.cpu_count == 4
        assert info.memory_gb == 8.0
        assert info.has_gpu is False
        assert info.recommended_mode == "low"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
