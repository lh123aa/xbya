"""
Groq Whisper 云端 ASR 插件
使用 Groq API 调用 whisper-large-v3-turbo 模型
优势：比本地 base 模型快 3 倍、准 2 倍
"""

import os
import time
import logging
from typing import Optional, List

from interfaces.asr import ASREngine

logger = logging.getLogger(__name__)


class GroqWhisperASR(ASREngine):
    """Groq Whisper 云端 ASR 实现"""

    def __init__(self, api_key: str = "", model: str = "whisper-large-v3-turbo",
                 language: str = "zh", base_url: str = "https://api.groq.com/openai/v1",
                 **_extra):
        self.api_key = api_key
        self.model = model
        self.language = language
        self.base_url = base_url
        self._available = bool(api_key)
        if self._available:
            logger.info(f"Groq Whisper 初始化成功: model={model}, language={language}")
        else:
            logger.warning("Groq Whisper: 未配置 api_key，不可用")

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
        try:
            url = f"{self.base_url}/audio/transcriptions"
            headers = {"Authorization": f"Bearer {self.api_key}"}

            with open(audio_path, "rb") as f:
                files = {"file": (os.path.basename(audio_path), f, "audio/wav")}
                data = {"model": self.model, "language": self.language}
                if initial_prompt:
                    data["prompt"] = initial_prompt

                resp = requests.post(url, headers=headers, files=files, data=data, timeout=15)

            if resp.status_code != 200:
                logger.error(f"Groq Whisper API 错误: {resp.status_code} {resp.text[:200]}")
                return None

            result = resp.json()
            text = (result.get("text") or "").strip()
            elapsed = (time.monotonic() - t0) * 1000
            logger.info(f"Groq Whisper 识别完成: {elapsed:.0f}ms, 文本={text[:60]!r}")
            return text if text else None

        except Exception as e:
            logger.error(f"Groq Whisper 识别失败: {e}")
            return None

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
