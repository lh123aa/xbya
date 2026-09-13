# -*- coding: utf-8 -*-
"""D50 回归：`PluginLoader` 的结果不许依赖"别人有没有先 scan()"。

## 这个用例防的是什么

`get_plugin_loader()` 是单例，但**"谁负责扫盘"原先没有归属**：
`core/app.py:153` 会代劳，而 `FileService` / `VoiceService` / `AiService`
的 `__init__` 只拿单例、不扫盘，**默认别人扫过了**。

于是同一个 `FileService()` 有两副面孔：
  · 在 App 之后构造 → 插件齐全（撞巧）
  · 独立构造 → `plugins` 空 → 每次 load 打「插件不存在: X」

**那条消息是错的**：不是插件不存在，是这个 loader 没发现任何插件。

## 为什么不能只断言"load 成功"

因为 `load()` 成功与否**还取决于磁盘上有没有那个插件**。
本文件的判据刻意分成两层：
  1. **顺序无关性**（核心）：同一 loader，scan 前后 load 同一个插件，结果必须一致
  2. **报错自证**：找不到插件时，消息里必须能看出"我确实找过"（列出已发现的名字），
     而不是一句无信息的「插件不存在」
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.plugin_loader import PluginLoader  # noqa: E402

#: 磁盘上必然存在的一个插件（`plugins/file_monitor/watchfiles/plugin.py`）
KNOWN = 'watchfiles'


def test_load_auto_scans_when_never_scanned():
    """**核心判据**：全新 loader 直接 load，必须先自己扫盘。

    反方向：把 `if not self._scanned: self.scan()` 删掉 → 本用例变红
    （返回值变成 None）。
    """
    pl = PluginLoader()
    assert pl._scanned is False, '前提：新 loader 没扫过盘'
    assert pl.plugins == {}, '前提：新 loader 的 plugins 是空的'

    inst = pl.load(KNOWN, params={})

    assert inst is not None, (
        f'load({KNOWN!r}) 返回 None —— 懒扫盘没生效，'
        f'结果依赖调用方是否先调过 scan()（这正是 D50）'
    )
    assert pl._scanned is True, 'load 之后应该已经扫过盘'
    assert KNOWN in pl.plugins


def test_result_is_independent_of_call_order():
    """先后两种顺序，`load` 的结果必须一致 —— 这是"顺序无关"的直接表述。"""
    a = PluginLoader()
    a.load(KNOWN, params={})                 # 先 load（会补扫）
    ra = a.plugins.get(KNOWN)

    b = PluginLoader()
    b.scan()                                 # 先 scan
    rb = b.plugins.get(KNOWN)

    assert (ra is None) == (rb is None), (
        '两种顺序下"能不能发现该插件"的结论不一致 —— 顺序仍然有影响'
    )
    assert ra is not None and rb is not None
    assert ra.name == rb.name


def test_scanned_flag_set_even_when_nothing_found(monkeypatch, tmp_path):
    """扫过盘（哪怕一个插件都没有）之后不该反复重扫。

    用独立标志而不是 `if not self.plugins` —— 空目录是合法状态。
    """
    empty = tmp_path / 'plugins'
    empty.mkdir()
    pl = PluginLoader(plugins_dir=str(empty))

    calls = {'n': 0}
    real_scan = pl.scan

    def counting_scan():
        calls['n'] += 1
        return real_scan()

    monkeypatch.setattr(pl, 'scan', counting_scan)

    pl.load('nope1')
    pl.load('nope2')
    pl.load('nope3')

    assert calls['n'] == 1, (
        f'scan() 被调了 {calls["n"]} 次 —— 空目录下反复重扫；'
        f'`_scanned` 标志没起作用'
    )
    assert pl._scanned is True


def test_missing_plugin_error_lists_what_was_found(caplog):
    """报错必须自证"我确实找过"。

    改前只有「插件不存在: X」——读日志的人无法区分
    "插件真没有"与"这个 loader 没扫盘"。
    """
    pl = PluginLoader()
    pl.scan()

    with caplog.at_level(logging.ERROR):
        got = pl.load('绝对不存在的插件名_zzz')

    assert got is None
    text = caplog.text
    assert '绝对不存在的插件名_zzz' in text
    # 关键：消息里要能看出"扫描过、发现了哪些"
    assert KNOWN in text, (
        '报错没有列出已发现的插件 —— 无法判断是"没扫盘"还是"真没有"'
    )


def test_error_message_distinguishes_unscanned_from_truly_absent(caplog):
    """两种失败原因的**消息要能分辨**。

    · 没扫盘（且目录为空）→ 已发现列表是"无"
    · 真没有（扫过了）→ 已发现列表里有一堆名字
    同一句话必须把这两种情况区分开。
    """
    empty = Path(__file__).resolve().parent / '_d50_empty_plugins'
    empty.mkdir(exist_ok=True)
    try:
        pl = PluginLoader(plugins_dir=str(empty))
        with caplog.at_level(logging.ERROR):
            pl.load('whatever')
        assert '已发现 0 个' in caplog.text or '无' in caplog.text

        caplog.clear()
        pl2 = PluginLoader()
        with caplog.at_level(logging.ERROR):
            pl2.load('whatever')
        assert '已发现' in caplog.text
        assert KNOWN in caplog.text
    finally:
        try:
            empty.rmdir()
        except OSError:
            pass
