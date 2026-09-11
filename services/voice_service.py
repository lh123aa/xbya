"""
语音服务模块
负责语音识别和语音合成
"""

import logging
import tempfile
import os
from typing import Optional, Callable
from pathlib import Path

from core.event_bus import get_event_bus, EventType
from core.plugin_loader import get_plugin_loader
from core.config_manager import get_config_manager
from core.plugin_params import plugin_params as plugin_params_for

logger = logging.getLogger(__name__)


class VoiceService:
    """语音服务"""
    
    def __init__(self):
        """初始化语音服务"""
        self.event_bus = get_event_bus()
        self.plugin_loader = get_plugin_loader()
        self.config_manager = get_config_manager()
        
        # 插件实例
        self.asr = None
        self.tts = None
        
        # 录音相关
        self.recording = False
        self.audio_buffer = []
        
        # 回调函数
        self.on_speech_recognized: Optional[Callable[[str], None]] = None
        self.on_speech_synthesized: Optional[Callable[[bytes], None]] = None
        
        logger.info("语音服务初始化")
    
    def initialize(self) -> bool:
        """
        初始化语音插件
        
        Returns:
            是否初始化成功
        """
        try:
            # 加载ASR插件
            #
            # 参数**必须传**（P5-B2）：`PluginLoader.load(name, params=None)` 在
            # params 为空时走 `plugin_class()`，插件退回自己 `__init__` 的默认值 ——
            # 于是 `config.yaml` 里的 `plugins.asr.params.model_size` 与
            # `initial_prompt` 在这条路径上**完全没被读到**，而且是静默的。
            # 取参数的判据收敛在 core/plugin_params.py，与 core/app.py 同一条。
            asr_config = self.config_manager.get_plugin_config("asr")
            asr_engine = asr_config.get("engine", "faster_whisper")
            asr_params = plugin_params_for(self.config_manager, "asr", asr_engine)

            if asr_engine and asr_engine != "null":
                self.asr = self.plugin_loader.load(asr_engine, params=asr_params)
                if self.asr:
                    logger.info(
                        f"ASR插件加载成功: {asr_engine} "
                        f"(model_size={asr_params.get('model_size', '默认')}, "
                        f"偏置={len(asr_params.get('initial_prompt', ''))} 字)"
                    )
                else:
                    logger.warning(f"ASR插件加载失败: {asr_engine}")
                    self.asr = self.plugin_loader.load_by_interface("ASREngine")
            else:
                self.asr = self.plugin_loader.load_by_interface("ASREngine")
            
            # 加载TTS插件
            tts_config = self.config_manager.get_plugin_config("tts")
            tts_engine = tts_config.get("engine", "edge_tts")
            tts_params = plugin_params_for(self.config_manager, "tts", tts_engine)

            if tts_engine and tts_engine != "null":
                self.tts = self.plugin_loader.load(tts_engine, params=tts_params)
                if self.tts:
                    logger.info(f"TTS插件加载成功: {tts_engine}, 音色: {tts_params.get('voice', 'default')}")
                else:
                    logger.warning(f"TTS插件加载失败: {tts_engine}")
                    self.tts = self.plugin_loader.load_by_interface("TTSEngine")
            else:
                self.tts = self.plugin_loader.load_by_interface("TTSEngine")
            
            return True
            
        except Exception as e:
            logger.error(f"初始化语音插件失败: {e}")
            return False
    
    def speech_to_text(self, audio_path: str) -> Optional[str]:
        """
        语音转文字
        
        Args:
            audio_path: 音频文件路径
            
        Returns:
            识别出的文本，失败返回None
        """
        if self.asr is None:
            logger.error("ASR引擎未初始化")
            return None
        
        try:
            # 发送开始识别事件
            self.event_bus.emit(EventType.VOICE_RECOGNIZED, {
                "status": "processing",
                "audio_path": audio_path
            })
            
            # 执行识别
            text = self.asr.transcribe(audio_path)
            
            if text:
                logger.info(f"语音识别结果: {text}")
                
                # 发送识别完成事件
                self.event_bus.emit(EventType.VOICE_RECOGNIZED, {
                    "status": "success",
                    "text": text,
                    "audio_path": audio_path
                })
                
                # 调用回调函数
                if self.on_speech_recognized:
                    self.on_speech_recognized(text)
            else:
                logger.warning("语音识别失败")
                
                # 发送识别失败事件
                self.event_bus.emit(EventType.VOICE_RECOGNIZED, {
                    "status": "failed",
                    "audio_path": audio_path
                })
            
            return text
            
        except Exception as e:
            logger.error(f"语音识别异常: {e}")
            return None
    
    def text_to_speech(self, text: str) -> Optional[bytes]:
        """
        文字转语音
        
        Args:
            text: 要转换的文本
            
        Returns:
            音频数据，失败返回None
        """
        if self.tts is None:
            logger.error("TTS引擎未初始化")
            return None
        
        try:
            # 发送开始合成事件
            self.event_bus.emit(EventType.VOICE_RESPONSE, {
                "status": "processing",
                "text": text
            })
            
            # 执行合成
            audio_data = self.tts.speak(text)
            
            if audio_data:
                logger.info(f"语音合成完成，音频大小: {len(audio_data)} 字节")
                
                # 发送合成完成事件
                self.event_bus.emit(EventType.VOICE_RESPONSE, {
                    "status": "success",
                    "text": text,
                    "audio_size": len(audio_data)
                })
                
                # 调用回调函数
                if self.on_speech_synthesized:
                    self.on_speech_synthesized(audio_data)
            else:
                logger.warning("语音合成失败")
                
                # 发送合成失败事件
                self.event_bus.emit(EventType.VOICE_RESPONSE, {
                    "status": "failed",
                    "text": text
                })
            
            return audio_data
            
        except Exception as e:
            logger.error(f"语音合成异常: {e}")
            return None
    
    def speak(self, text: str) -> bool:
        """
        语音播报（合成并播放）
        
        Args:
            text: 要播报的文本
            
        Returns:
            是否成功
        """
        try:
            # 合成语音
            audio_data = self.text_to_speech(text)
            if audio_data is None:
                return False
            
            # 播放音频
            return self._play_audio(audio_data)
            
        except Exception as e:
            logger.error(f"语音播报失败: {e}")
            return False
    
    def _play_audio(self, audio_data: bytes) -> bool:
        """
        播放音频
        
        Args:
            audio_data: 音频数据
            
        Returns:
            是否成功
        """
        try:
            # 使用pygame播放音频
            import pygame
            import io
            
            # 将字节数据转换为音频文件
            audio_file = io.BytesIO(audio_data)
            
            # 初始化pygame mixer
            if not pygame.mixer.get_init():
                pygame.mixer.init()
            
            # 加载并播放
            sound = pygame.mixer.Sound(audio_file)
            sound.play()
            
            # 等待播放完成（退出时立即中断）
            import time
            while pygame.mixer.get_busy():
                # 退出时强制停止
                try:
                    from core.app import get_app
                    app = get_app()
                    if not getattr(app, '_running', True):
                        pygame.mixer.stop()
                        break
                except Exception:
                    pass
                time.sleep(0.1)
            
            return True
            
        except Exception as e:
            logger.error(f"播放音频失败: {e}")
            return False
    
    def start_recording(self) -> bool:
        """
        开始录音
        
        Returns:
            是否成功
        """
        try:
            self.recording = True
            self.audio_buffer = []
            
            # 发送开始录音事件
            self.event_bus.emit(EventType.VOICE_RECORD_START)
            
            logger.info("开始录音")
            return True
            
        except Exception as e:
            logger.error(f"开始录音失败: {e}")
            return False
    
    def stop_recording(self) -> Optional[str]:
        """
        停止录音并识别
        
        Returns:
            识别出的文本，失败返回None
        """
        try:
            self.recording = False
            
            # 发送停止录音事件
            self.event_bus.emit(EventType.VOICE_RECORD_STOP)
            
            logger.info("停止录音")
            
            # 这里应该将录音数据保存为文件并识别
            # 暂时返回None
            return None
            
        except Exception as e:
            logger.error(f"停止录音失败: {e}")
            return None
    
    def process_voice(self, audio_path: str) -> dict:
        """
        处理语音（完整流程）
        
        Args:
            audio_path: 音频文件路径
            
        Returns:
            处理结果字典
        """
        result = {
            "text": None,
            "audio": None,
            "success": False
        }
        
        try:
            # 语音识别
            text = self.speech_to_text(audio_path)
            if text is None:
                return result
            
            result["text"] = text
            
            # 这里应该调用AI生成回复
            # 暂时使用简单回复
            response_text = f"你说的是：{text}"
            
            # 语音合成
            audio_data = self.text_to_speech(response_text)
            if audio_data:
                result["audio"] = audio_data
            
            result["success"] = True
            return result
            
        except Exception as e:
            logger.error(f"处理语音失败: {e}")
            return result


# 全局语音服务实例
_voice_service: Optional[VoiceService] = None


def get_voice_service() -> VoiceService:
    """
    获取语音服务单例
    
    Returns:
        语音服务实例
    """
    global _voice_service
    
    if _voice_service is None:
        _voice_service = VoiceService()
    
    return _voice_service
