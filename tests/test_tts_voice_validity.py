"""
TTS 音色有效性守护（D35）

缺陷背景：设置界面的音色菜单里，「晓伊 · 活泼可爱（欣雅推荐）」这一项
写的音色名是 `zh-CN-xbyaNeural` —— **这个音色在 Edge TTS 根本不存在**。
它是把项目名 `xbya` 拼进 `zh-CN-<name>Neural` 模板造出来的假名字。

后果不是"报错"，而是**全静默**：
  Edge TTS 对不存在的音色不抛异常，只是返回**空音频流**。
  于是 ① 界面能选中、能保存；② 插件记一条"合成完成"；
  ③ 用户什么都听不到，日志里只有 14 次 `No audio was received.`

更糟的是它是三个地方的默认值（设置界面 fallback、菜单"推荐"项、插件
构造函数），所以**什么都不改的用户默认就踩中**。

本用例钉住两件事：
  1. 菜单里每个音色都必须"形如" Edge 真实音色名（模板 + 已知列表）；
  2. 菜单/配置里**不许再出现** `xbyaNeural` 这个假名字。
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

#: 实测（2026-09，edge-tts 真机逐个流式合成过）可用的中文音色白名单。
#: 这是"离线判据"—— 用例不打网络，靠这张表挡住拼错的音色名。
KNOWN_GOOD_ZH_VOICES = {
    "zh-CN-XiaoxiaoNeural",
    "zh-CN-XiaoyiNeural",
    "zh-CN-liaoning-XiaobeiNeural",
    "zh-CN-shaanxi-XiaoniNeural",
    "zh-HK-HiuGaaiNeural",
    "zh-HK-HiuMaanNeural",
    "zh-HK-WanLungNeural",
    "zh-TW-HsiaoChenNeural",
    "zh-TW-HsiaoYuNeural",
    "zh-TW-YunJheNeural",
    "zh-CN-YunxiNeural",
    "zh-CN-YunjianNeural",
    "zh-CN-YunxiaNeural",
    "zh-CN-YunyangNeural",
}

#: 已知**不存在**的音色名（拼错/臆造的），出现在任何地方都是缺陷。
KNOWN_BAD_VOICES = {"zh-CN-xbyaNeural"}


def _settings_source() -> str:
    return (ROOT / "ui" / "settings_dialog.py").read_text(encoding="utf-8")


def _menu_voices() -> list:
    """从 TTS_VOICES 菜单里取出所有音色值。"""
    src = _settings_source()
    block = src.split("TTS_VOICES = [", 1)[1].split("\n]", 1)[0]
    return re.findall(r'"(zh-[A-Za-z-]+Neural)"', block)


class TestVoiceMenu:
    def test_menu_is_not_empty(self):
        """哨兵：先把"截取失败"堵掉，否则下面几条会在空集上假绿。"""
        voices = _menu_voices()
        assert len(voices) >= 10, f"音色菜单只解析出 {len(voices)} 项，解析可能失效"

    def test_every_menu_voice_is_a_real_edge_voice(self):
        """菜单里每个音色都必须在实测可用的白名单里。"""
        unknown = [v for v in _menu_voices() if v not in KNOWN_GOOD_ZH_VOICES]
        assert not unknown, (
            f"音色菜单里有未经验证（很可能是臆造）的音色：{unknown}。"
            f"Edge 对不存在的音色不报错、只回空音频，会导致用户完全听不到声音。"
        )

    def test_no_known_bad_voice_in_menu(self):
        """反方向：假音色不许出现在菜单里。"""
        bad = [v for v in _menu_voices() if v in KNOWN_BAD_VOICES]
        assert not bad, f"菜单里仍有不存在的音色：{bad}"

    def test_recommended_option_points_at_a_real_voice(self):
        """「欣雅推荐」那项必须指向真实音色（它是最容易被选中的）。"""
        src = _settings_source()
        recommended = [
            m for m in re.findall(r'\("([^"]*推荐[^"]*)",\s*"(zh-[A-Za-z-]+Neural)"\)', src)
        ]
        assert recommended, "没有找到标注「推荐」的音色项，界面可能被改过"
        for label, voice in recommended:
            assert voice in KNOWN_GOOD_ZH_VOICES, (
                f"推荐音色 '{label}' 指向不存在的音色 {voice}"
            )


class TestNoBadVoiceAnywhere:
    def test_settings_dialog_has_no_bad_voice(self):
        """设置界面（含默认值 fallback）不许再出现假音色。"""
        src = _settings_source()
        for bad in KNOWN_BAD_VOICES:
            assert bad not in src, f"ui/settings_dialog.py 仍引用不存在的音色 {bad}"

    def test_tts_plugin_default_voice_is_real(self):
        """插件构造函数的默认音色必须是真实的（直接 new 的场景要用它）。"""
        from plugins.tts.edge_tts.plugin import EdgeTTS

        import inspect

        sig = inspect.signature(EdgeTTS.__init__)
        default = sig.parameters["voice"].default
        assert default not in KNOWN_BAD_VOICES, f"插件默认音色仍是不存在的 {default}"
        assert default in KNOWN_GOOD_ZH_VOICES, f"插件默认音色 {default} 未经验证"


class TestEmptyAudioIsReported:
    """空音频必须被当成失败，不能静默返回 b''。"""

    def test_empty_audio_returns_none_and_logs(self, caplog):
        """用假底层模拟"音色不存在→空流"，断言插件报错并返回 None。"""
        import logging

        from plugins.tts.edge_tts.plugin import EdgeTTS

        tts = EdgeTTS(voice="zh-CN-XiaoyiNeural")
        assert tts.is_available(), "edge-tts 未安装，本用例无法运行"

        # 让内部 _speak 返回空 bytes，模拟 Edge 对未知音色的行为
        import asyncio

        real_new_loop = asyncio.new_event_loop

        def fake_loop():
            loop = real_new_loop()

            class _Wrapper:
                def run_until_complete(self, coro):
                    coro.close()          # 丢弃真实合成，直接给空结果
                    return b""

                def close(self):
                    loop.close()

            return _Wrapper()

        asyncio.new_event_loop = fake_loop
        try:
            with caplog.at_level(logging.ERROR):
                result = tts.speak("测试")
        finally:
            asyncio.new_event_loop = real_new_loop

        assert result is None, "空音频必须返回 None，否则调用方会以为合成成功"
        assert any("空音频" in r.message for r in caplog.records), (
            "空音频必须打 ERROR 日志并点名音色，否则排查时无从下手"
        )
