"""pytest 全局配置与夹具

职责：
1. 共享单个 QApplication（PySide6 不允许重复创建）
2. 会话结束时显式清理线程/Qt/pygame，避免进程退出崩溃（技术债 D7）
3. 提供通用的"等待条件成立"辅助

背景（D7）：
    全量测试运行时间歇性以退出码 0xC0000409 结束。根因是 Qt/PySide6 在 Windows
    上的 teardown 竞态 + 后台线程（TTS 播放/网络合成/Agent 执行器）在解释器
    关闭时仍在活动。测试结果本身始终正确，但退出码会污染 CI 信号。
    这里在 sessionfinish 阶段做一次有序收尾。
"""

import gc
import logging
import sys
import threading
import time
from pathlib import Path

import pytest

# 项目根目录加入 sys.path（各测试文件也各自做过，这里兜底保证 conftest 可用）
PROJECT_ROOT = Path(__file__).parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════
#  QApplication 单例
# ══════════════════════════════════════════════════════

@pytest.fixture(scope="session")
def qapp():
    """全测试会话共享的 QApplication

    部分测试文件自带 module/class 级 qapp 夹具；本夹具作为会话级兜底，
    保证任何测试路径下都有一个可用的 QApplication。
    """
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


# ══════════════════════════════════════════════════════
#  通用辅助夹具
# ══════════════════════════════════════════════════════

@pytest.fixture
def wait_until():
    """返回一个"等待条件成立"的函数

    用法：
        wait_until(lambda: len(results) > 0, timeout=5.0)
    """
    def _wait(predicate, timeout: float = 5.0, interval: float = 0.02) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if predicate():
                return True
            time.sleep(interval)
        return predicate()

    return _wait


# ══════════════════════════════════════════════════════
#  会话级收尾（技术债 D7）
# ══════════════════════════════════════════════════════

def pytest_sessionfinish(session, exitstatus):
    """会话结束时的有序清理

    顺序很关键：
    1. 给非 daemon 线程一点收尾时间（Agent 执行器 / TTS 播放线程）
    2. 停掉 pygame 混音器（释放音频设备句柄）
    3. 断开 Qt 事件过滤器（热键管理器注册的全局钩子）
    4. 关闭并销毁所有顶层窗口（QWebEngineView 尤其需要显式收尾）
    5. 回收 Qt 对象
    """
    # 1. 等待非 daemon 线程收尾（最多 2s）
    deadline = time.time() + 2.0
    while time.time() < deadline:
        alive = [
            t for t in threading.enumerate()
            if t is not threading.main_thread() and not t.daemon and t.is_alive()
        ]
        if not alive:
            break
        time.sleep(0.05)

    # 2. pygame
    try:
        import pygame
        if pygame.mixer.get_init():
            pygame.mixer.music.stop()
            pygame.mixer.stop()
            pygame.mixer.quit()
    except Exception:
        pass

    # 3. Qt 事件过滤器（热键）
    try:
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
        if app is not None:
            from services.hotkey_manager import HotkeyManager
            for obj in gc.get_objects():
                if isinstance(obj, HotkeyManager):
                    try:
                        obj.unregister()
                    except Exception:
                        pass
            app.processEvents()
    except Exception:
        pass

    # 4. 显式关闭并销毁顶层窗口
    #    QWebEngineView 在 Windows 上若由 GC 在解释器退出阶段回收，
    #    会在 C++ 侧已析构后回调，触发进程级崩溃（D7）
    try:
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
        if app is not None:
            for widget in list(app.topLevelWidgets()):
                try:
                    widget.close()
                    widget.deleteLater()
                except Exception:
                    pass
            app.processEvents()
            app.processEvents()   # 二次冲刷 deleteLater 队列
    except Exception:
        pass

    # 5. 回收并再给一轮事件处理机会
    gc.collect()
    try:
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
        if app is not None:
            app.processEvents()
    except Exception:
        pass
