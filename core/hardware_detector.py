"""
硬件检测模块
负责检测系统硬件配置，推荐性能模式
"""

import platform
import logging
from typing import Dict, Any, Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class HardwareInfo:
    """硬件信息"""
    cpu_count: int
    memory_gb: float
    has_gpu: bool
    gpu_name: Optional[str]
    gpu_memory_gb: Optional[float]
    recommended_mode: str


class HardwareDetector:
    """硬件检测器"""
    
    def __init__(self):
        self._hardware_info: Optional[HardwareInfo] = None
    
    def detect(self) -> HardwareInfo:
        """
        检测硬件信息
        
        Returns:
            硬件信息
        """
        if self._hardware_info is not None:
            return self._hardware_info
        
        try:
            import psutil
            
            # CPU信息
            cpu_count = psutil.cpu_count(logical=True)
            
            # 内存信息
            memory = psutil.virtual_memory()
            memory_gb = memory.total / (1024 ** 3)
            
            # GPU信息
            has_gpu, gpu_name, gpu_memory_gb = self._detect_gpu()
            
            # 推荐性能模式
            recommended_mode = self._recommend_mode(memory_gb, has_gpu, gpu_memory_gb)
            
            self._hardware_info = HardwareInfo(
                cpu_count=cpu_count,
                memory_gb=memory_gb,
                has_gpu=has_gpu,
                gpu_name=gpu_name,
                gpu_memory_gb=gpu_memory_gb,
                recommended_mode=recommended_mode
            )
            
            logger.info(f"硬件检测完成: CPU={cpu_count}核, 内存={memory_gb:.1f}GB, "
                       f"GPU={'有' if has_gpu else '无'}({gpu_name}), "
                       f"推荐模式={recommended_mode}")
            
            return self._hardware_info
            
        except ImportError:
            logger.warning("psutil未安装，使用默认硬件信息")
            return self._get_default_hardware_info()
        except Exception as e:
            logger.error(f"硬件检测失败: {e}")
            return self._get_default_hardware_info()
    
    def _detect_gpu(self) -> tuple[bool, Optional[str], Optional[float]]:
        """
        检测GPU信息
        
        Returns:
            (是否有GPU, GPU名称, 显存GB)
        """
        try:
            import torch
            if torch.cuda.is_available():
                gpu_name = torch.cuda.get_device_name(0)
                gpu_memory = torch.cuda.get_device_properties(0).total_mem / (1024 ** 3)
                return True, gpu_name, gpu_memory
        except (ImportError, RuntimeError):
            pass
        
        # 尝试使用nvidia-smi（Windows）
        try:
            import subprocess
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0:
                lines = result.stdout.strip().split('\n')
                if lines:
                    parts = lines[0].split(',')
                    if len(parts) >= 2:
                        gpu_name = parts[0].strip()
                        gpu_memory_mb = float(parts[1].strip())
                        return True, gpu_name, gpu_memory_mb / 1024
        except (FileNotFoundError, subprocess.TimeoutExpired, ValueError):
            pass
        
        return False, None, None
    
    def _recommend_mode(self, memory_gb: float, has_gpu: bool, gpu_memory_gb: Optional[float]) -> str:
        """
        推荐性能模式
        
        Args:
            memory_gb: 内存GB
            has_gpu: 是否有GPU
            gpu_memory_gb: 显存GB
            
        Returns:
            推荐的性能模式
        """
        # 高档：24GB+内存 + 8GB+显存
        if memory_gb >= 24 and has_gpu and gpu_memory_gb and gpu_memory_gb >= 8:
            return "high"
        
        # 中档：12GB+内存 + 4GB+显存
        if memory_gb >= 12 and has_gpu and gpu_memory_gb and gpu_memory_gb >= 4:
            return "medium"
        
        # 低档：其他配置
        return "low"
    
    def _get_default_hardware_info(self) -> HardwareInfo:
        """获取默认硬件信息"""
        return HardwareInfo(
            cpu_count=4,
            memory_gb=8.0,
            has_gpu=False,
            gpu_name=None,
            gpu_memory_gb=None,
            recommended_mode="low"
        )
    
    def get_hardware_summary(self) -> str:
        """
        获取硬件摘要信息
        
        Returns:
            硬件摘要字符串
        """
        info = self.detect()
        
        summary = [
            f"CPU核心数: {info.cpu_count}",
            f"内存大小: {info.memory_gb:.1f} GB",
            f"GPU: {'有' if info.has_gpu else '无'}",
        ]
        
        if info.has_gpu:
            summary.append(f"GPU型号: {info.gpu_name}")
            if info.gpu_memory_gb:
                summary.append(f"显存大小: {info.gpu_memory_gb:.1f} GB")
        
        summary.append(f"推荐性能模式: {info.recommended_mode}")
        
        return "\n".join(summary)


# 全局硬件检测器实例
_hardware_detector: Optional[HardwareDetector] = None


def get_hardware_detector() -> HardwareDetector:
    """
    获取硬件检测器单例
    
    Returns:
        硬件检测器实例
    """
    global _hardware_detector
    
    if _hardware_detector is None:
        _hardware_detector = HardwareDetector()
    
    return _hardware_detector
