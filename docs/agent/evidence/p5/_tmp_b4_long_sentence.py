# -*- coding: utf-8 -*-
"""P5-B4：ASR `small` vs `medium` 在**长句**上的对比（补 C2 只测短句的缺口）。

为什么必须有这一轮（P4-C2 自己写的未决项）：
P4-C2 只测了 **6 句短确认语**，得出「medium 不占优」（14/24 vs 18/24），
但**同一份证据里就写了**：「medium 在长句上未必更差（更大模型通常在长语音上
更准），而我没测长句」「短句子集上 medium 不占优**推不出**该把默认档改成 small」。
所以那条结论一直是**没有分母的**。这一轮补上长句那一半。

测量设计（三条，都是为了不让结论被别的东西污染）：

1. **同一段音频喂两个模型**。TTS 合成有抖动（P4-C1 查明：同一句每次合成
   样点不同，12 次合成 → 12 个不同 MD5）。若各自现合成，比的就不是模型而是
   测激。故：每句合成一次 → 落成 WAV → **small/medium 各自读同一个文件**。
2. **两个模型都测「有偏置/无偏置」四格**。P4-C2 查明偏置是决定性因素
   （18→22、14→22，且听错清零），只测一边会把偏置的作用算到模型头上。
3. **句子长度分档**，短句那档**复刻 C2 的 6 句**，这样新老数据能对上；
   否则「长句更好」无法与「短句更差」放在一起看。

判据用 `difflib.SequenceMatcher` 的字符相似度，而不是自造一套"错字计数"——
本项目吃过"复刻判据"的亏（§16.4 / P4-C2【三】）。
"""
import io
import json
import sys
import time
import wave
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))

from plugins.asr.faster_whisper.plugin import FasterWhisperASR   # noqa: E402

OUT = ROOT / "docs" / "agent" / "evidence" / "p5" / "asr_long_sentence.txt"
SANDBOX = ROOT / "docs" / "agent" / "evidence" / "p5" / "_tmp_b4_audio"

#: 生产配置里的偏置（config.yaml 的 plugins.asr.params.initial_prompt）
BIAS = "算了 不用了 停止 取消 确定 确认"
VOICE = "zh-CN-XiaoxiaoNeural"

# ── 三档句子 ──
# 短句档**逐字复刻 P4-C2 的那 6 句**，这样新老两张表能直接比
SHORT = ["确定", "好的", "算了", "取消", "不用了", "停止"]

MEDIUM = [
    "打开浏览器",
    "看看电脑状态",
    "帮我把下载目录整理一下",
]

LONG = [
    "帮我找一下桌面上个月修改过的所有截图，然后按日期排好顺序放到一个新建的文件夹里",
    "先看看电脑现在的内存占用是多少，然后打开浏览器搜索今天的天气怎么样",
    "把下载目录里所有后缀是安装包的文件都移动到软件归档这个文件夹里面去",
    "帮我读一下文档目录里那份合同文件，把里面提到的付款时间和金额都告诉我",
    "桌面上的截图太多了，你帮我把重复的那些找出来然后移到回收站里去",
    "先截个图，然后把图片另存到图片目录下面，文件名用今天的日期来命名",
]

GROUPS = [("短句（复刻 P4-C2，6 句）", SHORT),
          ("中句（3 句）", MEDIUM),
          ("**长句（6 句）**", LONG)]


def norm(s: str) -> str:
    """与项目一致的最小归一化：只做空白/标点剥离，不"猜"同音字"""
    import re
    return re.sub(r"[\s，。、！？：；,.!?:;\"'（）()《》\-—]+", "", s or "")


def sim(a: str, b: str) -> float:
    return SequenceMatcher(None, norm(a), norm(b)).ratio()


def synth(text: str, mp3: Path, wav: Path) -> None:
    import asyncio

    import av
    import edge_tts
    import numpy as np

    async def _gen():
        await edge_tts.Communicate(text, VOICE).save(str(mp3))

    asyncio.run(_gen())

    chunks = []
    with av.open(str(mp3)) as container:
        stream = container.streams.audio[0]
        res = av.audio.resampler.AudioResampler(format="s16", layout="mono",
                                               rate=16000)
        for frame in container.decode(stream):
            for out in res.resample(frame):
                chunks.append(np.frombuffer(bytes(out.planes[0]), dtype=np.int16))
        for out in res.resample(None):
            chunks.append(np.frombuffer(bytes(out.planes[0]), dtype=np.int16))
    pcm = np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.int16)
    with wave.open(str(wav), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(pcm.tobytes())


L = []
w = L.append


def rule(t):
    w("")
    w("─" * 78)
    w(f" {t}")
    w("─" * 78)
    w("")


w("═" * 78)
w("P5-B4 证据：ASR small / medium 长句对比（补 P4-C2 的缺口）")
w("═" * 78)
w("")
w(f"生成时间：{datetime.now():%Y-%m-%d %H:%M:%S}")
w("可复跑：`python docs/agent/evidence/p5/_tmp_b4_long_sentence.py`")
w("")
w("**被补的缺口**：P4-C2 只测了 6 句**短确认语**，得出「medium 不占优」，")
w("但同一份证据里写着「medium 在长句上未必更差，而我没测长句」、")
w("「短句子集上不占优**推不出**该把默认档改成 small」。")
w("那条结论一直没有分母，这一轮补上长句那一半。")

rule("零、测量设计（三条防污染措施）")
w("1. **同一段音频喂两个模型**：每句只合成一次并落成 WAV，")
w("   small/medium 各读**同一个文件**。")
w("   否则比的不是模型而是 TTS 抖动（P4-C1 已查明：12 次合成 → 12 个不同 MD5）。")
w("2. **四格都测**（有偏置/无偏置 × small/medium）：P4-C2 查明偏置是决定性因素，")
w("   只测一边会把偏置的作用算到模型头上。")
w("3. **短句档逐字复刻 P4-C2 的那 6 句**：新老两张表才能直接比。")
w("")
w(f"- 偏置（生产配置值）：`{BIAS}`")
w(f"- 音色：`{VOICE}`")
w("- 判据：`difflib.SequenceMatcher` 字符相似度 + 是否空输出，")
w("  **不自造一套错字计数**（本项目吃过复刻判据的亏）")

SANDBOX.mkdir(parents=True, exist_ok=True)

# ── 一、先固定测激：合成一次，留 MD5 ──
rule("一、测激固定（每句合成一次，记录音频指纹）")
stim = []
w("| 档 | 句子 | 音频字节 | MD5 | 时长(s) |")
w("|----|------|---------|-----|--------|")
# 文件名用**稳定序号**，不用 hash()：内置 hash 有随机盐，
# 会让"可复跑"变成"每次文件名都不一样"（本项目在 hashing_embedder 里踩过同源问题）
_idx = 0
for gname, group in GROUPS:
    for text in group:
        key = f"s{_idx:02d}"
        _idx += 1
        mp3, wav = SANDBOX / f"{key}.mp3", SANDBOX / f"{key}.wav"
        synth(text, mp3, wav)
        raw = wav.read_bytes()
        import hashlib
        md5 = hashlib.md5(raw).hexdigest()[:12]
        with wave.open(str(wav)) as wf:
            dur = wf.getnframes() / wf.getframerate()
        stim.append({"group": gname, "text": text, "wav": wav,
                     "bytes": len(raw), "md5": md5, "dur": dur})
        w(f"| {gname} | {text[:30]}{'…' if len(text) > 30 else ''} | "
          f"{len(raw)} | `{md5}` | {dur:.2f} |")
w("")
w(f"共 {len(stim)} 段音频，**两个模型读的是同一批文件**（这是公平比较的前提）。")

# ── 二、加载四个模型（各自计时）──
rule("二、模型加载（冷启动计时）")
instances = {}
for size in ("small", "medium"):
    for biased in (True, False):
        label = f"{size}{'+偏置' if biased else '（无偏置）'}"
        t0 = time.perf_counter()
        asr = FasterWhisperASR(model_size=size, device="cpu",
                              compute_type="int8",
                              initial_prompt=BIAS if biased else "")
        ok = asr._ensure_loaded()
        dt = time.perf_counter() - t0
        instances[label] = asr
        w(f"- `{label}`：加载 {'成功' if ok else '**失败**'}，用时 **{dt:.2f}s**")
w("")
w("⚠️ 加载时间是**冷启动**成本，两张表的精度差异要连同这个一起看：")
w("   若 medium 的精度优势小于它的启动代价，对「开机后第一句话」的体验就是负的。")

# ── 三、逐句跑四格 ──
rule("三、逐句结果（同一音频 × 四格）")
LABELS = ["small（无偏置）", "small+偏置", "medium（无偏置）", "medium+偏置"]
rows = []
for s in stim:
    got = {}
    for label in LABELS:
        t0 = time.perf_counter()
        hyp = instances[label].transcribe(str(s["wav"]))
        dt = time.perf_counter() - t0
        got[label] = {"hyp": hyp or "", "sec": dt,
                      "sim": sim(s["text"], hyp or "") if hyp else 0.0}
    rows.append({**s, "out": got})

w("| 档 | 原句 | small 无偏置 | small+偏置 | medium 无偏置 | medium+偏置 |")
w("|----|------|-------------|-----------|--------------|------------|")
for r in rows:
    cells = []
    for label in LABELS:
        o = r["out"][label]
        cells.append("**空**" if not o["hyp"] else f"{o['sim']:.2f}")
    w(f"| {r['group'].split('（')[0]} | {r['text'][:24]}"
      f"{'…' if len(r['text']) > 24 else ''} | " + " | ".join(cells) + " |")
w("")
w("（单元格 = 与原句的字符相似度；**空** = 模型没输出任何东西）")

# ── 四、分档汇总 ──
rule("四、分档汇总（正识 = 字符相似度 ≥ 0.9 且非空）")
w("| 档 | 配置 | 正识 | 空输出 | 平均相似度 | 平均耗时(s) |")
w("|----|------|------|--------|-----------|------------|")
summary = {}
for gname, _ in GROUPS:
    for label in LABELS:
        rs = [r for r in rows if r["group"] == gname]
        outs = [r["out"][label] for r in rs]
        good = sum(1 for o in outs if o["hyp"] and o["sim"] >= 0.9)
        empty = sum(1 for o in outs if not o["hyp"])
        avg = sum(o["sim"] for o in outs) / len(outs)
        sec = sum(o["sec"] for o in outs) / len(outs)
        summary[(gname, label)] = {"good": good, "n": len(rs), "empty": empty,
                                   "avg_sim": avg, "avg_sec": sec}
        w(f"| {gname} | {label} | **{good}/{len(rs)}** | {empty} | "
          f"{avg:.3f} | {sec:.2f} |")
    w("")

# ── 五、总体 + 长短对比 ──
rule("五、总体与「长句 vs 短句」直接对比")
LONG_KEY = GROUPS[2][0]
SHORT_KEY = GROUPS[0][0]
w("| 配置 | 全部 | 短句 | 长句 | 长句−短句 |")
w("|------|------|------|------|-----------|")
for label in LABELS:
    tot_rs = rows
    g_all = sum(1 for r in tot_rs
                if r["out"][label]["hyp"] and r["out"][label]["sim"] >= 0.9)
    g_s = summary[(SHORT_KEY, label)]["good"]
    g_l = summary[(LONG_KEY, label)]["good"]
    w(f"| {label} | {g_all}/{len(tot_rs)} | {g_s}/6 | {g_l}/6 | {g_l - g_s:+d} |")
w("")

# ── 六、模型本身的长短句差异 ──
rule("六、结论所需的核心对比：medium 相对 small，在长句上是更好还是更差")
for biased in ("+偏置", "（无偏置）"):
    s_lab = f"small{biased}"
    m_lab = f"medium{biased}"
    s_l = summary[(LONG_KEY, s_lab)]
    m_l = summary[(LONG_KEY, m_lab)]
    s_s = summary[(SHORT_KEY, s_lab)]
    m_s = summary[(SHORT_KEY, m_lab)]
    w(f"### {biased}")
    w("")
    w(f"- 长句：small **{s_l['good']}/6** vs medium **{m_l['good']}/6**"
      f"（差 {m_l['good'] - s_l['good']:+d}）")
    w(f"- 短句：small **{s_s['good']}/6** vs medium **{m_s['good']}/6**"
      f"（差 {m_s['good'] - s_s['good']:+d}）")
    w(f"- 平均相似度（长句）：small {s_l['avg_sim']:.3f} vs "
      f"medium {m_l['avg_sim']:.3f}")
    w(f"- 平均耗时（长句）：small {s_l['avg_sec']:.2f}s vs "
      f"medium {m_l['avg_sec']:.2f}s")
    w("")

# ── 七、原始转写全文（不许只给分数）──
rule("七、原始转写全文（分数是加工过的；这里是模型真的吐出来的字）")
for r in rows:
    w(f"### {r['group']} · {r['text']}")
    w("")
    for label in LABELS:
        o = r["out"][label]
        w(f"- `{label}`：{('（空输出）' if not o['hyp'] else o['hyp'])}"
          f"   〔相似度 {o['sim']:.3f}，{o['sec']:.2f}s〕")
    w("")

w("═" * 78)
w(" 边界声明（这份证据**不**说明什么）")
w("═" * 78)
w("")
w("- 它**不**验证麦克风采集：测激全部来自 **TTS 合成**（P4-C1 已查明合成端")
w("  自身有抖动）。真人语音的时长/停顿/口音分布与 TTS 不同，")
w("  真实场景只有人能测（P5-C2）。")
w("- 它**不**决定 `config.yaml` 该写哪一档：这是**产品取舍**（精度 vs 冷启动")
w("  与内存），本文件只提供数字。")
w("- 它**不**覆盖英文/中英混说：本批全是中文句子。")
w("- 单机单次：每句只跑一次（音频固定，所以同音频重跑是确定的；")
w("  **换一批合成音频结论可能不同** —— 这正是「测激抖动」的含义）。")

OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text("\n".join(L) + "\n", encoding="utf-8")
print(f"写入 {OUT}（{OUT.stat().st_size} bytes）")

raw = SANDBOX / "asr_long_sentence_rows.json"
io.open(raw, "w", encoding="utf-8", newline="\n").write(json.dumps(
    [{"group": r["group"], "text": r["text"], "md5": r["md5"],
      "out": {k: {"hyp": v["hyp"], "sim": v["sim"], "sec": v["sec"]}
              for k, v in r["out"].items()}} for r in rows],
    ensure_ascii=False, indent=2) + "\n")
print(f"逐句原始数据：{raw}（{raw.stat().st_size} bytes）")
