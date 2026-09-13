# -*- coding: utf-8 -*-
"""配置接线审计 —— 找出「写了但没人读」的配置项。

## 为什么这是完整性的第一问

本项目有明确前科（D22 / D23）：`ui.render_mode` 与 `ui.pet_sprite`
**存在于配置里、看起来能用，实际从未被读取**；用户改了没反应，
也没有任何报错。这类缺陷的特征是"配置项看起来存在、实际不接线"。

判据很直接：**配置里的每个叶子键，都应该在代码里被 `get("<key>")` 读过**。
读不到 = 要么没接线，要么拼错了键名（后者更隐蔽：改的人以为生效了）。

用法：
    python tools/audit_config_wiring.py            # 全部
    python tools/audit_config_wiring.py --unused   # 只看没被读取的
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: 扫描源码的范围（配置的消费者都在这些目录）
SRC_DIRS = ['core', 'ui', 'services', 'plugins', 'agent', 'animation', 'tools']

#: 允许"不需要被读取"的键（有正当理由的），逐条写明理由。
ALLOW_UNREAD = {
    # 纯文档/注释性质的键
    'system.perf_evaluated',      # 由代码 set() 写入，不是读的
    # 示例/模板文件里的键
    'path_whitelist',             # 由 safety 插件用自己的方式读，见下
}


def flatten(d, prefix=''):
    """把嵌套 dict 展平成 `a.b.c` -> 值"""
    out = {}
    if isinstance(d, dict):
        for k, v in d.items():
            key = f'{prefix}.{k}' if prefix else str(k)
            if isinstance(v, dict):
                out.update(flatten(v, key))
            else:
                out[key] = v
    return out


def collect_source_text() -> str:
    parts = []
    for d in SRC_DIRS:
        p = ROOT / d
        if not p.is_dir():
            continue
        for f in p.rglob('*.py'):
            if '__pycache__' in str(f):
                continue
            try:
                parts.append(f.read_text(encoding='utf-8', errors='replace'))
            except Exception:
                pass
    return '\n'.join(parts)


def main() -> int:
    ap = argparse.ArgumentParser(description='配置接线审计')
    ap.add_argument('--unused', action='store_true', help='只看未被读取的键')
    args = ap.parse_args()

    import yaml
    cfg_path = ROOT / 'config.yaml'
    if not cfg_path.is_file():
        print('  找不到 config.yaml')
        return 1
    cfg = yaml.safe_load(cfg_path.read_text(encoding='utf-8')) or {}
    flat = flatten(cfg)
    src = collect_source_text()

    print()
    print('=' * 78)
    print(f'  配置接线审计（config.yaml 共 {len(flat)} 个叶子键）')
    print('=' * 78)

    unread, read_keys = [], []
    for key, val in sorted(flat.items()):
        # 判据：源码里出现过这个键的**完整路径**（用引号包起来，
        # 避免 `a.b` 被 `x.a.b` 之类的片段误命中）
        # 也接受只写末段的形式（如 get("listening")），但要更谨慎
        leaf = key.split('.')[-1]
        hit_full = f'"{key}"' in src or f"'{key}'" in src
        # 末段匹配：要求 get( 或 [ 里出现 leaf
        hit_leaf = bool(re.search(
            rf'(get\(|\[)\s*[\'"]{re.escape(leaf)}[\'"]', src))
        if hit_full or hit_leaf:
            read_keys.append(key)
        else:
            if key in ALLOW_UNREAD:
                continue
            unread.append((key, val))

    print()
    print(f'  ✓ 被读取或有合理豁免: {len(read_keys)} 个')
    print(f'  ? 疑似**没接线**: {len(unread)} 个')
    if unread:
        print()
        print('  ' + '-' * 74)
        print(f"  {'配置键':<44} {'值':<24}")
        print('  ' + '-' * 74)
        for k, v in unread:
            vs = str(v)
            if len(vs) > 22:
                vs = vs[:20] + '…'
            print(f'  {k:<44} {vs:<24}')
    print()
    print('=' * 78)
    print('  说明：判据是"源码里是否出现该键名"。')
    print('        · 命中 = 有人读它（可能仍有条件分支绕过，需人工看）')
    print('        · 未命中 = **极可能没接线** —— 改了不会有任何效果，')
    print('          而且不会报错（这正是 D22 的形态）')
    print()
    print('  ⚠️ 本脚本有一个**已知的假阳性来源**，别把它的输出当结论：')
    print('     `plugins.<能力>.params` 下的键**不是**按名字被读取的 ——')
    print('     `core/plugin_loader.py:203` 是 `plugin_class(**params)`，')
    print('     整个参数字典展开传进构造函数，键名**只以形参形式存在**，')
    print('     源码里根本不会出现 `get("model")` 这样的调用。')
    print('     所以那一族键会**成批**落进"疑似没接线"，那是测量方法的缺陷，')
    print('     不是产品缺陷。')
    print()
    print('     要判这一族，用**行为探针**：')
    print('       python tools/audit_plugin_params.py   # 配置键 vs 构造函数签名')
    print('     它的判据是"这个键会不会被构造函数接受"，不是"名字有没有出现"。')
    print('=' * 78)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
