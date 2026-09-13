"""
Edge TTS插件
实现语音合成功能
"""

import asyncio
import logging
from typing import Optional, List

from interfaces.tts import TTSEngine

logger = logging.getLogger(__name__)


class EdgeTTS(TTSEngine):
    """Edge TTS实现"""

    def __init__(self, voice: str = "zh-CN-XiaoyiNeural",
                 rate: int = 0, pitch: int = 0):
        """
        初始化Edge TTS

        Args:
            voice: 语音名称
            rate: 语速调整 (-50 ~ +50 百分比)
            pitch: 音调调整 (-50 ~ +50 Hz)
        """
        self.voice = voice
        self.rate = rate
        self.pitch = pitch
        self._available = False
        
        # 检查依赖
        self._check_dependency()
    
    def _check_dependency(self) -> bool:
        """检查依赖"""
        try:
            import edge_tts
            self._available = True
            logger.info("Edge TTS依赖检查通过")
            return True
        except ImportError:
            logger.warning("edge-tts未安装，请运行: pip install edge-tts")
            return False
    
    def speak(self, text: str) -> Optional[bytes]:
        """
        将文本转为语音音频
        
        Args:
            text: 要转换的文本
            
        Returns:
            音频数据（字节格式）
        """
        if not self._available:
            logger.error("Edge TTS不可用")
            return None
        
        try:
            import edge_tts
            import io
            
            # 创建异步任务
            async def _speak():
                # 只在非默认值时传 rate/pitch（传 "0%" 可能被 Edge TTS 拒绝）
                kwargs = {}
                if self.rate != 0:
                    kwargs["rate"] = f"{'+' if self.rate > 0 else ''}{self.rate}%"
                if self.pitch != 0:
                    kwargs["pitch"] = f"{'+' if self.pitch > 0 else ''}{self.pitch}Hz"
                communicate = edge_tts.Communicate(text, self.voice, **kwargs)
                audio_data = io.BytesIO()

                async for chunk in communicate.stream():
                    if chunk["type"] == "audio":
                        audio_data.write(chunk["data"])

                return audio_data.getvalue()

            # 运行异步任务
            loop = asyncio.new_event_loop()
            audio_bytes = loop.run_until_complete(_speak())
            loop.close()

            # 空音频必须当成失败上报：语音名写错时 Edge 不报异常、只回空流，
            # 调用方拿到 b"" 会静默"合成成功但没声音"，排查时无从下手。
            if not audio_bytes:
                logger.error(
                    f"语音合成返回空音频（音色 '{self.voice}' 可能不存在）："
                    f"请核对 voice 是否为 Edge 真实音色，"
                    f"可用 plugins/tts/edge_tts 的 get_available_voices() 列出"
                )
                return None

            logger.info(f"语音合成完成，音频大小: {len(audio_bytes)} 字节，音色: {self.voice}，语速: {self.rate}%，音调: {self.pitch}Hz")
            return audio_bytes
            
        except Exception as e:
            # 解释器/线程池关闭阶段的收尾调用：静默降级为"无音频"，不打错误日志。
            # 两种 RuntimeError 文本都要覆盖：
            #   "cannot schedule new futures after shutdown"（executor 已停）
            #   "cannot schedule new futures after interpreter shutdown"
            import sys as _sys
            _msg = str(e)
            if _sys.is_finalizing() or "cannot schedule new futures" in _msg:
                logger.debug(f"语音合成跳过（运行时关闭中）: {e}")
                return None
            logger.error(f"语音合成失败: {e}")
            return None
    
    def speak_to_file(self, text: str, output_path: str) -> bool:
        """
        将文本转为语音并保存到文件
        
        Args:
            text: 要转换的文本
            output_path: 输出文件路径
            
        Returns:
            是否成功
        """
        if not self._available:
            logger.error("Edge TTS不可用")
            return False
        
        try:
            import edge_tts
            
            # 创建异步任务
            async def _speak_to_file():
                kwargs = {}
                if self.rate != 0:
                    kwargs["rate"] = f"{'+' if self.rate > 0 else ''}{self.rate}%"
                if self.pitch != 0:
                    kwargs["pitch"] = f"{'+' if self.pitch > 0 else ''}{self.pitch}Hz"
                communicate = edge_tts.Communicate(text, self.voice, **kwargs)
                await communicate.save(output_path)
            
            # 运行异步任务
            loop = asyncio.new_event_loop()
            loop.run_until_complete(_speak_to_file())
            loop.close()
            
            logger.info(f"语音合成保存成功: {output_path}")
            return True
            
        except Exception as e:
            logger.error(f"语音合成保存失败: {e}")
            return False
    
    def get_available_voices(self) -> List[str]:
        """
        获取可用的语音列表
        
        Returns:
            可用语音名称列表
        """
        if not self._available:
            return []
        
        try:
            import edge_tts
            
            # 获取语音列表
            async def _get_voices():
                voices = await edge_tts.list_voices()
                return [v["ShortName"] for v in voices if v["Locale"].startswith("zh-")]
            
            loop = asyncio.new_event_loop()
            voices = loop.run_until_complete(_get_voices())
            loop.close()
            
            return voices
            
        except Exception as e:
            logger.error(f"获取语音列表失败: {e}")
            return []
    
    def set_voice(self, voice_name: str) -> bool:
        """
        设置语音
        
        Args:
            voice_name: 语音名称
            
        Returns:
            是否设置成功
        """
        self.voice = voice_name
        logger.info(f"语音已设置为: {voice_name}")
        return True
    
    def is_available(self) -> bool:
        """检查是否可用"""
        return self._available


def register():
    return {
        "name": "edge_tts",
        "version": "1.0.0",
        "interface": "TTSEngine",
        "class": "EdgeTTS",
        "dependencies": ["edge-tts"]
    }
