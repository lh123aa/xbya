"""
麦克风录音服务
负责捕获麦克风音频并进行语音识别
"""

import logging
import tempfile
import os
import wave
import struct
import threading
import time
from typing import Optional, Callable
from core.temp_manager import get_tmp_dir

logger = logging.getLogger(__name__)


class MicrophoneService:
    """麦克风录音服务"""
    
    def __init__(self):
        """初始化麦克风服务"""
        self.is_recording = False
        self.audio_buffer = []
        self.sample_rate = 16000
        self.channels = 1
        self.chunk_size = 1024
        self.input_device = None  # 指定麦克风设备索引（None=系统默认；自动挑选真实麦克风）

        # 常驻监听
        self._listening = False
        self._listen_stop = False
        self._listen_thread = None
        self._cooldown_until = 0.0   # 触发冷却截止时间戳（防环境音/回声连珠炮触发转写）

        # 回调
        self.on_speech_detected: Optional[Callable[[str], None]] = None
        self.on_recording_start: Optional[Callable[[], None]] = None
        self.on_recording_stop: Optional[Callable[[], None]] = None

        # 依赖检查
        self.pyaudio_available = False
        self._check_pyaudio()

        logger.info("麦克风服务初始化")
    
    def _check_pyaudio(self):
        """检查PyAudio是否可用"""
        try:
            import pyaudio
            self.pyaudio_available = True
            logger.info("PyAudio可用")
        except ImportError:
            logger.warning("pyaudio未安装，运行: pip install pyaudio")
    
    def is_available(self) -> bool:
        """检查麦克风是否可用"""
        if not self.pyaudio_available:
            return False
        
        try:
            import pyaudio
            p = pyaudio.PyAudio()
            # 检查是否有输入设备
            for i in range(p.get_device_count()):
                info = p.get_device_info_by_index(i)
                if info['maxInputChannels'] > 0:
                    p.terminate()
                    return True
            p.terminate()
            return False
        except Exception:
            return False
    
    def _pick_mic_index(self) -> Optional[int]:
        """挑选最合适的真实麦克风设备索引（返回 None=使用系统默认）。

        遍历所有输入设备：
        - 排除非麦克风（立体声混音/扬声器/声音映射器/主声音捕获 等）
        - 优先挑选"麦克风/Microphone/Mic"命名且声道最少的（通常是真实麦克风）
        - 若已显式指定 self.input_device 则直接用
        """
        if self.input_device is not None:
            return self.input_device
        try:
            import pyaudio
            p = pyaudio.PyAudio()
            good = []
            for i in range(p.get_device_count()):
                info = p.get_device_info_by_index(i)
                if info.get('maxInputChannels', 0) <= 0:
                    continue
                name = (info.get('name') or '').lower()
                bad = ('混音', 'stereo', '扬声器', 'speaker', '声音映射', '波形', '主声音', 'loopback', 'output')
                if any(b in name for b in bad):
                    continue
                good.append(i)
            p.terminate()
            if good:
                # 有真实麦克风：优先非默认可用的第一个；若有多个，选索引小且非默认的
                logger.info(f"[mic] 选中麦克风设备索引 {good[0]}")
                return good[0]
        except Exception as e:
            logger.warning(f"[mic] 设备挑选失败，使用默认: {e}")
        return None

    def start_recording(self, duration: float = 5.0) -> Optional[str]:
        """
        录音（语音结束自动提前停止）
        
        Args:
            duration: 最大录音时长（秒）
            
        Returns:
            录音文件路径，失败返回None
        """
        if not self.pyaudio_available:
            logger.error("PyAudio不可用")
            return None
        
        if self.is_recording:
            logger.warning("正在录音中")
            return None
        
        try:
            import pyaudio
            
            self.is_recording = True
            self.audio_buffer = []
            
            if self.on_recording_start:
                self.on_recording_start()
            
            p = pyaudio.PyAudio()
            
            # 打开麦克风流（16000Hz失败则回退默认采样率）
            rate = self.sample_rate
            stream = None
            try:
                stream = p.open(
                    format=pyaudio.paInt16,
                    channels=self.channels,
                    rate=rate,
                    input=True,
                    frames_per_buffer=self.chunk_size
                )
            except Exception as e:
                logger.warning(f"16000Hz打开失败({e})，尝试默认采样率")
                dev_info = p.get_default_input_device_info()
                rate = int(dev_info["defaultSampleRate"])
                stream = p.open(
                    format=pyaudio.paInt16,
                    channels=self.channels,
                    rate=rate,
                    input=True,
                    frames_per_buffer=self.chunk_size
                )
            
            logger.info(f"开始录音（{rate}Hz），最大时长: {duration}秒")
            
            # 能量检测：检测到语音后，静音持续1秒即提前停止（用户说完即停）
            frames = []
            total_frames = int(rate / self.chunk_size * duration)
            voice_detected = False
            silence_frames = 0
            max_silence_frames = int(rate / self.chunk_size * 1.0)  # 1秒静音
            
            for i in range(total_frames):
                if not self.is_recording:
                    break
                data = stream.read(self.chunk_size, exception_on_overflow=False)
                frames.append(data)
                
                # 能量检测（每4个chunk算一次，降低开销）
                if i % 4 == 0:
                    import numpy as np
                    chunk = np.frombuffer(data, dtype=np.int16).astype(np.float32)
                    vol = np.mean(np.abs(chunk))
                    if vol > 300:
                        voice_detected = True
                        silence_frames = 0
                    elif voice_detected and vol < 100:
                        silence_frames += 4
                        if silence_frames >= max_silence_frames:
                            logger.info(f"检测到说话结束（静音1秒），提前停止录音")
                            break
            
            # 停止录音
            stream.stop_stream()
            stream.close()
            p.terminate()
            
            # 保存到临时文件
            with tempfile.NamedTemporaryFile(suffix='.wav', delete=False, dir=get_tmp_dir()) as f:
                temp_path = f.name
            
            wf = wave.open(temp_path, 'wb')
            wf.setnchannels(self.channels)
            wf.setsampwidth(p.get_sample_size(pyaudio.paInt16))
            wf.setframerate(rate)
            wf.writeframes(b''.join(frames))
            wf.close()
            
            self.is_recording = False
            
            if self.on_recording_stop:
                self.on_recording_stop()
            
            logger.info(f"录音完成: {temp_path} ({len(frames)}帧)")
            return temp_path
            
        except Exception as e:
            logger.error(f"录音失败: {e}")
            self.is_recording = False
            return None
    
    def stop_recording(self):
        """停止录音"""
        self.is_recording = False
    
    def listen_continuous(self, callback: Callable[[str], None], 
                         silence_threshold: float = 500,
                         silence_duration: float = 1.5):
        """
        持续监听模式
        
        Args:
            callback: 识别到语音时的回调
            silence_threshold: 静音阈值
            silence_duration: 静音持续时间（秒）
        """
        if not self.pyaudio_available:
            logger.error("PyAudio不可用")
            return
        
        import pyaudio
        import numpy as np
        
        self.is_recording = True
        self.on_speech_detected = callback
        
        p = pyaudio.PyAudio()
        stream = p.open(
            format=pyaudio.paInt16,
            channels=self.channels,
            rate=self.sample_rate,
            input=True,
            frames_per_buffer=self.chunk_size
        )
        
        logger.info("开始持续监听...")
        
        frames = []
        silence_frames = 0
        max_silence_frames = int(silence_duration * self.sample_rate / self.chunk_size)
        
        try:
            while self.is_recording:
                data = stream.read(self.chunk_size, exception_on_overflow=False)
                frames.append(data)
                
                # 检测音量
                audio_data = np.frombuffer(data, dtype=np.int16)
                volume = np.abs(audio_data).mean()
                
                if volume < silence_threshold:
                    silence_frames += 1
                else:
                    silence_frames = 0
                
                # 静音超过阈值，处理语音
                if silence_frames > max_silence_frames and len(frames) > 10:
                    # 保存并识别
                    audio_data = b''.join(frames)
                    frames = []
                    silence_frames = 0
                    
                    # 异步处理
                    threading.Thread(
                        target=self._process_audio,
                        args=(audio_data,),
                        daemon=True
                    ).start()
        
        except Exception as e:
            logger.error(f"监听异常: {e}")
        finally:
            stream.stop_stream()
            stream.close()
            p.terminate()

    def listen_standby(self, callback, on_start=None,
                       speech_volume: float = 300.0,
                       max_seconds: float = 5.0) -> bool:
        """常驻监听：检测到一次说话→生成wav→callback(wav_path)
        
        状态机：STANDBY(等待) → LISTENING(缓存) → 结束后回到STANDBY循环
        单次说话最长max_seconds，语音后静音0.8秒判定结束。
        自适应静音阈值：录音期间跟踪峰值，静音判定为峰值的40%。

        注意：回调的 wav 为临时文件，由消费者负责删除（调用成功后自行 unlink）。
        """
        if not self.pyaudio_available:
            logger.error("PyAudio不可用，无法常驻监听")
            return False
        if self._listening:
            logger.warning("已在监听中")
            return False

        try:
            import pyaudio
            import numpy as np

            self._listening = True
            self._listen_stop = False

            def _loop():
                p = None
                stream = None
                try:
                    p = pyaudio.PyAudio()
                    rate = self.sample_rate
                    # 挑选真实麦克风设备（排除立体声混音等）；失败回退默认
                    dev_idx = self._pick_mic_index()
                    if dev_idx is not None:
                        try:
                            stream = p.open(
                                format=pyaudio.paInt16,
                                channels=self.channels,
                                rate=rate,
                                input=True,
                                input_device_index=dev_idx,
                                frames_per_buffer=self.chunk_size
                            )
                            logger.info(f"使用指定麦克风设备 idx={dev_idx}")
                        except Exception as e:
                            logger.warning(f"指定麦克风打开失败({e})，回退默认设备")
                            dev_idx = None
                    if dev_idx is None:
                        try:
                            stream = p.open(
                                format=pyaudio.paInt16,
                                channels=self.channels,
                                rate=rate,
                                input=True,
                                frames_per_buffer=self.chunk_size
                            )
                        except Exception as e:
                            logger.warning(f"监听打开失败({e})，尝试默认采样率")
                            info = p.get_default_input_device_info()
                            rate = int(info["defaultSampleRate"])
                            stream = p.open(
                                format=pyaudio.paInt16,
                                channels=self.channels,
                                rate=rate,
                                input=True,
                                frames_per_buffer=self.chunk_size
                            )

                    logger.info(f"常驻监听启动，等待说话... (基础阈值={speech_volume}, 采样率={rate})")
                    frames = []
                    state = "standby"
                    voice_points = 0
                    silence_sec = 0.0
                    total_sec = 0.0
                    peak_vol = 0.0  # 录音期间跟踪峰值音量
                    noise_floor = 0.0  # 背景噪声底噪（自适应）
                    chunk_sec = self.chunk_size / max(rate, 1)  # 单个chunk秒数（按实际rate）
                    chunk_count = 0  # 独立采样计数（每4个chunk评估一次音量）
                    vol_log_count = 0

                    while not self._listen_stop:
                        data = stream.read(self.chunk_size, exception_on_overflow=False)
                        chunk_count += 1
                        # 每 chunk 都评估音量（更快捕捉起音，避免开头被粗采样错过）
                        # 优化：空数组先短路（avoid/stream异常），用均值近似绝对值均值
                        # （近零均值前提下 RMS*0.8 ≈ mean|·|，省去一次 abs 拷贝）
                        vol = self._quick_volume(data)

                        if state == "standby":
                            # 稳健背景噪声估计：只缓慢跟随"持续的低音量"，用更保守的上升
                            # 防止突发声(关门/拍桌/喇叭)把底噪瞬间拉高、导致阈值被带偏。
                            if noise_floor <= 0:
                                noise_floor = speech_volume
                            elif vol < noise_floor:
                                # 音量低于基线 → 快速下调（环境变安静）
                                noise_floor = max(speech_volume * 0.5, noise_floor * 0.7 + vol * 0.3)
                            else:
                                # 音量高于基线 → 只很慢地上升（免被瞬时突发带偏）
                                noise_floor = noise_floor * 0.985 + vol * 0.015
                            # 触发阈值：底噪的1.7倍 + 60，且不低于基础阈值（偏防噪音）
                            trigger_threshold = max(speech_volume, noise_floor * 1.7 + 60)
                            # 每300 chunk 输出一次音量（约5秒），避免刷屏
                            vol_log_count += 1
                            if vol_log_count >= 300:
                                vol_log_count = 0
                                logger.info(f"[音量] vol={vol:.1f} (触发阈值={trigger_threshold:.0f}, 底噪={noise_floor:.0f})")
                            if vol > trigger_threshold:
                                # 触发冷却：刚处理完一段语音后，短时间内忽略新触发，
                                # 避免环境音/扬声器余音立即再次触发转写（烧 CPU）
                                if time.time() < self._cooldown_until:
                                    voice_points = 0
                                    continue
                                # 连续 2 点超阈值才触发（拒孤立突发/单次噪声尖峰，偏防噪音）
                                voice_points += 1
                                if voice_points >= 2:
                                    state = "listening"
                                    frames = [data]  # 当前帧已入 frames
                                    total_sec = 0.0
                                    silence_sec = 0.0
                                    peak_vol = 0.0
                                    logger.info(f"检测到语音，开始录音 (vol={vol:.0f} > 阈值{trigger_threshold:.0f})")
                                    if on_start:
                                        # 兼容一到两个参数：优先传音量(vol)，回调无参也能跑
                                        try:
                                            on_start(vol)
                                        except TypeError:
                                            on_start()
                                    continue  # 切换帧已入 frames，跳过末尾重复append
                            else:
                                voice_points = 0
                        else:
                            total_sec += chunk_sec
                            # 跟踪峰值音量
                            if vol > peak_vol:
                                peak_vol = vol
                            # 自适应静音阈值：峰值的40%，最低不低于speech_volume*0.5
                            adaptive_silence = max(peak_vol * 0.4, speech_volume * 0.5)
                            if vol > adaptive_silence:
                                silence_sec = 0.0
                            else:
                                silence_sec += chunk_sec
                            # 端点自适应：说话越大，容忍的停顿越长（0.5~1.0s），
                            # 避免中气足的长句被固定 0.8s 提前截断
                            end_silence = min(1.0, max(0.5, peak_vol / 1600.0 + 0.45))
                            if silence_sec >= end_silence or total_sec >= max_seconds:
                                if len(frames) > int(rate * 0.4 / self.chunk_size):
                                    try:
                                        import wave
                                        import tempfile
                                        with tempfile.NamedTemporaryFile(suffix='.wav', delete=False, dir=get_tmp_dir()) as f:
                                            wav_path = f.name
                                        wf = wave.open(wav_path, 'wb')
                                        wf.setnchannels(self.channels)
                                        wf.setsampwidth(2)
                                        wf.setframerate(rate)  # 用实际rate
                                        wf.writeframes(b''.join(frames))
                                        wf.close()
                                        logger.info(f"监听捕获说话→回调")
                                        callback(wav_path)
                                        # 触发冷却：这段语音处理期间不再触发新录音，防环境音连珠炮
                                        cooldown = 2.5  # 秒
                                        self._cooldown_until = time.time() + cooldown
                                    except Exception as e:
                                        logger.error(f"保存wav失败: {e}")
                                state = "standby"
                                frames = []
                                voice_points = 0
                                silence_sec = 0.0
                        # 缓存当前帧
                        if state == "listening":
                            frames.append(data)
                except Exception as e:
                    logger.error(f"常驻监听线程异常退出: {e}")
                finally:
                    if stream is not None:
                        try:
                            stream.stop_stream()
                            stream.close()
                        except Exception:
                            pass
                    if p is not None:
                        try:
                            p.terminate()
                        except Exception:
                            pass
                    self._listening = False
                    logger.info("常驻监听已停止")

            import threading
            self._listen_thread = threading.Thread(target=_loop, daemon=True)
            self._listen_thread.start()
            return True

        except Exception as e:
            logger.error(f"启动常驻监听失败: {e}")
            self._listening = False
            return False

    def stop_listen_standby(self) -> None:
        """停止常驻监听"""
        self._listen_stop = True

    def is_listening(self) -> bool:
        """是否正在监听"""
        return bool(self._listening)

    def _quick_volume(self, data: bytes) -> float:
        """快速音量估计：用 int16 直接算 mean(|·|)，避免 astype(float32) 的内存拷贝。

        常驻监听每 chunk 调用，是自旋热点。空数据短路，避免 numpy 报错。
        采样数列较大时按 1/2 抽取再算，进一步减负（对音量峰值判定足够）。
        """
        import numpy as np
        n = len(data) // 2  # int16 样本数
        if n <= 0:
            return 0.0
        arr = np.frombuffer(data, dtype=np.int16)
        if n > 256:
            arr = arr[::2]  # 下采样一半，减 workload（峰值判定不受影响）
        return float(np.mean(np.abs(arr)))
    
    def _process_audio(self, audio_data: bytes):
        """处理音频数据"""
        try:
            # 保存到临时文件
            with tempfile.NamedTemporaryFile(suffix='.wav', delete=False, dir=get_tmp_dir()) as f:
                temp_path = f.name
            
            import wave
            p_audio = __import__('pyaudio')
            p = p_audio.PyAudio()
            
            wf = wave.open(temp_path, 'wb')
            wf.setnchannels(self.channels)
            wf.setsampwidth(p.get_sample_size(p_audio.paInt16))
            wf.setframerate(self.sample_rate)
            wf.writeframes(audio_data)
            wf.close()
            p.terminate()
            
            # 识别
            if self.on_speech_detected:
                self.on_speech_detected(temp_path)
            
            # 清理
            try:
                os.unlink(temp_path)
            except:
                pass
                
        except Exception as e:
            logger.error(f"处理音频失败: {e}")


class WakeWordDetector:
    """唤醒词检测器"""
    
    def __init__(self, wake_word: str = "嘿欣雅"):
        """
        初始化唤醒词检测器
        
        Args:
            wake_word: 唤醒词
        """
        self.wake_word = wake_word
        self.is_listening = False
        self.on_wake_word: Optional[Callable[[], None]] = None
        
        logger.info(f"唤醒词检测器初始化: {wake_word}")
    
    def check_text(self, text: str) -> bool:
        """
        检查文本是否包含唤醒词
        
        Args:
            text: 识别出的文本
            
        Returns:
            是否包含唤醒词
        """
        if not text:
            return False
        
        # 简单匹配（可以改进为模糊匹配）：兼容"欣雅/小忆"
        if self.wake_word in text or "欣雅" in text:
            logger.info(f"检测到唤醒词: {text}")
            return True
        
        return False


# 全局实例
_microphone_service: Optional[MicrophoneService] = None
_wake_word_detector: Optional[WakeWordDetector] = None


def get_microphone_service() -> MicrophoneService:
    """获取麦克风服务单例"""
    global _microphone_service
    if _microphone_service is None:
        _microphone_service = MicrophoneService()
    return _microphone_service


def get_wake_word_detector() -> WakeWordDetector:
    """获取唤醒词检测器单例"""
    global _wake_word_detector
    if _wake_word_detector is None:
        _wake_word_detector = WakeWordDetector()
    return _wake_word_detector

