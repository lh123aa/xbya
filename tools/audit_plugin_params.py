# -*- coding: utf-8 -*-
"""配置键 vs 插件构造函数的「接受能力」审计。

## 为什么做这个

`config.yaml` 的 `plugins.<能力>.params` 是**整体展开**传给插件构造函数的：

    instance = plugin_info.plugin_class(**params)      # core/plugin_loader.py:203

于是两种键会**静默出问题**：

  1. **写了但插件不接受** —— 若构造函数没有 `**kwargs` 兜底，直接 TypeError；
     若有 `**_extra` 兜底（本项目多数插件有），则**被悄悄吞掉**：
     用户改了 `model_size`，以为换了本地模型，实际什么都没发生。
  2. **插件支持但配置没写** —— 走函数默认值，用户不知道有这个开关。

两种都属于"配置看起来存在、实际不生效"（D22 的形态）。
本脚本把两者都列出来，让"哪些键真的有用"变成一张可核对的表。

用法：
    python tools/audit_plugin_params.py
"""
from __future__ import annotations

import importlib
import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def find_class(module_name: str, engine: str):
    """按 engine 名找到实现类（先试同名，再扫模块里所有类）。"""
    mod = importlib.import_module(module_name)
    # 1) 模块里唯一/主要的类
    classes = [o for _, o in vars(mod).items()
               if inspect.isclass(o) and o.__module__ == mod.__name__]
    if len(classes) == 1:
        return classes[0]
    # 2) 按名字猜（engine 的驼峰形式）
    want = engine.replace('_', '').lower()
    for c in classes:
        if want in c.__name__.lower():
            return c
    return classes[0] if classes else None


#: 能力 -> 插件模块目录（engine 名会拼在后面）
PLUGIN_DIRS = {
    'asr': 'plugins.asr',
    'tts': 'plugins.tts',
    'llm': 'plugins.llm',
    'embedding': 'plugins.embedding',
    'vector_db': 'plugins.vector_db',
    'voiceprint': 'plugins.voiceprint',
    'avatar': 'plugins.avatar',
}


def main() -> int:
    import yaml
    cfg = yaml.safe_load((ROOT / 'config.yaml').read_text(encoding='utf-8')) or {}
    plugins = cfg.get('plugins', {})

    print()
    print('=' * 82)
    print('  插件参数接线审计（配置写了的键 vs 构造函数接受的键）')
    print('=' * 82)

    problems = []
    for cap, spec in sorted(plugins.items()):
        if not isinstance(spec, dict):
            continue
        engine = spec.get('engine')
        params = spec.get('params') or {}
        moddir = PLUGIN_DIRS.get(cap)
        if not engine or not moddir:
            continue
        modname = f'{moddir}.{engine}.plugin'
        try:
            cls = find_class(modname, engine)
        except Exception as e:
            print(f'\n  ── {cap} ({engine}) ──')
            print(f'    ? 找不到插件类: {type(e).__name__}: {str(e)[:50]}')
            problems.append((cap, engine, '类不存在', list(params)))
            continue
        sig = inspect.signature(cls.__init__)
        accepted = [p for p in sig.parameters if p != 'self']
        has_kwargs = any(sig.parameters[p].kind == inspect.Parameter.VAR_KEYWORD
                         for p in sig.parameters)

        ignored = [k for k in params if k not in accepted]
        missing = [k for k in accepted if k not in params]

        print(f'\n  ── {cap} ({engine}) — {cls.__name__} ──')
        print(f'    配置提供 {len(params)} 个键，构造函数接受 {len(accepted)} 个'
              + ('（含 **kwargs 兜底）' if has_kwargs else ''))
        if ignored:
            mark = '⚠️ 被 **kwargs 静默吞掉' if has_kwargs else '❌ 会 TypeError'
            print(f'    {mark}: {ignored}')
            problems.append((cap, engine, 'ignored', ignored))
        if missing:
            print(f'    未配置（用默认值）: {missing}')
        if not ignored:
            print('    ✓ 配置键全部被接受')

    print()
    print('=' * 82)
    if problems:
        print(f'  ⚠️  {len(problems)} 个能力存在"配了但不生效"的键：')
        for cap, eng, kind, keys in problems:
            print(f'    {cap} ({eng}): {kind} -> {keys}')
        print()
        print('  含义：这些键**改了不会有任何效果**，而且不会报错 ——')
        print('        正是 D22「配置项看起来存在、实际不接线」的形态。')
        print('        处置：要么换到支持它的 engine，要么从配置里删掉，')
        print('              要么在插件里真正实现它。')
    else:
        print('  ✓ 所有配置键都被对应插件接受')
    print('=' * 82)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
