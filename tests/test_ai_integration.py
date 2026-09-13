"""
AI集成测试
"""

import pytest
import sys
from pathlib import Path

# 添加项目根目录到系统路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


class TestOllamaLLM:
    """Ollama LLM测试类"""
    
    def test_import_ollama_plugin(self):
        """测试导入Ollama插件"""
        from plugins.llm.ollama.plugin import OllamaLLM
        
        assert OllamaLLM is not None
    
    def test_ollama_initialization(self):
        """测试Ollama初始化"""
        from plugins.llm.ollama.plugin import OllamaLLM
        
        # 尝试初始化（可能失败，因为Ollama服务可能未运行）
        llm = OllamaLLM()
        assert llm.model is not None
        assert llm.base_url is not None
    
    def test_ollama_model_info(self):
        """测试获取模型信息"""
        from plugins.llm.ollama.plugin import OllamaLLM
        
        llm = OllamaLLM()
        info = llm.get_model_info()
        
        assert "name" in info
        assert "base_url" in info
        assert "available" in info


class TestFasterWhisperASR:
    """Faster Whisper ASR测试类"""
    
    def test_import_faster_whisper_plugin(self):
        """测试导入Faster Whisper插件"""
        from plugins.asr.faster_whisper.plugin import FasterWhisperASR
        
        assert FasterWhisperASR is not None
    
    def test_faster_whisper_initialization(self):
        """测试Faster Whisper初始化"""
        from plugins.asr.faster_whisper.plugin import FasterWhisperASR
        
        # 尝试初始化（可能失败，因为模型可能未下载）
        asr = FasterWhisperASR()
        assert asr.model_size is not None
        assert asr.device is not None
    
    def test_faster_whisper_supported_formats(self):
        """测试支持的音频格式"""
        from plugins.asr.faster_whisper.plugin import FasterWhisperASR
        
        asr = FasterWhisperASR()
        formats = asr.get_supported_formats()
        
        assert isinstance(formats, list)
        assert "wav" in formats
        assert "mp3" in formats


class TestASRDeviceFallback:
    """CUDA 设备故障必须回退 CPU。

    实测故障：`WhisperModel(device="cuda")` 在缺 cuBLAS 的机器上**能构造成功**，
    直到第一次 `transcribe()` 才抛 "Library cublas64_12.dll is not found"。
    不做回退时的用户现象是"麦克风有反应但永远听不出话" —— 每条语音都在
    识别阶段静默失败（异常被宽口 except 吞掉，只留一行 ERROR 日志）。
    """

    def test_device_error_markers_detected(self):
        """常见的 GPU 运行库缺失报错应被识别为"设备问题" """
        from plugins.asr.faster_whisper.plugin import FasterWhisperASR as A

        real = [
            "Library cublas64_12.dll is not found or cannot be loaded",
            "Could not load library libcudnn_ops.so.9",
            "CUDA out of memory. Tried to allocate 2.00 GiB",
            "no kernel image is available for execution on the device",
            "cudart64_12.dll not found",
        ]
        for msg in real:
            assert A._looks_like_device_error(RuntimeError(msg)) is True, msg

    def test_non_device_error_not_misclassified(self):
        """非设备类错误**不能**被当成设备问题（否则会掩盖真实故障）"""
        from plugins.asr.faster_whisper.plugin import FasterWhisperASR as A

        others = [
            "Invalid audio file format",
            "FileNotFoundError: no such file: tmp.wav",
            "Permission denied",
            "list index out of range",
        ]
        for msg in others:
            assert A._looks_like_device_error(RuntimeError(msg)) is False, msg

    def test_recover_to_cpu_reloads_model(self, monkeypatch):
        """_recover_to_cpu 应把 device 改成 cpu 并重新加载"""
        from plugins.asr.faster_whisper import plugin as mod

        asr = mod.FasterWhisperASR(model_size="small", device="cuda")
        calls = []

        class FakeModel:
            def __init__(self, size, device, compute_type):
                calls.append(device)
                if device == "cuda":
                    raise RuntimeError("Library cublas64_12.dll is not found")

        monkeypatch.setattr(mod, "WhisperModel", FakeModel, raising=False)
        import faster_whisper
        monkeypatch.setattr(faster_whisper, "WhisperModel", FakeModel)

        ok = asr._recover_to_cpu()
        assert ok is True, "回退 CPU 应成功"
        assert asr.device == "cpu"
        assert "cpu" in calls, "应当真的用 cpu 重新构造过一次"

    def test_recover_noop_when_already_cpu(self):
        """已经在 CPU 上时 _recover_to_cpu 不应做任何事"""
        from plugins.asr.faster_whisper.plugin import FasterWhisperASR

        asr = FasterWhisperASR(model_size="small", device="cpu")
        assert asr._recover_to_cpu() is False

    def test_load_falls_back_to_cpu_on_construct_error(self, monkeypatch):
        """构造期就失败时，_load_model 也应直接回退 CPU"""
        from plugins.asr.faster_whisper import plugin as mod

        tried = []

        class FakeModel:
            def __init__(self, size, device, compute_type):
                tried.append(device)
                if device != "cpu":
                    raise RuntimeError("cuda unavailable")

        import faster_whisper
        monkeypatch.setattr(faster_whisper, "WhisperModel", FakeModel)

        asr = mod.FasterWhisperASR(model_size="small", device="cuda")
        assert asr._load_model() is True
        assert tried == ["cuda", "cpu"], f"应先试 cuda 再回退 cpu，实际 {tried}"
        assert asr.device == "cpu", "实际生效的设备应被记录下来"


class TestASRHallucinationFilter:
    """幻觉过滤：非语音音频上 Whisper 会输出固定的"训练集残渣"。

    实测：环境噪音被识别成 `字幕by索兰娅`，然后被当成用户指令送进管线，
    表现为**驴唇不对马嘴** —— 用户说 A，她去执行 B。
    """

    def test_known_hallucinations_rejected(self):
        from plugins.asr.faster_whisper.plugin import FasterWhisperASR as A

        for t in ("字幕by索兰娅", "謝謝觀看", "请不吝点赞 订阅 转发 打赏支持明镜与点点栏目",
                  "Subs by Amara.org", "字幕组 荣誉出品"):
            assert A._is_hallucination(t) is True, f"未拦截幻觉: {t!r}"

    def test_real_speech_not_rejected(self):
        """反方向：真实口语（含短应答）绝不能误杀"""
        from plugins.asr.faster_whisper.plugin import FasterWhisperASR as A

        for t in ("在嗎?在嗎?", "你能不能听到", "今天天气怎么样", "帮我列一下桌面文件",
                  "谢谢", "好", "嗯", "不要", "取消"):
            assert A._is_hallucination(t) is False, f"误杀了真实语音: {t!r}"

    def test_short_reply_never_treated_as_hallucination(self):
        """1~3 字的口语应答一律放行（用户真的会说"谢谢/好/嗯"）"""
        from plugins.asr.faster_whisper.plugin import FasterWhisperASR as A

        for t in ("好", "嗯", "谢谢", "是的", "不要"):
            assert A._is_hallucination(t) is False


class TestASRInferenceSerialization:
    """推理必须串行：并发调用 CTranslate2 会让结果串台。

    实测：常驻监听在她说话期间就排了下一段音频，两段并发进 ASR，
    于是**A 的音频配上了 B 的文本** —— 用户感知就是"驴唇不对马嘴"。
    """

    def test_lock_exists_and_is_shared(self):
        from plugins.asr.faster_whisper.plugin import FasterWhisperASR as A
        import threading

        assert hasattr(A, "_INFER_LOCK"), "缺少推理串行锁"
        assert isinstance(A._INFER_LOCK, type(threading.Lock()))

    def test_concurrent_transcribe_is_serialized(self, monkeypatch, tmp_path):
        """两个线程同时 transcribe，实际进入模型的时间段不得重叠"""
        from plugins.asr.faster_whisper import plugin as mod
        import threading
        import time as _t

        intervals = []
        lock_guard = threading.Lock()

        class FakeSeg:
            text = "测试"
            no_speech_prob = 0.1
            avg_logprob = -0.5
            words = [1, 2, 3]

        class FakeModel:
            def transcribe(self, path, **kw):
                start = _t.time()
                _t.sleep(0.25)              # 模拟推理耗时
                with lock_guard:
                    intervals.append((start, _t.time()))
                return [FakeSeg()], None

        asr = mod.FasterWhisperASR(model_size="small", device="cpu")
        asr.model = FakeModel()
        asr._available = True
        asr._load_attempted = True
        asr._enhance_audio = lambda p: None      # 跳过音频处理

        wav = tmp_path / "a.wav"
        wav.write_bytes(b"\x00\x00" * 100)

        threads = [threading.Thread(target=asr.transcribe, args=(str(wav),))
                   for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(intervals) == 2, "两次推理都应完成"
        intervals.sort()
        gap = intervals[1][0] - intervals[0][1]
        assert gap >= -0.01, \
            f"两次推理重叠了（gap={gap:.3f}s）—— 并发会让识别结果串台"


class TestASRCancellation:
    """ASR 可打断：用户在识别期间按打断，识别应尽早停下。

    为什么必须支持：本机 8 核 CPU 上 8 秒音频要跑 3~5 秒。旧行为是
    "打断只重置状态、ASR 仍跑完"，用户点了打断还得白等几秒 —— 感知是"没反应"。
    """

    def _make_asr(self, tmp_path, segments):
        """构造一个用假模型驱动的 ASR（帧是**生成器**，模拟真实边解码边产出）"""
        from plugins.asr.faster_whisper import plugin as mod

        class FakeModel:
            def transcribe(self, path, **kw):
                # 关键：返回生成器（faster-whisper 的真实契约），
                # 这样取消检查能落在"片段之间"
                def gen():
                    for s in segments:
                        yield s
                return gen(), None

        asr = mod.FasterWhisperASR(model_size="small", device="cpu")
        asr.model = FakeModel()
        asr._available = True
        asr._load_attempted = True
        asr._enhance_audio = lambda p: None
        return asr

    def _seg(self, text, ns=0.1, lp=-0.5):
        class S:
            pass
        s = S()
        s.text = text
        s.no_speech_prob = ns
        s.avg_logprob = lp
        return s

    def test_cancel_when_idle_is_noop(self, tmp_path):
        """★ 空闲时按打断不应留下"待生效"的取消标志

        这是"永久失聪"的防线：用户按一次打断（当时没有识别在跑），
        如果标志留在那儿，**下一次**识别一进门就会被取消 ——
        表现是"打断一次之后就再也听不见了"，而且日志上一切正常。
        """
        asr = self._make_asr(tmp_path, [self._seg("你好世界")])
        wav = tmp_path / "a.wav"
        wav.write_bytes(b"\x00\x00" * 100)

        asr.request_cancel()            # 空闲时打断 → 应为 no-op
        assert asr.is_cancelled() is False, \
            "空闲时的打断请求不应置位（会污染下一次识别）"
        out = asr.transcribe(str(wav))
        assert out == "你好世界", f"下一次识别被误伤，实际={out!r}"

    def test_cancel_flag_reset_after_run(self, tmp_path):
        """一次识别结束后两个状态都必须复位（下轮从干净状态开始）"""
        asr = self._make_asr(tmp_path, [self._seg("你好世界")])
        wav = tmp_path / "a.wav"
        wav.write_bytes(b"\x00\x00" * 100)

        asr.transcribe(str(wav))
        assert asr._infer_running is False, "识别结束后 _infer_running 必须复位"
        assert asr.is_cancelled() is False, "识别结束后取消标志必须清掉"

    def test_cancel_mid_stream_stops_early(self, tmp_path):
        """解码中途取消 → 立即返回 None，不把整段算完

        判据用**消费掉的片段数**：假生成器有 5 段，第 1 段之后取消，
        那么第 2 段起不应被消费。
        """
        consumed = []

        from plugins.asr.faster_whisper import plugin as mod

        asr_holder = {}

        class FakeModel:
            def transcribe(self, path, **kw):
                def gen():
                    for i in range(5):
                        consumed.append(i)
                        if i == 1:
                            # 模拟"用户此刻按了打断"
                            asr_holder["asr"].request_cancel()
                        yield self._seg(f"片段{i}")
                return gen(), None

            def _seg(self, text):
                class S:
                    pass
                s = S()
                s.text = text
                s.no_speech_prob = 0.1
                s.avg_logprob = -0.5
                return s

        asr = mod.FasterWhisperASR(model_size="small", device="cpu")
        asr_holder["asr"] = asr
        asr.model = FakeModel()
        asr._available = True
        asr._load_attempted = True
        asr._enhance_audio = lambda p: None

        wav = tmp_path / "a.wav"
        wav.write_bytes(b"\x00\x00" * 100)
        out = asr.transcribe(str(wav))

        assert out is None, "中途取消应返回 None"
        assert len(consumed) <= 3, \
            f"取消后仍消费了 {len(consumed)} 个片段（应尽早停下）: {consumed}"

    def test_cancel_helpers_exist_and_are_idempotent(self):
        """三个接口存在且幂等；打断只在"有识别在跑"时生效"""
        from plugins.asr.faster_whisper.plugin import FasterWhisperASR

        asr = FasterWhisperASR(model_size="small", device="cpu")
        assert asr.is_cancelled() is False

        # 空闲时：打断是 no-op（防污染下一次）
        asr.request_cancel()
        asr.request_cancel()
        assert asr.is_cancelled() is False, "空闲时打断不应置位"

        # 模拟"识别在跑"：打断应生效，且重复请求不抛异常
        asr._infer_running = True
        asr.request_cancel()
        asr.request_cancel()
        assert asr.is_cancelled() is True

        # 显式复位（测试/运维用）
        asr.clear_cancel()
        assert asr.is_cancelled() is False


class TestASRSegmentFilterUsesChars:
    """片段放行判据必须建立在**始终可得**的字段上。

    背景（真实踩过的坑）：`word_timestamps=False` 时 `segment.words` 为空，
    若判据仍用 `word_count`，那么三个放行分支全不成立 → **每一段都被丢弃**
    → 表现就是"她听不见我说话"，而日志看起来一切正常。
    """

    def _run(self, tmp_path, segments):
        from plugins.asr.faster_whisper import plugin as mod

        class FakeModel:
            def transcribe(self, path, **kw):
                return iter(segments), None

        asr = mod.FasterWhisperASR(model_size="small", device="cpu")
        asr.model = FakeModel()
        asr._available = True
        asr._load_attempted = True
        asr._enhance_audio = lambda p: None
        wav = tmp_path / "a.wav"
        wav.write_bytes(b"\x00\x00" * 100)
        return asr.transcribe(str(wav))

    def _seg(self, text, ns=0.1, lp=-0.5, words=None):
        class S:
            pass
        s = S()
        s.text = text
        s.no_speech_prob = ns
        s.avg_logprob = lp
        s.words = words          # 默认 None：模拟 word_timestamps=False
        return s

    def test_long_chinese_segment_accepted_without_words(self, tmp_path):
        """★ 核心回归：无 words 字段时，正常长度中文也必须被放行"""
        out = self._run(tmp_path, [self._seg("帮我打开桌面的文件")])
        assert out == "帮我打开桌面的文件", \
            f"无 words 字段导致整段被丢弃（= 听不见），实际={out!r}"

    def test_short_reply_accepted_when_clear(self, tmp_path):
        """1~2 字应答（好/嗯）在清晰时也要放行"""
        out = self._run(tmp_path, [self._seg("好的", ns=0.3, lp=-0.9)])
        assert out == "好的", f"短应答被误杀，实际={out!r}"

    def test_noisy_segment_still_rejected(self, tmp_path):
        """反方向：高 no_speech + 低 logprob 的噪音仍应被丢弃"""
        out = self._run(tmp_path, [self._seg("嗯", ns=0.95, lp=-1.9)])
        assert out is None, f"噪音片段没被拦住，实际={out!r}"

    def test_empty_segment_rejected(self, tmp_path):
        """空文本必须丢弃（不能拼出一个空串当识别结果）"""
        out = self._run(tmp_path, [self._seg("   ", ns=0.1, lp=-0.3)])
        assert out is None, f"空片段没被拦住，实际={out!r}"


class TestEdgeTTS:
    """Edge TTS测试类"""
    
    def test_import_edge_tts_plugin(self):
        """测试导入Edge TTS插件"""
        from plugins.tts.edge_tts.plugin import EdgeTTS
        
        assert EdgeTTS is not None
    
    def test_edge_tts_initialization(self):
        """测试Edge TTS初始化"""
        from plugins.tts.edge_tts.plugin import EdgeTTS
        
        tts = EdgeTTS()
        assert tts.voice is not None
    
    def test_edge_tts_set_voice(self):
        """测试设置语音"""
        from plugins.tts.edge_tts.plugin import EdgeTTS
        
        tts = EdgeTTS()
        result = tts.set_voice("zh-CN-YunxiNeural")
        assert result is True
        assert tts.voice == "zh-CN-YunxiNeural"


class TestWatchfilesMonitor:
    """Watchfiles文件监控测试类"""
    
    def test_import_watchfiles_plugin(self):
        """测试导入Watchfiles插件"""
        from plugins.file_monitor.watchfiles.plugin import WatchfilesMonitor
        
        assert WatchfilesMonitor is not None
    
    def test_watchfiles_initialization(self):
        """测试Watchfiles初始化"""
        from plugins.file_monitor.watchfiles.plugin import WatchfilesMonitor
        
        monitor = WatchfilesMonitor()
        assert monitor._running is False
        assert monitor._watch_dirs == []
    
    def test_watchfiles_watched_dirs(self):
        """测试监控目录管理"""
        from plugins.file_monitor.watchfiles.plugin import WatchfilesMonitor
        
        monitor = WatchfilesMonitor()
        
        # 添加监控目录
        monitor._watch_dirs.append("/test/dir")
        assert "/test/dir" in monitor.get_watched_dirs()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

class TestPetName:
    def test_set_pet_name_injects_persona(self):
        """set_pet_name 更新人设：名字注入 system_prompt"""
        from plugins.llm.ollama.plugin import OllamaLLM
        llm = OllamaLLM.__new__(OllamaLLM)
        llm.system_prompt = None
        llm.set_pet_name("欣雅")
        assert "欣雅" in llm.system_prompt

    def test_set_pet_name_empty_fallback(self):
        """空名字回退默认欣雅"""
        from plugins.llm.ollama.plugin import OllamaLLM
        llm = OllamaLLM.__new__(OllamaLLM)
        llm.set_pet_name("")
        assert "欣雅" in llm.system_prompt
