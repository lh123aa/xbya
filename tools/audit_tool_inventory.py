# -*- coding: utf-8 -*-
"""工具清单的唯一事实来源：真实注册表里有几个、分别叫什么。

AGENTS.md 两处写了不同数字（§三 "21 个"、§十一 "18 个"），先数清楚。
判据用**运行时注册表**，不是数类名 —— 因为"定义了但没注册"也是一种缺陷。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main() -> int:
    # 1) 数类定义
    import importlib
    import inspect

    from agent.tools.base import BaseTool
    per_file = {}
    for name in ['file_tools', 'system_tools', 'browser_tools',
                 'productivity_tools', 'memory_tools']:
        mod = importlib.import_module(f'agent.tools.{name}')
        names = sorted(
            o.__name__ for _, o in vars(mod).items()
            if inspect.isclass(o) and issubclass(o, BaseTool)
            and o is not BaseTool
            and getattr(o, '__module__', '') == f'agent.tools.{name}'
            and not o.__name__.startswith('_')
        )
        per_file[name] = names

    print()
    print('=' * 74)
    print('  一、类定义（排除 BaseTool 与下划线开头的中间基类）')
    print('=' * 74)
    total = 0
    for f, names in per_file.items():
        total += len(names)
        print(f'  {f:22s} {len(names)} 个: {names}')
    print(f'\n  合计: {total} 个')

    # 2) 真实注册表
    print()
    print('=' * 74)
    print('  二、运行时注册表（这才是"能被路由调用"的集合）')
    print('=' * 74)
    try:
        from agent.tools.registry import ToolRegistry
        reg = ToolRegistry()
        # 试着用项目自己的装配路径注册
        try:
            from agent.plugins import register_tools
            register_tools(reg)
        except Exception as e:
            print(f'  register_tools 失败: {type(e).__name__}: {e}')
        names = sorted(reg.tools.keys()) if hasattr(reg, 'tools') else []
        print(f'  注册表里 {len(names)} 个: {names}')
        if hasattr(reg, 'to_llm_schemas'):
            schemas = reg.to_llm_schemas()
            print(f'  能转成 LLM schema 的 {len(schemas)} 个')
    except Exception as e:
        print(f'  注册表探测失败: {type(e).__name__}: {e}')

    print()
    print('=' * 74)
    print('  三、文档里的数字')
    print('=' * 74)
    agents = (ROOT / 'AGENTS.md').read_text(encoding='utf-8')
    import re
    for m in re.finditer(r'[^\n]{0,40}(\d+)\s*个工具[^\n]{0,40}', agents):
        print(f'   AGENTS.md: …{m.group(0).strip()}…')
    for m in re.finditer(r'工具[^\n]{0,20}共\s*(\d+)\s*个', agents):
        print(f'   AGENTS.md: {m.group(0)}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
