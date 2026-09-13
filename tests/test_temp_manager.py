"""
临时文件管理测试：统一目录 + 超龄清理 + 新鲜保留
"""

import os
import sys
import time
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import pytest
from core.temp_manager import get_tmp_dir, cleanup_old_files, TmpCleaner


@pytest.fixture
def tmp_workdir(tmp_path):
    """独立测试目录（不触碰真实统一目录）"""
    return str(tmp_path)


def _make_file(path: str, age_min: float):
    """创建文件并设置 mtime（age_min 分钟前）"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x")
    past = time.time() - age_min * 60
    os.utime(p, (past, past))
    return p


class TestGetTmpDir:
    def test_creates_dir(self):
        d = get_tmp_dir()
        assert os.path.isdir(d)
        assert d.endswith("xbya_pet")


class TestCleanupOldFiles:
    def test_removes_old_files(self, tmp_workdir):
        """超龄文件被删除"""
        old = _make_file(os.path.join(tmp_workdir, "old.wav"), age_min=10)
        assert cleanup_old_files(max_age_min=3, directory=tmp_workdir) == 1
        assert not old.exists()

    def test_keeps_fresh_files(self, tmp_workdir):
        """新鲜文件（正在使用）保留"""
        fresh = _make_file(os.path.join(tmp_workdir, "fresh.mp3"), age_min=0.1)
        assert cleanup_old_files(max_age_min=3, directory=tmp_workdir) == 0
        assert fresh.exists()

    def test_mixed(self, tmp_workdir):
        """混合场景：只删超龄"""
        _make_file(os.path.join(tmp_workdir, "a.wav"), age_min=10)
        _make_file(os.path.join(tmp_workdir, "b.mp3"), age_min=1)
        removed = cleanup_old_files(max_age_min=3, directory=tmp_workdir)
        assert removed == 1
        assert not (Path(tmp_workdir) / "a.wav").exists()
        assert (Path(tmp_workdir) / "b.mp3").exists()

    def test_missing_dir(self, tmp_path):
        """目录不存在：0 删除不抛异常"""
        assert cleanup_old_files(directory=str(tmp_path / "nope")) == 0

    def test_old_subdir_removed(self, tmp_workdir):
        """子目录整体超龄则整目录删除"""
        f = _make_file(os.path.join(tmp_workdir, "sub", "x.wav"), age_min=10)
        assert cleanup_old_files(max_age_min=3, directory=tmp_workdir) >= 1
        assert not f.exists()


class TestTmpCleaner:
    @pytest.fixture(autouse=True)
    def qapp(self):
        """QTimer 依赖 QApplication"""
        from PySide6.QtWidgets import QApplication
        return QApplication.instance() or QApplication([])

    def test_start_stop(self):
        """QTimer 驱动：start 后激活，stop 后停（无需真实等待）"""
        cleaner = TmpCleaner(interval_ms=60_000)
        cleaner.start()
        assert cleaner.is_active() is True
        cleaner.stop()
        assert cleaner.is_active() is False

    def test_cleanup_now(self, tmp_workdir, monkeypatch):
        """手动触发清理"""
        _make_file(os.path.join(tmp_workdir, "old.wav"), age_min=10)
        monkeypatch.setattr("core.temp_manager.get_tmp_dir", lambda: tmp_workdir)
        assert TmpCleaner().cleanup_now() == 1
