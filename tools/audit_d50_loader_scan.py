# -*- coding: utf-8 -*-
"""D50 定位：`插件不存在` 到底是谁打的。

现象：`FileService.initialize()` 里出现 `ERROR 插件不存在: watchfiles`，
但同一个插件用**新建的 PluginLoader** 手动 `scan()` 后 `load()` 明明是成功的。

候选解释：
  H1. `FileService` 拿到的 loader **没扫过盘**（`self.plugins` 是空的）
  H2. 单例被别处 `reset` / 重新构造，`plugins` 丢了
  H3. 名字对不上（大小写/空格）

判据：**把那个 loader 的 `plugins` 键集打印出来**，空字典就是 H1。
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.INFO, format='  [%(levelname)s] %(message)s')


def main() -> int:
    from core.plugin_loader import get_plugin_loader, PluginLoader

    print()
    print('=' * 74)
    print('  H1：get_plugin_loader() 返回的实例，plugins 里有什么')
    print('=' * 74)
    ldr = get_plugin_loader()
    print(f'  实例 id = {id(ldr)}')
    print(f'  len(ldr.plugins) = {len(ldr.plugins)}')
    print(f'  keys = {sorted(ldr.plugins.keys())}')
    print(f'  len(ldr.loaded_plugins) = {len(ldr.loaded_plugins)}')

    print()
    print('  → 这个实例 `load("watchfiles")` 的结果：')
    try:
        r = ldr.load('watchfiles', params={})
        print(f'    {r!r}')
    except Exception as e:
        print(f'    抛异常 {type(e).__name__}: {e}')

    print()
    print('=' * 74)
    print('  对照：新实例 + 手动 scan 之后')
    print('=' * 74)
    fresh = PluginLoader()
    print(f'  新实例 id = {id(fresh)}，scan 前 len = {len(fresh.plugins)}')
    fresh.scan()
    print(f'  scan 后 len = {len(fresh.plugins)}')
    print(f'  load("watchfiles") -> {fresh.load("watchfiles", params={})!r}')

    print()
    print('=' * 74)
    print('  FileService 走的那条路')
    print('=' * 74)
    from services.file_service import FileService
    s = FileService()
    print(f'  s.plugin_loader 实例 id = {id(s.plugin_loader)}')
    print(f'  与 get_plugin_loader() 同一个吗? {s.plugin_loader is ldr}')
    print(f'  s.plugin_loader.plugins 键数 = {len(s.plugin_loader.plugins)}')
    print()
    print('  → initialize() 里的真实日志：')
    s.initialize()

    print()
    print('=' * 74)
    print('  结论')
    print('=' * 74)
    if len(ldr.plugins) == 0:
        print('  H1 成立：`get_plugin_loader()` 给的实例**从未 scan 过**，')
        print('  所以 `load()` 里 `if plugin_name not in self.plugins` 立刻命中，')
        print('  打出「插件不存在: watchfiles」—— 而真相是**这个 loader 没扫盘**。')
        print()
        print('  ⚠️ 消息本身是错的：它说"插件不存在"，')
        print('     实际是"这个 loader 还没发现任何插件"。')
    else:
        print('  H1 不成立，需要继续查。')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
