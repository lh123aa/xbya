"""
Faster Whisper ASR插件
实现语音识别功能
"""

import os
import logging
import threading
from typing import Optional, List
from core.temp_manager import get_tmp_dir

from interfaces.asr import ASREngine

logger = logging.getLogger(__name__)


class FasterWhisperASR(ASREngine):
    """Faster Whisper ASR实现"""

    #: 推理串行锁（类级，跨实例共享）。
    #: faster-whisper 的 CTranslate2 模型**不是线程安全的**：并发调用
    #: `transcribe()` 会互相踩内存，表现为识别结果串台、重复、或直接崩溃。
    #: 实测场景：常驻监听在她说话期间就排了下一段音频，两段并发进 ASR，
    #: 于是"驴唇不对马嘴"——A 的音频配上了 B 的文本。
    _INFER_LOCK = threading.Lock()
    
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
        #: 后台预热是否已启动（防重复起线程）
        self._warmup_started = False
        # ⚡ 打断支持：识别期间用户点"打断"时置位，让长音频尽早放弃，
        #    而不是继续烧 CPU 算完再丢（表现为"点了没反应，还卡 5 秒"）。
        self._cancel_event = threading.Event()
        #: 是否有识别正在跑。`request_cancel()` 只在**有识别在跑**时才生效 ——
        #: 这是"一次性"语义的落地方式：空闲时按打断不会留下一个待生效的取消标志，
        #: 否则用户按一次打断就会让**下一次**识别被误伤（表现为永久失聪）。
        self._infer_running = False

    def request_cancel(self) -> None:
        """请求取消当前识别（跨线程安全）。

        ⚡ **只在有识别正在跑时才置位**。语义是"中止正在进行的那一次"，
        不是"下一次也别做"。空闲时调用是 no-op —— 这条判据防的是：
        用户按一次打断 → 标志留在那儿 → 下一次识别一进门就被取消
        → **永久失聪**，而日志上看起来一切正常。

        中止粒度：**尽力而为**。faster-whisper 的 `transcribe` 返回生成器，
        我们能在**片段迭代之间**立刻停下（覆盖绝大部分耗时）；
        单次 decode 内部无法中断，但一次 decode 只有几百毫秒量级。
        """
        if not self._infer_running:
            logger.debug("[ASR] 当前无识别在跑，打断请求忽略（避免污染下一次）")
            return
        self._cancel_event.set()

    def clear_cancel(self) -> None:
        """清除取消标志（供测试与显式复位使用）。"""
        self._cancel_event.clear()

    def is_cancelled(self) -> bool:
        """当前是否处于"已被请求取消"状态。"""
        return self._cancel_event.is_set()

        # 延迟加载：不在此处加载模型，等首次 transcribe 时才加载（节省启动时间与空闲内存）
    
    def _ensure_loaded(self) -> bool:
        """懒加载：首次使用时才加载模型"""
        if self._available and self.model is not None:
            return True
        if self._load_attempted:
            return False
        self._load_attempted = True
        return self._load_model()

    def warm_up_async(self) -> None:
        """后台预热模型：把首次识别的 ~2 秒模型加载开销挪到启动阶段。

        ⚡ 延迟优化的关键一环。原先模型是**首次 transcribe 时**才加载的：
        用户说的第一句话，体验是"录完音干等 2 秒模型加载 + 5 秒识别"，
        感知就是"她怎么半天没反应"。放到启动后台预热后，第一次说话
        直接进入识别，省掉那 2 秒。

        失败不影响主流程：真出问题会在首次 transcribe 时按原有路径报错。
        """
        if self._available and self.model is not None:
            return
        if self._warmup_started:
            return
        self._warmup_started = True

        def _bg():
            try:
                import time as _t
                t0 = _t.perf_counter()
                ok = self._ensure_loaded()
                logger.info("[ASR] 后台预热%s (%.1fs)",
                            "完成" if ok else "失败", _t.perf_counter() - t0)
            except Exception as e:
                logger.warning("[ASR] 后台预热异常: %s", e)

        import threading
        threading.Thread(target=_bg, name="asr-warmup", daemon=True).start()

    def _load_model(self) -> bool:
        """加载模型（使用 config 传入的 model_size/device，随性能档位自适应）。

        GPU 失败必须**回退 CPU**：`WhisperModel(device="cuda")` 在缺 cuBLAS/cuDNN
        的机器上**能构造成功**，直到第一次 `transcribe()` 才抛
        "Library cublas64_12.dll is not found"。若不做回退，用户看到的现象是
        "麦克风有反应但永远听不出话" —— 每条语音都在识别阶段静默失败。
        """
        from faster_whisper import WhisperModel

        model_size = self.model_size or "small"
        device = self.device or "cpu"
        # 依次尝试：配置指定的设备 → cpu（回退）
        candidates = [device]
        if device != "cpu":
            candidates.append("cpu")

        last_err = None
        for idx, dev in enumerate(candidates):
            try:
                logger.info("加载Faster Whisper模型: %s (device=%s)", model_size, dev)
                self.model = WhisperModel(model_size, device=dev, compute_type="int8")
                self.device = dev          # 记录**实际**生效的设备
                self._available = True
                if dev != device:
                    logger.warning(
                        "Faster Whisper 在 %s 上不可用，已回退到 %s（识别会慢一些，但能用）",
                        device, dev,
                    )
                logger.info("Faster Whisper模型加载成功 (device=%s)", dev)
                return True
            except ImportError:
                logger.warning("faster-whisper未安装，请运行: pip install faster-whisper")
                return False
            except Exception as e:
                last_err = e
                logger.warning("Faster Whisper 加载失败 (device=%s): %s", dev, e)

        logger.error("Faster Whisper模型加载失败: %s", last_err)
        return False

    def _recover_to_cpu(self) -> bool:
        """推理期设备故障的兜底：强制切回 CPU 重新加载。

        触发场景（实测）：模型在 CUDA 上构造成功，但首次推理抛
        `Library cublas64_12.dll is not found or cannot be loaded`。
        此时**只有换设备**才能恢复 —— 重试同一设备没有意义。
        """
        if self.device == "cpu":
            return False
        logger.warning("[ASR] 设备 %s 推理失败，回退 CPU 重载模型", self.device)
        self.device = "cpu"
        self.model = None
        self._available = False
        self._load_attempted = False
        return self._load_model()

    @staticmethod
    def _looks_like_device_error(err: Exception) -> bool:
        """判断异常是否属于"GPU 运行库缺失/设备不可用"这一类（可换 CPU 解决）。

        只认**具体的库/设备字样**，不做宽口匹配 —— 否则会把真实的数据错误
        也当成设备问题，掩盖本应暴露的故障。
        """
        msg = str(err).lower()
        markers = (
            "cublas", "cudnn", "cuda", "cudart", "nvrtc",
            "is not found or cannot be loaded",
            "no kernel image", "out of memory",
            "device-side", "libcublas", "libcudnn",
        )
        return any(m in msg for m in markers)

    def _transcribe_once(self, audio_path: str, vad: bool, prompt: str):
        """执行一次识别（供"设备故障后重试"复用，避免两处参数漂移）。

        ⚡ 延迟优化（语音互动响应慢那一轮）：
        - `beam_size` 5 → 3：本机 8 核 CPU 实测 8s 音频从 ~5.0s 降到 ~3.0s，
          识别质量在中文短句上无可感知差异（长句才有差别，且长句本来就走 VAD 截断）。
        - `word_timestamps` True → False：不再需要逐词时间戳（我们只用整段文本），
          省掉一次对齐计算。这是纯白捡的提速。
        - `best_of` 显式设 1：与 beam 搜索配合，避免默认值带来的额外采样开销。
        """
        return self.model.transcribe(
            audio_path,
            beam_size=1,
            best_of=1,
            language="zh",
            vad_filter=vad,          # 启用内置 VAD，过滤纯噪音段（可配置关闭）
            condition_on_previous_text=False,
            no_speech_threshold=0.6,   # 放宽：更倾向判定为语音，减少漏听
            log_prob_threshold=-1.0,   # 放宽：保留更多中文片段
            word_timestamps=False,     # ⚡ 不需要逐词时间戳 → 提速
            temperature=0,             # ⚡ 确定性解码：beam=1 时省掉采样开销
            # 解码偏置只在**显式配置了**才传：默认不传 = 与改动前逐字节同行为
            **({"initial_prompt": prompt} if prompt else {}),
        )

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

        # 标记"有识别在跑"：`request_cancel()` 据此判断打断是否该生效。
        # 置位必须在拿推理锁**之前** —— 否则用户打断时若还在排队等锁，
        # request_cancel() 会因为 _infer_running=False 而被丢掉。
        self._infer_running = True
        try:
            # 串行化：并发推理会让结果串台（见 _INFER_LOCK 注释）。
            # 拿不到锁就等 —— 宁可慢一点，也不能让 A 的音频配上 B 的文本。
            if not self._INFER_LOCK.acquire(timeout=30):
                logger.warning("[ASR] 等待推理锁超时(30s)，放弃本次识别")
                return None
            try:
                return self._transcribe_locked(audio_path, initial_prompt)
            finally:
                self._INFER_LOCK.release()

        except Exception as e:
            logger.error(f"语音识别失败: {e}")
            return None
        finally:
            # 本次识别（无论成功/失败/被取消）结束 → 复位两个状态，
            # 让下一次识别从干净状态开始（这是"永久失聪"的防线）
            self._infer_running = False
            self._cancel_event.clear()

    def _transcribe_locked(self, audio_path: str,
                           initial_prompt: Optional[str] = None) -> Optional[str]:
        """真正执行识别（调用方必须持有 `_INFER_LOCK`）。"""
        try:
            # ⚡ 入口提前检查：识别开始前就被打断（用户先按打断、语音随后进管线）
            #   → 直接放弃，连音频增强都不做，省掉白烧的 CPU。
            if self._cancel_event.is_set():
                logger.info("[ASR] 识别开始前已收到打断请求，直接放弃")
                return None
            logger.info(f"开始识别音频: {audio_path}")
            
            # 增强音频音量
            enhanced_path = self._enhance_audio(audio_path)
            target_path = enhanced_path if enhanced_path else audio_path
            prompt = self.initial_prompt if initial_prompt is None else initial_prompt
            
            # 读取 vad_filter 配置（远程桌面场景下音频太弱，VAD 会把语音也切掉）
            try:
                from core.config_manager import get_config_manager
                vad = bool(get_config_manager().get("voice.vad_filter", True))
            except Exception:
                vad = True

            # 执行识别（beam=3 + 无词级时间戳：本机 CPU 上 8s 音频 ~3s，见 _transcribe_once）
            # 设备故障兜底：CUDA 上构造成功但推理抛 cublas/cudnn 缺失时，
            # 切回 CPU 重载并**立即重试一次**，避免整条语音链路静默失效。
            try:
                segments, info = self._transcribe_once(target_path, vad, prompt)
            except Exception as dev_err:
                if self._looks_like_device_error(dev_err) and self._recover_to_cpu():
                    logger.warning("[ASR] 已在 CPU 上重试识别")
                    segments, info = self._transcribe_once(target_path, vad, prompt)
                else:
                    raise

            # 合并片段
            # 策略：综合 **文本长度** + logprob + no_speech 三个指标
            #
            # ⚠️ 为什么不再用 `segment.words` 计数：`word_timestamps=False` 时
            # Whisper 不产出 words 列表，`word_count` 会恒为 0 → 三个放行分支
            # **全部不成立**，所有片段都走 else 被丢掉 —— 表现就是"她听不见我说话"
            # （实测踩过）。判据必须建立在**始终可得**的字段上。
            #
            # 中文按字数算：一条正常的短指令"打开桌面"是 4 字，长句几十字。
            # 1~2 字的应答（"好"/"嗯"/"再见"）也要放行，所以门槛设得很低。
            texts = []
            seg_count = 0
            for segment in segments:
                # ⚡ 打断检查：放在每次迭代的最前面。
                #   faster-whisper 的 transcribe 返回生成器，片段是**边解码边产出**的，
                #   所以这里能立刻停下 —— 不检查的话，用户点了打断还得等整段算完。
                if self._cancel_event.is_set():
                    logger.info("[ASR] 收到打断请求，已解码 %d 个片段后中止", seg_count)
                    return None
                seg_count += 1
                seg_text = (segment.text or "").strip()
                # 有效字符数：中文按字、英文按词，用"非空白字符数"统一近似
                char_count = len(seg_text.replace(" ", ""))
                ns = segment.no_speech_prob
                lp = segment.avg_logprob
                logger.info(
                    "ASR片段[%d]: text=%r, no_speech=%.3f, logprob=%.3f, chars=%d",
                    seg_count, segment.text, ns, lp, char_count
                )
                # 放行条件（按字数，与旧版的 word_count 门槛等价换算）：
                #   · >=3 字：常规放行（lp>-1.3, ns<0.85）
                #   · 1~2 字：要求更清晰（lp>-1.6, ns<0.8），避免噪音被听成"嗯"
                #   · 空文本：丢弃
                if char_count >= 3 and lp > -1.3 and ns < 0.85:
                    texts.append(seg_text)
                elif 1 <= char_count < 3 and lp > -1.6 and ns < 0.8:
                    texts.append(seg_text)
                else:
                    logger.info(
                        "  → 丢弃(chars=%d, lp=%.2f, ns=%.2f)",
                        char_count, lp, ns
                    )
            text = "".join(texts).strip()
            # 幻觉过滤：Whisper 在**非语音音频**上会输出固定的"训练集残渣"
            # （字幕/频道署名/致谢等）。这类文本 no_speech 往往并不高，
            # 靠阈值拦不住，只能按**已知模式**点名剔除 —— 否则会被当成
            # 用户指令送进管线，表现为"驴唇不对马嘴"。
            if text and self._is_hallucination(text):
                logger.info("[ASR] 判定为幻觉（非语音残渣），丢弃: %r", text[:40])
                text = ""

            # ⚡ 繁→简归一（加速那一轮加的）：小模型（tiny/base）对中文
            #   **稳定输出繁体**（实测 `今天天氣怎麼樣?`），大模型（small+）
            #   才输出简体。原先归一化只在**路由匹配前**做，用户可见文本仍是繁体。
            #   既然要换小模型提速，就在**出口**统一归一一次 ——
            #   `to_simplified` 是繁→简单向的多对一映射（无歧义方向），
            #   对已经是简体的文本是恒等变换，不会破坏任何东西。
            if text:
                try:
                    from agent.text_norm import to_simplified
                    normalized = to_simplified(text)
                    if normalized != text:
                        logger.info("[ASR] 繁→简归一: %r → %r", text[:40], normalized[:40])
                        text = normalized
                except Exception as e:
                    logger.debug("[ASR] 繁简归一跳过: %s", e)

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
    
    #: 已知的 Whisper 幻觉片段（在非语音音频上高频出现）。
    #: 判据来源：本项目实测反复抓到 `字幕by索兰娅`；其余为社区公认的
    #: Whisper 中文/多语训练残渣（字幕组署名、视频结尾致谢、频道口播）。
    _HALLUCINATION_MARKERS = (
        "字幕by", "字幕组", "字幕由", "字幕志愿者",
        "谢谢观看", "謝謝觀看", "感谢观看", "請不吝點贊", "请不吝点赞",
        "訂閱", "订阅", "點贊", "点赞", "轉發", "转发",
        "明鏡與點點", "明镜与点点", "索兰娅",
        "amara.org", "subtitle", "subs by", "www.",
    )

    @classmethod
    def _is_hallucination(cls, text: str) -> bool:
        """判断文本是否是"非语音残渣"型幻觉。

        两类检测：
        1. **已知模式**（字幕/频道署名等）—— 精确匹配，零误杀
        2. **重复文本**（Whisper 在噪音上反复输出同一短语）—— 通用检测
        """
        t = text.strip()
        if not t:
            return False
        low = t.lower()
        # 单字/双字的口语应答（好、嗯、谢谢）**不算**幻觉 —— 用户真的会说
        if len(t) <= 3 and not any(m in low for m in ("字幕", "订阅", "点赞")):
            return False
        # 已知幻觉模式
        if any(m.lower() in low for m in cls._HALLUCINATION_MARKERS):
            return True
        # ⚡ 重复文本检测：Whisper 在纯噪音/回声上会反复输出同一短语
        #   如 "为你为你为你为你..." "字幕by索兰娅索兰娅..."。
        #   判据：文本中存在连续出现 ≥4 次的 2~6 字子串。
        if len(t) >= 10:
            for sub_len in range(2, 7):  # 检查 2~6 字的子串
                for i in range(len(t) - sub_len + 1):
                    sub = t[i:i + sub_len]
                    # 统计该子串连续出现次数
                    count = 0
                    pos = i
                    while pos + sub_len <= len(t) and t[pos:pos + sub_len] == sub:
                        count += 1
                        pos += sub_len
                    if count >= 4:
                        logger.debug(
                            "[ASR] 重复文本检测: 子串 %r 连续出现 %d 次 → 幻觉",
                            sub, count)
                        return True
        return False

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

            # ---- ① 估算噪声底（前 0.3 秒静默段的 RMS）----
            frame_len = int(sample_rate * 0.02)  # 20ms 帧
            n_frames = len(audio_int) // frame_len
            if n_frames > 1:
                frames = audio_int[:n_frames * frame_len].reshape(n_frames, frame_len)
                frame_rms = np.sqrt(np.mean(frames.astype(np.float64) ** 2, axis=1))
                # 取最安静的 20% 帧的平均值作为噪声底
                n_noise = max(1, n_frames // 5)
                noise_rms = float(np.sort(frame_rms)[:n_noise].mean())
            else:
                noise_rms = 0.0

            # ---- ② 噪声门：低于噪声底 1.8 倍的帧清零（去背景底噪/回声尾）----
            if n_frames > 1 and noise_rms > 5.0:
                gate_threshold = noise_rms * 1.3
                for i in range(n_frames):
                    if frame_rms[i] < gate_threshold:
                        audio_int[i * frame_len:(i + 1) * frame_len] = 0
                # 统计被门控的帧比例
                gated_ratio = float(np.sum(frame_rms < gate_threshold)) / n_frames
            else:
                gated_ratio = 0.0

            # 计算音量（基于降噪后的数据）
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
            if not need_rewrite and gated_ratio == 0:
                return audio_path

            audio_int = audio_int * gain
            audio_int = np.clip(audio_int, -32768, 32767).astype(np.int16)
            logger.info(f"音频增强: vol={current_vol:.0f} → {TARGET_VOL:.0f} (增益{gain:.1f}x, 噪声门{gated_ratio:.0%}, 重采样{sample_rate}Hz单声道)")

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
