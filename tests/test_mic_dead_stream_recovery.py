# -*- coding: utf-8 -*-
"""死流恢复不许"把同一个死设备再挑回来"（D39）。

## 缺陷现场

用户报告"她怎么听不见，还答非所问"。日志里真正的异常是这个：

    [mic] 连续 6 秒电平恒为 0 → 判定音频流已死，重开设备
    [mic] 死流恢复：已重开 idx=7
    [mic] 连续 6 秒电平恒为 0 → 判定音频流已死，重开设备
    [mic] 死流恢复：已重开 idx=7
    ... 实测 **16 次，每秒一次，停不下来**

## 根因：`exclude` 在"已显式配置设备"时被完全忽略

`_pick_mic_index()` 的第一优先级是：

    if self.input_device is not None:
        return self.input_device      # ← 直接返回，**根本没看 exclude**

于是当 `voice.mic_device = 7`（用户显式指定）时：

    1. 死流 → 调 `_pick_mic_index(exclude=7)`，期望"换一个"
    2. 但第一分支直接 `return 7` —— exclude 被无视
    3. 重开同一个死设备 → 6 秒后又死 → 无限循环

后果不只是刷日志：每次重开都要开关音频流，**把设备搅得更不稳定**，
而且 `noise_floor` 被反复重置/污染，最终底噪涨到 4000+，
真人声（600 左右）再也过不了阈值 —— 用户听到的现象就是"她听不见"。

## 正确行为

`exclude` 是**硬约束**：调用方说"这个设备不要"，就绝不能返回它。
用户显式配置表达的是"首选"，不是"哪怕它死了也只能用它" ——
死流恢复的语义本来就是"当前设备已经确认不可用，请换一个"。
"""

import pytest


class _FakePa:
    """假 PyAudio：只提供设备枚举与默认设备。"""

    DEVICES = [
        {"index": 0, "name": "Microsoft 声音映射器 - Input", "maxInputChannels": 1},
        {"index": 1, "name": "麦克风 (Realtek(R) Audio)", "maxInputChannels": 1},
        {"index": 2, "name": "麦克风阵列 (UU远程虚拟音频设备)", "maxInputChannels": 1},
    ]

    def get_device_count(self):
        return len(self.DEVICES)

    def get_device_info_by_index(self, i):
        return self.DEVICES[i]

    def get_default_input_device_info(self):
        return {"index": 1}

    def terminate(self):
        pass


@pytest.fixture
def mic(monkeypatch):
    import services.microphone_service as ms

    monkeypatch.setattr(ms, "_HAS_NUMPY", True, raising=False)
    m = ms.MicrophoneService()
    # 关掉实测电平那条路，让判据只落在"优先级与 exclude"上
    monkeypatch.setattr(m, "_pick_by_level", lambda exclude=None: None)
    monkeypatch.setattr(m, "_can_open_at_rate",
                        staticmethod(lambda p, i, r: True), raising=False)

    import pyaudio
    monkeypatch.setattr(pyaudio, "PyAudio", lambda: _FakePa())
    return m


class TestExcludeIsHardConstraint:
    def test_explicit_device_is_not_returned_when_excluded(self, mic):
        """显式配置的设备一旦被 exclude，就**绝不能**再被返回。

        这是本缺陷的核心：原实现在第一优先级里直接 `return self.input_device`，
        `exclude` 形同不存在 —— 死流恢复于是把同一个死设备反复挑回来。
        """
        mic.input_device = 1
        got = mic._pick_mic_index(exclude=1)
        assert got != 1, (
            "把刚判定为'死流'的设备又挑回来了 —— "
            "这会让恢复循环变成每秒重开一次的无限循环"
        )

    def test_explicit_device_returned_when_not_excluded(self, mic):
        """反方向保护：不 exclude 时，显式配置必须照常生效。"""
        mic.input_device = 1
        assert mic._pick_mic_index() == 1

    def test_excluded_device_skipped_even_with_name_match(self, mic):
        """名字片段匹配同样要遵守 exclude。"""
        mic.input_device = None
        mic.input_device_name = "Realtek"
        got = mic._pick_mic_index(exclude=1)
        assert got != 1, "名字匹配绕过了 exclude"


class TestRecoveryLoopCannotRepeatSameDevice:
    def test_recovery_picks_a_different_device(self, mic):
        """模拟"设备 1 死了要换一个"：必须拿到别的索引。"""
        mic.input_device = 1
        seen = set()
        for _ in range(3):
            nxt = mic._pick_mic_index(exclude=1)
            if nxt is not None:
                seen.add(nxt)
        assert 1 not in seen, f"恢复时又选中了死设备: {seen}"
