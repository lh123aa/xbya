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


def _matches_phrase(text: str) -> bool:
    """这段转写会不会被产品当成"确认/取消"？（用于测**误触发**）

    ⚠️ **必须用产品的真实判据，不能自己写一套子串匹配**。
    第一版这里写的是 `p in norm`（朴素子串），于是「你好呀」因为含单字「好」
    被报成"被拉成确认语" —— 而那个缺陷在 P4-B5 已经被修掉了
    （`_matches_any` 改成"确认从严、取消从宽"，单字必须整句成立）。
    继续用旧判据会让测量工具**报出一个产品已经不会犯的错**，
    并把它记在"解码偏置的副作用"账上（P4-C2 第一轮就是这么错的）。

    教训与 §16.4 同源：测量工具必须调用**被测系统本身**的判定，而不是复刻一份。
    """
    from agent.providers.router.rule_router import (
        CANCEL_PHRASES,
        CONFIRM_PHRASES,
        RuleRouter,
    )
    from agent.text_norm import to_simplified

    norm = to_simplified(text).lower()
    if not norm:
        return False
    return (RuleRouter._matches_any(norm, CANCEL_PHRASES)
            or RuleRouter._matches_any(norm, CONFIRM_PHRASES, strict=True))


#: 对照句：都不是确认/取消语，用来测"偏置会不会把别的话拉过去"
CONTROLS: list = [
    "你好呀",
    "今天天气怎么样",
    "打开浏览器",
    "讲个笑话",
]


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


def mode_short(asr, rounds: int, voice: str, model_size: str,
               initial_prompt: str = "") -> int:
    """短句确认/取消语命中率（P4-B5）

    为什么单独一个模式：§7.3 五场景里「确定」是**最短**的一句，
    而它决定了"用户批准后到底删不删"。实测「算了」只有 2/4 被正识，
    于是待确认项会一直挂着 —— 用户以为取消了，程序还在等。

    **三类结果必须分开报**，因为它们的修法完全不同：

    | 类别 | 含义 | 该改哪儿 |
    |------|------|---------|
    | HIT | 正识 | — |
    | 空输出 | 整句被丢（VAD 判成静音 / 短词门槛拦掉） | ASR 侧门槛与 VAD |
    | 听错 | 出了字但不对（「散了」「三郎」） | 解码偏置 or 管线侧近似匹配 |

    把"空输出"和"听错"混成一个"命中率"就会看不清该修哪一层 ——
    这与 C4 里"反爬 / 解析器 bug / 网络不通"必须分开是同一条纪律。
    """
    from agent.providers.router.rule_router import CANCEL_PHRASES, CONFIRM_PHRASES

    print(f"\n模式：短句命中率 {rounds} 次/句（P4-B5）  voice={voice}  asr={model_size}")
    if initial_prompt:
        print(f"解码偏置 initial_prompt = {initial_prompt!r}")
    else:
        print("解码偏置 initial_prompt = （无，基线）")
    print("=" * 78)

    kmap = {}
    for p in CONFIRM_PHRASES:
        kmap.setdefault(p, "确定")
    for p in CANCEL_PHRASES:
        kmap.setdefault(p, "取消")
    targets = [(p, kmap[p]) for p in ("算了", "不用了", "停止", "取消", "确定", "确认")]

    tmp = Path(tempfile.mkdtemp(prefix="stim_short_"))
    totals = {"HIT": 0, "空输出": 0, "听错": 0}
    for si, (text, kind) in enumerate(targets):
        hits, empties, wrongs = 0, 0, 0
        outs = []
        for i in range(rounds):
            mp3 = tmp / f"k{si}_{i}.mp3"
            wav = tmp / f"k{si}_{i}.wav"
            _synth(text, mp3, voice)
            _to_wav16k(mp3, wav)
            try:
                hyp = (asr.transcribe(str(wav), initial_prompt=initial_prompt or None)
                       or "").strip()
            except TypeError:
                # 插件尚未支持 initial_prompt（加参数前的旧版本）
                hyp = (asr.transcribe(str(wav)) or "").strip()
            outs.append(hyp)
            if not hyp:
                empties += 1
            elif _hit(hyp, [text]):
                hits += 1
            else:
                wrongs += 1
        totals["HIT"] += hits
        totals["空输出"] += empties
        totals["听错"] += wrongs
        flag = "OK  " if hits == rounds else "不稳"
        print(f"[{flag}] {text!r}（{kind}） 正识 {hits}/{rounds}"
              f"  空输出 {empties}  听错 {wrongs}")
        for o in sorted(set(outs)):
            n = outs.count(o)
            if not o:
                print(f"        | {n}/{rounds} 次 ← **整句被丢掉（空输出）**")
            elif _hit(o, [text]):
                print(f"        | {n}/{rounds} 次 {o!r}")
            else:
                print(f"        | {n}/{rounds} 次 {o!r}   ← **听错了**")

    n = len(targets) * rounds
    print("=" * 78)
    print(f"短句汇总（{n} 次）：正识 {totals['HIT']}/{n}"
          f"，空输出 {totals['空输出']}，听错 {totals['听错']}")

    # ── 副作用测量：偏置会不会把**别的话**也拉到确认/取消词上？──
    #
    # 这一步不能省。解码偏置是"让某些词更容易被解码出来"，
    # 那么被误拉成「确定」的代价是**执行一个用户没批准的操作**（删除走回收站也是删）。
    # 只报"正识率上升"就收工，等于把一个更危险的副作用留在暗处。
    false_trig = 0
    print("\n对照（这些**不是**确认/取消语，绝不该被听成确认/取消）")
    for text in CONTROLS:
        outs = []
        for i in range(rounds):
            mp3 = tmp / f"c{abs(hash(text)) % 10000}_{i}.mp3"
            wav = tmp / f"c{abs(hash(text)) % 10000}_{i}.wav"
            _synth(text, mp3, voice)
            _to_wav16k(mp3, wav)
            try:
                hyp = (asr.transcribe(str(wav), initial_prompt=initial_prompt or None)
                       or "").strip()
            except TypeError:
                hyp = (asr.transcribe(str(wav)) or "").strip()
            outs.append(hyp)
        pulled = [o for o in outs if o and _matches_phrase(o)]
        false_trig += len(pulled)
        mark = "" if not pulled else "   ← **被拉成确认/取消语！**"
        print(f"  {text!r}: {outs}{mark}")
    print(f"\n误触发（非确认语被听成确认/取消语）：{false_trig}/{len(CONTROLS) * rounds}")

    print("→ 空输出要改 ASR 的 VAD/短词门槛；听错要改解码偏置或管线侧近似匹配。")
    print("  两者分开报，才知道该动哪一层。")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="测激稳定性测量（替身抖动 vs 解码确定性）")
    ap.add_argument("--synth", type=int, default=6, help="每句现场合成次数（默认 6）")
    ap.add_argument("--wav-dir", default="", help="改为对目录里的 wav 做重复解码")
    ap.add_argument("--repeat", type=int, default=6, help="每个 wav 重复解码次数（默认 6）")
    ap.add_argument("--short", type=int, default=0,
                    help="短句确认/取消语模式：每句合成 N 次并报命中率（P4-B5）")
    ap.add_argument("--initial-prompt", default="",
                    help="短句模式下传给 ASR 的解码偏置（A/B 用；空=基线）")
    ap.add_argument("--voice", default="zh-CN-XiaoxiaoNeural", help="edge_tts 中文语音")
    ap.add_argument("--model", default="small", help="faster-whisper 模型（须本机已缓存）")
    args = ap.parse_args()

    asr = _asr(args.model)
    if args.wav_dir:
        return mode_wavdir(asr, Path(args.wav_dir), args.repeat)
    if args.short:
        return mode_short(asr, args.short, args.voice, args.model, args.initial_prompt)
    return mode_synth(asr, args.synth, args.voice, args.model)


if __name__ == "__main__":
    raise SystemExit(main())
