# -*- coding: utf-8 -*-
"""P5-B4 探针 4：那一句到底是不是"合成得就不对"？用更大的模型当裁判。

已知（探针 2/3）：
  · 音频 8.26s、能量均匀（后半段 RMS 4257 vs 前半段 4605，**不是静音**）
  · 产品参数（`vad_filter=True`）下只有 1 个片段 t=0.21~3.03s → **后半段被 VAD 切掉**
  · `vad_filter=False` 能多转出 7 个字，但**内容仍然是错的**
    （'把下三幕裡所有後製是安裝包…'），相似度只从 0.435 动到 0.444
  · 四格（small/medium × 有/无偏置）全部在这句上失败，且**开头几个字就听错**

「开头就听错」这一点很关键：开头是**能量最强、最清晰**的部分，
连它都听成 '打下在'（原文「把下载」），不像"模型能力不足"该有的样子 ——
更像**测激本身有问题**（合成得就不像那句话）。

判据（用模型当裁判，而不是靠我的耳朵）：
  拿**明显更强的模型**去听同一段音频。
    · 若强模型**也**听成那串怪话 ⇒ 音频本身就不对（测激问题，与档位无关）
    · 若强模型**听对了** ⇒ 是 small 能力不足（那才是档位问题）

用 `large-v3` 需要下载 ~3GB。这里先用**已有的 medium + 关闭 VAD + 更大的 beam**
当第一级裁判；若 medium 也失败，再决定要不要下载 large-v3。
（下载是不可逆的磁盘开销，先花小代价试。）
"""
import sys
from difflib import SequenceMatcher
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))

from plugins.asr.faster_whisper.plugin import FasterWhisperASR   # noqa: E402

AUDIO = ROOT / "docs" / "agent" / "evidence" / "p5" / "_tmp_b4_audio"
WAV = AUDIO / "s11.wav"
TEXT = "把下载目录里所有后缀是安装包的文件都移动到软件归档这个文件夹里面去"


def norm(s):
    import re
    return re.sub(r"[\s，。、！？：；,.!?]+", "", s or "")


def sim(a, b):
    return SequenceMatcher(None, norm(a), norm(b)).ratio()


print("=" * 76)
print("P5-B4 探针 4：用更强的配置当裁判 —— 音频本身对不对？")
print("=" * 76)
print()
print(f"原句：{TEXT}")
print()

# 对照：同一音色合成的**同一句话**再合成一次，看是不是每次都一样糟
import asyncio                                                    # noqa: E402

import av
import edge_tts
import numpy as np
import wave


def synth(text, out_mp3, out_wav):
    async def _g():
        await edge_tts.Communicate(text, "zh-CN-XiaoxiaoNeural").save(str(out_mp3))
    asyncio.run(_g())
    chunks = []
    with av.open(str(out_mp3)) as c:
        st = c.streams.audio[0]
        rs = av.audio.resampler.AudioResampler(format="s16", layout="mono",
                                              rate=16000)
        for fr in c.decode(st):
            for o in rs.resample(fr):
                chunks.append(np.frombuffer(bytes(o.planes[0]), dtype=np.int16))
        for o in rs.resample(None):
            chunks.append(np.frombuffer(bytes(o.planes[0]), dtype=np.int16))
    pcm = np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.int16)
    with wave.open(str(out_wav), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(pcm.tobytes())


print("[一、medium 当裁判（vad_filter 关，beam 加大）]")
med = FasterWhisperASR(model_size="medium", device="cpu", compute_type="int8")
med._ensure_loaded()
for beam in (5, 10):
    segs = list(med.model.transcribe(
        str(WAV), beam_size=beam, language="zh", vad_filter=False,
        condition_on_previous_text=False, no_speech_threshold=0.6,
        log_prob_threshold=-1.0, word_timestamps=True)[0])
    text = "".join(s.text for s in segs).strip()
    print(f"  medium beam={beam}: {text!r}")
    print(f"     相似度 {sim(TEXT, text):.3f}")
print()

print("[二、把这一句**重新合成一次**再听（测激抖动的反方向）]")
alt_wav = AUDIO / "s11_alt.wav"
synth(TEXT, AUDIO / "s11_alt.mp3", alt_wav)
for size in ("small", "medium"):
    a = FasterWhisperASR(model_size=size, device="cpu", compute_type="int8")
    a._ensure_loaded()
    for vad in (True, False):
        segs = list(a.model.transcribe(
            str(alt_wav), beam_size=5, language="zh", vad_filter=vad,
            condition_on_previous_text=False, no_speech_threshold=0.6,
            log_prob_threshold=-1.0, word_timestamps=True)[0])
        text = "".join(s.text for s in segs).strip()
        span = f"{segs[0].start:.2f}~{segs[-1].end:.2f}s" if segs else "—"
        print(f"  新合成 + {size:6s} vad={str(vad):5s} span={span}: {text!r}")
        print(f"      相似度 {sim(TEXT, text):.3f}")
print()

print("[三、同一段**原音频**换用不同初始偏置（排除偏置干扰）]")
for prompt in ("", "这是一句普通话的语音指令", "把下载目录里的文件移动"):
    segs = list(med.model.transcribe(
        str(WAV), beam_size=5, language="zh", vad_filter=False,
        condition_on_previous_text=False, no_speech_threshold=0.6,
        log_prob_threshold=-1.0, word_timestamps=True,
        **({"initial_prompt": prompt} if prompt else {}))[0])
    text = "".join(s.text for s in segs).strip()
    print(f"  prompt={prompt!r:28s} → {text!r}  相似度 {sim(TEXT, text):.3f}")
print()
print("=" * 76)
print("怎么读：")
print("  · 若 medium/换偏置/重新合成**都能听对** ⇒ 原音频是**个案坏测激**，")
print("    与档位无关，B4 的对照表里这一句应作为测激剔除项标注")
print("  · 若**全都听不对**（包括重新合成） ⇒ 这句话在 TTS 端就合成得不像，")
print("    属测激问题，同样与档位无关")
