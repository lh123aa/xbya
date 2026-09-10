"""
小忆桌面智能管家 - 应用主控模块 v2.0
协调所有核心组件：配置、插件、事件、语音、文件监控、AI
"""

import logging
import sys
import os
import time
from typing import Optional, Dict, Any, List
from pathlib import Path

# 设置项目根目录到path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.config_manager import get_config_manager, ConfigManager
from core.plugin_loader import get_plugin_loader, PluginLoader
from core.hardware_detector import get_hardware_detector, HardwareDetector
from core.event_bus import get_event_bus, EventBus, EventType
from core.temp_manager import get_tmp_dir, TmpCleaner
from services.hotkey_manager import HotkeyManager

logger = logging.getLogger(__name__)

# 全局单实例锁（QSharedMemory）：防止重复启动导致内存翻倍/热键冲突
_shared_memory = None


def acquire_single_instance_lock() -> bool:
    """
    获取单实例锁。
    使用 QSharedMemory 跨进程互斥：第二个实例创建同名共享内存段会失败，
    从而判定"已有实例在运行"。

    Returns:
        True=成功获取（本实例是唯一）；False=已有实例（应退出）。
    """
    global _shared_memory
    try:
        from PySide6.QtCore import QSharedMemory
        _shared_memory = QSharedMemory("xinya_desktop_pet_single_instance_v1")
        if not _shared_memory.create(1):
            # 创建失败说明已有实例共享同名段
            _shared_memory = None
            return False
        return True
    except Exception as e:
        logger.warning(f"单实例锁获取失败: {e}")
        # 锁获取失败不阻塞运行（降级为无锁模式）
        _shared_memory = None
        return True


def release_single_instance_lock():
    """释放单实例锁"""
    global _shared_memory
    if _shared_memory is not None:
        try:
            _shared_memory.detach()
            _shared_memory = None
        except Exception:
            pass


class XiaoyiApp:
    """小忆应用主控类"""
    
    def __init__(self, config_path: str = "config.yaml"):
        """
        初始化应用
        
        Args:
            config_path: 配置文件路径
        """
        self.config_path = Path(config_path)
        self._running = False
        self._audio_muted = False   # 用户级静音：置真时立即停掉正在播放的语音
        self._interrupt_requested = False  # 打断标志：说话中被用户语音打断时停止当前播放
        
        # 核心组件
        self.config_manager: Optional[ConfigManager] = None
        self.plugin_loader: Optional[PluginLoader] = None
        self.hardware_detector: Optional[HardwareDetector] = None
        self.event_bus: Optional[EventBus] = None
        
        # 全局热键管理器（initialize时创建）
        self.hotkey_manager = None
        
        # Agent 执行层（initialize时按配置装配）
        self.agent_stack = None
        # Agent 层专用的**内核**事件总线（与 self.event_bus 不是同一个类，见
        # _setup_agent_layer 的说明）；PetWindow 通过 stack.bus 收发 Agent 事件
        self.agent_bus = None
        
        # 插件实例（通过接口访问）
        self.plugins: Dict[str, Any] = {}
        
        logger.info("小忆应用初始化中...")
    
    def initialize(self) -> bool:
        """
        初始化所有组件
        
        Returns:
            是否初始化成功
        """
        try:
            # 1. 初始化配置管理器
            logger.info("1/5 初始化配置管理器...")
            self.config_manager = get_config_manager(str(self.config_path))
            
            # 2. 检测硬件
            logger.info("2/5 检测硬件配置...")
            self.hardware_detector = get_hardware_detector()
            hardware_info = self.hardware_detector.detect()
            
            # 首次启动性能评估：未评估过则按硬件推荐自动应用性能档与模型。
            # 之后保留用户手动选择的配置（用户可在设置里改）。
            evaluated = self.config_manager.get("system.perf_evaluated", False)
            current_mode = self.config_manager.get_performance_mode()
            if not evaluated or current_mode == "auto":
                rec = hardware_info.recommended_mode
                logger.info(f"首次性能评估: 硬件推荐={rec}")
                # 显式设为推荐档并应用对应模型/设备；不覆盖用户已自定义的 llm 引擎/模型
                self.config_manager.set_performance_mode(rec)
                self.config_manager.set("system.perf_evaluated", True)
                current_mode = rec
            
            # 3. 初始化插件加载器
            logger.info("3/5 初始化插件系统...")
            self.plugin_loader = get_plugin_loader()
            plugins = self.plugin_loader.scan()
            logger.info(f"   发现 {len(plugins)} 个插件")
            
            # 4. 加载插件
            logger.info("4/5 加载插件...")
            self._load_plugins()
            
            # 5. 初始化事件总线
            logger.info("5/5 初始化事件总线...")
            self.event_bus = get_event_bus()
            
            # 6. 热键管理器
            self.hotkey_manager = HotkeyManager()

            # 7. Agent 执行层（配置驱动装配；失败不阻塞启动，降级为纯对话）
            logger.info("7/7 装配 Agent 执行层...")
            self._setup_agent_layer()

            self._running = True
            logger.info("=" * 40)
            logger.info("小忆应用初始化完成！")
            logger.info("=" * 40)

            # 宠物名字注入（LLM 人设以 config app.name 为准，默认欣雅）
            pet_name = self.config_manager.get("app.name", "欣雅")
            llm = self.get_plugin("LLMEngine")
            if llm is not None and hasattr(llm, "set_pet_name"):
                llm.set_pet_name(pet_name)

            # 发布应用启动事件
            self.event_bus.emit(EventType.APP_START)
            
            return True
            
        except Exception as e:
            logger.error(f"应用初始化失败: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    def _load_plugins(self) -> None:
        """加载所有必要的插件"""
        plugin_types = ["asr", "tts", "llm", "embedding", "vector_db", 
                       "voiceprint", "file_monitor", "avatar"]
        
        for plugin_type in plugin_types:
            engine_name = self.config_manager.get(f"plugins.{plugin_type}.engine")
            
            if engine_name and engine_name != "null":
                # 获取插件配置参数
                # 云端 LLM（openai_api）：使用 plugins.llm.cloud 参数（base_url/api_key/model）
                plugin_params = self.config_manager.get(f"plugins.{plugin_type}.params") or {}
                if plugin_type == "llm" and engine_name == "openai_api":
                    cloud_params = self.config_manager.get(f"plugins.{plugin_type}.cloud") or {}
                    plugin_params = {**plugin_params, **cloud_params}
                logger.info(f"   加载 {plugin_type} -> {engine_name}")
                plugin = self.plugin_loader.load(engine_name, params=plugin_params)
                if plugin:
                    self.plugins[plugin_type] = plugin
                    # 用接口方式也存一份
                    interface_name = self._get_interface_name(plugin_type)
                    self.plugins[interface_name] = plugin
                else:
                    logger.warning(f"   {plugin_type} 加载失败，使用空对象")
                    self._load_null_plugin(plugin_type)
            else:
                logger.info(f"   {plugin_type} 已禁用，使用空对象")
                self._load_null_plugin(plugin_type)
    
    def _setup_agent_layer(self) -> None:
        """装配 Agent 执行层（配置驱动）

        失败不阻塞启动：捕获异常并降级为"纯对话模式"
        （PetWindow 检测到 agent_stack 为 None 时走原 LLM 链路）。

        ⚠️ **Agent 层用独立的内核事件总线，不能复用 `self.event_bus`**。
        项目里有两个不兼容的 EventBus：

        | 类 | 订阅 | 发送 | 事件类型 |
        |----|------|------|---------|
        | `core.event_bus.EventBus` | `subscribe(EventType, h)` | `emit(EventType, data: dict)` | 枚举 |
        | `core.kernel.events.EventBus` | `on(str, h)` → disposer | `emit(str, **kwargs)` | 字符串 |

        Agent 层（以及 `PetWindow` 的 Agent 接线）用的是**后者**。
        原先这里传的是 `self.event_bus`（前者），于是 `pipeline.start()` 里
        `bus.on(...)` 直接 `AttributeError`，被本方法的 except 吞掉 →
        **Agent 层在整个应用里一直是死的**（静默降级为纯对话），
        而脚本级验收各自 new 内核总线，全都测不出来。
        """
        try:
            from agent.bootstrap import AgentConfig, build_agent_stack
            from core.kernel.events import EventBus as AgentEventBus

            config = AgentConfig.from_config_manager(self.config_manager)

            # Agent 层自带一条内核总线：与项目原有总线的事件词表不同，
            # 不桥接（PetWindow 通过 stack.bus 收发，Agent 层对外是自洽的）。
            self.agent_bus = AgentEventBus()

            self.agent_stack = build_agent_stack(
                config=config,
                bus=self.agent_bus,
                synthesize=self.synthesize,        # 供确认语预热
                llm_once=self.chat_once,           # 供结果润色
                llm_route_call=self.route_with_tools,  # 供意图兜底
            )
            self.agent_stack.start()

            stats = self.agent_stack.stats()
            logger.info(
                "   Agent 层就绪: enabled=%s, tools=%d, router=%s, 白名单=%s",
                stats["enabled"], stats["tools"], stats["router"],
                stats["safety_whitelist"],
            )
        except Exception as e:
            logger.error("Agent 层装配失败，降级为纯对话模式: %s", e, exc_info=True)
            self.agent_stack = None
            self.agent_bus = None

    def _teardown_agent_layer(self) -> None:
        """释放 Agent 执行层"""
        if self.agent_stack is None:
            return
        try:
            self.agent_stack.dispose()
        except Exception as e:
            logger.warning("Agent 层释放失败: %s", e)
        finally:
            self.agent_stack = None
            self.agent_bus = None

    def _load_null_plugin(self, plugin_type: str) -> None:
        """加载空对象插件"""
        interface_name = self._get_interface_name(plugin_type)
        null_plugin = self.plugin_loader.load_by_interface(interface_name)
        if null_plugin:
            self.plugins[plugin_type] = null_plugin
            self.plugins[interface_name] = null_plugin
    
    def _get_interface_name(self, plugin_type: str) -> str:
        """获取插件类型对应的接口名称"""
        interface_map = {
            "asr": "ASREngine",
            "tts": "TTSEngine",
            "llm": "LLMEngine",
            "embedding": "EmbeddingEngine",
            "vector_db": "VectorDBEngine",
            "voiceprint": "VoiceprintEngine",
            "file_monitor": "FileMonitorEngine",
            "avatar": "AvatarEngine"
        }
        return interface_map.get(plugin_type, "")
    
    def get_plugin(self, plugin_type: str) -> Any:
        """
        获取插件实例
        
        Args:
            plugin_type: 插件类型（如 "tts", "llm"）或接口名（如 "TTSEngine"）
            
        Returns:
            插件实例
        """
        return self.plugins.get(plugin_type)
    
    # ========== 语音相关 ==========
    
    def speak(self, text: str) -> bool:
        """
        语音播报
        
        Args:
            text: 要播报的文本
            
        Returns:
            是否成功
        """
        tts = self.get_plugin("TTSEngine")
        if tts and tts.is_available():
            try:
                audio_data = tts.speak(text)
                if audio_data:
                    return self._play_audio(audio_data)
            except Exception as e:
                logger.error(f"语音播报失败: {e}")
        return False
    
    def synthesize(self, text: str) -> Optional[bytes]:
        """
        仅合成语音（不播放），供流式播报流水线使用
        
        Args:
            text: 要合成的文本
            
        Returns:
            音频字节（MP3），失败返回None
        """
        tts = self.get_plugin("TTSEngine")
        if tts and tts.is_available():
            try:
                return tts.speak(text)
            except RuntimeError as e:
                # 解释器关闭中/线程池已停 → 静默降级（后台线程可能在退出阶段
                # 仍在收尾，此处不应产生错误日志噪音）
                if sys.is_finalizing() or "interpreter shutdown" in str(e):
                    logger.debug("语音合成跳过（运行时关闭中）: %s", e)
                    return None
                logger.error(f"语音合成失败: {e}")
                return None
            except Exception as e:
                logger.error(f"语音合成失败: {e}")
        return None
    
    def play_audio(self, audio_data: bytes) -> bool:
        """
        播放音频数据（合成与播放分离，便于流式队列）
        
        Args:
            audio_data: 音频字节
            
        Returns:
            是否播放成功
        """
        return self._play_audio(audio_data)
    
    def chat_stream(self, prompt: str, context: list = None) -> Optional[list]:
        """
        流式对话（返回句子列表，供按句播报）
        
        Args:
            prompt: 用户输入
            context: 对话上下文
            
        Returns:
            句子列表；失败返回None
        """
        llm = self.get_plugin("LLMEngine")
        if llm and llm.is_available():
            try:
                if hasattr(llm, "chat_stream"):
                    return llm.chat_stream(prompt, context)
                # 回退：非流式 → 全量文本分句
                from core.text_utils import split_sentences
                text = llm.chat(prompt, context)
                if not text:
                    return None
                return split_sentences(text)
            except Exception as e:
                logger.error(f"流式对话失败: {e}")
                return None
        return None
    
    def chat_once(self, prompt: str, context: list = None) -> Optional[str]:
        """
        单次 LLM 补全（返回完整文本，不分句）

        供 Agent 层做结果润色等需要"一整段话"的场景使用。

        Args:
            prompt: 提示词
            context: 对话上下文

        Returns:
            完整回复文本；失败返回 None
        """
        try:
            sentences = self.chat_stream(prompt, context)
        except Exception as e:
            logger.warning("LLM 单次补全失败: %s", e)
            return None
        if not sentences:
            return None
        return "".join(sentences)

    def route_with_tools(self, system: str, user: str, tools: list) -> Optional[dict]:
        """
        用 function calling 做意图判定（供 Agent 路由兜底）

        当前 LLM 插件不支持 tools 时返回 None → 路由自动降级为纯规则。

        Args:
            system: 系统提示
            user: 用户输入
            tools: 工具 schema 列表

        Returns:
            {"name": ..., "arguments": {...}}；不可用时返回 None
        """
        llm = self.get_plugin("LLMEngine")
        if llm is None or not hasattr(llm, "chat_with_tools"):
            return None
        try:
            return llm.chat_with_tools(system, user, tools)
        except Exception as e:
            logger.warning("LLM 路由调用失败: %s", e)
            return None

    def _play_audio(self, audio_data: bytes) -> bool:
        """播放音频数据（带音频设备自愈：设备被关/变更时自动重开重试）"""
        import pygame
        import tempfile
        import os

        # 生成临时文件
        with tempfile.NamedTemporaryFile(suffix='.mp3', delete=False, dir=get_tmp_dir()) as f:
            f.write(audio_data)
            temp_path = f.name

        for attempt in range(2):  # 最多重试一次（自愈设备失效）
            try:
                if not pygame.mixer.get_init():
                    pygame.mixer.init()
                pygame.mixer.music.load(temp_path)
                pygame.mixer.music.play()
                # 等待播放完成（退出/静音/被打断时立即停止）
                while pygame.mixer.music.get_busy():
                    if not self._running or self._audio_muted or self._interrupt_requested:
                        pygame.mixer.music.stop()
                        break
                    pygame.time.wait(50)
                break
            except Exception as e:
                # 设备未开/句柄失效 → 强制重开再试一次
                if attempt == 0:
                    logger.warning(f"音频播放失败，重开设备重试: {e}")
                    try:
                        pygame.mixer.quit()
                    except Exception:
                        pass
                    continue
                logger.error(f"音频播放失败: {e}")
                break
            finally:
                try:
                    os.unlink(temp_path)
                except Exception:
                    pass
        return True

    def interrupt_speech(self, request_id: str = "") -> None:
        """打断当前语音：置打断标志、停止播放，并**通知 Agent 层**

        供"用户说话打断宠物"机制调用（Ctrl+Alt+D 与检测到用户抢话时）。

        ⚠️ **只置标志 + 停 pygame 是不够的**：Agent 层那边
        （`agent/pipeline.py::_on_interrupt`）有一整套打断语义 ——
        取消该请求下的全部任务、作废在途的 LLM 润色补播、取消挂起中的计划 ——
        但 `EventTypes.SPEECH_INTERRUPTED` 在过去**全仓库没有任何发射点**，
        于是"按 Ctrl+Alt+D 打断"实际只停了声音，Agent 层该做的事一件都没做：
        用户以为打断了，后台的多步计划还在往下跑。

        Args:
            request_id: 要打断的请求；留空表示"全部停下"（全局打断）
        """
        self._interrupt_requested = True
        try:
            import pygame
            if pygame.mixer.get_init():
                pygame.mixer.music.stop()
                pygame.mixer.stop()
        except Exception:
            pass

        self.notify_agent_interrupt(request_id)

    def notify_agent_interrupt(self, request_id: str = "") -> None:
        """把"被打断"发到 Agent 层的事件总线

        Agent 层用的是**独立的内核总线**（见 `_setup_agent_layer` 的说明），
        所以不能用 `self.event_bus`，必须发到 `self.agent_bus`。
        没装配 Agent 层（纯对话模式）时安静跳过 —— 打断降到原来的语义。
        """
        bus = getattr(self, "agent_bus", None)
        if bus is None:
            return
        try:
            from core.kernel.events import EventTypes
            bus.emit(EventTypes.SPEECH_INTERRUPTED, request_id=request_id)
        except Exception as e:
            logger.warning("[打断] Agent 层打断事件发送失败: %s", e)

    def set_muted(self, muted: bool) -> None:
        """用户级静音开关：置真时立即停止当前正在播放的语音"""
        self._audio_muted = bool(muted)
        if muted:
            try:
                import pygame
                if pygame.mixer.get_init():
                    pygame.mixer.music.stop()
                    pygame.mixer.stop()
            except Exception:
                pass
    
    def transcribe(self, audio_path: str) -> Optional[str]:
        """
        语音识别
        
        Args:
            audio_path: 音频文件路径
            
        Returns:
            识别出的文本
        """
        asr = self.get_plugin("ASREngine")
        if asr and asr.is_available():
            return asr.transcribe(audio_path)
        return None
    
    def chat(self, prompt: str, context: list = None) -> Optional[str]:
        """
        AI对话
        
        Args:
            prompt: 用户输入
            context: 对话上下文
            
        Returns:
            AI回复
        """
        llm = self.get_plugin("LLMEngine")
        if llm and llm.is_available():
            return llm.chat(prompt, context)
        return "抱歉，AI功能暂时不可用。请安装Ollama并启动服务。"
    
    # ========== 文件监控相关 ==========
    
    def start_file_monitor(self, watch_dirs: List[str] = None) -> bool:
        """
        启动文件监控
        
        Args:
            watch_dirs: 监控目录列表
            
        Returns:
            是否成功
        """
        if watch_dirs is None:
            watch_dirs = self.config_manager.get("plugins.file_monitor.params.watch_dirs", 
                                                  ["~/Desktop", "~/Downloads"])
        
        file_monitor = self.get_plugin("FileMonitorEngine")
        if file_monitor and file_monitor.is_available():
            def on_file_change(event_type: str, file_path: str):
                if self.event_bus:
                    self.event_bus.emit(EventType.FILE_DETECTED, {
                        "event_type": event_type,
                        "file_path": file_path
                    })
            
            return file_monitor.start(watch_dirs, on_file_change)
        return False
    
    def stop_file_monitor(self) -> bool:
        """停止文件监控"""
        file_monitor = self.get_plugin("FileMonitorEngine")
        if file_monitor:
            return file_monitor.stop()
        return True
    
    # ========== 应用控制 ==========
    
    def run(self) -> None:
        """运行应用主循环"""
        # 单实例锁：第二个实例直接退出，防内存翻倍/热键冲突
        if not acquire_single_instance_lock():
            logger.error("已有一个『欣雅』实例在运行，本次启动取消。")
            print("[提示] 欣雅已在运行，请勿重复启动。")
            return

        if not self._running:
            if not self.initialize():
                release_single_instance_lock()
                return
        
        logger.info("小忆应用启动...")
        
        pet_window = None
        
        try:
            from PySide6.QtWidgets import QApplication
            from ui.pet_window import PetWindow
            
            app = QApplication.instance() or QApplication(sys.argv)
            
            # 创建宠物窗口
            pet_window = PetWindow()
            self.pet_window = pet_window  # 供设置/其他模块引用（如快捷键重绑）
            pet_window.set_app(self)
            pet_window.load_pet("cat")
            
            # 启用 VRM 渲染（必须先于 show()！show 后添加原生子窗口 DWM 合成异常）
            if hasattr(pet_window, "enable_vrm"):
                vrm_model = self.config_manager.get("ui.vrm_model", "assets/vrm/cat.vrm")
                ok = pet_window.enable_vrm(vrm_model)
                logger.info(f"VRM渲染: {'已启用' if ok else '未启用(降级精灵图)'}")
            
            pet_window.show()
            
            # 启动常驻语音监听
            if hasattr(pet_window, "start_voice_monitor"):
                pet_window.start_voice_monitor()
            
            # 注册全局热键
            if hasattr(pet_window, "toggle_voice_monitor"):
                self.setup_hotkey(pet_window)
            
            # 打印启动信息
            self._print_banner()

            # 后台临时文件清理器（每 10 分钟自动回收 wav/mp3 等产物；与对话上下文无关）
            try:
                self._tmp_cleaner = TmpCleaner()
                self._tmp_cleaner.start()
                logger.info("临时文件清理器已启动（周期 10 分钟，后台自动）")
            except Exception as e:
                logger.warning("临时文件清理器启动失败: %s", e)

            # 运行窗口（阻塞）
            app.exec()
            
        except KeyboardInterrupt:
            logger.info("收到退出信号")
        except Exception as e:
            logger.error(f"运行异常: {e}")
            import traceback
            traceback.print_exc()
        finally:
            try:
                if self._tmp_cleaner:
                    self._tmp_cleaner.stop()
            except Exception:
                pass
            try:
                if pet_window and hasattr(pet_window, "stop_voice_monitor"):
                    pet_window.stop_voice_monitor()
            except Exception:
                pass
            try:
                self.teardown_hotkey()
            except Exception:
                pass
            self.shutdown()
            release_single_instance_lock()
    
    # ========== 热键管理 ==========
    
    def setup_hotkey(self, pet_window) -> None:
        """启动时注册热键并绑定回调"""
        if not self.hotkey_manager:
            return
        enabled = self.config_manager.get("voice.hotkey_enabled", True)
        if enabled:
            ok = self.hotkey_manager.register(self.config_manager.get("voice.hotkey_toggle", "Ctrl+Alt+M"))
            if not ok:
                logger.warning("热键注册失败（可能被占用）")
                if pet_window and hasattr(pet_window, "show_bubble"):
                    pet_window.show_bubble("热键已被占用，请在设置中更换", 3000)
        self.hotkey_manager.set_on_hotkey(pet_window.toggle_voice_monitor)
        # 额外热键：静音开关（读配置，默认 Ctrl+Alt+S）
        try:
            if hasattr(pet_window, "toggle_voice_mute"):
                self.hotkey_manager.register_extra(
                    self.config_manager.get("voice.hotkey_mute", "Ctrl+Alt+S"),
                    pet_window.toggle_voice_mute)
        except Exception:
            pass
        # 额外热键：打断当前语音（读配置，默认 Ctrl+Alt+D）
        try:
            if hasattr(pet_window, "interrupt_current_speech"):
                ok = self.hotkey_manager.register_extra(
                    self.config_manager.get("voice.hotkey_interrupt", "Ctrl+Alt+D"),
                    pet_window.interrupt_current_speech)
                if not ok:
                    logger.warning("打断热键注册失败")
        except Exception as e:
            logger.warning(f"注册打断热键失败: {e}")
        # 安装到QApplication
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
        if app:
            app.installNativeEventFilter(self.hotkey_manager)
        # 验证热键状态
        logger.info(f"[热键] 初始化完成: 已注册={self.hotkey_manager.is_registered()}, "
                     f"主回调={'有' if self.hotkey_manager._callback else '无'}, "
                     f"额外热键数={len(self.hotkey_manager._multi)}")
    
    def teardown_hotkey(self) -> None:
        """关闭时注销热键"""
        if self.hotkey_manager:
            from PySide6.QtWidgets import QApplication
            app = QApplication.instance()
            if app:
                app.removeNativeEventFilter(self.hotkey_manager)
            self.hotkey_manager.unregister()
    
    def _print_banner(self):
        """打印启动信息"""
        print()
        print("=" * 45)
        print("    小忆桌面智能管家 v2.0")
        print("=" * 45)
        print()
        
        # 硬件信息
        hw = self.hardware_detector.detect() if self.hardware_detector else None
        if hw:
            print(f"  硬件: {hw.cpu_count}核 CPU | {hw.memory_gb:.0f}GB RAM | "
                  f"{'GPU: ' + hw.gpu_name if hw.has_gpu else '无GPU'}")
        
        # 性能模式
        mode = self.config_manager.get_performance_mode() if self.config_manager else "unknown"
        print(f"  模式: {mode}")
        
        # 已加载插件
        loaded = [k for k in self.plugins.keys() if not k.startswith("Null")]
        print(f"  插件: {', '.join(loaded) if loaded else '无'}")
        
        print()
        print("  操作说明:")
        print("    - 拖拽宠物移动位置")
        print("    - 点击宠物触发互动")
        print("    - 宠物会自动切换状态")
        print("    - 按 S 测试语音播报")
        print("    - 按 ESC 退出")
        print()
    
    def shutdown(self) -> None:
        """关闭应用"""
        if not self._running:
            return

        logger.info("小忆应用关闭中...")
        self._running = False

        # 强制停止所有音频播放
        try:
            import pygame
            if pygame.mixer.get_init():
                pygame.mixer.music.stop()
                pygame.mixer.stop()
                pygame.mixer.quit()
        except Exception:
            pass

        # 发布应用停止事件
        if self.event_bus:
            self.event_bus.emit(EventType.APP_STOP)

        # 释放 Agent 执行层（先停任务，再断订阅）
        self._teardown_agent_layer()

        # 停止文件监控
        self.stop_file_monitor()

        logger.info("小忆应用已关闭")
    
    def get_status(self) -> Dict[str, Any]:
        """获取应用状态"""
        status = {
            "running": self._running,
            "performance_mode": self.config_manager.get_performance_mode() if self.config_manager else "unknown",
            "plugins": {},
            "hardware": {},
            "loaded_plugins": []
        }
        
        # 插件状态
        for plugin_type in ["asr", "tts", "llm", "embedding", "vector_db", 
                           "voiceprint", "file_monitor", "avatar"]:
            plugin = self.plugins.get(plugin_type)
            if plugin:
                status["plugins"][plugin_type] = {
                    "available": plugin.is_available() if hasattr(plugin, 'is_available') else True
                }
                status["loaded_plugins"].append(plugin_type)
        
        # 硬件状态
        if self.hardware_detector:
            hw = self.hardware_detector.detect()
            status["hardware"] = {
                "cpu_count": hw.cpu_count,
                "memory_gb": hw.memory_gb,
                "has_gpu": hw.has_gpu,
                "gpu_name": hw.gpu_name
            }
        
        return status

    #: 支持的性能档位
    PERFORMANCE_MODES = ("low", "medium", "high")

    def switch_performance_mode(self, mode: str) -> bool:
        """切换性能模式并持久化

        切换后按新档位应用对应模型/设备配置；已加载的插件不重载
        （需要重载时由调用方重启应用）。

        Args:
            mode: "low" / "medium" / "high"

        Returns:
            True=切换成功；False=档位非法或配置管理器未就绪
        """
        if not self.config_manager:
            logger.warning("配置管理器未就绪，无法切换性能模式")
            return False

        mode = (mode or "").strip().lower()
        if mode not in self.PERFORMANCE_MODES:
            logger.warning("非法性能模式: %s（可选: %s）", mode, self.PERFORMANCE_MODES)
            return False

        try:
            self.config_manager.set_performance_mode(mode)
            logger.info("性能模式已切换为: %s", mode)
            return True
        except Exception as e:
            logger.error("切换性能模式失败: %s", e)
            return False


# 全局应用实例
_app: Optional[XiaoyiApp] = None


def get_app(config_path: str = "config.yaml") -> XiaoyiApp:
    """获取应用单例"""
    global _app
    if _app is None:
        _app = XiaoyiApp(config_path)
    return _app
