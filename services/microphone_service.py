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
        self.input_device_name = ""  # 指定设备**名字片段**（索引缺失时的备选，跨机器更稳）
        self._config_device = None  # 记录 config 里的原始值，供诊断显示

        # 常驻监听
        self._listening = False
        self._listen_stop = False
        self._listen_paused = False   # 播报期间暂停标志（防回声）
        self._paused_at = 0.0         # 暂停起始时刻（看门狗超时自动恢复）
        self._max_pause_sec = 120.0   # 暂停上限：超过则强制恢复（防漏调 resume 后永久失聪）
        self._listen_thread = None
        self._cooldown_until = 0.0   # 触发冷却截止时间戳（防环境音/回声连珠炮触发转写）
        #: 自动挑选设备的**理由**（实测电平摘要），供 UI/诊断显示"为什么选它"
        self._auto_pick_reason = ""

        # ── 声源自动跟随（自动判断哪个设备有声音并切过去）──
        #: 当前监听实际打开的**设备索引**（None=系统默认）。监听线程写，观察者读。
        self._active_device: Optional[int] = None
        #: 请求切到的设备索引（监听线程每 chunk 检查，非 None 就重开流）
        self._switch_to: Optional[int] = None
        #: 观察者线程与开关
        self._watch_thread: Optional[threading.Thread] = None
        self._watch_stop = False
        #: 当前设备的**近期电平**（监听线程持续更新，观察者拿来对比）
        self._active_level = 0.0
        self._active_level_at = 0.0
        #: 观察者参数（可按需调；默认 20 秒看一次，避免频繁开关流）
        self._watch_interval = 20.0
        #: 轮转游标：每轮只探一个候选设备（全探会长时间独占麦克风）
        self._watch_rotor = 0
        #: 切换判据：候选设备的电平必须超过当前设备的 N 倍才切（防来回抖）
        self._switch_ratio = 3.0
        #: 切换冷却：刚切完一段时间内不再切（给用户/设备稳定时间）
        self._switch_cooldown_sec = 30.0
        self._last_switch_at = 0.0
        #: 用户显式指定的设备（config 或热键）——**指定了就不自动跟随**，
        #: 否则会和用户的明确选择打架。
        self._pin_device = False

        # 回调
        self.on_speech_detected: Optional[Callable[[str], None]] = None
        self.on_recording_start: Optional[Callable[[], None]] = None
        self.on_recording_stop: Optional[Callable[[], None]] = None

        # 依赖检查
        self.pyaudio_available = False
        self._check_pyaudio()

        # 从配置读取指定设备（voice.mic_device：索引或名字片段）
        self._load_device_from_config()

        logger.info("麦克风服务初始化")

    def _load_device_from_config(self) -> None:
        """读取 `voice.mic_device` 指定输入设备。

        取值语义：
          - 不填 / null / ""     → 自动挑选
          - 整数或纯数字字符串   → 按**设备索引**指定
          - 其他字符串           → 按**名字片段**匹配（跨机器更稳，如 "UU远程"）

        多声源环境（本机麦克风 + 远程桌面虚拟麦克风）必须显式指定，
        否则自动挑选可能选到收不到用户声音的那个。
        """
        try:
            from core.config_manager import get_config_manager
            raw = get_config_manager().get("voice.mic_device", None)
        except Exception as e:
            logger.debug("[mic] 读取 voice.mic_device 失败（用自动挑选）: %s", e)
            return
        self._config_device = raw
        if raw is None or raw == "":
            return
        if self.set_input_device(raw):
            # 用户显式指定了设备 → 关闭声源自动跟随（明确选择优先）
            self._pin_device = True
            logger.info("[mic] 配置生效：使用设备 %r", raw)
        else:
            logger.warning("[mic] 配置的 voice.mic_device=%r 无效，回退自动挑选", raw)

    
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

        优先级：
        1. 显式指定（`self.input_device`，来自 config `voice.mic_device`）
        2. 配置的**名字片段**匹配（`self.input_device_name`）
        3. 自动挑选 —— **实测电平**挑出真正有声音的那个（见下）

        ## 为什么自动挑选必须实测电平（根因修复）

        原先第 3 步是"排除回环类后取第一个"。这在多声源下**必错**：
        本机实测 14 个输入设备，`good[0]` = idx1 `麦克风 (Realtek(R) Audio)`
        读数 RMS=52（就是底噪），而用户真正说话进的是
        idx8 `麦克风阵列 (UU远程虚拟音频设备)` RMS=3993 —— **差 76 倍**。

        后果是全部日志"看起来正常"：麦克风有触发（底噪偶尔过阈）、
        ASR 有输出（在底噪上幻觉出"字幕by索兰娅"），但永远听不到用户。
        这正是 D26 登记的现象，但 D26 那轮只做到了"可手动切换"，
        **自动挑选仍取第一个** —— 用户不改配置就还是听不见。

        改成短采样实测：挑 RMS 最高的**非回环**设备。代价是启动多 ~1 秒
        （串行 0.15s/设备），换来"开箱即能听见"。
        取不到电平（无 numpy/pyaudio 异常）时退回原"取第一个"行为。
        """
        if self.input_device is not None:
            return self.input_device
        if getattr(self, "input_device_name", ""):
            idx = self.find_device_by_name(self.input_device_name)
            if idx is not None:
                return idx
            logger.warning(
                "[mic] 配置的设备名 %r 未匹配到任何输入设备，回退自动挑选",
                self.input_device_name,
            )

        # ── 自动挑选：优先实测电平 ──
        picked = self._pick_by_level()
        if picked is not None:
            return picked

        # ── 实测不可用时的兜底：优先系统默认设备 ──
        try:
            import pyaudio
            p = pyaudio.PyAudio()
            default_idx = None
            good = []
            try:
                default_idx = int(p.get_default_input_device_info()["index"])
            except Exception:
                pass
            for i in range(p.get_device_count()):
                info = p.get_device_info_by_index(i)
                if info.get('maxInputChannels', 0) <= 0:
                    continue
                name = (info.get('name') or '').lower()
                if any(b in name for b in self._BAD_NAME_PARTS):
                    continue
                good.append(i)
            p.terminate()
            if good:
                # 优先选系统默认设备（idx=1 通常是正确的麦克风）
                pick = good[0]
                if default_idx is not None and default_idx in good:
                    pick = default_idx
                logger.info(
                    "[mic] 自动选中麦克风设备索引 %d（实测电平区分度不足，"
                    "使用%s设备；多声源时建议显式配置 voice.mic_device）",
                    pick, "系统默认" if pick == default_idx else "第一个")
                return pick
        except Exception as e:
            logger.warning(f"[mic] 设备挑选失败，使用默认: {e}")
        return None

    def _pick_by_level(self, probe_seconds: float = 0.6) -> Optional[int]:
        """按**实测电平**挑设备：返回 RMS 最高的非回环设备索引。

        设计取舍：
        - 采样 0.6s/设备 × 10 个 mic ≈ 6 秒。**不能更短**：实测 0.15s
          时所有设备都读 0.2（PyAudio 打开流后的前几个 chunk 是暖机静音，
          短采样全被暖机吃掉 → 每个设备读数一样 → 判据失效）。
          6 秒是"只在启动跑一次"的代价，换来开箱即能听见。
        - 只在**差异显著**时才相信实测：最高与次高若相差不到 2 倍，
          说明都在底噪水平、区分度不足 → 返回 None 交给兜底逻辑，
          避免"在安静环境里随机挑一个"。
        - 排除回环（`kind == 'loopback'`）：回环收到的是**系统播放声**，
          远程桌面场景下系统声音也很大，容易把回环误当成"有声音的麦克风"。
        """
        try:
            import numpy as np  # noqa: F401  （probe_device_levels 需要）
        except ImportError:
            return None

        try:
            devs = [d for d in self.list_input_devices() if d.get("kind") == "mic"]
            if not devs:
                return None
            levels = self.probe_device_levels(seconds=probe_seconds,
                                              indices=[d["index"] for d in devs])
            levels = [r for r in levels if r.get("ok")]
            if not levels:
                return None
            levels.sort(key=lambda r: r["avg"], reverse=True)
            best = levels[0]
            second = levels[1] if len(levels) > 1 else None

            # 区分度不足：都在底噪水平 → 不硬猜
            if second is not None and best["avg"] < max(30.0, second["avg"] * 2.0):
                logger.info(
                    "[mic] 各设备电平接近（最高 %.1f / 次高 %.1f），"
                    "无法可靠区分，退回按顺序挑选", best["avg"], second["avg"])
                return None

            logger.info(
                "[mic] 按实测电平自动选中 idx=%d (RMS=%.1f, peak=%.0f, %s)",
                best["index"], best["avg"], best["peak"], best["name"])
            # 记下来，供 UI/诊断显示"为什么选它"
            self._auto_pick_reason = (
                f"实测电平最高 RMS={best['avg']:.1f}（次高 {second['avg']:.1f}）"
                if second else f"实测电平 RMS={best['avg']:.1f}"
            )
            return int(best["index"])
        except Exception as e:
            logger.debug("[mic] 电平挑选跳过: %s", e)
            return None


    # ========== 设备枚举与选择（多声源场景） ==========

    #: 判定为"非麦克风"的名字片段（混音/回环/扬声器等，收到的是系统播放声而非人声）
    _BAD_NAME_PARTS = ('混音', 'stereo', '扬声器', 'speaker', '声音映射', '波形',
                       '主声音', 'loopback', 'output', 'mapper')

    @classmethod
    def list_input_devices(cls, include_virtual: bool = True) -> list[dict]:
        """列出所有可用输入设备。

        Args:
            include_virtual: 是否包含"混音/回环"类设备（默认包含，标 `kind='loopback'`），
                便于用户看清全部声源；自动挑选时仍会排除它们。

        Returns:
            [{index, name, channels, native_rate, kind, is_default}, ...]
            kind: 'mic' 普通麦克风 | 'loopback' 系统声音回环
        """
        out: list[dict] = []
        try:
            import pyaudio
            p = pyaudio.PyAudio()
            try:
                default_idx = None
                try:
                    default_idx = int(p.get_default_input_device_info()["index"])
                except Exception:
                    pass
                for i in range(p.get_device_count()):
                    try:
                        info = p.get_device_info_by_index(i)
                    except Exception:
                        continue
                    if int(info.get("maxInputChannels", 0)) <= 0:
                        continue
                    name = info.get("name") or ""
                    low = name.lower()
                    is_loop = any(b in low for b in cls._BAD_NAME_PARTS)
                    if is_loop and not include_virtual:
                        continue
                    out.append({
                        "index": i,
                        "name": name,
                        "channels": int(info.get("maxInputChannels", 0)),
                        "native_rate": int(info.get("defaultSampleRate", 0) or 0),
                        "kind": "loopback" if is_loop else "mic",
                        "is_default": (i == default_idx),
                    })
            finally:
                p.terminate()
        except Exception as e:
            logger.warning("[mic] 枚举输入设备失败: %s", e)
        return out

    @classmethod
    def find_device_by_name(cls, needle: str) -> Optional[int]:
        """按名字片段查找设备索引（大小写不敏感）。

        多个匹配时优先"非回环"设备，其次索引最小者 —— 回环设备收到的是
        系统播放声，通常不是用户想要的输入。
        """
        if not needle:
            return None
        n = needle.strip().lower()
        cands = [d for d in cls.list_input_devices() if n in d["name"].lower()]
        if not cands:
            return None
        cands.sort(key=lambda d: (d["kind"] != "mic", d["index"]))
        return int(cands[0]["index"])

    def set_input_device(self, selector) -> bool:
        """运行时切换输入设备。

        Args:
            selector: int/数字字符串 → 设备索引；其他字符串 → 名字片段；
                      None 或 "" → 恢复自动挑选

        Returns:
            是否切换成功（切换成功仅表示记录生效，**重启监听后**才真正使用新设备）
        """
        if selector is None or selector == "":
            self.input_device = None
            self.input_device_name = ""
            logger.info("[mic] 已恢复自动挑选设备")
            return True

        # 索引形式
        try:
            idx = int(selector)
        except (TypeError, ValueError):
            idx = None

        devices = self.list_input_devices()
        if idx is not None:
            if any(d["index"] == idx for d in devices):
                self.input_device = idx
                self.input_device_name = ""
                name = next(d["name"] for d in devices if d["index"] == idx)
                logger.info("[mic] 已指定设备索引 %d (%s)", idx, name)
                return True
            logger.warning("[mic] 设备索引 %d 不存在或非输入设备", idx)
            return False

        found = self.find_device_by_name(str(selector))
        if found is None:
            logger.warning("[mic] 未找到名字含 %r 的输入设备", selector)
            return False
        self.input_device = found
        self.input_device_name = str(selector)
        logger.info("[mic] 已按名字指定设备 %d (%r)", found, selector)
        return True

    def probe_device_levels(self, seconds: float = 1.2,
                            indices: Optional[list[int]] = None) -> list[dict]:
        """逐个设备短采样，返回音量，用于**找出哪个声源真的有声音**。

        串行采样：并行打开多个 PyAudio 流会触发访问违例（0xC0000005）。
        采样率按设备原生采样率，打开失败会回退 16000/48000/44100。

        Returns:
            [{index, name, avg, peak, ok, error}, ...]（按 peak 降序）
        """
        results: list[dict] = []
        devices = self.list_input_devices()
        if indices is not None:
            devices = [d for d in devices if d["index"] in indices]

        try:
            import numpy as np
            import pyaudio
        except ImportError as e:
            logger.warning("[mic] 探测不可用: %s", e)
            return results

        p = pyaudio.PyAudio()
        try:
            for dev in devices:
                idx = dev["index"]
                entry = {"index": idx, "name": dev["name"],
                         "avg": 0.0, "peak": 0.0, "ok": False, "error": ""}
                # 采样率候选：原生优先，再退回常见值
                rates = []
                for r in (dev["native_rate"], 16000, 48000, 44100):
                    if r and r not in rates:
                        rates.append(int(r))
                opened = False
                for rate in rates:
                    stream = None
                    try:
                        ch = 1 if dev["channels"] >= 1 else 1
                        stream = p.open(format=pyaudio.paInt16, channels=ch,
                                        rate=rate, input=True,
                                        input_device_index=idx,
                                        frames_per_buffer=self.chunk_size)
                        vols = []
                        n = max(1, int(rate / self.chunk_size * seconds))
                        for _ in range(n):
                            data = stream.read(self.chunk_size,
                                               exception_on_overflow=False)
                            arr = np.frombuffer(data, dtype=np.int16)
                            vols.append(float(np.mean(np.abs(arr))) if len(arr) else 0.0)
                        if vols:
                            entry["avg"] = round(sum(vols) / len(vols), 1)
                            entry["peak"] = round(max(vols), 1)
                            entry["ok"] = True
                            entry["rate"] = rate
                        opened = True
                        break
                    except Exception as e:
                        entry["error"] = str(e)
                    finally:
                        if stream is not None:
                            try:
                                stream.stop_stream()
                                stream.close()
                            except Exception:
                                pass
                if not opened and not entry["error"]:
                    entry["error"] = "无法打开"
                results.append(entry)
        finally:
            p.terminate()

        results.sort(key=lambda r: r["peak"], reverse=True)
        return results

    def describe_devices(self) -> str:
        """返回人类可读的设备清单（供日志/诊断输出）。"""
        devs = self.list_input_devices()
        if not devs:
            return "（未发现任何输入设备）"
        lines = []
        cur = self._pick_mic_index()
        for d in devs:
            mark = " ←当前" if d["index"] == cur else ""
            kind = "回环" if d["kind"] == "loopback" else "麦克风"
            default = " [系统默认]" if d["is_default"] else ""
            lines.append("  idx=%-3d %-6s %-4dch %6dHz  %s%s%s" % (
                d["index"], kind, d["channels"], d["native_rate"],
                d["name"], default, mark))
        return "\n".join(lines)


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
                    # 记录实际打开的流对应哪个设备索引（供声源跟随对比）
                    self._active_device = dev_idx
                    self._active_level = 0.0
                    self._active_level_at = time.time()
                    # 声源自动跟随：未显式指定设备时才启用
                    self.start_source_watch()
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
                        # ── 声源自动跟随：有人请求换设备 → 关流重开 ──
                        # 放在 read 之前检查（read 会阻塞；切换请求由观察者线程置位）
                        if self._switch_to is not None:
                            new_idx = self._switch_to
                            self._switch_to = None
                            logger.info("[mic] 正在切换到设备 idx=%s（重开音频流）",
                                        new_idx)
                            try:
                                if stream is not None:
                                    stream.stop_stream()
                                    stream.close()
                            except Exception:
                                pass
                            stream = None
                            try:
                                stream = p.open(
                                    format=pyaudio.paInt16,
                                    channels=self.channels,
                                    rate=self.sample_rate,
                                    input=True,
                                    input_device_index=new_idx,
                                    frames_per_buffer=self.chunk_size,
                                )
                                rate = self.sample_rate
                                dev_idx = new_idx
                                self._active_device = new_idx
                                self._active_level = 0.0
                                # 换设备后噪声基线要重估，否则新设备的阈值会沿用旧的
                                noise_floor = 0.0
                                state = "standby"
                                frames = []
                                voice_points = 0
                                logger.info("[mic] 设备切换完成：idx=%s", new_idx)
                            except Exception as e:
                                logger.warning("[mic] 切换到 idx=%s 失败(%s)，回退原设备",
                                               new_idx, e)
                                try:
                                    stream = p.open(
                                        format=pyaudio.paInt16,
                                        channels=self.channels,
                                        rate=self.sample_rate,
                                        input=True,
                                        input_device_index=dev_idx,
                                        frames_per_buffer=self.chunk_size,
                                    )
                                except Exception as e2:
                                    logger.error("[mic] 回退也失败: %s，重试默认设备", e2)
                                    raise

                        data = stream.read(self.chunk_size, exception_on_overflow=False)

                        # 播报期间暂停：跳过所有录音处理，但必须读数据（保持流健康）
                        if self._listen_paused:
                            # 看门狗：暂停过久说明 resume 被漏掉了 → 强制恢复，
                            # 否则用户感知是"完全没反应"
                            if self._paused_at and (time.time() - self._paused_at) > self._max_pause_sec:
                                logger.warning(
                                    "[mic] 暂停已超过 %.0fs，看门狗强制恢复监听",
                                    self._max_pause_sec)
                                self._listen_paused = False
                                self._paused_at = 0.0
                            else:
                                voice_points = 0
                                if state == "listening":
                                    # 正在录音时被暂停：丢弃已录帧，回到 standby
                                    state = "standby"
                                    frames = []
                                continue

                        chunk_count += 1
                        # 每 chunk 都评估音量（更快捕捉起音，避免开头被粗采样错过）
                        # 优化：空数组先短路（avoid/stream异常），用均值近似绝对值均值
                        # （近零均值前提下 RMS*0.8 ≈ mean|·|，省去一次 abs 拷贝）
                        vol = self._quick_volume(data)
                        # 声源自动跟随的**当前设备电平**：取近期峰值（滑动），
                        # 供观察者线程与候选设备对比。用 max 而不是平均：
                        # 判据关心的是"这个设备能不能收到声音"，不是平均响度。
                        self._active_level = max(vol, self._active_level * 0.9)
                        self._active_level_at = time.time()

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
                            # 注：不再设噪声底上限。
                            # 原先的上限在环境安静时合理，但当环境有持续高音量音频时，
                            # 上限把阈值压得太低 → 每帧都触发 → 系统空转。
                            # 让噪声底自然跟踪环境音量，触发阈值 = 底噪 × 1.7 + 60。
                            # 如果环境音量太高导致人声无法超越阈值，需要降低外部音量
                            # 或使用耳机（消除扬声器→麦克风回声链路）。
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
                                    peak_vol = vol  # 用触发帧的音量作为初始峰值
                                    min_vol = vol   # 谷值也从触发帧开始
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
                            # 跟踪峰值和谷值
                            if vol > peak_vol:
                                peak_vol = vol
                            if vol < min_vol:
                                min_vol = vol
                            # ⚡ 自适应静音阈值（根因修复：必须参考**底噪**）
                            #   原判据只看峰值（peak*0.5）—— 多声源/嘈杂环境下
                            #   触发点本身就在底噪边缘，导致静音线低于实际底噪，
                            #   于是**永远判不出"说完了"**，录音一路撑到 max_seconds。
                            adaptive_silence = max(
                                noise_floor * 1.35,   # 主基准：明显高于环境噪声才算说话
                                peak_vol * 0.35,      # 下限：相对峰值不低于 35%
                                speech_volume * 0.5,  # 兜底：不低于基础阈值的一半
                            )
                            if vol > adaptive_silence:
                                silence_sec = 0.0
                            else:
                                silence_sec += chunk_sec
                            # 端点自适应：说话越大，容忍的停顿越长（0.3~0.5s）
                            end_silence = min(0.5, max(0.3, peak_vol / 2400.0 + 0.25))
                            # ⚡ 早退逻辑 1：录了 1.2 秒仍无"明显高于底噪"的语音 ⇒ 不是人话
                            if (total_sec >= 1.2
                                    and peak_vol < max(noise_floor * 1.8,
                                                       speech_volume * 1.5)):
                                logger.info(
                                    "[mic] 早退A：录音 %.1fs 无有效语音 "
                                    "(峰值 %.0f < 底噪 %.0f×1.8)，放弃本次",
                                    total_sec, peak_vol, noise_floor)
                                state = "standby"
                                frames = []
                                voice_points = 0
                                silence_sec = 0.0
                                total_sec = 0.0
                                continue
                            # ⚡ 早退逻辑 2：动态范围检测
                            #   真人说话峰谷比 > 3（有停顿、有重音）；
                            #   背景音频/回声比较平坦（峰谷比 < 2.5）。
                            #   录了 1.0 秒后检查，若音量变化太平 → 放弃。
                            if total_sec >= 1.0 and min_vol > 0:
                                dyn_range = peak_vol / min(min_vol, peak_vol)
                                if dyn_range < 2.5:
                                    logger.info(
                                        "[mic] 早退B：录音 %.1fs 动态范围不足 "
                                        "(峰 %.0f / 谷 %.0f = %.1fx < 2.5)，疑似背景音频",
                                        total_sec, peak_vol, min_vol, dyn_range)
                                    state = "standby"
                                    frames = []
                                    voice_points = 0
                                    silence_sec = 0.0
                                    total_sec = 0.0
                                    continue
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
                                        # 触发冷却：防环境音连珠炮触发转写（烧 CPU）。
                                        # 注意：防回声**不靠**这个值 —— 播报期间
                                        # 麦克风已被 pause_listening 彻底停掉，
                                        # 所以这里保持很短即可，否则会造成
                                        # "刚说完一句就听不见下一句"的失聪窗口。
                                        cooldown = 0.8  # 秒
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
                    self.stop_source_watch()
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

    def pause_listening(self) -> None:
        """暂停监听（播报期间防回声）：设置暂停标志，监听线程会在下次循环时跳过录音。

        ⚠️ 配套的 `resume_listening()` 必须被调用，否则麦克风永久失聪（用户感知：
        "完全没反应"）。调用方用 try/finally 兜底；这里再加一道看门狗，
        暂停超过 `max_pause_sec` 自动恢复，防止任何漏掉的路径把麦克风锁死。
        """
        if not self._listen_paused:
            logger.info("[mic] 暂停监听（播报期间防回声）")
        self._listen_paused = True
        self._paused_at = time.time()

    def resume_listening(self) -> None:
        """恢复监听（播报结束后）

        ★ 同时清掉触发冷却：冷却的目的是"防回声/环境音连珠炮"，
        而播报期间的暂停已经**彻底**挡住了回声（麦克风根本没在录）。
        若此时还留着冷却，用户在这段时间说话会被静默忽略 ——
        感知就是"她听不见我说话了"。恢复即代表"可以听"，冷却应归零。
        """
        if self._listen_paused:
            logger.info("[mic] 恢复监听")
        self._listen_paused = False
        self._paused_at = 0.0
        self._cooldown_until = 0.0

    def is_paused(self) -> bool:
        """当前是否处于暂停状态"""
        return bool(self._listen_paused)

    def is_listening(self) -> bool:
        """是否正在监听"""
        return bool(self._listening)

    # ══════════════════════════════════════════════
    #  声源自动跟随：自动判断哪个设备有声音并切过去
    # ══════════════════════════════════════════════

    def request_device_switch(self, index: Optional[int]) -> None:
        """请求监听线程切到指定设备（线程安全）。

        监听线程在**每个 chunk 之后**检查本标志，非 None 就关流重开。
        不在观察者线程里直接开关流：两条线程同时操作 PyAudio 会访问违例
        （0xC0000005，实测踩过）。
        """
        self._switch_to = index

    def start_source_watch(self) -> None:
        """启动声源观察者：定期探测各设备，发现"别的设备更有声音"就自动切过去。

        ## 为什么需要它（而不是只在启动时挑一次）

        启动时挑设备**只在用户此刻正在说话时有效**。用户开机时不说话时，
        所有设备都读到底噪，挑选判据失效（实测：0.15s 短采样时每个设备
        都读 0.2）。而"哪个设备收到声音"是**会变的**：
          · 用户从"本机麦克风"改到"远程桌面虚拟麦克风"
          · UU远程 会话建立/断开
          · Windows 重排设备（实测同一麦克风在 idx=1/7/15/22 之间跳）

        所以正确的做法是**运行期持续观察**：哪个设备在动就跟随它。

        ## 与"用户显式指定"的关系

        用户在 config 写了 `voice.mic_device`（或按热键轮换）时**不启动观察者** ——
        用户的明确选择优先，自动跟随会和它打架（用户刚切过去就被切回来）。
        """
        if self._pin_device:
            logger.info("[mic] 用户已显式指定设备，不启用声源自动跟随")
            return
        if self._watch_thread is not None and self._watch_thread.is_alive():
            return
        self._watch_stop = False
        self._watch_thread = threading.Thread(
            target=self._watch_loop, name="mic-source-watch", daemon=True)
        self._watch_thread.start()
        logger.info("[mic] 声源自动跟随已启用（每 %.0fs 检查一次）",
                    self._watch_interval)

    def stop_source_watch(self) -> None:
        """停止声源观察者"""
        self._watch_stop = True

    def _watch_loop(self) -> None:
        """观察者主循环：定期比对候选设备电平，必要时请求切换。"""
        # 首轮等一个间隔再开始：让监听线程先把 _active_level 填上
        while not self._watch_stop:
            for _ in range(int(self._watch_interval * 10)):
                if self._watch_stop:
                    return
                time.sleep(0.1)
            try:
                self._watch_tick()
            except Exception as e:
                logger.debug("[mic] 声源观察异常: %s", e)

    def _watch_tick(self) -> None:
        """一次观察：选一个候选，若明显比当前设备有声音 → 请求切换。

        只探**一个**候选（轮转），不是每轮全探：全探 10 个设备 ×0.6s = 6 秒，
        期间麦克风被独占、用户说话可能被漏掉。轮转则每轮只占 0.6s，
        且能在 N 轮内覆盖所有设备。
        """
        if self._listen_paused:
            return                      # 播报期间不折腾设备
        if time.time() - self._last_switch_at < self._switch_cooldown_sec:
            return                      # 刚切过，给它稳定时间
        if self.is_recording:
            return                      # 正在录音，别动

        cands = [d for d in self.list_input_devices() if d.get("kind") == "mic"]
        if len(cands) <= 1:
            return
        self._watch_rotor = (getattr(self, "_watch_rotor", 0) + 1) % len(cands)
        cand = cands[self._watch_rotor]
        if cand["index"] == self._active_device:
            return                      # 轮到的就是当前设备，跳过

        try:
            rows = self.probe_device_levels(seconds=0.6, indices=[cand["index"]])
        except Exception as e:
            logger.debug("[mic] 候选探测失败: %s", e)
            return
        if not rows or not rows[0].get("ok"):
            return
        cand_level = float(rows[0].get("avg") or 0.0)
        cur_level = float(self._active_level or 0.0)

        # 判据：候选必须**明显**更有声音（cur*ratio），且有绝对下限
        # （防"当前设备静音时，候选的底噪也算赢"）。
        if cand_level < max(120.0, cur_level * self._switch_ratio):
            return
        # 当前设备本来就在正常收音（刚更新过且电平不低）→ 不切，避免打架
        fresh = (time.time() - self._active_level_at) < 3.0
        if fresh and cur_level >= 120.0:
            return

        logger.info(
            "[mic] 声源自动切换：idx=%s (%.1f) → idx=%d (%.1f, %s)",
            self._active_device, cur_level, cand["index"], cand_level,
            cand["name"][:32])
        self._last_switch_at = time.time()
        self._auto_pick_reason = (
            f"自动跟随：idx={cand['index']} 电平 {cand_level:.1f} "
            f"远超当前 {cur_level:.1f}")
        self.request_device_switch(int(cand["index"]))

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

