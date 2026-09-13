# -*- coding: utf-8 -*-
"""审计：`agent.plugins` 清单里每个 module 是否真的可导入。

## 为什么查这个

`config.yaml` 的 `agent.plugins` 是**配置驱动的插件装配清单**（偿还 D12 的产物）：
换实现 = 改配置。清单里写错一个模块路径，后果与 D49/D50 同族 ——
`build_agent_stack` 捕获异常后降级为"纯对话模式"，**Agent 层整体静默失效**。

判据不是"字符串看着对"，而是 `importlib.util.find_spec` 真的能找到它。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402


def main() -> int:
    cfg = yaml.safe_load((ROOT / 'config.yaml').read_text(encoding='utf-8')) or {}
    manifest = ((cfg.get('agent') or {}).get('plugins')) or []

    print()
    print('=' * 76)
    print(f'  agent.plugins 清单（{len(manifest)} 项）')
    print('=' * 76)

    ids = set()
    bad = []
    for item in manifest:
        pid = item.get('id')
        mod = item.get('module')
        deps = item.get('depends_on') or []
        ids.add(pid)

        # 1) module 能不能找到
        try:
            spec = importlib.util.find_spec(mod) if mod else None
            ok = spec is not None
            detail = '' if ok else 'find_spec 返回 None'
        except Exception as e:
            ok = False
            detail = f'{type(e).__name__}: {e}'

        # 2) 依赖是否在清单里、且**排在前面**（顺序加载时这是硬要求）
        missing_deps = [d for d in deps if d not in {i.get('id') for i in manifest}]
        print(f'  {pid:26s} deps={str(deps):46s} '
              f'{"✓" if ok else "❌"} {mod or "（缺 module）"}')
        if not ok:
            bad.append((pid, mod, detail))
            print(f'      └─ {detail}')
        if missing_deps:
            bad.append((pid, mod, f'depends_on 里有清单外的 id: {missing_deps}'))
            print(f'      └─ depends_on 引用了不存在的 id: {missing_deps}')

    print()
    print('=' * 76)
    print('  顺序检查：依赖必须排在被依赖者之后')
    print('=' * 76)
    seen = []
    order_bad = []
    for item in manifest:
        pid = item.get('id')
        for d in (item.get('depends_on') or []):
            if d not in seen:
                order_bad.append((pid, d))
                print(f'  ❌ {pid} 依赖 {d}，但 {d} 还没加载（顺序错了）')
        seen.append(pid)
    if not order_bad:
        print('  ✓ 全部依赖都排在前面')

    print()
    print('=' * 76)
    if bad or order_bad:
        print(f'  ❌ {len(bad)} 个模块/依赖问题，{len(order_bad)} 个顺序问题')
        rc = 1
    else:
        print(f'  ✓ {len(manifest)} 个插件模块路径与依赖全部成立')
        rc = 0
    print('=' * 76)
    return rc


if __name__ == '__main__':
    raise SystemExit(main())
