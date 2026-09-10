"""
语音流水线测试：按句合成 + 顺序播放
"""

import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import pytest
from PySide6.QtWidgets import QApplication

from ui.pet_window import PetWindow


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


class FakeApp:
    """假的 XiaoyiApp：记录调用"""

    def __init__(self):
        self.calls = []
        self.transcribe_result = "你好呀"
        self.chat_stream_result = ["第一句。", "第二句！", "第三句！"]
        self.synthesized = {}
        for i, s in enumerate(["第一句。", "第二句！", "第三句！"]):
            self.synthesized[s] = b"AUDIO-%d" % i
        self.config_manager = None

    def transcribe(self, wav_path):
        self.calls.append(("transcribe", wav_path))
        return self.transcribe_result

    def chat_stream(self, prompt, context=None):
        self.calls.append(("chat_stream", prompt))
        return self.chat_stream_result

    def chat(self, prompt, context=None):
        self.calls.append(("chat", prompt))
        return "".join(self.chat_stream_result)

    def synthesize(self, text):
        self.calls.append(("synthesize", text))
        return self.synthesized.get(text)

    def play_audio(self, data):
        self.calls.append(("play_audio", data))
        return True


class TestSpeakSentences:
    def test_orders_playback(self, qapp):
        """按句子顺序合成并播放"""
        win = PetWindow()
        app = FakeApp()
        win.app = app
        win._speak_sentences(["第一句。", "第二句！", "第三句！"])
        plays = [c for c in app.calls if c[0] == "play_audio"]
        assert [p[1] for p in plays] == [b"AUDIO-0", b"AUDIO-1", b"AUDIO-2"]
        win.close()

    def test_skips_failed_synth(self, qapp):
        """合成失败的句子跳过，不中断顺序"""
        win = PetWindow()
        app = FakeApp()
        app.synthesized["第二句！"] = None
        win.app = app
        win._speak_sentences(["第一句。", "第二句！", "第三句！"])
        plays = [c for c in app.calls if c[0] == "play_audio"]
        assert [p[1] for p in plays] == [b"AUDIO-0", b"AUDIO-2"]
        win.close()


class TestVoicePipeline:
    def test_pipeline_uses_chat_stream(self, qapp, monkeypatch):
        """流水线：transcribe → chat_stream → 按句合成播放 → 历史"""
        win = PetWindow()
        app = FakeApp()
        win.app = app
        monkeypatch.setattr(win, "set_state", lambda s: None)
        monkeypatch.setattr(win, "show_bubble", lambda *a, **k: None)
        win._voice_pipeline("dummy.wav")

        assert ("chat_stream", "你好呀") in app.calls
        assert any(c[0] == "synthesize" for c in app.calls), "应逐句合成"
        assert any(c[0] == "play_audio" for c in app.calls), "应播放"
        assert win._chat_history[-1]["role"] == "assistant"
        assert win._processing is False
        win.close()

    def test_pipeline_invalid_transcript(self, qapp, monkeypatch):
        """识别为空时走无效处理，不调chat_stream"""
        win = PetWindow()
        app = FakeApp()
        app.transcribe_result = None
        win.app = app
        monkeypatch.setattr(win, "set_state", lambda s: None)
        monkeypatch.setattr(win, "show_bubble", lambda *a, **k: None)
        win._voice_pipeline("dummy.wav")

        assert ("chat_stream", "你好呀") not in app.calls
        assert win._recent_invalid >= 1
        win.close()

    def test_pipeline_no_response(self, qapp, monkeypatch):
        """LLM无回复时走无效处理"""
        win = PetWindow()
        app = FakeApp()
        app.chat_stream_result = None
        win.app = app
        monkeypatch.setattr(win, "set_state", lambda s: None)
        monkeypatch.setattr(win, "show_bubble", lambda *a, **k: None)
        win._voice_pipeline("dummy.wav")

        assert not any(c[0] == "play_audio" for c in app.calls)
        assert win._recent_invalid >= 1
        win.close()
