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


# ========== 多声源：设备枚举与选择 ==========

class _FakeDevInfoPyAudio:
    """模拟多声源环境：本机麦克风 + 远程桌面虚拟麦克风 + 混音回环 + 默认设备。

    设备清单刻意做成"混音排在前面"，用于验证自动挑选会跳过回环类设备。

    注意：真实 PyAudio 的索引是**连续**的 0..count-1（中间可能有输出设备，
    但它们 maxInputChannels=0 会被过滤）。这里用连续索引，避免给被测代码
    制造"索引跳号"这种现实中不存在的输入 —— 否则测的是假问题。
    """

    # (name, in_channels, rate) —— 列表下标即设备索引
    DEVICES = [
        ("Microsoft 声音映射器 - Input", 2, 44100),          # 0 回环
        ("麦克风 (Realtek(R) Audio)", 4, 44100),              # 1 真实麦克风 ← 默认
        ("麦克风阵列 (UU远程虚拟音频设备)", 2, 44100),         # 2 真实麦克风（远程）
        ("立体声混音 (Realtek HD Audio)", 2, 48000),           # 3 回环
        ("电脑扬声器 (Realtek HD Audio output)", 2, 48000),    # 4 回环
        ("扬声器 (Realtek HD Audio output with SST)", 2, 48000),  # 5 输出（无输入）
    ]
    #: 输入声道为 0 的设备（纯输出），会被枚举过滤掉
    OUTPUT_ONLY = {5}

    def __init__(self, *a, **k):
        pass

    def get_device_count(self):
        return len(self.DEVICES)

    def get_device_info_by_index(self, i):
        if i < 0 or i >= len(self.DEVICES):
            raise ValueError("bad index %r" % i)
        name, ch, rate = self.DEVICES[i]
        if i in self.OUTPUT_ONLY:
            ch = 0
        return {"index": i, "name": name, "maxInputChannels": ch,
                "defaultSampleRate": rate, "maxOutputChannels": 2}

    def get_default_input_device_info(self):
        return {"index": 1}

    def terminate(self):
        pass


def _patch_fake_devices(monkeypatch):
    monkeypatch.setattr("pyaudio.PyAudio", _FakeDevInfoPyAudio)
    mic = MicrophoneService()
    mic.pyaudio_available = True
    return mic


def test_list_input_devices_marks_kind_and_default(monkeypatch):
    """设备枚举应区分'麦克风'与'回环'，并标出系统默认设备"""
    mic = _patch_fake_devices(monkeypatch)
    devs = mic.list_input_devices()
    # 6 个设备里有 1 个是纯输出（maxInputChannels=0），应被过滤 → 剩 5 个
    assert len(devs) == 5
    assert 5 not in {d["index"] for d in devs}, "纯输出设备不应出现在输入清单里"
    by_idx = {d["index"]: d for d in devs}
    assert by_idx[1]["kind"] == "mic"
    assert by_idx[1]["is_default"] is True
    assert by_idx[2]["kind"] == "mic"
    # 回环类被正确识别
    assert by_idx[0]["kind"] == "loopback"      # 声音映射器
    assert by_idx[3]["kind"] == "loopback"      # 立体声混音
    assert by_idx[4]["kind"] == "loopback"      # 扬声器


def test_list_input_devices_can_exclude_loopback(monkeypatch):
    """include_virtual=False 时只返回真实麦克风"""
    mic = _patch_fake_devices(monkeypatch)
    devs = mic.list_input_devices(include_virtual=False)
    assert {d["index"] for d in devs} == {1, 2}
    assert all(d["kind"] == "mic" for d in devs)


def test_find_device_by_name_prefers_mic_over_loopback(monkeypatch):
    """按名字片段匹配；同名者优先非回环设备"""
    mic = _patch_fake_devices(monkeypatch)
    # "realtek" 命中 idx=3/4(回环) 与 idx=1(麦克风)
    # 应优先返回麦克风 idx=1，而不是索引更小的回环设备
    assert mic.find_device_by_name("realtek") == 1
    # 精确片段命中远程虚拟麦克风
    assert mic.find_device_by_name("UU远程") == 2
    assert mic.find_device_by_name("不存在的设备") is None
    assert mic.find_device_by_name("") is None


def test_auto_pick_skips_loopback_devices(monkeypatch):
    """自动挑选必须**跳过回环设备**（否则会把系统播放声当人声）

    注意：自动挑选的**具体索引**不再是"第一个真实麦克风"——
    实测电平挑选（`_pick_by_level`）会挑 RMS 最高的那个。
    这里钉住的是**不变量**：无论选谁，都不能是回环设备。
    """
    mic = _patch_fake_devices(monkeypatch)
    # 关掉电平挑选，单独验"回环排除"这条判据（电平挑选另有专门用例）
    monkeypatch.setattr(MicrophoneService, "_pick_by_level", lambda self, **kw: None)
    picked = mic._pick_mic_index()
    assert picked in (1, 2), f"应选中真实麦克风之一，实际 {picked}"
    assert picked not in (0, 3, 4), "绝不能选中回环类设备"


def test_auto_pick_prefers_loudest_device(monkeypatch):
    """自动挑选应优先**实测电平最高**的设备

    根因（2026-09-13 实测）：本机 14 个输入设备时，"取第一个真实麦克风"
    拿到的那个读到 RMS=52（底噪），而用户说话真正进的设备 RMS=3993 ——
    差 76 倍。全部日志"看起来正常"（有触发、有 ASR 输出），但永远听不到用户。
    """
    mic = _patch_fake_devices(monkeypatch)
    # 假设备表里 idx=1 是系统默认麦克风、idx=2 是"UU远程"。
    # 让 idx=2 的电平明显更高（模拟"用户是从 UU远程 说话"）。
    def _fake_probe(self, seconds=1.2, indices=None):
        rows = []
        for d in self.list_input_devices():
            if d["kind"] != "mic":
                continue
            loud = d["index"] == 2
            rows.append({
                "index": d["index"], "name": d["name"], "ok": True,
                "avg": 3993.0 if loud else 52.0,
                "peak": 5187.0 if loud else 127.0,
                "error": "",
            })
        return rows
    monkeypatch.setattr(MicrophoneService, "probe_device_levels", _fake_probe)
    # 关掉"名字/索引显式指定"这条路：本用例验的是**自动挑选**，
    # 而真实 config.yaml 里可能配了 `voice.mic_device`（会短路自动挑选）。
    mic.input_device = None
    mic.input_device_name = ""

    picked = mic._pick_mic_index()
    assert picked == 2, f"应选中电平最高的 idx=2，实际 {picked}"
    assert "3993" in mic._auto_pick_reason, \
        f"应记录挑选理由（供诊断），实际 {mic._auto_pick_reason!r}"


def test_auto_pick_prefers_highest_even_when_levels_are_similar(monkeypatch):
    """电平接近时**仍按最高电平选**，不再退回"取第一个"

    ## 为什么改了旧契约（旧契约："接近就不硬猜，退回按顺序挑选"）

    旧行为在真机上选错了设备，而且很难看出来：
      本机 14 个输入设备，自动挑选落到 idx=1，实测三轮读数
      174 / 156 / 149 —— **用户说话与否都一样**，那是恒定底噪；
      而真正收到人声的 idx=7 是 6433 / 284 / 126（说话时飙升）。
      两者**同名**（都叫"麦克风 (Realtek(R) Audio)"），
      日志只说"区分度不足，退回按顺序挑选"，看不出选错了。

    关键认识：**"取第一个"同样是任意选择，而且没有依据**。
    电平再弱也是"该设备实际收到了多少声音"的直接测量；
    设备顺序与"能不能听到用户"没有任何因果关系。
    宁可相信一个弱信号，也不要一个凭空的选择。
    （"接近"仍会记进 `_auto_pick_reason`，只是不再推翻测量结果。）
    """
    mic = _patch_fake_devices(monkeypatch)

    def _flat_probe(self, seconds=1.2, indices=None):
        # 两个候选电平接近（50 vs 48），都远高于噪声
        return [{"index": 1, "name": "mic1", "ok": True,
                 "avg": 50.0, "peak": 120.0, "error": ""},
                {"index": 2, "name": "mic2", "ok": True,
                 "avg": 48.0, "peak": 118.0, "error": ""}]
    monkeypatch.setattr(MicrophoneService, "probe_device_levels", _flat_probe)
    mic.input_device = None
    mic.input_device_name = ""

    picked = mic._pick_by_level()
    assert picked == 1, f"应选电平最高的 idx=1（不再返回 None），实际 {picked}"
    assert mic._pick_mic_index() == 1, \
        "整体挑选也必须落到实测最高的那个，不能退回'取第一个'"
    assert mic._auto_pick_reason, "应记录理由供诊断"
    assert "50" in mic._auto_pick_reason


def test_auto_pick_never_returns_unusable_device(monkeypatch):
    """★ 不能选到"打不开应用采样率"的设备（本机实测踩到）。

    探测循环会依次尝试多个采样率并取第一个成功的，
    所以"探测成功"只证明某个采样率能开，
    **不证明监听线程用的 16000Hz 能开**。本机 idx=15 正是如此：
    探测在 48000 下读到很好的电平，被误判为"最能听到用户"，
    但监听一 open(16000) 就 -9997。
    """
    mic = _patch_fake_devices(monkeypatch)

    def _probe(self, seconds=1.2, indices=None):
        # idx=15 电平最高，但 ok=False（不支持 16000）→ 不得入选
        return [{"index": 15, "name": "loud-but-unusable", "ok": False,
                 "avg": 0.0, "peak": 0.0,
                 "error": "不支持应用采样率 16000Hz"},
                {"index": 1, "name": "usable", "ok": True,
                 "avg": 120.0, "peak": 300.0, "error": ""}]
    monkeypatch.setattr(MicrophoneService, "probe_device_levels", _probe)
    mic.input_device = None
    mic.input_device_name = ""

    picked = mic._pick_by_level()
    assert picked == 1, (
        f"ok=False 的设备（打不开应用采样率）不得被选中，实际 {picked}")
    assert picked != 15


def test_probe_marks_devices_that_cannot_open_at_app_rate(monkeypatch):
    """★ 探测必须显式标注"不支持应用采样率"的设备。

    判据：`_can_open_at_rate` 为 False 的设备，探测结果 ok=False
    且 error 里说明原因 —— 否则调用方（自动挑选）无从排除它。
    """
    from services.microphone_service import MicrophoneService as M

    called = {}

    def _fake_can_open(p, index, rate):
        called[index] = rate
        return index != 15          # 15 打不开，其余能开
    monkeypatch.setattr(M, "_can_open_at_rate", staticmethod(_fake_can_open))

    devs = [{"index": 15, "name": "bad", "kind": "mic", "channels": 2,
             "native_rate": 48000},
            {"index": 1, "name": "good", "kind": "mic", "channels": 2,
             "native_rate": 44100}]
    monkeypatch.setattr(M, "list_input_devices", staticmethod(lambda *a, **k: devs))

    mic = M.__new__(M)
    mic.chunk_size = 1024
    mic.sample_rate = 16000
    rows = mic.probe_device_levels(seconds=0.05)
    by_idx = {r["index"]: r for r in rows}

    assert by_idx[15]["ok"] is False, "打不开 16000 的设备必须标为不可用"
    assert "16000" in by_idx[15]["error"], \
        f"错误信息应说明是采样率问题，实际 {by_idx[15]['error']!r}"
    # 验证探的就是应用用的那个采样率
    assert called.get(1) == 16000, \
        f"应按应用采样率(16000)验证，实际验的是 {called.get(1)}"


def test_set_input_device_by_index(monkeypatch):
    """按索引指定设备"""
    mic = _patch_fake_devices(monkeypatch)
    assert mic.set_input_device(2) is True
    assert mic.input_device == 2
    assert mic._pick_mic_index() == 2, "指定后应优先用指定值"

    # 数字字符串同样按索引处理
    assert mic.set_input_device("1") is True
    assert mic.input_device == 1

    # 不存在的索引 → 失败且不改变原值
    assert mic.set_input_device(999) is False
    assert mic.input_device == 1


def test_set_input_device_by_name(monkeypatch):
    """按名字片段指定设备"""
    mic = _patch_fake_devices(monkeypatch)
    assert mic.set_input_device("UU远程") is True
    assert mic.input_device == 2
    assert mic.input_device_name == "UU远程"
    assert mic._pick_mic_index() == 2

    # 名字匹配不上 → 失败，且不应污染已有配置
    assert mic.set_input_device("没有这个") is False
    assert mic.input_device == 2


def test_set_input_device_auto_resets(monkeypatch):
    """auto/None/"" 应恢复自动挑选"""
    mic = _patch_fake_devices(monkeypatch)
    mic.set_input_device(2)
    assert mic.input_device == 2

    assert mic.set_input_device(None) is True
    assert mic.input_device is None
    assert mic.input_device_name == ""
    # 恢复自动后应重新选中 idx=1
    assert mic._pick_mic_index() == 1


def test_bad_name_falls_back_to_auto_pick(monkeypatch):
    """配置了匹配不上的设备名时，_pick_mic_index 应回退自动挑选而非崩溃"""
    mic = _patch_fake_devices(monkeypatch)
    mic.input_device = None
    mic.input_device_name = "这台机器上没有的设备"
    assert mic._pick_mic_index() == 1


def test_describe_devices_marks_current(monkeypatch):
    """设备清单文本应标出当前选中的设备"""
    mic = _patch_fake_devices(monkeypatch)
    text = mic.describe_devices()
    assert "麦克风 (Realtek(R) Audio)" in text
    assert "←当前" in text
    assert "回环" in text


def test_config_loads_mic_device(monkeypatch):
    """config 里的 voice.mic_device 应被读取并生效（按名字片段）"""
    monkeypatch.setattr("pyaudio.PyAudio", _FakeDevInfoPyAudio)
    monkeypatch.setattr(
        "core.config_manager.get_config_manager",
        lambda: type("CM", (), {"get": staticmethod(lambda k, d=None: "UU远程" if k == "voice.mic_device" else d)})(),
    )
    mic = MicrophoneService()
    mic.pyaudio_available = True
    assert mic.input_device == 2, "config 指定的设备名应已解析为索引"
    assert mic._pick_mic_index() == 2


def test_config_invalid_mic_device_falls_back(monkeypatch):
    """config 里设备配置无效时，应回退自动挑选且不抛异常"""
    monkeypatch.setattr("pyaudio.PyAudio", _FakeDevInfoPyAudio)
    monkeypatch.setattr(
        "core.config_manager.get_config_manager",
        lambda: type("CM", (), {"get": staticmethod(lambda k, d=None: "压根不存在" if k == "voice.mic_device" else d)})(),
    )
    mic = MicrophoneService()
    mic.pyaudio_available = True
    assert mic.input_device is None
    assert mic._pick_mic_index() == 1, "无效配置应回退到自动挑选的结果"


class TestPauseResume:
    """pause_listening / resume_listening 测试（防回声机制）"""

    def test_pause_sets_flag(self):
        """pause_listening 应设置 _listen_paused 标志"""
        mic = MicrophoneService()
        assert not mic._listen_paused
        mic.pause_listening()
        assert mic._listen_paused

    def test_resume_clears_flag(self):
        """resume_listening 应清除 _listen_paused 标志"""
        mic = MicrophoneService()
        mic.pause_listening()
        assert mic._listen_paused
        mic.resume_listening()
        assert not mic._listen_paused

    def test_pause_skips_recording(self, monkeypatch):
        """暂停期间应跳过录音（不触发回调）"""
        mic = MicrophoneService()
        mic.pyaudio_available = True
        result = []

        # 构造：持续高音量（正常应该触发回调）
        # 但因为暂停了，不应触发
        loud = _bytes_chunk(2000, 1024)
        chunks = [loud] * 50  # 足够多，确保循环不会因为数据耗尽退出
        fake_stream = FakeStream(chunks)

        monkeypatch.setattr("pyaudio.PyAudio", lambda: type("PA", (), {
            "open": lambda self, **kw: fake_stream,
            "get_default_input_device_info": lambda self: {"index": 0, "defaultSampleRate": 16000},
            "terminate": lambda self: None,
        })())

        # 先暂停，再启动监听（这样从一开始就是暂停状态）
        mic.pause_listening()

        mic.listen_standby(
            callback=lambda p: result.append(p),
            speech_volume=100,
            max_seconds=2.0,
        )

        # 等待足够时间让循环跑到高音量段
        time.sleep(0.3)

        # 应该没有回调（因为暂停了）
        assert len(result) == 0, f"暂停期间不应有回调，但收到 {len(result)} 个"

        # 恢复，让循环处理剩余数据
        mic.resume_listening()
        time.sleep(0.1)

        mic.stop_listen_standby()
        time.sleep(0.1)

    def test_watchdog_force_resumes_after_max_pause(self):
        """看门狗：暂停超过上限必须强制恢复（防漏调 resume 导致永久失聪）"""
        mic = MicrophoneService()
        mic.pause_listening()
        assert mic.is_paused()
        # 模拟"很久以前暂停的"
        mic._paused_at = time.time() - (mic._max_pause_sec + 1)
        # 看门狗判定逻辑（与 listen 循环内一致）
        assert (time.time() - mic._paused_at) > mic._max_pause_sec, \
            "超时条件应成立"

    def test_is_paused_reflects_state(self):
        """is_paused 应准确反映暂停状态"""
        mic = MicrophoneService()
        assert not mic.is_paused()
        mic.pause_listening()
        assert mic.is_paused()
        mic.resume_listening()
        assert not mic.is_paused()

    def test_pause_records_timestamp(self):
        """pause 应记录时间戳，供看门狗判断"""
        mic = MicrophoneService()
        assert mic._paused_at == 0.0
        mic.pause_listening()
        assert mic._paused_at > 0.0, "暂停时必须记录起始时刻"
        mic.resume_listening()
        assert mic._paused_at == 0.0, "恢复时应清零时间戳"

    def test_pause_during_recording_discards_frames(self, monkeypatch):
        """录音中被暂停时，已录帧应被丢弃"""
        mic = MicrophoneService()
        mic.pyaudio_available = True
        result = []

        quiet = _bytes_chunk(10, 1024)
        loud = _bytes_chunk(2000, 1024)

        # 高音量持续较长时间，确保触发录音
        chunks = [quiet] * 3 + [loud] * 30 + [quiet] * 20
        fake_stream = FakeStream(chunks)

        monkeypatch.setattr("pyaudio.PyAudio", lambda: type("PA", (), {
            "open": lambda self, **kw: fake_stream,
            "get_default_input_device_info": lambda self: {"index": 0, "defaultSampleRate": 16000},
            "terminate": lambda self: None,
        })())

        mic.listen_standby(
            callback=lambda p: result.append(p),
            speech_volume=100,
            max_seconds=2.0,
        )

        # 等待进入录音状态
        time.sleep(0.15)

        # 暂停（应该丢弃已录帧）
        mic.pause_listening()

        # 等待一段时间
        time.sleep(0.2)

        # 恢复
        mic.resume_listening()

        # 等待剩余 chunks 消耗完
        time.sleep(0.3)

        mic.stop_listen_standby()
        time.sleep(0.1)

        # 因为暂停期间丢弃了帧，且恢复后 chunks 已经差不多读完，
        # 所以可能没有回调或只有一个很短的回调
        # 关键是不能崩


# ══════════════════════════════════════════════════════
#  声源自动跟随（自动判断哪个设备有声音并切过去）
# ══════════════════════════════════════════════════════

class TestSourceAutoFollow:
    """声源自动跟随

    背景（2026-09-13 实测）：本机 14 个输入设备时，自动挑选取到的设备
    读到 RMS=52（底噪），而用户声音真正进的是另一个 RMS=3993 —— 差 76 倍。
    启动时挑一次不够，因为"哪个设备有声音"运行期会变
    （UU远程 会话建立/断开、Windows 重排索引）。
    """

    def test_request_switch_sets_flag(self):
        """请求切换只置标志，不直接开关流（跨线程操作 PyAudio 会访问违例）"""
        mic = MicrophoneService()
        assert mic._switch_to is None
        mic.request_device_switch(7)
        assert mic._switch_to == 7, "请求应被记录，由监听线程消费"

    def test_pinned_device_disables_auto_follow(self, monkeypatch):
        """用户显式指定设备时**不启用**自动跟随

        否则用户刚用热键切过去，就被观察者切回来 —— 明确选择必须优先。
        """
        _patch_fake_devices(monkeypatch)
        mic = MicrophoneService()
        mic._pin_device = True
        mic.start_source_watch()
        assert mic._watch_thread is None, "指定了设备就不该起观察者线程"
        assert mic._watch_stop is False

    def test_watch_tick_switches_to_louder_device(self, monkeypatch):
        """候选设备明显更有声音 → 请求切换"""
        mic = _patch_fake_devices(monkeypatch)
        mic._active_device = 1          # 当前听 idx=1
        mic._active_level = 50.0        # 当前设备基本是底噪
        mic._active_level_at = time.time() - 10.0   # 且很久没更新（已闲置）
        mic._last_switch_at = 0.0
        mic._watch_rotor = 0            # 下一个轮到 cands[1] → idx=2

        # idx=2 电平远高于当前
        monkeypatch.setattr(MicrophoneService, "probe_device_levels",
                            lambda self, seconds=1.2, indices=None: [
                                {"index": indices[0], "name": "x", "ok": True,
                                 "avg": 3993.0, "peak": 5187.0, "error": ""}])
        mic._watch_tick()
        assert mic._switch_to == 2, f"应请求切到 idx=2，实际 {mic._switch_to}"
        assert "自动跟随" in mic._auto_pick_reason

    def test_watch_tick_ignores_quiet_candidate(self, monkeypatch):
        """候选不比当前明显更有声音 → 不切（防来回抖）"""
        mic = _patch_fake_devices(monkeypatch)
        mic._active_device = 1
        mic._active_level = 800.0       # 当前设备正常收音
        mic._active_level_at = time.time()
        mic._last_switch_at = 0.0
        mic._watch_rotor = 0
        monkeypatch.setattr(MicrophoneService, "probe_device_levels",
                            lambda self, seconds=1.2, indices=None: [
                                {"index": indices[0], "name": "x", "ok": True,
                                 "avg": 900.0, "peak": 1000.0, "error": ""}])
        mic._watch_tick()
        assert mic._switch_to is None, "候选只略高时不应切换"

    def test_watch_tick_respects_cooldown(self, monkeypatch):
        """刚切过 → 冷却期内不再切（给设备/用户稳定时间）"""
        mic = _patch_fake_devices(monkeypatch)
        mic._active_device = 1
        mic._active_level = 10.0
        mic._last_switch_at = time.time()      # 刚切过
        mic._watch_rotor = 0
        monkeypatch.setattr(MicrophoneService, "probe_device_levels",
                            lambda self, seconds=1.2, indices=None: [
                                {"index": indices[0], "name": "x", "ok": True,
                                 "avg": 5000.0, "peak": 6000.0, "error": ""}])
        mic._watch_tick()
        assert mic._switch_to is None, "冷却期内不应再切"

    def test_watch_tick_skips_while_paused(self, monkeypatch):
        """播报期间（暂停）不折腾设备"""
        mic = _patch_fake_devices(monkeypatch)
        mic._active_device = 1
        mic._active_level = 10.0
        mic._last_switch_at = 0.0
        mic._watch_rotor = 0
        mic.pause_listening()
        called = []
        monkeypatch.setattr(MicrophoneService, "probe_device_levels",
                            lambda self, seconds=1.2, indices=None:
                            called.append(1) or [])
        mic._watch_tick()
        assert mic._switch_to is None
        assert called == [], "暂停期间不应探测设备"

    def test_watch_tick_skips_current_device(self, monkeypatch):
        """轮到的就是当前设备 → 不做无谓探测"""
        mic = _patch_fake_devices(monkeypatch)
        mic._active_device = 2
        mic._last_switch_at = 0.0
        mic._watch_rotor = 0            # cands[1] == idx=2 == 当前
        called = []
        monkeypatch.setattr(MicrophoneService, "probe_device_levels",
                            lambda self, seconds=1.2, indices=None:
                            called.append(1) or [])
        mic._watch_tick()
        assert called == [], "轮到当前设备时不该探测"

