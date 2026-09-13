# -*- coding: utf-8 -*-
"""管线漏斗监督用例：ASR 出了文本就**必须**有去处，不许静默消失（D40）。

## 缺陷现场

端到端监督分析（131 秒真实样本）量出的漏斗：

    ① 触发 18  ->  ③ ASR 出文本 9  ->  ④ 送进对话 1      ★ 通过率 11%

**9 条识别结果里 8 条凭空消失**。日志在 `ASR 耗时 xxxms` 之后直接跳到
下一轮 `暂停监听` —— 没有"入队"、没有"丢弃"、没有"转 Agent"，**一片安静**。

用户感知正是"听到了半天不回复"：她明明识别出了东西，却什么都不做，
而日志里找不到任何理由。

## 根因（两条静默出口）

`_voice_pipeline` 在 ASR 之后串了两个过滤器，**两条都是静默 return**：

    if not text or self._should_ignore(text):
        self._handle_invalid()      # ← 只弹气泡+累加计数，不写"为什么"
        return
    if not self._is_real_speech(text):
        self._handle_noise()        # ← 只有 logger.debug（默认级别看不到）
        return

其中 `_should_ignore` 用 `voice.reply_min_length`（出厂 3）卡长度：
识别结果 `好` 只有 1 个字 → **必然**被拦。而本机环境下 ASR 最常
输出的恰恰就是 `好` 这类短词（背景音频里的一个词）。

于是形成闭环：**背景音 -> 识别成"好" -> 被判太短丢弃 -> 什么都不做**，
而用户完全不知道发生了什么。

## 本用例钉住什么

1. **每一级漏斗都要有可观测的去处**：被丢弃必须留下 INFO 级日志，
   并说明"被哪条规则、因为什么"。
2. `_should_ignore` / `_is_real_speech` 的判定理由必须可读出来
   （返回原因，而不是只返回 bool）—— 否则排查时只能靠猜。
"""

import logging

import pytest


@pytest.fixture
def win(qapp, monkeypatch):
    from PySide6.QtCore import QSettings
    from ui.pet_window import PetWindow

    w = PetWindow()
    monkeypatch.setattr(
        w, "_qsettings",
        lambda: QSettings("xbyaPet_test", "xbyaPet_test"),
    )
    yield w
    w.close()
    w.deleteLater()


class TestDropReasonIsObservable:
    """被丢弃的识别结果必须留下**可读的理由**。"""

    def test_short_text_drop_is_logged(self, win, caplog):
        """`好` 这种短词被丢时，必须 INFO 说明原因（不能静默）。"""
        with caplog.at_level(logging.INFO):
            dropped = win._should_ignore("好")
        assert dropped is True, "前提：'好' 确实会被长度规则拦下"
        text = " ".join(r.message for r in caplog.records)
        assert text, (
            "短文本被丢弃时没有任何日志 —— 用户看到的是"
            "「识别成功但毫无反应」，而排查者无从知道被哪条规则拦了"
        )
        assert "好" in text or "长度" in text, f"日志没说清原因: {text!r}"

    def test_noise_drop_is_logged_at_info(self, win, caplog):
        """被 _is_real_speech 判为噪音时，必须是 INFO（原先只有 debug）。"""
        with caplog.at_level(logging.INFO):
            ok = win._is_real_speech("请不吝点赞 订阅 转发 打赏支持明镜与点点栏目")
        assert ok is False
        text = " ".join(r.message for r in caplog.records)
        assert text, "判为噪音却只有 debug 日志，默认级别下完全看不到"

    def test_real_speech_passes_without_false_positive(self, win):
        """反方向保护：正常说话不许被这两条规则误杀。"""
        assert win._is_real_speech("今天天气怎么样") is True
        assert win._should_ignore("今天天气怎么样") is False


class TestFunnelHasNoSilentHole:
    """漏斗每一级都必须能对上数：进多少、出多少、丢多少。"""

    def test_every_drop_has_a_log_line(self, win, caplog):
        """遍历几种典型丢弃输入，每种都要有日志。"""
        cases = ["好", "嗯", "请不吝点赞 订阅 转发"]
        with caplog.at_level(logging.INFO):
            for c in cases:
                if win._should_ignore(c):
                    continue
                win._is_real_speech(c)
        n = len(caplog.records)
        assert n >= len(cases), (
            f"{len(cases)} 种丢弃场景只产生了 {n} 条日志 —— 存在静默出口"
        )
