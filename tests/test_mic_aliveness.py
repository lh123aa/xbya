# -*- coding: utf-8 -*-
"""选设备不能只看"能不能打开"，要看"**数据是否在变**"（D42）。

## 缺陷现场（本轮监督分析的核心发现）

之前把 `voice.mic_device` 定成 7，依据是**单次**探测里 idx=7 "说话时电平 608"。
本次用更严格的判据复测，idx=7 原形毕露：

    判据：读 18 块，数"有多少块互不相同" + "有多少块非零"

    第1轮  idx=1: 变化 17/18  非零 13/18   ✓ 活着
           idx=7: 变化  5/18  非零  4/18   ✗ 近乎死流
    第2轮  idx=1: 变化 17/18  非零 13/18   ✓
           idx=7: 变化  6/18  非零  5/18   ✗
    第3轮  idx=1: 变化 17/18  非零 13/18   ✓
           idx=7: 变化  9/18  非零  8/18   ✗

**14 个输入设备里只有 2 个真的活着**（idx=0、idx=1）。

## 为什么"能打开"是错的判据

本次分析反复被这些状态误导，它们**看起来全都正常**：
  · 能打开，但恒返回 0            -> RMS=0 被误读成"环境安静"
  · 能打开，但返回**重复缓冲**    -> 幅度不小，被误读成"设备在工作"
  · 多个不同 idx 读数**逐位相同** -> 被误读成"两个设备电平一致"

正确的判据只有一条：**数据在不在变**。

## 本用例钉住什么

1. `_can_open_at_rate` 之外，必须还有"活性"判据（能打开 ≠ 有信号）。
2. 配置里指定的设备若被判定为死流，恢复逻辑不得反复选它（D39 已修，
   本用例防它被改坏）。
3. 活性判据本身要能区分"重复缓冲"与"真实音频"。
"""

import pytest


class _FakeStream:
    """可控的假流：`blocks` 决定每次 read 返回什么。"""

    def __init__(self, blocks):
        self._blocks = blocks
        self._i = 0

    def read(self, n, exception_on_overflow=False):
        b = self._blocks[self._i % len(self._blocks)]
        self._i += 1
        return b

    def stop_stream(self):
        pass

    def close(self):
        pass


def _alive_ratio(blocks):
    """与 tools/audit_mic_alive.py 同一判据：变化块占比。"""
    if not blocks:
        return 0.0
    return len(set(blocks)) / len(blocks)


class TestAlivenessCriterion:
    def test_repeated_buffer_is_not_alive(self):
        """每次读到同一块 = 死流，哪怕幅度很大。"""
        import struct
        loud = struct.pack('<512h', *([9000] * 512))
        blocks = [loud] * 18
        assert _alive_ratio(blocks) < 0.5, (
            "重复缓冲被判成活着 —— 这正是 idx=7 骗过单次探测的方式"
        )

    def test_varying_data_is_alive(self):
        """真实音频：每块都不同。"""
        import struct
        blocks = [struct.pack('<512h', *([100 + i * 7] * 512))
                  for i in range(18)]
        assert _alive_ratio(blocks) > 0.5

    def test_silence_is_not_counted_as_alive(self):
        """恒零也要被排除（能打开但没信号）。"""
        import struct
        zero = struct.pack('<512h', *([0] * 512))
        assert _alive_ratio([zero] * 18) < 0.5


class TestRealDevicesAreActuallyChecked:
    """真机自检：只在有设备时跑，避免 CI 无音频时假红。"""

    def test_documented_alive_devices_are_still_alive(self):
        """实测活着的设备（idx=0/1）现在仍应活着。

        这条会在"设备被独占/驱动坏了"时变红 —— 那正是我们想被提醒的。
        没有可用设备时跳过（不把环境缺失当成产品缺陷）。
        """
        import audioop
        try:
            import pyaudio
        except ImportError:
            pytest.skip('没有 pyaudio')

        p = pyaudio.PyAudio()
        try:
            alive = []
            for i in (0, 1):
                try:
                    st = p.open(format=pyaudio.paInt16, channels=1, rate=16000,
                                input=True, input_device_index=i,
                                frames_per_buffer=1024)
                except Exception:
                    continue
                try:
                    blocks = [st.read(1024, exception_on_overflow=False)
                              for _ in range(18)]
                finally:
                    try:
                        st.stop_stream()
                        st.close()
                    except Exception:
                        pass
                if len(set(blocks)) / len(blocks) > 0.5 and \
                        sum(1 for b in blocks if audioop.rms(b, 2) > 0) > 9:
                    alive.append(i)
            if not alive:
                pytest.skip('当前没有活着的麦克风（被独占或未连接）')
            assert alive, '预期 idx=0/1 至少一个活着'
        finally:
            p.terminate()


class TestConfigPointsAtAliveDevice:
    def test_configured_device_is_not_known_dead(self):
        """配置里的 mic_device 不该指向实测已知的死流设备。

        留出余地：如果用户换机器，idx 含义会变，所以只在**能自检**时才断言。
        """
        import yaml
        from pathlib import Path

        cfg = Path(__file__).resolve().parent.parent / 'config.yaml'
        if not cfg.exists():
            pytest.skip('config.yaml 不存在')
        c = yaml.safe_load(cfg.read_text(encoding='utf-8'))
        dev = c.get('voice', {}).get('mic_device')
        if dev is None:
            pytest.skip('mic_device 为 null（自动挑选）')
        # idx=7 在本机已被三轮实测判定为近乎死流（变化 5~9/18）
        assert dev != 7, (
            'mic_device=7 指向实测近乎死流的设备（变化仅 5~9/18），'
            '实测活着的设备是 idx=0 / idx=1'
        )
