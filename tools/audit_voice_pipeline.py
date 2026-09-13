# -*- coding: utf-8 -*-
"""语音管线**端到端**监督分析。

不问"哪里坏了"，而是把每个阶段的**耗时与成功/失败**都量出来，
让数据指出瓶颈在哪。

阶段：
  A 采集   捕获到 wav
  B ASR    wav -> 文本
  C 路由   文本 -> 交给谁（闲聊/Agent）
  D LLM    文本 -> 回复
  E TTS    回复 -> 音频
  F 播放   音频 -> 出声

用法：
  python _pipeline_audit.py <日志文件>
"""
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime

TS = re.compile(r'^(\d{2}:\d{2}:\d{2})\s+\[(\w+)\]\s+([\w.]+):\s*(.*)$')


def load(path):
    rows = []
    for line in open(path, encoding='utf-8', errors='replace'):
        m = TS.match(line.rstrip('\n'))
        if m:
            t, lvl, comp, msg = m.groups()
            rows.append({'t': t, 'lvl': lvl, 'comp': comp, 'msg': msg})
    return rows


def secs(t):
    h, m, s = map(int, t.split(':'))
    return h * 3600 + m * 60 + s


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else 'logs/xbya.log'
    rows = load(path)
    if not rows:
        print(f'  日志为空或格式不符: {path}')
        return 1

    print(f'  日志: {path}')
    print(f'  跨度: {rows[0]["t"]} ~ {rows[-1]["t"]}   共 {len(rows)} 行')
    dur = secs(rows[-1]['t']) - secs(rows[0]['t'])
    print(f'  时长: {dur} 秒')
    print()

    # ── 阶段计数 ──
    c = Counter()
    for r in rows:
        m = r['msg']
        if '监听捕获说话→回调' in m:
            c['A_捕获'] += 1
        if '检测到语音，开始录音' in m:
            c['A_触发'] += 1
        if '早退A' in m:
            c['A_早退无语音'] += 1
        if '早退B' in m:
            c['A_早退背景音'] += 1
        if '识别完成' in m:
            c['B_ASR成功'] += 1
        if '判定为幻觉' in m:
            c['B_ASR幻觉丢弃'] += 1
        if 'ASR 耗时' in m:
            c['B_ASR计时'] += 1
        if '语音交给 Agent 层处理' in m:
            c['C_转Agent'] += 1
        if '回复成功' in m:
            c['D_LLM成功'] += 1
        if '429' in m:
            c['D_LLM限流429'] += 1
        if '降级到备用' in m:
            c['D_降级备用'] += 1
        if '都失败了' in m:
            c['D_双失败'] += 1
        if '语音合成完成' in m:
            c['E_TTS成功'] += 1
        if '语音合成失败' in m:
            c['E_TTS失败'] += 1
        if '空音频' in m:
            c['E_TTS空音频'] += 1
        if '播报' in m:
            c['F_播报'] += 1
        if '看门狗' in m and '强制' in m:
            c['X_看门狗强制'] += 1
        if '死流恢复' in m:
            c['X_死流恢复'] += 1
        if r['lvl'] == 'ERROR':
            c['X_ERROR'] += 1

    print('  ═══ 各阶段计数 ═══')
    order = ['A_触发', 'A_捕获', 'A_早退无语音', 'A_早退背景音',
             'B_ASR成功', 'B_ASR幻觉丢弃', 'C_转Agent',
             'D_LLM成功', 'D_LLM限流429', 'D_降级备用', 'D_双失败',
             'E_TTS成功', 'E_TTS失败', 'E_TTS空音频', 'F_播报',
             'X_看门狗强制', 'X_死流恢复', 'X_ERROR']
    for k in order:
        if c[k]:
            print(f'    {k:<18} {c[k]:>5}')

    # ── ASR 耗时分布 ──
    asr = [int(m.group(1)) for r in rows
           if (m := re.search(r'ASR 耗时 (\d+)ms', r['msg']))]
    if asr:
        asr.sort()
        print()
        print('  ═══ ASR 耗时(ms) ═══')
        print(f'    次数={len(asr)} 最短={asr[0]} 中位={asr[len(asr)//2]} '
              f'P90={asr[int(len(asr)*0.9)]} 最长={asr[-1]}')

    # ── 识别文本分布（答非所问的判据：输入本身是什么）──
    texts = Counter()
    for r in rows:
        m = re.search(r"文本='([^']*)'", r['msg'])
        if m:
            texts[m.group(1)] += 1
        m2 = re.search(r"丢弃: '([^']*)'", r['msg'])
        if m2:
            texts[f"[幻觉] {m2.group(1)}"] += 1
    if texts:
        print()
        print('  ═══ ASR 实际识别到的文本 TOP10 ═══')
        for txt, n in texts.most_common(10):
            print(f'    {n:>4} x  {txt[:56]}')

    # ── 端到端延迟：捕获 -> 播报 ──
    print()
    print('  ═══ 端到端延迟（捕获 -> 首次播报）═══')
    caps = [secs(r['t']) for r in rows if '监听捕获说话→回调' in r['msg']]
    spoke = [secs(r['t']) for r in rows if '合成完成' in r['msg']]
    lat = []
    for cp in caps:
        nxt = [s for s in spoke if s >= cp]
        if nxt:
            lat.append(min(nxt) - cp)
    if lat:
        lat.sort()
        print(f'    样本={len(lat)} 最短={lat[0]}s 中位={lat[len(lat)//2]}s '
              f'P90={lat[int(len(lat)*0.9)]}s 最长={lat[-1]}s')
    else:
        print('    样本不足')

    # ── 失败的完整链路 ──
    print()
    print('  ═══ 未走通的链路（捕获后没等到 TTS）═══')
    miss = 0
    for cp in caps:
        if not [s for s in spoke if 0 <= s - cp <= 30]:
            miss += 1
    print(f'    捕获 {len(caps)} 次，其中 {miss} 次在 30 秒内没有任何 TTS')

    return 0


if __name__ == '__main__':
    sys.exit(main())
