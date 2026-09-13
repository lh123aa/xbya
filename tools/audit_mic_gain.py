# -*- coding: utf-8 -*-
"""判定"麦克风增益过低"还是"环境真的很安静"。

## 为什么要做这个区分

实测 idx=1：你说话时原始峰值只有 **683 / 32767 = 2.1% 满量程**，
而程序阈值要 ~200+，所以永远触发不了。

但这有两种完全不同的成因，处理方式完全相反：
  A. **增益过低**（系统/驱动把输入音量压到很低）→ 改系统设置即可
  B. **信号本身很小**（麦克风离得远 / 被静音 / 房间极静）→ 改位置

## 判据

拿一个**已知很响**的参照源比：让系统播放一段音乐（扬声器出声），
同时用麦克风录。若扬声器的声音在麦克风里也很小 → 增益问题；
若扬声器声很大、只有人声小 → 是距离/房间问题。

另外：如果**记录到的数据几乎不动**（不管多响），那是死流。
"""
import time
import struct
import audioop

import pyaudio

RATE, CHUNK = 16000, 1024


def measure(p, idx, secs):
    try:
        st = p.open(format=pyaudio.paInt16, channels=1, rate=RATE,
                    input=True, input_device_index=idx,
                    frames_per_buffer=CHUNK)
    except Exception as e:
        return {'err': str(e)[:40]}
    try:
        n = int(RATE / CHUNK * secs)
        blocks, rms, peaks = [], [], []
        for _ in range(n):
            b = st.read(CHUNK, exception_on_overflow=False)
            blocks.append(b)
            rms.append(audioop.rms(b, 2))
            v = struct.unpack(f'<{len(b)//2}h', b)
            peaks.append(max(abs(x) for x in v))
        return {
            'rms_avg': sum(rms) / len(rms),
            'rms_max': max(rms),
            'peak_max': max(peaks),
            'uniq': len(set(blocks)),
            'n': n,
        }
    finally:
        try:
            st.stop_stream()
            st.close()
        except Exception:
            pass


def report(tag, m):
    if not m or 'err' in m:
        print(f'  {tag:<22} 打不开（{(m or {}).get("err", "?")}）')
        return
    pct = m['peak_max'] / 32767 * 100
    flat = m['uniq'] < m['n'] * 0.5
    print(f'  {tag:<22} RMS均={m["rms_avg"]:>8.1f}  '
          f'峰值={m["peak_max"]:>6}  '
          f'满量程={pct:>5.1f}%  '
          f'变化块={m["uniq"]:>3}/{m["n"]:<3}'
          + ('  ← 数据几乎不动' if flat else ''))


p = pyaudio.PyAudio()

print()
print('  ' + '=' * 84)
print('   第 1 步：请【保持安静】3 秒（测本底）')
print('  ' + '=' * 84)
for t in (3, 2, 1):
    print(f'      {t}...', end='\r')
    time.sleep(1)
quiet = {i: measure(p, i, 2.5) for i in (0, 1)}

print('  ==========================================================')
print('   第 2 步：请【大声持续说话】4 秒 —— 现在开始！')
print('  ==========================================================')
for t in (4, 3, 2, 1):
    print(f'      {t}...', end='\r')
    time.sleep(1)
talk = {i: measure(p, i, 3.0) for i in (0, 1)}

print()
print('  ' + '=' * 84)
print(f'  {"设备/阶段":<22} {"RMS均":>8}  {"峰值":>6}  {"满量程":>7}  {"变化块":>8}')
print('  ' + '-' * 84)
for i in (0, 1):
    report(f'idx={i} 安静', quiet.get(i))
    report(f'idx={i} 说话', talk.get(i))
    q, t = quiet.get(i), talk.get(i)
    if q and t and 'err' not in q and 'err' not in t:
        gain = t['rms_avg'] / max(q['rms_avg'], 1.0)
        print(f'  {"":<22} 说话/安静 = {gain:.2f}x')
    print()

print('  ' + '=' * 84)
print('  判读：')
print('    · 满量程 < 5%  → 输入增益过低，或麦克风离你太远')
print('    · 说话/安静 < 1.8x → 你的声音没有被有效拾取')
print('    · 变化块明显少于总块数 → 设备在返回重复/冻结数据')
print('  ' + '=' * 84)
p.terminate()
