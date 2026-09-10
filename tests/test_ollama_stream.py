"""
Ollama LLM流式聊天测试（mock HTTP响应，不依赖真实服务）
"""

import sys
import json
import pytest
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from plugins.llm.ollama.plugin import OllamaLLM


class FakeResponse:
    """模拟 requests.Response（流式）"""

    def __init__(self, status_code=200, lines=None):
        self.status_code = status_code
        self._lines = lines or []

    def iter_lines(self, decode_unicode=True):
        for line in self._lines:
            yield line


class TestChatStream:
    def _make_llm(self, monkeypatch, response):
        llm = OllamaLLM.__new__(OllamaLLM)  # 绕过 __init__（不连真实服务）
        llm.base_url = "http://test:11434"
        llm.model = "qwen2.5:1.5b"
        llm.system_prompt = "测试"
        llm._available = True

        def fake_post(*args, **kwargs):
            assert kwargs.get("stream") is True, "必须使用流式请求"
            return response

        monkeypatch.setattr("plugins.llm.ollama.plugin.requests.post", fake_post)
        return llm

    def test_chat_stream_returns_sentences(self, monkeypatch):
        """流式响应按句切分返回"""
        lines = [
            json.dumps({"message": {"content": "你好呀"}}),
            json.dumps({"message": {"content": "！今天要"}}),
            json.dumps({"message": {"content": "一起玩吗？"}}),
            json.dumps({"message": {"content": "喵~"}}),
            json.dumps({"done": True}),
        ]
        llm = self._make_llm(monkeypatch, FakeResponse(lines=lines))
        result = llm.chat_stream("你好", context=[])
        assert result == ["你好呀！", "今天要一起玩吗？", "喵~"]

    def test_chat_stream_http_error(self, monkeypatch):
        """HTTP 404 时返回 None"""
        llm = self._make_llm(monkeypatch, FakeResponse(status_code=404))
        assert llm.chat_stream("你好") is None

    def test_chat_stream_error_chunk(self, monkeypatch):
        """流中出现 error chunk 返回 None"""
        lines = [
            json.dumps({"message": {"content": "部分内容"}}),
            json.dumps({"error": "model not found"}),
        ]
        llm = self._make_llm(monkeypatch, FakeResponse(lines=lines))
        assert llm.chat_stream("你好") is None

    def test_chat_stream_request_exception(self, monkeypatch):
        """网络异常时返回 None"""
        llm = OllamaLLM.__new__(OllamaLLM)
        llm.base_url = "http://test:11434"
        llm.model = "qwen2.5:1.5b"
        llm.system_prompt = ""
        llm._available = True

        def raise_exc(*args, **kwargs):
            raise ConnectionError("network down")

        monkeypatch.setattr("plugins.llm.ollama.plugin.requests.post", raise_exc)
        assert llm.chat_stream("你好") is None
