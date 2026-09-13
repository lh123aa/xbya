# -*- coding: utf-8 -*-
"""语音输入的判据必须能区分"活着"、"能收到声音"、"收到的是人声"（D44）。

## 本轮的三层判据（每层淘汰不同的假象）

排查"听不到"时踩到的假象，逐层都要有自己的判据 ——
只看"能不能打开"会被骗，只看"幅度大不大"也会被骗。

| 层 | 判据 | 它能淘汰的假象 |
|----|------|---------------|
| L1 活性 | 读到的块**在变**，且非零占比 > 50% | 能打开但恒零；能打开但返回**重复缓冲** |
| L2 响应 | 敲击/吹气时峰值**明显上升**（≥1.5x） | 流在跑但物理麦克风没把声音送进来 |
| L3 人声 | 说话段在人声频段（300-3400Hz）能量 **> 1.3x** 安静段 | 宽带噪声在起伏，被误当成说话 |

## 实测数据（本机，2026-09）

    L1 活性：14 个设备里只有 2 个活着（idx=0/1）
    L2 响应：**全部设备 1.00~1.15x —— 没有设备对敲击有响应**
    L3 人声：人声频段 0.82x（**低于**安静段）=> 录到的不是人声

结论：麦克风没有真正拾取用户的声音。这是硬件/系统层面问题，
**软件增益解决不了** —— 实测把录音放大到 40 倍（峰值 22680 不削顶），
ASR 依然全部返回 `None`；本地 faster-whisper 同样为空。
"""

import numpy as np
import pytest


# ── L1 活性判据 ──

def _alive_ratio(blocks):
    if not blocks:
        return 0.0, 0.0
    uniq = len(set(blocks)) / len(blocks)
    nz = sum(1 for b in blocks
             if np.abs(np.frombuffer(b, dtype=np.int16)).max() > 0) / len(blocks)
    return uniq, nz


def _pack(vals):
    return np.array(vals, dtype=np.int16).tobytes()


class TestL1Aliveness:
    def test_repeated_buffer_is_dead(self):
        """每次读到同一块 = 死流（本机 idx=8 实测 2/10）。"""
        loud = _pack([9000] * 512)
        u, nz = _alive_ratio([loud] * 10)
        assert u < 0.5, '重复缓冲被判成活着'

    def test_all_zero_is_dead(self):
        """恒零 = 没信号（本机 idx=2/6 实测）。"""
        z = _pack([0] * 512)
        u, nz = _alive_ratio([z] * 10)
        assert nz < 0.5

    def test_varying_nonzero_is_alive(self):
        blocks = [_pack([100 + i * 13] * 512) for i in range(10)]
        u, nz = _alive_ratio(blocks)
        assert u > 0.5 and nz > 0.5


# ── L2 响应判据 ──

def _peak(blocks):
    if not blocks:
        return 0
    pcm = np.frombuffer(b''.join(blocks), dtype=np.int16)
    return int(np.abs(pcm).max())


class TestL2TapResponse:
    def test_uniform_level_across_tap_is_no_response(self):
        """敲击与安静峰值几乎一样 = 麦克风没把声音送进来。

        本机实测所有设备都是 1.00~1.15x —— 这就是"设备活着但收不到声音"。
        """
        quiet = [_pack([500] * 512) for _ in range(5)]
        tap = [_pack([530] * 512) for _ in range(5)]
        ratio = _peak(tap) / max(_peak(quiet), 1)
        assert ratio < 1.5, (
            f'比值 {ratio:.2f}x 被判成"有响应" —— 实际只是噪声起伏'
        )

    def test_real_tap_shows_large_ratio(self):
        """真敲击会产生明显跳变。"""
        quiet = [_pack([100] * 512) for _ in range(5)]
        tap = [_pack([4000] * 512) for _ in range(5)]
        assert _peak(tap) / max(_peak(quiet), 1) >= 1.5


# ── L3 人声判据 ──

def _band_energy_ratio(speech_pcm, quiet_pcm, rate=16000,
                       lo=300, hi=3400):
    def e(x):
        x = x - x.mean()
        f = np.fft.rfft(x * np.hanning(len(x)))
        freqs = np.fft.rfftfreq(len(x), 1 / rate)
        return np.abs(f)[(freqs >= lo) & (freqs < hi)].mean()
    return e(speech_pcm) / max(e(quiet_pcm), 1e-9)


class TestL3HumanVoiceBand:
    def test_matching_energy_means_not_voice(self):
        """人声频段能量没上升 => 录到的不是人声。

        本机实测 0.82x（**低于**安静段），说明那只是宽带噪声。
        """
        rng = np.random.default_rng(0)
        quiet = rng.normal(0, 120, 16000 * 2)
        speech = rng.normal(0, 130, 16000 * 2)   # 只是噪声变响，没有共振峰
        r = _band_energy_ratio(speech, quiet)
        assert r < 1.3, f'纯噪声被判成有人声（{r:.2f}x）'

    def test_tone_in_voice_band_is_detected(self):
        """人声频段有真实能量时判据要能认出来（反方向保护）。"""
        t = np.arange(16000 * 2) / 16000
        rng = np.random.default_rng(1)
        quiet = rng.normal(0, 100, len(t))
        # 700Hz 正弦 = 典型人声基频附近
        speech = quiet + 3000 * np.sin(2 * np.pi * 700 * t)
        assert _band_energy_ratio(speech, quiet) > 1.3


class TestGainCannotFixSnr:
    """把"软件增益救不了 SNR"这一结论固化成用例。"""

    def test_amplifying_noise_does_not_create_voice(self):
        """等比放大不改变频段比值 —— 所以增益救不了。"""
        rng = np.random.default_rng(2)
        quiet = rng.normal(0, 120, 16000 * 2)
        speech = rng.normal(0, 130, 16000 * 2)
        r1 = _band_energy_ratio(speech, quiet)
        r2 = _band_energy_ratio(speech * 20, quiet * 20)
        assert abs(r1 - r2) < 0.05, (
            f'放大后比值变了（{r1:.2f} -> {r2:.2f}）—— '
            f'与"等比增益不改变信噪比"矛盾'
        )
