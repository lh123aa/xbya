# -*- coding: utf-8 -*-
"""录一段并**保存下来**，直接看波形里到底有没有人声。

前一个实验的结论是"任何增益都无法识别"。但那只说明 ASR 没认出来，
**不排除录音里根本没有我的说话声**。本脚本把波形切段打印，
让"有没有人声、人声在第几秒"变成看得见的东西。
"""
import os
import sys
import time
import wave

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pyaudio

RATE, CHUNK = 16000, 1024
SECS = 7.0
OUT = 'logs/_probe_recording.wav'

os.makedirs('logs', exist_ok=True)

print('=' * 70)
print('  录音 + 波形分析')
print('=' * 70)
print()
print('  请按这个节奏说：')
print('    第 0-2 秒：**不要说话**（让我测底噪）')
print('    第 2-5 秒：大声说「今天天气怎么样」')
print('    第 5-7 秒：不要说话')
print()

p = pyaudio.PyAudio()
st = p.open(format=pyaudio.paInt16, channels=1, rate=RATE,
            input=True, input_device_index=1, frames_per_buffer=CHUNK)
frames = []
t0 = time.time()
while time.time() - t0 < SECS:
    frames.append(st.read(CHUNK, exception_on_overflow=False))
    el = time.time() - t0
    phase = '安静' if el < 2 else ('说话 <<<' if el < 5 else '安静')
    print(f'      {el:4.1f}s  {phase}    ', end='\r')
st.stop_stream()
st.close()
p.terminate()
print('      录音完成                          ')

pcm = np.frombuffer(b''.join(frames), dtype=np.int16)
with wave.open(OUT, 'wb') as w:
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(RATE)
    w.writeframes(pcm.tobytes())

print(f'  已保存: {OUT}')
print(f'  总长 {len(pcm)/RATE:.2f}s  峰值 {int(np.abs(pcm).max())}  '
      f'满量程 {np.abs(pcm).max()/32767*100:.2f}%')
print()

# 逐 0.5 秒打印 RMS，看有没有起伏
win = int(RATE * 0.5)
print('  ' + '=' * 62)
print(f"  {'时间':>10} {'RMS':>9} {'峰值':>9}  说明")
print('  ' + '-' * 62)
seg_rms = []
for i in range(0, len(pcm) - win + 1, win):
    seg = pcm[i:i + win]
    r = float(np.sqrt(np.mean(seg.astype(np.float64) ** 2)))
    pk = int(np.abs(seg).max())
    seg_rms.append(r)
    t0s, t1s = i / RATE, (i + win) / RATE
    tag = ''
    if t1s <= 2.0:
        tag = '（应安静）'
    elif t0s >= 2.0 and t1s <= 5.0:
        tag = '（应说话）'
    else:
        tag = '（应安静）'
    print(f'  {t0s:4.1f}-{t1s:4.1f}s {r:>9.1f} {pk:>9}  {tag}')

if seg_rms:
    quiet = [r for r in seg_rms if r > 0]
    print()
    print(f'  RMS 范围: {min(seg_rms):.1f} ~ {max(seg_rms):.1f}')
    ratio = max(seg_rms) / max(min(seg_rms), 1.0)
    print(f'  峰谷比  : {ratio:.2f}x')
    print()
    if ratio < 1.5:
        print('  ✗ 全程电平几乎没有起伏 —— 录音里**听不出有说话**')
    else:
        print(f'  ✓ 有明显起伏（{ratio:.2f}x），说明录到了声音')
print('  ' + '=' * 62)
