# -*- coding: utf-8 -*-
"""P5-B4 探针 2：那一句到底是「音频里没有后半句」还是「产品把后半句过滤掉了」？

上一支探针查出：原句 8.26s、字数相称（合成端没截断），但整句只转出前 14 字；
把音频**切成两半**单独喂，后半段转出的是「謝謝觀看」（典型的**静音幻觉**）。

两种解释还没分开：
  甲. 音频后半段**就是静音** → 模型说了句幻觉，产品按既有规则把它滤掉了
      （那这条就是**测激问题**，不是 ASR 档位问题）
  乙. 音频后半段**有语音** → 是产品把真实的第二段过滤掉了
      （那这条就是**产品问题**）

分开它们的判据：**逐段原样打印模型的输出 + 每个片段的三个指标
（word_count / avg_logprob / no_speech_prob）+ 逐条套用产品的过滤规则**。
同时测音频后半段的实际音量（静音的话幅度接近 0）。

⚠️ 这里必须把过滤规则**原样复刻一遍**（照着 plugin.py:130-138 写），
而且要说明：复刻只用于**诊断**，不能作为产品判据 —— 本项目吃过
"测量工具复刻判据"的亏（P4-C2【三】）。真正的产品判据仍是
`FasterWhisperASR.transcribe()` 的返回值。
"""
import sys
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))

from plugins.asr.faster_whisper.plugin import FasterWhisperASR   # noqa: E402

AUDIO = ROOT / "docs" / "agent" / "evidence" / "p5" / "_tmp_b4_audio"
WAV = AUDIO / "s11.wav"                    # 那一句 33 字的音频
BIAS = "算了 不用了 停止 取消 确定 确认"


def rms_by_half(path: Path) -> None:
    with wave.open(str(path)) as wf:
        rate = wf.getframerate()
        pcm = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)
    half = len(pcm) // 2
    for label, seg in (("前半段", pcm[:half]), ("后半段", pcm[half:])):
        rms = float(np.sqrt(np.mean(seg.astype(np.float64) ** 2)))
        peak = int(np.max(np.abs(seg)))
        # 逐 0.5s 窗口的 RMS，看后半段是"全程安静"还是"有一段安静"
        win = rate // 2
        prof = [
            round(float(np.sqrt(np.mean(seg[i:i + win].astype(np.float64) ** 2))))
            for i in range(0, max(1, len(seg) - win), win)
        ]
        print(f"  {label}：RMS={rms:.1f} 峰值={peak} "
              f"每 0.5s RMS={prof[:12]}")


print("=" * 76)
print("P5-B4 探针 2：音频内容 vs 产品过滤规则")
print("=" * 76)
print()
print(f"音频：{WAV.name}")
print()
print("[一、音频音量分布]")
rms_by_half(WAV)
print()

print("[二、模型逐段输出 + 三个指标 + 产品规则判定]")
print("（参数与产品 transcribe() 完全一致：beam=5 / zh / vad_filter / "
      "condition_on_previous_text=False）")
print()

for label, bias in (("无偏置", ""), ("有偏置", BIAS)):
    asr = FasterWhisperASR(model_size="small", device="cpu", compute_type="int8",
                          initial_prompt=bias)
    asr._ensure_loaded()
    kw = {"beam_size": 5, "language": "zh", "vad_filter": True,
          "condition_on_previous_text": False, "no_speech_threshold": 0.6,
          "log_prob_threshold": -1.0, "word_timestamps": True}
    if bias:
        kw["initial_prompt"] = bias
    segments, info = asr.model.transcribe(str(WAV), **kw)

    kept, dropped = [], []
    n = 0
    for seg in segments:
        n += 1
        words = len(seg.words) if getattr(seg, "words", None) else 0
        lp = seg.avg_logprob
        ns = seg.no_speech_prob
        # 原样复刻 plugin.py:130-138 的放行条件（仅供诊断）
        if words >= 3 and lp > -1.3 and ns < 0.85:
            verdict, why = "保留", "规则1"
        elif words >= 4 and lp > -1.1:
            verdict, why = "保留", "规则2"
        elif 1 <= words < 3 and lp > -1.6 and ns < 0.8:
            verdict, why = "保留", "规则3(短词)"
        else:
            verdict, why = "丢弃", "三条规则都不满足"
        (kept if verdict == "保留" else dropped).append(seg.text)
        print(f"  [{label}] 片段{n}: {seg.text!r}")
        print(f"           t={seg.start:.2f}~{seg.end:.2f}s  words={words}  "
              f"avg_logprob={lp:.3f}  no_speech={ns:.3f}  → {verdict}（{why}）")

    print(f"  [{label}] 产品口径合并结果：{''.join(kept).strip()!r}")
    print(f"  [{label}] 被丢弃的片段文本：{dropped}")
    print()

print("=" * 76)
print("怎么读：")
print("  · 若「被丢弃的片段」里**有后半句的真实内容** ⇒ 是产品过滤太严（乙）")
print("  · 若被丢弃的片段是「謝謝觀看」这类**幻觉**，且后半段音频 RMS 接近 0")
print("    ⇒ 音频后半段本来就是静音（甲），这条属**测激问题**，")
print("      与 small/medium 档位无关，不该记成「模型听不全」")
