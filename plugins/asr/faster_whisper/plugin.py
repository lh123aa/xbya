"""
Faster Whisper ASR插件
实现语音识别功能
"""

import os
import logging
from typing import Optional, List
from core.temp_manager import get_tmp_dir

from interfaces.asr import ASREngine

logger = logging.getLogger(__name__)


class FasterWhisperASR(ASREngine):
    """Faster Whisper ASR实现"""
    
    def __init__(self, model_size: str = "base", device: str = "cpu", compute_type: str = "int8",
                 initial_prompt: str = ""):
        """
        初始化Faster Whisper ASR

        Args:
            model_size: 模型大小（tiny, base, small, medium, large-v3）
            device: 计算设备（cpu, cuda）
            compute_type: 计算类型（int8, float16, float32）
            initial_prompt: 解码偏置（可选）。给 Whisper 一段"可能的用词"，
                它会倾向于把音频往这些词上靠。**短句（1~2 字）最吃这个**：
                实测「算了」在无偏置下会被听成「散了」「三郎」（P4-B5），
                而这两个字决定了"用户到底取没取消"。
                默认空 = 不偏置（保持原行为，向后兼容）。
        """
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self.initial_prompt = initial_prompt or ""
        self.model = None
        self._available = False
        self._load_attempted = False

        # 延迟加载：不在此处加载模型，等首次 transcribe 时才加载（节省启动时间与空闲内存）
    
    def _ensure_loaded(self) -> bool:
        """懒加载：首次使用时才加载模型"""
        if self._available and self.model is not None:
            return True
        if self._load_attempted:
            return False
        self._load_attempted = True
        return self._load_model()
    
    def _load_model(self) -> bool:
        """加载模型（使用 config 传入的 model_size/device，随性能档位自适应）"""
        try:
            from faster_whisper import WhisperModel

            model_size = self.model_size or "small"
            device = self.device or "cpu"
            logger.info(f"加载Faster Whisper模型: {model_size} (device={device})")
            self.model = WhisperModel(
                model_size,
                device=device,
                compute_type="int8"
            )
            self._available = True
            logger.info("Faster Whisper模型加载成功")
            return True
            
        except ImportError:
            logger.warning("faster-whisper未安装，请运行: pip install faster-whisper")
            return False
        except Exception as e:
            logger.error(f"加载Faster Whisper模型失败: {e}")
            return False
    
    def transcribe(self, audio_path: str, initial_prompt: Optional[str] = None) -> Optional[str]:
        """
        将音频文件转为文本

        Args:
            audio_path: 音频路径
            initial_prompt: 覆盖本次调用的解码偏置；None 时用构造时的 `initial_prompt`
                （即 config 里的 `asr.faster_whisper.initial_prompt`）。
                留成"可覆盖"是为了让 `tools/measure_asr_stimulus.py --short`
                能对同一批音频做**有偏置 / 无偏置的 A/B**，而不是靠改配置再比。
        """
        if not self._ensure_loaded() or self.model is None:
            logger.error("Faster Whisper模型未加载")
            return None
        
        try:
            logger.info(f"开始识别音频: {audio_path}")
            
            # 增强音频音量
            enhanced_path = self._enhance_audio(audio_path)
            target_path = enhanced_path if enhanced_path else audio_path
            prompt = self.initial_prompt if initial_prompt is None else initial_prompt
            
            # 执行识别（beam=5：更准；中文精度优先，本机8核CPU可承受）
            segments, info = self.model.transcribe(
                target_path,
                beam_size=5,
                language="zh",
                vad_filter=True,          # 启用内置 VAD，过滤纯噪音段
                condition_on_previous_text=False,
                no_speech_threshold=0.6,   # 放宽：更倾向判定为语音，减少漏听
                log_prob_threshold=-1.0,   # 放宽：保留更多中文片段
                word_timestamps=True,
                # 解码偏置只在**显式配置了**才传：默认不传 = 与改动前逐字节同行为
                **({"initial_prompt": prompt} if prompt else {}),
            )

            # 合并片段
            # 策略：综合 word_count + logprob + no_speech 三个指标
            texts = []
            seg_count = 0
            for segment in segments:
                seg_count += 1
                has_words = hasattr(segment, 'words') and segment.words and len(segment.words) > 0
                word_count = len(segment.words) if has_words else 0
                ns = segment.no_speech_prob
                lp = segment.avg_logprob
                logger.info(f"ASR片段[{seg_count}]: text={segment.text!r}, no_speech={ns:.3f}, logprob={lp:.3f}, words={word_count}")
                # 保留条件：放宽短词门槛（"嗯/好的/行/再见/谢谢" 等 1~2 字应答不再被丢）。
                # 注意：中文短词的 Whisper avg_logprob 普遍偏低(-0.8 ~ -1.5)，不能设太严。
                #   - word_count>=3：常规放行（lp>-1.3, ns<0.85）——放宽 lp 减少漏听
                #   - 短词(1~2字)：放宽 lp 到 -1.6、ns 到 0.8，避免真实的"好/再见/谢谢"被误杀。
                #     环境噪音误触发仍由音量触发阈值 + VAD 兜底。
                if word_count >= 3 and lp > -1.3 and ns < 0.85:
                    texts.append(segment.text)
                elif word_count >= 4 and lp > -1.1:
                    texts.append(segment.text)
                elif 1 <= word_count < 3 and lp > -1.6 and ns < 0.8:
                    # 短词但清晰（logprob 高、非静音）→ 放行，避免"听短话没反应"
                    texts.append(segment.text)
                else:
                    logger.info(f"  → 丢弃(word={word_count}, lp={lp:.2f}, ns={ns:.2f})")
            text = "".join(texts).strip()
            logger.info(f"ASR共{seg_count}个片段, 合并后: [{text[:100]}]")
            
            # 清理增强文件
            if enhanced_path and enhanced_path != audio_path:
                try:
                    os.unlink(enhanced_path)
                except:
                    pass
            
            logger.info(f"语音识别完成: {text[:100]}...")
            return text if text else None
            
        except Exception as e:
            logger.error(f"语音识别失败: {e}")
            return None
    
    def _enhance_audio(self, audio_path: str) -> Optional[str]:
        """增强音频：统一重采样到 16k 单声道 + 音量归一化。

        Whisper 需要 16k 单声道。麦克风流失败回退到设备采样率(常见 44.1k/48k，
        且可能立体声)时，直接喂会给 Whisper 会导致识别质量明显下降。
        这里先做：多声道→单声道、任意采样率→16k，再归一化音量。
        """
        try:
            import wave
            import numpy as np
            import tempfile

            TARGET_RATE = 16000

            # 读取音频
            with wave.open(audio_path, 'rb') as wf:
                frames = wf.readframes(wf.getnframes())
                sample_width = wf.getsampwidth()
                sample_rate = wf.getframerate()
                channels = wf.getnchannels()
            orig_rate = sample_rate   # 记录原始采样率（用于判定是否需要重写）
            orig_channels = channels  # 记录原始声道数

            # 转换为numpy数组（int16 -> float32）
            audio_int = np.frombuffer(frames, dtype=np.int16).astype(np.float32)

            # 声道合并：立体声(多声道) → 单声道（取均值）
            if channels > 1:
                audio_int = audio_int.reshape(-1, channels).mean(axis=1)
                audio_int = audio_int.astype(np.float32)
                channels = 1

            # 重采样到 16k（非线性需要；用 numpy 线性插值，零额外依赖）
            if sample_rate != TARGET_RATE and sample_rate > 0:
                n_out = int(len(audio_int) * TARGET_RATE / sample_rate)
                if n_out > 1:
                    x_old = np.linspace(0.0, 1.0, num=len(audio_int), endpoint=False)
                    x_new = np.linspace(0.0, 1.0, num=n_out, endpoint=False)
                    audio_int = np.interp(x_new, x_old, audio_int).astype(np.float32)
                    sample_rate = TARGET_RATE

            # 计算音量（基于重采样后的数据）
            current_vol = float(np.abs(audio_int).mean())

            if current_vol < 10.0:
                # 几乎无声：返回None，交给上层提示"没听到声音"
                logger.info(f"录音几乎无声({current_vol:.0f})，放弃识别")
                return None

            # 归一化到目标音量，确保Whisper能正确识别
            TARGET_VOL = 800.0
            MAX_GAIN = 30.0   # 放宽增益上限：低音量麦克风也能提升到位，避免"听不见"
            if current_vol >= TARGET_VOL:
                gain = 1.0
            else:
                gain = min(TARGET_VOL / max(current_vol, 1.0), MAX_GAIN)

            # 需要重采样/单声道化/增益之一时，都写出干净的目标文件
            # 用【原始】rate/channels 判断，避免就地修改后判断失效
            need_rewrite = (gain > 1.0) or (orig_rate != TARGET_RATE) or (orig_channels != 1)
            if not need_rewrite:
                return audio_path

            audio_int = audio_int * gain
            audio_int = np.clip(audio_int, -32768, 32767).astype(np.int16)
            logger.info(f"音频增强: vol={current_vol:.0f} -> {TARGET_VOL:.0f} (增益 {gain:.1f}x, 重采样到 {sample_rate}Hz 单声道)")

            enhanced_path = tempfile.NamedTemporaryFile(
                suffix='.wav', delete=False, dir=get_tmp_dir()).name
            with wave.open(enhanced_path, 'wb') as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(sample_rate)
                wf.writeframes(audio_int.tobytes())
            return enhanced_path

        except Exception as e:
            logger.warning(f"音频增强失败(降级为原音频): {e}")
            return None
    
    def transcribe_stream(self, audio_stream) -> Optional[str]:
        """
        实时流转文本识别
        
        Args:
            audio_stream: 音频流对象
            
        Returns:
            识别出的文本
        """
        # 实时流识别需要更复杂的实现
        # 暂时返回None
        logger.warning("实时流识别暂未实现")
        return None
    
    def get_supported_formats(self) -> List[str]:
        """
        获取支持的音频格式
        
        Returns:
            支持的音频格式列表
        """
        return ["wav", "mp3", "flac", "ogg", "m4a", "wma", "aac"]
    
    def is_available(self) -> bool:
        """检查是否可用（懒加载引擎：未加载时视为"可能可用"，加载失败才判False）"""
        if self._available:
            return True
        # 尚未尝试加载 → 按需求时加载（避免启动即占内存）
        return not self._load_attempted


def register():
    return {
        "name": "faster_whisper",
        "version": "1.0.0",
        "interface": "ASREngine",
        "class": "FasterWhisperASR",
        "dependencies": ["faster-whisper"]
    }
