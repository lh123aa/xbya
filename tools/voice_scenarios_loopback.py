"""真机语音场景「回环」自动验证（TTS → ASR → 真实 Agent 管线）

把 `docs/agent/acceptance.md` §7.3 的 5 项「需真机麦克风 + 人在场」的人工语音验证
换成**可重复运行的自动回环**：用 edge_tts 合成中文语音 → 解码成 16k 单声道 WAV →
交给**项目自有的 ASR 插件**（`plugins/asr/faster_whisper/plugin.py`）转写 →
把转写文本注入**真实配置装配的真实 Agent 栈**（`agent.bootstrap.build_agent_stack`）。

════════════════════════════════════════════════════════════════════════
证据强度声明（重要，勿删）
════════════════════════════════════════════════════════════════════════
本脚本覆盖：**ASR → 事件总线 → Agent 管线 → 事件/播报 + 审计/回收站** 这一段。

本脚本**不覆盖**：
  1. **麦克风硬件采集** —— 音频来自 TTS 合成，不经过声卡/麦克风/增益/回声消除，
     也没有环境噪声与人说话的自然停顿。这一环只能由真人对着麦克风验证。
  2. **PySide6 GUI 与动效** —— 不起窗口，`think/surprise` 动效与气泡由
     `ui/pet_window.py` 的槽函数负责，此处只断言它们依赖的事件是否被正确发射。
  3. **人耳听感** —— 是否"听到"播报内容不在断言范围（只断言事件发出了音频/文本）。
  4. **生产模型** —— `config.yaml` 里 `asr.faster_whisper.model_size: medium`，
     但本机 HuggingFace 缓存里**只有** tiny / base / small，故实际用 `small`
     （见 `--model` 选项）。medium 上的识别质量未验证。

**结论：本回环 ≠ 人工验收。** 它是人工验收的**前置过滤**：把"管线接错/行为不符"这类
问题自动拦掉，剩下的"麦克风好不好用、听起来对不对"仍必须真人上机。

════════════════════════════════════════════════════════════════════════
5 个场景（原文见 `docs/agent/acceptance.md` §7.3）
════════════════════════════════════════════════════════════════════════
  1. 搜索文件    ：「找一下桌面上的 PDF 文件」→ 确认语 + 结果 + 与沙箱实际文件一致
  2. 删除确认流程：「删除桌面上的截图」→ 待确认预览 →「确定」→ 回收站可恢复
  3. 打断         ：「找一下所有文件」→ 立即打断 → 不再播报结果 + 动效切 surprise
  4. 降级路径     ：非工具指令走 `pipeline.chat` 回原 LLM；Agent 关闭/未装配时回原链路
  5. 安全边界     ：「打开 C 盘 Windows 文件夹」→ 拒绝 + 友好提示 + 审计留痕

用法：
    python tools/voice_scenarios_loopback.py                 # 全跑（含 TTS/ASR）
    python tools/voice_scenarios_loopback.py --asr-only      # 只跑 TTS→ASR 回环
    python tools/voice_scenarios_loopback.py --no-tts        # 跳过 TTS/ASR，只验管线
    python tools/voice_scenarios_loopback.py --model small   # 指定已缓存的 ASR 模型
    python tools/voice_scenarios_loopback.py --voice zh-CN-XiaoxiaoNeural

退出码：0 = 全部通过；1 = 有失败/降级。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import shutil
import sys
import tempfile
import time
import wave
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from agent.bootstrap import AgentConfig, build_agent_stack          # noqa: E402
from core.kernel.events import EventBus, EventTypes                 # noqa: E402

# ══════════════════════════════════════════════════════════════════════
#  结果记录（与 p1/p3_acceptance_smoke.py 同风格）
# ══════════════════════════════════════════════════════════════════════

PASS, FAIL, DEGRADED = "[PASS]", "[FAIL]", "[DEGRADED]"
results: List[Tuple[str, str, str]] = []      # (status, label, detail)
scenarios: List[Dict[str, Any]] = []          # 逐场景的结构化记录

#: seed_desktop() 上一轮造出来的样本文件名（用于重播种时只清自己造的东西）
_SEED_CREATED: List[str] = []


def check(ok: bool, label: str, detail: str = "") -> bool:
    """记一条断言（PASS/FAIL）"""
    results.append((PASS if ok else FAIL, label, detail))
    print(f"{PASS if ok else FAIL} {label}" + (f"  → {detail}" if detail else ""))
    return bool(ok)


def degrade(label: str, detail: str = "") -> bool:
    """记一条「降级/未验证」——不计入失败，但会阻止整体判定为"全绿"（与 FAIL 同样退出 1）

    用于环境限制导致的无法验证（例如没装 av、模型未缓存）。
    """
    results.append((DEGRADED, label, detail))
    print(f"{DEGRADED} {label}" + (f"  → {detail}" if detail else ""))
    return False


def wait_for(sink: list, timeout: float = 8.0) -> bool:
    """等事件落进 sink（与 p1/p3 冒烟同一实现）"""
    deadline = time.time() + timeout
    while not sink and time.time() < deadline:
        time.sleep(0.02)
    return bool(sink)


def _await_events(rec: "Recorder", event_types: List[str], timeout: float = 8.0) -> bool:
    """轮询等待**任意一个**指定事件出现

    为什么不用 `wait_for(rec.all(T))`：`rec.all()` 是即时求值，调用时若还没有
    该事件就会立刻返回空列表，`wait_for` 随即"超时"返回 —— 快事件偶然通过、
    稍慢的事件必然误判失败。这里改为在截止时间内循环检查。
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        if any(rec.all(t) for t in event_types):
            return True
        time.sleep(0.02)
    return any(rec.all(t) for t in event_types)


# ══════════════════════════════════════════════════════════════════════
#  文本规范化与比对
# ══════════════════════════════════════════════════════════════════════

#: Whisper（small 档）在纯合成语音上会稳定输出**繁体字**（实测："截图"→"截圖"、
#: "确定"→"確定"、"给我讲个笑话"→"給我講個笑話"）。这是 ASR 的输出特性，不是管线缺陷，
#: 但它会让"关键词包含"断言的结论取决于字形。这里做**最小**的繁→简映射，
#: 并逐条标注「本次发生了字形转换」，绝不静默放过。
TRAD_TO_SIMP = {
    "圖": "图", "確": "确", "給": "给", "講": "讲", "個": "个", "話": "话",
    "們": "们", "裡": "里", "檔": "档", "刪": "删", "資": "资", "夾": "夹",
    "開": "开", "關": "关", "東": "东", "這": "这", "麼": "么", "說": "说",
    "對": "对", "錯": "错", "點": "点", "讓": "让", "還": "还", "過": "过",
    "顯": "显", "視": "视", "訊": "讯", "網": "网", "頁": "页", "轉": "转",
    "換": "换", "進": "进", "運": "运", "執": "执", "行": "行", "錄": "录",
    "聲": "声", "數": "数", "據": "据", "應": "应", "當": "当", "時": "时",
    "間": "间", "間": "间", "見": "见", "現": "现", "實": "实", "內": "内",
    "別": "别", "將": "将", "於": "于", "為": "为", "與": "与", "無": "无",
    "麼": "么", "樣": "样", "張": "张", "計": "计", "劃": "划", "項": "项",
}

_PUNCT_RE = re.compile(
    "[\\s\u3000,，。.、！!？?；;：:\"'“”‘’（）()《》〈〉…—\\-_~·]+")


def normalize(text: str) -> tuple:
    """归一化：去空白与标点、转小写、繁→简

    Returns:
        (归一化文本, 本次被转换的字符对列表)
    """
    if not text:
        return "", []
    converted: List[str] = []
    out_chars: List[str] = []
    for ch in text:
        simp = TRAD_TO_SIMP.get(ch)
        if simp is not None and simp != ch:
            converted.append(f"{ch}→{simp}")
            out_chars.append(simp)
        else:
            out_chars.append(ch)
    joined = _PUNCT_RE.sub("", "".join(out_chars))
    return joined.lower(), converted


def keyword_ok(hyp: str, keywords: List[str]) -> tuple:
    """关键词包含断言（繁简归一后比对）

    Returns:
        (是否全部命中, 说明文本)
    """
    norm, converted = normalize(hyp)
    missing = []
    for kw in keywords:
        kw_norm, _ = normalize(kw)
        if kw_norm and kw_norm not in norm:
            missing.append(kw)
    note = f"命中 {len(keywords) - len(missing)}/{len(keywords)}"
    if converted:
        note += f"；繁→简转换 {' '.join(converted)}"
    if missing:
        note += f"；缺关键词 {missing}"
    return (not missing), note


# ══════════════════════════════════════════════════════════════════════
#  TTS → ASR 回环（真实链路：edge_tts mp3 → PyAV 解码 16k 单声道 WAV → 项目 ASR 插件）
# ══════════════════════════════════════════════════════════════════════

class Loopback:
    """合成 + 识别一段话，返回转写文本与耗时"""

    def __init__(self, voice: str, model_size: str, enabled: bool = True,
                 audio_dir: Optional[Path] = None,
                 stimulus_dir: Optional[Path] = None,
                 fresh_stimulus: bool = False) -> None:
        self.voice = voice
        self.model_size = model_size
        self.enabled = enabled
        self.audio_dir = audio_dir or Path(tempfile.mkdtemp(prefix="xbya_loop_"))
        # 固定测激目录：见 roundtrip() 的长注释（为什么必须固定）
        self.stimulus_dir = Path(stimulus_dir) if stimulus_dir else None
        self.fresh_stimulus = fresh_stimulus
        self._asr = None
        self.load_seconds: Optional[float] = None
        self.records: List[Dict[str, Any]] = []
        self.stimulus_reused = 0
        self.stimulus_created = 0

    # ── 依赖探测 ──
    @staticmethod
    def probe() -> Dict[str, Any]:
        info: Dict[str, Any] = {"av": None, "edge_tts": None, "faster_whisper": None}
        try:
            import av
            info["av"] = getattr(av, "__version__", "unknown")
        except Exception as e:                       # pragma: no cover - 环境相关
            info["av_error"] = str(e)
        try:
            import edge_tts                        # noqa: F401
            info["edge_tts"] = "ok"
        except Exception as e:                       # pragma: no cover
            info["edge_tts_error"] = str(e)
        try:
            import faster_whisper
            info["faster_whisper"] = getattr(faster_whisper, "__version__", "unknown")
        except Exception as e:                       # pragma: no cover
            info["faster_whisper_error"] = str(e)
        return info

    def _ensure_asr(self):
        """装载项目自有 ASR 插件（懒加载模型）"""
        if self._asr is not None:
            return self._asr
        from plugins.asr.faster_whisper.plugin import FasterWhisperASR
        t0 = time.perf_counter()
        self._asr = FasterWhisperASR(model_size=self.model_size, device="cpu")
        self._asr._ensure_loaded()            # 显式预热，让首次耗时不计入单条 ASR 计时
        self.load_seconds = time.perf_counter() - t0
        if not self._asr._available:
            raise RuntimeError(
                f"ASR 模型 {self.model_size} 加载失败（本机缓存里有 tiny/base/small）"
            )
        return self._asr

    def _synthesize(self, text: str, mp3: Path) -> float:
        import edge_tts

        async def _gen() -> None:
            await edge_tts.Communicate(text, self.voice).save(str(mp3))

        t0 = time.perf_counter()
        asyncio.run(_gen())
        return time.perf_counter() - t0

    @staticmethod
    def _mp3_to_wav16k(src: Path, dst: Path) -> None:
        """用 PyAV 把 mp3 解成 16k 单声道 s16 WAV

        为什么需要这一步：项目 ASR 插件的 `_enhance_audio()` 只吃 WAV
        （它用 stdlib `wave` 打开，mp3 会抛异常后"降级为原音频"再交给
        faster-whisper）。这里显式产出插件正门能吃的 WAV，从而让
        **项目自有 ASR 代码路径** 真正参与回环，而不是只调 faster-whisper。
        """
        import av
        import numpy as np

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

    @staticmethod
    def _wav_stats(path: Path) -> Any:
        """读取 WAV 的 `(时长秒, 平均绝对幅度)`；读不了返回 `(0.0, 0.0)`"""
        import wave
        import numpy as np
        try:
            with wave.open(str(path), "rb") as wf:
                n = wf.getnframes()
                rate = wf.getframerate() or 1
                pcm = np.frombuffer(wf.readframes(n), dtype=np.int16)
        except Exception:
            return (0.0, 0.0)
        dur = n / rate
        amp = float(np.abs(pcm.astype(np.float32)).mean()) if len(pcm) else 0.0
        return (dur, amp)

    def roundtrip(self, text: str, index: int, tag: str = "") -> Dict[str, Any]:
        """合成 → 解码 → 转写，返回一条记录"""
        rec: Dict[str, Any] = {"tag": tag or f"u{index}", "text": text}
        if not self.enabled:
            rec.update(transcript=text, tts_s=0.0, asr_s=0.0, mode="bypass(未走 TTS/ASR)")
            self.records.append(rec)
            print(f"  [跳过 TTS/ASR] {tag}: {text!r}")
            return rec

        mp3 = self.audio_dir / f"utt{index}.mp3"
        wav = self.audio_dir / f"utt{index}.wav"

        # ── 测激：优先用固定音频（fixture），否则现场合成 ──
        #
        # 为什么必须能固定：`edge_tts` 每次合成**同一句话的波形并不完全相同**
        # （实测字节数一致、样点均值在 1473~1506 之间抖动）。而 faster-whisper
        # 对**同一份文件**是确定的（实测 7 个文件各转写 6 次，结果 6/6 完全一致）。
        # 于是回环的抖动全部来自"这次合成出来的那一版音频运气如何"——
        # 那是**替身（用 TTS 假装人在说话）的抖动，不是被测产品的行为**。
        #
        # 归档证据：`docs/agent/evidence/acceptance/G12.txt` 里那次 45/50，
        # 场景 2 把「删除桌面上的截图」转写成「山豬桌面上集圖」（首词整个丢失），
        # 而同一句话**现场重合成 10 次全部正确**、同一份文件**重转写 6 次完全一致**，
        # `vad_filter` 开/关各 10/10 —— 说明那是那一版音频自身的偶发劣化。
        #
        # 所以：回归默认吃固定测激（可重复、可比对），
        # `--fresh-stimulus` 才每次重新合成（用来**测量替身抖动**，而不是测产品）。
        fixture = None
        if self.stimulus_dir is not None:
            fixture = self.stimulus_dir / f"{rec['tag']}.wav"
        reuse = bool(fixture is not None and fixture.exists() and not self.fresh_stimulus)

        if reuse:
            shutil.copyfile(fixture, wav)
            tts_s = 0.0
            rec["stimulus"] = f"fixed:{fixture.name}"
        else:
            tts_s = self._synthesize(text, mp3)
            self._mp3_to_wav16k(mp3, wav)
            rec["stimulus"] = "fresh" if fixture is None else "fresh(将固定)"

        # ── 测激健全性：音频被截断/近乎无声 → 重新合成一次 ──
        #
        # 为什么查"测激"而不是查"答案"：整批跑时偶发过 ASR 把
        # 「删除桌面上的截图」识别成「山豬桌面上集圖」。所以这里只校验
        # "这段音频像不像一句正常的话"（时长是否够、是否近乎无声），
        # 不达标记为测激可疑并重合成一次。
        #
        # **绝不会**因为"识别出来的字不对"而重试 —— 那是真实结果，重试就是凑绿。
        dur, amp = self._wav_stats(wav)
        expect_min = 0.12 * max(1, len(text))          # 宽松下限，只拦明显截断
        if not reuse and (dur < expect_min or amp < 50.0):
            print(f"  [注意] 测激可疑（时长 {dur:.2f}s / 幅度 {amp:.0f}）"
                  f"→ 重新合成一次（属合成端抖动，不是产品结论）")
            tts_s = self._synthesize(text, mp3)
            self._mp3_to_wav16k(mp3, wav)
            dur, amp = self._wav_stats(wav)

        asr = self._ensure_asr()

        # ── 空转写重试一次（**只对"空"重试**）──
        #
        # 实测抖动：同一句「给我讲个笑话」，上一轮能正常转写，下一轮
        # `transcribe()` 返回 None 且只耗时 0.08s（明显没真正跑）——
        # 这是**采集/解码端没拿到信号**，属基础设施失败，不是产品行为。
        # 所以这里只在"转写为空"时重来一次，并打印提示；
        # **绝不会**因为"转写非空但关键词不对"而重试 —— 那是真实结果，重试就是凑绿。
        attempts = 0
        hyp = None
        asr_s = 0.0
        for attempts in range(1, 3):
            t0 = time.perf_counter()
            hyp = asr.transcribe(str(wav))
            asr_s = time.perf_counter() - t0
            if hyp:
                break
            if attempts == 1:
                print(f"  [注意] 转写为空（{asr_s:.2f}s）→ 重新合成再试一次"
                      f"（空转写属采集端抖动，不是产品结论）")
                tts_s = self._synthesize(text, mp3)
                self._mp3_to_wav16k(mp3, wav)

        rec.update(
            transcript=hyp or "",
            tts_s=round(tts_s, 2),
            asr_s=round(asr_s, 2),
            wav_bytes=wav.stat().st_size,
            wav_seconds=round(dur, 2),
            wav_amp=round(amp, 1),
            asr_attempts=attempts,
            mode="edge_tts→PyAV→plugin",
        )
        self.records.append(rec)
        print(f"  [TTS {tts_s:.2f}s / ASR {asr_s:.2f}s / 第{attempts}次] 合成={text!r}")
        print(f"                       转写={hyp!r}")
        print(f"                       音频 {dur:.2f}s / 幅度 {amp:.0f} / "
              f"{wav.stat().st_size}B / 测激={rec['stimulus']}")

        # 固定测激的**建立**放在"解码非空"之后：
        # 若把一段连一个字都转不出来的音频固化成 fixture，
        # 就会把一次偶发失败永久写进回归基线（那是最糟的假绿/假红）。
        if fixture is not None and not reuse and hyp:
            try:
                fixture.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(wav, fixture)
                self.stimulus_created += 1
            except Exception as e:                      # pragma: no cover - 磁盘相关
                print(f"  [注意] 固定测激写入失败（不影响本轮结论）：{e}")
        elif reuse:
            self.stimulus_reused += 1

        # 诊断用：把测激留档，便于"同一段音频离线重转写"来区分
        # "音频有问题" 与 "ASR 跨调用状态有问题"（默认关闭，不污染产物）
        keep = os.environ.get("xbya_KEEP_AUDIO")
        if keep:
            try:
                Path(keep).mkdir(parents=True, exist_ok=True)
                shutil.copyfile(wav, Path(keep) / f"{rec['tag']}.wav")
            except Exception:
                pass
        return rec

    def cleanup(self) -> None:
        shutil.rmtree(self.audio_dir, ignore_errors=True)


# ══════════════════════════════════════════════════════════════════════
#  场景沙箱与 Agent 装配
# ══════════════════════════════════════════════════════════════════════

def make_sandbox() -> Path:
    """造一个"像用户桌面"的沙箱：白名单根之一必须正好叫 Desktop

    原因：`file_tools._resolve_dirs()` 先按白名单根的**目录名**匹配别名
    （"Desktop" → 该根），再把结果交给工具。根不叫 Desktop 时，路由产出的
    "Desktop" 会被当成路径去 `validate_path()` 从而被拒。p1/p3 冒烟同样如此。

    返回 Desktop 目录本身；审计库/记忆库等落在它的**父目录**（沙箱内、白名单外），
    否则会被 `validate_path()` 拦下（这是安全设计的正确行为）。
    """
    root = Path(tempfile.mkdtemp(prefix="xbya_voice_")) / "Desktop"
    root.mkdir(parents=True)
    return root


def seed_desktop(desktop: Path) -> None:
    """放几份"用户桌面"该有的东西（每次断言都重新播种，保证可重复）

    只清理**本函数自己造出来**的样本文件；审计库/记忆库（由 Agent 栈写在同一个
    白名单根下）必须原地保留 —— 一是它们在 Windows 上被 SQLite 持有句柄，
    二是删掉会掩盖"审计是否留痕"的断言。
    """
    global _SEED_CREATED
    for name in list(_SEED_CREATED):
        p = desktop / name
        try:
            if p.exists():
                p.unlink()
        except OSError:
            pass
    _SEED_CREATED = []
    for name, payload in (("合同_2025.pdf", b"x" * 64), ("旧合同.pdf", b"y" * 32),
                          ("截图_01.png", b"z" * 128), ("截图_02.png", b"z" * 256),
                          ("笔记.txt", b"hello agent")):
        (desktop / name).write_bytes(payload)
        _SEED_CREATED.append(name)


def build_cfg(desktop: Path, sandbox: Path, enabled: bool = True) -> AgentConfig:
    """真实配置装配（与 p1/p3 冒烟同一套开关取向）

    - llm_router_enabled=False：5 个场景全部走规则路由，**不调用任何真实 LLM API**
    - remember_choices=False：避免"批准一次后同目录免确认"掩盖场景 2 的确认流程
    - ack_warmup=False：不预热 TTS；ack 事件里 audio=None，脚本不播放音频

    Args:
        desktop: 白名单根（必须叫 Desktop）
        sandbox: 审计库/记忆库所在目录（**在白名单之外**）
    """
    return AgentConfig(
        enabled=enabled,
        router_provider="hybrid",
        llm_router_enabled=False,
        path_whitelist=[str(desktop)],
        audit_db=str(sandbox / "audit.db"),
        audit_enabled=True,
        ack_enabled=True,
        ack_warmup=False,
        remember_choices=False,
        planner_enabled=True,
        planner_provider="hybrid",
        memory_enabled=True,
        memory_store=str(sandbox / "memory.db"),
        memory_embedder="hashing",
        memory_vector_backend="auto",
        tracker_persist=False,
    )



class Recorder:
    """订阅总线上的全部 Agent 事件，按场景取用"""

    def __init__(self, bus: EventBus) -> None:
        self.bus = bus
        self.events: List[Tuple[str, Dict[str, Any]]] = []
        self._disposer = bus.on_any(lambda e: self.events.append((e.type, dict(e.data))))

    def clear(self) -> None:
        self.events.clear()

    def all(self, event_type: str) -> List[Dict[str, Any]]:
        return [d for t, d in self.events if t == event_type]

    def first(self, event_type: str) -> Dict[str, Any]:
        got = self.all(event_type)
        return got[0] if got else {}

    def dispose(self) -> None:
        try:
            self._disposer()
        except Exception:
            pass


# ══════════════════════════════════════════════════════════════════════
#  主流程
# ══════════════════════════════════════════════════════════════════════

def run_scenario_1(pipe, rec: Recorder, lb: Loopback, desktop: Path, idx: int) -> None:
    """场景 1：搜索文件「找一下桌面上的 PDF 文件」"""
    print("\n[场景 1] 搜索文件（P0）")
    text = "找一下桌面上的 PDF 文件"
    rt = lb.roundtrip(text, idx, "场景1")
    ok_kw, note = keyword_ok(rt["transcript"], ["找", "桌面", "PDF"])
    check(ok_kw, "ASR 转写基本正确（找+桌面+PDF）", note)

    rec.clear()
    pipe.handle_text(rt["transcript"], "s1")
    got_ack = _await_events(rec, [EventTypes.FEEDBACK_ACK], timeout=2.0)
    # 用事件自带的 elapsed_ms（管线内部 perf_counter 计算）而不是墙钟，
    # 因为 wait_for 是 20ms 轮询，墙钟会把轮询间隔算进去
    ack = rec.first(EventTypes.FEEDBACK_ACK)
    ack_ms = float(ack.get("elapsed_ms") or 0.0)

    got_res = _await_events(rec, [EventTypes.FEEDBACK_RESULT], timeout=8.0)
    res = rec.first(EventTypes.FEEDBACK_RESULT)
    res_ms = float(res.get("elapsed_ms") or 0.0)

    print(f"  ack: {ack.get('text')!r} (event elapsed_ms={ack_ms:.1f})")
    print(f"  result: ok={res.get('success')} tool={res.get('tool_name')} "
          f"elapsed_ms={res_ms:.1f} summary={res.get('summary','')[:60]!r}")

    check(got_ack, "1.5s 内发出确认语事件", f"elapsed_ms={ack_ms:.1f}")
    check(ack_ms < 1500, "确认语延迟 < 1.5s（§7.3 指标）", f"{ack_ms:.1f}ms")
    check(bool(ack.get("text")), "确认语有可播报文本", repr(ack.get("text", ""))[:30])
    check(got_res, "3s 内产出结果事件", f"elapsed_ms={res_ms:.1f}")
    check(res_ms < 3000, "结果播报延迟 < 3s（§7.3 指标）", f"{res_ms:.1f}ms")
    check(res.get("tool_name") == "file_search", "动作识别为 file_search",
          str(res.get("tool_name")))
    check(res.get("success") is True, "搜索成功", str(res.get("success")))
    names = [f.get("name") for f in (res.get("data") or [])] \
        if isinstance(res.get("data"), list) else []
    on_disk = sorted(p.name for p in desktop.glob("*.pdf"))
    print(f"  播报内容与桌面实际 PDF：结果={names} 磁盘={on_disk}")
    check(set(names) == set(on_disk) and bool(on_disk),
          "结果与桌面实际文件一致", f"{names} vs {on_disk}")
    scenarios.append({"id": 1, "name": "搜索文件", "utt": rt, "verdict": "见上方断言"})


def run_scenario_2(pipe, rec: Recorder, lb: Loopback, desktop: Path, idx: int,
                   audit_db: Path) -> None:
    """场景 2：删除确认流程「删除桌面上的截图」→「确定」→ 回收站

    ⚠️ §7.3 的这条清单从"说删除截图"起手，但**只验这一句会掩盖一个真实缺陷**。
    实测机制（已用 `RuleRouter` 单独复现，见报告）：

      - 简体「删除桌面上的截图」→ `params={'dirs': ['Desktop'], 'pattern': '*截图*'}` ✅
      - 繁体「删除桌面上的截圖」（faster-whisper small 对合成语音的稳定输出）
        → `params={'dirs': ['Desktop']}` —— **`pattern` 丢了**，因为关键词表里只有简体
        「截图」，而 `_extract_extension_pattern()` 认不出"截圖"。
      - 规则路由的**置信度仍是 0.95**（`截图` 之外还有"删除"命中，分数够），
        于是它没有被置信度阈值拦下，而是带着**没有目标**的 `file_delete` 进了管线。
      - 管线 `_resolve_references()` 再拿 `EntityTracker` 里**上一次搜索的结果**补目标
        —— 连续对话里"上一次"往往就是别的东西（此处是 PDF 合同）。

    因此本场景按真实对话顺序（先一句搜索把实体栈填上，再删）来跑，并断言：
      - 若确认目标 = 桌面实际 PNG → 批准，走完回收站 + 审计（§7.3 全流程通过）
      - 若确认目标 ≠ PNG（拿到的是上一次搜索结果）→ **取消确认（不落盘）**并报 FAIL，
        附上证据文案。安全第一：绝不为凑数字去批准一次删错文件的确认。
    """
    print("\n[场景 2] 删除确认流程（P0）")
    seed_desktop(desktop)                     # 重新播种，保证可重复
    pngs = sorted(p.name for p in desktop.glob("*.png"))
    pdfs = sorted(p.name for p in desktop.glob("*.pdf"))

    # 先跑一句搜索，把实体栈填成"上一次找到的是 PDF"（真实连续对话里很常见）
    rec.clear()
    pipe.handle_text("找一下桌面上的 PDF 文件", "s2warm")
    _await_events(rec, [EventTypes.FEEDBACK_RESULT], timeout=8.0)
    warm = rec.first(EventTypes.FEEDBACK_RESULT)
    print(f"  [前置] 先搜PDF → {warm.get('summary','')!r}")

    rt = lb.roundtrip("删除桌面上的截图", idx, "场景2-指令")
    ok_kw, note = keyword_ok(rt["transcript"], ["删除", "桌面", "截图"])
    check(ok_kw, "ASR 转写基本正确（删除+桌面+截图）", note)

    rec.clear()
    cmd = pipe.handle_text(rt["transcript"], "s2")
    got_conf = _await_events(rec, [EventTypes.FEEDBACK_CONFIRM], timeout=8.0)
    conf = rec.first(EventTypes.FEEDBACK_CONFIRM)
    print(f"  路由：action={cmd.action} params={cmd.params}")
    print(f"  确认问句：{conf.get('question','')!r}")
    print(f"  预览：{conf.get('preview','')!r}  risk={conf.get('risk')}")
    check(got_conf, "发出待确认事件（气泡预览 + 确认问句）", str(conf.get("action")))
    check(rec.first(EventTypes.FEEDBACK_RESULT) == {}, "确认前未执行删除")
    check(all((desktop / n).exists() for n in pngs + pdfs), "确认前文件仍在原处")

    preview = str(conf.get("preview", "")) + str(conf.get("question", ""))
    target_ok = bool(got_conf) and all(n in preview for n in pngs) \
        and not any(n in preview for n in pdfs)
    if target_ok:
        check(True, "确认目标是桌面上的截图（PNG）", f"{pngs}")
    else:
        stale = [n for n in pdfs if n in preview]
        check(False, "确认目标是桌面上的截图（PNG）",
              f"实际预览指向 {stale or '未知文件'}（截图应为 {pngs}）")
        if not cmd.params.get("pattern") and cmd.params.get("dirs"):
            check(False, "路由为 file_delete 产出了明确目标（pattern）",
                  f"只有 dirs={cmd.params['dirs']} 没有 pattern —— 目标由实体栈补上，"
                  f"supplied targets={[Path(t).name for t in cmd.params.get('targets', [])]}")
        print("  → 判定：删除目标来自**上一次搜索的实体栈残留**，不是用户说的「截图」。")
        print("  → 该确认被**取消**（绝不批准一次删错文件的确认）。")

    # 取消语为什么不用「算了」：脚本实测（见证据文件「短句可识别性」段）
    #   '算了' 0/4 全部被识别成「散了」；'不用了' 4/4；'取消' 4/4；'停止' 1/4。
    # 「算了」本身在 CONFIRM_PHRASES 旁边就是 CANCEL_PHRASES 的首词，
    # 也就是说这是**真人大概率会说、但本机 ASR 认不出**的一句 —— 作为发现记录，
    # 但流程验证用可识别的「不用了」，否则场景会退化成"验证 ASR 运气"。
    confirm_utt = "确定" if target_ok else "不用了"
    rt2 = lb.roundtrip(confirm_utt, idx + 1,
                       "场景2-确认" if target_ok else "场景2-取消")
    ok_kw2, note2 = keyword_ok(rt2["transcript"], [confirm_utt])
    check(ok_kw2, f"ASR 转写基本正确（{confirm_utt}）", note2)

    rec.clear()
    pipe.handle_text(rt2["transcript"], "s2b")
    got_res = _await_events(rec, [EventTypes.FEEDBACK_RESULT], timeout=10.0)
    res = rec.first(EventTypes.FEEDBACK_RESULT)
    print(f"  结果：ok={res.get('success')} summary={res.get('summary','')!r}")
    check(got_res, "确认/取消后产出结果事件")
    check(all((desktop / n).exists() for n in pdfs), "合同文件未被误删（安全底线）")
    if target_ok:
        check(res.get("success") is True, "删除执行成功", str(res.get("success")))
        check(not any((desktop / n).exists() for n in pngs),
              "截图已从桌面移走", f"目标 {pngs}")
    else:
        check(not any((desktop / n).exists() is False for n in pdfs),
              "取消后未执行任何删除", str(res.get("success")))
        # 取消若没被识别，待确认项会一直挂着（用户会看到"没反应"）——单独断言
        pending = _pending_count(pipe)
        check(pending == 0, "取消后待确认项已清空（不会一直挂着等用户）",
              f"pending={pending}")

    # 回收站可恢复性：**自动确证**（读真实回收站的 $I 元数据比对原路径）
    if target_ok:
        probe = _recycle_bin_restore_probe(desktop, pngs)
        if probe is True:
            check(True, "文件在回收站中可恢复",
                  "回收站 $I 记录里的原路径与删除目标一致，且数据本体 $R 存在")
        elif probe is False:
            check(False, "文件在回收站中可恢复", "回收站里找不到这些文件")
        else:
            degrade("文件在回收站中可恢复（环境不可读，无法自动确证）", str(probe))
    else:
        degrade("文件在回收站中可恢复（因未批准删除，本轮不适用）",
                "确认目标错误 → 按安全优先取消了删除")

    audit = _audit_rows(audit_db)
    del_rows = [r for r in audit if r.get("action") == "file_delete"]
    if target_ok:
        check(bool(del_rows), "审计日志记录了 file_delete", f"{len(del_rows)} 条")
    else:
        check(not del_rows, "未批准 → 审计里没有 file_delete 记录", f"{len(del_rows)} 条")
    if del_rows:
        print(f"  审计：success={del_rows[0].get('success')} "
              f"detail={str(del_rows[0].get('detail'))[:50]!r}")
    scenarios.append({"id": 2, "name": "删除确认流程", "utt": rt,
                      "utt2": rt2, "verdict": "见上方断言"})


#: `$I` 元数据的 v2 头长度（8 字节头 + 8 字节文件大小 + 8 字节删除时间）
_RECYCLE_HEADER = 24


def _parse_recycle_record(data: bytes) -> Any:
    """解析回收站 `$I` 元数据 → `(原路径, 文件大小)`；解析不了返回 None

    Windows 回收站每个条目是一对文件：`$I…` 元数据 + `$R…` 数据本体。
    `$I` 的 **v2 格式**：`[0:8]` 版本(=2)、`[8:16]` 文件大小、`[16:24]` 删除时间、
    `[24:28]` 原路径字符数，随后是 **UTF-16LE 的原路径**（含结尾 NUL）。
    原路径是我们能自动确证"这个文件确实在回收站、且知道从哪来"的关键。
    """
    if len(data) < _RECYCLE_HEADER + 4:
        return None
    if int.from_bytes(data[0:8], "little") != 2:
        return None
    size = int.from_bytes(data[8:16], "little")
    name_len = int.from_bytes(data[24:28], "little")
    if name_len <= 0:
        return None
    raw = data[28:28 + name_len * 2]
    original = raw.decode("utf-16-le", errors="ignore").rstrip("\x00")
    return (original, size) if original else None


def _recycle_bin_lookup(desktop: Path, names: List[str]) -> Any:
    """在**真实回收站**里找刚删的这些文件（用 `$I` 里的原路径精确匹配）

    这一条以前记的是 DEGRADED（"需人工打开回收站确认"），理由写的是
    "回收站条目名被重写成 `$R…`，无法可靠对应，靠猜等于伪造证据"。
    实测后发现那个判断**保守过头**：`$I` 元数据里就写着原路径，可以精确对上号。
    有据可依就不该再降级 —— 顺带把"删除确实可恢复"从人工项变成自动项。

    Returns:
        True = 全部文件都在回收站里且数据本体存在；False = 没找到；
        str = 环境不可读等真正无法确证的情况
    """
    root = Path("C:\\$Recycle.Bin")
    try:
        subs = list(root.iterdir())
    except OSError as e:
        return f"回收站目录不可读（{type(e).__name__}），无法自动确证"

    wanted = {str(desktop / n).lower(): n for n in names}
    hit: set = set()
    bodies_missing: List[str] = []
    for sub in subs:
        try:
            metas = list(sub.glob("$I*"))
        except OSError:
            continue
        for meta in metas:
            try:
                parsed = _parse_recycle_record(meta.read_bytes())
            except OSError:
                continue
            if not parsed:
                continue
            original, _size = parsed
            key = original.lower()
            if key not in wanted:
                continue
            hit.add(key)
            body = meta.with_name("$R" + meta.name[2:])
            if not body.exists():
                bodies_missing.append(wanted[key])

    if not hit:
        return False
    if bodies_missing:
        return f"回收站里有记录但数据本体（$R）缺失：{bodies_missing}"
    return True


def _recycle_bin_restore_probe(desktop: Path, names: List[str]) -> Any:
    """探测刚删除的文件是否真的进了回收站（可恢复）

    两步：①原路径必须已不存在（否则删除根本没生效）；
    ②在真实回收站里按 `$I` 记录的原路径找到它们，且数据本体（`$R`）在。

    Returns:
        True/False = 自动确证；str = 真正无法自动确证的原因（脚本不伪造结论）
    """
    try:
        import send2trash                              # noqa: F401
    except Exception as e:                             # pragma: no cover
        return f"未安装 send2trash：{e}"
    still = [n for n in names if (desktop / n).exists()]
    if still:
        return False                                   # 原路径还在 → 删除根本没生效
    return _recycle_bin_lookup(desktop, names)


def _pending_count(pipe) -> int:
    """待确认项数量（用真实守卫的计数，不自己猜内部结构）"""
    try:
        return int(pipe._safety.pending_count())      # noqa: SLF001
    except Exception:
        return -1


def _audit_rows(audit_db: Path) -> List[Dict[str, Any]]:
    """读审计库（只读，不写）

    表名是 `audit`（见 `basic_guard._init_audit_db`），不是 `audit_log`。
    """
    import sqlite3
    if not audit_db.exists():
        return []
    try:
        conn = sqlite3.connect(f"file:{audit_db}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM audit ORDER BY ts DESC LIMIT 50")]
        conn.close()
        return rows
    except Exception as e:
        print(f"  [warn] 读审计库失败：{e}")
        return []


def run_scenario_3(pipe, rec: Recorder, lb: Loopback, stack, idx: int) -> None:
    """场景 3：打断（P0）

    §7.3 原文：说"找一下所有文件" → 播报确认语后立即按 Ctrl+Alt+D →
    立即停止播报 / 动效切 surprise / 不再播报结果。

    打断在实现里的真实表现（`agent/pipeline.py::_on_interrupt`，监听
    `EventTypes.SPEECH_INTERRUPTED`）：取消该 request 下全部任务 + 作废在途润色
    （不再补播）+ 作废在途多步计划。脚本逐步验证这两件事，**并额外验证
    "Ctrl+Alt+D 是否真的能走到这里"** —— 后者是接线问题，查得到就断言，查不到就如实报告。
    """
    print("\n[场景 3] 打断（P0）")
    rt = lb.roundtrip("找一下所有文件", idx, "场景3")
    ok_kw, note = keyword_ok(rt["transcript"], ["找", "文件"])
    check(ok_kw, "ASR 转写基本正确（找+文件）", note)

    executor = stack.executor
    inflight_before = getattr(executor, "inflight_count", lambda: -1)()

    rec.clear()
    pipe.handle_text(rt["transcript"], "s3")
    got_ack = wait_for(rec.all(EventTypes.FEEDBACK_ACK), timeout=2.0)
    check(got_ack, "已播报确认语（打断的前提）",
          repr(rec.first(EventTypes.FEEDBACK_ACK).get("text", ""))[:24])

    interrupts_before = pipe.stats()["interrupts"]
    pipe._on_interrupt(  # noqa: SLF001 - 直接复现总线回调（等同于 bus.emit）
        type("E", (), {"data": {"request_id": "s3"}})()
    )
    after = pipe.stats()["interrupts"]
    check(after == interrupts_before + 1, "打断被管线记录（stats.interrupts +1）",
          f"{interrupts_before} → {after}")

    # 打断后不应再发出该 request 的结果
    deadline = time.time() + 2.0
    while time.time() < deadline:
        time.sleep(0.05)
    late = [d for d in rec.all(EventTypes.FEEDBACK_RESULT) if d.get("request_id") == "s3"]
    check(not late, "打断后不再播报结果（无 feedback.result）",
          f"{len(late)} 条" if late else "0 条")

    # 多步计划被作废
    check(pipe.stats()["plan_pending"] == 0, "打断后无挂起计划",
          str(pipe.stats()["plan_pending"]))

    # ── 关键：Ctrl+Alt+D 到 Agent 总线的接线 ──
    print("\n  [打断接线核查] Ctrl+Alt+D → interrupt_current_speech() → "
          "app.interrupt_speech()")
    src = (PROJECT / "core" / "app.py").read_text(encoding="utf-8")
    pet = (PROJECT / "ui" / "pet_window.py").read_text(encoding="utf-8")
    emits_speech_interrupted = "SPEECH_INTERRUPTED" in src or "speech.interrupted" in src
    emits_speech_interrupted = emits_speech_interrupted or \
        ("SPEECH_INTERRUPTED" in pet or "speech.interrupted" in pet)
    print(f"  core/app.py 是否发射 speech.interrupted：{emits_speech_interrupted}")
    check(emits_speech_interrupted,
          "Ctrl+Alt+D 的运行路径会把打断事件送到 Agent 总线",
          "源码里找不到 SPEECH_INTERRUPTED 发射点")

    # 无论接线如何，都实测一次"真实 app.interrupt_speech() 是否让管线看到打断"
    from core.app import xbyaApp as _RealApp

    class _FakeApp:
        """最小的 app 壳：只提供 interrupt_speech 依赖的属性"""

        def __init__(self) -> None:
            self._interrupt_requested = False
            self.agent_stack = stack
            self.agent_bus = stack.bus      # 真实实现往这条总线上发事件
            self._audio_muted = False

        # ⚠️ **绑定真实实现，不要抄一份**。
        # 原写法在这里自己重写了一个 `interrupt_speech()`，语义是修复前的旧版
        # （只置标志、不发事件），于是这条核查无论产品代码怎么改都恒为 FAIL ——
        # 假件复刻了它本该检测的那个 bug，断言就成了空转。
        # 现在直接引用 `xbyaApp` 的方法（只假造它依赖的环境属性）：
        # 产品代码一旦回退，这里立刻变红。
        interrupt_speech = _RealApp.interrupt_speech
        notify_agent_interrupt = _RealApp.notify_agent_interrupt

    fake = _FakeApp()
    before = pipe.stats()["interrupts"]
    fake.interrupt_speech()
    time.sleep(0.1)
    seen = pipe.stats()["interrupts"] - before
    print(f"  app.interrupt_speech() 后，管线 interrupts 变化：{seen}")
    check(seen > 0,
          "真实打断入口（app.interrupt_speech / Ctrl+Alt+D）能被 Agent 管线感知",
          f"interrupts +{seen}")

    scenarios.append({"id": 3, "name": "打断", "utt": rt, "verdict": "见上方断言"})


def run_scenario_4(pipe, rec: Recorder, lb: Loopback, stack, desktop: Path,
                   sandbox: Path, idx: int) -> None:
    """场景 4：降级路径（P0，R6）「给我讲个笑话」/「你好呀」→ 原 LLM 闲聊链路"""
    print("\n[场景 4] 降级路径（P0 / R6）")
    rt = lb.roundtrip("给我讲个笑话", idx, "场景4-闲聊")
    # 实测 small 档对这句常见转写为「两个笑话」——"笑话"能保住，故只断言核心名词
    ok_kw, note = keyword_ok(rt["transcript"], ["笑话"])
    check(ok_kw, "ASR 转写基本正确（笑话）", note)

    rec.clear()
    pipe.handle_text(rt["transcript"], "s4")
    got_chat = wait_for(rec.all("pipeline.chat"), timeout=5.0)
    chat = rec.first("pipeline.chat")
    print(f"  pipeline.chat：text={chat.get('text')!r} conf={chat.get('confidence')}")
    check(got_chat, "非工具指令回退到 pipeline.chat（交回原 LLM 链路）")
    check(not rec.all(EventTypes.FEEDBACK_RESULT),
          "未误判为工具指令（无 feedback.result）")

    rt2 = lb.roundtrip("你好呀", idx + 1, "场景4-问候")
    ok_kw2, note2 = keyword_ok(rt2["transcript"], ["你好"])
    check(ok_kw2, "ASR 转写基本正确（你好）", note2)
    rec.clear()
    pipe.handle_text(rt2["transcript"], "s4b")
    ok_chat = wait_for(rec.all("pipeline.chat"), timeout=5.0)
    action = pipe._router.route(rt2["transcript"], {}).action       # noqa: SLF001
    check(ok_chat or action == "chat",
          "「你好呀」走原链路（pipeline.chat 或 chat 意图）", f"action={action}")

    # ── 降级：Agent 装配失败 → app.agent_stack=None → 走原 _run_llm_reply ──
    print("\n  [降级核查] Agent 装配失败 / 未启用时的回退")
    from core.app import xbyaApp

    pet_src = (PROJECT / "ui" / "pet_window.py").read_text(encoding="utf-8")
    app_src = (PROJECT / "core" / "app.py").read_text(encoding="utf-8")

    # 说明：这里**不实例化 PetWindow**（QWidget 子类必须走 Qt 的 __new__，且要 QApplication）。
    # 改为对三个可判定的契约做断言 —— 每一条都能在源码里指出位置。
    check("if self._agent_enabled:" in pet_src
          and "self._run_llm_reply(text)" in pet_src,
          "pet_window 的 Agent 分支受 _agent_enabled 守卫，否则走 _run_llm_reply",
          "ui/pet_window.py L1354/L1371")
    check("return False" in pet_src and "stack is None" in pet_src,
          "_agent_enabled 在 agent_stack is None 时返回 False",
          "ui/pet_window.py L332-338")
    check("self.agent_stack = None" in app_src and "except Exception" in app_src,
          "core.app 装配异常时把 agent_stack 置 None（异常不冒泡到语音链路）",
          "core/app.py L206-261")
    check(hasattr(xbyaApp, "_setup_agent_layer"),
          "core.app 有 Agent 装配收口点（_setup_agent_layer）")
    # 真实装配的"降级栈"必须把全部输入转成 chat
    offline = build_agent_stack(build_cfg(desktop, sandbox, enabled=False),
                                EventBus(), synthesize=None)
    offline.start()
    try:
        orec = Recorder(offline.bus)
        orec.clear()
        offline.pipeline.handle_text("找一下桌面上的合同", "s4c")
        _await_events(orec, ["pipeline.chat"], timeout=5.0)
        print(f"  enabled=False：events={[t for t, _ in orec.events][:6]}")
        check(bool(orec.all("pipeline.chat")) and not orec.all(EventTypes.FEEDBACK_RESULT),
              "agent.enabled=false 时全部转闲聊、不执行工具")
        check(offline.stats()["enabled"] is False, "栈如实报告 enabled=False")
        orec.dispose()
    finally:
        offline.dispose()

    scenarios.append({"id": 4, "name": "降级路径", "utt": rt, "utt2": rt2,
                      "verdict": "见上方断言"})


def run_scenario_5(pipe, rec: Recorder, lb: Loopback, desktop: Path,
                   audit_db: Path, stack, idx: int) -> None:
    """场景 5：安全边界（P0，R1）「打开 C 盘 Windows 文件夹」→ 拒绝 + 审计

    ⚠️ 实测发现（如实记录，未改产品代码）：这句口语**不会**走到路径白名单 ——
    规则路由把它解成 `open_app(target="c盘windows")`，而 white-list 预校验只拦
    "看起来是路径"的取值（`_looks_like_path`：绝对路径 / 含分隔符 / `~` / 盘符形式），
    "c盘windows" 是裸名字 → 跳过校验 → 交给 `OpenAppTool` → 找不到该应用 → 失败。

    结果**依然是安全的**（什么都没打开），但"被拒绝并友好提示 + 审计留痕"这两条
    §7.3 要求没有发生。故本场景按三段验证：
      A. 真实语音路径：不得执行越界动作（安全）+ 说明实际行为与原因
      B. 显式路径形态（"打开 C:\\Windows\\System32\\cmd.exe"）：白名单必须硬拒绝 + 审计
      C. 审计库里能否看到这次语音尝试
    """
    print("\n[场景 5] 安全边界（P0 / R1）")
    rt = lb.roundtrip("打开 C 盘 Windows 文件夹", idx, "场景5")
    ok_kw, note = keyword_ok(rt["transcript"], ["打开", "Windows", "文件夹"])
    check(ok_kw, "ASR 转写基本正确（打开+Windows+文件夹）", note)

    rec.clear()
    cmd = pipe.handle_text(rt["transcript"], "s5")
    _await_events(rec, [EventTypes.FEEDBACK_RESULT, EventTypes.FEEDBACK_CONFIRM],
                  timeout=8.0)
    res = rec.first(EventTypes.FEEDBACK_RESULT)
    confirms = rec.all(EventTypes.FEEDBACK_CONFIRM)
    print(f"  路由结果：action={cmd.action} params={cmd.params}")
    print(f"  结果：ok={res.get('success')} summary={res.get('summary','')!r}")
    print(f"  确认事件：{len(confirms)}")

    # A. 安全底线：无论走哪条路，都不能真的执行越界动作
    check(cmd.action in ("open_app", "file_read", "file_list", "chat"),
          "越界说法未被解成写/删类动作", str(cmd.action))
    check(bool(res) or bool(confirms), "越界指令有明确反馈（拒绝或求证）")
    check(not res or res.get("success") is False,
          "越界指令没有被成功执行（安全底线）",
          f"success={res.get('success') if res else None}")

    # 真实行为如实记录：这里期望的是"白名单硬拒绝"，实测是"工具层没找到"
    summary = str(res.get("summary", ""))
    hard_reject = any(k in summary for k in ("不能", "不行", "不可以", "不允许", "白名单"))
    if hard_reject:
        check(True, "被白名单硬拒绝且理由可读", summary[:60])
    else:
        check(False, "被白名单硬拒绝并友好提示（§7.3 第 2 条）",
              f"实际为工具层失败而非白名单拒绝：{summary[:50]!r}")
        print("  说明：该说法未构成路径，白名单预校验未介入（见函数 docstring）")

    # B. 显式路径形态：白名单必须硬拒绝
    explicit = r"打开 C:\Windows\System32\cmd.exe"
    rec.clear()
    cmd2 = pipe.handle_text(explicit, "s5b")
    _await_events(rec, [EventTypes.FEEDBACK_RESULT], timeout=8.0)
    res2 = rec.first(EventTypes.FEEDBACK_RESULT)
    print(f"  [显式路径] action={cmd2.action} params={cmd2.params}")
    print(f"  [显式路径] ok={res2.get('success')} summary={res2.get('summary','')!r}")
    check(res2.get("success") is False, "显式越界路径被拒绝", str(res2.get("success")))
    check(any(k in str(res2.get("summary", "")) for k in ("不能", "不允许", "白名单", "不行")),
          "显式越界路径的拒绝理由可读", str(res2.get("summary", ""))[:60])

    # C. 审计留痕
    rows = _audit_rows(audit_db)
    print(f"  审计库共 {len(rows)} 条：" +
          ", ".join(f"{r.get('action')}(success={r.get('success')})" for r in rows[:6]))
    deny_rows = [r for r in rows if r.get("success") in (0, False)]
    check(bool(deny_rows), "审计日志记录了被拒的操作", f"{len(deny_rows)} 条")
    if deny_rows:
        print(f"  审计（最新拒绝）：action={deny_rows[0].get('action')} "
              f"detail={str(deny_rows[0].get('detail'))[:70]!r}")
    voice_deny = [r for r in deny_rows
                  if "盘" in str(r.get("params", ""))
                  or "c:\\windows" in str(r.get("params", "")).lower()]
    if voice_deny:
        check(True, "语音那句的越界尝试也在审计里留痕")
    else:
        check(False, "语音那句的越界尝试在审计里留痕（§7.3 第 3 条）",
              "该句未构成路径 → 白名单未介入 → 无拒绝记录（工具层失败不入审计）")
        # 顺带把真实的审计参数打出来，便于人工核对是哪一条被记了
        for r in rows:
            print(f"    audit: action={r.get('action')} success={r.get('success')} "
                  f"params={str(r.get('params'))[:70]!r}")
    scenarios.append({"id": 5, "name": "安全边界", "utt": rt, "verdict": "见上方断言"})


def run_short_utterance_probe(lb: Loopback, rounds: int = 4) -> None:
    """短句可识别性探针：为什么场景 2 的取消语不用「算了」

    动机：`CONFIRM_PHRASES` / `CANCEL_PHRASES` 里全是 2~3 字短句
    （"确定" / "好的" / "算了" / "取消"…），而短句恰恰是 ASR 最不稳的输入。
    这里对几个候选各跑 `rounds` 次 TTS→ASR，把命中率如实打出来 ——
    命中的用，不命中的作为发现记录，**不为了让场景过而挑一条运气好的**。
    """
    candidates = ["确定", "好的", "算了", "不用了", "取消", "停止"]
    print(f"\n[短句可识别性探针] 每个候选 {rounds} 次 TTS→ASR（模型 {lb.model_size}）")
    table = []
    for i, text in enumerate(candidates):
        outs = []
        for k in range(rounds):
            rec = lb.roundtrip(text, 900 + i * 10 + k, f"probe-{text}-{k+1}")
            outs.append(rec.get("transcript") or "")
        hits = sum(1 for o in outs if text in normalize(o)[0] or text in o)
        table.append((text, hits, outs))
        print(f"  {text!r:8s} 命中 {hits}/{rounds} → {outs}")
    bad = [t for t, h, _ in table if h < rounds]
    if bad:
        degrade("确认/取消短句在 ASR 回环下稳定可识别",
                f"这些候选未全中：{ [(t, h) for t, h, _ in table if h < rounds] }"
                "（真人真机会受环境影响，此处仅记录回环实测）")


def main() -> int:
    ap = argparse.ArgumentParser(description="欣雅语音场景回环验证（TTS→ASR→真实 Agent 管线）")
    ap.add_argument("--voice", default="zh-CN-XiaoxiaoNeural", help="edge_tts 中文语音")
    ap.add_argument("--model", default="small", help="faster-whisper 模型（须本机已缓存）")
    ap.add_argument("--no-tts", action="store_true", help="跳过 TTS/ASR，只验管线行为")
    ap.add_argument("--asr-only", action="store_true", help="只跑 TTS→ASR 回环")
    ap.add_argument("--short-utterance-probe", action="store_true",
                    help="额外跑短句（确认/取消语）可识别性探针")
    ap.add_argument("--stimulus-dir", default="docs/agent/evidence/voice/fixtures",
                    help="固定测激目录（默认 docs/agent/evidence/voice/fixtures）")
    ap.add_argument("--fresh-stimulus", action="store_true",
                    help="每次重新合成测激（用于测量替身抖动，测激不再可重复）")
    ap.add_argument("--no-stimulus-cache", action="store_true",
                    help="既不读也不写固定测激（等价于不落盘的一次性合成）")
    ap.add_argument("-v", "--verbose", action="store_true", help="打印 INFO 日志")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    print("=" * 74)
    print("欣雅 · 语音场景回环验证（acceptance.md §7.3 五项）")
    print("=" * 74)
    print("证据强度：覆盖 ASR→Agent 管线→事件/审计；**不覆盖麦克风硬件采集与 GUI 动效**")
    print("本回环 ≠ 人工验收。详见脚本 docstring 与 docs/agent/evidence/voice/loopback.txt")

    env = Loopback.probe()
    print(f"\n环境：av={env['av']} edge_tts={env['edge_tts']} "
          f"faster_whisper={env['faster_whisper']}")
    print(f"ASR 模型：{args.model}（config.yaml 写的是 medium，本机未缓存 medium）")
    print(f"TTS 语音：{args.voice}")
    if args.no_stimulus_cache:
        print("测激模式：**每次现场合成、不落盘**（测替身抖动，结果不可重复）")
    elif args.fresh_stimulus:
        print(f"测激模式：**每次现场合成并覆盖固定测激** {args.stimulus_dir}"
              f"（测替身抖动，结果不可重复）")
    else:
        print(f"测激模式：**固定测激** {args.stimulus_dir}"
              f"（可重复；缺哪句就现场合成哪句并固定下来）")
        print("          —— 回环测的是**产品**，TTS 只是「人嘴」的替身；"
              "替身抖动另用 --fresh-stimulus 单独测")

    if env["av"] is None and not args.no_tts:
        degrade("PyAV(av) 可用（mp3 解码依赖）",
                "av 不可用：edge_tts 的 mp3 无法解码；改用 --no-tts（则不是 ASR 回环）")
        return 1

    desktop = make_sandbox()
    sandbox = desktop.parent            # 审计库/记忆库落在白名单之外
    seed_desktop(desktop)
    audit_db = sandbox / "audit.db"

    stim_dir = None if args.no_stimulus_cache else (PROJECT / args.stimulus_dir)
    lb = Loopback(voice=args.voice, model_size=args.model,
                  enabled=not args.no_tts,
                  stimulus_dir=stim_dir, fresh_stimulus=args.fresh_stimulus)
    stack = None
    try:
        if args.asr_only:
            print("\n[仅 ASR 回环] 逐句合成 → 转写")
            for i, text in enumerate(["找一下桌面上的 PDF 文件", "删除桌面上的截图",
                                      "确定", "给我讲个笑话", "打开 C 盘 Windows 文件夹"]):
                lb.roundtrip(text, i, f"asr{i+1}")
            if lb.load_seconds:
                print(f"\nASR 模型加载耗时：{lb.load_seconds:.1f}s")
        else:
            bus = EventBus()
            rec = Recorder(bus)
            cfg = build_cfg(desktop, sandbox)
            stack = build_agent_stack(cfg, bus, synthesize=None)
            stack.start()
            st = stack.stats()
            print(f"\n装配：tools={st['tools']} router={st['router']} "
                  f"planner={st['planner']} memory={st['memory']} enabled={st['enabled']}")
            print(f"白名单：{st['safety_whitelist']}")
            check(st["enabled"] is True, "Agent 栈按真实配置装配并启用",
                  f"{st['tools']} 个工具")
            check(st["safety_whitelist"] == [str(desktop)], "白名单 = 沙箱 Desktop")

            pipe = stack.pipeline
            run_scenario_1(pipe, rec, lb, desktop, 0)
            run_scenario_2(pipe, rec, lb, desktop, 10, audit_db)
            run_scenario_3(pipe, rec, lb, stack, 20)
            run_scenario_4(pipe, rec, lb, stack, desktop, sandbox, 30)
            run_scenario_5(pipe, rec, lb, desktop, audit_db, stack, 40)

            if lb.load_seconds is not None:
                print(f"\nASR 模型加载耗时（一次性）：{lb.load_seconds:.1f}s")
            if args.short_utterance_probe:
                run_short_utterance_probe(lb)
            print("\n[ASR 回环汇总]")
            real = [r for r in lb.records if r.get("mode", "").startswith("edge_tts")]
            tot_asr = sum(r.get("asr_s", 0) for r in real)
            tot_tts = sum(r.get("tts_s", 0) for r in real)
            if real:
                print(f"  真实回环 {len(real)} 句（另有 {len(lb.records)-len(real)} 句按 --no-tts 跳过）："
                      f"TTS 合计 {tot_tts:.1f}s，ASR 合计 {tot_asr:.1f}s"
                      f"（均值 {tot_asr/len(real):.2f}s/句）")
            else:
                print(f"  本轮到 {len(lb.records)} 句全部按 --no-tts 跳过（未走 TTS/ASR）")
            rec.dispose()
    finally:
        if stack is not None:
            stack.dispose()
        lb.cleanup()
        shutil.rmtree(sandbox, ignore_errors=True)

    passed = sum(1 for s, _, _ in results if s == PASS)
    failed = [r for r in results if r[0] == FAIL]
    degr = [r for r in results if r[0] == DEGRADED]
    total = len(results)
    print("\n" + "=" * 74)
    print(f"语音回环验证结果：{passed}/{total} 通过"
          f"（失败 {len(failed)}，降级 {len(degr)}）")
    for s, label, detail in results:
        if s != PASS:
            print(f"  {s} {label}" + (f"  → {detail}" if detail else ""))
    print("覆盖范围：ASR → Agent 管线 → 事件/审计/回收站；**不含麦克风采集与 GUI 动效**")
    print("=" * 74)
    return 0 if (passed == total and not degr) else 1


if __name__ == "__main__":
    raise SystemExit(main())
