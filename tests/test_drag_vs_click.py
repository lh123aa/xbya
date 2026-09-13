# -*- coding: utf-8 -*-
"""拖拽与点击的判据必须一致（D38）。

缺陷背景：用户报告"启动位置是上次关闭时所在位置"——实测发现**位置系统本身是对的**
（启动确实恢复到存档坐标），但**几秒后位置就被改掉了**。日志给出直接证据：

    14:06:00  [位置] 恢复到 (500, 350)      ← 恢复正确
    14:06:05  [位置] 保存到 (1643, 477)     ← 5 秒后被改写
    14:06:05  [互动] 拖拽触发: angry        ← 被判定成"拖拽"

根因：`mousePressEvent` 只要按下左键就把 `self.dragging = True`；
`mouseReleaseEvent` 里 `was_dragging = self.dragging`，**只按"按下过"判定拖拽**，
完全不看实际位移。

后果有两条，都是用户可见的：
  1. **一次单击也会被判成拖拽** —— 哪怕鼠标一动没动，松手时照样
     `on_drag()`（动画变 angry），并且 `_save_position()` 已经被调用过；
  2. 真正的"误触"（手抖拖了 1~2px）会把**位置存档改掉** ——
     用户下次启动就落到一个自己没选过的位置，而现象看起来像"位置记错了"。

正确判据：拖拽 = 按下过 **且** 位移超过阈值；点击 = 按下过 **且** 位移不超过阈值。
两者互补且互斥，不能各用各的标准。
"""

import pytest
from PySide6.QtCore import QPointF
from PySide6.QtCore import QSettings
from PySide6.QtCore import Qt
from PySide6.QtGui import QMouseEvent

from ui.pet_window import PetWindow


def _press(win, x, y):
    ev = QMouseEvent(QMouseEvent.Type.MouseButtonPress,
                     QPointF(x, y), QPointF(x, y),
                     Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
    win.mousePressEvent(ev)


def _move(win, x, y):
    ev = QMouseEvent(QMouseEvent.Type.MouseMove,
                     QPointF(x, y), QPointF(x, y),
                     Qt.NoButton, Qt.LeftButton, Qt.NoModifier)
    win.mouseMoveEvent(ev)


def _release(win, x, y):
    ev = QMouseEvent(QMouseEvent.Type.MouseButtonRelease,
                     QPointF(x, y), QPointF(x, y),
                     Qt.LeftButton, Qt.NoButton, Qt.NoModifier)
    win.mouseReleaseEvent(ev)


@pytest.fixture
def win(qapp, monkeypatch):
    """造一个 pet 窗口；位置存档走隔离命名空间，绝不碰用户真实存档。

    与 tests/test_window_position.py 同一套做法 —— 本项目吃过"复刻判据"的亏，
    测试要打真实实现，不另写一份。
    """
    w = PetWindow()
    monkeypatch.setattr(
        w, "_qsettings",
        lambda: QSettings("xbyaPet_test", "xbyaPet_test"),
    )
    s = w._qsettings()
    s.clear()
    s.sync()
    w._position_restored = True       # 越过"恢复完成前不写"的闸门
    yield w
    w.close()
    w.deleteLater()


class TestPureClickIsNotADrag:
    """按下→原地松手，**必须**算点击而不是拖拽。"""

    def test_stationary_press_release_is_not_drag(self, win, monkeypatch):
        drag_calls = []
        monkeypatch.setattr(win.state_machine, "on_drag",
                            lambda: (drag_calls.append(1), "angry")[1])
        monkeypatch.setattr(win.state_machine, "on_click", lambda: "pet")

        _press(win, 10, 10)
        _release(win, 10, 10)

        assert not drag_calls, (
            "原地单击被判成了拖拽 —— 会触发动效并改写位置存档"
        )

    def test_tiny_jitter_is_not_drag(self, win, monkeypatch):
        """手抖 1~3px 仍在"点击"范围内，不该算拖拽。"""
        drag_calls = []
        monkeypatch.setattr(win.state_machine, "on_drag",
                            lambda: (drag_calls.append(1), "angry")[1])
        monkeypatch.setattr(win.state_machine, "on_click", lambda: "pet")

        _press(win, 10, 10)
        _move(win, 12, 11)            # 位移约 2px
        _release(win, 12, 11)

        assert not drag_calls, "2px 的手抖被当成了拖拽"


class TestRealDragIsADrag:
    """反方向保护：真的拖了，就必须算拖拽。"""

    def test_large_move_is_drag(self, win, monkeypatch):
        drag_calls = []
        monkeypatch.setattr(win.state_machine, "on_drag",
                            lambda: (drag_calls.append(1), "angry")[1])
        monkeypatch.setattr(win.state_machine, "on_click", lambda: "pet")

        _press(win, 10, 10)
        _move(win, 120, 90)           # 明显位移
        _release(win, 120, 90)

        assert drag_calls, "真的拖了却没触发拖拽动效"


class TestDragThresholdIsShared:
    """拖拽与点击必须用**同一个**位移判据，否则两者会重叠。"""

    def test_threshold_constant_exists_and_is_reused(self):
        import inspect
        src = inspect.getsource(PetWindow.mouseReleaseEvent)
        # 拖拽分支必须真的检查位移，而不是只看"按下过"
        assert "moved" in src, "mouseReleaseEvent 里没有位移判据"
        assert "was_dragging and moved" in src or "was_dragging and not moved" in src, (
            "拖拽判定必须与位移联动。原实现只看 `was_dragging`，"
            "于是任何一次按下都会被判成拖拽"
        )

    def test_click_and_drag_are_mutually_exclusive(self, win, monkeypatch):
        """同一次交互不能既算点击又算拖拽。"""
        events = []
        monkeypatch.setattr(win.state_machine, "on_drag",
                            lambda: (events.append("drag"), "angry")[1])
        monkeypatch.setattr(win.state_machine, "on_click",
                            lambda: (events.append("click"), "pet")[1])

        _press(win, 10, 10)
        _release(win, 10, 10)

        assert events.count("click") + events.count("drag") <= 1, (
            f"一次交互触发了多个判定: {events}"
        )
