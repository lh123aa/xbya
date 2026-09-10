"""
临时文件管理：统一临时目录 + 定时清理机制

职责：
- 所有运行期生成的文件（语音录音wav / ASR增强音频 / TTS音频mp3）统一写入
  temp.gettempdir()/xiaoyi_pet/，避免散落在系统临时目录
- TmpCleaner 每 10 分钟清理超龄文件（默认 3 分钟以上未修改——防误删正在使用）
- 只清理文件系统临时产物；**绝不触碰对话上下文等内存状态**
"""

import logging
import os
import shutil
import time
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

CLEAN_INTERVAL_MS = 600_000   # 清理周期：10 分钟
DUST_AGE_MIN = 3.0            # 文件年龄阈值：3 分钟未修改才删除（防正在使用）

_DIR = None


def get_tmp_dir() -> str:
    """统一的临时工作目录（懒创建，幂等）"""
    global _DIR
    if _DIR is None:
        _DIR = os.path.join(tempfile.gettempdir(), "xiaoyi_pet")
    os.makedirs(_DIR, exist_ok=True)
    return _DIR


def cleanup_old_files(max_age_min: float = DUST_AGE_MIN, directory: str = None) -> int:
    """删除目录下超过 max_age_min 未修改的文件，返回删除数量。

    Args:
        max_age_min: 文件年龄阈值（分钟），超过才删除
        directory: 清理目录（默认统一临时目录；测试可指定）
    """
    d = directory or get_tmp_dir()
    if not os.path.isdir(d):
        return 0
    now = time.time()
    removed = 0
    try:
        for p in Path(d).iterdir():
            try:
                if p.is_file():
                    if (now - p.stat().st_mtime) > max_age_min * 60:
                        p.unlink()
                        removed += 1
                elif p.is_dir():
                    mtimes = [f.stat().st_mtime for f in p.rglob("*") if f.is_file()]
                    newest = max(mtimes) if mtimes else now
                    if (now - newest) > max_age_min * 60:
                        shutil.rmtree(p, ignore_errors=True)
                        removed += 1
            except OSError:
                continue
    except OSError:
        logger.warning("清理临时目录失败", exc_info=True)
    if removed:
        logger.info("[临时清理] 已清理 %d 个文件 (目录=%s)", removed, d)
    return removed


class TmpCleaner:
    """QTimer 驱动的定时清理器（默认 10 分钟周期）。

    作用范围仅限于临时文件产物；对话上下文（_chat_history 等内存数据）
    与本机制无关，不会被清理。
    """

    def __init__(self, interval_ms: int = CLEAN_INTERVAL_MS):
        from PySide6.QtCore import QTimer
        self._timer = QTimer()
        self._timer.setInterval(interval_ms)
        self._timer.timeout.connect(lambda: cleanup_old_files())

    def start(self) -> None:
        """启动清理循环（需在 QApplication 主线程）"""
        self._timer.start()

    def stop(self) -> None:
        """停止清理循环（应用退出时调用）"""
        self._timer.stop()

    def cleanup_now(self) -> int:
        """立即执行一次清理（测试/手动触发）"""
        return cleanup_old_files()

    def is_active(self) -> bool:
        return self._timer.isActive()
