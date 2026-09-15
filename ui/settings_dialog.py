# -*- coding: utf-8 -*-
"""小忆系统设置对话框"""
import logging
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QTabWidget, QWidget,
    QPushButton, QCheckBox, QSlider, QSpinBox, QLineEdit,
    QComboBox, QPlainTextEdit, QLabel, QDialogButtonBox, QMessageBox,
)

try:
    import requests
except ImportError:
    requests = None  # type: ignore

logger = logging.getLogger(__name__)

# Edge TTS 中文音色（仅保留实际可用的音色）
# 按性别分组排列：女声在前，男声在后
TTS_VOICES = [
    # ── 🎀 女声 ──
    ("🎀 晓晓 · 温暖知性", "zh-CN-XiaoxiaoNeural"),
    ("🎀 晓伊 · 活泼可爱（欣雅推荐）", "zh-CN-XiaoyiNeural"),
    ("🎀 晓北 · 东北腔·爽朗", "zh-CN-liaoning-XiaobeiNeural"),
    ("🎀 晓妮 · 陕西方言·亲切", "zh-CN-shaanxi-XiaoniNeural"),
    ("🎀 晓嘉 · 粤语女声", "zh-HK-HiuGaaiNeural"),
    ("🎀 晓曼 · 粤语女声", "zh-HK-HiuMaanNeural"),
    ("🎀 晓晨 · 台湾腔女声", "zh-TW-HsiaoChenNeural"),
    ("🎀 晓雨 · 台湾腔女声", "zh-TW-HsiaoYuNeural"),
    # ── 🎩 男声 ──
    ("🎩 云希 · 阳光少年", "zh-CN-YunxiNeural"),
    ("🎩 云健 · 沉稳成熟", "zh-CN-YunjianNeural"),
    ("🎩 云夏 · 青涩少年", "zh-CN-YunxiaNeural"),
    ("🎩 云扬 · 热情激昂", "zh-CN-YunyangNeural"),
    ("🎩 云龙 · 粤语男声", "zh-HK-WanLungNeural"),
    ("🎩 云哲 · 台湾腔男声", "zh-TW-YunJheNeural"),
]

# 按性别分组（用于筛选下拉框）
TTS_VOICE_GROUPS = {
    "🎀 女声": list(range(0, 8)),   # 索引 0~7
    "🎩 男声": list(range(8, 14)),   # 索引 8~13
}

ASR_MODELS = ["tiny", "base", "small", "medium"]
ASR_DEVICES = ["cpu", "cuda"]
PERF_MODES = ["low", "medium", "high"]

PREVIEW_TEXT = "你好，我是欣雅"


class HotkeyCaptureDialog(QDialog):
    """模态快捷键捕获对话框：独占键盘，按下组合键后自动保存并关闭。

    用独立模态窗口保证按键事件必然到达（不受主对话框焦点/事件干扰）。
    Esc 或点击取消=不变更。
    """
    def __init__(self, parent=None, current=""):
        super().__init__(parent)
        self.setWindowTitle("设置快捷键")
        self.setModal(True)
        self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)
        self.setFixedSize(360, 140)
        self._current = current
        self._captured = None

        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(f"当前：{current}"))
        self._hint = QLabel("请按下想要的键（字母/数字/符号\\/F键都行），Esc 取消")
        self._hint.setAlignment(Qt.AlignCenter)
        lay.addWidget(self._hint)
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        lay.addWidget(cancel, alignment=Qt.AlignRight)
        self._hint.setStyleSheet("font-size:14px; font-weight:bold; padding:10px;")

    def captured(self) -> Optional[str]:
        return self._captured

    def keyPressEvent(self, event):
        key = event.key()
        if key == Qt.Key_Escape:
            self.reject()
            return
        from services.hotkey_manager import parse_key_event
        combo = parse_key_event(key, event.modifiers())
        if combo:
            # 防止与自身当前值冲突，但允许改
            self._captured = combo
            self._hint.setText(f"已捕获：{combo}（点击确定）")
            self.accept()
        else:
            # 无效组合（如单键无修饰）提示
            self._hint.setText("该键无效，请用 组合键（Ctrl/Alt/Shift + 键）")
        event.accept()


class HotkeyCaptureButton(QPushButton):
    """点击后弹出模态捕获对话框，按键组合保存到 current_combo"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setToolTip("点击后按下组合键（如 Ctrl+Alt+M），Esc 取消")
        self.clicked.connect(self._open_capture)
        self._capturing = False
        self.current_combo = ""
        self._on_captured = None  # 可选回调：捕获完立即通知

    def set_combo(self, combo: str):
        self.current_combo = combo
        self.setText(combo)
        self.setChecked(False)

    def start_capture(self):
        self._open_capture()

    def finish_capture(self, combo: str):
        self._save_combo(combo)

    def set_on_captured(self, cb):
        self._on_captured = cb

    def _open_capture(self):
        dlg = HotkeyCaptureDialog(self, self.current_combo)
        if dlg.exec() == QDialog.Accepted and dlg.captured():
            self._save_combo(dlg.captured())
        # 无论是否取消，都恢复非捕获态
        self.setChecked(False)

    def _save_combo(self, combo: str):
        self.current_combo = combo
        self.setText(combo)
        self.setChecked(False)
        if self._on_captured:
            try:
                self._on_captured(combo)
            except Exception:
                pass


class SettingsDialog(QDialog):
    """出厂默认值（恢复默认时填充到控件，不写config）"""
    DEFAULT_VALUES = {
        "app.name": "欣雅",
        "ui.subtitle_enabled": True,
        "ui.subtitle_fg_color": "#FFFFFF",
        "ui.subtitle_bg_color": "#000000",
        "voice.listening": True,
        "voice.speech_volume": 300,
        "voice.max_listen_seconds": 10,
        "voice.echo_cooldown": 3,
        "voice.reply_min_length": 3,
        "plugins.llm.params.model": "qwen2.5:1.5b",
        "plugins.asr.params.model_size": "small",
        "plugins.asr.params.device": "cpu",
        "plugins.tts.params.voice": "zh-CN-XiaoyiNeural",
        "plugins.tts.params.rate": 0,
        "plugins.tts.params.pitch": 0,
        "plugins.llm.engine": "ollama",
        "voice.hotkey_toggle": "Ctrl+Alt+M",
        "voice.hotkey_mute": "Ctrl+Alt+S",
        "voice.hotkey_interrupt": "Ctrl+Alt+D",
        "voice.hotkey_mic_next": "Ctrl+Alt+N",
        "ui.pet_size": 300,
        "ui.always_on_top": True,
        "system.autostart": False,
        "system.performance_mode": "low",
    }

    def __init__(self, config_manager, parent=None):
        super().__init__(parent)
        self.cfg = config_manager
        self.setWindowTitle("系统设置")
        self.setModal(False)  # 非模态，宠物继续可用
        self.resize(560, 460)

        self._build_ui()
        self._load_values()

    # ---------- UI构建 ----------

    def _build_ui(self):
        root = QVBoxLayout(self)
        self.tabs = QTabWidget()
        root.addWidget(self.tabs)

        # 归类：语音（输入+输出合并）、AI 大脑、外观、快捷键、系统信息
        # 原「⚡ 性能」页已并入「ℹ️ 系统信息」——"性能档位由本机硬件决定"，
        # 两页本来就共用 hardware_detector 与同一份硬件摘要，分开只是让人来回切。
        self.tabs.addTab(self._tab_voice(), "🎤 语音")
        self.tabs.addTab(self._tab_ai(), "🧠 AI 大脑")
        self.tabs.addTab(self._tab_look(), "🎀 外观")
        self.tabs.addTab(self._tab_hotkey(), "⌨️ 快捷键")
        self.tabs.addTab(self._tab_info(), "ℹ️ 系统信息")

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        reset_btn = QPushButton("恢复默认")
        btns.addButton(reset_btn, QDialogButtonBox.ResetRole)
        btns.accepted.connect(self._on_accept)
        btns.rejected.connect(self.reject)
        reset_btn.clicked.connect(self.reset_to_defaults)
        root.addWidget(btns)

    def _tab_voice(self):
        """🎤 语音：输入(监听/ASR) + 输出(TTS音色/语速/音调) 归为一页"""
        w = QWidget()
        form = QFormLayout(w)

        # ── 语音输入 part ──
        input_grp = QLabel("── 语音识别（输入） ──")
        form.addRow(input_grp)

        self.listening_check = QCheckBox("常驻语音监听")
        self.listening_check.setToolTip("开启后无需点击，说话即响应")
        self.speech_volume_slider = QSlider(Qt.Horizontal)
        self.speech_volume_slider.setRange(50, 1000)
        self.speech_volume_label = QLabel("300")
        row = QHBoxLayout()
        row.addWidget(self.speech_volume_slider)
        row.addWidget(self.speech_volume_label)
        self.speech_volume_slider.valueChanged.connect(
            lambda v: self.speech_volume_label.setText(str(v)))
        self.max_listen_spin = QSpinBox(); self.max_listen_spin.setRange(3, 15)
        self.echo_cooldown_spin = QSpinBox(); self.echo_cooldown_spin.setRange(1, 10)
        self.reply_min_spin = QSpinBox(); self.reply_min_spin.setRange(1, 10)
        self.asr_model_combo = QComboBox()
        self.asr_model_combo.addItems(ASR_MODELS)
        self.asr_device_combo = QComboBox()
        self.asr_device_combo.addItems(ASR_DEVICES)
        form.addRow("监听", self.listening_check)
        form.addRow("说话音量阈值", row)
        form.addRow("单次最长监听(秒)", self.max_listen_spin)
        form.addRow("回声冷却(秒)", self.echo_cooldown_spin)
        form.addRow("最小回复字数", self.reply_min_spin)
        form.addRow("识别模型", self.asr_model_combo)
        form.addRow("识别设备", self.asr_device_combo)

        # 麦克风增益
        self.mic_gain_slider = QSlider(Qt.Horizontal)
        self.mic_gain_slider.setRange(2, 20)  # 1.0 ~ 10.0 (×0.5)
        self.mic_gain_slider.setSingleStep(1)
        self.mic_gain_label = QLabel("2.5")
        gain_row = QHBoxLayout()
        gain_row.addWidget(self.mic_gain_slider)
        gain_row.addWidget(self.mic_gain_label)
        self.mic_gain_slider.valueChanged.connect(
            lambda v: self.mic_gain_label.setText(f"{v/2:.1f}"))
        form.addRow("麦克风增益", gain_row)

        # 唤醒词
        self.wake_word_edit = QLineEdit()
        form.addRow("唤醒词", self.wake_word_edit)

        # VAD 过滤
        self.vad_filter_check = QCheckBox("VAD 过滤")
        self.vad_filter_check.setToolTip("过滤环境噪音，但可能截断长句（D19：需真实噪声环境验证）")
        form.addRow("VAD", self.vad_filter_check)

        # ── 语音输出 part ──
        out_grp = QLabel("── 语音合成（输出） ──")
        form.addRow(out_grp)

        # 音色筛选 + 下拉
        self.tts_filter_combo = QComboBox()
        self.tts_filter_combo.addItem("全部", "__all__")
        for group_name in TTS_VOICE_GROUPS:
            self.tts_filter_combo.addItem(f"📂 {group_name}", group_name)
        self.tts_filter_combo.currentIndexChanged.connect(self._filter_tts_voices)
        form.addRow("分类筛选", self.tts_filter_combo)

        self.tts_voice_combo = QComboBox()
        self.tts_voice_combo.setMinimumWidth(300)
        for label, value in TTS_VOICES:
            self.tts_voice_combo.addItem(label, value)
        self.tts_voice_combo.currentIndexChanged.connect(self._on_tts_setting_changed)
        form.addRow("音色", self.tts_voice_combo)

        self.tts_rate_slider = QSlider(Qt.Horizontal)
        self.tts_rate_slider.setRange(-50, 50); self.tts_rate_slider.setValue(0)
        self.tts_rate_label = QLabel("正常")
        rate_row = QHBoxLayout()
        rate_row.addWidget(QLabel("慢")); rate_row.addWidget(self.tts_rate_slider)
        rate_row.addWidget(QLabel("快")); rate_row.addWidget(self.tts_rate_label)
        self.tts_rate_slider.valueChanged.connect(self._update_rate_label)
        self.tts_rate_slider.sliderReleased.connect(self._on_tts_setting_changed)
        form.addRow("语速", rate_row)

        self.tts_pitch_slider = QSlider(Qt.Horizontal)
        self.tts_pitch_slider.setRange(-50, 50); self.tts_pitch_slider.setValue(0)
        self.tts_pitch_label = QLabel("正常")
        pitch_row = QHBoxLayout()
        pitch_row.addWidget(QLabel("低")); pitch_row.addWidget(self.tts_pitch_slider)
        pitch_row.addWidget(QLabel("高")); pitch_row.addWidget(self.tts_pitch_label)
        self.tts_pitch_slider.valueChanged.connect(self._update_pitch_label)
        self.tts_pitch_slider.sliderReleased.connect(self._on_tts_setting_changed)
        form.addRow("音调", pitch_row)

        preview_row = QHBoxLayout()
        preview = QPushButton("▶ 试听当前音色")
        preview.clicked.connect(self._preview_tts)
        preview_row.addWidget(preview)
        refresh_btn = QPushButton("🔄 在线刷新音色")
        refresh_btn.setToolTip("从 Microsoft 获取最新可用音色（需联网）")
        refresh_btn.clicked.connect(self._refresh_online_voices)
        preview_row.addWidget(refresh_btn)
        form.addRow("", preview_row)
        form.addRow(QLabel("✅ 音色/语速/音调立即生效"))

        return w

    def _build_perf_section(self, form: QFormLayout):
        """构建「性能与模型」区块（原独立页，现并入系统信息页）。

        Args:
            form: 目标表单布局（由 `_tab_info` 传入，与系统信息共用一页）

        为什么合并：性能档位**由本机硬件决定**，与"系统信息"读的是同一个
        `hardware_detector`、同一份硬件摘要。分成两页只会让人来回切页对照。
        """
        self.perf_combo2 = QComboBox()
        self.perf_combo2.addItems(["low", "medium", "high"])
        # 自动评估按钮
        auto_btn = QPushButton("🔍 重新检测硬件并推荐")
        auto_btn.clicked.connect(self._auto_evaluate)
        form.addRow(QLabel("── 性能与模型 ──"))
        form.addRow("性能档位", self.perf_combo2)
        form.addRow("", auto_btn)
        # 插件状态
        self._perf_plugin_label = QLabel("—")
        self._perf_plugin_label.setWordWrap(True)
        form.addRow("插件状态", self._perf_plugin_label)
        form.addRow("", QLabel(
            "说明：档位越低越省资源(低→tiny/30fps)，越高越准(高→medium/60fps)。"
            "ASR 模型/设备可在『语音』页手动改。"
        ))

    def _auto_evaluate(self):
        """重新检测硬件，按推荐设置性能档位（并联动ASR模型/设备）"""
        try:
            from core.hardware_detector import get_hardware_detector
            hw = get_hardware_detector().detect()
            rec = hw.recommended_mode
            self.perf_combo2.setCurrentText(rec)
            self._refresh_perf_hw()
            self._apply_perf_to_asr(rec)
        except Exception as e:
            self._perf_plugin_label.setText(f"检测失败: {e}")

    def _refresh_perf_hw(self):
        """刷新硬件摘要 + 插件状态（系统信息页用，两处内容已同页）"""
        self._refresh_plugin_status()

    def _apply_perf_to_asr(self, mode: str):
        """把性能档位的 ASR 模型/设备同步到语音识别页控件（low→tiny, medium→small, high→medium）"""
        mapping = {"low": "tiny", "medium": "small", "high": "medium"}
        size = mapping.get(mode, "small")
        if hasattr(self, "asr_model_combo"):
            idx = self.asr_model_combo.findText(size)
            if idx >= 0:
                self.asr_model_combo.setCurrentIndex(idx)

    def _refresh_plugin_status(self):
        """刷新插件状态摘要"""
        try:
            from core.app import get_app
            app = get_app()
            if not app:
                self._perf_plugin_label.setText("应用未运行")
                return
            status = app.get_status()
            plugins = status.get("plugins", {})
            labels = {
                "asr": "🎙️ ASR语音识别", "tts": "🔊 TTS语音合成",
                "llm": "🧠 LLM大语言模型", "voiceprint": "🔐 声纹识别",
                "file_monitor": "📁 文件监控", "avatar": "🖼️ 形象生成",
            }
            lines = []
            for key, label in labels.items():
                avail = plugins.get(key, {}).get("available", False)
                icon = "✅" if avail else "❌"
                # 附加 LLM 模型名
                extra = ""
                if key == "llm" and avail:
                    model = app.config_manager.get("plugins.llm.params.model", "")
                    if model:
                        extra = f" ({model})"
                lines.append(f"{icon} {label}{extra}")
            self._perf_plugin_label.setText("\n".join(lines))
        except Exception as e:
            self._perf_plugin_label.setText(f"获取插件状态失败: {e}")

    def _tab_ai(self):
        w = QWidget()
        form = QFormLayout(w)
        # 引擎选择：本地 Ollama / 云端 OpenAI 兼容 API / OpenRouter
        self.llm_engine_combo = QComboBox()
        self.llm_engine_combo.addItem("本地 Ollama", "ollama")
        self.llm_engine_combo.addItem("云端 API (OpenAI兼容)", "openai_api")
        self.llm_engine_combo.addItem("OpenRouter / Groq 等", "openrouter")
        # 云端参数
        self.cloud_url_edit = QLineEdit()
        self.cloud_key_edit = QLineEdit()
        self.cloud_key_edit.setEchoMode(QLineEdit.Password)
        self.cloud_model_combo = QComboBox()
        self.cloud_model_combo.setEditable(True)
        # 本地 Ollama 参数
        self.llm_url_edit = QLineEdit()
        self.llm_model_combo = QComboBox()
        self.llm_model_combo.setEditable(True)  # 允许输入未列出的模型
        self.llm_prompt_edit = QPlainTextEdit()
        self.llm_prompt_edit.setFixedHeight(120)
        # 引擎切换 → 显示/隐藏对应区块
        self._cloud_group = QWidget()
        cf = QFormLayout(self._cloud_group)
        cf.setContentsMargins(0, 0, 0, 0)
        cf.addRow("API地址", self.cloud_url_edit)
        cf.addRow("API Key", self.cloud_key_edit)
        cf.addRow("模型", self.cloud_model_combo)
        self._local_group = QWidget()
        lf = QFormLayout(self._local_group)
        lf.setContentsMargins(0, 0, 0, 0)
        lf.addRow("Ollama地址", self.llm_url_edit)
        lf.addRow("模型", self.llm_model_combo)
        form.addRow("LLM 引擎", self.llm_engine_combo)
        form.addRow(self._local_group)
        form.addRow(self._cloud_group)
        form.addRow("人设提示词", self.llm_prompt_edit)
        self.llm_engine_combo.currentIndexChanged.connect(self._update_engine_groups)
        hint = QLabel("⚠️ 修改后需重启生效")
        form.addRow(hint)
        return w

    def _update_engine_groups(self):
        """按引擎切换显示本地/云端参数区"""
        eng = self.llm_engine_combo.currentData()
        is_cloud = eng in ("openai_api", "openrouter")
        self._cloud_group.setVisible(is_cloud)
        self._local_group.setVisible(eng == "ollama")

    def _tab_asr(self):
        w = QWidget()
        form = QFormLayout(w)
        self.asr_model_combo = QComboBox()
        self.asr_model_combo.addItems(ASR_MODELS)
        self.asr_device_combo = QComboBox()
        self.asr_device_combo.addItems(ASR_DEVICES)
        form.addRow("模型大小", self.asr_model_combo)
        form.addRow("设备", self.asr_device_combo)
        hint = QLabel("⚠️ 修改后需重启生效")
        form.addRow(hint)
        return w

    def _tab_tts(self):
        w = QWidget()
        form = QFormLayout(w)

        # 语音分类筛选
        self.tts_filter_combo = QComboBox()
        self.tts_filter_combo.addItem("全部", "__all__")
        for group_name in TTS_VOICE_GROUPS:
            self.tts_filter_combo.addItem(f"📂 {group_name}", group_name)
        self.tts_filter_combo.currentIndexChanged.connect(self._filter_tts_voices)
        form.addRow("分类筛选", self.tts_filter_combo)

        # 音色下拉
        self.tts_voice_combo = QComboBox()
        self.tts_voice_combo.setMinimumWidth(300)
        for label, value in TTS_VOICES:
            self.tts_voice_combo.addItem(label, value)
        self.tts_voice_combo.currentIndexChanged.connect(self._on_tts_setting_changed)
        form.addRow("音色", self.tts_voice_combo)

        # 语速/音调
        self.tts_rate_slider = QSlider(Qt.Horizontal)
        self.tts_rate_slider.setRange(-50, 50)
        self.tts_rate_slider.setValue(0)
        self.tts_rate_label = QLabel("正常")
        rate_row = QHBoxLayout()
        rate_row.addWidget(QLabel("慢"))
        rate_row.addWidget(self.tts_rate_slider)
        rate_row.addWidget(QLabel("快"))
        rate_row.addWidget(self.tts_rate_label)
        self.tts_rate_slider.valueChanged.connect(self._update_rate_label)
        self.tts_rate_slider.sliderReleased.connect(self._on_tts_setting_changed)
        form.addRow("语速", rate_row)

        self.tts_pitch_slider = QSlider(Qt.Horizontal)
        self.tts_pitch_slider.setRange(-50, 50)
        self.tts_pitch_slider.setValue(0)
        self.tts_pitch_label = QLabel("正常")
        pitch_row = QHBoxLayout()
        pitch_row.addWidget(QLabel("低"))
        pitch_row.addWidget(self.tts_pitch_slider)
        pitch_row.addWidget(QLabel("高"))
        pitch_row.addWidget(self.tts_pitch_label)
        self.tts_pitch_slider.valueChanged.connect(self._update_pitch_label)
        self.tts_pitch_slider.sliderReleased.connect(self._on_tts_setting_changed)
        form.addRow("音调", pitch_row)

        # 试听区域
        preview_row = QHBoxLayout()
        preview = QPushButton("▶ 试听当前音色")
        preview.clicked.connect(self._preview_tts)
        preview_row.addWidget(preview)

        # 在线刷新按钮
        refresh_btn = QPushButton("🔄 在线刷新音色列表")
        refresh_btn.setToolTip("从 Microsoft 获取最新可用音色（需联网）")
        refresh_btn.clicked.connect(self._refresh_online_voices)
        preview_row.addWidget(refresh_btn)
        form.addRow("", preview_row)

        hint = QLabel("✅ 音色/语速/音调修改后立即生效，无需重启")
        form.addRow(hint)
        return w

    def _filter_tts_voices(self):
        """按分类筛选音色"""
        group = self.tts_filter_combo.currentData()
        self.tts_voice_combo.clear()
        if group == "__all__":
            for label, value in TTS_VOICES:
                self.tts_voice_combo.addItem(label, value)
        else:
            indices = TTS_VOICE_GROUPS.get(group, [])
            for idx in indices:
                if idx < len(TTS_VOICES):
                    label, value = TTS_VOICES[idx]
                    self.tts_voice_combo.addItem(label, value)

    def _on_tts_setting_changed(self):
        """音色/语速/音调改变时立即更新运行中的TTS插件（无需重启/无需点确定）"""
        voice = self.tts_voice_combo.currentData()
        if not voice:
            return
        try:
            from core.app import get_app
            app = get_app()
            if not app:
                return
            tts = app.get_plugin("TTSEngine")
            if tts and hasattr(tts, "set_voice"):
                tts.set_voice(voice)
                tts.rate = self.tts_rate_slider.value()
                tts.pitch = self.tts_pitch_slider.value()
                logger.info(f"TTS音色已实时切换: {voice}, 语速: {tts.rate}%, 音调: {tts.pitch}Hz")
        except Exception as e:
            logger.debug(f"实时切换TTS音色: {e}")

    def _update_rate_label(self, v):
        labels = {0: "正常"}
        if v < 0:
            labels = {-50: "极慢", -25: "较慢", -10: "略慢"}
        elif v > 0:
            labels = {10: "略快", 25: "较快", 50: "极快"}
        # 找最近的标签
        closest = min(labels.keys(), key=lambda k: abs(k - v)) if labels else 0
        self.tts_rate_label.setText(labels.get(closest, f"{'+' if v > 0 else ''}{v}%"))

    def _update_pitch_label(self, v):
        if v == 0:
            self.tts_pitch_label.setText("正常")
        elif v < 0:
            self.tts_pitch_label.setText(f"降低 {v}")
        else:
            self.tts_pitch_label.setText(f"升高 +{v}")

    def _refresh_online_voices(self):
        """从 Microsoft 在线获取中文音色"""
        import threading
        threading.Thread(target=self._fetch_online_voices_thread, daemon=True).start()

    def _fetch_online_voices_thread(self):
        try:
            import asyncio
            import edge_tts

            async def fetch():
                voices = await edge_tts.list_voices()
                return [v for v in voices if v["Locale"].startswith("zh-")]

            voices = asyncio.run(fetch())
            # 更新UI
            self.tts_online_voices = []
            for v in voices:
                gender = "女" if v["Gender"] == "Female" else "男"
                local = v.get("LocalName", "")
                name = v["ShortName"]
                self.tts_online_voices.append((name, gender, local))

            from PySide6.QtCore import QMetaObject, Qt
            QMetaObject.invokeMethod(self, "_show_online_voices",
                                     Qt.ConnectionType.QueuedConnection)
        except Exception as e:
            logger.error(f"在线刷新音色失败: {e}")

    def _show_online_voices(self):
        """显示在线获取的音色"""
        if not hasattr(self, "tts_online_voices") or not self.tts_online_voices:
            return
        from PySide6.QtWidgets import QMessageBox
        lines = [f"  {g}  {n}  ({name})" for name, g, n in self.tts_online_voices]
        QMessageBox.information(self, "在线音色列表",
                                f"共 {len(lines)} 个中文音色：\n\n" + "\n".join(lines))

    def _tab_hotkey(self):
        """⌨️ 快捷键：麦克风开关 / 静音 / 打断 / 切换麦克风 四个全局热键"""
        w = QWidget()
        form = QFormLayout(w)
        self.hotkey_enabled_check = QCheckBox("启用全局快捷键")
        form.addRow("启用", self.hotkey_enabled_check)

        # 麦克风开关（主热键）
        self.hotkey_btn = HotkeyCaptureButton()
        form.addRow("麦克风开关", self.hotkey_btn)

        # 静音开关
        self.mute_hotkey_btn = HotkeyCaptureButton()
        form.addRow("静音开关", self.mute_hotkey_btn)

        # 打断当前语音
        self.interrupt_hotkey_btn = HotkeyCaptureButton()
        self.interrupt_hotkey_btn.setToolTip("宠物说话时按下，立即停止说话（Ctrl+Alt+D）")
        form.addRow("打断说话", self.interrupt_hotkey_btn)

        # 切换麦克风设备
        self.mic_cycle_hotkey_btn = HotkeyCaptureButton()
        self.mic_cycle_hotkey_btn.setToolTip("按顺序轮换麦克风设备，多声源场景快速试哪个能收到声音（Ctrl+Alt+N）")
        form.addRow("切换麦克风", self.mic_cycle_hotkey_btn)

        tip = QLabel(
            "可以自由设置：单个键（如 K、F5）或组合键（如 Ctrl+Alt+M）都行\n"
            "点击按钮 → 按下想要的键 → 自动保存，Esc 取消\n"
            "⚠️ 用单个键会全局监听该键（可能影响打字），建议用组合键"
        )
        form.addRow(tip)
        return w

    def _tab_look(self):
        """🎀 外观：宠物名称/大小/置顶/字幕"""
        w = QWidget()
        form = QFormLayout(w)
        self.name_edit = QLineEdit()
        self.pet_size_slider = QSlider(Qt.Horizontal)
        self.pet_size_slider.setRange(100, 400)
        self.pet_size_label = QLabel("300")
        self.pet_size_slider.valueChanged.connect(
            lambda v: self.pet_size_label.setText(f"{v}px"))
        row = QHBoxLayout()
        row.addWidget(self.pet_size_slider)
        row.addWidget(self.pet_size_label)
        self.topmost_check = QCheckBox("窗口置顶")
        self.autostart_check = QCheckBox("开机自启")
        # 脚下字幕：开关 + 颜色
        self.subtitle_check = QCheckBox("显示脚下字幕")
        self.subtitle_fg_btn = QPushButton("白色")
        self.subtitle_bg_btn = QPushButton("黑色")
        self._subtitle_fg_color = "#FFFFFF"
        self._subtitle_bg_color = "#000000"
        self.subtitle_fg_btn.clicked.connect(lambda: self._pick_letter_color("fg"))
        self.subtitle_bg_btn.clicked.connect(lambda: self._pick_letter_color("bg"))
        sub_row = QHBoxLayout()
        sub_row.addWidget(self.subtitle_check)
        sub_row.addWidget(QLabel("文字色:"))
        sub_row.addWidget(self.subtitle_fg_btn)
        sub_row.addWidget(QLabel("底色:"))
        sub_row.addWidget(self.subtitle_bg_btn)
        form.addRow("宠物名称", self.name_edit)
        form.addRow("宠物大小", row)
        form.addRow("位置", QLabel("保存位置由拖拽决定"))
        form.addRow("置顶", self.topmost_check)
        form.addRow("开机自启", self.autostart_check)
        form.addRow("字幕", sub_row)
        return w

    def _pick_letter_color(self, which: str):
        """弹颜色选择器，更新按钮文本"""
        from PySide6.QtWidgets import QColorDialog
        cur = QColor(self._subtitle_fg_color if which == "fg" else self._subtitle_bg_color)
        col = QColorDialog.getColor(cur, self, "选择字幕颜色")
        if col.isValid():
            hex_s = col.name()
            if which == "fg":
                self._subtitle_fg_color = hex_s
                self.subtitle_fg_btn.setText(hex_s)
            else:
                self._subtitle_bg_color = hex_s
                self.subtitle_bg_btn.setText(hex_s)

    def _tab_info(self):
        """ℹ️ 系统信息：硬件/运行环境 + 性能与模型（原「⚡ 性能」页已并入）。"""
        w = QWidget()
        form = QFormLayout(w)

        # ── 系统信息 part ──
        form.addRow(QLabel("── 系统信息 ──"))
        info = self._collect_system_info()
        for k, v in info.items():
            label = QLabel(str(v))
            label.setWordWrap(True)
            label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            form.addRow(k, label)

        # ── 性能与模型 part（原独立页，现同页） ──
        self._build_perf_section(form)
        return w

    def _collect_system_info(self) -> dict:
        try:
            from core.hardware_detector import get_hardware_detector
            hw = get_hardware_detector().detect()
            info = {
                "CPU": f"{hw.cpu_count} 核",
                "内存": f"{hw.memory_gb:.1f} GB",
                "GPU": hw.gpu_name if hw.has_gpu else "无",
            }
        except Exception:
            info = {"CPU": "未知", "内存": "未知", "GPU": "未知"}
        info["版本"] = self.cfg.get("app.version", "2.0.0")
        # Ollama状态
        base_url = self.cfg.get("plugins.llm.params.base_url", "http://localhost:11434")
        try:
            r = requests.get(f"{base_url}/api/tags", timeout=3)
            info["Ollama"] = f"在线 ({r.status_code})"
            models = r.json().get("models", [])
            info["模型列表"] = ", ".join(m["name"] for m in models[:5])
        except Exception:
            info["Ollama"] = "离线"
        info["麦克风"] = "可用" if self._mic_available() else "不可用"
        return info

    def _mic_available(self) -> bool:
        try:
            from services.microphone_service import MicrophoneService
            return MicrophoneService().is_available()
        except Exception:
            return False

    # ---------- 加载/保存 ----------

    def _load_values(self):
        cfg = self.cfg
        self.name_edit.setText(cfg.get("app.name", "欣雅"))
        # 字幕
        self.subtitle_check.setChecked(bool(cfg.get("ui.subtitle_enabled", True)))
        fg = str(cfg.get("ui.subtitle_fg_color", "#FFFFFF"))
        bg = str(cfg.get("ui.subtitle_bg_color", "#000000"))
        self._subtitle_fg_color = fg
        self._subtitle_bg_color = bg
        self.subtitle_fg_btn.setText(fg)
        self.subtitle_bg_btn.setText(bg)
        self.listening_check.setChecked(cfg.get("voice.listening", True))
        vol = int(cfg.get("voice.speech_volume", 300))
        self.speech_volume_slider.setValue(vol)
        self.speech_volume_label.setText(str(vol))
        self.max_listen_spin.setValue(int(cfg.get("voice.max_listen_seconds", 10)))
        self.echo_cooldown_spin.setValue(int(cfg.get("voice.echo_cooldown", 3)))
        self.reply_min_spin.setValue(int(cfg.get("voice.reply_min_length", 3)))

        self.llm_url_edit.setText(cfg.get("plugins.llm.params.base_url", "http://localhost:11434"))
        # 模型列表动态填充
        self._refresh_llm_models()
        self.llm_prompt_edit.setPlainText(cfg.get("plugins.llm.params.system_prompt", ""))

        # LLM 引擎切换（本地 Ollama / 云端 API）
        engine = cfg.get("plugins.llm.engine", "ollama")
        ei = self.llm_engine_combo.findData(engine)
        self.llm_engine_combo.setCurrentIndex(max(ei, 0))
        # AI 页直接读写 plugins.llm.params（实际生效的配置），cloud 段仅作兼容保留
        # —— 避免 cloud/params 双份互相覆盖导致 groq 等配置被 openai 默认值顶掉。
        self.cloud_url_edit.setText(cfg.get("plugins.llm.params.base_url", "https://api.openai.com/v1"))
        self.cloud_key_edit.setText(cfg.get("plugins.llm.params.api_key", ""))
        cloud_model = cfg.get("plugins.llm.params.model", "gpt-4o-mini")
        if self.cloud_model_combo.findText(cloud_model) < 0:
            self.cloud_model_combo.addItem(cloud_model)
        self.cloud_model_combo.setCurrentText(cloud_model)
        self._update_engine_groups()

        idx = self.asr_model_combo.findText(cfg.get("plugins.asr.params.model_size", "small"))
        self.asr_model_combo.setCurrentIndex(max(idx, 0))
        idx = self.asr_device_combo.findText(cfg.get("plugins.asr.params.device", "cpu"))
        self.asr_device_combo.setCurrentIndex(max(idx, 0))
        # 麦克风增益
        gain = float(cfg.get("voice.mic_gain", 2.5))
        self.mic_gain_slider.setValue(int(gain * 2))
        self.mic_gain_label.setText(f"{gain:.1f}")
        # 唤醒词
        self.wake_word_edit.setText(cfg.get("voice.wake_word", "嘿欣雅"))
        # VAD
        self.vad_filter_check.setChecked(bool(cfg.get("voice.vad_filter", False)))

        voice = cfg.get("plugins.tts.params.voice", "zh-CN-XiaoyiNeural")
        idx = self.tts_voice_combo.findData(voice)
        if idx >= 0:
            self.tts_voice_combo.setCurrentIndex(idx)
        # 语速/音调
        self.tts_rate_slider.setValue(int(cfg.get("plugins.tts.params.rate", 0)))
        self._update_rate_label(self.tts_rate_slider.value())
        self.tts_pitch_slider.setValue(int(cfg.get("plugins.tts.params.pitch", 0)))
        self._update_pitch_label(self.tts_pitch_slider.value())

        self.hotkey_enabled_check.setChecked(cfg.get("voice.hotkey_enabled", True))
        self.hotkey_btn.set_combo(cfg.get("voice.hotkey_toggle", "Ctrl+Alt+M"))
        self.mute_hotkey_btn.set_combo(cfg.get("voice.hotkey_mute", "Ctrl+Alt+S"))
        self.interrupt_hotkey_btn.set_combo(cfg.get("voice.hotkey_interrupt", "Ctrl+Alt+D"))
        self.mic_cycle_hotkey_btn.set_combo(cfg.get("voice.hotkey_mic_next", "Ctrl+Alt+N"))

        size = int(cfg.get("ui.pet_size", 300))
        self.pet_size_slider.setValue(size)
        self.pet_size_label.setText(f"{size}px")
        self.topmost_check.setChecked(cfg.get("ui.always_on_top", True))
        self.autostart_check.setChecked(cfg.get("system.autostart", False))
        # 性能档位（权威值，控件现位于「系统信息」页）
        idx = self.perf_combo2.findText(cfg.get("system.performance_mode", "low"))
        self.perf_combo2.setCurrentIndex(max(idx, 0))
        self._refresh_perf_hw()

    def _refresh_llm_models(self):
        """从Ollama拉取模型列表填充下拉（失败则保留可编辑输入框）"""
        self.llm_model_combo.clear()
        base_url = self.llm_url_edit.text().rstrip("/")
        try:
            r = requests.get(f"{base_url}/api/tags", timeout=3)
            models = r.json().get("models", [])
            for m in models:
                self.llm_model_combo.addItem(m.get("name", ""))
            self.llm_model_combo.setEditText("")
        except Exception as e:
            logger.warning(f"获取Ollama模型列表失败: {e}")
        # 填入当前配置值（可编辑）
        cur = self.cfg.get("plugins.llm.params.model", "qwen2.5:1.5b")
        if self.llm_model_combo.findText(cur) < 0:
            self.llm_model_combo.addItem(cur)
        self.llm_model_combo.setCurrentText(cur)

    def apply_to_config(self) -> bool:
        """读控件值写回config。返回是否成功"""
        cfg = self.cfg
        cfg.set("app.name", self.name_edit.text().strip() or "欣雅")
        # 字幕
        cfg.set("ui.subtitle_enabled", bool(self.subtitle_check.isChecked()))
        cfg.set("ui.subtitle_fg_color", self._subtitle_fg_color)
        cfg.set("ui.subtitle_bg_color", self._subtitle_bg_color)
        cfg.set("voice.listening", self.listening_check.isChecked())
        cfg.set("voice.speech_volume", self.speech_volume_slider.value())
        cfg.set("voice.max_listen_seconds", self.max_listen_spin.value())
        cfg.set("voice.echo_cooldown", self.echo_cooldown_spin.value())
        cfg.set("voice.reply_min_length", self.reply_min_spin.value())

        cfg.set("plugins.llm.params.base_url", self.llm_url_edit.text().strip())
        cfg.set("plugins.llm.params.model", self.llm_model_combo.currentText().strip())
        cfg.set("plugins.llm.params.system_prompt", self.llm_prompt_edit.toPlainText().strip())
        # LLM 引擎（本地 Ollama / 云端 API / OpenRouter）
        llm_engine = self.llm_engine_combo.currentData()
        cfg.set("plugins.llm.engine", llm_engine)
        # AI 页直接写 plugins.llm.params（实际生效），cloud 段同步一份做兼容但以 params 为准
        if llm_engine in ("openai_api", "openrouter"):
            cfg.set("plugins.llm.params.base_url", self.cloud_url_edit.text().strip())
            cfg.set("plugins.llm.params.api_key", self.cloud_key_edit.text().strip())
            cfg.set("plugins.llm.params.model", self.cloud_model_combo.currentText().strip())
            cfg.set("plugins.llm.cloud.base_url", self.cloud_url_edit.text().strip())
            cfg.set("plugins.llm.cloud.api_key", self.cloud_key_edit.text().strip())
            cfg.set("plugins.llm.cloud.model", self.cloud_model_combo.currentText().strip())

        cfg.set("plugins.asr.params.model_size", self.asr_model_combo.currentText())
        cfg.set("plugins.asr.params.device", self.asr_device_combo.currentText())
        # 麦克风增益 / 唤醒词 / VAD
        cfg.set("voice.mic_gain", self.mic_gain_slider.value() / 2)
        cfg.set("voice.wake_word", self.wake_word_edit.text().strip() or "嘿欣雅")
        cfg.set("voice.vad_filter", bool(self.vad_filter_check.isChecked()))
        cfg.set("plugins.tts.params.voice", self.tts_voice_combo.currentData())
        cfg.set("plugins.tts.params.rate", self.tts_rate_slider.value())
        cfg.set("plugins.tts.params.pitch", self.tts_pitch_slider.value())
        # TTS 音色立即生效（无需重启）
        try:
            from core.app import get_app
            app = get_app()
            tts = app.get_plugin("TTSEngine") if app else None
            if tts and hasattr(tts, "set_voice"):
                tts.set_voice(self.tts_voice_combo.currentData())
                tts.rate = self.tts_rate_slider.value()
                tts.pitch = self.tts_pitch_slider.value()
        except Exception:
            pass

        cfg.set("voice.hotkey_enabled", self.hotkey_enabled_check.isChecked())
        cfg.set("voice.hotkey_toggle", self.hotkey_btn.current_combo)
        cfg.set("voice.hotkey_mute", self.mute_hotkey_btn.current_combo)
        cfg.set("voice.hotkey_interrupt", self.interrupt_hotkey_btn.current_combo)
        cfg.set("voice.hotkey_mic_next", self.mic_cycle_hotkey_btn.current_combo)

        cfg.set("ui.pet_size", self.pet_size_slider.value())
        cfg.set("ui.always_on_top", self.topmost_check.isChecked())
        cfg.set("system.autostart", self.autostart_check.isChecked())
        # 权威档位来自"性能与模型"页
        cfg.set("system.performance_mode", self.perf_combo2.currentText())
        # 快捷键立即重绑（无需重启）
        try:
            from core.app import get_app
            app = get_app()
            if app and hasattr(app, "pet_window"):
                pw = app.pet_window
                if pw and hasattr(pw, "rebind_hotkey"):
                    pw.rebind_hotkey()
        except Exception:
            pass
        return True

    def _on_accept(self):
        if self.apply_to_config():
            self.accept()

    def _preview_tts(self):
        """试听当前选择音色（支持语速/音调）"""
        import threading
        voice = self.tts_voice_combo.currentData()
        rate = self.tts_rate_slider.value()
        pitch = self.tts_pitch_slider.value()
        threading.Thread(target=self._preview_thread, args=(voice, rate, pitch), daemon=True).start()

    def _preview_thread(self, voice, rate=0, pitch=0):
        """edge_tts 合成后经 pygame 播放"""
        import os
        import tempfile
        tmp_path = None
        try:
            import asyncio
            import edge_tts
            import pygame

            text = PREVIEW_TEXT

            # Windows: 先关文件句柄再让 edge_tts 写入
            fd, tmp_path = tempfile.mkstemp(suffix=".mp3")
            os.close(fd)

            # 只在非默认值时传 rate/pitch
            kwargs = {}
            if rate != 0:
                kwargs["rate"] = f"{'+' if rate > 0 else ''}{rate}%"
            if pitch != 0:
                kwargs["pitch"] = f"{'+' if pitch > 0 else ''}{pitch}Hz"

            asyncio.run(edge_tts.Communicate(text, voice, **kwargs).save(tmp_path))

            if not pygame.mixer.get_init():
                pygame.mixer.pre_init(frequency=24000, size=-16, channels=1, buffer=1024)
                pygame.mixer.init()
            # Windows 上用 Sound 对象代替 music（支持从文件路径加载，不锁文件）
            sound = pygame.mixer.Sound(tmp_path)
            sound.play()
            while pygame.mixer.get_busy():
                pygame.time.wait(50)
            sound.stop()
        except Exception as e:
            logger.error(f"试听失败: {e}")
        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass

    def reset_to_defaults(self):
        """恢复出厂默认值（只改界面，需保存生效）"""
        QMessageBox.information(self, "恢复默认", "已恢复出厂默认值，点击\"确定\"保存生效")
        self._fill_default_values()

    def _fill_default_values(self):
        """将出厂默认值填充到控件（不写config）"""
        d = self.DEFAULT_VALUES
        self.name_edit.setText(d["app.name"])
        self.listening_check.setChecked(d["voice.listening"])
        self.speech_volume_slider.setValue(d["voice.speech_volume"])
        self.speech_volume_label.setText(str(d["voice.speech_volume"]))
        self.max_listen_spin.setValue(d["voice.max_listen_seconds"])
        self.echo_cooldown_spin.setValue(d["voice.echo_cooldown"])
        self.reply_min_spin.setValue(d["voice.reply_min_length"])
        self.llm_model_combo.setCurrentText(d["plugins.llm.params.model"])
        self.asr_model_combo.setCurrentIndex(max(self.asr_model_combo.findText(d["plugins.asr.params.model_size"]), 0))
        self.asr_device_combo.setCurrentIndex(max(self.asr_device_combo.findText(d["plugins.asr.params.device"]), 0))
        idx = self.tts_voice_combo.findData(d["plugins.tts.params.voice"])
        if idx >= 0:
            self.tts_voice_combo.setCurrentIndex(idx)
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
        self.subtitle_check.setChecked(d.get("ui.subtitle_enabled", True))
        # 热键
        self.hotkey_btn.set_combo(d.get("voice.hotkey_toggle", "Ctrl+Alt+M"))
        self.mute_hotkey_btn.set_combo(d.get("voice.hotkey_mute", "Ctrl+Alt+S"))
        self.interrupt_hotkey_btn.set_combo(d.get("voice.hotkey_interrupt", "Ctrl+Alt+D"))
        self.pet_size_slider.setValue(d["ui.pet_size"])
        self.pet_size_label.setText(f"{d['ui.pet_size']}px")
        self.topmost_check.setChecked(d["ui.always_on_top"])
        self.autostart_check.setChecked(d["system.autostart"])
        self.perf_combo2.setCurrentIndex(max(self.perf_combo2.findText(d["system.performance_mode"]), 0))

