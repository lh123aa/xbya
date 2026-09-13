# -*- coding: utf-8 -*-
"""D50 收口：`get_plugin_loader()` 的"扫盘时机"依赖调用顺序。

`core/app.py:152-153` 拿到单例后**立刻 scan()**，所以走 App 那条路时一切正常。
但 `FileService` / `VoiceService` / `AiService` 的 `__init__` 也各自
`get_plugin_loader()` —— **它们不 scan**，只依赖"别人先扫过"。

于是同一个 `FileService()` 有两副面孔：
  · 在 App 之后构造 → 插件齐全（撞巧）
  · 独立构造/在 App 之前构造 → `plugins` 是空字典 → 每次 load 都
    "插件不存在: X"

本探针把这两种顺序**都跑一遍**，用同一个类的两种表现证明它依赖顺序。
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.WARNING, format='  [%(levelname)s] %(message)s')


def show(tag: str) -> None:
    from core.plugin_loader import get_plugin_loader
    from services.file_service import FileService

    print(f'\n{"=" * 74}\n  {tag}\n{"=" * 74}')
    ldr = get_plugin_loader()
    print(f'  单例 plugins 键数 = {len(ldr.plugins)}')

    s = FileService()
    s.initialize()
    from interfaces.embedding import NullEmbedding
    fm = type(s.file_monitor).__name__ if s.file_monitor else None
    print(f'  file_monitor 实际类型 = {fm}')
    print(f'  → 文件监控可用吗: {"是" if fm == "WatchfilesMonitor" else "否（空对象）"}')


def main() -> int:
    # 甲：先 scan（模拟 App 的启动顺序）
    from core.plugin_loader import get_plugin_loader
    get_plugin_loader().scan()
    show('甲. 先 scan() 再构造 FileService（= App 的真实顺序）')

    # 乙：把单例重置回"没扫过"的状态，再构造
    #     这是 D50 的关键：单例的内存状态是可被上面那步"顺手"修好的
    import core.plugin_loader as pl_mod
    print(f'\n\n{"=" * 74}')
    print('  乙. 把单例重置成"从未 scan"（= 独立脚本 / 测试 / 别的入口）')
    print(f'{"=" * 74}')
    singleton = get_plugin_loader()
    saved = dict(singleton.plugins)
    singleton.plugins.clear()
    singleton.loaded_plugins.clear()
    print(f'  已清空单例：plugins 键数 = {len(singleton.plugins)}')

    from services.file_service import FileService
    s = FileService()
    s.initialize()
    fm = type(s.file_monitor).__name__ if s.file_monitor else None
    print(f'  file_monitor 实际类型 = {fm}')
    print(f'  → 文件监控可用吗: {"是" if fm == "WatchfilesMonitor" else "否（空对象）"}')

    # 还原，别污染后面的东西
    singleton.plugins.update(saved)

    print(f'\n{"=" * 74}\n  结论\n{"=" * 74}')
    print('  同一个 FileService()，两次结果**取决于"别人有没有先扫盘"**。')
    print('  这不是"插件坏了"，是**单例的初始化责任没归属**：')
    print('    · App 里由 core/app.py:153 代劳')
    print('    · 三个 Service 自己不扫，默认"别人扫过了"')
    print('  独立构造时三者的 load() 全部失败，而错误消息说的是')
    print('    「插件不存在: X」—— **它说的是错的**，真相是"这个 loader 没扫盘"。')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
