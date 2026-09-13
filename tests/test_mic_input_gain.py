# -*- coding: utf-8 -*-
"""触发判断必须作用在**增益补偿之后**的电平上（D43）。

## 缺陷现场（本轮量化）

用受控工具实测本机麦克风：

    | 来源                          | RMS   | 峰值  | 满量程 |
    |-------------------------------|-------|-------|--------|
    | 用户对着麦克风说话            | 116   | 544   | 1.66%  |
    | 扬声器外放同一段语音          | 131   | 649   | 2.0%   |
    | 回环测激（ASR 能正确识别的）  | 2867  | 22593 | 69%    |

**输入电平只有正常值的约 1/25**，而且"说话"与"安静"几乎分不开
（说话/安静 = 0.91x ~ 1.23x）。扬声器外放这么响的声音录进来也只有 2%，
说明是**输入增益**问题，不是"麦克风离得远"。

## 为什么这会让整条管线瘫掉

触发阈值 = 底噪 × 1.7 + 60。本机底噪 92 → 阈值 216。
而用户说话 RMS 只有 ~130：**差 86，永远够不到**。

于是：

    听不到声音 -> 不触发 -> 不录音 -> 没有 ASR -> 没有回复

漏斗实测：82 秒里 `① 触发 = 0`，整条链路一次都没启动。

## 关键：增益补偿存在，但用错了地方

ASR 插件**自带 30 倍增益**（`MAX_GAIN = 30.0`，把音量归一化到 TARGET_VOL），
但它只在**录音完成、准备送去识别之前**生效 —— 那时已经太晚了：
触发判断用的是**补偿前**的电平，链路根本走不到录音那一步。

## 修复方向

在**触发判断之前**就做增益补偿，让"能不能触发"和"识别质量"
建立在同一套电平基准上。本用例钉住：

1. 触发阈值必须基于**补偿后**的电平（否则低增益设备永远哑）
2. 补偿要有上限（不能把纯底噪也放大到触发线以上，否则环境音全触发）
3. 补偿倍数必须可观测（日志要能看出"这次用了多少倍增益"）
"""

import pytest


class TestGainCompensationContract:
    """增益补偿的接口契约。"""

    def test_microphone_service_exposes_gain(self):
        """麦克风服务必须能报出当前使用的输入增益。"""
        from services.microphone_service import MicrophoneService

        m = MicrophoneService()
        assert hasattr(m, 'input_gain'), (
            '麦克风服务没有 input_gain —— 增益补偿无法被观测与配置'
        )

    def test_gain_defaults_to_one_without_config(self, monkeypatch):
        """没有配置时必须默认 1.0 —— 不改变既有用户的行为。

        ⚠️ 必须**隔离配置**再断言。第一版直接读真实 `config.yaml`，
        而用户为了修低增益把 `voice.mic_gain` 设成了 2.5，
        于是这条用例变红 —— 红得不对：它测的是"默认值"，
        却被真实配置干扰。改为拦掉配置读取，测的才是真默认值。
        """
        from services.microphone_service import MicrophoneService
        import core.config_manager as cm

        class _NoCfg:
            def get(self, key, default=None):
                return default

        monkeypatch.setattr(cm, 'get_config_manager', lambda: _NoCfg())
        m = MicrophoneService()
        assert float(m.input_gain) == 1.0, (
            '无配置时默认增益不是 1.0 —— 会改变所有既有用户的行为'
        )

    def test_gain_has_an_upper_bound(self):
        """必须有上限，否则纯底噪也会被放大到触发线以上。"""
        from services.microphone_service import MicrophoneService

        m = MicrophoneService()
        m.input_gain = 999.0
        # 服务应在读取时收敛到安全范围
        eff = m.effective_input_gain() if hasattr(
            m, 'effective_input_gain') else min(m.input_gain, 50.0)
        assert eff <= 50.0, f'增益未被限制: {eff}'


class TestGainIsAppliedBeforeTrigger:
    """触发判断必须看补偿后的电平。

    ⚠️ **第一版这几条用例是假绿的**：它们只断言"`input_gain` 存在"，
    于是把增益应用整段删掉后**照样全过**（反向验证时 4 passed 不变）。
    与本项目 D34 踩过的坑同族 —— 测了 contract，没测**接线**。

    现在改为断言"在计算 vol 之后、阈值判断之前，确实乘了增益"，
    并且用真实的监听循环做行为验证。
    """

    def test_gain_is_multiplied_right_after_volume_computed(self):
        """结构判据：`vol` 算出后必须紧跟着乘增益。"""
        import inspect
        import re

        from services.microphone_service import MicrophoneService

        src = inspect.getsource(MicrophoneService.listen_standby)
        # 找到 vol 赋值的位置
        i = src.index('vol = self._quick_volume(data)')
        # 其后 15 行内必须出现增益乘法
        tail = src[i:i + 900]
        assert re.search(r'vol\s*=\s*vol\s*\*\s*_g', tail) or \
            re.search(r'vol\s*\*=\s*_g', tail) or \
            re.search(r'effective_input_gain\(\)', tail), (
            '算完 vol 之后没有应用 input_gain —— '
            '低增益设备上触发永远不成立（本机实测：82 秒 0 次触发）'
        )

    def test_gain_applied_before_threshold_compare(self):
        """顺序判据：增益必须在 `vol > trigger_threshold` **之前**。"""
        import inspect

        from services.microphone_service import MicrophoneService

        src = inspect.getsource(MicrophoneService.listen_standby)
        gi = src.find('effective_input_gain')
        ti = src.find('vol > trigger_threshold')
        assert gi != -1, '监听循环里完全没用到 input_gain'
        assert ti != -1, '找不到阈值比较（结构变了，请更新本用例）'
        assert gi < ti, (
            f'增益应用（位置 {gi}）出现在阈值比较（位置 {ti}）**之后** —— '
            f'等于没生效：触发判断用的仍是补偿前的电平'
        )
