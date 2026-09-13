"""
Groq Whisper 云端 ASR 插件
使用 Groq API 调用 whisper-large-v3-turbo 模型
优势：比本地 base 模型快 3 倍、准 2 倍

音频预处理（关键）：
云端的 large-v3-turbo 虽然比本地 base 强得多，但对**音量过低**的音频同样会退化
（典型症状：把低音量语音识别成"谢谢大家""嗯"这类高频短句）。
因此这里复用与 faster_whisper 相同的增强链：重采样 16k 单声道 → 噪声门 → 音量归一化。
"""

import os
import time
import logging
import wave
import tempfile
from typing import Optional, List

from interfaces.asr import ASREngine

logger = logging.getLogger(__name__)

TARGET_RATE = 16000
TARGET_VOL = 800.0
MAX_GAIN = 30.0


class GroqWhisperASR(ASREngine):
    """Groq Whisper 云端 ASR 实现"""

    def __init__(self, api_key: str = "", model: str = "whisper-large-v3-turbo",
                 language: str = "zh", base_url: str = "https://api.groq.com/openai/v1",
                 initial_prompt: str = "", enhance: bool = True, **_extra):
        self.api_key = api_key
        self.model = model
        self.language = language
        self.base_url = base_url
        #: 中文识别提示词：给 Whisper 提供领域锚点。
        #: 实测能显著减少"谢谢大家/字幕by"这类通用短句幻觉 ——
        #: 模型在低信噪比时会退化成"最可能的通用短语"，而提示词把先验往对话上拉。
        self.initial_prompt = initial_prompt or (
            "以下是用户对桌面助手说的话，简体中文口语对话。"
        )
        self.enhance = bool(enhance)
        self._available = bool(api_key)
        if self._available:
            logger.info(f"Groq Whisper 初始化成功: model={model}, language={language}, enhance={enhance}")
        else:
            logger.warning("Groq Whisper: 未配置 api_key，不可用")

    # ══════════════════════════════════════════════
    #  音频增强
    # ══════════════════════════════════════════════

    def _enhance_audio(self, audio_path: str) -> Optional[str]:
        """重采样 16k 单声道 + 噪声门 + 音量归一化

        Returns:
            增强后的临时文件路径；无需增强或失败时返回 None（调用方用原文件）
        """
        try:
            import numpy as np

            with wave.open(audio_path, 'rb') as wf:
                channels = wf.getnchannels()
                sampwidth = wf.getsampwidth()
                orig_rate = wf.getframerate()
                frames = wf.readframes(wf.getnframes())

            if sampwidth != 2:
                logger.debug("非 16bit 音频，跳过增强")
                return None

            audio_int = np.frombuffer(frames, dtype=np.int16).astype(np.float32)
            if len(audio_int) == 0:
                return None

            if channels > 1:
                audio_int = audio_int.reshape(-1, channels).mean(axis=1).astype(np.float32)
                channels = 1

            # 重采样到 16k
            if orig_rate != TARGET_RATE and orig_rate > 0:
                n_out = int(len(audio_int) * TARGET_RATE / orig_rate)
                if n_out > 1:
                    x_old = np.linspace(0.0, 1.0, num=len(audio_int), endpoint=False)
                    x_new = np.linspace(0.0, 1.0, num=n_out, endpoint=False)
                    audio_int = np.interp(x_new, x_old, audio_int).astype(np.float32)
                    orig_rate = TARGET_RATE

            # 噪声底估计（最安静的 20% 帧）
            frame_len = int(orig_rate * 0.02)
            n_frames = len(audio_int) // frame_len if frame_len > 0 else 0
            if n_frames > 1:
                frames2d = audio_int[:n_frames * frame_len].reshape(n_frames, frame_len)
                frame_rms = np.sqrt(np.mean(frames2d.astype(np.float64) ** 2, axis=1))
                n_noise = max(1, n_frames // 5)
                noise_rms = float(np.sort(frame_rms)[:n_noise].mean())
                # 噪声门：低于噪声底 1.3 倍的帧清零
                if noise_rms > 5.0:
                    gate_threshold = noise_rms * 1.3
                    for i in range(n_frames):
                        if frame_rms[i] < gate_threshold:
                            audio_int[i * frame_len:(i + 1) * frame_len] = 0
                    gated_ratio = float(np.sum(frame_rms < gate_threshold)) / n_frames
                else:
                    gated_ratio = 0.0
            else:
                gated_ratio = 0.0

            current_vol = float(np.abs(audio_int).mean())
            if current_vol < 5.0:
                logger.info(f"Groq Whisper: 录音几乎无声({current_vol:.0f})，放弃识别")
                return None

            # 音量归一化
            if current_vol >= TARGET_VOL:
                gain = 1.0
            else:
                gain = min(TARGET_VOL / max(current_vol, 1.0), MAX_GAIN)

            need_rewrite = (gain > 1.0) or (orig_rate != TARGET_RATE) or (channels != 1)
            if not need_rewrite and gated_ratio == 0:
                return None

            audio_int = np.clip(audio_int * gain, -32768, 32767).astype(np.int16)
            logger.info(
                f"Groq Whisper 音频增强: vol={current_vol:.0f} → {TARGET_VOL:.0f} "
                f"(增益{gain:.1f}x, 噪声门{gated_ratio:.0%}, 重采样{orig_rate}Hz单声道)"
            )

            from core.temp_manager import get_tmp_dir
            fd, enhanced_path = tempfile.mkstemp(suffix='.wav', dir=get_tmp_dir())
            os.close(fd)
            with wave.open(enhanced_path, 'wb') as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(orig_rate)
                wf.writeframes(audio_int.tobytes())
            return enhanced_path

        except Exception as e:
            logger.warning(f"Groq Whisper 音频增强失败(降级用原音频): {e}")
            return None

    # ══════════════════════════════════════════════
    #  识别
    # ══════════════════════════════════════════════

    def transcribe(self, audio_path: str, initial_prompt: Optional[str] = None) -> Optional[str]:
        """将音频文件转为文本（通过 Groq API）"""
        if not self._available:
            return None

        try:
            import requests
        except ImportError:
            logger.error("requests 未安装")
            return None

        t0 = time.monotonic()
        enhanced = None
        send_path = audio_path
        try:
            if self.enhance:
                enhanced = self._enhance_audio(audio_path)
                if enhanced:
                    send_path = enhanced

            url = f"{self.base_url}/audio/transcriptions"
            headers = {"Authorization": f"Bearer {self.api_key}"}
            prompt = initial_prompt or self.initial_prompt

            with open(send_path, "rb") as f:
                files = {"file": (os.path.basename(send_path), f, "audio/wav")}
                data = {
                    "model": self.model,
                    "language": self.language,
                    "response_format": "json",
                    # temperature=0：贪心解码，减少"随机编一句通用短语"的概率
                    "temperature": "0",
                }
                if prompt:
                    data["prompt"] = prompt
                resp = requests.post(url, headers=headers, files=files, data=data, timeout=20)

            if resp.status_code != 200:
                logger.error(f"Groq Whisper API 错误: {resp.status_code} {resp.text[:200]}")
                return None

            result = resp.json()
            text = (result.get("text") or "").strip()
            text = self._clean_text(text)
            elapsed = (time.monotonic() - t0) * 1000

            # 幻觉过滤：云端的 large-v3 同样会在噪音上输出"谢谢大家/字幕by"
            if self._is_hallucination(text):
                logger.info(f"Groq Whisper 判定为幻觉，丢弃: {text[:40]!r}")
                return None

            logger.info(f"Groq Whisper 识别完成: {elapsed:.0f}ms, 文本={text[:60]!r}")
            return text if text else None

        except Exception as e:
            logger.error(f"Groq Whisper 识别失败: {e}")
            return None
        finally:
            if enhanced:
                try:
                    os.unlink(enhanced)
                except OSError:
                    pass

    @staticmethod
    def _clean_text(text: str) -> str:
        """清理 Whisper 常见的尾部杂字

        实测 large-v3-turbo 会在句子末尾多吐一个全角拉丁字母或孤立符号
        （"你好呀Ｂ" / "给我讲个笑话Ｐ。"）—— 这是模型在无语音段落的
        概率性补全，不是用户说的内容，且会污染后续 LLM 的语义理解。
        判据：**尾部**孤立的全角拉丁字母/半角单字母（前后无其他拉丁词）。
        """
        import re
        t = (text or "").strip()
        if not t:
            return t
        # 尾部孤立单字母 + 可选尾标点。
        # "孤立"= 该字母前面**不是**拉丁字母（否则会砍掉 "PDF" 的 F、"Windows" 的 s）。
        t = re.sub(r"(?<![A-Za-zＡ-Ｚａ-ｚ])[Ａ-Ｚａ-ｚA-Za-z][，,。.、！!？?；;]*\s*$", "", t)
        return t.strip()

    #: 已知的"非语音残渣"幻觉模式
    _HALLUCINATION_MARKERS = (        "字幕", "订阅", "点赞", "转发", "打赏", "弹幕",
        "bilibili", "www.", "http", "一键三连", "关注我",
        "欢迎来到", "by ", "by索", "谢谢观看", "谢谢大家",
        "作词", "作曲", "编曲", "混音",
    )

    @classmethod
    def _is_hallucination(cls, text: str) -> bool:
        """判断文本是否是"非语音残渣"型幻觉"""
        t = (text or "").strip()
        if not t:
            return False
        low = t.lower()
        # 极短口语应答不算幻觉（用户真的会说"好""嗯"）
        if len(t) <= 2:
            return False
        for m in cls._HALLUCINATION_MARKERS:
            if m.lower() in low:
                return True
        # 重复文本检测：同一 2~6 字子串连续出现 ≥4 次
        if len(t) >= 10:
            for sub_len in range(2, 7):
                for i in range(len(t) - sub_len + 1):
                    sub = t[i:i + sub_len]
                    count, pos = 0, i
                    while pos + sub_len <= len(t) and t[pos:pos + sub_len] == sub:
                        count += 1
                        pos += sub_len
                    if count >= 4:
                        logger.debug("[Groq Whisper] 重复文本检测: %r ×%d → 幻觉", sub, count)
                        return True
        return False

    def transcribe_stream(self, audio_stream) -> Optional[str]:
        """实时流识别（暂不支持）"""
        return None

    def get_supported_formats(self) -> List[str]:
        return ["wav", "mp3", "flac", "ogg", "m4a", "wma", "aac"]

    def is_available(self) -> bool:
        return self._available

    def request_cancel(self):
        """取消当前识别（云端无法取消，置空操作）"""
        pass


def register():
    """注册插件"""
    return {
        "name": "groq_whisper",
        "version": "1.0.0",
        "interface": "ASREngine",
        "class": "GroqWhisperASR",
        "dependencies": ["requests"]
    }
