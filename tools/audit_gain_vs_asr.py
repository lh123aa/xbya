# -*- coding: utf-8 -*-
"""决定性实验：把**真实麦克风录音**放大后喂给 ASR，看能不能识别。

## 要回答的问题

上一轮结论是"麦克风满量程只有 1.66%，是硬件问题"。
但**幅度低本身不等于不能用** —— 16bit 有 96dB 动态范围，
只用了 1.66% 也还有约 9.4 bit 有效精度。

真正决定成败的是**信噪比（SNR）**：
  · SNR 够 → 放大后 ASR 照样能识别 → **可以靠软件增益解决**
  · SNR 不够 → 放大只是把噪声一起放大 → 只能改硬件/系统设置

所以不能靠推断，要**实测**：录一段真实说话，按不同倍数放大，
分别喂给 ASR，看识别结果。

## 判据

对每个增益倍数 g，记录：
  · 放大后峰值是否削顶（clip）
  · ASR 是否识别出内容、内容是否正确

若某个 g 能让 ASR 正确识别 → 说明软件增益可行，继续调大 `voice.mic_gain`。
若所有 g 都不行 → 确认是 SNR 问题。
"""
import io
import os
import sys
import time
import wave

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pyaudio

RATE, CHUNK = 16000, 1024
SECS = 4.0
SAY = '今天天气怎么样'

print('=' * 74)
print('  决定性实验：真实录音 × 不同增益 → ASR')
print('=' * 74)
print()
print(f'  请对着麦克风清晰地说：「{SAY}」')
print(f'  录音 {SECS:.0f} 秒 —— 现在开始！')
print()

p = pyaudio.PyAudio()
st = p.open(format=pyaudio.paInt16, channels=1, rate=RATE,
            input=True, input_device_index=1, frames_per_buffer=CHUNK)
frames = []
for i in range(int(RATE / CHUNK * SECS)):
    frames.append(st.read(CHUNK, exception_on_overflow=False))
    if i % 8 == 0:
        print(f'      录音中... {i*CHUNK/RATE:.1f}s', end='\r')
st.stop_stream()
st.close()
p.terminate()
print('      录音完成                    ')

pcm = np.frombuffer(b''.join(frames), dtype=np.int16)
peak = int(np.abs(pcm).max())
rms = float(np.sqrt(np.mean(pcm.astype(np.float64) ** 2)))
print(f'  原始：峰值={peak}  满量程={peak/32767*100:.2f}%  RMS={rms:.1f}')

# 安静段（前 0.5 秒通常无人声）用于估底噪
head = pcm[:int(RATE * 0.5)]
head_rms = float(np.sqrt(np.mean(head.astype(np.float64) ** 2)))
snr = 20 * np.log10(max(rms, 1e-9) / max(head_rms, 1e-9)) if head_rms > 0 else 0
print(f'  估算 SNR ≈ {snr:.1f} dB（前 0.5s 当作底噪）')
print()

import yaml
C = yaml.safe_load(open('config.yaml', encoding='utf-8'))
LLM = C['plugins']['llm']['params']
from plugins.asr.groq_whisper.plugin import GroqWhisperASR

asr = GroqWhisperASR(api_key=LLM['api_key'], model='whisper-large-v3-turbo',
                     language='zh', base_url='https://api.groq.com/openai/v1',
                     enhance=True)

TMP = os.path.join(os.environ.get('TEMP', '.'), '_gain_probe.wav')


def write_wav(path, data):
    with wave.open(path, 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(data.tobytes())


print('  ' + '=' * 70)
print(f"  {'增益':>5} {'放大后峰值':>10} {'削顶?':>6}  ASR 识别结果")
print('  ' + '-' * 70)
results = {}
for g in (1, 2, 5, 10, 20, 40):
    amp = np.clip(pcm.astype(np.float64) * g, -32768, 32767).astype(np.int16)
    p2 = int(np.abs(amp).max())
    clipped = p2 >= 32767
    write_wav(TMP, amp)
    try:
        txt = asr.transcribe(TMP)
    except Exception as e:
        txt = f'<异常 {type(e).__name__}>'
    results[g] = txt
    ok = txt and SAY[:3] in (txt or '')
    print(f"  {g:>5}x {p2:>10} {'是' if clipped else '否':>6}  "
          f"{txt!r}{'  ✓ 正确' if ok else ''}")

os.unlink(TMP)

print()
print('  ' + '=' * 70)
good = [g for g, t in results.items() if t and SAY[:3] in t]
if good:
    print(f'  ▶ 增益 {min(good)}x 起 ASR 能正确识别 → **软件增益可行**')
    print(f'    建议把 voice.mic_gain 设为 {min(good)}（在 config.yaml 里）')
else:
    print('  ▶ **任何增益都无法识别** → SNR 不足，软件增益解决不了')
    print('    需要改善信噪比：麦克风靠近嘴 / 关闭降噪 / 换设备')
print('  ' + '=' * 70)
