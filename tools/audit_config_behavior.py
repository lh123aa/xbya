# -*- coding: utf-8 -*-
"""配置接线审计（**行为判据**版）。

## 为什么不能只做文本匹配

第一版按"源码里有没有出现这个键名"判定，结果 20 个"疑似未接线"里
**大部分是误报**：

  · `plugins.*.params.*` —— 走 `plugin_class(**params)` 展开，
    键名以**关键字实参**形式出现，文本里根本没有 `"max_tokens"` 这个字符串；
  · 有的键由 `set()` 写入而非读出（如 `system.perf_evaluated`）。

文本判据会把"实现方式不同"误判成"没接线" —— 这比不查更糟：
**它会让人去修不存在的问题**。

## 本脚本的做法：真的把值改掉，看行为变不变

对每个待验键：
  1. 读出当前值
  2. 构造一份**改过该键**的配置
  3. 让消费者（插件/服务）加载它
  4. 比较"构造出的对象"在改前/改后是否不同

改前改后没差别 => 这个键影响不了任何东西 => **没接线**（或拼错了名字）。

用法：
    python tools/audit_config_wiring.py --behavior
"""
from __future__ import annotations

import argparse
import copy
import importlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def flatten(d, prefix=''):
    out = {}
    if isinstance(d, dict):
        for k, v in d.items():
            key = f'{prefix}.{k}' if prefix else str(k)
            if isinstance(v, dict):
                out.update(flatten(v, key))
            else:
                out[key] = v
    return out


def get_path(d, key, default=None):
    cur = d
    for part in key.split('.'):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


def set_path(d, key, val):
    parts = key.split('.')
    cur = d
    for p in parts[:-1]:
        cur = cur.setdefault(p, {})
    cur[parts[-1]] = val


#: 待验键 -> (模块, 工厂函数, 取值路径)
#: 工厂接收 params dict，返回一个可比较指纹的对象。
PROBES = {
    'plugins.asr.params': ('plugins.asr.faster_whisper.plugin',
                           'FasterWhisperASR'),
    'plugins.avatar.params': ('plugins.avatar.sadtalker.plugin',
                              'SadTalkerAvatar'),
}


def fingerprint(obj) -> str:
    """把一个对象的关键属性序列化成可比较的字符串。"""
    parts = []
    for a in sorted(vars(obj)) if hasattr(obj, '__dict__') else []:
        if a.startswith('_'):
            continue
        v = getattr(obj, a, None)
        if isinstance(v, (str, int, float, bool, type(None))):
            parts.append(f'{a}={v}')
    return '|'.join(parts)


def build(module_name, cls_name, params):
    mod = importlib.import_module(module_name)
    cls = getattr(mod, cls_name)
    return cls(**params)


def main() -> int:
    ap = argparse.ArgumentParser(description='配置接线审计（行为版）')
    ap.add_argument('--behavior', action='store_true')
    ap.add_argument('--static', action='store_true')
    args = ap.parse_args()

    import yaml
    cfg = yaml.safe_load((ROOT / 'config.yaml').read_text(encoding='utf-8')) or {}

    if not args.behavior:
        print('  用 --behavior 跑行为审计')
        return 0

    print()
    print('=' * 78)
    print('  配置接线审计（行为判据：改值 → 看对象是否变化）')
    print('=' * 78)

    results = []
    for prefix, (mod_name, cls_name) in PROBES.items():
        params = get_path(cfg, prefix, {}) or {}
        if not params:
            continue
        print(f'\n  ── {prefix} ──')
        for key in sorted(params):
            altered = copy.deepcopy(params)
            cur = altered[key]
            # 造一个**明显不同**的值（类型尽量保持）
            if isinstance(cur, bool):
                altered[key] = not cur
            elif isinstance(cur, int):
                altered[key] = cur + 7
            elif isinstance(cur, str):
                altered[key] = cur + '_X'
            else:
                altered[key] = 'X'
            try:
                a = fingerprint(build(mod_name, cls_name, dict(params)))
                b = fingerprint(build(mod_name, cls_name, altered))
            except Exception as e:
                print(f'    {key:<18} ? 构造失败: {type(e).__name__}: {str(e)[:40]}')
                results.append((key, None))
                continue
            if a != b:
                print(f'    {key:<18} ✓ 影响行为')
                results.append((key, True))
            else:
                print(f'    {key:<18} ✗ **改了没反应（没接线）**')
                print(f'        原: {a[:70]}')
                print(f'        改: {b[:70]}')
                results.append((key, False))

    ok = sum(1 for _, v in results if v is True)
    bad = [k for k, v in results if v is False]
    unk = [k for k, v in results if v is None]
    print()
    print('=' * 78)
    print(f'  接线正常 {ok} 个 / **没接线 {len(bad)} 个** / 无法判定 {len(unk)} 个')
    for k in bad:
        print(f'    ✗ {k}')
    print('=' * 78)
    return 0 if not bad else 1


if __name__ == '__main__':
    raise SystemExit(main())
