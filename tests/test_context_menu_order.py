# -*- coding: utf-8 -*-
"""右键菜单的项序（D48）。

## 为什么钉项序

菜单是**自上而下扫读**的 —— 常用项排在顶部才不用每次找。
"置顶"是这个菜单里最常用的开关（用户明确要求它排最前），
但它是**后加进去的**，之前排在"字幕/声音"之后。

项序不会被任何断言自然覆盖：调整菜单代码时顺序很容易被顺手改回去，
而**没有任何东西会变红**。所以这里显式钉住。

## 判据

  · 第一个**非分隔符**项必须是「置顶」
  · 四个功能开关在同一组内、顺序为 置顶 → 字幕 → 声音 → 麦克风
  · 分隔符仍然把「功能开关」「设置」「重启/退出」分成三组
"""

import pytest


@pytest.fixture
def win(qapp, monkeypatch):
    from PySide6.QtCore import QSettings
    from ui.pet_window import PetWindow

    w = PetWindow()
    monkeypatch.setattr(w, "_qsettings",
                        lambda: QSettings("xbyaPet_test", "xbyaPet_test"))
    yield w
    w.close()
    w.deleteLater()


def _labels(menu):
    return [None if a.isSeparator() else a.text() for a in menu.actions()]


class TestAlwaysOnTopIsFirst:
    def test_first_non_separator_is_always_on_top(self, win):
        menu = win._build_context_menu()
        labels = [x for x in _labels(menu) if x is not None]
        assert labels, '菜单是空的'
        assert '置顶' in labels[0], (
            f'第一项是 {labels[0]!r}，不是置顶 —— '
            f'常用项应在顶部（菜单自上而下扫读）。实际顺序: {labels}'
        )

    def test_no_leading_separator(self, win):
        """第一项不能是分隔符（会在顶部留一条横线）。"""
        menu = win._build_context_menu()
        acts = menu.actions()
        assert acts and not acts[0].isSeparator(), '菜单第一项是分隔符'


class TestFunctionTogglesGrouping:
    def test_four_toggles_in_order(self, win):
        """四个功能开关同属第一组，顺序固定。"""
        menu = win._build_context_menu()
        labels = _labels(menu)
        # 取第一个分隔符之前的项
        first_group = []
        for x in labels:
            if x is None:
                break
            first_group.append(x)
        joined = ' '.join(first_group)
        for name in ('置顶', '字幕', '声音', '麦克风'):
            assert name in joined, f'第一组里没有「{name}」: {first_group}'
        order = [next(i for i, t in enumerate(first_group) if name in t)
                 for name in ('置顶', '字幕', '声音', '麦克风')]
        assert order == sorted(order), (
            f'功能开关顺序不对，期望 置顶→字幕→声音→麦克风，实际 {first_group}'
        )

    def test_toggles_are_checkable(self, win):
        """四个开关都必须是可勾选的（否则看不出当前状态）。"""
        menu = win._build_context_menu()
        for a in menu.actions():
            if a.isSeparator():
                continue
            if any(n in a.text() for n in ('置顶', '字幕', '声音', '麦克风')):
                assert a.isCheckable(), f'{a.text()} 不可勾选'


class TestSeparatorsStillDivide:
    def test_three_groups(self, win):
        """分隔符把菜单分成三组：功能开关 / 设置 / 重启退出。"""
        menu = win._build_context_menu()
        labels = _labels(menu)
        n_sep = sum(1 for x in labels if x is None)
        assert n_sep == 2, f'期望 2 个分隔符（分三组），实际 {n_sep} 个'
        # 分组内容
        groups, cur = [], []
        for x in labels:
            if x is None:
                groups.append(cur)
                cur = []
            else:
                cur.append(x)
        groups.append(cur)
        assert len(groups) == 3
        assert any('设置' in t for t in groups[1]), f'第二组应是设置: {groups[1]}'
        assert any('退出' in t for t in groups[2]), f'第三组应是重启/退出: {groups[2]}'
