# -*- coding: utf-8 -*-
"""P5-B4 探针：那一句「四个模型全都在同一个词上截断」到底是什么造成的？

现象（见 asr_long_sentence.txt 第三节）：
  「把下载目录里所有后缀是安装包的文件都移动到软件归档这个文件夹里面去」
  四格**全部**只转写出前 14 个字（相似度都恰好 0.435），后半句整段消失。

四个模型在同一位置、以同一方式失败，**不像模型能力差异**，更像测激或前处理问题。
在把"medium 也不行"写进结论之前必须先分清是哪一个：

  A. **合成/音频被截断** —— WAV 本身就只有前半句
  B. **`_enhance_audio` 的静音裁剪/VAD 把后半句切了** —— 原 WAV 完整，
     但插件预处理后变短
  C. **模型真的听不全** —— 两条路径都拿到完整音频，仍然只转出前半句

判据就是三方对比：原 WAV 时长 vs 增强后 WAV 时长 vs 转写文本。
不猜，直接量。
"""
import io
import sys
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))

from plugins.asr.faster_whisper.plugin import FasterWhisperASR   # noqa: E402

AUDIO = ROOT / "docs" / "agent" / "evidence" / "p5" / "_tmp_b4_audio"

TARGET = "把下载目录里所有后缀是安装包的文件都移动到软件归档这个文件夹里面去"


def dur(path: Path) -> float:
    with wave.open(str(path)) as wf:
        return wf.getnframes() / wf.getframerate()


print("=" * 76)
print("P5-B4 探针：同一句四格全截断 —— 是测激、前处理、还是模型？")
print("=" * 76)
print()

# 找到这一句的音频文件（按内容找，不按序号猜）
rows = __import__("json").loads(
    (AUDIO / "asr_long_sentence_rows.json").read_text(encoding="utf-8"))
target_row = next(r for r in rows if r["text"] == TARGET)
md5 = target_row["md5"]
wav = None
for p in sorted(AUDIO.glob("*.wav")):
    import hashlib
    if hashlib.md5(p.read_bytes()).hexdigest()[:12] == md5:
        wav = p
        break
assert wav is not None, "找不到这一句的音频"
print(f"音频文件：{wav.name}（MD5 {md5}）")
print(f"原句：{TARGET}")
print()

# ── 判据 1：原始 WAV 的时长与样点数 ──
raw_dur = dur(wav)
with wave.open(str(wav)) as wf:
    frames = wf.getnframes()
    rate = wf.getframerate()
print(f"[原始 WAV] 时长 {raw_dur:.2f}s（{frames} 样点 @ {rate}Hz）")
print(f"           一句话 {len(TARGET)} 个字 → 约 "
      f"{raw_dur / len(TARGET):.3f}s/字（正常中文语速约 0.15~0.25s/字）")
if raw_dur < len(TARGET) * 0.10:
    print("           ⚠️ 明显偏短 —— 合成端可能就截断了")
else:
    print("           ✔ 时长与字数相称，**合成端没有截断**")
print()

# ── 判据 2：插件预处理后的 WAV ──
asr = FasterWhisperASR(model_size="small", device="cpu", compute_type="int8")
asr._ensure_loaded()

enh = asr._enhance_audio(str(wav))
print(f"[增强后 WAV] _enhance_audio() 返回：{enh!r}")
if enh and Path(enh) is not None and str(enh) != str(wav):
    try:
        ed = dur(Path(enh))
        print(f"             时长 {ed:.2f}s（原 {raw_dur:.2f}s，"
              f"保留 {ed / raw_dur:.0%}）")
        if ed < raw_dur * 0.6:
            print("             ⚠️ 前处理砍掉了大半音频 → 截断来自预处理")
        else:
            print("             ✔ 前处理基本保留了音频")
    except Exception as e:                                   # noqa: BLE001
        print(f"             读增强后文件失败：{type(e).__name__}: {e}")
else:
    print("             与前处理无关（返回 None 或原路径 = 降级为原音频）")
print()

# ── 判据 3：直接喂原始 WAV 与喂增强后 WAV，转写是否不同 ──
print("[转写对比]")
h_raw = asr.transcribe(str(wav))
print(f"  走产品正门 transcribe(raw) → {h_raw!r}")
if enh and str(enh) != str(wav):
    h_enh = asr.transcribe(str(enh))
    print(f"  直接喂增强后音频       → {h_enh!r}")
    print(f"  两者相同？{h_raw == h_enh}")
print()

# ── 判据 4：把音频切成前后两半，看后半句是否"能被听见" ──
print("[切片试验] 若后半段音频里**有内容**，单独喂它应当也有转写")
with wave.open(str(wav)) as wf:
    params = wf.getparams()
    data = wf.readframes(params.nframes)
import numpy as np                                               # noqa: E402
pcm = np.frombuffer(data, dtype=np.int16)
mid = len(pcm) // 2
for label, seg in (("前半段", pcm[:mid]), ("后半段", pcm[mid:])):
    out = AUDIO / f"slice_{label}.wav"
    with wave.open(str(out), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(params.framerate)
        wf.writeframes(seg.tobytes())
    h = asr.transcribe(str(out))
    print(f"  {label}（{len(seg) / params.framerate:.2f}s）→ {h!r}")
print()
print("=" * 76)
print("怎么读这份输出：")
print("  · 原始 WAV 完整 + 切片后半段**有转写** ⇒ 音频里有后半句，")
print("    那么四格全截断就不是「测激没声音」，而是**解码/预处理**层面的事")
print("  · 原始 WAV 完整 + 切片后半段**空** ⇒ 后半句本来就没合成出来，")
print("    这是 TTS 端的问题，与 ASR 档位无关")
