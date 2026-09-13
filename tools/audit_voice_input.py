# -*- coding: utf-8 -*-
"""语音输入诊断报告 —— 一键给出可判定的结论。

把本轮所有测量收敛成一个可复跑的脚本，输出：
  1. 每个设备的活性（数据是否在变）
  2. 敲击响应（能不能收到声音）
  3. 频谱判据（录到的是人声还是噪声）
  4. 明确的下一步动作

用法：
    python tools/audit_voice_input.py            # 完整诊断
    python tools/audit_voice_input.py --quick    # 只做活性 + 敲击
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pyaudio

RATE, CHUNK = 16000, 1024


def read_blocks(p, idx, secs, chunk=CHUNK):
    """读若干块；打不开返回 (None, 错误原因)"""
    try:
        st = p.open(format=pyaudio.paInt16, channels=1, rate=RATE,
                    input=True, input_device_index=idx,
                    frames_per_buffer=chunk)
    except Exception as e:
        return None, str(e)[:40]
    try:
        n = int(RATE / chunk * secs)
        return [st.read(chunk, exception_on_overflow=False) for _ in range(n)], ''
    finally:
        try:
            st.stop_stream()
            st.close()
        except Exception:
            pass


def aliveness(blocks):
    """(变化块占比, 非零占比)"""
    if not blocks:
        return 0.0, 0.0
    uniq = len(set(blocks)) / len(blocks)
    nz = sum(1 for b in blocks
             if np.abs(np.frombuffer(b, dtype=np.int16)).max() > 0) / len(blocks)
    return uniq, nz


def peak_rms(blocks):
    if not blocks:
        return 0, 0.0
    pcm = np.frombuffer(b''.join(blocks), dtype=np.int16).astype(np.float64)
    return int(np.abs(pcm).max()), float(np.sqrt(np.mean(pcm ** 2)))


def band_ratios(blocks_speech, blocks_quiet):
    """说话段/安静段 在各频段的能量比。

    人声在 300-3400Hz 有明显能量；若该比值 <= 1，说明录到的不是人声。
    """
    def spec(blocks):
        if not blocks:
            return None, None
        x = np.frombuffer(b''.join(blocks), dtype=np.int16).astype(np.float64)
        if len(x) < 64:
            return None, None
        x = x - x.mean()
        f = np.fft.rfft(x * np.hanning(len(x)))
        return np.fft.rfftfreq(len(x), 1 / RATE), np.abs(f)

    f1, m1 = spec(blocks_speech)
    f2, m2 = spec(blocks_quiet)
    if m1 is None or m2 is None:
        return {}
    out = {}
    for name, a, b in (('低频<300', 0, 300), ('人声300-3400', 300, 3400),
                       ('高频>3400', 3400, 8000)):
        v1 = m1[(f1 >= a) & (f1 < b)].mean()
        v2 = m2[(f2 >= a) & (f2 < b)].mean()
        out[name] = v1 / max(v2, 1e-9)
    return out


def main():
    ap = argparse.ArgumentParser(description='语音输入诊断')
    ap.add_argument('--quick', action='store_true', help='只做活性 + 敲击')
    args = ap.parse_args()

    p = pyaudio.PyAudio()
    print()
    print('=' * 80)
    print('  语音输入诊断报告')
    print('=' * 80)

    # ── 1. 活性 ──
    print()
    print('【1】设备活性（判据：数据是否在变 + 非零占比 > 50%）')
    print(f"  {'idx':>4} {'设备':<34} {'变化块':>10} {'非零':>10}  判定")
    print('  ' + '-' * 74)
    alive = []
    for i in range(p.get_device_count()):
        d = p.get_device_info_by_index(i)
        if d.get('maxInputChannels', 0) <= 0:
            continue
        name = str(d.get('name', ''))[:32]
        blocks, err = read_blocks(p, i, 0.8)
        if blocks is None:
            continue
        u, nz = aliveness(blocks)
        ok = u > 0.5 and nz > 0.5
        mark = '✓ 活着' if ok else '✗ 死流/无信号'
        print(f"  {i:>4} {name:<34} {u*100:>9.0f}% {nz*100:>9.0f}%  {mark}")
        if ok:
            alive.append(i)

    print()
    print(f'  ▶ 活着的设备：{alive if alive else "**无**"}')

    # ── 2. 敲击响应 ──
    print()
    print('【2】敲击响应（能收到声音的设备会对敲击/吹气有明显峰值上升）')
    print()
    cands = alive if alive else []
    tap_ok = []
    for idx in cands:
        name = str(p.get_device_info_by_index(idx).get('name', ''))[:30]
        print(f'  idx={idx} {name}')
        print('      安静 2 秒...', end='', flush=True)
        time.sleep(1.6)
        q, e1 = read_blocks(p, idx, 1.6)
        qpk, _ = peak_rms(q) if q else (0, 0)
        print(f'\r      安静峰值={qpk:<7}')
        print('      现在敲麦克风/吹气 2 秒 <<<<', end='', flush=True)
        time.sleep(1.6)
        t, e2 = read_blocks(p, idx, 1.6)
        tpk, _ = peak_rms(t) if t else (0, 0)
        ratio = tpk / max(qpk, 1)
        v = '✓ 有响应' if ratio >= 1.5 else ('~ 弱' if ratio >= 1.2 else '✗ 无响应')
        print(f'\r      敲击峰值={tpk:<7} 比值={ratio:.2f}x  {v}')
        if ratio >= 1.5:
            tap_ok.append((ratio, idx))
        print()

    # ── 3. 频谱判据 ──
    if not args.quick and (tap_ok or alive):
        probe = tap_ok[0][1] if tap_ok else alive[0]
        print(f'【3】频谱判据（设备 idx={probe}）：录"安静"与"说话"，比各频段能量')
        print('      安静 3 秒...', end='', flush=True)
        time.sleep(2.4)
        quiet, _ = read_blocks(p, probe, 2.4)
        print(f'\r      现在大声说话 3 秒 <<<<', end='', flush=True)
        time.sleep(2.4)
        speech, _ = read_blocks(p, probe, 2.4)
        print('\r      分析中...                    ')
        r = band_ratios(speech, quiet)
        if r:
            print()
            for k, v in r.items():
                tag = ''
                if '人声' in k:
                    tag = '  ← 关键：>1.3 才说明录到了人声' if v <= 1.3 else '  ✓ 有人声'
                print(f'        {k:<14} 说话/安静 = {v:>5.2f}x{tag}')
            key = r.get('人声300-3400', 0)
            print()
            if key > 1.3:
                print('  ▶ 人声频段有明显能量 → 麦克风**能**收到你的声音')
            else:
                print('  ▶ 人声频段没有能量（<=1.3x）→ 录到的**不是人声**')
                print('     这只是宽带噪声在起伏，麦克风没有真正拾取你的声音。')

    # ── 结论 ──
    print()
    print('=' * 80)
    if not alive:
        print('  结论：**没有可用麦克风** —— 所有设备都是死流或恒零。')
        print('        检查：设备是否被其他程序独占 / Windows 隐私设置')
    elif not tap_ok:
        print('  结论：设备"活着"（数据在变）但**对敲击无响应**。')
        print('        这说明采集流在跑，但物理麦克风没有把声音送进来。')
        print()
        print('  请依次检查：')
        print('    1. 笔记本/键盘上的**麦克风静音键**（常有指示灯）')
        print('    2. Windows 设置 → 隐私 → 麦克风 → 允许桌面应用访问')
        print('    3. 声音设置 → 输入 → 设备属性 → **音量是否被拉到 0**')
        print('    4. 是否插着**耳机**（插上后内置麦克风常被自动禁用）')
        print('    5. USB/蓝牙麦克风是否真的连上了')
    else:
        print(f'  结论：可用设备 idx={tap_ok[0][1]}（响应 {tap_ok[0][0]:.2f}x）')
        print(f'        建议配置 voice.mic_device: {tap_ok[0][1]}')
    print('=' * 80)
    p.terminate()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
