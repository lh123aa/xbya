# -*- coding: utf-8 -*-
"""量化「听到半天不回复」—— 分主/备两条路测 LLM 延迟。

背景：受控端到端实测 LLM 单次 **13.24 秒**，而 ASR 只要 0.7s、TTS 只要 1.8s。
Groq 主模型配额耗尽（429）后**每次都走备用** OpenRouter，而备用明显更慢。
用户感知正是"听到半天不回复"。

本脚本分别测主/备两条路的"首字节 + 完整回复"耗时，给出可比数字。
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yaml

CFG = yaml.safe_load(open('config.yaml', encoding='utf-8'))
P = CFG['plugins']['llm']['params']
MSGS = [
    {'role': 'system', 'content': P.get('system_prompt', '你是欣雅。')[:400]},
    {'role': 'user', 'content': '今天天气怎么样'},
]


def probe(name, base, key, model):
    """返回 (首字节秒, 完整秒, 结果或错误)"""
    body = json.dumps({'model': model, 'messages': MSGS,
                       'max_tokens': 200, 'stream': True}).encode()
    req = urllib.request.Request(
        base.rstrip('/') + '/chat/completions', data=body,
        headers={'Authorization': f'Bearer {key}',
                 'Content-Type': 'application/json'})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            first = None
            chars = 0
            for raw in r:
                line = raw.decode('utf-8', 'replace').strip()
                if not line.startswith('data:'):
                    continue
                payload = line[5:].strip()
                if payload == '[DONE]':
                    break
                try:
                    obj = json.loads(payload)
                except Exception:
                    continue
                d = (obj.get('choices') or [{}])[0].get('delta', {}) or {}
                c = d.get('content') or ''
                if c:
                    if first is None:
                        first = time.time() - t0
                    chars += len(c)
            total = time.time() - t0
            return (first, total, f'{chars} 字')
    except urllib.error.HTTPError as e:
        msg = e.read().decode()[:110].replace('\n', ' ')
        return (None, time.time() - t0, f'HTTP {e.code}: {msg}')
    except Exception as e:
        return (None, time.time() - t0, f'{type(e).__name__}: {e}')


print('=' * 74)
print('  LLM 延迟分解：主提供商 vs 备用提供商')
print('=' * 74)

rows = [
    ('主 Groq', P['base_url'], P['api_key'], P['model']),
    ('备 OpenRouter', P['fallback_base_url'], P['fallback_api_key'],
     P['fallback_model']),
]
for name, base, key, model in rows:
    first, total, res = probe(name, base, key, model)
    fs = f'{first:.2f}s' if first is not None else '  —  '
    print(f'  {name:<14} 首字 {fs:>9}  完整 {total:>7.2f}s   {res[:46]}')
    print(f'                 model={model}')

print()
print('  判据：用户感知的"半天不回复" = 首字延迟（不是完整延迟）')
print('        >3 秒就会明显觉得"她卡住了"')
print('=' * 74)
