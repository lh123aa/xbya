# -*- coding: utf-8 -*-
"""麦克风可用性一键自检 —— 回答"现在这台机器上，哪个麦克风真的活着？"

## 为什么要专门做这个工具

本次监督分析反复被同一件事误导：**设备"能打开"不代表"有信号"**。
实测出现过这些状态，全都是"看起来正常"的：

  · 能打开，但恒返回 0            -> 读到的 RMS=0，被误读成"环境很安静"
  · 能打开，但返回**完全相同**的重复缓冲 -> 被误读成"设备在工作"
  · 多个不同 idx 读数**逐位相同**   -> 被误读成"两个设备电平一致"

判据必须是"**数据是否在变**"，而不是"能不能打开"或"幅度大不大"。

## 判据（三条全过才算活着）

  1. 能打开（不抛异常）
  2. 读到的块**有变化**（>50% 的块互不相同）
  3. 非零样本占比 > 50%

用法：
    python tools/audit_mic_alive.py            # 自检全部输入设备
    python tools/audit_mic_alive.py --secs 2   # 每设备读 2 秒
"""
import argparse
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import audioop
import pyaudio

RATE = 16000


def probe(p, idx, secs, chunk):
    try:
        st = p.open(format=pyaudio.paInt16, channels=1, rate=RATE,
                    input=True, input_device_index=idx,
                    frames_per_buffer=chunk)
    except Exception as e:
        return {'ok': False, 'why': f'打不开: {str(e)[:36]}'}
    try:
        n = int(RATE / chunk * secs)
        blocks = []
        for _ in range(n):
            blocks.append(st.read(chunk, exception_on_overflow=False))
        uniq = len(set(blocks))
        rms = [audioop.rms(b, 2) for b in blocks]
        nz = sum(1 for v in rms if v > 0)
        varying = uniq / max(len(blocks), 1)
        nzr = nz / max(len(blocks), 1)
        ok = varying > 0.5 and nzr > 0.5
        why = ''
        if uniq <= 1:
            why = '重复缓冲（每次读到同一块）'
        elif varying <= 0.5:
            why = f'几乎不变（{uniq}/{len(blocks)} 块不同）'
        elif nzr <= 0.5:
            why = f'多为零（{nz}/{len(blocks)} 非零）'
        return {
            'ok': ok, 'why': why,
            'blocks': len(blocks), 'uniq': uniq,
            'nz': nz, 'rms_min': min(rms), 'rms_max': max(rms),
            'avg': sum(rms) / len(rms),
        }
    finally:
        try:
            st.stop_stream()
            st.close()
        except Exception:
            pass


def main():
    ap = argparse.ArgumentParser(description='麦克风可用性自检')
    ap.add_argument('--secs', type=float, default=1.2, help='每设备读取秒数')
    ap.add_argument('--chunk', type=int, default=1024)
    args = ap.parse_args()

    p = pyaudio.PyAudio()

    print()
    print('  ' + '=' * 86)
    print(f"  麦克风自检（每设备读 {args.secs}s，判据：数据在变 + 非零）")
    print('  ' + '=' * 86)
    print(f"  {'idx':>4} {'设备名':<40} {'均RMS':>8} {'变化块':>9} {'非零':>8}  判定")
    print('  ' + '-' * 86)

    alive = []
    for i in range(p.get_device_count()):
        info = p.get_device_info_by_index(i)
        if info.get('maxInputChannels', 0) <= 0:
            continue
        name = str(info.get('name', ''))[:38]
        r = probe(p, i, args.secs, args.chunk)
        if not r['ok'] and 'blocks' not in r:
            print(f"  {i:>4} {name:<40} {'—':>8} {'—':>9} {'—':>8}  {r['why']}")
            continue
        mark = '✓ 活着' if r['ok'] else f"✗ {r['why']}"
        print(f"  {i:>4} {name:<40} {r['avg']:>8.1f} "
              f"{r['uniq']:>4}/{r['blocks']:<4} {r['nz']:>4}/{r['blocks']:<3}  {mark}")
        if r['ok']:
            alive.append((r['avg'], i, name))

    print()
    if alive:
        alive.sort(reverse=True)
        print(f"  ▶ 可用的设备（{len(alive)} 个）：")
        for avg, i, name in alive[:5]:
            print(f'      idx={i:<3} 均RMS={avg:>8.1f}  {name}')
        print()
        print(f'  ▶ 建议配置：voice.mic_device: {alive[0][1]}')
    else:
        print('  ▶ **没有任何设备在提供实时音频**')
        print('      常见原因：')
        print('        · 被别的程序独占（浏览器/微信/会议软件会独占麦克风）')
        print('        · Windows 隐私设置里关掉了麦克风权限')
        print('        · 设备被禁用或驱动异常')
    print('  ' + '=' * 86)
    p.terminate()
    return 0 if alive else 1


if __name__ == '__main__':
    raise SystemExit(main())
