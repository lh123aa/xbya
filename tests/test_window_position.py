"""窗口位置记忆：回归用例（D31）。

用真实 PetWindow 方法验证，不另写一份判据 —— 本项目吃过"复刻判据"的亏
（两份实现只改一份，测试全绿而产品是坏的）。
"""
import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication


@pytest.fixture
def win(qapp, monkeypatch):
    """造一个 pet 窗口，位置存档走隔离命名空间，绝不碰用户真实存档。"""
    from ui.pet_window import PetWindow
    w = PetWindow()
    monkeypatch.setattr(
        w, "_qsettings",
        lambda: QSettings("xbyaPet_test", "xbyaPet_test"),
    )
    s = w._qsettings()
    s.clear()
    s.sync()
    yield w
    w.close()
    w.deleteLater()


# ---------- 判据对称性 ----------

def test_usable_rejects_screen_outside_edges(win):
    """★ 主缺陷：旧判据只挡右/下边，左侧贴边存下的屏幕外坐标被判合法。

    为什么这条用例要**注入屏幕几何**而不是读真实屏幕：
    本机主屏在 `left=0`，此时"贴左边缘"的坐标 `left-w+8` 是负数，
    旧实现那句 `if x < 0: return False` 会**碰巧**挡住它 ——
    于是用例在旧实现下也是绿的（我实测踩到过这个假绿）。
    真正的单向性缺陷只在 **屏幕 left > 0**（左侧挂着副屏、主屏整体右移）
    时暴露：此时贴左边缘的 x 是**正数**，`x<0` 挡不住，
    `x >= right-10` 也不管左边 → 旧判据放行一个几乎全在屏幕外的位置。

    所以这里 monkeypatch 出一个"主屏从 x=1920 开始"的几何，
    让缺陷必然可见（与真实桌面布局无关，换机器也照样能抓）。
    """
    from PySide6.QtCore import QRect

    fake = QRect(1920, 0, 1920, 1032)   # 主屏整体右移，左侧有副屏
    win._get_screen_geometry = lambda: fake
    monkeypatch_screens = QRect(1920, 0, 1920, 1032)

    class _FakeScreen:
        def availableGeometry(self):
            return monkeypatch_screens

    import PySide6.QtGui as QtGui
    win_width, win_height = win.width(), win.height()

    # 直接验证判据本身（把屏幕列表换成假屏）
    original_screens = QtGui.QGuiApplication.screens
    QtGui.QGuiApplication.screens = staticmethod(lambda: [_FakeScreen()])
    try:
        # ★ 贴左边缘：窗口只剩 8px 露在屏幕内 —— x 是正数，旧实现会放行
        assert win._is_position_usable(1920 - win_width + 8, 300) is False, \
            "贴左边缘留下的屏幕外坐标必须判不可用（旧实现会误判为可用）"
        # 整个窗口在屏幕左侧之外
        assert win._is_position_usable(1920 - win_width - 50, 300) is False
        # 整个窗口在屏幕上方之外
        assert win._is_position_usable(2020, -win_height - 50) is False
        # 屏幕内正常位置仍须放行（反方向保护）
        assert win._is_position_usable(1920 + 100, 300) is True
    finally:
        QtGui.QGuiApplication.screens = original_screens


def test_usable_accepts_normal_positions(win):
    """屏幕内正常位置必须判可用（反方向保护，避免修过头）。"""
    from PySide6.QtGui import QGuiApplication
    g = QGuiApplication.primaryScreen().availableGeometry()
    assert win._is_position_usable(g.left(), g.top()) is True
    assert win._is_position_usable(g.center().x(), g.center().y()) is True
    assert win._is_position_usable(g.right() - win.width(), g.bottom() - win.height()) is True


def test_usable_requires_enough_visible_area(win):
    """只露出几个像素不算"可用"（用户抓不住）。"""
    from PySide6.QtGui import QGuiApplication
    g = QGuiApplication.primaryScreen().availableGeometry()
    # 只露出 5px 宽
    assert win._is_position_usable(g.right() - 5, g.top() + 100) is False


# ---------- 保存闸门 ----------

def test_save_skipped_before_restore(win):
    """★ 主缺陷：恢复完成前窗口是 (0,0)，绝不能把它写进存档。"""
    win._position_restored = False
    win.move(0, 0)
    win._save_position()
    s = win._qsettings()
    assert not s.contains("pos/x"), "恢复完成前不许写位置（会把左上角写死）"


def test_save_writes_after_restore(win):
    """恢复完成后正常保存（反方向保护）。"""
    from PySide6.QtGui import QGuiApplication
    g = QGuiApplication.primaryScreen().availableGeometry()
    win._position_restored = True
    x, y = g.center().x() - 100, g.center().y() - 100
    win.move(x, y)
    win._save_position()
    s = win._qsettings()
    assert int(s.value("pos/x")) == x
    assert int(s.value("pos/y")) == y


def test_save_uses_pre_snap_position_not_offscreen(win):
    """★ 主缺陷：贴边后窗口在屏幕外，存档必须是吸附前的可见位置。"""
    from PySide6.QtGui import QGuiApplication
    g = QGuiApplication.primaryScreen().availableGeometry()
    win._position_restored = True

    # 必须落在 SNAP_DIST 之内，_edge_snap() 才会真的吸附
    # （窗口右边缘距屏幕右边缘 = 4px < SNAP_DIST 12px）
    visible_x = g.right() - win.width() - 4
    visible_y = g.top() + 300
    win.move(visible_x, visible_y)
    win._edge_snap()                      # 触发贴边 → 窗口被移到屏幕外
    assert win._is_snapped_state is True, "位置须落在吸附阈值内才触发贴边"
    assert win.x() > g.right() - 20, "贴边后窗口应被移到屏幕外（只剩手柄）"
    win._save_position()

    s = win._qsettings()
    assert int(s.value("pos/x")) == visible_x, "贴边后存档必须是吸附前的可见位置"
    assert int(s.value("pos/y")) == visible_y


def test_save_skips_when_snapped_without_pre_pos(win):
    """没有吸附前位置可依据时，宁可跳过也不存屏幕外坐标。"""
    win._position_restored = True
    win._is_snapped_state = True
    win._pre_snap_pos = None
    win._save_position()
    s = win._qsettings()
    assert not s.contains("pos/x")


# ---------- 恢复闸门 ----------

def test_restore_rejects_offscreen_archive_and_uses_default(win):
    """★ 存档是屏幕外坐标时 → 走默认位置，而不是"恢复"到屏幕外。"""
    from PySide6.QtGui import QGuiApplication
    g = QGuiApplication.primaryScreen().availableGeometry()
    s = win._qsettings()
    s.setValue("pos/x", g.left() - win.width() - 400)
    s.setValue("pos/y", g.top() + 100)
    s.sync()

    win._position_restored = False
    win._restore_position()

    assert win._is_position_usable(win.x(), win.y()), \
        f"恢复后位置({win.x()},{win.y()})必须可用，不能停在屏幕外"


def test_restore_applies_valid_archive(win):
    """合法存档必须原样恢复（反方向保护）。"""
    from PySide6.QtGui import QGuiApplication
    g = QGuiApplication.primaryScreen().availableGeometry()
    x, y = g.center().x() - 200, g.center().y() - 150
    s = win._qsettings()
    s.setValue("pos/x", x)
    s.setValue("pos/y", y)
    s.sync()

    win._position_restored = False
    win._restore_position()
    assert (win.x(), win.y()) == (x, y)


# ---------- VRM 模式：用户报告的"跑到屏幕左上角" ----------

def test_vrm_load_pet_still_restores_position(win):
    """★ 主缺陷：VRM 模式下 `load_pet` 提前 return，位置恢复整段被跳过。

    于是窗口停在 `__init__` 的 (0,0) —— 正是用户描述的
    "有时重启位置就跑到屏幕左上角了"。
    """
    from PySide6.QtGui import QGuiApplication
    g = QGuiApplication.primaryScreen().availableGeometry()
    x, y = g.center().x() - 200, g.center().y() - 150

    s = win._qsettings()
    s.setValue("pos/x", x)
    s.setValue("pos/y", y)
    s.sync()

    win.render_mode = "vrm"
    win._position_restored = False
    win.load_pet("xbya")

    assert win._position_restored is True, "VRM 模式下也必须完成位置恢复"
    assert (win.x(), win.y()) == (x, y), \
        f"VRM 模式必须恢复到存档位置，实际停在 ({win.x()},{win.y()})"


def test_sprite_load_pet_restores_position(win):
    """sprite 模式同样恢复（反方向保护，确认改动没破坏原路径）。"""
    from PySide6.QtGui import QGuiApplication
    g = QGuiApplication.primaryScreen().availableGeometry()
    x, y = g.center().x() - 200, g.center().y() - 150

    s = win._qsettings()
    s.setValue("pos/x", x)
    s.setValue("pos/y", y)
    s.sync()

    win.render_mode = "sprite"
    win._position_restored = False
    win.load_pet("xbya")

    assert win._position_restored is True
    assert (win.x(), win.y()) == (x, y)
