# -*- coding: utf-8 -*-
"""D49 现场核对：配置里的引擎名，加载器**是否真的认识**。

判据不是"目录存在"，而是 `PluginLoader.scan()` 的输出里有没有这个名字 ——
这正是 `embed_anything` / `leann` 藏了这么久的原因：目录在，`plugin.py` 不在。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402
from core.plugin_loader import PluginLoader  # noqa: E402


def main() -> int:
    cfg = yaml.safe_load((ROOT / 'config.yaml').read_text(encoding='utf-8')) or {}
    plugins = cfg.get('plugins', {})

    pl = PluginLoader()
    pl.scan()
    known = set(pl.plugins.keys())

    print()
    print('=' * 74)
    print('  D49 核对：config.yaml 的每个 plugins.<能力>.engine 是否真存在')
    print('=' * 74)
    print(f'  加载器认识 {len(known)} 个: {sorted(known)}')
    print()

    bad = []
    for cap in sorted(plugins):
        spec = plugins[cap]
        if not isinstance(spec, dict):
            continue
        eng = spec.get('engine')
        if eng is None or eng == 'null':
            print(f'  {cap:11s} engine=None    → 该能力已禁用（显式声明，不是说谎）')
            continue
        ok = eng in known
        print(f'  {cap:11s} engine={eng!r:22s} → {"✓ 存在" if ok else "❌ 不存在"}')
        if not ok:
            bad.append((cap, eng))

    print()
    print('=' * 74)
    if bad:
        print(f'  ❌ 仍有 {len(bad)} 个引擎名指向不存在的插件：{bad}')
        print('     这些键**改了不会有任何效果**，且启动时只有一行 WARNING。')
        rc = 1
    else:
        print('  ✓ 所有 engine 要么真实存在，要么显式 null —— 没有"配置说谎"')
        rc = 0
    print('=' * 74)
    return rc


if __name__ == '__main__':
    raise SystemExit(main())
