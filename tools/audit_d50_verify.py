# -*- coding: utf-8 -*-
"""D50 验证：懒扫盘是否生效（用全新 loader，不碰单例的脏状态）。"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.WARNING, format='  [%(levelname)s] %(message)s')


def main() -> int:
    from core.plugin_loader import PluginLoader

    print()
    print('=' * 74)
    print('  全新 PluginLoader（从未 scan），直接 load')
    print('=' * 74)
    pl = PluginLoader()
    print(f'  扫盘前: _scanned={pl._scanned}  plugins={len(pl.plugins)}')

    r = pl.load('watchfiles', params={'watch_dirs': ['~/Desktop'], 'recursive': True})
    print(f'  load("watchfiles") -> {type(r).__name__ if r else None}')
    print(f'  懒扫盘后: _scanned={pl._scanned}  plugins={len(pl.plugins)}')

    print()
    print('  → 判据：上面必须是 WatchfilesMonitor（不是 None），')
    print('     且 _scanned 由 False 变 True。')

    print()
    print('=' * 74)
    print('  对照：三个 Service 独立构造时，插件是否齐全')
    print('=' * 74)
    # 清一个全新单例环境
    import core.plugin_loader as m
    m._plugin_loader = None

    from services.file_service import FileService
    s = FileService()
    print(f'  FileService 拿到的 loader.plugins = {len(s.plugin_loader.plugins)}（构造时）')
    s.initialize()
    print(f'  initialize() 后 file_monitor = {type(s.file_monitor).__name__}')
    print(f'  initialize() 后 embedding    = {type(s.embedding).__name__}')
    print(f'  initialize() 后 vector_db    = {type(s.vector_db).__name__}')
    print()
    print('  → 判据：file_monitor 应为 WatchfilesMonitor（修前是 NullFileMonitor）')

    print()
    print('=' * 74)
    print('  D49 的状态（engine=null 后应仍说"已禁用"）')
    print('=' * 74)
    for k, v in sorted(s._degraded.items()):
        print(f'    {k}: {v}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
