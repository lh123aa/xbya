"""
XiaoyiApp语音链路接口测试：chat_stream/synthesize/play_audio
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import pytest
from core.app import XiaoyiApp


class TestAppVoiceInterface:
    def _make_app(self):
        app = XiaoyiApp.__new__(XiaoyiApp)  # 不跑完整初始化
        app.plugins = {}
        return app

    def test_chat_stream_uses_llm(self):
        """chat_stream 转发到 LLM 插件的 chat_stream"""
        app = self._make_app()
        llm = MagicMock()
        llm.is_available.return_value = True
        llm.chat_stream.return_value = ["你好。", "再见！"]
        app.plugins["LLMEngine"] = llm

        result = app.chat_stream("你好", context=[])
        assert result == ["你好。", "再见！"]
        llm.chat_stream.assert_called_once_with("你好", [])

    def test_chat_stream_fallback(self):
        """LLM 无 chat_stream 时回退到 chat + 分句"""
        app = self._make_app()
        llm = MagicMock()
        llm.is_available.return_value = True
        del llm.chat_stream  # 模拟旧插件无此方法
        llm.chat.return_value = "今天不错。真的！"
        app.plugins["LLMEngine"] = llm

        result = app.chat_stream("你好", context=[])
        assert result == ["今天不错。", "真的！"]

    def test_synthesize_returns_bytes(self):
        """synthesize 调用 TTS 合成返回音频字节"""
        app = self._make_app()
        tts = MagicMock()
        tts.is_available.return_value = True
        tts.speak.return_value = b"MP3DATA"
        app.plugins["TTSEngine"] = tts

        assert app.synthesize("你好") == b"MP3DATA"

    def test_synthesize_unavailable(self):
        """TTS 不可用时返回 None"""
        app = self._make_app()
        tts = MagicMock()
        tts.is_available.return_value = False
        app.plugins["TTSEngine"] = tts

        assert app.synthesize("你好") is None

    def test_play_audio_success(self):
        """play_audio 调用 _play_audio"""
        app = self._make_app()
        app._play_audio = MagicMock(return_value=True)
        assert app.play_audio(b"DATA") is True
