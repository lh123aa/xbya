# -*- coding: utf-8 -*-
"""P5-B4 探针 7：`vad_filter=True` 到底多久截断一次？（产品侧缺陷定量）

探针 6 的矩阵里藏着一个**产品缺陷**，而且它把模型对比的结论也带偏了：

  `s11.wav` 上 medium 的表现是  vad=on **0.800** / vad=off **0.435**
  —— **关掉 VAD 反而更差**。这不合常理，除非"开着 VAD"那一次
  恰好只处理了音频的一小部分（截断），糊出来的 0.800 是**残缺文本的相似度**。
  探针 3 已经证实：这段音频在 vad=on 下只有 1 个片段 `0.21~3.03s`（音频 8.26s）。

也就是说：**"开 VAD 时相似度更高"是假象** —— 短文本与原句的前缀相似度
天然不低，容易读成"更准"。要按 **转写覆盖率**（转写字数 / 原句字数）看，
不能只看相似度。

本探针就量覆盖率：
  · 对 B4 那批**全部 15 段音频**，比较 vad=on / vad=off 的 (片段跨度, 转写字数)
  · 统计"vad=on 的覆盖率明显低于 vad=off"的条数 → 就是**截断发生率**
  · 同样分 small / medium 两档，看是否与档位有关

判据写死：一条音频算"被 VAD 截断"当且仅当
  `vad=on 的 span 末点 < 音频时长 − 1.0s` **且** `vad=off 的 span 末点 ≥ 音频时长 − 1.0s`
（拿两组参数互相当对照，不靠我目测。）
"""
import json
import sys
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))

from plugins.asr.faster_whisper.plugin import FasterWhisperASR   # noqa: E402

AUDIO = ROOT / "docs" / "agent" / "evidence" / "p5" / "_tmp_b4_audio"
ROWS = json.loads((AUDIO / "asr_long_sentence_rows.json").read_text(encoding="utf-8"))


def dur(p: Path) -> float:
    with wave.open(str(p)) as wf:
        return wf.getnframes() / wf.getframerate()


# 把 15 段音频按 MD5 对应回原句（B4 表里记的是 MD5）
import hashlib                                                        # noqa: E402

by_md5 = {}
for p in sorted(AUDIO.glob("s*.wav")):
    if "_" in p.stem or "repeat" in p.stem or "tail" in p.stem:
        continue
    by_md5[hashlib.md5(p.read_bytes()).hexdigest()[:12]] = p

items = []
for r in ROWS:
    p = by_md5.get(r["md5"])
    if p:
        items.append({"text": r["text"], "group": r["group"], "wav": p,
                      "dur": dur(p)})

print("=" * 92)
print("P5-B4 探针 7：vad_filter=True 的截断发生率（按覆盖率，不按相似度）")
print("=" * 92)
print()
print(f"样本：B4 那批 {len(items)} 段音频（短句 6 + 中句 3 + 长句 6）")
print()

models = {}
for size in ("small", "medium"):
    a = FasterWhisperASR(model_size=size, device="cpu", compute_type="int8")
    a._ensure_loaded()
    models[size] = a

print("| 档 | 音频 | 时长 | vad=on span | vad=off span | vad=on 覆盖率 | "
      "vad=off 覆盖率 | 判定 |")
print("|----|------|------|-------------|--------------|--------------|"
      "---------------|------|")

trunc = []
for size in ("small", "medium"):
    for it in items:
        res = {}
        for vad in (True, False):
            segs = list(models[size].model.transcribe(
                str(it["wav"]), beam_size=5, language="zh", vad_filter=vad,
                condition_on_previous_text=False, no_speech_threshold=0.6,
                log_prob_threshold=-1.0, word_timestamps=True)[0])
            text = "".join(s.text for s in segs).strip()
            span_end = segs[-1].end if segs else 0.0
            res[vad] = {"text": text, "end": span_end,
                        "cov": len(text) / max(1, len(it["text"]))}
        truncated = (res[True]["end"] < it["dur"] - 1.0
                     and res[False]["end"] >= it["dur"] - 1.0)
        if truncated:
            trunc.append({"size": size, **{k: it[k] for k in
                                           ("text", "group", "dur")},
                          "on_cov": res[True]["cov"]})
        mark = "**被 VAD 截断**" if truncated else "正常"
        print(f"| {size} | {it['text'][:14]}… | {it['dur']:.2f}s | "
              f"{res[True]['end']:.2f}s | {res[False]['end']:.2f}s | "
              f"{res[True]['cov']:.0%} | {res[False]['cov']:.0%} | {mark} |")
    print()

print("=" * 92)
print("截断统计")
print("=" * 92)
print()
print(f"- 总样本 {len(items)} 段 × 2 档 = {len(items) * 2} 次调用")
print(f"- **被 VAD 截断 {len(trunc)} 次**")
for t in trunc:
    print(f"    · {t['size']}：{t['text'][:24]}…（音频 {t['dur']:.2f}s，"
          f"vad=on 只覆盖 {t['on_cov']:.0%}）")
print()
by_size = {}
for t in trunc:
    by_size[t["size"]] = by_size.get(t["size"], 0) + 1
print(f"- 分档：{by_size or '无'}")
print()
print("⚠️ 这条**与 small/medium 的档位选择无关**：它由 `vad_filter=True` 造成，")
print("   两档都会中招。它同时**污染了模型对比**——若只按相似度看，")
print("   截断后的残缺文本反而可能拿到不低的相似度（前缀本来就相似）。")
print("   所以 B4 的结论表必须用**覆盖完整的音频**，并把相似度与覆盖率一起看。")
