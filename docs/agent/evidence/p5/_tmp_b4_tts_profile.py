# -*- coding: utf-8 -*-
"""P5-B4 探针 5：**两句都听不出来**的那句，edge-tts 到底合成了什么？

探针 4 的结论把问题指向了测激：同一句话
  · 原合成音频：small 0.435 / medium 0.800（**medium 也没听全**）
  · **重新合成**后：small 0.576 / medium **1.000**

「重新合成同一句话，medium 就从 0.800 跳到 1.000」——这只有在
"原音频本身有问题"时才说得通。所以要直接看音频：

判据一（能不能看见）：把两段音频都转成**频谱图文本**是不现实的，
  但可以量几个能区分的量：**时长、字数/秒、静音段占比、过零率、能量包络**。
  edge-tts 合成失败常见形态是**后半句没念**或**念成别的音**，这两者
  在时长和能量包络上是能看出来的。

判据二（用模型交叉验证）：同音色换**另一种语速/另一段文本长度**再合成，
  看是否只有这一句坏 —— 即"是不是这段话本身触发了 TTS 的问题"。

顺带留档：`edge_tts` 的合成是**联网服务**，所以"同一句话两次合成不同"
既可能是网络抖动，也可能是服务端模型差异。这条写进边界声明。
"""
import asyncio
import hashlib
import sys
import wave
from pathlib import Path

import av
import edge_tts
import numpy as np

ROOT = Path(__file__).resolve().parents[4]
AUDIO = ROOT / "docs" / "agent" / "evidence" / "p5" / "_tmp_b4_audio"
TEXT = "把下载目录里所有后缀是安装包的文件都移动到软件归档这个文件夹里面去"
VOICE = "zh-CN-XiaoxiaoNeural"


def synth(text: str, tag: str) -> Path:
    mp3 = AUDIO / f"{tag}.mp3"
    wav = AUDIO / f"{tag}.wav"

    async def _g():
        await edge_tts.Communicate(text, VOICE).save(str(mp3))

    asyncio.run(_g())
    chunks = []
    with av.open(str(mp3)) as c:
        st = c.streams.audio[0]
        rs = av.audio.resampler.AudioResampler(format="s16", layout="mono",
                                              rate=16000)
        for fr in c.decode(st):
            for o in rs.resample(fr):
                chunks.append(np.frombuffer(bytes(o.planes[0]), dtype=np.int16))
        for o in rs.resample(None):
            chunks.append(np.frombuffer(bytes(o.planes[0]), dtype=np.int16))
    pcm = np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.int16)
    with wave.open(str(wav), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(pcm.tobytes())
    return wav


def profile(wav: Path) -> dict:
    with wave.open(str(wav)) as wf:
        rate = wf.getframerate()
        pcm = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)
    dur = len(pcm) / rate
    win = rate // 20                       # 50ms 窗口
    rms = np.array([float(np.sqrt(np.mean(pcm[i:i + win].astype(np.float64) ** 2)))
                    for i in range(0, max(1, len(pcm) - win), win)])
    thr = max(30.0, rms.max() * 0.05)
    silent = float(np.mean(rms < thr)) if len(rms) else 1.0
    zcr = float(np.mean(np.abs(np.diff(np.sign(pcm.astype(np.int32)))) > 0))
    return {"dur": dur, "chars_per_sec": len(TEXT) / dur if dur else 0,
            "silent_ratio": silent, "rms_mean": float(rms.mean()),
            "rms_max": float(rms.max()), "zcr": zcr,
            "bytes": wav.stat().st_size,
            "md5": hashlib.md5(wav.read_bytes()).hexdigest()[:12]}


print("=" * 78)
print("P5-B4 探针 5：同一句话多次合成的音频画像对比")
print("=" * 78)
print()
print(f"文本（{len(TEXT)} 字）：{TEXT}")
print()

originals = {"s11（B4 表用的原音频）": AUDIO / "s11.wav",
             "s11_alt（探针4 重新合成）": AUDIO / "s11_alt.wav"}
profiles = {}
for label, p in originals.items():
    if p.is_file():
        profiles[label] = profile(p)

print("### 已有的两段")
print()
print("| 音频 | 时长(s) | 字/秒 | 静音占比 | RMS 均值 | RMS 峰值 | 过零率 | MD5 |")
print("|------|--------|-------|---------|---------|---------|-------|-----|")
for label, pr in profiles.items():
    print(f"| {label} | {pr['dur']:.2f} | {pr['chars_per_sec']:.2f} | "
          f"{pr['silent_ratio']:.1%} | {pr['rms_mean']:.0f} | "
          f"{pr['rms_max']:.0f} | {pr['zcr']:.3f} | `{pr['md5']}` |")
print()

print("### 再合成 3 次，看是「偶发坏」还是「每次都不一样」")
print()
print("| 次数 | 时长(s) | 字/秒 | 静音占比 | MD5 |")
print("|------|--------|-------|---------|-----|")
for i in range(1, 4):
    w = synth(TEXT, f"s11_repeat{i}")
    pr = profile(w)
    print(f"| #{i} | {pr['dur']:.2f} | {pr['chars_per_sec']:.2f} | "
          f"{pr['silent_ratio']:.1%} | `{pr['md5']}` |")
print()

print("### 把这句话的**后半句**单独合成，看它自己能不能合对")
print()
tail = "的文件都移动到软件归档这个文件夹里面去"
w = synth(tail, "s11_tail")
pr = profile(w)
print(f"- 后半句（{len(tail)} 字）：时长 {pr['dur']:.2f}s、"
      f"{pr['chars_per_sec']:.2f} 字/秒、静音占比 {pr['silent_ratio']:.1%}"
      f"、MD5 `{pr['md5']}`")
print()

print("=" * 78)
print("边界声明")
print("=" * 78)
print()
print("`edge_tts` 是**联网合成服务**。所以「同一句话两次合成音频不同」这一现象，")
print("我**无法**在本机分辨它来自：网络抖动 / 服务端模型负载 / 这段话本身触发了")
print("某种异常路径。本探针只能如实记录「确实不同」以及「不同到什么程度」。")
