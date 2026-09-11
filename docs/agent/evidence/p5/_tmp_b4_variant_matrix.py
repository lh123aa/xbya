# -*- coding: utf-8 -*-
"""P5-B4 探针 6：把**测激效应**与**模型效应**分开 —— 同一批音频跑满四格。

到这里已经查清的事实链：

  1. 原音频**不是**静音（后半段 RMS 4257 vs 前半段 4605）、也**不是**截断
     （8.26s，与字数相称）
  2. 产品参数（`vad_filter=True`）下只出 1 个片段 `t=0.21~3.03s` ⇒
     **VAD 把后面的语音当噪音切掉了**，这是**产品侧**行为
  3. 关掉 VAD 能多转出后半句，但 small 对这句的相似度只从 0.435 动到 0.444
  4. 用 medium 当裁判：原音频 **0.800**、（换偏置后）0.879；**重新合成**后 **1.000**
     ⇒ 原音频"更难"，但不是不可解
  5. 5 段同文本音频**时长完全一致（8.26s）**、字数/秒一致（3.99），
     但**静音占比**分成两簇：**3.0~4.8%** 与 **15.2%**

第 5 条说明差异不在"念没念完"，而在**停顿**。而"停顿"正好是 VAD 与
小模型共同脆弱的地方。

所以本探针做一张**能分清归因**的表：
  · 5 段音频 × (small/medium) × (vad on/off) → 相似度
  · 同时记录每段音频的静音占比，看相似度是否随它变化
  · 若 medium 在**每一段**上都 ≥ small，而 small 随静音占比波动 ⇒
    这是**模型鲁棒性差异**，不是测激随机性
  · 若某段音频**两个模型都低** ⇒ 那段才是坏测激

**不预设结论，表出来是什么就是什么。**
"""
import json
import sys
import wave
from difflib import SequenceMatcher
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))

from plugins.asr.faster_whisper.plugin import FasterWhisperASR   # noqa: E402

AUDIO = ROOT / "docs" / "agent" / "evidence" / "p5" / "_tmp_b4_audio"
TEXT = "把下载目录里所有后缀是安装包的文件都移动到软件归档这个文件夹里面去"


def norm(s):
    import re
    return re.sub(r"[\s，。、！？：；,.!?]+", "", s or "")


def sim(a, b):
    return SequenceMatcher(None, norm(a), norm(b)).ratio()


def silent_ratio(wav: Path) -> float:
    with wave.open(str(wav)) as wf:
        rate = wf.getframerate()
        pcm = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)
    win = rate // 20
    rms = np.array([float(np.sqrt(np.mean(pcm[i:i + win].astype(np.float64) ** 2)))
                    for i in range(0, max(1, len(pcm) - win), win)])
    thr = max(30.0, rms.max() * 0.05)
    return float(np.mean(rms < thr)) if len(rms) else 1.0


VARIANTS = [p for p in sorted(AUDIO.glob("s11*.wav")) if p.is_file()]
print(f"音频变体：{[v.name for v in VARIANTS]}")
print()

rows = []
for v in VARIANTS:
    rows.append({"name": v.name, "wav": v, "silent": silent_ratio(v)})

models = {}
for size in ("small", "medium"):
    a = FasterWhisperASR(model_size=size, device="cpu", compute_type="int8")
    a._ensure_loaded()
    models[size] = a

print("=" * 96)
print("5 段音频 × 2 模型 × 2 VAD 设置")
print("=" * 96)
print()
hdr = (f"| 音频 | 静音占比 | small vad=on | small vad=off | "
       f"medium vad=on | medium vad=off |")
print(hdr)
print("|------|----------|--------------|---------------|"
      "---------------|----------------|")
table = []
for r in rows:
    cells = []
    row = {"name": r["name"], "silent": r["silent"]}
    for size in ("small", "medium"):
        for vad in (True, False):
            segs = list(models[size].model.transcribe(
                str(r["wav"]), beam_size=5, language="zh", vad_filter=vad,
                condition_on_previous_text=False, no_speech_threshold=0.6,
                log_prob_threshold=-1.0, word_timestamps=True)[0])
            text = "".join(s.text for s in segs).strip()
            s = sim(TEXT, text)
            span = f"{segs[0].start:.2f}~{segs[-1].end:.2f}s" if segs else "—"
            row[f"{size}_{vad}"] = {"text": text, "sim": s, "span": span,
                                    "nseg": len(segs)}
            cells.append(f"{s:.3f}")
    table.append(row)
    print(f"| `{r['name']}` | {r['silent']:.1%} | " + " | ".join(cells) + " |")
print()

print("=" * 96)
print("逐段完整转写（不许只看分数）")
print("=" * 96)
print()
for row in table:
    print(f"### `{row['name']}`（静音占比 {row['silent']:.1%}）")
    print()
    for key in ("small_True", "small_False", "medium_True", "medium_False"):
        o = row[key]
        vad = "on " if key.endswith("True") else "off"
        size = key.split("_")[0]
        print(f"- `{size}` vad={vad} span={o['span']}（{o['nseg']} 片段）"
              f" 相似度 {o['sim']:.3f}")
        print(f"    {o['text']!r}")
    print()

print("=" * 96)
print("归因：模型效应 vs 测激效应")
print("=" * 96)
print()
for vad in (True, False):
    diffs = [row[f"medium_{vad}"]["sim"] - row[f"small_{vad}"]["sim"]
             for row in table]
    print(f"- vad={'on ' if vad else 'off'}：medium − small = "
          f"{[round(d, 3) for d in diffs]}"
          f"  平均 {sum(diffs) / len(diffs):+.3f}"
          f"  最小 {min(diffs):+.3f}")
print()
lo = [row for row in table if min(row["small_False"]["sim"],
                                  row["medium_False"]["sim"]) < 0.6]
print(f"- **两个模型都低（<0.6）的音频**（这些才更像坏测激）："
      f"{[row['name'] for row in lo] or '无'}")
print(f"- 静音占比与 small(vad=off) 相似度的关系：")
for row in table:
    print(f"    {row['name']:24s} 静音 {row['silent']:6.1%} → "
          f"small {row['small_False']['sim']:.3f} / "
          f"medium {row['medium_False']['sim']:.3f}")

out = AUDIO / "b4_variant_matrix.json"
out.write_text(json.dumps(
    [{k: (v if k != "wav" else str(v)) for k, v in row.items()} for row in rows]
    + table, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
print()
print(f"原始矩阵已存：{out}")
