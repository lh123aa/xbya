# 🗺️ P6 迭代计划 — 欣雅桌面管家功能补全

> 由「驾驭工程」方法论产出，基于系统框架完整性审计与功能缺口分析。
> 审计日期：2026-09-15 | 基线测试：3028 passed / 1 failed* / 3 skipped

---

## 一、缺口全景（12 项，按 P0/P1/P2 分级）

### 判定标准
- **P0（阻塞核心体验）**：用户日常使用中必碰、不修=产品不可用
- **P1（影响完整性）**：功能缺失但有变通路径
- **P2（锦上添花）**：体验优化、防御性加固

| # | 缺口 | 级别 | 来源 |
|---|------|------|------|
| F1 | 情绪标记 `(开心)` 未清洗，字幕显示脏字 | **P0** | 用户反馈 |
| F2 | 打断热键默认值 `\` 干扰打字 | **P0** | 测试报告 H1 |
| F3 | 恢复默认值不完整（6 项不重置） | **P1** | 测试报告 S1-S4 |
| F4 | 快捷键设置缺少第 4 个（麦克风轮换） | **P1** | 测试报告 H2 |
| F5 | 麦克风增益无设置 UI | **P1** | 测试报告 G3 |
| F6 | 唤醒词无设置 UI | **P1** | 测试报告 G5 |
| F7 | VAD 过滤无设置 UI（D19 待人工） | **P1** | AGENTS.md D19 |
| F8 | 热键开关不完全（mic_next 不受控） | **P2** | 测试报告 H3 |
| F9 | 试听播放器缓冲区与主播放器不一致 | **P2** | 测试报告 S5 |
| F10 | ConfigManager.set() 整份落盘（D21） | **P2** | AGENTS.md D21 |
| F11 | 语音页缺少最大监听时长 UI | **P2** | 语音页缺失 |
| F12 | 语音页缺少回声冷却 UI | **P2** | 语音页缺失 |

---

## 二、逐项详设

### F1 — 情绪标记清洗（P0）

**现状**：`_sanitize_text()` 清洗 `[EMOTION:happy]` 但**不清洗** LLM 输出的 `(开心)` `(笑)` `(歪头)` 等括号情绪标记。

**目标**：字幕和 TTS 输入中不再出现任何情绪标记。

**改动方案**：

```python
# ui/pet_window.py  _sanitize_text() 末尾新增
# 去掉括号包裹的中文情绪标记：(开心) (笑) (歪头) (害羞) (叹气) 等
text = re.sub(r'[（(][\u4e00-\u9fff]{1,6}[）)]', '', text)
```

**注意**：正则范围限制为 1-6 个汉字 + 中英文括号，避免误删正常括号内容（如"（含税价）"）。需要补充测试用例覆盖边界：
- `(开心)` → 空 ✅
- `（笑）` → 空 ✅
- `用户说（很好）` → `用户说` ✅
- `(含税价100元)` → 保留（太长，>6字不匹配）✅
- 正常括号内容 `http://example.com` → 保留 ✅

**验收**：新增 5+ 条参数化测试用例

---

### F2 — 打断热键默认值（P0）

**现状**：`config.yaml` 中 `voice.hotkey_interrupt: \`（反斜杠），单键无修饰，全局监听会干扰打字。

**改动方案**：
1. `config.yaml` 将 `hotkey_interrupt` 默认值改为 `Ctrl+Alt+D`（注释中已提过这个值）
2. `ui/settings_dialog.py` 的 `DEFAULT_VALUES` 字典同步更新

**验收**：`config.yaml` 中 `hotkey_interrupt` 值为 `Ctrl+Alt+D`

---

### F3 — 恢复默认值补全（P1）

**现状**：`_fill_default_values()` 不重置以下 6 项：
- `plugins.tts.params.rate`（TTS 语速）
- `plugins.tts.params.pitch`（TTS 音调）
- `plugins.llm.engine`（LLM 引擎选择）
- `ui.subtitle_fg_color`（字幕前景色）
- `ui.subtitle_bg_color`（字幕背景色）
- `voice.hotkey_interrupt`（打断热键）

**改动方案**：

在 `_fill_default_values()` 中补全：

```python
# TTS 语速/音调
self.tts_rate_slider.setValue(d.get("plugins.tts.params.rate", 0))
self._update_rate_label(self.tts_rate_slider.value())
self.tts_pitch_slider.setValue(d.get("plugins.tts.params.pitch", 0))
self._update_pitch_label(self.tts_pitch_slider.value())

# LLM 引擎
engine = d.get("plugins.llm.engine", "ollama")
ei = self.llm_engine_combo.findData(engine)
self.llm_engine_combo.setCurrentIndex(max(ei, 0))

# 字幕颜色
fg = d.get("ui.subtitle_fg_color", "#FFFFFF")
bg = d.get("ui.subtitle_bg_color", "#000000")
self._subtitle_fg_color = fg
self._subtitle_bg_color = bg
self.subtitle_fg_btn.setText(fg)
self.subtitle_bg_btn.setText(bg)

# 打断热键
self.interrupt_hotkey_btn.set_combo(d.get("voice.hotkey_interrupt", "Ctrl+Alt+D"))
```

**验收**：点击恢复默认后，上述 6 项回到默认值

---

### F4 — 快捷键页补全麦克风轮换（P1）

**现状**：设置对话框快捷键页只显示 3 个热键（toggle/mute/interrupt），第 4 个（mic_cycle `Ctrl+Alt+N`）无 UI。

**改动方案**：在 `_tab_hotkey()` 中新增一行：

```python
# 麦克风轮换
self.mic_cycle_hotkey_btn = HotkeyCaptureButton()
self.mic_cycle_hotkey_btn.setToolTip("按顺序轮换麦克风设备（Ctrl+Alt+N）")
form.addRow("切换麦克风", self.mic_cycle_hotkey_btn)
```

同步更新 `_load_values()` 和 `apply_to_config()` 中读写 `voice.hotkey_mic_next`。

---

### F5 — 麦克风增益设置 UI（P1）

**现状**：`config.yaml` 有 `voice.mic_gain: 2.5` 但设置对话框无对应 UI。

**改动方案**：在语音页（`_tab_asr()`）新增滑块：

```python
self.mic_gain_slider = QSlider(Qt.Horizontal)
self.mic_gain_slider.setRange(1, 10)  # 1.0 ~ 10.0
self.mic_gain_slider.setSingleStep(1)
self.mic_gain_slider.setValue(int(cfg.get("voice.mic_gain", 2.5) * 2))
self.mic_gain_label = QLabel("2.5")
self.mic_gain_slider.valueChanged.connect(
    lambda v: self.mic_gain_label.setText(f"{v/2:.1f}"))
form.addRow("麦克风增益", ...)
```

**验收**：滑块值保存到 `voice.mic_gain`，重启后生效

---

### F6 — 唤醒词设置 UI（P1）

**现状**：`config.yaml` 有 `voice.wake_word: 嘿欣雅` 但无 UI。

**改动方案**：在语音页新增输入框：

```python
self.wake_word_edit = QLineEdit()
form.addRow("唤醒词", self.wake_word_edit)
```

读写 `voice.wake_word`。

---

### F7 — VAD 过滤开关 UI（P1）

**现状**：`config.yaml` 有 `voice.vad_filter: false` 但无 UI。D19 登记为"待人工"。

**改动方案**：在语音页新增开关：

```python
self.vad_filter_check = QCheckBox("VAD 过滤（过滤环境噪音，但可能截断长句）")
form.addRow("VAD", self.vad_filter_check)
```

读写 `voice.vad_filter`。**注意**：D19 的根因（真实噪声下的取舍）仍需人工验证，但至少让用户能自行开关。

---

### F8 — 热键开关补全 mic_next（P2）

**现状**：`rebind_hotkey()` 中 `register_extra(mic_combo, ...)` 不受 `hotkey_enabled` 控制。

**改动**：在 `rebind_hotkey()` 中将 mic_next 的注册也包在 `if cm.get("voice.hotkey_enabled", True)` 内。

---

### F9 — 试听播放器缓冲区统一（P2）

**现状**：`_preview_thread()` 中 `pygame.mixer.init()` 用默认缓冲区（~46ms），主播放器用 `pre_init(buffer=1024)`（~23ms）。

**改动**：在 `_preview_thread()` 开头加 `pygame.mixer.pre_init(buffer=1024)` 再 `init()`。

---

### F10 — ConfigManager.set() 行为收窄（P2）

**现状**：D21——`set()` 无条件落盘整份配置，含默认值和注释被 `yaml.dump` 吃掉。

**改动方案**（需连同调用点一起改）：
1. 将 `set()` 改为只写内存，新增 `save()` 显式落盘
2. 给所有现有调用点（`ui/pet_window.py` 4 处 + `core/app.py` 1 处）补上 `save()`
3. 测试中用 `monkeypatch` 拦截 `save()` 防止污染 `config.yaml`

**风险**：改 `set()` 行为是行为变更，需确保所有调用点都被覆盖。

**验收**：切换字幕开关 → 重启 → 仍生效；测试跑完 config.yaml MD5 不变。

---

### F11-F12 — 语音页补全参数 UI（P2）

**现状**：语音页缺少 `max_listen_seconds` 和 `echo_cooldown` 的可配置入口。

**改动**：在 `_tab_asr()` 或 `_tab_voice()` 中补全 `max_listen_spin` 和 `echo_cooldown_spin` 的实际 UI 控件（当前代码有 `setValue` 但无对应 `addRow`）。

---

## 三、实施顺序（推荐）

```
Phase A（1-2h）—— 阻塞修复
├── F1  情绪标记清洗（P0，改动最小，风险最低）
└── F2  打断热键默认值（P0，改配置即可）

Phase B（2-3h）—— 设置页补全
├── F3  恢复默认值补全（P1）
├── F4  快捷键页补全 mic_cycle（P1）
├── F5  麦克风增益 UI（P1）
├── F6  唤醒词 UI（P1）
└── F7  VAD 开关 UI（P1）

Phase C（1-2h）—— 防御加固
├── F8  热键开关补全（P2）
├── F9  试听缓冲区统一（P2）
├── F10 ConfigManager.set() 收窄（P2，需连同调用点）
└── F11-F12 语音页补全（P2）
```

**总工时估算**：4-7 小时（单人开发）

---

## 四、验收标准

### Phase A 验收
- [ ] 字幕中不再出现 `(开心)` 等括号情绪标记（5+ 测试用例）
- [ ] `config.yaml` 中 `hotkey_interrupt` 值为 `Ctrl+Alt+D`
- [ ] 全量回归 0 新增失败

### Phase B 验收
- [ ] 恢复默认后 TTS 语速/音调回到 0
- [ ] 恢复默认后 LLM 引擎回到 ollama
- [ ] 恢复默认后字幕颜色回到白底黑字
- [ ] 恢复默认后打断热键回到 Ctrl+Alt+D
- [ ] 快捷键页显示 4 个热键，mic_cycle 可配置
- [ ] 语音页显示增益滑块、唤醒词输入框、VAD 开关
- [ ] 所有新控件的值正确读写 config.yaml

### Phase C 验收
- [ ] 热键全部关闭后 mic_cycle 也不注册
- [ ] 试听播放器延迟与主播放器一致
- [ ] config.yaml 在测试前后 MD5 不变（D21 修复判据）

---

## 五、风险

| # | 风险 | 概率 | 应对 |
|---|------|------|------|
| R1 | F1 正则误删正常括号内容 | 低 | 限制 1-6 字 + 测试覆盖边界 |
| R2 | F10 改 set() 行为导致调用点遗漏 | 中 | 先搜全仓 set() 调用点再改 |
| R3 | F7 开关 VAD 后噪音幻觉（D19） | 中 | 标注警告文案，让用户自决 |

---

## 六、不在本轮范围

| 项 | 理由 |
|---|------|
| D19 真实噪声录音对比 | 需硬件环境，属人工验收 |
| D21 完整修复（含 yaml.dump 注释保留） | 改动面大，建议独立轮次 |
| P5-C1 轮换 key 人工验证 | 需真机操作 |
| P5-C2 麦克风+GUI+人耳验收 | 需人工 |

---

## 七、执行结果（2026-09-15）

| Phase | 结果 | 测试 |
|-------|------|------|
| **Phase A** | ✅ F1 + F2 完成 | 3038 passed |
| **Phase B** | ✅ F3-F7 完成 | 3038 passed |
| **Phase C** | ✅ F9 完成；F8/F10/F11-12 已确认无需修改 | 3038 passed |

### 改动文件

| 文件 | 改动 |
|------|------|
| `ui/pet_window.py` | F1: `_sanitize_text()` 新增括号情绪正则 + 调整控制标记与链接清洗顺序 |
| `config.yaml` | F2: `hotkey_interrupt: \` → `Ctrl+Alt+D` |
| `ui/settings_dialog.py` | F3: `_fill_default_values()` 补全 12 项；F4: 快捷键页新增 mic_cycle；F5: 增益滑块；F6: 唤醒词输入框；F7: VAD 开关；F9: 试听缓冲区统一 |
| `tests/test_subtitle_dynamic.py` | F1: 新增 10 条测试用例（6 正向 + 4 反向） |

### F8/F10/F11-12 已确认无需修改

- **F8**（热键开关补全）：代码已确认 mic_next 在 `hotkey_enabled=False` 时不注册
- **F10**（ConfigManager.set() 收窄）：已实现 `_explicit_keys` 守护，只覆盖显式 set 过的键
- **F11-12**（语音页参数）：`max_listen_spin` / `echo_cooldown_spin` 已在语音页

---

**文档版本：** v1.1（执行完毕）
**产出方式：** 驾驭工程引导 + 系统框架审计 + 自动化验收
