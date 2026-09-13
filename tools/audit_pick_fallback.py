# -*- coding: utf-8 -*-
"""挑一个**快且不烧思维链**的备用模型。

问题：当前备用 `nex-agi/nex-n2.5-pro:free` 是推理型，实测每次先吐
500~800 字思维链才给 6~31 字正文，耗时 4.9~13.0 秒。
用户感知就是"听到半天不回复"。

判据（缺一不可）：
  1. 免费可用（账户已失去通用 free 额度，只有部分模型仍免费）
  2. **不产生 reasoning**（否则思维链吃时间又吃 max_tokens）
  3. 首字延迟低（用户感知的是首字，不是完整时长）
  4. 能说中文
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
KEY = P['fallback_api_key']

CANDIDATES = [
    'nex-agi/nex-n2.5-mini:free',
    'inclusionai/ling-3.0-flash-sante:free',
    'inclusionai/ling-3.0-flash-fin:free',
    'dots-studio/dots-3-note-preview:free',
    'nex-agi/nex-n2.5-pro:free',          # 当前配置（对照）
]


def probe(model):
    body = json.dumps({
        'model': model,
        'messages': [{'role': 'system', 'content': '你是欣雅，用中文简短回答，一句话。'},
                     {'role': 'user', 'content': '今天天气怎么样'}],
        'max_tokens': 200,
    }).encode()
    req = urllib.request.Request(
        'https://openrouter.ai/api/v1/chat/completions', data=body,
        headers={'Authorization': f'Bearer {KEY}',
                 'Content-Type': 'application/json'})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            d = json.loads(r.read())
    except urllib.error.HTTPError as e:
        return {'err': f'HTTP {e.code}: {e.read().decode()[:70]}'}
    except Exception as e:
        return {'err': f'{type(e).__name__}'}
    el = time.time() - t0
    m = d['choices'][0]['message']
    return {
        'sec': el,
        'content': (m.get('content') or '').strip(),
        'reasoning': len(m.get('reasoning') or ''),
    }


print('=' * 88)
print('  备用模型选型：要"快且不烧思维链"')
print('=' * 88)
print(f"  {'模型':<44} {'耗时':>7} {'正文':>6} {'思维链':>7}  判定")
print('  ' + '-' * 84)

best = []
for m in CANDIDATES:
    r = probe(m)
    if 'err' in r:
        print(f'  {m:<44} {"—":>7} {"—":>6} {"—":>7}  ✗ {r["err"][:28]}')
        continue
    ok_fast = r['sec'] < 6.0
    ok_think = r['reasoning'] == 0
    ok_text = len(r['content']) >= 4
    verdict = ('✓ 推荐' if (ok_fast and ok_think and ok_text) else
               ('~ 可用但偏慢' if ok_text else '✗ 无正文'))
    if ok_think and ok_text and r['reasoning'] == 0:
        best.append((r['sec'], m))
    print(f"  {m:<44} {r['sec']:>6.2f}s {len(r['content']):>5}字 "
          f"{r['reasoning']:>6}字  {verdict}")
    if r['content']:
        print(f"      └ 回复: {r['content'][:56]}")

print()
if best:
    best.sort()
    print(f'  ▶ 最快且无思维链: {best[0][1]}  ({best[0][0]:.2f}s)')
else:
    print('  ▶ 没有候选同时满足"无思维链 + 有正文"')
print('=' * 88)
