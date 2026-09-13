# -*- coding: utf-8 -*-
"""外放可行性测量（v2）—— **同时**采集，避免顺序测量的混淆。

## v1 的问题

v1 逐个设备顺序采集，于是 idx=1 的"安静段"与 idx=7 的"说话段"
**不是同一时刻**。两次结果放一起比，比的是"时间差"而不是"设备差"，
结论自相矛盾（一个说可分、一个说不可分）。

## v2 的做法

把要比较的设备**同时打开**，在同一段音频里取两段：
  · 安静段（你不说话，视频照常放）
  · 说话段（你大声说，视频照常放）

然后对**每个设备**分别算这两段的峰谷比。
这样"安静 vs 说话"的差异只来自你，不来自时间。

## 判据

外放环境下不能靠"响度超过阈值"（视频可能比人声大）。
但人声有**停顿与重音**，视频音**连续平坦** —— 差异在"起伏"上。
所以看：说话段的峰谷比是否明显高于安静段。
"""
import time
import audioop

import pyaudio

RATE, CHUNK = 16000, 1024
WIN = max(1, int(0.2 * RATE / CHUNK))
DEVICES = [1, 7]


def open_all(p):
    sts = {}
    for i in DEVICES:
        try:
            sts[i] = p.open(format=pyaudio.paInt16, channels=1, rate=RATE,
                            input=True, input_device_index=i,
                            frames_per_buffer=CHUNK)
        except Exception as e:
            print(f'    ! idx={i} 打不开: {str(e)[:40]}')
    return sts


def collect(sts, secs):
    """同时从所有流采集，返回 {idx: [rms, ...]}"""
    n = int(RATE / CHUNK * secs)
    out = {i: [] for i in sts}
    for _ in range(n):
        for i, st in sts.items():
            try:
                out[i].append(
                    audioop.rms(st.read(CHUNK, exception_on_overflow=False), 2))
            except Exception:
                out[i].append(0)
    return out


def stats(rms):
    nz = [v for v in rms if v > 0]
    if len(nz) < 4:
        return None
    env = [sum(rms[i:i + WIN]) / len(rms[i:i + WIN])
           for i in range(0, max(1, len(rms) - WIN + 1), WIN)]
    env = [e for e in env if e > 0]
    if len(env) < 2:
        return None
    return {
        'avg': sum(env) / len(env),
        'dyn': max(env) / max(min(env), 1.0),
        'nz': len(nz),
        'n': len(rms),
    }


def main():
    p = pyaudio.PyAudio()
    print()
    print('  打开设备（同时占用）...')
    sts = open_all(p)
    if not sts:
        print('  没有可用设备，退出')
        p.terminate()
        return 1

    print()
    print('  ==========================================================')
    print('   第 1 段：视频照常放，你【不要说话】—— 5 秒')
    print('  ==========================================================')
    for t in (5, 4, 3, 2, 1):
        print(f'      {t}...', end='\r')
        time.sleep(1)
    quiet = collect(sts, 3.5)

    print('  ==========================================================')
    print('   第 2 段：视频继续放，你【大声持续说话】—— 5 秒')
    print('  ==========================================================')
    for t in (5, 4, 3, 2, 1):
        print(f'      {t}...', end='\r')
        time.sleep(1)
    talk = collect(sts, 3.5)
    print('      采集完成                                  ')

    for st in sts.values():
        try:
            st.stop_stream()
            st.close()
        except Exception:
            pass

    print()
    print('  ' + '=' * 82)
    print(f"  {'idx':>3} {'安静均':>10} {'安静峰谷':>9} {'说话均':>10} "
          f"{'说话峰谷':>9} {'非零样本':>8}  判定")
    print('  ' + '-' * 82)
    for i in DEVICES:
        if i not in sts:
            continue
        q, t = stats(quiet.get(i, [])), stats(talk.get(i, []))
        if not q or not t:
            print(f'  {i:>3} {"—":>10} {"—":>9} {"—":>10} {"—":>9} {"—":>8}  '
                  f'死流（能打开但恒为 0）')
            continue
        gain = t['dyn'] - q['dyn']
        if t['dyn'] >= 2.5 and gain >= 0.8:
            v = '✓ 起伏可分 → 免阈值方案可行'
        elif gain >= 0.4:
            v = '~ 弱可分 → 需声纹辅助'
        else:
            v = '✗ 分不出'
        print(f"  {i:>3} {q['avg']:>10.1f} {q['dyn']:>9.2f} "
              f"{t['avg']:>10.1f} {t['dyn']:>9.2f} "
              f"{t['nz']:>4}/{t['n']:<3}  {v}")

    print()
    print('  说明：峰谷比高 = 信号有起伏（人声）；低 = 平坦（视频/音乐）')
    print('        两段是**同时**采集的，差异只来自你说话，不来自时间')
    p.terminate()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
