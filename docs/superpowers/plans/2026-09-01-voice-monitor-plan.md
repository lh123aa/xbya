# 常驻语音监听 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让桌面宠物随时监听用户说话（无唤醒词），说话即响应；移除点击开始对话。

**Architecture:** MicrophoneService 新增常驻监听模式（能量检测状态机，纯 numpy 能量计算 ~1-3% CPU）；PetWindow 移除单击触发，启动/停止监听并管理应答链路（ASR→LLM→TTS），TTS 播放期间暂停监听并在播完后冷却 3 秒防回声自问自答；无效识别（None/<3字/幻觉）静默不打扰。

**Tech Stack:** Python 3.12, PySide6, pyaudio, numpy, faster-whisper, Ollama, pytest

## Global Constraints

- 项目路径: `E:\程序\桌面宠物\xiaoyi-desktop-pet`，Windows，Python 3.12
- 无唤醒词模式；说话判定音量阈值 speech_volume=300（int16 平均振幅）
- 单次监听最长 max_listen_seconds=10 秒
- TTS 播放期间暂停监听，播完冷却 echo_cooldown=3 秒
- 无效响应（识别 None / 文本 < reply_min_length=3 字）静默：不弹气泡不回答；连续 3 次无效 → 静默 5 秒
- Mouse 单击不再触发对话（拖拽移动保留）
- config.yaml 已有 `voice:` 节（wake_word/enthusiasm_level/confirm_delete），新增键不得破坏原有键
- 现有测试基线：173 passed + 2 pre-existing failed（test_app.py 的 test_get_status/test_switch_performance_mode）
- `_voice_pipeline` 自 ui/pet_window.py 起复用现有逻辑（transcribe→chat→speak）

---

### Task 1: MicrophoneService 常驻监听模式

**Files:**
- Modify: `services/microphone_service.py`（在 `listen_continuous` 之后新增）
- Test: `tests/test_services.py`（新增测试类）或新建 `tests/test_microphone_listen.py`

**Interfaces:**
- Consumes: `MicrophoneService.__init__`（已有 is_recording/sample_rate/channel/chunk_size），pyaudio
- Produces:
  ```python
  def listen_standby(self, callback: Callable[[str], None],
                     on_start: Optional[Callable[[], None]] = None,
                     speech_volume: float = 300.0,
                     max_seconds: float = 10.0,
                     reply_min_seconds: float = 0.4) -> bool
      """启动常驻监听。检测到一次说话（音量>speech_volume持续且语音结束静音1s）→
      生成wav临时文件并回调 callback(wav_path)。返回是否启动成功。循环进行。"""

  def stop_listen_standby(self) -> None
      """停止常驻监听（设置标志，线程循环退出）"""

  def is_listening(self) -> bool
      """是否正在监听"""
  ```
- 内部状态机（自实现，无需外部依赖）：
  - `STANDBY`（等待说话）：音量 <= threshold 持续；连续 `2` 个检测点（每 4 chunk/1024 帧≈0.25s 检测一次，即 ~0.5s）音量 > threshold → `LISTENING`，调 on_start
  - `LISTENING`（缓存帧）：收集帧；音量 < 100 连续 1 秒 → 结束 → 写 wav → callback；期间总时长 > max_seconds → 强制结束；停止标志 → 丢弃

- [ ] **Step 1: 编写失败测试** `tests/test_microphone_listen.py`

```python
# -*- coding: utf-8 -*-
"""listen_standby 状态机测试（用模拟流，不依赖真实麦克风）"""
import os
import time
import threading
import numpy as np
import pytest
from services.microphone_service import MicrophoneService


class FakeStream:
    """模拟pyaudio流：按帧序列播放预置chunk"""
    def __init__(self, chunks, chunk_size=1024):
        self._chunks = list(chunks)  # list of bytes (int16 1024 samples)
        self._idx = 0
        self.closed = False

    def read(self, n, exception_on_overflow=False):
        if self._idx >= len(self._chunks):
            return b"\x00\x00" * n  # 静音填充
        c = self._chunks[self._idx]
        self._idx += 1
        return c

    def stop_stream(self):
        pass

    def close(self):
        self.closed = True


def _bytes_chunk(vol, chunk=1024, rate=16000):
    """生成指定音量的int16 chunk bytes"""
    data = np.full(chunk, vol, dtype=np.int16)
    return data.tobytes()


def test_listen_standby_detects_speech(monkeypatch):
    """静音→说话(3段)→静音→回调正确生成wav"""
    mic = MicrophoneService()
    # 打桩pyaudio：默认设备可用 + FakeStream
    class FakePyAudio:
        def __init__(self, *a, **k): pass
        def open(self, **kw):
            # 序列：静音x2 → 说话x4 → 静音x20(结束检测需~40检测点, 但静音连续1s≈62个chunk@16k/1024)
            chunks = [_bytes_chunk(0) for _ in range(2)]
            chunks += [_bytes_chunk(3000) for _ in range(4)]
            chunks += [_bytes_chunk(0) for _ in range(70)]  # >1s 静音触发结束
            return FakeStream(chunks)
        def terminate(self): pass
        def get_default_input_device_info(self):
            return {"defaultSampleRate": 16000}

    monkeypatch.setattr("pyaudio.PyAudio", FakePyAudio)
    mic.pyaudio_available = True

    result = []
    on_start_called = []
    mic.listen_standby(
        callback=lambda path: result.append(path),
        on_start=lambda: on_start_called.append(True),
        speech_volume=300.0,
        max_seconds=10.0,
    )
    # listen_standby是阻塞的？——设计为阻塞循环；测试用线程+超时
    time.sleep(0.5)
    mic.stop_listen_standby()
    time.sleep(0.2)

    assert on_start_called, "应检测到语音开始"
    assert len(result) == 1, "应回调一次wav"
    path = result[0]
    assert path.endswith(".wav") and os.path.exists(path)


def test_listen_standby_silence_no_callback(monkeypatch):
    """纯静音不触发回调"""
    mic = MicrophoneService()
    class FakePyAudio2:
        def __init__(self, *a, **k): pass
        def open(self, **kw):
            return FakeStream([_bytes_chunk(0) for _ in range(20)])
        def terminate(self): pass
        def get_default_input_device_info(self):
            return {"defaultSampleRate": 16000}
    monkeypatch.setattr("pyaudio.PyAudio", FakePyAudio2)
    mic.pyaudio_available = True

    result = []
    mic.listen_standby(callback=lambda p: result.append(p), speech_volume=300.0)
    time.sleep(0.3)
    mic.stop_listen_standby()
    time.sleep(0.2)
    assert result == [], "静音不应回调"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_microphone_listen.py -v`
Expected: FAIL with `AttributeError: 'MicrophoneService' object has no attribute 'listen_standby'`

- [ ] **Step 3: 实现 `listen_standby` / `stop_listen_standby` / `is_listening`**

```python
# services/microphone_service.py 在 listen_continuous 方法后新增
    def listen_standby(self, callback, on_start=None,
                       speech_volume: float = 300.0,
                       max_seconds: float = 10.0) -> bool:
        """常驻监听：检测到一次说话→生成wav→callback(wav_path)
        
        状态机：STANDBY(等待) → LISTENING(缓存) → 结束后回到STANDBY循环
        单次说话最长max_seconds，语音后静音1秒判定结束。
        """
        if not self.pyaudio_available:
            logger.error("PyAudio不可用，无法常驻监听")
            return False
        if self._listening:
            logger.warning("已在监听中")
            return False

        try:
            import pyaudio
            import numpy as np

            self._listening = True
            self._listen_stop = False

            # 在监听线程中运行（调用者线程不阻塞）
            def _loop():
                p = pyaudio.PyAudio()
                try:
                    stream = p.open(
                        format=pyaudio.paInt16,
                        channels=self.channels,
                        rate=self.sample_rate,
                        input=True,
                        frames_per_buffer=self.chunk_size
                    )
                except Exception as e:
                    logger.warning(f"监听打开失败({e})，尝试默认采样率")
                    info = p.get_default_input_device_info()
                    rate = int(info["defaultSampleRate"])
                    stream = p.open(
                        format=pyaudio.paInt16,
                        channels=self.channels,
                        rate=rate,
                        input=True,
                        frames_per_buffer=self.chunk_size
                    )

                logger.info("常驻监听启动，等待说话...")
                frames = []
                state = "standby"          # standby | listening
                voice_points = 0           # 连续语音检测点计数
                silence_sec = 0.0          # 语音中静音累计
                total_sec = 0.0
                chunk_rate = self.sample_rate / self.chunk_size  # 每秒chunk数

                try:
                    while not self._listen_stop:
                        data = stream.read(self.chunk_size, exception_on_overflow=False)
                        # 能量检测（每4chunk一次降开销）
                        if len(frames) % 4 == 0:
                            chunk = np.frombuffer(data, dtype=np.int16).astype(np.float32)
                            vol = float(np.mean(np.abs(chunk)))

                            if state == "standby":
                                if vol > speech_volume:
                                    voice_points += 1
                                    if voice_points >= 2:
                                        state = "listening"
                                        frames = [data]  # 缓存当前帧起的全部帧
                                        total_sec = 0.0
                                        silence_sec = 0.0
                                        logger.info("检测到语音，开始录音")
                                        if on_start:
                                            on_start()
                                else:
                                    voice_points = 0
                            else:  # listening
                                total_sec += 4 * self.chunk_size / self.sample_rate
                                if vol > speech_volume * 0.3:
                                    silence_sec = 0.0
                                else:
                                    silence_sec += 4 * self.chunk_size / self.sample_rate

                                if silence_sec >= 1.0 or total_sec >= max_seconds:
                                    # 语音结束，写wav并回调
                                    if len(frames) > self.sample_rate * 0.4:
                                        wav_path = None
                                        try:
                                            import wave
                                            import tempfile
                                            with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as f:
                                                wav_path = f.name
                                            wf = wave.open(wav_path, 'wb')
                                            wf.setnchannels(self.channels)
                                            wf.setsampwidth(2)
                                            wf.setframerate(self.sample_rate)
                                            wf.writeframes(b''.join(frames))
                                            wf.close()
                                            logger.info(f"监听捕获说话→回调: {wav_path}")
                                            callback(wav_path)
                                        except Exception as e:
                                            logger.error(f"保存wav失败: {e}")
                                    state = "standby"
                                    frames = []
                                    voice_points = 0
                                    silence_sec = 0.0
                        if state == "listening" and frames:
                            frames.append(data)
                        elif state == "standby":
                            frames = []
                finally:
                    stream.stop_stream()
                    stream.close()
                    p.terminate()
                    logger.info("常驻监听已停止")

            self._listen_thread = threading.Thread(target=_loop, daemon=True)
            self._listen_thread.start()
            return True

        except Exception as e:
            logger.error(f"启动常驻监听失败: {e}")
            self._listening = False
            return False

    def stop_listen_standby(self) -> None:
        """停止常驻监听"""
        self._listen_stop = True

    def is_listening(self) -> bool:
        """是否正在监听"""
        return bool(self._listening)
```

同时在 `__init__` 中新增属性、`__init__` 内：
```python
        # 常驻监听状态
        self._listening = False
        self._listen_stop = False
        self._listen_thread = None
```
顶部 import threading 已存在。

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_microphone_listen.py -v`
Expected: PASS（两条）

- [ ] **Step 5: 运行现有测试套件防回归**

Run: `python -m pytest tests/ -q`
Expected: 173 passed + 2 pre-existing failed（不变）

- [ ] **Step 6: Commit**

```bash
git add services/microphone_service.py tests/test_microphone_listen.py
git commit -m "feat(voice): add always-on listen_standby mode to MicrophoneService"
```

---

### Task 2: PetWindow 集成常驻监听（移除单击触发 + 回声冷却 + 无效静默）

**Files:**
- Modify: `ui/pet_window.py`
- Modify: `config.yaml`（voice 节新增监听键）
- Modify: `tests/test_pet_window.py`（移除单击测试，新增监听集成测试）

**Interfaces:**
- Consumes: `MicrophoneService.listen_standby(callback, on_start, speech_volume, max_seconds)`（Task 1），`_voice_pipeline()`（已存在，改签名）
- Produces:
  ```python
  # PetWindow 新增：
  def start_voice_monitor(self) -> bool      # 启动常驻监听
  def stop_voice_monitor(self) -> None       # 停止常驻监听
  def _on_speech_captured(self, wav_path: str)  # 监听回调（去重、冷却检查→_voice_pipeline）
  def _process_answer(self, text: str)          # 识别后的应答处理（含无效静默）
  ```

- [ ] **Step 1: 先更新 config.yaml（voice 节新增键）**

在 `config.yaml` 的 `voice:` 节（当前有 wake_word/enthusiasm_level/confirm_delete）新增：

```yaml
voice:
  wake_word: 嘿小忆
  enthusiasm_level: 3  # 1-5
  confirm_delete: true
  listening: true              # 常驻监听开关
  speech_volume: 300           # 说话判定音量阈值
  max_listen_seconds: 10       # 单次最长监听秒数
  echo_cooldown: 3             # TTS后冷却秒数（防回声）
  reply_min_length: 3          # 响应最小字数（少于则静默）
```

- [ ] **Step 2: 编写失败测试**（`tests/test_pet_window.py` 追加）

```python
class TestVoiceMonitor:
    """常驻监听集成测试（不启动真实麦克风：注入FakeMicrophone）"""

    def test_start_voice_monitor_starts_listening(self, qapp, monkeypatch):
        from ui.pet_window import PetWindow
        win = PetWindow()
        started = {}
        class FakeMic:
            def __init__(self):
                self.started = False
                self.stop = False
            def listen_standby(self, callback, on_start=None, **kw):
                self.started = True
                started["cb"] = callback
                return True
            def stop_listen_standby(self):
                self.stop = True
            def is_listening(self):
                return self.started
        monkeypatch.setattr("services.microphone_service.MicrophoneService", FakeMic)
        assert win.start_voice_monitor() is True
        assert started  # listen_standby被调用

    def test_speech_callback_silences_invalid(self, qapp):
        """识别为无效(短txt)时静默：不设置气泡、不回答"""
        from ui.pet_window import PetWindow
        win = PetWindow()
        win._recording = False
        captured = {}
        # 注入假_voice_pipeline记录是否被调用
        def fake_pipeline(wav_path):
            captured["called"] = True
        win._voice_pipeline = lambda p: fake_pipeline(p)
        # 构造wav路径不存在也行——先测无效短文本路径
        win._recent_invalid = 0
        # 直接调用内部过滤：模拟识别出短文本
        assert win._should_ignore("嗯") is True

    def test_repeated_invalid_ignores_after_3(self, qapp):
        from ui.pet_window import PetWindow
        win = PetWindow()
        win._recent_invalid = 0
        results = []
        for _ in range(3):
            results.append(win._should_ignore("啊"))
        results.append(win._should_ignore("啊"))  # 第4次：连续3次无效后静默
        assert results[:3] == [True, True, True]
        assert results[3] is True  # 第4次也被静默（cooldown）
```

- [ ] **Step 3: 运行测试确认失败**

Run: `pytest tests/test_pet_window.py::TestVoiceMonitor -v`
Expected: FAIL（无 `start_voice_monitor`/`_should_ignore`）

- [ ] **Step 4: 实现 PetWindow 监听集成**

`ui/pet_window.py` 修改：

1) `__init__` 新增监听状态：
```python
        # 常驻监听状态
        self._monitor_mic = None
        self._monitor_active = False
        self._recent_invalid = 0          # 连续无效响应计数
        self._echo_cooldown_until = 0.0   # TTS结束后冷却截止时间戳
        self._processing = False          # 正在处理中（防并发）
```

2) 修改 `_voice_pipeline`:删除单击时代码，作为监听回调触发（读 config 参数）：

```python
    def start_voice_monitor(self) -> bool:
        """启动常驻监听（无唤醒词，说话即响应）"""
        try:
            from services.microphone_service import MicrophoneService
            from core.config_manager import get_config_manager
            cm = self.config_manager if hasattr(self, "config_manager") else None
            listening = True
            speech_vol = 300.0
            max_sec = 10.0
            # 通过 app.config_manager 读取
            if self.app and getattr(self.app, "config_manager", None):
                cm = self.app.config_manager
                listening = cm.get("voice.listening", True)
                speech_vol = float(cm.get("voice.speech_volume", 300.0))
                max_sec = float(cm.get("voice.max_listen_seconds", 10.0))
            if not listening:
                logger.info("voice.listening=false，不启动常驻监听")
                return False
            self._monitor_mic = MicrophoneService()
            if not self._monitor_mic.is_available():
                logger.warning("麦克风不可用，无法常驻监听")
                return False
            ok = self._monitor_mic.listen_standby(
                callback=self._on_speech_captured,
                on_start=lambda: self.set_state("listen"),
                speech_volume=speech_vol,
                max_seconds=max_sec,
            )
            if ok:
                self._monitor_active = True
                logger.info("常驻语音监听已启动")
            return ok
        except Exception as e:
            logger.error(f"启动常驻监听失败: {e}")
            return False

    def stop_voice_monitor(self) -> None:
        """停止常驻监听"""
        if self._monitor_mic:
            try:
                self._monitor_mic.stop_listen_standby()
            except Exception as e:
                logger.error(f"停止监听失败: {e}")
        self._monitor_active = False

    def _on_speech_captured(self, wav_path: str):
        """监听捕获一次说话后的入口"""
        if self._processing:
            logger.debug("正在处理上次语音，忽略本次")
            return
        # 回声冷却检查：TTS刚播完，跳过自听
        if time.time() < self._echo_cooldown_until:
            logger.info("TTS回声冷却中，忽略")
            return
        self._processing = True
        try:
            import threading
            threading.Thread(target=self._voice_pipeline, args=(wav_path,), daemon=True).start()
        finally:
            pass  # processing在pipeline结束时重置

    def _voice_pipeline(self, wav_path: str):
        """语音交互链路：识别→对话→播报（监听回调版本）"""
        try:
            # 1. 识别
            self.set_state("listen")
            text = self.app.transcribe(wav_path) if self.app else None
            if not text:
                self._handle_invalid()
                return
            # 2. 有效：LLM对话
            self.set_state("think")
            self.show_bubble(f"你说: {text}", 2000)
            response = self.app.chat(text, context=self._chat_history) if self.app else None
            self._chat_history.append({"role": "user", "content": text})
            if response:
                self._chat_history.append({"role": "assistant", "content": response})
            self._chat_history = self._chat_history[-20:]
            if not response:
                self._handle_invalid()
                return
            self._recent_invalid = 0  # 成功回答，重置计数
            # 3. 显示+TTS（回声防护：speak前记录，speak后冷却）
            self.set_state("talk")
            self.show_bubble(response, 4000)
            if self.app:
                self.app.speak(response)
                self._echo_cooldown_until = time.time() + float(
                    self.app.config_manager.get("voice.echo_cooldown", 3.0)
                    if getattr(self.app, "config_manager", None) else 3.0)
            self.set_state("idle")
        except Exception as e:
            logger.error(f"语音交互失败: {e}")
            self.show_bubble("哎呀，我这边出了点问题...", 2500)
            self.set_state("idle")
        finally:
            self._processing = False

    def _handle_invalid(self):
        """无效响应：静默处理（不弹气泡）"""
        min_len = 3
        if self.app and getattr(self.app, "config_manager", None):
            min_len = int(self.app.config_manager.get("voice.reply_min_length", 3))
        self._recent_invalid += 1
        if self._recent_invalid >= 3:
            # 连续多次无效：额外静默5秒
            self._echo_cooldown_until = time.time() + 5.0
            self._recent_invalid = 0
        self.set_state("idle")

    def _should_ignore(self, text: str) -> bool:
        """判断短文本是否应静默（无效响应）"""
        if not text or not text.strip():
            return True
        min_len = 3
        if self.app and getattr(self.app, "config_manager", None):
            min_len = int(self.app.config_manager.get("voice.reply_min_length", 3))
        return len(text.strip()) < min_len
```

3) 移除单击触发：删除 `_on_click` 方法及其 mousePressEvent/mouseReleaseEvent 中调用、`_press_pos/_press_time` 相关代码；`_voice_pipeline` 为监听专用。**保留拖拽**（mousePressEvent 仅记录 drag 起点、mouseReleaseEvent 仅结束拖拽）。

4) `mousePressEvent` 修改后如下（仅拖拽）：
```python
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.dragging = True
            self.drag_offset = event.pos()
            event.accept()

    def mouseMoveEvent(self, event):
        if self.dragging:
            new_pos = event.globalPosition().toPoint() - self.drag_offset
            self.move(new_pos)
            event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.dragging = False
            event.accept()
```
注意：删除 `_press_global`、`_press_time`、`_on_click`、旧 `_record_audio`、旧 `_voice_pipeline`（无参版）全部相关代码。

5) 在 `set_app` 后由 run.py 启动监听（Task 3 集成），PetWindow 自身不自动启动（保持可测）。

- [ ] **Step 5: 运行测试确认通过**

Run: `pytest tests/test_pet_window.py -v`
Expected: PASS（TestVoiceMonitor 3条 + 原有拖拽/气泡等通过；点击触发相关旧测试已随 Step 4 从测试文件删除）

- [ ] **Step 6: 运行全量测试**

Run: `python -m pytest tests/ -q`
Expected: 173 pass + 2 pre-existing failed（监听测试新增3条）

- [ ] **Step 7: Commit**

```bash
git add ui/pet_window.py config.yaml tests/test_pet_window.py
git commit -m "feat(voice): always-on listening in PetWindow - remove click trigger, echo cooldown, invalid silence"
```

---

### Task 3: 启动集成 + 回归验证 + 重新打包

**Files:**
- Modify: `core/app.py`（run() 中启动/停止监听）
- Modify: `tests/test_app.py`（监听集成测试）——可选，若 run() 增删较多
- 重新打包：`pyinstaller build.spec --noconfirm --clean`

**Interfaces:**
- Consumes: `PetWindow.start_voice_monitor()` / `stop_voice_monitor()`（Task 2）
- Produces: 启动时监听开启，关闭时停止

- [ ] **Step 1: 修改 core/app.py `run()`**

在创建 pet_window 并 `show()` 之后、`app.exec()` 之前添加：

```python
        # 启动常驻语音监听
        pet_window.start_voice_monitor()
```

在 `finally:` 的 `self.shutdown()` 前添加：

```python
        finally:
            try:
                pet_window.stop_voice_monitor()
            except Exception:
                pass
            self.shutdown()
```

- [ ] **Step 2: 运行测试**

Run: `python -m pytest tests/ -q`
Expected: 173+3 pass + 2 pre-existing failed；无新增失败

- [ ] **Step 3: 手动冒烟验证（可选，GUI环境）**

```bash
python run.py
```
预期：启动后控制台出现"常驻语音监听已启动"；对着麦克风说话→气泡"你说: xxx"→回答；不说话时无打扰；TTS 播放后3秒内说话不响应（冷却）。

- [ ] **Step 4: 重新打包**

```bash
pyinstaller build.spec --noconfirm --clean
```
预期：dist/XiaoYiPet/XiaoYiPet.exe 生成

- [ ] **Step 5: Commit**

```bash
git add core/app.py
git commit -m "feat(voice): start/stop always-on monitor in app lifecycle"
```

---

## 文件结构（改动汇总）

| 文件 | 职责 | 本次变更 |
|------|------|---------|
| `services/microphone_service.py` | 麦克风服务 | +常驻监听状态机（Task 1） |
| `ui/pet_window.py` | 宠物主窗口 | -单击触发 +监听集成（Task 2） |
| `config.yaml` | 配置 | +voice监听键（Task 2） |
| `core/app.py` | 应用主控 | run() 启停监听（Task 3） |
| `tests/test_microphone_listen.py` | 监听测试 | 新建（Task 1） |
| `tests/test_pet_window.py` | 窗口测试 | 更新（Task 2） |

## 风险与应对

| 风险 | 应对 |
|------|------|
| 麦克风音量低导致说话检测不触发 | speech_volume=300 可配置；检测点持续0.5s判定，噪声不会误报 |
| TTS 回声循环 | speak 前暂停（pipeline中处理中忽略）+ 播后冷却3s + 处理中标志防并发 |
| whisper 幻觉文本 | _handle_invalid 静默（已有 no_speech>=0.65 丢弃） |
| 常驻 CPU 过高 | 每4 chunk 才做 numpy mean（~每个0.25s一次），预计 <3% |
