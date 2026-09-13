# -*- coding: utf-8 -*-
"""受控端到端：已知语音 → ASR → LLM → TTS，逐段计时与判定。

与 `voice_scenarios_loopback.py` 的区别：
  · 那个测的是 **Agent 工具管线**（找文件/删除/打断…）
  · 本脚本测的是**闲聊链路**（ASR → LLM 回复 → TTS 合成），
    也就是用户日常"跟她说句话"走的那条路

不覆盖：麦克风采集、GUI、人耳听感（与回环工具同样的边界）。
"""
import asyncio
import io
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yaml
from pathlib import Path

CFG = yaml.safe_load(open('config.yaml', encoding='utf-8'))
P = CFG['plugins']['llm']['params']

SENTENCE = '欣雅你好，今天天气怎么样'


def synth(text, voice):
    import edge_tts

    async def go():
        c = edge_tts.Communicate(text, voice)
        buf = io.BytesIO()
        async for ch in c.stream():
            if ch['type'] == 'audio':
                buf.write(ch['data'])
        return buf.getvalue()

    return asyncio.run(go())


def to_wav(mp3_bytes, dst):
    """mp3 -> 16k 单声道 wav 文件。

    直接复用项目回环工具的 `_mp3_to_wav16k` —— 本仓库吃过"复刻判据"的亏
    （两份实现只改一份，测试全绿而产品是坏的），转换逻辑同理。
    """
    import tempfile

    from tools.voice_scenarios_loopback import Loopback

    with tempfile.NamedTemporaryFile(suffix='.mp3', delete=False) as f:
        f.write(mp3_bytes)
        tmp = f.name
    try:
        Loopback._mp3_to_wav16k(Path(tmp), Path(dst))
    finally:
        os.unlink(tmp)
    return Path(dst).read_bytes()


print('=' * 72)
print('  受控端到端：语音 → ASR → LLM → TTS')
print('=' * 72)

# ── 1. 造一段已知语音（当"人嘴"的替身）──
t0 = time.time()
mp3 = synth(SENTENCE, CFG['plugins']['tts']['params']['voice'])
wav = to_wav(mp3, os.path.join(os.environ.get('TEMP', '.'), '_e2e_probe.wav'))
print(f'  ① 测激   : "{SENTENCE}"')
print(f'             mp3 {len(mp3)}B -> wav {len(wav)}B  ({time.time()-t0:.2f}s)')

# ── 2. ASR ──
t0 = time.time()
from plugins.asr.groq_whisper.plugin import GroqWhisperASR

asr = GroqWhisperASR(
    api_key=P['api_key'],
    model='whisper-large-v3-turbo',
    language='zh',
    base_url='https://api.groq.com/openai/v1',
    enhance=True,
)
text = asr.transcribe_bytes(wav) if hasattr(asr, 'transcribe_bytes') else None
if text is None:
    # 退回文件接口
    import tempfile
    with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as f:
        f.write(wav)
        tmp = f.name
    try:
        text = asr.transcribe(tmp)
    finally:
        os.unlink(tmp)
asr_ms = (time.time() - t0) * 1000
print(f'  ② ASR    : "{text}"   ({asr_ms:.0f}ms)')
ok_asr = bool(text and '天气' in text)
print(f'             判定: {"✓ 识别正确" if ok_asr else "✗ 识别不符"}')

# ── 3. LLM ──
t0 = time.time()
from plugins.llm.openrouter.plugin import UniversalLLM

llm = UniversalLLM(**{k: v for k, v in P.items()
                      if k in ('api_key', 'base_url', 'model',
                               'fallback_api_key', 'fallback_base_url',
                               'fallback_model', 'system_prompt',
                               'reply_style', 'max_tokens')})
reply = llm.chat(text) if text else None
llm_s = time.time() - t0
print(f'  ③ LLM    : "{reply}"   ({llm_s:.2f}s)')
ok_llm = bool(reply)
print(f'             判定: {"✓ 有回复" if ok_llm else "✗ 无回复（见上方 ERROR）"}')
print(f'             配额状态: {llm.quota_stats()}')

# ── 4. TTS ──
if reply:
    t0 = time.time()
    audio = synth(reply, CFG['plugins']['tts']['params']['voice'])
    print(f'  ④ TTS    : {len(audio)} 字节   ({time.time()-t0:.2f}s)')
    ok_tts = len(audio) > 1000
    print(f'             判定: {"✓ 合成成功" if ok_tts else "✗ 空音频"}')
else:
    ok_tts = False
    print('  ④ TTS    : 跳过（无回复可播）')

print()
print('=' * 72)
allok = ok_asr and ok_llm and ok_tts
print(f'  结论：{"✓ 闲聊链路完整走通（ASR→LLM→TTS）" if allok else "✗ 链路有断点"}')
print(f'        ASR={"✓" if ok_asr else "✗"}  LLM={"✓" if ok_llm else "✗"}  TTS={"✓" if ok_tts else "✗"}')
print('=' * 72)
sys.exit(0 if allok else 1)
