# -*- coding: utf-8 -*-
"""逐个试**尚未用过**的设备接口，找出哪个真的能收到你的声音。

## 为什么要试

本机有 14 个"输入设备"，但它们是同一批硬件的**不同 host API 别名**：
  · idx=1 (MME) / idx=7 (DirectSound)  -> 都叫「麦克风 (Realtek(R) Audio)」
  · idx=22/23/24 (WDM-KS)             -> 「麦克风 1/2/3 (Realtek HD Audio Mic input)」
                                          **从没试过**，ch=4，像是真正的物理插孔
  · idx=0/2/8/14/16                   -> 「UU远程虚拟音频设备」（远程桌面虚拟设备）

之前只试过 idx=0/1/7/8，**从不成功的那些里挑**。本脚本把所有能打开的设备
都试一遍，并用"敲击响应"作判据 —— 敲麦克风会产生极明显的冲击，
比"说话"更容易客观判定。

## 判据

对每个设备：先静默 2 秒，再敲击/吹气 2 秒。比较两段的**峰值**。
峰值显著上升的（>= 2 倍）就是能收到声音的设备。
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pyaudio

RATE, CHUNK = 16000, 1024

CANDIDATES = [1, 7, 22, 23, 24, 0, 2, 6, 8, 15, 18]


def grab(p, idx, secs):
    """返回 (峰值, RMS, 是否成功, 错误)"""
    try:
        st = p.open(format=pyaudio.paInt16, channels=1, rate=RATE,
                    input=True, input_device_index=idx,
                    frames_per_buffer=CHUNK)
    except Exception as e:
        return None, None, False, str(e)[:34]
    try:
        pk, rs = [], []
        for _ in range(int(RATE / CHUNK * secs)):
            b = st.read(CHUNK, exception_on_overflow=False)
            a = np.abs(np.frombuffer(b, dtype=np.int16))
            pk.append(int(a.max()))
            rs.append(float(np.sqrt(np.mean(a.astype(np.float64) ** 2))))
        return max(pk), sum(rs) / len(rs), True, ''
    finally:
        try:
            st.stop_stream()
            st.close()
        except Exception:
            pass


def main():
    p = pyaudio.PyAudio()
    print()
    print('  ' + '=' * 78)
    print('   逐设备"敲击响应"测试')
    print('  ' + '=' * 78)
    print('   每个设备会测两段：')
    print('     · 第 1 段 2 秒 —— 【保持安静】')
    print('     · 第 2 段 2 秒 —— 【用手指轻敲麦克风 / 对着吹气】')
    print()
    print('   判据：第 2 段峰值明显高于第 1 段（>=1.5 倍）=> 这个设备能收到声音')
    print()

    results = []
    for idx in CANDIDATES:
        try:
            name = str(p.get_device_info_by_index(idx).get('name'))[:30]
        except Exception:
            continue
        print(f'  --- idx={idx}  {name} ---')

        print('      安静 2 秒...', end='', flush=True)
        for t in (2, 1):
            time.sleep(0.8)
        q_pk, q_rms, ok, err = grab(p, idx, 2.0)
        if not ok:
            print(f'\r      ✗ 打不开: {err}')
            continue
        print(f'\r      安静: 峰值={q_pk:<7} RMS={q_rms:.0f}')

        # 重新打开测第二段（模拟"换个时刻"）
        print('      现在【敲麦克风/吹气】2 秒 <<<<', end='', flush=True)
        for t in (2, 1):
            time.sleep(0.8)
        t_pk, t_rms, ok2, err2 = grab(p, idx, 2.0)
        if not ok2:
            print(f'\r      ✗ 第二段打不开')
            continue

        ratio = t_pk / max(q_pk, 1)
        verdict = ('✓ 能收到声音' if ratio >= 1.5 else
                   ('~ 略有响应' if ratio >= 1.2 else '✗ 无响应'))
        print(f'\r      敲击: 峰值={t_pk:<7} RMS={t_rms:.0f}   '
              f'比值={ratio:.2f}x  {verdict}')
        results.append((ratio, idx, name, q_pk, t_pk))
        print()

    print('  ' + '=' * 78)
    if results:
        results.sort(reverse=True)
        print('  排名（按敲击响应比值）：')
        for ratio, idx, name, q, t in results:
            mark = '★' if ratio >= 1.5 else ' '
            print(f'   {mark} idx={idx:<3} {ratio:>6.2f}x  安静{q:>6} -> 敲击{t:>6}  {name}')
        best = results[0]
        if best[0] >= 1.5:
            print()
            print(f'  ▶ 建议使用 idx={best[1]}（{best[2]}），响应 {best[0]:.2f}x')
        else:
            print()
            print('  ▶ **没有任何设备对敲击有响应**')
            print('     说明：麦克风可能被物理静音（笔记本有静音键/指示灯）、')
            print('           或者是 USB/蓝牙麦克风未真正连接。')
    print('  ' + '=' * 78)
    p.terminate()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
