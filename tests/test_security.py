"""
安全与个性化测试
"""

import pytest
import sys
from pathlib import Path

# 添加项目根目录到系统路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


class TestThreeDSpeakerVoiceprint:
    """3D-Speaker声纹识别测试类"""
    
    def test_import_three_d_speaker_plugin(self):
        """测试导入3D-Speaker插件"""
        from plugins.voiceprint.three_d_speaker.plugin import ThreeDSpeakerVoiceprint
        
        assert ThreeDSpeakerVoiceprint is not None
    
    def test_three_d_speaker_initialization(self, tmp_path):
        """测试3D-Speaker初始化（用临时存储目录，避免读到磁盘上的既有声纹）"""
        from plugins.voiceprint.three_d_speaker.plugin import ThreeDSpeakerVoiceprint
        
        voiceprint = ThreeDSpeakerVoiceprint(storage_dir=str(tmp_path / "vp"))
        assert voiceprint.model_name is not None
        # 阈值默认 0.75（与 config.yaml plugins.voiceprint.params.threshold 一致）
        assert voiceprint.threshold == 0.75
        assert voiceprint.voiceprint_db == {}
    
    def test_three_d_speaker_enroll(self, tmp_path):
        """测试声纹注册（用真实可解码的 wav，空文件会被解码器拒绝）"""
        from plugins.voiceprint.three_d_speaker.plugin import ThreeDSpeakerVoiceprint
        import wave
        import struct
        import math
        
        voiceprint = ThreeDSpeakerVoiceprint(storage_dir=str(tmp_path / "vp"))
        
        # 生成一段 1 秒 16kHz 单声道正弦波 wav（有效音频，非空文件）
        temp_path = str(tmp_path / "sample.wav")
        with wave.open(temp_path, 'wb') as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            frames = b''.join(
                struct.pack('<h', int(16000 * math.sin(2 * math.pi * 220 * t / 16000)))
                for t in range(16000)
            )
            wf.writeframes(frames)
        
        # 注册声纹
        result = voiceprint.enroll(temp_path, "test_user")
        assert result is True
        assert "test_user" in voiceprint.voiceprint_db
    
    def test_three_d_speaker_verify(self):
        """测试声纹验证"""
        from plugins.voiceprint.three_d_speaker.plugin import ThreeDSpeakerVoiceprint
        import numpy as np
        
        voiceprint = ThreeDSpeakerVoiceprint()
        
        # 直接设置声纹特征
        features = np.random.randn(192)
        features = features / np.linalg.norm(features)
        voiceprint.voiceprint_db["test_user"] = features
        
        # 测试余弦相似度计算
        similarity = voiceprint._cosine_similarity(features, features)
        assert abs(similarity - 1.0) < 1e-6  # 相同向量相似度为1（允许浮点误差）
        
        # 测试阈值判断
        assert similarity >= voiceprint.threshold


class TestLivePortraitAvatar:
    """LivePortrait形象生成测试类"""
    
    def test_import_liveportrait_plugin(self):
        """测试导入LivePortrait插件"""
        from plugins.avatar.liveportrait.plugin import LivePortraitAvatar
        
        assert LivePortraitAvatar is not None
    
    def test_liveportrait_initialization(self):
        """测试LivePortrait初始化"""
        from plugins.avatar.liveportrait.plugin import LivePortraitAvatar
        
        avatar = LivePortraitAvatar()
        assert avatar.resolution == 256
        assert avatar.fps == 10
    
    def test_liveportrait_supported_actions(self):
        """测试支持的动作"""
        from plugins.avatar.liveportrait.plugin import LivePortraitAvatar
        
        avatar = LivePortraitAvatar()
        actions = avatar.get_supported_actions()
        
        assert isinstance(actions, list)
        assert "idle" in actions
        assert "listen" in actions
        assert "think" in actions


class TestSecurityFeatures:
    """安全功能测试类"""
    
    def test_event_bus_security_events(self):
        """测试事件总线安全事件"""
        from core.event_bus import get_event_bus, EventType
        
        event_bus = get_event_bus()
        
        # 测试安全事件
        assert hasattr(EventType, 'SECURITY_VERIFY')
        assert hasattr(EventType, 'SECURITY_CONFIRM')
        assert hasattr(EventType, 'SECURITY_DENY')
    
    def test_config_security_settings(self):
        """测试配置安全设置"""
        from core.config_manager import get_config_manager
        
        config_manager = get_config_manager()
        
        # 测试语音配置
        voice_config = config_manager.get_voice_config()
        assert "confirm_delete" in voice_config
        assert voice_config["confirm_delete"] is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
