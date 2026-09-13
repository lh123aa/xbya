"""
PetWindow - PySide6桌面宠物主窗口
无边框、透明背景、置顶，带精灵动画、对话气泡与拖拽
"""

from PySide6.QtWidgets import QWidget, QMenu, QMessageBox
from PySide6.QtCore import Qt, QTimer, QPoint, QRect, QUrlQuery, QSettings, QObject, Signal, QThread, QMetaObject
from PySide6.QtGui import QPainter, QColor
from animation.controller import AnimationController
from animation.vrm_state_map import STATE_TO_VRM
from ui.state_machine import PetStateMachine
import logging
import threading
import time
import os
import json
from pathlib import Path

logger = logging.getLogger(__name__)

# VRM 模块占位（lazy import）：enable_vrm 首次调用时才真正导入 QtWebEngine，
# 模块顶层不 import —— 单测 monkeypatch ui.pet_window.QWebEngineView/VrmBridge
# 依赖此结构（attr 非 None 时直接复用注入的替身）
QWebEngineView = None
VrmBridge = None

# VRM 模式下顶部留白气泡区高度（像素）：view 占窗口底部（高 = 窗口高 - 60）
SUBTITLE_AREA = 44  # 底部字幕区高度（黑底白字，脚下位置）


class AgentEventBridge(QObject):
    """Agent 事件 → Qt 信号 的中转桥

    Agent 层的事件可能由**执行线程**发出（工具执行完成回调），
    直接在其中操作 Qt 控件会违反线程约束。这里统一转成 Qt 信号，
    由 Qt 的队列连接自动投递回 UI 线程。
    """

    #: 即时确认语：{request_id, text, audio(bytes|None)}
    ackReceived = Signal(dict)
    #: 执行结果：{request_id, summary, emotion, data, success, elapsed_ms}
    resultReceived = Signal(dict)
    #: 请求确认：{request_id, question, risk, action}
    confirmReceived = Signal(dict)
    #: 闲聊回退：{request_id, text}
    chatReceived = Signal(dict)
    #: 润色补播（P2-2）：{request_id, summary, previous, action}
    refineReceived = Signal(dict)
    #: 提醒到点（F7）：{reminder_id, what, text}
    #: 由**调度线程**发出，必须经信号回 UI 线程再出声
    reminderReceived = Signal(dict)


class PetWindow(QWidget):
    """宠物主窗口"""

    def __init__(self):
        super().__init__()

        # 窗口属性
        self.setWindowFlags(
            Qt.FramelessWindowHint |
            Qt.WindowStaysOnTopHint |
            Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(300, 350)

        # 动画控制器
        self.anim_controller = AnimationController()

        # 状态机
        self.state_machine = PetStateMachine()

        # 位置
        self.pet_x = 100
        self.pet_y = 100

        # 拖拽
        self.dragging = False
        self._drag_mouse_start = QPoint()
        self._drag_win_start = QPoint()
        self._is_snapped_state = False  # 只有 _edge_snap() 真正执行贴边时才置 True

        # 常驻监听状态
        self._monitor_mic = None
        self._monitor_enabled = True   # 监听开关（热键/设置同步）
        self._pet_size = 300           # 宠物大小（设置界面同步）
        self._settings_dlg = None      # 设置对话框引用（防GC）
        self._recent_invalid = 0          # 连续无效响应计数
        self._echo_cooldown_until = 0.0   # TTS结束后冷却截止时间戳
        self._processing = False          # 正在处理中（防并发）
        self._processing_since = 0.0      # 进入处理态的时刻（看门狗用）
        self._processing_watchdog_sec = 45.0  # 处理态卡死上限（秒），超时强制解锁
        self._invalid_hint_until = 0.0    # 无效提示气泡冷却截止时间戳
        self._pending_wavs: list = []     # 处理中收到的待处理语音（FIFO，防止连说被丢）
        self._speaking = False            # 是否正在播放（半双工：播放时暂停监听降噪）
        self._echo_tail_until = 0.0       # 播放后残响屏蔽截止时间戳（防回声自言自语）
        self._last_speech_duration = 0.0  # 上次播报实际持续秒数（用于缩放回声屏蔽窗口）
        self._voice_muted = False         # 用户级静音开关：完全关闭语音输出（气泡仍显示）
        self._interrupt_block_until = 0.0 # 打断冷却截止时间戳（防宠物自身回声连环打断）
        self._interrupt_active_until = 0.0  # 打断反馈气泡保护期：此时间内不被tick清除
        self._interrupt_cooldown_until = 0.0  # 用户主动打断冷却：防止连续快速打断
        self._sentence_emotions: list[str] = []  # 逐句情绪缓存（播放时逐句切换动效）
        # ★ 播报代数计数器：每次 _speak_sentences 递增。
        #   用途：只允许"最新一次播报"的延迟恢复真正恢复麦克风。
        #   没有它时，一次交互里 ack→result→refine 三次播报各自起一个
        #   1.5s 延迟恢复线程，**先起的那个会在后面还在说话时把麦克风打开** ——
        #   麦克风拾到 TTS 回声 → 触发假录音 → 冷却期挡住用户真实说话。
        #   用户感知就是"她听不见我说话了"。
        self._speak_generation = 0

        # ── Agent 层接入（P0）──
        self._agent_bridge = AgentEventBridge()
        self._agent_bridge.ackReceived.connect(self._on_agent_ack)
        self._agent_bridge.resultReceived.connect(self._on_agent_result)
        self._agent_bridge.confirmReceived.connect(self._on_agent_confirm)
        self._agent_bridge.chatReceived.connect(self._on_agent_chat)
        self._agent_bridge.refineReceived.connect(self._on_agent_refine)
        self._agent_bridge.reminderReceived.connect(self._on_agent_reminder)
        self._agent_disposers: list = []   # 事件订阅的注销函数
        self._agent_busy = False           # Agent 正在处理（阻止并发新请求）
        self._agent_busy_since = 0.0       # Agent 开始处理的时刻（用于超时兜底）

        # 脚下字幕样式（默认黑底白字；设置保存后 update_subtitle_style 更新）
        self._subtitle_enabled = True
        self._subtitle_bg = QColor(0, 0, 0, 200)
        self._subtitle_fg = QColor(255, 255, 255)

        # 语音交互
        self._chat_history: list = []  # 对话历史（最近10轮）

        # 气泡
        self.bubble_text = ""
        self.bubble_timer = 0

        # VRM 渲染（Task 4 新增；默认 sprite，由 enable_vrm 显式启用）
        self.render_mode = "sprite"
        self._vrm_view = None
        self._vrm_bridge = None

        # 主循环 (按性能档位 FPS，来自 ui.fps.current：low=15/medium=30/high=60)
        self.timer = QTimer()
        self.timer.timeout.connect(self.tick)
        self._apply_fps_from_config()

        # 位置记忆防抖 timer：拖动结束后延迟保存位置（避免每帧写 QSettings）
        self._pos_save_timer = QTimer()
        self._pos_save_timer.setSingleShot(True)
        self._pos_save_timer.setInterval(700)
        self._pos_save_timer.timeout.connect(self._save_position)

        # 时间
        self.last_time = time.time()

        # 应用引用
        self.app = None

        # 位置恢复延迟到 load_pet 之后（此时窗口尺寸尚未确定，用默认300x350验证会误判）
        self._position_restored = False

        logger.info("PetWindow初始化完成")

    def _apply_fps_from_config(self):
        """按性能档位设置主循环帧率（ui.fps.current: low=15/medium=30/high=60）"""
        fps = 30
        try:
            app = getattr(self, "app", None)
            if app and getattr(app, "config_manager", None):
                fps = int(app.config_manager.get("ui.fps.current", 30))
            else:
                from core.config_manager import get_config_manager
                fps = int(get_config_manager().get("ui.fps.current", 30))
        except (TypeError, ValueError):
            fps = 30
        fps = max(10, min(60, fps))
        interval = max(16, int(1000 / fps))
        self._fps = fps
        self.timer.start(interval)
        logger.info(f"主循环帧率已设置: {fps}fps")

    # ========== 桌面位置记忆（QSettings） ==========

    # 用组织名/应用名限定 QSettings 命名空间，避免与其他程序冲突
    def _qsettings(self) -> QSettings:
        return QSettings("xbyaPet", "xbyaPet")

    def _restore_position(self) -> None:
        """启动时恢复上次关闭的位置；若不存在或位置非法则用默认(居中偏下)。"""
        try:
            s = self._qsettings()
            if not s.contains("pos/x") or not s.contains("pos/y"):
                logger.info("[位置] 无存档，使用默认位置")
                self._set_default_position()
                return
            x, y = int(s.value("pos/x", -1)), int(s.value("pos/y", -1))
            if x < 0 or y < 0:
                logger.info(f"[位置] 存档非法 x={x} y={y}，使用默认位置")
                self._set_default_position()
                return
            # 若保存的位置已不在任何屏幕可见区（分辨率变化/多屏拔除），回退默认
            geo = self._get_screen_geometry()
            w, h = self.width(), self.height()
            if (x >= geo.right() - 10 or x + w <= geo.left() or
                    y >= geo.bottom() - 10 or y + h <= geo.top()):
                logger.info(f"[位置] 存档位置({x},{y})超出屏幕范围，使用默认位置")
                self._set_default_position()
                return
            logger.info(f"[位置] 恢复到 ({x}, {y})")
            self.move(x, y)
        except Exception as e:
            logger.warning(f"恢复存档位置失败: {e}")
            self._set_default_position()

    def _set_default_position(self) -> None:
        """无存档/存档非法时：放到当前屏幕右下角偏上一点点。"""
        try:
            geo = self._get_screen_geometry()
            x = geo.right() - self.width() - 60
            y = geo.top() + 120
            self.move(x, y)
        except Exception:
            pass

    def _save_position(self) -> None:
        """保存当前窗口位置到 QSettings。"""
        try:
            s = self._qsettings()
            x, y = int(self.x()), int(self.y())
            s.setValue("pos/x", x)
            s.setValue("pos/y", y)
            s.sync()
            logger.info(f"[位置] 保存到 ({x}, {y})")
        except Exception as e:
            logger.warning(f"保存存档位置失败: {e}")

    def _debounced_save_position(self) -> None:
        """拖动结束后延迟保存位置（防抖，避免拖动每帧写磁盘）。"""
        try:
            self._pos_save_timer.start()
        except Exception:
            pass

    def closeEvent(self, event):
        """关闭窗口时：停止音频 → 保存位置 → 退出事件循环。"""
        # 强制停止所有音频播放
        try:
            import pygame
            if pygame.mixer.get_init():
                pygame.mixer.music.stop()
                pygame.mixer.stop()
                pygame.mixer.quit()
        except Exception:
            pass
        try:
            self._save_position()
        except Exception:
            pass
        # 停止语音监听
        try:
            if hasattr(self, "stop_voice_monitor"):
                self.stop_voice_monitor()
        except Exception:
            pass
        super().closeEvent(event)
        # 退出 Qt 事件循环 → 触发 app.py finally 中的 shutdown()
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
        if app:
            app.quit()

    def set_app(self, app):
        """设置应用引用"""
        self.app = app
        # 宠物名字：窗口标题（默认欣雅）
        try:
            name = app.config_manager.get("app.name", "欣雅") if (app and getattr(app, "config_manager", None)) else "欣雅"
            self.setWindowTitle(str(name) or "欣雅")
        except Exception:
            self.setWindowTitle("欣雅")
        # 字幕样式初始化（默认黑底白字）
        try:
            cm = app.config_manager if (app and getattr(app, "config_manager", None)) else None
            if cm:
                self.update_subtitle_style(
                    bool(cm.get("ui.subtitle_enabled", True)),
                    str(cm.get("ui.subtitle_bg_color", "#000000")),
                    str(cm.get("ui.subtitle_fg_color", "#FFFFFF")),
                )
                # 静音状态从配置恢复（同步内存标志 + app 层，保证菜单勾选与真实静音一致）
                self._voice_muted = bool(cm.get("voice.muted", False))
                if app and hasattr(app, "set_muted"):
                    try:
                        app.set_muted(self._voice_muted)
                    except Exception:
                        pass
        except Exception:
            pass
        # 声纹打断依赖用户声纹注册——未注册时异步弹窗引导录入
        self._maybe_enroll_voiceprint()
        # Agent 层事件订阅（app 已装配 Agent 栈时才生效）
        self._wire_agent_events()

    # ========== Agent 层接入 ==========

    def _wire_agent_events(self) -> None:
        """订阅 Agent 层事件（若 app 已装配 Agent 栈）

        跨线程安全：Agent 事件可能来自执行线程，这里只做
        「事件 → Qt 信号」的转译，UI 操作全部在槽函数（UI 线程）内完成。
        """
        self._unwire_agent_events()

        stack = getattr(self.app, "agent_stack", None) if self.app else None
        if stack is None:
            return

        from core.kernel.events import EventTypes

        bus = stack.bus
        bridge = self._agent_bridge

        def _on_ack(event):
            bridge.ackReceived.emit(dict(event.data))

        def _on_result(event):
            bridge.resultReceived.emit(dict(event.data))

        def _on_confirm(event):
            bridge.confirmReceived.emit(dict(event.data))

        def _on_chat(event):
            bridge.chatReceived.emit(dict(event.data))

        def _on_refine(event):
            bridge.refineReceived.emit(dict(event.data))

        def _on_reminder(event):
            bridge.reminderReceived.emit(dict(event.data))

        self._agent_disposers = [
            bus.on(EventTypes.FEEDBACK_ACK, _on_ack),
            bus.on(EventTypes.FEEDBACK_RESULT, _on_result),
            bus.on(EventTypes.FEEDBACK_CONFIRM, _on_confirm),
            bus.on(EventTypes.FEEDBACK_REFINE, _on_refine),
            bus.on(EventTypes.REMINDER_DUE, _on_reminder),
            bus.on("pipeline.chat", _on_chat),
        ]
        logger.info("[agent] PetWindow 已订阅 Agent 事件")

    def _unwire_agent_events(self) -> None:
        """取消 Agent 事件订阅"""
        for dispose in self._agent_disposers:
            try:
                dispose()
            except Exception:
                pass
        self._agent_disposers = []

    @property
    def _agent_enabled(self) -> bool:
        """Agent 栈是否可用且已启用"""
        stack = getattr(self.app, "agent_stack", None) if self.app else None
        if stack is None or getattr(stack, "disposed", True):
            return False
        return bool(getattr(stack.pipeline, "_enabled", False))

    def _on_agent_ack(self, data: dict) -> None:
        """Agent 即时确认：先让用户听到/看到反馈（不等执行结果）

        注意：本方法在主线程（Qt signal）中执行。
        play_audio / _speak_sentences 会阻塞，必须放到后台线程。
        """
        text = data.get("text") or ""
        audio = data.get("audio")
        elapsed = data.get("elapsed_ms", 0)

        if text:
            self.show_bubble(text, 2000)
        # 确认语不改变状态机（think 已由 _voice_pipeline 设置）

        if audio or text:
            def _play():
                try:
                    if audio:
                        self.app.play_audio(audio)
                    else:
                        self._speak_sentences([text])
                except Exception as e:
                    logger.warning("[agent] 播放缓存确认语失败: %s", e)
                finally:
                    # 确认语也是"她发出的声音"，播完同样要屏蔽回声，
                    # 否则这段音频会被麦克风收回、当成用户的下一句话
                    self._arm_echo_guard()
            threading.Thread(target=_play, daemon=True).start()

        logger.info("[agent] ack 已反馈 (%.0fms): %s", elapsed, text)

    def _on_agent_result(self, data: dict) -> None:
        """Agent 执行结果：情绪动效 + 气泡 + 语音播报"""
        self._agent_busy = False
        self._agent_busy_since = 0.0
        summary = data.get("summary") or ""
        emotion = data.get("emotion") or "talk"
        success = data.get("success", True)
        elapsed = data.get("elapsed_ms", 0)

        logger.info("[agent] 结果 (%.0fms, ok=%s): %s", elapsed, success, summary[:60])

        # 情绪动效（int / str 兜底映射到已有状态）
        anim_state = emotion if emotion in (
            "happy", "sad", "angry", "surprise", "love", "dance",
            "think", "calm_down", "talk", "idle",
        ) else "talk"
        self.anim_controller.set_state(anim_state)
        self._forward_vrm_state(anim_state)

        if not summary:
            self.set_state("idle")
            return

        self.show_bubble(summary, max(2500, int(len(summary) / 4.0 * 1000) + 500))

        # 播报结果（复用现有分句播报链路：逐句字幕 + 逐句动效）
        try:
            from core.text_utils import split_sentences
            sentences = split_sentences(summary) or [summary]
        except Exception:
            sentences = [summary]

        self._sentence_emotions = [anim_state] * len(sentences)
        # 播报结果（复用现有分句播报链路：逐句字幕 + 逐句动效）
        # ⚠️ **立即暂停麦克风**（在起线程之前）：防止播报开始后麦克风
        # 拾到TTS音频被当成用户说话。_speak_sentences 内部也会暂停，
        # 但那里在后台线程里，有竞态窗口。
        if self._monitor_mic:
            try:
                self._monitor_mic.pause_listening()
            except Exception:
                pass
        def _play_then_guard():
            try:
                self._speak_sentences(sentences)
            finally:
                self._arm_echo_guard()

        threading.Thread(target=_play_then_guard, daemon=True).start()

    def _arm_echo_guard(self) -> None:
        """在**播放结束之后**启动回声冷却 + 残响屏蔽窗口。

        所有播报路径（Agent 结果 / Agent 确认语 / 润色补播 / LLM 回复）
        都必须走这里，否则那条路径的回声就不会被屏蔽 —— 之前的
        `_on_agent_ack` / `_on_agent_refine` 就属于"漏挂"的那类。

        播报期间麦克风已由 _speak_sentences 暂停（pause_listening），
        此处冷却只需覆盖残响（扬声器余音 + 房间混响），固定 2 秒足够。
        """
        tail = 1.0   # 残响屏蔽窗口（秒）—— 暂停机制已挡住播报期间的回声，缩短以加快交互
        self._echo_tail_until = time.time() + tail
        self._echo_cooldown_until = time.time() + tail
        logger.info("[voice] 回声屏蔽已启动 (残响窗口 %.1fs)", tail)

    def _on_agent_refine(self, data: dict) -> None:
        """润色补播（P2-2）：LLM 把措辞改善后补一句，不打断当前播报节奏

        约定：结果已用模板文案正常播报过，这里只做"锦上添花"。
        若正在播报，则跳过（避免两句叠在一起）；气泡仍更新为更好的文案。
        """
        summary = data.get("summary") or ""
        if not summary:
            return
        if getattr(self, "_speaking", False):
            logger.debug("[agent] 润色到达时正在播报，仅更新气泡")
            self.show_bubble(summary, max(2500, int(len(summary) / 4.0 * 1000) + 500))
            return

        logger.info("[agent] 润色补播: %s", summary[:60])
        self.show_bubble(summary, max(2500, int(len(summary) / 4.0 * 1000) + 500))
        try:
            from core.text_utils import split_sentences
            sentences = split_sentences(summary) or [summary]
        except Exception:
            sentences = [summary]

        self._sentence_emotions = ["talk"] * len(sentences)
        # _speak_sentences 会阻塞，必须放后台；播完同样要挂回声屏蔽
        def _play_then_guard():
            try:
                self._speak_sentences(sentences)
            finally:
                self._arm_echo_guard()

        threading.Thread(target=_play_then_guard, daemon=True).start()

    def _on_agent_confirm(self, data: dict) -> None:
        """Agent 请求确认：气泡 + 语音询问，等待用户答复"""
        question = data.get("question") or "确定要这样做吗？"
        risk = data.get("risk", "medium")

        logger.info("[agent] 待确认 (risk=%s): %s", risk, question)

        # 高风险用惊讶表情，中风险用思考表情
        self.set_state("surprise" if risk == "high" else "think")
        self.show_bubble(question, max(3000, int(len(question) / 4.0 * 1000) + 1000))

        self._agent_busy = True
        self._sentence_emotions = ["think"] * 1
        # _speak_sentences 会阻塞，必须放后台；播完挂回声屏蔽
        def _play_then_guard():
            try:
                self._speak_sentences([question])
            finally:
                self._arm_echo_guard()

        threading.Thread(target=_play_then_guard, daemon=True).start()

    def _on_agent_chat(self, data: dict) -> None:
        """Agent 判定为闲聊：回退到原有 LLM 链路

        _run_llm_reply 包含 LLM 调用 + TTS + 播放，会阻塞主线程，
        必须放到后台线程。
        """
        text = data.get("text") or ""
        request_id = data.get("request_id") or ""
        self._agent_busy = False
        self._agent_busy_since = 0.0
        if text:
            threading.Thread(
                target=self._run_llm_reply,
                args=(text, request_id),
                daemon=True,
            ).start()

    def _on_agent_reminder(self, data: dict) -> None:
        """提醒到点：播报内容（F7）

        这条路径**不是**用户主动请求触发的，所以：
        - 不设 `_agent_busy`（提醒不该挡住用户下一次对话）
        - 正在说话时也不打断（`_speak_sentences` 自己会排队/忽略），
          但仍要把气泡显示出来，避免"没听见就等于没提醒"
        """
        text = data.get("text") or ""
        what = data.get("what") or ""
        if not text:
            return
        logger.info("[提醒] 到点播报: %s", what)
        self.show_bubble(text, 4000)
        try:
            # _speak_sentences 会阻塞，必须放后台
            threading.Thread(
                target=self._speak_sentences,
                args=([text],),
                daemon=True,
            ).start()
        except Exception as e:
            logger.warning("[提醒] 播报失败: %s", e)

    def _relayout_pet(self):
        """把精灵**水平居中**、并**抬到字幕条上方**。

        修的是一个一直存在的布局缺陷：`pet_x/pet_y` 原本硬编码 (100, 100)
        且**从不随窗口尺寸重算**。窗口是 `pet_size + 100`（127 → 228×228），
        而精灵画布只有 128×128 —— 于是精灵被画在 x[100..227] y[100..227]，
        **贴在窗口右下角**；窗口底部 44px 的字幕条（`SUBTITLE_AREA`）又正好
        落在 y[186..221]，**压在角色胸口上**。

        现在的算法（不写死坐标，跟着尺寸走）：
          · 水平：在窗口里居中
          · 垂直：放进「窗口高 − 字幕区」这块可用区里居中
        两个夹取都做了 `max(0, …)`，窗口比画布还小时不会算出负坐标。
        """
        try:
            cw, ch = self.anim_controller.get_size()
        except Exception:
            return
        avail_h = max(1, self.height() - SUBTITLE_AREA)
        self.pet_x = max(0, (self.width() - cw) // 2)
        self.pet_y = max(0, (avail_h - ch) // 2)

    def load_pet(self, pet_name: str):
        """加载宠物"""
        self.anim_controller.load_pet(pet_name)
        if self.render_mode == "vrm":
            return  # VRM 模式窗口尺寸由 3D 视图控制，不按精灵尺寸调整
        size = self.anim_controller.get_size()
        self.setFixedSize(size[0] + 100, size[1] + 100)  # 留边距给阴影和气泡
        logger.info(f"[位置] load_pet后尺寸={self.width()}x{self.height()}, 当前位置=({self.x()},{self.y()})")
        self._relayout_pet()
        # 窗口尺寸确定后再恢复位置（此时验证用的宽高才是真实的）
        if not self._position_restored:
            self._position_restored = True
            self._restore_position()
            logger.info(f"[位置] restore后位置=({self.x()},{self.y()})")

    def tick(self):
        """主循环"""
        now = time.time()
        delta = now - self.last_time
        self.last_time = now

        # Agent 忙碌超时兜底：Agent 层异常/事件丢失时，_agent_busy 会永久为 True，
        # 导致所有后续语音被静默排队 → 用户感知为"她听不见我说话"。
        # 超过 30 秒无结果即视为失败，释放锁让用户能继续对话。
        if self._agent_busy and self._agent_busy_since:
            if now - self._agent_busy_since > 30.0:
                logger.warning("[agent] 处理超时(>30s)，释放忙碌锁")
                self._agent_busy = False
                self._agent_busy_since = 0.0
                self.show_bubble("抱歉，这个请求超时了…", 2500)
                self.set_state("idle")

        # 处理态看门狗：ASR/LLM/TTS 任一阶段卡死时强制解锁。
        # 与上面的 _agent_busy 超时是**两道独立的闸**：那道管 Agent 层丢事件，
        # 这道管 _processing 标志本身被卡住（例如 ASR 推理线程挂死）。
        self._recover_stuck_processing()

        # 更新状态机
        new_state = self.state_machine.update(delta)
        if new_state:
            self.anim_controller.set_state(new_state)
            self._forward_vrm_state(new_state)

        # 更新动画（VRM 模式由 Web 渲染层驱动，跳过精灵合成）
        # 拖拽时跳过动画更新，减少 CPU 占用
        if self.render_mode != "vrm" and not self.dragging:
            self.anim_controller.update(delta)

        # 更新气泡
        now_ts = time.time()
        in_protected = now_ts < self._interrupt_active_until
        if self.bubble_timer > 0 and not self._speaking and not in_protected:
            self.bubble_timer -= delta * 1000
            if self.bubble_timer <= 0:
                self.bubble_text = ""

        # 智能重绘：只在有新帧或气泡变化时才触发 paintEvent
        if self.render_mode == "vrm":
            self.update()
        elif (self.anim_controller.has_new_frame()
              or self.bubble_text != getattr(self, '_last_bubble', '')):
            self.anim_controller.mark_frame_consumed()
            self._last_bubble = self.bubble_text
            self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        if self.render_mode == "vrm":
            if self.bubble_text:
                self._draw_subtitle(painter)
            painter.end()
            return

        cw, ch = self.anim_controller.get_size()

        # 绘制阴影（只在有实际内容时绘制）
        painter.setBrush(QColor(0, 0, 0, 40))
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(
            self.pet_x + 20,
            self.pet_y + ch - 10,
            cw - 40,
            20
        )

        # 绘制宠物
        frame = self.anim_controller.composite()
        painter.drawImage(self.pet_x, self.pet_y, frame)

        # 绘制脚下字幕
        if self.bubble_text:
            self._draw_subtitle(painter)

        painter.end()

    def _draw_subtitle(self, painter: QPainter):
        """绘制脚下字幕：黑底白字，位于窗口底部居中。

        背景框宽度自适应文字长度（最多 2 行，按可用宽度换行；
        第 2 行放不下则用 … 截断）。"""
        if not self._subtitle_enabled or not self.bubble_text:
            return
        font = painter.font()
        font.setPointSize(11)
        painter.setFont(font)
        fm = painter.fontMetrics()

        # 可用绘制区：宽度 = 窗口宽 - 左右边距；高度 = 字幕区
        max_w = max(24, self.width() - 16)
        max_h = SUBTITLE_AREA - 8
        text = self.bubble_text.strip()

        # 把文本按可用宽度包裹成最多 2 行
        lines = self._wrap_text(text, fm, max_w, max_lines=2)
        # 两行取最宽一行作为背景宽度（自适应文字长度），再加水平 padding
        line_w = max(fm.horizontalAdvance(ln) for ln in lines) if lines else 0
        pad_x = 14
        box_w = min(max_w, line_w + pad_x * 2)
        box_h = len(lines) * fm.height() + 8  # 每行高 + 上下 padding
        box_h = min(box_h, max_h)

        # 底部居中
        x = (self.width() - box_w) // 2
        y = self.height() - SUBTITLE_AREA + 2
        painter.setBrush(self._subtitle_bg)
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(x, y, box_w, box_h, 6, 6)

        # 文本：两行垂直居中，水平居中
        painter.setPen(self._subtitle_fg)
        text_rect = QRect(x, y, box_w, box_h)
        painter.drawText(
            text_rect, Qt.AlignCenter | Qt.TextWordWrap, "\n".join(lines)
        )

    def _wrap_text(self, text: str, fm, max_w: int, max_lines: int = 2):
        """把文本按宽度换行，最多 max_lines 行；最后一行放不下用 … 截断。

        按字符（含中文）逐个累积，保证不超宽。返回行列表。
        """
        lines = []
        cur = ""
        for ch in text:
            if fm.horizontalAdvance(cur + ch) <= max_w:
                cur += ch
                continue
            # 当前字符会让该行超宽：若已是最后一行，直接截断 + …
            if len(lines) >= max_lines - 1:
                cur = self._clamp_line(cur, fm, max_w, ellipsis=True)
                lines.append(cur)
                return lines
            # 否则换行
            if cur:
                lines.append(cur)
            cur = ch
        if cur:
            if len(lines) >= max_lines:
                # 已到最大行数，剩余文本无法展示：在最后一行加 …
                lines[-1] = self._clamp_line(lines[-1], fm, max_w, ellipsis=True)
            else:
                lines.append(cur)
        return lines

    def _clamp_line(self, line: str, fm, max_w: int, ellipsis: bool = False):
        """确保单行不超宽；必要时用 … 末尾截断。”"""
        if fm.horizontalAdvance(line) <= max_w:
            return line
        if not ellipsis:
            return line
        out = ""
        for ch in line:
            if fm.horizontalAdvance(out + ch + "…") > max_w:
                break
            out += ch
        return out + "…"

    def update_subtitle_style(self, enabled: bool, bg_hex: str = "#000000",
                              fg_hex: str = "#FFFFFF") -> None:
        """更新字幕样式（设置保存后热更新）"""
        self._subtitle_enabled = bool(enabled)
        try:
            self._subtitle_bg = QColor(bg_hex)
            self._subtitle_bg.setAlpha(200)  # 半透明黑底
        except Exception:
            self._subtitle_bg = QColor(0, 0, 0, 200)
        try:
            self._subtitle_fg = QColor(fg_hex)
        except Exception:
            self._subtitle_fg = QColor(255, 255, 255)
        self.update()

    def _is_main_thread(self) -> bool:
        """检查当前是否在主线程"""
        return QThread.currentThread() == self.thread()

    def _invoke_on_main(self, fn) -> None:
        """如果不在主线程，通过 QTimer.singleShot(0, ...) 转到主线程执行"""
        if self._is_main_thread():
            fn()
        else:
            QTimer.singleShot(0, fn)

    @staticmethod
    def _sanitize_text(text: str) -> str:
        """清理 LLM 输出中的 markdown 格式符号，只保留纯文本"""
        import re
        if not text:
            return text
        # 去掉 markdown 加粗/斜体标记 **bold** / *italic* / __bold__ / _italic_ / ___bold___
        text = re.sub(r'\*{1,3}(.+?)\*{1,3}', r'\1', text)
        text = re.sub(r'_{1,3}(.+?)_{1,3}', r'\1', text)
        # 去掉标题标记 # Title ## Subtitle
        text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
        # 去掉代码块标记 ```code```
        text = re.sub(r'```[\s\S]*?```', '', text)
        text = re.sub(r'`(.+?)`', r'\1', text)
        # 去掉引用标记 > quote
        text = re.sub(r'^>\s+', '', text, flags=re.MULTILINE)
        # 去掉水平分割线 --- / *** / ___
        text = re.sub(r'^[\-\*=_]{3,}\s*$', '', text, flags=re.MULTILINE)
        # 去掉链接 [text](url) → text
        text = re.sub(r'\[(.+?)\]\(.+?\)', r'\1', text)
        # 去掉孤立的 * / # / > 残留（连续2个以上）
        text = re.sub(r'[\*#>]{2,}', '', text)
        # 去掉多余空白行（清理后可能留下空行）
        text = re.sub(r'\n{3,}', '\n\n', text)
        return text.strip()

    def show_bubble(self, text: str, duration: int = 3000):
        """显示字幕文本（线程安全：后台线程调用会自动转到主线程）"""
        cleaned = self._sanitize_text(text)
        def _do():
            self.bubble_text = cleaned
            self.bubble_timer = duration
        self._invoke_on_main(_do)

    def set_state(self, state: str):
        """外部设置状态（线程安全：后台线程调用会自动转到主线程）"""
        def _do():
            self.state_machine.trigger_state(state)
            self.anim_controller.set_state(state)
            self._forward_vrm_state(state)
        self._invoke_on_main(_do)

    # ========== VRM 渲染层（Task 4） ==========

    def enable_vrm(self, model_path: str) -> bool:
        """启用 VRM 渲染（QWebEngineView 嵌入窗口底部区域）

        由 app 启动时显式调用（model_path 由调用方从 config 读取传入，
        PetWindow 内不直接读 config，保持单一职责）。
        - view 以 child 覆盖窗口下部（顶部留 VRM_BUBBLE_AREA 气泡区），页面背景透明
        - 失败自动降级：模型缺失/viewer 缺失 → False；bridgeError / loadFinished(False)
          / 任何异常 → _disable_vrm() 回 sprite

        Returns:
            True=启用成功（render_mode="vrm"）；False=失败或降级（"sprite"）
        """
        if self.render_mode == "vrm":
            return True
        if not model_path or not os.path.exists(model_path):
            logger.warning("[vrm] 模型文件不存在，保持 sprite 模式: %s", model_path)
            return False
        try:
            global QWebEngineView, VrmBridge
            if VrmBridge is None:
                from ui.vrm_bridge import VrmBridge as _bridge_cls
                VrmBridge = _bridge_cls
            if QWebEngineView is None:
                from PySide6.QtWebEngineWidgets import QWebEngineView as _view_cls
                QWebEngineView = _view_cls
            from PySide6.QtCore import QUrl as _qurl_cls

            viewer_html = str(
                Path(os.path.dirname(os.path.abspath(__file__))).parent
                / "assets" / "vrm" / "viewer.html"
            )
            if not os.path.exists(viewer_html):
                logger.warning("[vrm] viewer.html 不存在，保持 sprite 模式: %s", viewer_html)
                return False

            view = QWebEngineView(self)
            self._vrm_view = view
            bridge = VrmBridge(view)  # VrmBridge 内部自建 QWebChannel 并注册 petBridge
            self._vrm_bridge = bridge
            bridge.bridgeError.connect(self._disable_vrm)
            bridge.dragDelta.connect(self._on_vrm_drag)  # JS拖拽上报 → 移动窗口
            bridge.bodyPartClicked.connect(self._on_body_click)  # 点击部位 → 互动响应

            # 透明 hack：WebEngine 原生层透明（Windows DWM 需要 view 双层属性）
            view.setAttribute(Qt.WA_TranslucentBackground)
            view.setAttribute(Qt.WA_NoSystemBackground)
            view.page().setBackgroundColor(QColor(0, 0, 0, 0))

            # 转发 JS console 到 Python 日志，便于排查渲染卡点
            def _js_console(level, message, line, source):
                try:
                    logger.info("[vrm-js][%s] %s", level, message)
                except Exception:
                    pass
            try:
                view.page().javaScriptConsoleMessage.connect(_js_console)
            except Exception:
                pass

            view.loadFinished.connect(self._on_load_finished)

            # 关键：VRM 模式下 view 覆盖窗口，右键会走 WebEngine 的浏览器默认菜单，
            # 而不是 PetWindow.contextMenuEvent。这里让 view 弹出自定义菜单。
            view.setContextMenuPolicy(Qt.CustomContextMenu)
            view.customContextMenuRequested.connect(self._show_vrm_menu)

            self._layout_vrm_view()
            self.render_mode = "vrm"
            # 把模型路径作为 query 参数传给 viewer.html（app.js 从 ?model= 读取）
            # 用 QUrlQuery 避免中文路径编码问题
            _model_abs = os.path.abspath(model_path)
            _query = QUrlQuery()
            _query.addQueryItem("model", _model_abs)
            # 时间戳强制刷新，防止 QWebEngine 缓存旧模型
            _query.addQueryItem("_t", str(int(time.time() * 1000)))
            # 性能档位 → 渲染质量（low 关抗锯齿/降分辨率，medium/high 保持）
            _pfm = "high"
            try:
                _pfm = str(self.app.config_manager.get("system.performance_mode", "low")) if self.app else "low"
            except Exception:
                _pfm = "low"
            _query.addQueryItem("pfm", _pfm)
            # 面向镜头的修正角（度）：可配，默认 180（保持改动前行为）。
            # 为什么要可配：VRM 0.x 正面朝 +Z、1.0 正面朝 −Z，写死一个值
            # 必然让其中一半模型背对镜头（实测见 docs/agent/evidence/p5/vrm_facing.txt）。
            _yaw = 180
            try:
                if self.app:
                    _yaw = int(self.app.config_manager.get("ui.vrm_yaw_deg", 180))
            except Exception:
                _yaw = 180
            _query.addQueryItem("yaw", str(_yaw))
            view_url = _qurl_cls.fromLocalFile(viewer_html)
            view_url.setQuery(_query)
            view.load(view_url)
            logger.info("[vrm] 已启用 VRM 渲染: %s (viewer=%s)", model_path, viewer_html)
            return True
        except Exception as e:
            logger.error("[vrm] 启用 VRM 失败，降级 sprite: %s", e)
            self._disable_vrm()
            return False

    def _disable_vrm(self) -> None:
        """降级/回退：render_mode 切回 sprite，隐藏并释放 WebEngine view

        sprite 精灵路径完整保留（降级目标），需可重入/幂等。
        """
        self.render_mode = "sprite"
        # 恢复精灵尺寸（对齐 load_pet 公式：size + 100 边距），避免降级后画面被裁切
        size = self.anim_controller.get_size()
        self.setFixedSize(size[0] + 100, size[1] + 100)
        self._vrm_bridge = None
        view = self._vrm_view
        self._vrm_view = None
        if view is not None:
            try:
                view.hide()
            except Exception:
                pass
            try:
                view.deleteLater()
            except Exception:
                pass
            logger.info("[vrm] 已降级为 sprite 渲染")

    def _on_load_finished(self, ok: bool) -> None:
        """viewer.html 加载完成回调；失败则降级 sprite"""
        if not ok:
            logger.warning("[vrm] viewer 加载失败（loadFinished=False），降级 sprite")
            self._disable_vrm()

    def _forward_vrm_state(self, state: str) -> None:
        """vrm 模式下把小写状态名转发给 bridge（经 STATE_TO_VRM 查表）

        未映射状态（wander/stare/dance 等）跳过；sprite 模式下为 no-op。
        """
        if self.render_mode != "vrm" or self._vrm_bridge is None:
            return
        js_state = STATE_TO_VRM.get(state)
        if js_state:
            try:
                self._vrm_bridge.set_state(js_state)
            except Exception as e:
                logger.error("[vrm] 状态转发失败: %s", e)

    def _on_vrm_drag(self, dx: float, dy: float) -> None:
        """JS 拖拽上报的位移增量 → 移动窗口（仅 vrm 模式）"""
        if self.render_mode != "vrm":
            return
        self.move(int(self.x() + dx), int(self.y() + dy))
        # 拖动中防抖记录位置（松手后 700ms 写 QSettings）
        self._debounced_save_position()

    # 点击部位 → (动作名, 气泡文案)
    _BODY_CLICK_MAP = {
        "head": ("pet", "呀，好舒服呀~"),
        "body": ("pet", "嘿嘿，我在呢~"),
        "tail": ("hit_tail", "唉呀，那边有点敏感啦！"),
        "ear": ("hello", "嗯？找我吗？"),
    }

    def _on_body_click(self, part: str):
        """点击宠物不同部位 → 播放动作 + 气泡反馈（仅 vrm 模式）"""
        if self.render_mode != "vrm":
            return
        action, text = self._BODY_CLICK_MAP.get(part, ("pet", "嗯？"))
        if self._vrm_bridge is not None:
            self._vrm_bridge.play_action(action)
        if self._echo_cooldown_until <= time.time():
            self.show_bubble(text, 2500)

    def _layout_vrm_view(self) -> None:
        """view 占窗口上部（底部留 SUBTITLE_AREA 字幕区给 QPainter）"""
        view = self._vrm_view
        if view is None:
            return
        view.setGeometry(
            0, 0, self.width(),
            max(0, self.height() - SUBTITLE_AREA),
        )

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._layout_vrm_view()
        # 窗口尺寸一变就重算精灵位置。缺这一句的话，`apply_settings` 改
        # ui.pet_size 后 `setFixedSize` 会触发本回调，但 pet_x/pet_y 仍是旧值 ——
        # 精灵会重新贴到右下角、又被字幕条压住（就是修之前那个现象）。
        self._relayout_pet()

    # ========== 拖拽 + 边缘吸附 ==========

    # 贴边吸附阈值（px）：窗口边缘距屏幕边缘 < SNAP_DIST 时触发
    SNAP_DIST = 12
    # 贴边后保留的可见手柄宽度/高度（px）
    HANDLE_SIZE = 8

    def _get_screen_geometry(self) -> QRect:
        """获取当前鼠标所在屏幕的可用几何（排除任务栏）"""
        from PySide6.QtGui import QGuiApplication
        screen = QGuiApplication.screenAt(self.mapToGlobal(QPoint(0, 0)))
        if screen is None:
            screen = QGuiApplication.primaryScreen()
        return screen.availableGeometry() if screen else QRect(0, 0, 1920, 1080)

    def _edge_snap(self):
        """贴边吸附：窗口贴到屏幕边缘后隐藏，只留手柄。仅左右边缘触发。"""
        geo = self._get_screen_geometry()
        x, y = self.x(), self.y()
        w, h = self.width(), self.height()
        snapped = False

        # 左边缘
        if x <= geo.left() + self.SNAP_DIST:
            self.move(geo.left() - w + self.HANDLE_SIZE, y)
            snapped = True
        # 右边缘
        elif x + w >= geo.right() - self.SNAP_DIST:
            self.move(geo.right() - self.HANDLE_SIZE, y)
            snapped = True

        if snapped:
            self._is_snapped_state = True
            logger.debug(f"边缘吸附 → ({self.x()}, {self.y()})")

    def _is_edge_snapped(self) -> bool:
        """检查是否处于贴边隐藏状态（只看标志，不靠位置判断）"""
        return self._is_snapped_state

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            # 取消贴边状态：如果当前是贴边隐藏状态，点击时弹出
            if self._is_edge_snapped():
                self._unsnap()
                return
            self.dragging = True
            # 记录按下信息（用于判断"点击打断"）
            self._press_time = time.time()
            self._press_pos = event.position()
            # 记录鼠标全局位置 + 窗口当前位置，用于拖拽偏移
            gp = event.globalPosition()
            self._drag_mouse_start = QPoint(int(gp.x()), int(gp.y()))
            self._drag_win_start = self.pos()
            event.accept()

    def mouseMoveEvent(self, event):
        if self.dragging:
            gp = event.globalPosition()
            dx = int(gp.x()) - self._drag_mouse_start.x()
            dy = int(gp.y()) - self._drag_mouse_start.y()
            new_x = self._drag_win_start.x() + dx
            new_y = self._drag_win_start.y() + dy
            # 限制在屏幕范围内
            geo = self._get_screen_geometry()
            new_x = max(geo.left(), min(new_x, geo.right() - self.width()))
            new_y = max(geo.top(), min(new_y, geo.bottom() - self.height()))
            self.move(new_x, new_y)
            event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            was_dragging = self.dragging
            self.dragging = False
            self._edge_snap()
            # 拖拽松手后记住新位置（sprite 模式拖拽由本窗口处理）
            try:
                self._save_position()
            except Exception:
                pass
            # 「点击打断」：快速点击（按下→松开时间短且几乎未移动）
            # 覆盖所有活跃状态：speaking / processing / listen / think
            press_t = getattr(self, "_press_time", None)
            press_p = getattr(self, "_press_pos", None)
            dur = (time.time() - press_t) if press_t else 999
            moved = False
            if press_p is not None:
                rel_p = event.position()
                moved = (rel_p - press_p).manhattanLength() > 6
            is_active = self._speaking or self._processing or self._monitor_enabled
            if not moved and dur < 0.4:
                if is_active:
                    # 活跃状态 → 打断
                    logger.info("[打断] 点击触发打断 (speaking=%s, processing=%s)", self._speaking, self._processing)
                    self.interrupt_current_speech()
                else:
                    # 空闲状态 → 触发交互动效（摸头/戳/开心）
                    new_state = self.state_machine.on_click()
                    self.anim_controller.set_state(new_state)
                    self._forward_vrm_state(new_state)
                    logger.info("[互动] 点击触发: %s", new_state)
            # 拖拽结束 → 触发动效
            if was_dragging and not moved:
                drag_state = self.state_machine.on_drag()
                self.anim_controller.set_state(drag_state)
                self._forward_vrm_state(drag_state)
                logger.info("[互动] 拖拽触发: %s", drag_state)
            event.accept()

    def _unsnap(self):
        """从贴边状态弹出到正常位置（仅左右边缘贴边）"""
        geo = self._get_screen_geometry()
        x, y = self.x(), self.y()
        w, h = self.width(), self.height()
        # 弹回方向：根据当前贴的是哪条边
        if x <= geo.left() + self.SNAP_DIST + 5:
            x = geo.left() + 4
        elif x + w >= geo.right() - self.SNAP_DIST - 5:
            x = geo.right() - w - 4
        self.move(x, y)
        self._is_snapped_state = False
        logger.debug(f"取消贴边 → ({x}, {y})")

    # ========== 鼠标事件 ==========

    def start_voice_monitor(self) -> bool:
        """启动常驻监听（无唤醒词，说话即响应）"""
        try:
            from services.microphone_service import get_microphone_service
            listening = True
            speech_vol = 300.0
            max_sec = 10.0
            if self.app and getattr(self.app, "config_manager", None):
                cm = self.app.config_manager
                listening = cm.get("voice.listening", True)
                speech_vol = float(cm.get("voice.speech_volume", 300.0))
                max_sec = float(cm.get("voice.max_listen_seconds", 10.0))
            if not listening:
                logger.info("voice.listening=false，不启动常驻监听")
                self._monitor_enabled = False
                return False
            self._monitor_mic = get_microphone_service()
            if not self._monitor_mic.is_available():
                logger.warning("麦克风不可用，无法常驻监听")
                self._monitor_enabled = False
                return False
            # 每次启动监听都按 config 重新解析设备：用户可能刚改过
            # voice.mic_device（多声源场景下必须能切换，不能只在进程启动时读一次）
            try:
                want = cm.get("voice.mic_device", None) if (self.app and getattr(self.app, "config_manager", None)) else None
                if want is None or want == "":
                    # ── 自动探测前等待 TTS 播完 ──
                    # 启动时 TTS 在播报确认语/开场白，所有设备都拾取回声，
                    # 此时探测 = 选到"回声最大的设备"而非"真正收人声的设备"。
                    # 等播报结束 + 回声冷却后再探测，安静环境下才能分辨。
                    wait_start = time.time()
                    while getattr(self, "_speaking", False) and time.time() - wait_start < 10.0:
                        time.sleep(0.2)
                    # 额外等回声冷却（TTS 播完后扬声器残响 ~2s）
                    cooldown_left = self._echo_cooldown_until - time.time()
                    if cooldown_left > 0:
                        logger.info("[mic] 等待回声冷却结束（%.1fs）再探测设备", cooldown_left)
                        time.sleep(min(cooldown_left, 5.0))
                    logger.info("[mic] TTS 已结束，开始自动探测设备")
                    self._monitor_mic.set_input_device(None)
                else:
                    self._monitor_mic.set_input_device(want)
            except Exception as e:
                logger.warning("[mic] 应用 config 设备失败: %s", e)
            dev = self.current_microphone_device()
            if dev:
                logger.info("[mic] 监听使用设备 idx=%d (%s)", dev["index"], dev["name"])
            else:
                logger.info("[mic] 监听使用系统默认输入设备")
            # on_start：检测到语音起始时立即给出**视觉反馈**。
            # ⚡ 延迟优化的关键一环：ASR 在本机 8 核 CPU 上要 3~5 秒，
            #   用户在这几秒里看不到任何变化 → 感知是"她根本没听见"。
            #   这里在**检测到声音的瞬间**就切 listen 动效 + 弹气泡，
            #   把"有没有反应"和"反应内容"解耦：
            #     · 有反应 = 检测到声音（<100ms，必然及时）
            #     · 内容   = ASR + LLM 结果（3~6 秒，无法避免）
            # 是否打断由录音结束后的 _on_speech_captured 声纹验证决定
            # （保证只有用户声音能打断）。
            def _on_voice_start(vol):
                self.set_state("listen")
                # 只在"当前没有在播报、也没在处理"时弹气泡 ——
                # 播报期间的气泡属于宠物自己说的话，不能被这里的提示顶掉。
                if not self._speaking and not self._processing:
                    self._show_listening_feedback()
                # 不在语音起始就打断：避免宠物回声被误判为"用户开口"而立即误停。
                # `_speaking` 期间麦克风拾到的起始大概率是宠物自己的声音，需等完整语音做声纹确认。
            ok = self._monitor_mic.listen_standby(
                callback=self._on_speech_captured,
                on_start=_on_voice_start,
                speech_volume=speech_vol,
                max_seconds=max_sec,
            )
            if ok:
                self._monitor_enabled = True
                logger.info("常驻语音监听已启动")
            else:
                self._monitor_enabled = False
            return ok
        except Exception as e:
            logger.error(f"启动常驻监听失败: {e}")
            self._monitor_enabled = False
            return False

    def stop_voice_monitor(self) -> None:
        """停止常驻监听"""
        if self._monitor_mic:
            try:
                self._monitor_mic.stop_listen_standby()
            except Exception as e:
                logger.error(f"停止监听失败: {e}")

    # ========== 麦克风设备切换（多声源） ==========

    def list_microphone_devices(self) -> list:
        """列出所有输入设备（供设置界面/诊断使用）。"""
        try:
            from services.microphone_service import MicrophoneService
            return MicrophoneService.list_input_devices()
        except Exception as e:
            logger.warning("[mic] 枚举设备失败: %s", e)
            return []

    def current_microphone_device(self):
        """返回当前使用的设备信息（dict）或 None（自动挑选/不可用）。"""
        try:
            from services.microphone_service import MicrophoneService
            mic = self._monitor_mic or get_microphone_service()
            cur = mic._pick_mic_index()
            if cur is None:
                return None
            for d in MicrophoneService.list_input_devices():
                if d["index"] == cur:
                    return d
        except Exception as e:
            logger.debug("[mic] 读取当前设备失败: %s", e)
        return None

    def switch_microphone(self, selector) -> bool:
        """运行时切换输入设备（重启常驻监听使其生效）。

        Args:
            selector: int/数字字符串 → 设备索引；其他字符串 → 名字片段；
                      None/"" /"auto" → 恢复自动挑选

        Returns:
            是否成功。成功后**自动重启监听**，无需重启应用。

        多声源场景（本机麦克风 + 远程桌面虚拟麦克风）下，"自动挑选"可能选到
        收不到用户声音的那个 —— 用本方法显式切换，或把选择写进
        `config.yaml` 的 `voice.mic_device`。
        """
        if isinstance(selector, str) and selector.lower() in ("auto", "none", "null", ""):
            selector = None
        try:
            mic = self._monitor_mic or get_microphone_service()
        except Exception as e:
            logger.error("[mic] 切换失败，麦克风服务不可用: %s", e)
            return False

        was_monitoring = self._monitor_enabled
        # 先停监听：同一设备被两个流同时打开会失败
        if was_monitoring:
            self.stop_voice_monitor()
            time.sleep(0.3)

        ok = mic.set_input_device(selector)
        if not ok:
            # 切换失败：把监听恢复回原状，不留"监听已停"的副作用
            if was_monitoring:
                self.start_voice_monitor()
            return False

        # 写回配置，重启后仍生效
        try:
            if self.app and getattr(self.app, "config_manager", None):
                self.app.config_manager.set("voice.mic_device", selector)
        except Exception as e:
            logger.warning("[mic] 写入 config 失败（仅本次生效）: %s", e)

        if was_monitoring:
            started = self.start_voice_monitor()
            if not started:
                logger.warning("[mic] 切换后监听重启失败")
        dev = self.current_microphone_device()
        if dev:
            logger.info("[mic] 已切换到设备 idx=%d (%s)", dev["index"], dev["name"])
            self.show_bubble("🎤 麦克风：%s" % dev["name"][:24], 2200)
        return True

    def probe_microphone_levels(self, seconds: float = 1.2) -> list:
        """逐个设备短采样测音量，用于找出"哪个声源真的有声音"。"""
        try:
            mic = self._monitor_mic or get_microphone_service()
            return mic.probe_device_levels(seconds=seconds)
        except Exception as e:
            logger.warning("[mic] 试听失败: %s", e)
            return []

    def cycle_microphone_device(self):
        """热键回调：按顺序轮换到下一个输入设备（只轮换真实麦克风，跳过回环）。

        多声源场景下用来快速试出"哪个设备能收到我的声音"：
        每按一次切一个，并弹泡显示设备名。选到合适的之后，
        用 `tools/list_mic_devices.py --set <idx>` 固化到配置。
        """
        try:
            from services.microphone_service import MicrophoneService
            devs = [d for d in MicrophoneService.list_input_devices()
                    if d["kind"] == "mic"]
            if not devs:
                self.show_bubble("没有可用麦克风", 2000)
                return
            cur = self.current_microphone_device()
            cur_idx = cur["index"] if cur else None
            order = [d["index"] for d in devs]
            if cur_idx in order:
                nxt = order[(order.index(cur_idx) + 1) % len(order)]
            else:
                nxt = order[0]
            if self.switch_microphone(nxt):
                dev = self.current_microphone_device()
                name = dev["name"][:20] if dev else str(nxt)
                self.show_bubble("🎤 %s（%d/%d）" % (
                    name, order.index(nxt) + 1, len(order)), 2500)
                logger.info("[mic] 热键轮换 → idx=%d (%s)", nxt, name)
        except Exception as e:
            logger.error("[mic] 轮换设备失败: %s", e)


    def _on_speech_captured(self, wav_path: str):
        """监听捕获一次说话后的入口。

        语音处理策略：
        - 自动打断（可选，config voice.auto_interrupt）：宠物说话时检测到语音→打断。
          外放环境下不可靠（易漏断/自断），默认关闭；主打断方式为热键或点击宠物。
          热键**以配置为准**：`voice.hotkey_interrupt`，出厂值 `Ctrl+Alt+D`
          （见本文件 `_register_hotkeys()`）。此处原先写死 `Ctrl+Alt+Space`，
          与实际绑定**不一致** —— 人工验收手册 M3 要求"按打断热键"，照着这行注释按
          会按到没绑定的组合，于是场景测不出来。文档写死一个可配置的值就是这个下场。
        - 正在处理 / 冷却中：加入待处理队列（最多2条），当前对话结束后按序处理
        - 空闲：立即处理
        """
        # 自动打断：仅当配置开启（voice.auto_interrupt=true）时启用声纹验证打断
        if self._speaking:
            auto_interrupt = False
            if self.app and getattr(self.app, "config_manager", None):
                auto_interrupt = bool(self.app.config_manager.get("voice.auto_interrupt", False))
            if not auto_interrupt:
                # 宠物说话期间捕获到语音：默认不打断，但**不再静默丢弃** ——
                # 入待处理队列，等本轮播完立即处理。用户感知是"她说了但我打断不了"，
                # 而不是"她完全没听见"（原实现直接删 wav，用户无从判断）
                if len(self._pending_wavs) < 2:
                    self._pending_wavs.append(wav_path)
                    logger.info("[语音] 播报期间捕获语音，已入队 (len=%d)", len(self._pending_wavs))
                    self._show_heard_feedback()
                else:
                    self._pending_wavs[0] = wav_path  # 覆盖最旧
                    logger.info("[语音] 播报期间队列已满，覆盖最旧")
                return
            # 防回声自触发：打断冷却期内丢弃，避免连环打断
            if time.time() < self._interrupt_block_until:
                self._delete_wav(wav_path)
                return
            # 声纹验证：只有用户本人声音才能打断
            if not self._is_user_voice(wav_path):
                logger.info("[打断] 非用户声纹（宠物回声/他人），不打断")
                self._delete_wav(wav_path)
                return
            logger.info("[打断] 用户声纹验证通过，打断宠物说话")
            # 停止当前播放（播放线程检测到打断标志后退出，清空剩余队列）
            if self.app and hasattr(self.app, "interrupt_speech"):
                try:
                    self.app.interrupt_speech()
                except Exception as e:
                    logger.warning(f"打断失败: {e}")
            self._speaking = False
            # 打断时恢复麦克风监听
            if self._monitor_mic:
                try:
                    self._monitor_mic.resume_listening()
                except Exception:
                    pass
            # 冷却：防宠物自己的下一句回声立即再触发打断（死循环）
            self._interrupt_block_until = time.time() + 1.5
            # 用户打断的语音入队（最高优先），当前 pipeline 播完停止后由 _poll_pending 立即处理。
            if len(self._pending_wavs) < 2:
                self._pending_wavs.append(wav_path)
            else:
                self._pending_wavs[0] = wav_path  # 覆盖最旧，保证打断的语音被处理
            return
        # 第2层：播放后残响屏蔽窗口（覆盖扬声器余音/环境混响），避免自言自语
        if time.time() < self._echo_tail_until:
            self._delete_wav(wav_path)
            return
        # 处理中/冷却中/Agent执行中 → 若有空闲则入队（最多2条），否则丢弃最旧
        # _agent_busy：语音管线已把请求交给 Agent 层，结果尚未返回，此时不应并发新请求
        if self._processing or self._agent_busy or time.time() < self._echo_cooldown_until:
            if len(self._pending_wavs) < 2:
                logger.debug("正在处理，语音入待处理队列 (len=%d)", len(self._pending_wavs) + 1)
                self._pending_wavs.append(wav_path)
            else:
                logger.debug("待处理队列已满(2)，丢弃本次")
                self._delete_wav(wav_path)
            return
        # 空闲：立即处理
        self._start_pipeline(wav_path)

    def _show_heard_feedback(self):
        """视觉反馈：让用户知道"我听到了"。跨线程安全。"""
        def _show():
            try:
                self.show_bubble("🎧 听到了…", 1500)
            except Exception:
                pass
        self._invoke_on_main(_show)

    def _show_listening_feedback(self):
        """语音起始的即时反馈（<100ms 可见）。

        ⚡ 与 `_show_heard_feedback` 的区别：
        - 本方法在**检测到声音的瞬间**触发（录音还没结束、ASR 还没开始）
        - `_show_heard_feedback` 在 ASR **识别出文本之后**触发

        两者配合形成"两段式反馈"：先让用户知道"我在听了"，
        再让用户知道"我听清了"。这解决了 ASR 慢导致的"看起来没反应"。

        防抖：连续语音（用户停顿又接着说）不应反复刷新气泡。
        """
        now = time.time()
        if now - getattr(self, "_last_listen_feedback", 0.0) < 2.0:
            return
        self._last_listen_feedback = now

        def _show():
            try:
                self.show_bubble("🎧 在听…", 1200)
            except Exception:
                pass
        self._invoke_on_main(_show)

    def _is_user_voice(self, wav_path: str) -> bool:
        """声纹验证：判断这段语音是否属于已 enroll 的用户。

        只有已注册用户声纹且 verify 通过才返回 True（即只有用户本人能打断）。
        未注册/引擎不可用 → 返回 False（安全，不打断）。
        """
        try:
            if self.app and hasattr(self.app, "get_plugin"):
                vp = self.app.get_plugin("VoiceprintEngine")
                if vp is None or not vp.is_available():
                    return False
                if not vp.is_enrolled("user"):
                    logger.debug("[声纹] 用户未注册声纹，不打断")
                    return False
                return bool(vp.verify(wav_path, "user"))
        except Exception as e:
            logger.warning(f"声纹验证异常: {e}")
            return False
        return False

    def _maybe_enroll_voiceprint(self) -> None:
        """启动时检查：若声纹引擎可用且用户未注册，异步弹窗引导录入声纹。
        声纹是"仅用户可打断"的前提，未注册则打断功能不生效。
        """
        try:
            if self.app and hasattr(self.app, "get_plugin"):
                vp = self.app.get_plugin("VoiceprintEngine")
                if vp is None or not vp.is_available():
                    return
                if vp.is_enrolled("user"):
                    return
        except Exception:
            return
        # 弹窗录入（延迟到窗口显示后，避免阻塞启动）
        from PySide6.QtCore import QTimer
        QTimer.singleShot(800, self._show_enroll_dialog)

    def _show_enroll_dialog(self) -> None:
        """引导用户录入声纹（读一句话），完成后存入 voiceprint 引擎"""
        from PySide6.QtWidgets import QMessageBox, QPushButton
        logger.info("[声纹] 用户未注册，弹出录入引导")

        # 读一段引导语
        msg = QMessageBox(self)
        msg.setWindowTitle("🔐 声纹录入（仅需一次）")
        msg.setText(
            "为了让「打断」只认你的声音（宠物自己不会打断自己），\n"
            "需要先录入你的声纹。\n\n"
            "点击「开始录入」后，请用你平时的声音念这句话：\n\n"
            "    \"欣雅你好，这是验证我的声音\"\n\n"
            "录完自动完成，之后只有你的声音能打断欣雅说话。"
        )
        record_btn = msg.addButton("🎙️ 开始录入", QMessageBox.AcceptRole)
        msg.addButton("暂不录入", QMessageBox.RejectRole)
        msg.exec()
        if msg.clickedButton() is not record_btn:
            logger.info("[声纹] 用户取消录入")
            return

        # 录音 + 录入
        self._record_and_enroll_voiceprint()

    def _record_and_enroll_voiceprint(self) -> None:
        """用麦克风录一段用户语音 → 提取声纹 → 存入引擎"""
        import threading
        def _do():
            try:
                from services.microphone_service import get_microphone_service
                mic = get_microphone_service()
                if not mic.is_available():
                    logger.warning("[声纹] 麦克风不可用，无法录入")
                    return
                self.show_bubble("请说：欣雅你好，这是验证我的声音 🎙️", 4000)
                import time
                wav = mic.start_recording(duration=6.0)
                if not wav:
                    logger.warning("[声纹] 录音失败")
                    return
                vp = self.app.get_plugin("VoiceprintEngine") if self.app else None
                if vp is None:
                    return
                ok = vp.enroll(wav, "user")
                # 清理录音
                try:
                    os.remove(wav)
                except Exception:
                    pass
                if ok:
                    logger.info("[声纹] 录入成功")
                    self.show_bubble("声纹录入成功 ✅ 现在只有你能打断我啦", 3000)
                else:
                    self.show_bubble("声纹录入失败，请重试", 3000)
            except Exception as e:
                logger.error(f"[声纹] 录入异常: {e}")
        threading.Thread(target=_do, daemon=True).start()

    def _start_pipeline(self, wav_path: str):
        """启动一次语音处理（进入 _processing）"""
        self._processing = True
        # ⚡ 看门狗起点：记录进入处理态的时刻。
        #   若任何阶段（ASR/LLM/TTS）卡死，_processing 会永久为 True，
        #   之后所有语音都只入队不处理 —— 用户感知是"她彻底不理我了"。
        #   `_tick` 里的看门狗据此强制解锁（见 _recover_stuck_processing）。
        self._processing_since = time.time()
        # ⚡ 立即暂停麦克风：ASR/LLM 期间麦克风在听 = TTS 回声持续触发假录音，
        #   假录音堆积 → ASR 音频含回声 → 7 秒才识别完 → 用户感知延迟巨大。
        if self._monitor_mic:
            try:
                self._monitor_mic.pause_listening()
            except Exception:
                pass
        import threading
        threading.Thread(target=self._voice_pipeline, args=(wav_path,), daemon=True).start()

    def _recover_stuck_processing(self) -> bool:
        """看门狗：处理态卡死超过上限则强制解锁。

        Returns:
            True = 本次执行了强制解锁
        """
        limit = getattr(self, "_processing_watchdog_sec", 45.0)
        since = getattr(self, "_processing_since", 0.0)
        if not self._processing or not since:
            return False
        age = time.time() - since
        if age <= limit:
            return False

        logger.warning(
            "[看门狗] 处理态已卡住 %.0f 秒（上限 %.0f 秒），强制解锁并给出提示",
            age, limit
        )
        self._processing = False
        self._processing_since = 0.0
        self._agent_busy = False
        self._agent_busy_since = 0.0
        # 顺手清掉待处理队列：这些语音已经太旧，播出去也是答非所问
        for w in list(self._pending_wavs):
            self._delete_wav(w)
        self._pending_wavs.clear()
        # 让用户知道发生了什么（沉默失败是最糟的失败）
        self.set_state("idle")
        try:
            self.show_bubble("刚才有点卡住了，再说一次好吗？", 2500)
        except Exception:
            pass
        return True

    def _poll_pending(self):
        """处理完当前语音后，取出待处理队列中的下一条继续处理"""
        if self._processing or not self._pending_wavs:
            return
        wav_path = self._pending_wavs.pop(0)
        self._start_pipeline(wav_path)

    def _delete_wav(self, wav_path: str):
        """删除监听生成的临时wav（消费者负责清理）"""
        try:
            os.remove(wav_path)
        except OSError:
            pass

    def _speak_sentences(self, sentences: list):
        """按句播报：句子并行合成、顺序播放（边合成边说）

        核心思路：合成用线程池（TTS网络请求），播放用后台线程
        （pygame阻塞播放）。播放线程按句子顺序从队列消费，
        第一句合成完立即开播，后续句在此期间并行合成。

        Args:
            sentences: 句子列表（按顺序播放）
        """
        if not sentences or not self.app:
            return
        # 解释器关闭中：不再起线程/发起网络请求，否则会在 teardown 阶段报
        # "cannot schedule new futures after interpreter shutdown" 噪音
        import sys as _sys
        if _sys.is_finalizing():
            logger.debug("[voice] 解释器关闭中，跳过播报")
            return
        _paused_mic = False
        # 本次播报的代数：只有"我自己是最新一代"时才允许恢复麦克风。
        # 见 __init__ 里 _speak_generation 的注释（ack→result→refine 连续播报的场景）。
        self._speak_generation += 1
        _my_generation = self._speak_generation
        try:
            import threading
            import queue as _queue
            import concurrent.futures

            # ★ 播报一开始立即暂停麦克风（防回声）—— 不等 _player 循环，
            # 因为第一句合成完就立即播放，mic 线程可能已经在拾 TTS 余音了。
            # ⚠️ 由下面的 finally 兜底恢复：本函数有多条提前 return / 异常出口，
            #    漏掉任何一个都会让麦克风**永久停在暂停状态** ——
            #    用户感知就是"完全没反应"（实测踩过）。
            if self._monitor_mic:
                try:
                    self._monitor_mic.pause_listening()
                    _paused_mic = True
                except Exception:
                    pass

            # 队列项：(audio, text)。携带文本以便播放时逐句动态更新字幕
            audio_queue = _queue.Queue()
            # 逐句情绪索引：播放线程按此索引从 _sentence_emotions 取对应情绪
            self._sentence_idx = 0
            # 本次播报起始时刻（收尾时用来算"说了多久"→ 回声窗口长度）
            t_speak_start = time.time()

            # 播放线程：顺序播放音频块（阻塞式）
            # 字幕与音频同步：每句开播前更新为该句文本（字幕逐句滚动），
            # 播放期间 tick 冻结计时，全部播完后再停留 2.5s 清除。
            def _player():
                while True:
                    # 每句取队列前先检查打断（避免阻塞在get时错过打断信号）
                    try:
                        item = audio_queue.get(timeout=0.5)
                    except _queue.Empty:
                        # 超时：检查打断/退出/静音
                        if not self.app or not getattr(self.app, '_running', True):
                            break
                        if self._voice_muted:
                            break
                        if self.app and getattr(self.app, "_interrupt_requested", False):
                            logger.info("[voice] 播放线程：检测到打断信号（等待队列期间）")
                            break
                        continue
                    if item is None:
                        # 全部播完：字幕再停留一会儿（若正在显示说话文本）
                        if self.bubble_text:
                            self.bubble_timer = 2500
                        break                    # 退出时跳过播放
                    if not self.app or not getattr(self.app, '_running', True):
                        break
                    # 静音后立即停止，丢弃剩余队列（避免继续播）
                    if self._voice_muted:
                        logger.info("[voice] 静音中，停止后续播放")
                        self._speaking = False
                        break
                    # 打断检查：开始播放新句前检查是否已被打断
                    if self.app and getattr(self.app, "_interrupt_requested", False):
                        logger.info("[voice] 播放线程：被打断，跳过剩余句子")
                        break
                    audio, text = item
                    # 逐句更新字幕：只保留当前句（显示循环中正在说的那句），
                    # 时长按该句预估，播放时被 _speaking 冻结，播完进入下一句
                    if text:
                        show_dur = max(2500, int(len(text) / 4.0 * 1000) + 500)
                        self.show_bubble(text, show_dur)

                    # ── 逐句情绪动效切换（跨线程安全） ──
                    if self._sentence_emotions and self._sentence_idx < len(self._sentence_emotions):
                        sent_emotion = self._sentence_emotions[self._sentence_idx]
                        if sent_emotion and sent_emotion != "talk":
                            # 播放该句时切换到对应情绪动效（转主线程执行）
                            def _set_emotion(em=sent_emotion):
                                self.anim_controller.set_state(em)
                                self._forward_vrm_state(em)
                            self._invoke_on_main(_set_emotion)
                            logger.debug("[emotion] 逐句动效: [%d] %s", self._sentence_idx, sent_emotion)
                    self._sentence_idx += 1
                    # 半双工：正在播放视为"宠物在说话"，捕获的拾音是自己的声音 → 丢弃
                    self._speaking = True
                    # 每段播放前重置打断标志（确保新一段能正常播完，除非再次被打断）
                    if self.app and hasattr(self.app, "_interrupt_requested"):
                        self.app._interrupt_requested = False
                    try:
                        self.app.play_audio(audio)
                    except Exception as e:
                        logger.error(f"播放失败: {e}")
                    finally:
                        self._speaking = False
                    # 被打断：丢弃剩余队列，结束播放线程（打断后立即处理用户新语音）
                    if self.app and getattr(self.app, "_interrupt_requested", False):
                        logger.info("[voice] 被用户语音打断，停止后续播放")
                        # 排空队列（丢弃未播的句）
                        while True:
                            try:
                                audio_queue.get_nowait()
                            except Exception:
                                break
                        break
            player_thread = threading.Thread(target=_player, daemon=True)
            player_thread.start()

            # 合成线程池：逐句 TTS（网络请求耗时，并行加快）
            def _synth(s):
                try:
                    app = self.app
                    if app is None or not hasattr(app, "synthesize"):
                        return None
                    return app.synthesize(s)
                except Exception as e:
                    logger.error(f"合成失败: {e}")
                    return None

            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                try:
                    futures = [pool.submit(_synth, s) for s in sentences]
                except RuntimeError as e:
                    # 解释器关闭中/线程池已停：安静跳过，不制造噪音日志
                    logger.debug("[voice] 合成线程池不可用，跳过本次播报: %s", e)
                    return
                # 按句子顺序取结果入队（保序）；音频+文本一起入队，供字幕动态更新
                # 每句最多等15秒（TTS网络请求），超时跳过该句
                for s, f in zip(sentences, futures):
                    # 合成前检查打断（避免继续合成已不需要的句子）
                    if self.app and getattr(self.app, "_interrupt_requested", False):
                        logger.info("[voice] 合成中断：检测到打断信号，跳过剩余合成")
                        # 取消未完成的合成任务
                        for pending_f in futures:
                            pending_f.cancel()
                        break
                    try:
                        audio = f.result(timeout=15)
                    except concurrent.futures.TimeoutError:
                        logger.warning(f"[voice] TTS合成超时(15s)，跳过: {s[:30]}...")
                        audio = None
                    except Exception as e:
                        logger.error(f"[voice] TTS合成异常: {e}")
                        audio = None
                    if audio:
                        audio_queue.put((audio, s))

            audio_queue.put(None)
            player_thread.join()
            # 记录本次播报实际时长：用于把回声屏蔽窗口按"她说了多久"放大
            # （固定 4 秒挡不住一段 15 秒长回复的余音 + 房间混响）
            try:
                self._last_speech_duration = max(0.0, time.time() - t_speak_start)
            except Exception:
                self._last_speech_duration = 0.0
            # 播报结束：处理播报期间被排队的用户语音（否则会一直躺在队列里没人取）
            try:
                self._poll_pending()
            except Exception as e:
                logger.warning("[voice] 播报结束后取出待处理语音失败: %s", e)

        except Exception as e:
            logger.error(f"按句播报失败: {e}")
        finally:
            # ★ 兜底恢复麦克风：无论正常播完、提前 return 还是异常，
            #   都必须恢复监听，否则用户感知是"完全没反应"。
            #   延迟 1.5s 是为了让扬声器余音/房间混响先散掉。
            #
            # ⚠️ 代数守卫（根因修复）：一次交互会连续触发多次播报
            #    （ack → result → refine），每次播报都在这里起一个 1.5s 延迟
            #    恢复线程。若不加守卫，**先起的那个会在后续播报还在说话时
            #    把麦克风打开** → 拾到 TTS 回声 → 触发假录音 + 冷却期
            #    → 用户真实说话被挡掉，感知即"她听不见我说话了"。
            #    判据：只有仍是最新一次播报（_my_generation == 当前代数）
            #    且此刻确实没在播报时，才真正恢复。
            if _paused_mic and self._monitor_mic:
                try:
                    import threading as _th
                    import time as _t

                    def _resume_later():
                        _t.sleep(0.8)
                        # 期间又开了新一轮播报 → 交给那一轮负责恢复
                        if _my_generation != self._speak_generation:
                            logger.debug(
                                "[mic] 播报代数已更新(%d→%d)，本轮不恢复麦克风",
                                _my_generation, self._speak_generation)
                            return
                        # 仍在播报（理论上不会，防御性检查）→ 不恢复
                        if self._speaking:
                            logger.debug("[mic] 仍在播报中，跳过本轮恢复")
                            return
                        try:
                            self._monitor_mic.resume_listening()
                        except Exception as _e:
                            logger.warning("[mic] 恢复监听失败: %s", _e)
                    _th.Thread(target=_resume_later, daemon=True).start()
                except Exception as e:
                    logger.warning("[voice] 恢复麦克风失败: %s", e)

    def _voice_pipeline(self, wav_path: str):
        """语音交互链路：识别→流式对话→按句播报（监听回调版本）

        每个阶段（ASR/LLM/TTS）之间检查打断标志，被中断时立即退出，
        避免在已打断后仍执行耗时操作（如LLM请求/TTS合成）。
        """
        try:
            # ⚠️ 关键：进入新一次语音处理时**先清掉上一轮的打断标志**。
            # 该标志由点击宠物 / Ctrl+Alt+D 置 True，而唯一的清除点在播放循环
            # 内部（`_speak_sentences` 的每句播放前）。于是出现这个死局：
            #   点击一次宠物（当时没在播放）→ 标志永远停在 True
            #   → 之后每段语音都撞上 "ASR后检测到打断，跳过后续处理"
            #   → 用户看到的现象是"麦克风有反应，但永远不回应"。
            # 语义上，用户重新开口本身就意味着"新的对话开始"，旧打断不应继续生效。
            if self.app and hasattr(self.app, "_interrupt_requested"):
                self.app._interrupt_requested = False
            self.set_state("listen")
            # ⚡ ASR 期间可打断：记录当前识别对应的"代数"，
            #   打断回调会据此调用 ASR 的 request_cancel()，
            #   让还在烧 CPU 的识别立刻停下（不然用户点了打断还得等 3~5 秒）。
            _asr_t0 = time.perf_counter()
            text = self.app.transcribe(wav_path) if self.app else None
            _asr_ms = (time.perf_counter() - _asr_t0) * 1000.0
            logger.info("[voice] ASR 耗时 %.0fms", _asr_ms)
            # ASR后检查打断（用户可能在**本次识别期间**按了打断）
            if self.app and getattr(self.app, "_interrupt_requested", False):
                logger.info("[voice] ASR后检测到打断，跳过后续处理（ASR 耗时 %.0fms）", _asr_ms)
                return
            if not text or self._should_ignore(text):
                self._handle_invalid()
                return
            # 第1层：有效语音判定——过滤视频字幕/环境音，避免答非所问
            if not self._is_real_speech(text):
                self._handle_noise()
                return
            self.set_state("think")
            # 即时反馈：识别到的瞬间先让用户知道"听到了"（本地慢设备上消除"没反应"的空白感）
            self.show_bubble("🎧 听到了…", 1200)
            # 不显示"你说: xxx"气泡（省功耗，用户原话）
            # LLM前检查打断（用户可能在思考期间按了打断）
            if self.app and getattr(self.app, "_interrupt_requested", False):
                logger.info("[voice] LLM前检测到打断，跳过对话生成")
                return
            # ── Agent 分支：优先交给 Agent 层（意图命中则执行工具，否则回退 LLM）──
            if self._agent_enabled:
                from agent.message import new_request_id
                from core.kernel.events import EventTypes

                request_id = new_request_id()
                self._agent_busy = True
                self._agent_busy_since = time.time()
                logger.info("[agent] 语音交给 Agent 层处理 (rid=%s)", request_id)
                self.app.agent_stack.bus.emit(
                    EventTypes.SPEECH_RECOGNIZED,
                    text=text,
                    request_id=request_id,
                    source="voice",
                )
                # 立即返回：后续由 ack / result / chat 事件驱动
                # （_agent_busy 会在结果或闲聊回退时清除）
                return

            self._run_llm_reply(text)
        except Exception as e:
            logger.error(f"语音交互失败: {e}")
            self.show_bubble("哎呀，我这边出了点问题...", 2500)
            self.set_state("idle")
        finally:
            self._processing = False
            # 正常收尾 → 归零看门狗起点，避免下一轮被上一轮的旧时刻误判为"卡住"
            self._processing_since = 0.0
            self._delete_wav(wav_path)
            # ⚡ 兜底恢复麦克风：ASR 失败 / 空文本 / 噪音判定时，
            #   _start_pipeline 暂停了麦克风但没有任何播报路径来恢复它。
            #   播报路径（_speak_sentences）内部会再暂停一次，
            #   所以这里的恢复不会与播报冲突 —— 即使竞态也只是短暂闪开。
            if self._monitor_mic and not self._speaking:
                try:
                    import time as _t
                    def _safe_resume():
                        _t.sleep(0.3)  # 短暂延迟，让播报路径有机会先暂停
                        if not self._speaking and not self._processing:
                            self._monitor_mic.resume_listening()
                    threading.Thread(target=_safe_resume, daemon=True).start()
                except Exception:
                    pass
            # 处理完当前语音后，取待处理队列中的下一条继续（连说不再被丢）
            try:
                self._poll_pending()
            except Exception as e:
                logger.warning(f"取出待处理语音失败: {e}")

    def _run_llm_reply(self, text: str, request_id: str = "") -> None:
        """原有 LLM 链路：流式对话 → 情绪分析 → 按句播报

        既是 Agent 关闭时的主路径，也是 Agent 判定为"闲聊"时的回退路径。
        """
        try:
            if self.app and getattr(self.app, "_interrupt_requested", False):
                logger.info("[voice] LLM前检测到打断，跳过对话生成")
                return

            sentences = self.app.chat_stream(text, context=self._chat_history) if self.app else None
            if sentences:
                full_reply = "".join(sentences)
            else:
                full_reply = None
            self._chat_history.append({"role": "user", "content": text})
            if full_reply:
                self._chat_history.append({"role": "assistant", "content": full_reply})
            self._chat_history = self._chat_history[-20:]
            if not sentences:
                self._handle_invalid()
                return
            self._recent_invalid = 0

            # ── 语义情绪分析：匹配LLM回复内容到动效 ──
            from services.emotion_analyzer import dominant_emotion, analyze_per_sentence
            reply_emotion = dominant_emotion(sentences)
            self.state_machine.set_emotion(reply_emotion if reply_emotion != "talk" else "neutral")

            # 根据情绪选择动效状态（非默认 talk 的情况下，优先用情绪动效）
            if reply_emotion != "talk":
                anim_state = reply_emotion  # happy/sad/angry/surprise/love/dance/think/calm
            else:
                anim_state = "talk"

            self.set_state(anim_state)
            logger.info("[emotion] 回复情绪: %s → 动效: %s (文本: %.30s...)",
                        reply_emotion, anim_state, full_reply or "")

            # 逐句情绪缓存（用于播放时逐句切换动效）
            self._sentence_emotions = analyze_per_sentence(sentences)
            # 语义级静默：LLM 标记 [silent] 或短语气词附和 → 只气泡，不出声
            silent_reply = not self._should_speak(full_reply)
            if silent_reply:
                # 去掉标记仅保留正文，仍显示气泡，但不出声
                clean = full_reply.replace("[silent]", "", 1).strip()
                if clean:
                    self.show_bubble(clean, 2500)
            else:
                # 说话：字幕由播放线程逐句动态更新（_speak_sentences），
                # 这里不预设全文，避免"先闪全文再跳单句"。无声音时给个兜底提示。
                pass
            if self.app and not silent_reply:
                # TTS前检查打断（用户可能在LLM生成期间按了打断）
                if self.app and getattr(self.app, "_interrupt_requested", False):
                    logger.info("[voice] TTS前检测到打断，跳过语音播报")
                    return
                # 用户级静音开关：完全关闭声音输出（气泡仍展示）
                if self._voice_muted:
                    logger.info("[voice] 已静音，跳过语音输出")
                else:
                    self._speak_sentences(sentences)
                # 第2层：冷却从**播放结束**起算（本方法会阻塞到播完），
                # 再额外加一段"残响屏蔽窗口"（播放后 ~1s 内拾音视为回声）
                self._arm_echo_guard()
            self.set_state("idle")
        except Exception as e:
            logger.error(f"LLM 回复链路失败: {e}")
            self.show_bubble("哎呀，我这边出了点问题...", 2500)
            self.set_state("idle")

    def _handle_invalid(self):
        """无效响应：气泡提示没听清（带冷却，避免刷屏）"""
        self._recent_invalid += 1
        now = time.time()
        if now >= self._invalid_hint_until:
            self._invalid_hint_until = now + 4.0
            self.show_bubble("嗯？我没听清，再说一遍嘛~", 2500)
        if self._recent_invalid >= 3:
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

    # 明显的"非人话"特征：视频字幕 / 系统弹幕 / 纯平台音。保守过滤（不会误杀真实对话）。
    _NOISE_MARKERS = ("字幕", "点赞", "订阅", "转发", "打赏", "弹幕", "bilibili",
                      "www.", "http", "一键三连", "关注我", "欢迎来到", "by ", "by索")

    def _is_real_speech(self, text: str) -> bool:
        """判定识别文本是否为「真实的对人说话」，过滤视频字幕/平台环境音。

        保守策略：只在出现明显非对话标记时判为噪音；其余一律视为真实对话，
        避免误杀用户的正常发言。
        """
        if not text or not text.strip():
            return False
        t = text.strip().lower()
        # 明显环境音/字幕标记 → 非人话
        for m in self._NOISE_MARKERS:
            if m in t:
                return False
        # 纯无意义重复（如单个词被识别为乱码/无价值）暂不额外过滤，保守放行
        return True

    def _handle_noise(self):
        """对噪音（非人话）的静默处置：不弹气泡、不写历史、不触发LLM。"""
        logger.debug("判定为环境噪音，静默忽略")
        self.set_state("idle")

    # 纯语气词/极短附和的集合（这类话不需要开口发声，用气泡即可）
    _INTERJECTIONS = set("嗯 哦 嗯嗯 哦哦 哈哈 嘿嘿 好的 好 行 知道 知道了 没事 嗯呐 好吧 是 对".split())

    def _should_speak(self, text: str) -> bool:
        """判定这次回复是否需要开口发声（语义级静默）。

        返回 True=出声；False=只气泡，不出声。
        判定：带 [silent] 标记 → 不出声；
        回复很短且是纯语气词/附和 → 不出声（即使 LLM 没加标记，代码兜底）。
        其余实质内容 → 出声。
        """
        if not text or not text.strip():
            return False
        t = text.strip()
        # LLM 显式标记
        if t.startswith("[silent]"):
            return False
        # 代码兜底：去标点后极短且全是语气词/附和
        import re
        letters = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", t)
        if len(letters) <= 4:
            # 很短，且每个字都属于语气词集合或熟词 → 不出声
            if letters in self._INTERJECTIONS or all(ch in "嗯哦哈嘿嘿好行知没事对是呀啊吧呢嘛" for ch in letters):
                return False
        return True

    def mouseDoubleClickEvent(self, event):
        """双击 → 触发 love 动效（爱心飘出 + 开心表情）"""
        if self.app:
            new_state = self.state_machine.on_double_click()
            self.anim_controller.set_state(new_state)
            self._forward_vrm_state(new_state)
            self.show_bubble("❤️ 嘿嘿~", 2500)

    def contextMenuEvent(self, event):
        """右键菜单：互动 / 心情 / 显示 / 系统"""
        menu = self._build_context_menu()
        menu.exec(event.globalPos())

    def _show_vrm_menu(self, pos):
        """VRM 模式下 view 的右键：弹出与窗口相同的自定义菜单。
        pos 为 view 本地坐标，转换为屏幕坐标后 exec。"""
        view = self._vrm_view
        if view is None:
            return
        try:
            menu = self._build_context_menu()
            global_pos = view.mapToGlobal(pos)
            menu.exec(global_pos)
        except Exception as e:
            logger.warning(f"VRM 右键菜单失败: {e}")

    def _build_context_menu(self) -> QMenu:
        """构建右键菜单（拆出便于测试）"""
        from PySide6.QtWidgets import QMenu
        from PySide6.QtGui import QAction
        menu = QMenu(self)
        cm = self.app.config_manager if (self.app and getattr(self.app, "config_manager", None)) else None

        # ── 功能开关 ──
        # 字幕
        sub_enabled = bool(cm.get("ui.subtitle_enabled", True)) if cm else self._subtitle_enabled
        act_sub = QAction("💬 字幕", self)
        act_sub.setCheckable(True)
        act_sub.setChecked(sub_enabled)
        act_sub.toggled.connect(self._toggle_subtitle)
        menu.addAction(act_sub)
        # 声音开关（反向语义：勾选✓ = 有声音；取消 = 静音）
        # _voice_muted=True 表示静音，因此勾选态 = not _voice_muted
        act_mute = QAction("🔊 声音", self)
        act_mute.setCheckable(True)
        act_mute.blockSignals(True)
        act_mute.setChecked(not self._voice_muted)  # ✓=有声音
        act_mute.blockSignals(False)
        act_mute.toggled.connect(lambda on: self._set_mute_state(not on))  # toggled(勾选=有声) → 取消即静音
        logger.info(f"[声音菜单] 初始勾选={not self._voice_muted} (config.muted={cm.get('voice.muted', False) if cm else '?'})")
        menu.addAction(act_mute)
        # 置顶
        top = bool(cm.get("ui.always_on_top", True)) if cm else bool(self.windowFlags() & Qt.WindowStaysOnTopHint)
        act_top = QAction("📌 置顶", self)
        act_top.setCheckable(True)
        act_top.setChecked(top)
        act_top.toggled.connect(self._toggle_top)
        menu.addAction(act_top)
        # 麦克风
        act_mic = QAction("🎤 麦克风", self)
        act_mic.setCheckable(True)
        act_mic.setChecked(self._monitor_enabled)
        act_mic.toggled.connect(self._toggle_mic)
        menu.addAction(act_mic)

        # ── 系统 ──
        menu.addSeparator()
        menu.addAction("📝 设置", self.open_settings)

        # ── 重启 / 退出 ──
        menu.addSeparator()
        menu.addAction("🔄 重启", self._restart_app)
        menu.addAction("❌ 退出", self.close)

        return menu

    # ========== 菜单动作实现 ==========

    def _restart_app(self):
        """重启应用：关闭当前进程，重新启动 run.py"""
        import sys
        import os
        import subprocess
        logger.info("用户请求重启应用...")
        try:
            # 先清理
            if self.app:
                self.app.shutdown()
            # 启动新进程
            run_py = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "run.py")
            subprocess.Popen([sys.executable, run_py],
                             cwd=os.path.dirname(os.path.abspath(run_py)))
        except Exception as e:
            logger.error(f"重启失败: {e}")
        finally:
            # 强制退出当前进程
            os._exit(0)

    def _interact(self, action: str, text: str):
        """互动：VRM 模式播放动作 + 气泡反馈；sprite 模式仅气泡"""
        if self._vrm_bridge is not None:
            try:
                self._vrm_bridge.play_action(action)
            except Exception as e:
                logger.warning(f"互动动作失败: {e}")
        self.show_bubble(text, 2200)

    def _set_mood(self, mood: str):
        """设置心情状态"""
        mapping = {
            "happy": "happy", "sad": "sad", "angry": "sad",
            "bored": "think", "normal": "idle",
        }
        state = mapping.get(mood, "idle")
        self.set_state(state)
        bubbles = {
            "happy": "开心！好耶～", "sad": "呜……有点难过",
            "angry": "哼！不开心了", "bored": "好无聊呀……", "normal": "我没事，放心~",
        }
        self.show_bubble(bubbles.get(mood, ""), 2000)

    def _toggle_subtitle(self, on: bool):
        """字幕显示开关"""
        self._subtitle_enabled = bool(on)
        if self.app and getattr(self.app, "config_manager", None):
            try:
                self.app.config_manager.set("ui.subtitle_enabled", bool(on))
            except Exception as e:
                logger.warning(f"保存字幕设置失败: {e}")
        self.show_bubble("字幕已开启 💬" if on else "字幕已关闭", 1500)

    def _toggle_top(self, on: bool):
        """窗口置顶开关"""
        flags = self.windowFlags()
        if on:
            flags |= Qt.WindowStaysOnTopHint
        else:
            flags &= ~Qt.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.show()
        if self.app and getattr(self.app, "config_manager", None):
            try:
                self.app.config_manager.set("ui.always_on_top", bool(on))
            except Exception as e:
                logger.warning(f"保存置顶设置失败: {e}")
        self.show_bubble("已置顶 📌" if on else "已取消置顶", 1500)

    def _toggle_mic(self, on: bool):
        """麦克风开关（复用 hotkey 回调的逻辑）"""
        if on:
            ok = self.start_voice_monitor()
            if ok:
                self.show_bubble("麦克风已开启 🎤", 1500)
            else:
                try:
                    from services.microphone_service import get_microphone_service
                    mic_ok = get_microphone_service().is_available()
                except Exception:
                    mic_ok = False
                self.show_bubble("麦克风不可用 😢" if not mic_ok else "常驻监听未启动，请检查设置", 2000)
        else:
            self.stop_voice_monitor()
            self._monitor_enabled = False
            self.show_bubble("麦克风已关闭 🔇", 1500)

    def _toggle_autostart(self, on: bool):
        """开机自启开关"""
        self._apply_autostart(bool(on))
        if self.app and getattr(self.app, "config_manager", None):
            try:
                self.app.config_manager.set("system.autostart", bool(on))
            except Exception as e:
                logger.warning(f"保存自启设置失败: {e}")
        self.show_bubble("已开启开机自启 🚀" if on else "已关闭开机自启", 1500)

    def _set_mute_state(self, muted: bool):
        """统一设置静音状态：muted=True=静音(不发声)；muted=False=有声音。
        菜单/热键/设置都走这一个入口，保证内存、app、config 三层一致。
        """
        muted = bool(muted)
        self._voice_muted = muted
        logger.info(f"[声音] 设置 muted={muted} → 已静音={self._voice_muted}")
        # 静音时立即停止当前正在播放的语音（即时生效）
        if self.app and hasattr(self.app, "set_muted"):
            try:
                self.app.set_muted(muted)
            except Exception as e:
                logger.warning(f"立即停止音频失败: {e}")
        if self.app and getattr(self.app, "config_manager", None):
            try:
                self.app.config_manager.set("voice.muted", muted)
            except Exception as e:
                logger.warning(f"保存静音设置失败: {e}")
        self.show_bubble("已静音（不再说话）🔇" if muted else "已恢复声音 🔊", 1500)

    def toggle_voice_mute(self):
        """静音开关（供热键调用：在静音/有声间切换）"""
        self._set_mute_state(not self._voice_muted)

    def interrupt_current_speech(self):
        """主动打断：用户按热键/点击 → 立即停止宠物当前说话/思考/处理。

        这是外放环境下最可靠的打断方式（不依赖音频识别，零误判零自打断）。

        覆盖场景：
        1. 正在播放语音 → 停止播放 + 清空剩余句子队列
        2. 正在LLM思考/生成 → 重置处理状态（pipeline线程会自然结束）
        3. 正在ASR识别 → 重置处理状态
        4. 正在录音监听 → 不停止监听，但清空待处理队列
        """
        now = time.time()

        # 冷却保护：防止用户连续快速按打断（500ms内不重复触发）
        if now < self._interrupt_cooldown_until:
            logger.debug("[打断] 冷却中，忽略重复打断")
            return
        self._interrupt_cooldown_until = now + 0.5

        logger.info("[打断] 用户主动打断 (speaking=%s, processing=%s)", self._speaking, self._processing)

        # 1. 停止语音播放（最高优先级）
        was_speaking = self._speaking
        self._speaking = False
        if self.app and hasattr(self.app, "interrupt_speech"):
            try:
                self.app.interrupt_speech()
            except Exception as e:
                logger.warning(f"打断播放失败: {e}")

        # 2. 取消正在进行的 ASR 识别（⚡ 延迟优化关键点）
        #    场景：用户说完 → ASR 正在烧 CPU（本机 3~5 秒）→ 用户按打断。
        #    旧行为：打断只重置状态，ASR 仍跑完（用户白等几秒），
        #            跑完后才撞上打断检查被丢弃 —— 感知就是"点了没反应"。
        #    新行为：立刻通知 ASR 中止解码，它会在片段迭代之间退出。
        self._cancel_active_asr()

        # 3. 清空待处理语音队列（避免排队的旧语音被处理）
        queue_len = len(self._pending_wavs)
        if queue_len > 0:
            for w in self._pending_wavs:
                self._delete_wav(w)
            self._pending_wavs.clear()
            logger.info(f"[打断] 已清空 {queue_len} 条待处理语音")

        # 4. 重置处理状态（让pipeline线程自然结束，不阻塞新语音输入）
        if self._processing:
            logger.info("[打断] 重置处理状态，pipeline线程将自然结束")
            self._processing = False
        self._processing_since = 0.0

        # 5. 重置冷却计时器（立即可接受新语音）
        self._echo_cooldown_until = 0.0
        self._echo_tail_until = 0.0

        # 6. 显示打断反馈（保护期内不被tick清除）
        if was_speaking:
            self.show_bubble("🛑 已停止，请说~", 2500)
        else:
            self.show_bubble("🎤 好的，请说~", 2000)
        self._interrupt_active_until = time.time() + 2.0

        # 7. 触发动效反馈（被打断 → 惊讶/生气）
        interrupt_state = self.state_machine.on_interrupt()
        self.anim_controller.set_state(interrupt_state)
        self._forward_vrm_state(interrupt_state)
        logger.info("[打断] 触发动效: %s", interrupt_state)

    def _cancel_active_asr(self) -> None:
        """请求中止正在进行的 ASR 识别（尽力而为，不抛异常）。

        为什么单独抽一个方法：ASR 插件的获取方式有两处可能（app.get_plugin），
        且打断路径**绝不能因为拿不到插件就中断** —— 打断本身必须永远成功。
        """
        if not self.app or not hasattr(self.app, "get_plugin"):
            return
        try:
            asr = self.app.get_plugin("ASREngine")
            if asr is None:
                return
            cancel = getattr(asr, "request_cancel", None)
            if callable(cancel):
                cancel()
                logger.info("[打断] 已请求中止 ASR 识别")
        except Exception as e:
            # 打断路径不因取插件失败而中断：记 warning 即可
            logger.warning("[打断] 请求中止 ASR 失败: %s", e)

    def _show_status(self):
        """显示当前状态信息"""
        if not self.app:
            return
        status = self.app.get_status()
        hw = status.get("hardware", {})
        plugins = status.get("plugins", {})
        lines = [
            f"🐱 {self.windowTitle()} v2.0",
            "",
            f"CPU: {hw.get('cpu_count', '?')}核  内存: {hw.get('memory_gb', '?'):.1f}GB",
            f"GPU: {'有' if hw.get('has_gpu') else '无'}" + (f" ({hw.get('gpu_name','')})" if hw.get('gpu_name') else ""),
            f"模式: {status.get('performance_mode', '?')}",
            "",
            "插件状态:",
        ]
        labels = {
            "asr": "🎙️ ASR", "tts": "🔊 TTS", "llm": "🧠 LLM",
            "voiceprint": "🔐 声纹", "file_monitor": "📁 文件监控", "avatar": "🖼️ 形象",
        }
        for key, label in labels.items():
            avail = plugins.get(key, {}).get("available", False)
            icon = "✅" if avail else "❌"
            lines.append(f"  {icon} {label}")
        QMessageBox.information(self, "小忆 · 状态信息", "\n".join(lines))

    # ========== 设置集成 ==========

    def open_settings(self):
        """打开设置对话框（非模态）"""
        from ui.settings_dialog import SettingsDialog
        cm = self.app.config_manager if (self.app and getattr(self.app, "config_manager", None)) else None
        if cm is None:
            from core.config_manager import get_config_manager
            cm = get_config_manager()
        self._settings_dlg = SettingsDialog(cm, parent=self)
        self._settings_dlg.accepted.connect(self.apply_settings)
        self._settings_dlg.show()

    def apply_settings(self):
        """设置保存后热更新"""
        try:
            cm = self.app.config_manager if (self.app and getattr(self.app, "config_manager", None)) else None
            if not cm:
                logger.warning("无配置管理器，跳过热更新")
                return
            # 1. 监听开关：运行中→按新配置重启监听（start内部读取参数）
            listening = bool(cm.get("voice.listening", True))
            self.stop_voice_monitor()
            self._monitor_enabled = False
            if listening:
                self.start_voice_monitor()
            # 2. 置顶
            top = bool(cm.get("ui.always_on_top", True))
            window_flags = self.windowFlags()
            if top:
                window_flags |= Qt.WindowStaysOnTopHint
            else:
                window_flags &= ~Qt.WindowStaysOnTopHint
            # setWindowFlags 会重建窗口句柄，位置会丢失 → 先保存后恢复
            saved_pos = self.pos()
            self.setWindowFlags(window_flags)
            self.move(saved_pos)
            self.show()
            # 3. 热键重注册
            self.rebind_hotkey()
            # 4. 开机自启
            self._apply_autostart(bool(cm.get("system.autostart", False)))
            # 5. 宠物大小
            try:
                size = int(cm.get("ui.pet_size", 300))
            except (TypeError, ValueError):
                size = 300
            if 100 <= size <= 400 and self._pet_size != size:
                self._pet_size = size
                # 按**精灵自身的宽高比**算高，不能写死正方形。
                # 非方形画布（全身立绘 128×289）若被塞进正方形窗口：
                #   窗口高 227 < 画布高 289 ⇒ 角色从 y=0 画起、**脚被裁掉**，
                #   而 44px 字幕条又落在 y[186..221] ⇒ 重新压回角色身上
                #   （就是 `_relayout_pet` 修掉的那个现象，从另一条路径复发）。
                # 改配置 `ui.pet_size` 调的是**画布宽**，高跟着比例走。
                cw, ch = self.anim_controller.get_size()
                ratio = (ch / cw) if cw else 1.0
                self.setFixedSize(size + 100,
                                  max(1, int(round(size * ratio))) + 100)
            # 6. 宠物名字（热更新：窗口标题 + LLM 人设）
            try:
                pet_name = str(cm.get("app.name", "欣雅") or "欣雅").strip() or "欣雅"
                self.setWindowTitle(pet_name)
                if self.app and hasattr(self.app, "get_plugin"):
                    llm = self.app.get_plugin("LLMEngine")
                    if llm is not None and hasattr(llm, "set_pet_name"):
                        llm.set_pet_name(pet_name)
            except Exception as e:
                logger.warning(f"更新宠物名字失败: {e}")
            # 7. 脚下字幕样式（开关 + 颜色热更新）
            try:
                self.update_subtitle_style(
                    bool(cm.get("ui.subtitle_enabled", True)),
                    str(cm.get("ui.subtitle_bg_color", "#000000")),
                    str(cm.get("ui.subtitle_fg_color", "#FFFFFF")),
                )
            except Exception as e:
                logger.warning(f"更新字幕样式失败: {e}")
            # 8. 帧率（性能档位）
            try:
                self._apply_fps_from_config()
            except Exception as e:
                logger.warning(f"更新帧率失败: {e}")
            self.show_bubble("设置已保存，部分项重启后生效", 2000)
        except Exception as e:
            logger.error(f"应用设置失败: {e}")

    def rebind_hotkey(self):
        """按config重注册热键（设置保存后调用）"""
        cm = self.app.config_manager if (self.app and getattr(self.app, "config_manager", None)) else None
        if not cm:
            return
        from core.app import get_app
        app = get_app() if not self.app else self.app
        hk = getattr(app, "hotkey_manager", None)
        if not hk:
            return
        try:
            hk.unregister()
            if not cm.get("voice.hotkey_enabled", True):
                return
            # 主热键：麦克风开关
            main_combo = cm.get("voice.hotkey_toggle", "Ctrl+Alt+M")
            if not hk.register(main_combo):
                self.show_bubble(f"热键 {main_combo} 被占用，请更换", 3000)
            # ★关键：重新绑定主热键回调（register只注册不绑回调，必须显式调用）
            hk.set_on_hotkey(self.toggle_voice_monitor)
            # 额外：静音开关
            mute_combo = cm.get("voice.hotkey_mute", "Ctrl+Alt+S")
            if hasattr(self, "toggle_voice_mute"):
                if not hk.register_extra(mute_combo, self.toggle_voice_mute):
                    logger.warning(f"静音热键 {mute_combo} 注册失败")
            # 额外：打断说话
            intr_combo = cm.get("voice.hotkey_interrupt", "Ctrl+Alt+D")
            if hasattr(self, "interrupt_current_speech"):
                if not hk.register_extra(intr_combo, self.interrupt_current_speech):
                    logger.warning(f"打断热键 {intr_combo} 注册失败")
            # 额外：轮换麦克风设备（多声源场景快速试哪个能收到声音）
            mic_combo = cm.get("voice.hotkey_mic_next", "Ctrl+Alt+N")
            if hasattr(self, "cycle_microphone_device"):
                if not hk.register_extra(mic_combo, self.cycle_microphone_device):
                    logger.warning(f"切换麦克风热键 {mic_combo} 注册失败")
        except Exception as e:
            logger.warning(f"重绑热键失败: {e}")

    def _is_monitor_active(self) -> bool:
        """当前是否处于监听中（_monitor_enabled为准，mic状态兜底）"""
        if self._monitor_enabled:
            return True
        mic = self._monitor_mic
        if mic is not None and hasattr(mic, "is_listening"):
            try:
                return bool(mic.is_listening())
            except Exception:
                pass
        return False

    def toggle_voice_monitor(self):
        """热键回调：切换监听"""
        if self._is_monitor_active():
            self.stop_voice_monitor()
            self._monitor_enabled = False
            self.show_bubble("麦克风已关闭 🔇", 1500)
        else:
            ok = self.start_voice_monitor()
            if ok:
                self.show_bubble("麦克风已开启 🎤", 1500)
            else:
                try:
                    from services.microphone_service import get_microphone_service
                    mic_ok = get_microphone_service().is_available()
                except Exception:
                    mic_ok = False
                self.show_bubble("麦克风不可用 😢" if not mic_ok else "常驻监听未启动，请检查设置", 2000)

    @staticmethod
    def _apply_autostart(enabled: bool):
        """开机自启注册表写入/删除"""
        import winreg
        key = r"Software\Microsoft\Windows\CurrentVersion\Run"
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_SET_VALUE) as k:
                if enabled:
                    import sys, os
                    exe = sys.executable if getattr(sys, "frozen", False) else f'"{sys.executable}" "{os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "run.py"))}"'
                    winreg.SetValueEx(k, "xbyaPet", 0, winreg.REG_SZ, exe)
                else:
                    try:
                        winreg.DeleteValue(k, "xbyaPet")
                    except FileNotFoundError:
                        pass
        except Exception as e:
            logger.error(f"设置开机自启失败: {e}")
