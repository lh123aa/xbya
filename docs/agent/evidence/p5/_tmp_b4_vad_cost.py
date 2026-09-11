# -*- coding: utf-8 -*-
"""P5-B4 探针 8：`vad_filter` 的**代价**是什么？（关掉它会不会诱发误听）

第八节查出 `vad_filter=True` 会在个别长音频上静默截断。
那么"直接把它关掉"是不是就行？**不能凭直觉答** ——
VAD 的本职工作是**过滤纯噪音段**（源码注释就写着"过滤纯噪音段"），
关掉它有可能让安静的片段被解码出幻觉文本（Whisper 在静音上编词是出了名的）。

所以必须量出这个 trade-off：
  · 合成几段**不该产生命令**的音频：纯静音、低幅白噪、环境噪声
  · 两档 × vad on/off，看是否吐出文本
  · 若 vad=off 在这些音频上开始"说话" ⇒ 关掉它是有代价的，
    不能简单改默认值，得先有别的防线（音量门槛/VAD 前置在采集端）
  · 若 vad=off 在这些音频上仍为空 ⇒ 关掉的代价在本机这批测激上看不出来

⚠️ 边界：这里用的是**合成噪音**，不是真实环境录音。真实房间噪声
（风扇/空调/键盘）的谱形不同，本探针**不能**替代真机测试（P5-C2）。
"""
import sys
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))

from plugins.asr.faster_whisper.plugin import FasterWhisperASR   # noqa: E402

AUDIO = ROOT / "docs" / "agent" / "evidence" / "p5" / "_tmp_b4_audio"
NOISE = AUDIO / "noise"
NOISE.mkdir(exist_ok=True)

RATE = 16000
rng = np.random.default_rng(20260911)          # 固定种子：可复跑


def write_wav(name: str, pcm: np.ndarray) -> Path:
    p = NOISE / name
    with wave.open(str(p), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(RATE)
        wf.writeframes(pcm.astype(np.int16).tobytes())
    return p


def tone(seconds: float, amp: float) -> np.ndarray:
    t = np.arange(int(seconds * RATE)) / RATE
    return amp * np.sin(2 * np.pi * 220 * t)


CASES = {
    "silence_5s.wav": np.zeros(int(5 * RATE)),
    "whitenoise_low_5s.wav": rng.normal(0, 30, int(5 * RATE)).clip(-200, 200),
    "whitenoise_mid_5s.wav": rng.normal(0, 300, int(5 * RATE)).clip(-3000, 3000),
    "hum_50hz_5s.wav": rng.normal(0, 200, int(5 * RATE)).clip(-3000, 3000)
                        + 400 * np.sin(2 * np.pi * 50 * np.arange(int(5 * RATE)) / RATE),
    "tone_220hz_5s.wav": tone(5, 3000),
}

paths = {n: write_wav(n, pcm) for n, pcm in CASES.items()}

print("=" * 88)
print("P5-B4 探针 8：关掉 vad_filter 的代价 —— 它会不会开始「听见」噪音？")
print("=" * 88)
print()
print("测激（合成，非真实录音）：")
for n, p in paths.items():
    with wave.open(str(p)) as wf:
        d = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)
    rms = float(np.sqrt(np.mean(d.astype(np.float64) ** 2)))
    print(f"  {n:26s} RMS={rms:8.1f}  峰值={int(np.max(np.abs(d))):6d}")
print()

models = {}
for size in ("small", "medium"):
    a = FasterWhisperASR(model_size=size, device="cpu", compute_type="int8")
    a._ensure_loaded()
    models[size] = a

print("| 测激 | small vad=on | small vad=off | medium vad=on | medium vad=off |")
print("|------|--------------|---------------|---------------|----------------|")
results = {}
for n, p in paths.items():
    cells = []
    for size in ("small", "medium"):
        for vad in (True, False):
            segs = list(models[size].model.transcribe(
                str(p), beam_size=5, language="zh", vad_filter=vad,
                condition_on_previous_text=False, no_speech_threshold=0.6,
                log_prob_threshold=-1.0, word_timestamps=True)[0])
            text = "".join(s.text for s in segs).strip()
            results[(n, size, vad)] = text
            cells.append("—（空）" if not text else f"**{text[:18]}**")
    print(f"| {n} | " + " | ".join(cells) + " |")
print()

# 关键对比：vad off 是否引入了原本没有的输出
introduced = []
for (n, size, vad), text in results.items():
    if vad and not text:
        off = results.get((n, size, False), "")
        if off:
            introduced.append((n, size, off))
print("=" * 88)
print("归因")
print("=" * 88)
print()
if introduced:
    print(f"**关掉 VAD 后，有 {len(introduced)} 格从「空」变成了「有文本」**：")
    for n, size, off in introduced:
        print(f"  · {n} / {size}：{off!r}")
    print()
    print("⇒ 关掉 VAD **有代价**：噪音被解码成文本了。")
    print("  因此**不能**简单把 `vad_filter` 改成 False —— 那会把"
          "「长句被截断」换成「噪音被听成话」。")
else:
    print("本批合成噪音上，vad=on 与 vad=off **输出一致**（都为空）。")
    print("⇒ 在本机这批测激上看不出关掉 VAD 的代价 —— 但这**不等于**没有代价，")
    print("  只说明本批合成噪音不够刁钻（真实环境噪声谱形不同，需真机验证）。")
print()
print("⚠️ 边界：这些是**合成噪音**，不是真实房间录音。结论只覆盖本批测激。")
