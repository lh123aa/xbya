"""
配置管理模块
负责加载、解析和管理系统配置
"""

import os
import time
import yaml
import logging
from typing import Dict, Any, Optional
from pathlib import Path

logger = logging.getLogger(__name__)


class _Missing:
    """路径取值的"不存在"哨兵（区别于值为 None）。"""

    def __repr__(self) -> str:  # pragma: no cover - 仅调试用
        return "<MISSING>"


_MISSING = _Missing()


class ConfigManager:
    """配置管理器"""
    
    # 默认配置
    DEFAULT_CONFIG = {
        "app": {
            "name": "小忆",
            "version": "1.0.0"
        },
        "system": {
            "performance_mode": "medium"  # low | medium | high
        },
        "plugins": {
            "asr": {
                "engine": "faster_whisper",
                "params": {
                    "model_size": "small",
                    "device": "cpu",
                    "compute_type": "int8"
                }
            },
            "tts": {
                "engine": "edge_tts",
                "params": {
                    "voice": "zh-CN-XiaoxiaoNeural"
                }
            },
            "llm": {
                "engine": "ollama",
                "params": {
                    "model": "qwen2.5:1.5b",
                    "base_url": "http://localhost:11434"
                }
            },
            "embedding": {
                "engine": "embed_anything",
                "params": {
                    "model": "BAAI/bge-small-zh"
                }
            },
            "vector_db": {
                "engine": "leann",
                "params": {
                    "storage_path": "./data/vectordb"
                }
            },
            "file_monitor": {
                "engine": "watchfiles",
                "params": {
                    "watch_dirs": ["~/Desktop", "~/Downloads"],
                    "recursive": True
                }
            },
            "voiceprint": {
                "engine": "3d_speaker",
                "params": {
                    "model_name": "ERes2Net",
                    "threshold": 0.7
                }
            },
            "avatar": {
                "engine": "liveportrait",
                "params": {
                    "resolution": 256,
                    "fps": 10
                }
            }
        },
        "ui": {
            "pet_size": 200,
            # 精灵图角色目录名（resources/sprites/<pet_sprite>/）。
            # 默认 "cat" 保持改动前行为；换成自己的角色只需改这一项。
            "pet_sprite": "cat",
            # 渲染模式：sprite（2D 序列帧）| vrm（3D 模型）。
            # 注意：以前这一项**从未被读取** —— PetWindow.render_mode 硬编码为 "sprite"，
            # 配置里写 vrm 只是碰巧因为 app.py 无条件调 enable_vrm() 才生效。
            # 现在 app.py 会按它决定是否启用 VRM，配置与行为一致。
            "render_mode": "sprite",
            "fps": {
                "low": 15,
                "medium": 30,
                "high": 60
            },
            "position": {
                "x": 100,
                "y": 100
            }
        },
        "voice": {
            "wake_word": "嘿欣雅",
            "enthusiasm_level": 3,  # 1-5
            "confirm_delete": True,
            "hotkey_enabled": True,
            "hotkey_toggle": "Ctrl+Alt+M",
            "hotkey_mute": "Ctrl+Alt+S",
            "hotkey_interrupt": "Ctrl+Alt+D",
            # 轮换麦克风设备（多声源场景快速试哪个能收到声音）
            "hotkey_mic_next": "Ctrl+Alt+N",
            # 输入设备指定：null=自动挑选；数字=按索引；字符串=按名字片段匹配。
            # 多声源（本机麦克风 + 远程桌面虚拟麦克风）时**必须显式指定**，
            # 否则自动挑选可能选到收不到用户声音的那个。
            "mic_device": None
        }
    }
    
    def __init__(self, config_path: str = "config.yaml"):
        """
        初始化配置管理器
        """
        self.config_path = Path(config_path)
        self.config: Dict[str, Any] = {}
        #: 程序显式 `set()` 过的键路径（点号分隔）。只有这些键的**内存值**
        #: 才允许覆盖磁盘 —— 其余键（默认值合并进来的、外部加的）一律以磁盘为准。
        #: 这是 D21 守护的判据：没它区分不出"默认值 None"和"程序真的设成了 None"。
        self._explicit_keys: set = set()
        # D21 防御：防重复保存——同一 key 在 2s 内只落盘一次
        self._save_timestamps: Dict[str, float] = {}
        self._save_cooldown_sec = 2.0
        self._load_config()
    
    def _load_config(self) -> None:
        """加载配置文件"""
        try:
            if self.config_path.exists():
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    self.config = yaml.safe_load(f) or {}
                logger.info(f"配置文件加载成功: {self.config_path}")
                
                # 合并默认配置（确保所有键都存在）
                self._merge_config(self.DEFAULT_CONFIG, self.config)
            else:
                logger.warning(f"配置文件不存在: {self.config_path}，使用默认配置")
                self.config = self.DEFAULT_CONFIG.copy()
                self._save_config()  # 保存默认配置到文件
                
        except Exception as e:
            logger.error(f"加载配置文件失败: {e}")
            self.config = self.DEFAULT_CONFIG.copy()
    
    def _merge_config(self, default: Dict, target: Dict) -> None:
        """递归合并配置"""
        for key, value in default.items():
            if key not in target:
                target[key] = value
            elif isinstance(value, dict) and isinstance(target[key], dict):
                self._merge_config(value, target[key])
    
    def _save_config(self) -> None:
        """保存配置到文件。

        ⚠️ D21 守护：`set()` 会调用本方法把**整份内存配置**落盘。如果外部
        （编辑器/工具/另一个进程）在本次加载之后往 `config.yaml` 改了值或加了键，
        直接 `yaml.dump(self.config)` 会**静默抹掉**那些改动 —— 已实测发生过
        （`voice.mic_device` / `voice.hotkey_mic_next` 就是这样丢的）。

        判据：**只有 `set()` 显式设过的键**（`_explicit_keys`）才用内存值，
        其余键一律回读磁盘、以磁盘为准。这样既保证"刚 set 的值一定落盘"，
        又保证"外部改动不会被内存快照吞掉"。

        为什么不能只做"补齐缺失键"：默认值合并（`_merge_config`）会把
        `voice.mic_device=None` 这类默认键提前塞进内存，于是"内存里有没有这个键"
        区分不出"是默认值"还是"程序真的设过" —— 必须靠 `_explicit_keys` 记录。
        """
        try:
            # 确保目录存在
            self.config_path.parent.mkdir(parents=True, exist_ok=True)

            # D21 守护：用磁盘内容打底，只把"程序显式 set 过"的键覆盖上去
            merged = self._config_for_save()

            with open(self.config_path, 'w', encoding='utf-8') as f:
                yaml.dump(merged, f, allow_unicode=True, default_flow_style=False)
            logger.debug(f"配置文件已保存: {self.config_path}")

        except Exception as e:
            logger.error(f"保存配置文件失败: {e}")

    def _config_for_save(self) -> Dict[str, Any]:
        """构造待落盘的配置：磁盘内容打底 + 程序显式设过的键覆盖。

        回读磁盘失败（文件损坏/不存在）时退回纯内存配置 —— 落盘优先于保全外部键。
        """
        disk: Dict[str, Any] = {}
        try:
            if self.config_path.exists():
                with open(self.config_path, "r", encoding="utf-8") as f:
                    loaded = yaml.safe_load(f)
                if isinstance(loaded, dict):
                    disk = loaded
        except Exception as e:
            logger.debug("[config] 回读磁盘配置失败，按纯内存落盘: %s", e)

        if not disk:
            # 首次创建 / 磁盘不可读：直接用内存配置
            return self.config

        # 把内存中"程序显式设过"的键逐个盖到磁盘副本上
        preserved = len(self._explicit_keys)
        for path in self._explicit_keys:
            value = self._get_by_path(self.config, path)
            if value is _MISSING:
                continue
            self._set_by_path(disk, path, value)

        # 磁盘上新增、内存里没有的键，也并入内存（让 get() 能立刻读到）
        added = self._merge_missing(disk, self.config)
        if added:
            logger.warning(
                "[config] 检测到磁盘上新增的配置键，已并入内存: %s",
                ", ".join(sorted(added)[:12]),
            )
        logger.debug("[config] 落盘：磁盘打底 + %d 个显式键覆盖", preserved)
        return disk

    @staticmethod
    def _get_by_path(cfg: Dict, path: str):
        """按点号路径取值；不存在返回 _MISSING。"""
        cur: Any = cfg
        for k in path.split("."):
            if not isinstance(cur, dict) or k not in cur:
                return _MISSING
            cur = cur[k]
        return cur

    @staticmethod
    def _set_by_path(cfg: Dict, path: str, value: Any) -> None:
        """按点号路径写入（中间层缺失则创建）。"""
        keys = path.split(".")
        cur = cfg
        for k in keys[:-1]:
            nxt = cur.get(k)
            if not isinstance(nxt, dict):
                nxt = {}
                cur[k] = nxt
            cur = nxt
        cur[keys[-1]] = value

    @classmethod
    def _merge_missing(cls, src: Dict, dst: Dict, prefix: str = "") -> list:
        """把 `src` 中 `dst` 缺失的键补进 `dst`，返回补入的键路径列表。"""
        added: list = []
        for key, value in src.items():
            path = f"{prefix}{key}"
            if key not in dst:
                dst[key] = value
                added.append(path)
            elif isinstance(value, dict) and isinstance(dst.get(key), dict):
                added.extend(cls._merge_missing(value, dst[key], f"{path}."))
        return added


    def get(self, key: str, default: Any = None) -> Any:
        """
        获取配置值
        
        Args:
            key: 配置键（支持点号分隔，如'plugins.asr.engine'）
            default: 默认值
            
        Returns:
            配置值
        """
        keys = key.split('.')
        value = self.config
        
        for k in keys:
            if isinstance(value, dict) and k in value:
                value = value[k]
            else:
                return default
        
        return value
    
    def set(self, key: str, value: Any) -> None:
        """
        设置配置值

        Args:
            key: 配置键（支持点号分隔）
            value: 配置值
        """
        keys = key.split('.')
        config = self.config

        for k in keys[:-1]:
            if k not in config:
                config[k] = {}
            config = config[k]

        config[keys[-1]] = value
        # 记录"程序显式设过"，落盘时只有这些键允许覆盖磁盘（见 _save_config）
        self._explicit_keys.add(key)
        # D21 防御：节流保存 —— 同一 key 在 cooldown 窗口内只落盘一次。
        #
        # 为什么需要：启动阶段有十几处 `set()`（各开关初始化、热键注册、
        # 设备解析…），每次都整份读写 YAML → 实测刷出 **30+ 行**"配置文件保存成功"，
        # 既拖慢启动又淹没日志。
        #
        # ⚠️ 安全性：窗口内**内存值仍然是最新的**，只是暂缓落盘；
        # 若进程在此期间退出，最后一小段改动可能丢 —— 所以：
        #   · 提供 `flush()` 供退出路径与需要"立刻持久化"的调用方显式落盘
        #   · `set` 的**首次**调用必然落盘（last_save 初值 0.0）
        now = time.time()
        last_save = self._save_timestamps.get(key, 0.0)
        if now - last_save >= self._save_cooldown_sec:
            self._save_timestamps[key] = now
            self._save_config()
        else:
            logger.debug(
                "[config] %s 在保存节流窗口内（%.1fs），暂缓落盘",
                key, self._save_cooldown_sec,
            )

    def flush(self) -> None:
        """强制立即落盘（不受节流窗口限制）。

        用途：进程退出前、或调用方明确要求"改完必须立刻持久化"时。
        """
        self._save_timestamps.clear()
        self._save_config()
    
    def get_performance_mode(self) -> str:
        """
        获取当前性能模式
        
        Returns:
            性能模式：low, medium, high
        """
        return self.get("system.performance_mode", "medium")
    
    def set_performance_mode(self, mode: str) -> None:
        """
        设置性能模式
        
        Args:
            mode: 性能模式：low, medium, high
        """
        if mode not in ["low", "medium", "high"]:
            raise ValueError(f"无效的性能模式: {mode}")
        
        # 同模式跳过：防止重复调用时覆盖用户已自定义的模型配置
        if mode == self.get_performance_mode() and mode != "auto":
            logger.info(f"性能模式已为 {mode}，跳过应用（保留自定义模型配置）")
            return
        
        self.set("system.performance_mode", mode)
        
        # 根据模式调整插件参数
        self._apply_mode_settings(mode)

        # ⚡ 性能模式是**用户显式动作**（设置界面里点一下），必须立刻持久化：
        #    · 它在窗口期内会连带 set 好几个键（model_size/device/fps），
        #      受保存节流影响，最后一个键可能没落盘；
        #    · 实测踩到过：测试里"切到 high 再切回 low"因节流没落盘，
        #      真实 config.yaml 永久停在 high → ASR 从 base 变 small → 慢 3 倍。
        #    这类"低频但影响大"的写操作不做节流。
        self.flush()
    
    def _apply_mode_settings(self, mode: str) -> None:
        """根据性能模式应用设置

        注意：只调整ASR模型、ASR设备、UI帧率这类资源敏感参数。
        **绝不修改 llm.engine / llm.params.model**——用户自定义的模型选择
        优先（历史上 low 模式禁用 LLM 曾导致桌宠核心对话功能被关闭）。

        档位→ASR 模型映射（本地 CPU 保守；高档若检测到可用 GPU 才尝试更大的模型）：
          low    -> tiny  (最快，最省资源)
          medium -> small (平衡)
          high   -> medium(更准，需较强者力/显存)
        """
        if mode == "low":
            # low：小模型求快，但保留 base 保证可识别度（不再用 tiny，避免"听不见"）
            self.set("plugins.asr.params.model_size", "base")
            self.set("plugins.asr.params.device", "cpu")
            self.set("ui.fps.current", 30)

        elif mode == "medium":
            self.set("plugins.asr.params.model_size", "small")
            self.set("plugins.asr.params.device", "cpu")
            self.set("ui.fps.current", 30)

        elif mode == "high":
            # 高档：**先看 GPU 是否真能用**，不能就退回 small。
            #
            # 实测教训：原来这里无条件写 `medium`，而本机 CUDA 运行库缺失
            # （cublas64_12.dll），medium 只能跑 CPU —— 一段 4 秒音频要 **16 秒**
            # 才出结果，用户感知就是"反应慢到没法用"。CPU 上 medium 与 small 的
            # 中文准确率差距远小于 16s vs 4s 的可用性差距。
            # 判据：只有「检测到 GPU **且** CUDA 运行库可用」才敢上 medium。
            if self._gpu_usable_for_asr():
                self.set("plugins.asr.params.model_size", "medium")
                self.set("plugins.asr.params.device", "cuda")
            else:
                logger.info("[perf] 高档但 GPU/CUDA 不可用 → ASR 用 small+cpu（medium 在 CPU 上太慢）")
                self.set("plugins.asr.params.model_size", "small")
                self.set("plugins.asr.params.device", "cpu")
            self.set("ui.fps.current", 60)

    @staticmethod
    def _gpu_usable_for_asr() -> bool:
        """探测 faster-whisper 能否真的在 GPU 上跑。

        只信**实际加载测试**，不信"系统里有独显" —— 本机有 MX250，
        但缺 cublas64_12.dll，`WhisperModel(device='cuda')` 能构造成功、
        首次推理才抛错。轻量探测 cublas/cudnn 是否可导入即可判定。
        """
        try:
            import ctypes
            for lib in ("cublas64_12.dll", "cublas64_11.dll",
                        "cudnn_ops64_9.dll", "cudnn64_8.dll"):
                try:
                    ctypes.CDLL(lib)
                    return True
                except OSError:
                    continue
            return False
        except Exception:
            return False
    
    def get_plugin_config(self, plugin_type: str) -> Dict[str, Any]:
        """
        获取插件配置
        
        Args:
            plugin_type: 插件类型（asr, tts, llm等）
            
        Returns:
            插件配置字典
        """
        return self.get(f"plugins.{plugin_type}", {})
    
    def get_ui_config(self) -> Dict[str, Any]:
        """
        获取UI配置
        
        Returns:
            UI配置字典
        """
        return self.get("ui", {})
    
    def get_voice_config(self) -> Dict[str, Any]:
        """
        获取语音配置
        
        Returns:
            语音配置字典
        """
        return self.get("voice", {})
    
    def reload(self) -> None:
        """重新加载配置"""
        self._load_config()


# 全局配置实例
_config_manager: Optional[ConfigManager] = None


def get_config_manager(config_path: str = "config.yaml") -> ConfigManager:
    """
    获取配置管理器单例
    
    Args:
        config_path: 配置文件路径
        
    Returns:
        配置管理器实例
    """
    global _config_manager
    
    if _config_manager is None:
        _config_manager = ConfigManager(config_path)
    
    return _config_manager
