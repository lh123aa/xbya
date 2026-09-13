# -*- coding: utf-8 -*-
"""语音管线监督分析 —— 逐阶段量化，不猜。

产出：每一类症状的**发生率**与**证据行号**，让"哪里坏了"变成可复核的数字。
"""
import re
import sys
from collections import Counter

TS = re.compile(r'^(\d{2}:\d{2}:\d{2})\s+\[(\w+)\]\s+([\w.]+):\s*(.*)$')


def load(path):
    rows = []
    for line in open(path, encoding='utf-8', errors='replace'):
        m = TS.match(line.rstrip('\n'))
        if m:
            rows.append({'t': m.group(1), 'lvl': m.group(2),
                         'comp': m.group(3), 'msg': m.group(4)})
    return rows


def secs(t):
    h, m, s = map(int, t.split(':'))
    return h * 3600 + m * 60 + s


def grep(rows, pat):
    return [r for r in rows if re.search(pat, r['msg'])]


def main(path):
    rows = load(path)
    if not rows:
        print('  日志为空'); return 1

    total = secs(rows[-1]['t']) - secs(rows[0]['t'])
    print(f'  样本：{rows[0]["t"]} ~ {rows[-1]["t"]}（{total} 秒，{len(rows)} 行）')
    print()

    # ── 五个阶段各自的入口/出口 ──
    trig = grep(rows, '检测到语音，开始录音')
    cap = grep(rows, '监听捕获说话→回调')
    asr_ok = grep(rows, '识别完成')
    halluc = grep(rows, '判定为幻觉')
    to_agent = grep(rows, '语音交给 Agent 层处理')
    llm_ok = grep(rows, '回复成功')
    llm_429 = grep(rows, '429')
    tts_ok = grep(rows, '语音合成完成')
    tts_fail = grep(rows, '语音合成失败|空音频')
    wd = grep(rows, '看门狗.*强制')
    pct = lambda a, b: f'{a/b*100:.0f}%' if b else '—'

    print('  ══════ 阶段漏斗（每一层的通过率）══════')
    print(f'    ① 触发（超过阈值）      {len(trig):>4}')
    print(f'    ② 捕获（录完交付）      {len(cap):>4}   占触发 {pct(len(cap), len(trig))}')
    print(f'    ③ ASR 出文本            {len(asr_ok):>4}   占捕获 {pct(len(asr_ok), len(cap))}')
    print(f'       其中判为幻觉丢弃      {len(halluc):>4}')
    print(f'    ④ 送进 Agent/对话       {len(to_agent):>4}   ★占 ASR {pct(len(to_agent), len(asr_ok))}')
    print(f'    ⑤ LLM 回复成功          {len(llm_ok):>4}   占送入 {pct(len(llm_ok), len(to_agent))}')
    print(f'       LLM 429 限流          {len(llm_429):>4}')
    print(f'    ⑥ TTS 合成（含预热）    {len(tts_ok):>4}')
    print(f'       TTS 失败/空音频       {len(tts_fail):>4}')
    print(f'    ⑦ 看门狗强制解锁        {len(wd):>4}')
    print()

    # ── 症状 1：听不到 ──
    print('  ══════ 症状①「听不到」══════')
    vols = [int(m.group(1)) for r in trig if (m := re.search(r'vol=(\d+)', r['msg']))]
    if vols:
        vols.sort()
        print(f'    触发时电平：最短 {vols[0]} 中位 {vols[len(vols)//2]} 最高 {vols[-1]}')
    nf = [int(m.group(1)) for r in grep(rows, r'\[音量\]')
          if (m := re.search(r'底噪=(\d+)', r['msg']))]
    if nf:
        print(f'    底噪：最小 {min(nf)} 中位 {sorted(nf)[len(nf)//2]} 最大 {max(nf)}')
        print(f'    阈值：约 {min(nf)*1.7+60:.0f} ~ {max(nf)*1.7+60:.0f}')
    dedup = grep(rows, '死流恢复')
    print(f'    死流恢复次数：{len(dedup)}')
    print()

    # ── 症状 2：听不清（识别质量）──
    print('  ══════ 症状②「听不清」══════')
    texts = Counter()
    for r in asr_ok:
        m = re.search(r"文本='([^']*)'", r['msg'])
        if m:
            texts[m.group(1)] += 1
    if texts:
        print(f'    识别到 {len(texts)} 种不同文本：')
        for t, n in texts.most_common(8):
            print(f'      {n:>3} x {t[:52]}')
        # 重复率 = 同一句被反复识别的程度
        rep = sum(n for n in texts.values() if n > 1)
        print(f'    重复识别（同一句 >=2 次）：{rep}/{len(asr_ok)}')
    asr_ms = [int(m.group(1)) for r in grep(rows, r'ASR 耗时 (\d+)ms')
              if (m := re.search(r'ASR 耗时 (\d+)ms', r['msg']))]
    if asr_ms:
        asr_ms.sort()
        print(f'    ASR 耗时：中位 {asr_ms[len(asr_ms)//2]}ms 最长 {asr_ms[-1]}ms')
    print()

    # ── 症状 3：不回复 ──
    print('  ══════ 症状③「不回复」══════')
    lost = len(asr_ok) - len(to_agent)
    print(f'    ASR 出了文本但没进对话：{lost}/{len(asr_ok)}')
    if to_agent and llm_ok:
        print(f'    进入对话后得到回复：{len(llm_ok)}/{len(to_agent)}')
    print()

    # ── 症状 4：回复不发声 ──
    print('  ══════ 症状④「回复不发声」══════')
    print(f'    LLM 回复 {len(llm_ok)} 次，TTS 合成 {len(tts_ok)} 次')
    print('    注意：预热（ack）也会计入合成，需看 ack_warmup 行')
    ack = grep(rows, '确认语预热结束')
    if ack:
        m = re.search(r'成功 (\d+) 条', ack[0]['msg'])
        if m:
            print(f'    其中预热占 {m.group(1)} 条 → 真正播报用的合成 '
                  f'{len(tts_ok) - int(m.group(1))} 条')
    print()

    # ── 症状 5：答非所问 ──
    print('  ══════ 症状⑤「答非所问」══════')
    print('    判据：把 ASR 文本与她的回复并排，看是否对得上')
    replies = grep(rows, r'回复情绪')
    for r in replies[-5:]:
        m = re.search(r'文本: (.*?)\.\.\.', r['msg'])
        if m:
            print(f'      {r["t"]}  她说: {m.group(1)[:50]}')
    print()

    # ── 端到端延迟 ──
    print('  ══════ 端到端延迟（捕获 → LLM 回复）══════')
    caps = [secs(r['t']) for r in cap]
    reps = [secs(r['t']) for r in llm_ok]
    lat = []
    for cp in caps:
        later = [x for x in reps if x >= cp]
        if later:
            lat.append(min(later) - cp)
    if lat:
        lat.sort()
        print(f'    样本 {len(lat)}：中位 {lat[len(lat)//2]}s 最长 {lat[-1]}s')
    else:
        print('    没有走通的样本')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else 'logs/xbya.log'))
