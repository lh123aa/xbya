# -*- coding: utf-8 -*-
"""listen_standby 状态机测试（用模拟流，不依赖真实麦克风）"""
import os
import time
import numpy as np
import pytest
from services.microphone_service import MicrophoneService


class FakeStream:
    """模拟pyaudio流：按帧序列播放预置chunk"""
    def __init__(self, chunks, chunk_size=1024):
        self._chunks = list(chunks)
        self._idx = 0
        self.closed = False

    def read(self, n, exception_on_overflow=False):
        if self._idx >= len(self._chunks):
            return b"\x00\x00" * n
        c = self._chunks[self._idx]
        self._idx += 1
        return c

    def stop_stream(self):
        pass

    def close(self):
        self.closed = True


def _bytes_chunk(vol, chunk=1024):
    """生成指定音量的int16 chunk bytes"""
    data = np.full(chunk, vol, dtype=np.int16)
    return data.tobytes()


def test_listen_standby_detects_speech(monkeypatch):
    """静音→说话→静音→回调正确生成wav并存档音频"""
    mic = MicrophoneService()
    pos = {}
    real_reader = None

    class FakePyAudio:
        def __init__(self, *a, **k): pass
        def open(self, **kw):
            chunks = [_bytes_chunk(0) for _ in range(2)]
            chunks += [_bytes_chunk(3000) for _ in range(8)]
            chunks += [_bytes_chunk(0) for _ in range(70)]  # >1s静音
            return FakeStream(chunks)
        def terminate(self): pass
        def get_default_input_device_info(self):
            return {"defaultSampleRate": 16000}

    monkeypatch.setattr("pyaudio.PyAudio", FakePyAudio)
    mic.pyaudio_available = True

    result = []
    on_start_called = []
    ok = mic.listen_standby(
        callback=lambda p: result.append(p),
        on_start=lambda: on_start_called.append(True),
        speech_volume=300.0,
        max_seconds=10.0,
    )
    time.sleep(0.8)
    mic.stop_listen_standby()
    time.sleep(0.3)

    assert ok is True, "监听应启动成功"
    assert on_start_called, "应检测到语音开始"
    assert len(result) == 1, "应回调一次wav"
    assert os.path.exists(result[0]) and result[0].endswith(".wav")


def test_listen_standby_silence_no_callback(monkeypatch):
    """纯静音不触发回调"""
    mic = MicrophoneService()

    class FakePyAudio2:
        def __init__(self, *a, **k): pass
        def open(self, **kw):
            return FakeStream([_bytes_chunk(0) for _ in range(20)])
        def terminate(self): pass
        def get_default_input_device_info(self):
            return {"defaultSampleRate": 16000}

    monkeypatch.setattr("pyaudio.PyAudio", FakePyAudio2)
    mic.pyaudio_available = True

    result = []
    ok = mic.listen_standby(callback=lambda p: result.append(p), speech_volume=300.0)
    time.sleep(0.5)
    mic.stop_listen_standby()
    time.sleep(0.3)
    assert ok is True
    assert result == [], "静音不应回调"


def test_listen_standby_max_seconds_force_end(monkeypatch):
    """连续大声流：超过max_seconds强制结束→恰好回调一次→循环回到standby继续监听"""
    mic = MicrophoneService()

    class FakePyAudio4:
        def __init__(self, *a, **k): pass
        def open(self, **kw):
            return FakeStream([_bytes_chunk(3000) for _ in range(30)])
        def terminate(self): pass
        def get_default_input_device_info(self):
            return {"defaultSampleRate": 16000}

    monkeypatch.setattr("pyaudio.PyAudio", FakePyAudio4)
    mic.pyaudio_available = True

    result = []
    ok = mic.listen_standby(
        callback=lambda p: result.append(p),
        speech_volume=300.0,
        max_seconds=1.0,
    )
    time.sleep(0.8)

    assert ok is True, "监听应启动成功"
    assert len(result) == 1, "连续噪音超max_seconds应恰好回调一次(wav)"
    assert os.path.exists(result[0]) and result[0].endswith(".wav")
    assert mic.is_listening() is True, "max_seconds强制结束后应回到standby继续监听"

    mic.stop_listen_standby()
    time.sleep(0.3)
    assert mic.is_listening() is False


def test_listen_standby_open_failure_resets_flag(monkeypatch):
    """p.open 打开失败（设备忙碌/无默认输入）时线程退出，_listening 必须复位"""
    mic = MicrophoneService()

    class OpenFailsPyAudio:
        def __init__(self, *a, **k): pass
        def open(self, **kw):
            raise OSError("device busy")
        def terminate(self): pass
        def get_default_input_device_info(self):
            return {"defaultSampleRate": 16000}

    monkeypatch.setattr("pyaudio.PyAudio", OpenFailsPyAudio)
    mic.pyaudio_available = True

    ok = mic.listen_standby(callback=lambda p: None, speech_volume=300.0)
    assert ok is True, "线程应已启动"
    time.sleep(0.3)
    assert mic.is_listening() is False, "打开失败后 is_listening 应复位为False"


def test_listen_standby_ctor_failure_resets_flag(monkeypatch):
    """PyAudio() 构造失败时线程退出，_listening 必须复位"""
    mic = MicrophoneService()

    class CtorFailsPyAudio:
        def __init__(self, *a, **k):
            raise OSError("no sound device")
        def open(self, **kw):
            raise OSError("no sound device")
        def terminate(self): pass
        def get_default_input_device_info(self):
            return {"defaultSampleRate": 16000}

    monkeypatch.setattr("pyaudio.PyAudio", CtorFailsPyAudio)
    mic.pyaudio_available = True

    ok = mic.listen_standby(callback=lambda p: None, speech_volume=300.0)
    assert ok is True, "线程应已启动"
    time.sleep(0.3)
    assert mic.is_listening() is False, "构造失败后 is_listening 应复位为False"


def test_stop_listen_stops_loop(monkeypatch):
    """stop_listen_standby 后线程应停止"""
    mic = MicrophoneService()

    class FakePyAudio3:
        def __init__(self, *a, **k): pass
        def open(self, **kw):
            return FakeStream([_bytes_chunk(0) for _ in range(1000)])
        def terminate(self): pass
        def get_default_input_device_info(self):
            return {"defaultSampleRate": 16000}

    monkeypatch.setattr("pyaudio.PyAudio", FakePyAudio3)
    mic.pyaudio_available = True

    ok = mic.listen_standby(callback=lambda p: None, speech_volume=300.0)
    assert ok is True
    assert mic.is_listening() is True
    mic.stop_listen_standby()
    time.sleep(0.3)
    assert mic.is_listening() is False
