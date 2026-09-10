# 常驻语音监听（无唤醒词）设计文档

> 2026-09-01 | 小忆桌面宠物 v3.0 功能演进

## 目标

- 移除"点击开始对话"：鼠标仅用于拖拽移动
- 常驻监听：用户说话即响应，无需任何操作
- 误触发尽量静默，不打扰用户

## 架构

### 1. 常驻监听线程（低 CPU ~1-3%）

复用 `services/microphone_service.py` 的 MicrophoneService，新增 `listen_standby(callback)` 模式：

- 循环读取 1024 帧 chunk，每 4 个 chunk 计算一次能量（`np.mean(np.abs())`）
- **语音开始**：音量 > 300 且持续 ≥ 2 个检测点（约 0.5 秒）
- **语音结束**：语音后静音连续 1 秒
- 结束后生成 wav 文件并回调（`callback(wav_path)`）
- **单次最长 10 秒**（防背景声死循环）

### 2. 回声防护（关键工程点）

- TTS 播放期间（`app.speak()` 前后）暂停监听
- 回答结束后冷却 **3 秒**再恢复监听（扬声器声 → 麦克风回环是"自问自答"循环的元凶）

### 3. 应答链路（复用现有）

- 检测到说话 → `listen` 状态 → ASR 识别（faster-whisper）→ LLM 回答（Ollama）→ `talk` 状态 + 气泡 + TTS
- **无效响应静默**：
  - 识别为空（None）/ 文本 < 3 字 / no_speech 幻觉 → 不弹气泡、不回答
  - 连续 3 次无效 → 额外静默 5 秒（防环境音循环）
- 状态机状态切换：`listen` → `think` → `talk` → `idle`

### 4. 点击触发移除

- 删除 `ui/pet_window.py` 中 `mousePressEvent/mouseReleaseEvent` 的单击判定与 `_on_click()` 入口
- 保留拖拽移动（窗口跟随鼠标）
- 保留 `_voice_pipeline()` 逻辑，改由监听回调触发；异常路径仍显示气泡

### 5. 配置（config.yaml）

```yaml
voice:
  listening: true        # 常驻监听开关
  speech_volume: 300     # 说话判定音量阈值（int16振幅）
  max_listen_seconds: 10 # 单次最长录音秒数
  echo_cooldown: 3       # TTS 后冷却秒数
  reply_min_length: 3    # 响应最小字数（少于则静默）
```

已在 config.yaml 中存在的 `voice.wake_word` 保留但不再使用（无唤醒词模式）。

## 性能预期

- 常驻监听 CPU ~1-3%（纯能量检测，无模型推理）
- ASR/LLM 仅在检测到有效语音时运行（成本与现有一致）
- 记忆占用增量 < 20MB

## 测试计划

1. **监听状态机**：用模拟音频流（wav 注入）验证 静音→说话→结束→回调 与 无语音时不回调
2. **单次上限**：持续噪声 10 秒强制结束
3. **回声冷却**：TTS 播放期间监听暂停、播完 +3s 恢复
4. **无效响应静默**：噪声音频 / 短文本 → 无气泡无回答；连续 3 次 → 静默 5 秒
5. **点击移除回归**：拖拽仍工作；单击不再触发对话
6. 现有测试套件保持通过（173 + 2 pre-existing 失败）

## 涉及文件

| 文件 | 改动 |
|------|------|
| `services/microphone_service.py` | 新增 `listen_standby()` 常驻监听 |
| `ui/pet_window.py` | 移除单击触发；新增监听集成（启动/停止、回调、回声冷却、无效静默） |
| `config.yaml` | 新增 voice 监听配置 |
| `tests/test_*` | 新增监听/冷却/静默测试，更新点击回归测试 |
