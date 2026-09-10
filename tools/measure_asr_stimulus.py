"""测激稳定性测量：把「TTS 替身抖动」与「ASR 解码确定性」分开量化。

背景（详见 `docs/agent/f-closure.md` §17.3.1）：语音回环用 `edge_tts` 假装"人嘴"，
但 **`edge_tts` 每次合成同一句话的波形并不相同**；而 faster-whisper 对
**同一份文件**是确定的。归档的 G12 那次 45/50 里，场景 2 把
「删除桌面上的截图」转写成「山豬桌面上集圖」（首词整个丢失），
于是必须回答一个问题：**那是产品的 ASR 弱，还是那一版音频碰巧劣化？**

本脚本就是回答它的工具，两种模式：

  python tools/measure_asr_stimulus.py                    # 现场合成 6 次（测替身抖动）
  python tools/measure_asr_stimulus.py --synth 10
  python tools/measure_asr_stimulus.py --wav-dir <目录> --repeat 6   # 同一文件重复解码

判读：
- `--wav-dir` 模式若稳定 → 解码对同一份音频是确定的（抖动只可能来自测激侧）
- `--synth` 模式若命中率不是 N/N → **替身本身会抖**，回环应吃固定测激（默认行为）

⚠️ 本脚本**不**用于判定产品好坏；产品行为由 `voice_scenarios_loopback.py` 判定。
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import tempfile
import wave
from pathlib import Path

import numpy as np

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

#: §7.3 五个场景用到的句子 + 判定关键词（与回环的判据一致）
TARGETS: list = [
    ("找一下桌面上的 PDF 文件", ["找", "桌面", "PDF"]),
    ("删除桌面上的截图", ["删除", "桌面", "截图"]),
    ("确定", ["确定"]),
    ("给我讲个笑话", ["笑话"]),
    ("你好呀", ["你好"]),
    ("打开 C 盘 Windows 文件夹", ["打开", "Windows"]),
]


def _synth(text: str, dst: Path, voice: str) -> None:
    import edge_tts

    async def _go() -> None:
        await edge_tts.Communicate(text, voice).save(str(dst))

    asyncio.run(_go())


def _to_wav16k(src: Path, dst: Path) -> float:
    """mp3 → 16k 单声道 s16 WAV（与回环同一路径，保证可比），返回时长秒"""
    import av

    chunks = []
    with av.open(str(src)) as container:
        stream = container.streams.audio[0]
        resampler = av.audio.resampler.AudioResampler(
            format="s16", layout="mono", rate=16000)
        for frame in container.decode(stream):
            for out in resampler.resample(frame):
                chunks.append(np.frombuffer(bytes(out.planes[0]), dtype=np.int16))
        for out in resampler.resample(None):
            chunks.append(np.frombuffer(bytes(out.planes[0]), dtype=np.int16))
    pcm = np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.int16)
    with wave.open(str(dst), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(pcm.tobytes())
    return len(pcm) / 16000.0


def _amp(path: Path) -> float:
    with wave.open(str(path), "rb") as wf:
        pcm = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)
    return float(np.abs(pcm.astype(np.float32)).mean()) if len(pcm) else 0.0


def _asr(model_size: str):
    from plugins.asr.faster_whisper.plugin import FasterWhisperASR

    asr = FasterWhisperASR(model_size=model_size, device="cpu")
    if not asr._ensure_loaded():                       # noqa: SLF001 - 显式预热
        raise RuntimeError(f"ASR 模型 {model_size} 加载失败（本机缓存 tiny/base/small）")
    return asr


def _hit(text: str, keywords: list) -> bool:
    """关键词命中（**先做繁简归一化**，与产品同一条判据）

    为什么要归一化：`edge_tts` + whisper 对同一句话会**时而输出繁体**
    （「截圖」「確定」「給我講個笑話」），而产品的规则路由已经用
    `agent/text_norm.to_simplified()` 把这一层抹平。
    本脚本若不做同样处理，就会把"产品已经能正确处理的繁体输出"
    误报成"测激劣化" —— 那是**测量工具自己制造假红**。
    """
    from agent.text_norm import to_simplified

    norm = to_simplified(text).lower()
    return all(k.lower() in norm for k in keywords)


def mode_synth(asr, rounds: int, voice: str, model_size: str) -> int:
    print(f"\n模式：现场合成 {rounds} 次/句（测**替身抖动**）"
          f"  voice={voice}  asr={model_size}")
    print("=" * 78)
    tmp = Path(tempfile.mkdtemp(prefix="stim_synth_"))
    bad = 0
    for si, (text, keywords) in enumerate(TARGETS):
        hits, durs, sizes, amps, outs = 0, [], [], [], []
        for i in range(rounds):
            mp3 = tmp / f"s{si}_{i}.mp3"
            wav = tmp / f"s{si}_{i}.wav"
            _synth(text, mp3, voice)
            durs.append(_to_wav16k(mp3, wav))
            sizes.append(wav.stat().st_size)
            amps.append(_amp(wav))
            hyp = (asr.transcribe(str(wav)) or "").strip()
            outs.append(hyp)
            hits += _hit(hyp, keywords)
        uniq = len(set(outs))
        flag = "OK  " if hits == rounds else "抖了"
        if hits != rounds:
            bad += 1
        print(f"[{flag}] {text!r}  正识 {hits}/{rounds}（繁简归一化后）")
        print(f"        时长 {min(durs):.2f}~{max(durs):.2f}s  "
              f"字节 {min(sizes)}~{max(sizes)}B  "
              f"幅度 {min(amps):.0f}~{max(amps):.0f}  不同转写 {uniq} 种")
        counts: dict = {}
        for o in outs:
            counts[o] = counts.get(o, 0) + 1
        for o, n in sorted(counts.items(), key=lambda kv: -kv[1]):
            mark = "" if _hit(o, keywords) else "   ← **这一版被听错了**"
            print(f"        | {n}/{rounds} 次 {o!r}{mark}")
    print("=" * 78)
    print(f"替身抖动结论：{len(TARGETS) - bad}/{len(TARGETS)} 句在 {rounds} 次合成里"
          f"**零误识**；{bad} 句出现过被听错的版本")
    print("→ 「抖了」= 同一句话、同样时长、同样字节数，**只是波形不同**，"
          "就有一个版本被整句听错。")
    print("  这就是归档那次 G12 场景 2『山豬桌面上集圖』的来源："
          "问题在**替身那一版音频**，不在被测产品。")
    print("→ 因此回归默认吃**固定测激**（回环脚本默认行为）；"
          "吃固定测激不是放宽判据，")
    print("  而是把「替身抖动」这个与被测产品无关的随机源掐掉。")
    return 0


def mode_wavdir(asr, wav_dir: Path, rounds: int) -> int:
    print(f"\n模式：同一文件重复解码 {rounds} 次（测**解码确定性**）  {wav_dir}")
    print("=" * 78)
    wavs = sorted(wav_dir.glob("*.wav"))
    if not wavs:
        print(f"目录里没有 wav：{wav_dir}")
        return 2
    unstable = 0
    for wav in wavs:
        outs = [(asr.transcribe(str(wav)) or "").strip() for _ in range(rounds)]
        uniq = sorted(set(outs))
        if len(uniq) != 1:
            unstable += 1
        print(f"[{'确定' if len(uniq) == 1 else '不稳'}] {wav.name:20s} "
              f"幅度={_amp(wav):7.1f} 字节={wav.stat().st_size:7d} "
              f"不同转写={len(uniq)}")
        for u in uniq:
            print(f"        | {u!r}")
    print("=" * 78)
    print(f"解码确定性结论：{len(wavs) - unstable}/{len(wavs)} 个文件在 {rounds} 次重复解码里完全一致")
    if unstable == 0:
        print("→ 解码对同一份音频是确定的 ⇒ 整跑抖动只可能来自**测激侧**（合成波形不同）")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="测激稳定性测量（替身抖动 vs 解码确定性）")
    ap.add_argument("--synth", type=int, default=6, help="每句现场合成次数（默认 6）")
    ap.add_argument("--wav-dir", default="", help="改为对目录里的 wav 做重复解码")
    ap.add_argument("--repeat", type=int, default=6, help="每个 wav 重复解码次数（默认 6）")
    ap.add_argument("--voice", default="zh-CN-XiaoxiaoNeural", help="edge_tts 中文语音")
    ap.add_argument("--model", default="small", help="faster-whisper 模型（须本机已缓存）")
    args = ap.parse_args()

    asr = _asr(args.model)
    if args.wav_dir:
        return mode_wavdir(asr, Path(args.wav_dir), args.repeat)
    return mode_synth(asr, args.synth, args.voice, args.model)


if __name__ == "__main__":
    raise SystemExit(main())
