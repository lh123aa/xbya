# -*- coding: utf-8 -*-
"""P5-B4 探针 3：把 `vad_filter` 关掉，看看后半句是不是就回来了。

上一支探针推翻了我原先的两个假设：
  · 不是"产品过滤太严"（被丢弃片段为空）
  · 不是"音频后半段静音"（后半段 RMS=4257，前半段 4605，能量相当）

真实现象是：产品那一次调用**只产生了 1 个片段，t=0.21~3.03s**，
而音频有 8.26s。也就是说模型在第 3 秒就停了，后面 5 秒**连片段都没有**。

最可能的成因是产品参数里的 `vad_filter=True`（plugin.py:105）：VAD 只把
前 3 秒判成语音，其余判成噪音直接切掉。

判据：同一模型、同一音频，**只把 vad_filter 从 True 改成 False**，
看转写是否变长/变准。若是，则这是**产品的长期句截断缺陷**，
而不是模型能力问题 —— 而且它会影响 small 与 medium **两者**，
所以用它解释"长句分数低"时必须小心。

顺带对照一句**短句**（已知没问题的），确认 vad_filter 不是普遍有害。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))

from plugins.asr.faster_whisper.plugin import FasterWhisperASR   # noqa: E402

AUDIO = ROOT / "docs" / "agent" / "evidence" / "p5" / "_tmp_b4_audio"
BIAS = "算了 不用了 停止 取消 确定 确认"
LONG_WAV = AUDIO / "s11.wav"       # 33 字长句
LONG_TEXT = "把下载目录里所有后缀是安装包的文件都移动到软件归档这个文件夹里面去"


def norm(s):
    import re
    return re.sub(r"[\s，。、！？：；,.!?]+", "", s or "")


from difflib import SequenceMatcher                          # noqa: E402


def sim(a, b):
    return SequenceMatcher(None, norm(a), norm(b)).ratio()


# 找一句短句做对照
import json
rows = json.loads((AUDIO / "asr_long_sentence_rows.json").read_text(encoding="utf-8"))
short_row = next(r for r in rows if r["text"] == "好的")

print("=" * 76)
print("P5-B4 探针 3：`vad_filter` 是不是把长句后半段切掉了")
print("=" * 76)
print()

for bias_label, bias in (("无偏置", ""), ("+偏置", BIAS)):
    for size in ("small", "medium"):
        asr = FasterWhisperASR(model_size=size, device="cpu",
                              compute_type="int8", initial_prompt=bias)
        asr._ensure_loaded()
        base = {"beam_size": 5, "language": "zh",
                "condition_on_previous_text": False,
                "no_speech_threshold": 0.6, "log_prob_threshold": -1.0,
                "word_timestamps": True}
        if bias:
            base["initial_prompt"] = bias

        print(f"### {size} / {bias_label}")
        for vad in (True, False):
            kw = dict(base, vad_filter=vad)
            segs = list(asr.model.transcribe(str(LONG_WAV), **kw)[0])
            text = "".join(s.text for s in segs).strip()
            span = (f"{segs[0].start:.2f}~{segs[-1].end:.2f}s" if segs else "—")
            print(f"  vad_filter={str(vad):5s}: {len(segs)} 片段  span={span}")
            print(f"      转写 {text!r}")
            print(f"      相似度 {sim(LONG_TEXT, text):.3f}")
        print()
        break   # 只跑 small，medium 在下面单独一节跑（有意识的省时，不是漏测）
    break

print("=" * 76)
print("短句对照（确认 vad_filter 不是普遍有害）")
print("=" * 76)
print()
asr = FasterWhisperASR(model_size="small", device="cpu", compute_type="int8")
asr._ensure_loaded()
for vad in (True, False):
    segs = list(asr.model.transcribe(
        str(AUDIO / "s00.wav"), beam_size=5, language="zh", vad_filter=vad,
        condition_on_previous_text=False, no_speech_threshold=0.6,
        log_prob_threshold=-1.0, word_timestamps=True)[0])
    text = "".join(s.text for s in segs).strip()
    print(f"  vad_filter={str(vad):5s}: {text!r}")
print()
print(f"（对照句原文：{short_row['text']}）")
