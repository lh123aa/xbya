# 设置页面（Settings Dialog）设计文档

> 2026-09-01 | 小忆桌面宠物 v3.0

## 目标

为桌宠提供完整的系统设置界面：语音/AI/外观/快捷键/系统信息，含全局热键控制麦克风开关。

## 入口与形态

- **入口**：右键猫猫 → "设置..."（contextMenuEvent 已有菜单，添加菜单项）
- **形态**：非模态 QDialog（可边聊边改），左侧 Tab 导航 + 右侧设置项（类系统设置样式）
- **持久化**：点击"保存"→ `ConfigManager.set()` 写回 config.yaml；"取消"放弃修改
- **返回**：对话框自带"恢复默认"按钮（重置本组为默认值）与"确定/取消"
- **重启生效标注**：ASR/LLM/TTS 组项旁 ⚠️ 标注，保存后提示"部分设置需重启生效"

## 设置分组

### 1. 语音交互（实时生效）
| 设置项 | 控件 | 配置键 | 默认 |
|--------|------|--------|------|
| 常驻监听开关 | 开关 | voice.listening | true |
| 说话音量阈值 | 滑块 50-1000 | voice.speech_volume | 300 |
| 单次最长监听(秒) | QSpinBox 3-15 | voice.max_listen_seconds | 10 |
| TTS回声冷却(秒) | QSpinBox 1-10 | voice.echo_cooldown | 3 |
| 最小回复字数 | QSpinBox 1-10 | voice.reply_min_length | 3 |

### 2. AI 对话（重启生效）
| 设置项 | 控件 | 配置键 | 默认 |
|--------|------|--------|------|
| Ollama服务地址 | QLineEdit | plugins.llm.params.base_url | http://localhost:11434 |
| 模型 | QComboBox（动态读/api/tags） | plugins.llm.params.model | qwen2.5:1.5b |
| 人设提示词 | QPlainTextEdit(多行) | plugins.llm.params.system_prompt | 默认猫咪人设 |

### 3. 语音识别 ASR（重启生效）
| 设置项 | 控件 | 配置键 | 默认 |
|--------|------|--------|------|
| 模型大小 | QComboBox tiny/base/small/medium | plugins.asr.params.model_size | small |
| 设备 | QComboBox cpu/cuda | plugins.asr.params.device | cpu |

### 4. 语音合成 TTS（重启生效）
| 设置项 | 控件 | 配置键 | 默认 |
|--------|------|--------|------|
| 音色 | QComboBox（内置8个中文音色） | plugins.tts.params.voice | zh-CN-XiaoxiaoNeural |

### 5. 快捷键
| 设置项 | 控件 | 配置键 | 默认 |
|--------|------|--------|------|
| 启用麦克风热键 | 开关 | voice.hotkey_enabled | true |
| 麦克风开关热键 | 按键捕获框（点击→按组合键→显示，ESC取消） | voice.hotkey_toggle | Ctrl+Alt+M |

- 注册：Windows `RegisterHotKey`（ctypes）+ `QAbstractNativeEventFilter`（WM_HOTKEY）
- 冲突检测：注册失败（热键已被占用）→ 气泡提示"热键占用，请更换"

### 6. 其他（实时生效）
| 设置项 | 控件 | 配置键 | 默认 |
|--------|------|--------|------|
| 宠物名称 | QLineEdit | app.name | 小忆 |
| 宠物大小 | QSlider 100-400 | ui.pet_size | 300 |
| 窗口置顶 | 开关 | ui.always_on_top | true |
| 开机自启 | 开关 | system.autostart | false |
| 性能模式 | QComboBox low/medium/high | system.performance_mode | low |

- 开机自启：Windows 注册表 `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` 写入/删除（键名 XiaoYiPet；启动 exe 时写入 exe 路径，源码运行时写 python run.py）
- 置顶开关：保存后宠物窗口 setWindowFlag(Qt.WindowStaysOnTopHint) 重显

### 7. 系统信息（只读）
- 硬件：CPU 核数 / 内存 GB / GPU 名（HardwareDetector）
- Ollama：连接状态（绿点/红点）+ 已装模型列表
- 麦克风：可用状态
- 版本：app.version

## 技术要点

- **新文件**：`ui/settings_dialog.py`（SettingsDialog 类）+ `services/hotkey_manager.py`（HotkeyManager 类）
- **热键捕获控件**：QPushButton 点击后进入捕获态（显示"按下快捷键..."），监听 keyPressEvent 组合键；支持 Ctrl/Alt/Shift+Key 组合；捕获逻辑独立可测
- **动态模型列表**：打开 AI 对话组时请求 `GET /api/tags`（5s 超时），失败则只显示"刷新"按钮保留配置值
- **保存流程**：读取控件值 → 校验（音量范围等）→ ConfigManager.set 批量写 → 对实时项做热更新回调 → 关闭对话框
- **热更新回调**：SettingsDialog.accepted 后由 PetWindow.apply_settings() 应用：
  - listening 开关 → 启动/停止监听
  - speech_volume/max_seconds → 若监听运行中，重启监听（stop+start）
  - always_on_top → setWindowFlag + show
  - 其余标注"重启生效"
- **热键集成**：HotkeyManager 注册热键 → WM_HOTKEY 回调 → pet_window.toggle_voice_monitor()（切换监听 + 气泡提示"麦克风已开启/已关闭"）

## 测试计划

1. `tests/test_hotkey_manager.py`：按键序列解析（空/单键/组合键）、注册失败处理（mock RegisterHotKey 返回0）
2. `tests/test_settings_dialog.py`：控件绑定初始值、保存写回 config、取消不写、热键捕获控件状态机（idle→capturing→captured）
3. `tests/test_pet_window.py`：apply_settings 热更新（listening 开关触发 start/stop）、热键回调 toggle 气泡
4. 现有基线：175 passed + 2 pre-existing FAILED 不变

## 涉及文件

| 文件 | 动作 |
|------|------|
| `ui/settings_dialog.py` | 新建（对话框 + 7 组设置） |
| `services/hotkey_manager.py` | 新建（全局热键注册/解析） |
| `ui/pet_window.py` | 右键菜单加"设置"；apply_settings；toggle_voice_monitor；热键回调 |
| `core/app.py` | 启动时注册热键；关闭时释放 |
| `config.yaml` | 新增 voice.hotkey_*/ui.always_on_top/system.autostart 键 |
| `tests/*` | 新增测试 |
