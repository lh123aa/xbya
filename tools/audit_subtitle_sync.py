# -*- coding: utf-8 -*-
"""量化字幕与语音的不同步程度。

## 要回答的问题

`_speak_sentences` 里字幕停留时间是这样估的：

    show_dur = max(2500, len(text)/4.0*1000 + 500)

即"每字 250ms + 500ms 余量，最少 2.5 秒"。
但 TTS 实际合成出来的音频时长取决于语速、标点、音色 —— 与字数不是线性关系。

本脚本对若干真实句子**实测**合成音频时长，与公式估算值对比，
给出"字幕与语音差多少"的可判定数字。
"""
import asyncio
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yaml

C = yaml.safe_load(open('config.yaml', encoding='utf-8'))
VOICE = C['plugins']['tts']['params']['voice']
RATE = C['plugins']['tts']['params'].get('rate', 0)


def synth(text):
    import edge_tts

    async def go():
        c = edge_tts.Communicate(
            text, VOICE,
            **({'rate': f'{RATE:+d}%'} if RATE else {}))
        buf = io.BytesIO()
        async for ch in c.stream():
            if ch['type'] == 'audio':
                buf.write(ch['data'])
        return buf.getvalue()

    return asyncio.run(go())


def mp3_duration(data):
    """用 av 解出真实时长（秒）"""
    import av
    import tempfile
    with tempfile.NamedTemporaryFile(suffix='.mp3', delete=False) as f:
        f.write(data)
        p = f.name
    try:
        with av.open(p) as c:
            return float(c.duration) / 1_000_000
    finally:
        os.unlink(p)


SENTENCES = [
    '嗯嗯，我在呢，哥～',
    '哥，我还不能实时查到准确天气呢。',
    '你在哪个城市呀？告诉我，我帮你看看今天该怎么穿～',
    '好呀，哥，我都记住啦。以后你想聊什么、问什么，直接找我就好。',
    '啊？',
]

print('=' * 84)
print('  字幕停留时间 vs 语音真实时长')
print('=' * 84)
print(f"  {'句子':<34} {'字数':>4} {'公式估':>8} {'实测':>8} {'差':>8}  判定")
print('  ' + '-' * 80)

bad = []
for s in SENTENCES:
    n = len(s)
    est_ms = max(2500, int(n / 4.0 * 1000) + 500)
    audio = synth(s)
    real_s = mp3_duration(audio)
    real_ms = real_s * 1000
    diff = est_ms - real_ms
    # 判据：差超过 500ms 就会肉眼可见地不同步
    if abs(diff) > 500:
        verdict = f'✗ 差 {diff/1000:+.1f}s'
        bad.append((s, est_ms, real_ms, diff))
    else:
        verdict = '✓ 基本同步'
    display = s if len(s) <= 32 else s[:30] + '…'
    print(f'  {display:<34} {n:>4} {est_ms:>7}ms {real_ms:>7.0f}ms '
          f'{diff:>+7.0f}ms  {verdict}')

print()
print('  ' + '=' * 80)
if bad:
    print(f'  ▶ {len(bad)}/{len(SENTENCES)} 句不同步（阈值 ±500ms）')
    print()
    print('  结论：按字数估算是**错的方法** —— 字幕在语音播完后还挂着，')
    print('        或语音还没说完字幕就消失了。')
    print()
    print('  正确做法：字幕停留时间 = **音频的真实时长**（播放完再清），')
    print('            而不是按字数猜。')
else:
    print('  ▶ 全部同步（公式恰好可用）')
print('  ' + '=' * 80)
