"""Agent 层装配（Bootstrap）

把散装的能力提供方按配置组装成一套可用的 Agent 栈。

设计（DSH 配置驱动组装思想的简体实现）：
- 单一入口 `build_agent_stack(config)` → AgentStack
- 换实现 = 配置里改 provider 名，不改调用方代码
- 返回对象持有全部 disposer，`dispose()` 一键回收

装配顺序（有依赖关系，不可乱序）：
    safety → registry（工具需要 guard）→ executor（需要 registry）
    → ack_cache（需要 TTS）→ pipeline（需要以上全部）
"""

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from agent.pipeline import AgentPipeline
from agent.plugins import (
    DEFAULT_MANIFEST,
    REQUIRED_SERVICES,
    SVC_ACK,
    SVC_EXECUTOR,
    SVC_MEMORY,
    SVC_PIPELINE,
    SVC_PLANNER,
    SVC_REGISTRY,
    SVC_REMINDER_SCHEDULER,
    SVC_ROUTER,
    SVC_SAFETY,
    SVC_SUMMARIZER,
    SVC_TRACKER,
    SVC_TRACKER_STORE,
    SVC_TRANSLATE,
    SVC_WEATHER,
    ambient_services,
)
from agent.providers.router.llm_router import LLMFunctionCall
from agent.seams.embedder import DEFAULT_DIM as DEFAULT_EMBED_DIM
from agent.seams.executor import ExecutorService
from agent.seams.memory import MemoryService
from agent.seams.planner import MAX_PLAN_STEPS, PlannerService
from agent.seams.router import RouterService
from agent.seams.safety import SafetyService
from agent.seams.summarizer import SummarizerService
from agent.tools.productivity_tools import (
    ReminderCallback,
    TranslateFunc,
    WeatherFunc,
)
from agent.tools.registry import ToolRegistry
from agent.tracker import EntityTracker
from agent.tracker_store import TrackerStore
from core.kernel.context import Context
from core.kernel.events import EventBus, EventTypes
from core.kernel.loader import PluginLoader
from services.ack_cache import AckCache   # noqa: F401  向后兼容再导出（测试会 patch 此类）

logger = logging.getLogger(__name__)


#: LLM 单次补全签名：(prompt) -> Optional[str]
LLMOnceCall = Callable[[str], Optional[str]]


@dataclass
class AgentConfig:
    """Agent 层配置（从 config.yaml 的 agent 段解析）"""

    enabled: bool = True

    # 插件清单（None → 使用 agent.plugins.DEFAULT_MANIFEST）
    plugins: Optional[List[Dict[str, Any]]] = None

    # 路由
    router_provider: str = "hybrid"
    confidence_threshold: float = 0.5
    llm_router_enabled: bool = True
    llm_router_timeout: float = 5.0

    # 安全
    path_whitelist: List[str] = field(default_factory=list)
    audit_db: Optional[str] = None
    audit_enabled: bool = True
    remember_choices: bool = True
    confirm_timeout: float = 30.0

    # 执行器
    pool_size: int = 4
    task_timeout: float = 60.0

    # 工具集开关
    tools_file: bool = True
    tools_system: bool = True
    tools_productivity: bool = True
    tools_browser: bool = True
    search_engine: str = "bing"

    # 生产力能力的真实后端（F7）
    productivity_translate: bool = True          # translate 复用项目 LLM
    productivity_weather: bool = True            # weather 走 Open-Meteo（免密钥）
    productivity_reminder_scheduler: bool = True  # 提醒到点调度线程
    weather_default_city: str = ""               # 用户没说城市时的默认城市（空=必须问）

    # 摘要器
    summarizer_provider: str = "hybrid"
    summarizer_threshold: int = 3
    async_refine: bool = True

    # 多步规划（P3 / D5）
    planner_enabled: bool = True
    planner_provider: str = "hybrid"
    planner_max_steps: int = MAX_PLAN_STEPS
    planner_llm_timeout: float = 20.0

    # 长期记忆（P3）
    memory_enabled: bool = True
    memory_store: Optional[str] = None
    memory_embedder: str = "hashing"
    memory_st_model: str = "all-MiniLM-L6-v2"
    memory_dim: int = DEFAULT_EMBED_DIM
    memory_max_items: int = 2000
    memory_vector_backend: str = "auto"
    memory_min_similarity: float = 0.25
    memory_episodes: bool = True
    memory_hints: bool = True

    # 管线
    ack_enabled: bool = True
    ack_warmup: bool = True
    ack_lru_size: int = 64

    # 实体栈持久化（P2-3）
    tracker_persist: bool = True
    tracker_store: Optional[str] = None

    @staticmethod
    def _parse_plugins(raw: Any) -> Optional[List[Dict[str, Any]]]:
        """解析插件清单

        Returns:
            合法清单列表；配置缺失/类型不对时返回 None（→ 用 DEFAULT_MANIFEST）
        """
        if raw is None:
            return None
        if not isinstance(raw, list):
            logger.warning("[agent] agent.plugins 必须是列表，收到 %s，改用默认清单",
                           type(raw).__name__)
            return None
        items = [dict(x) for x in raw if isinstance(x, dict)]
        if not items:
            logger.warning("[agent] agent.plugins 为空或元素非法，改用默认清单")
            return None
        return items

    @classmethod
    def from_config_manager(cls, cm: Any) -> "AgentConfig":
        """从 ConfigManager 读取配置

        Args:
            cm: ConfigManager 实例（可为 None → 全部使用默认值）

        Returns:
            AgentConfig
        """
        if cm is None:
            return cls()

        def get(key: str, default: Any) -> Any:
            try:
                v = cm.get(key, default)
                return default if v is None else v
            except Exception:
                return default

        whitelist = get("agent.safety.path_whitelist", None)
        if not isinstance(whitelist, list) or not whitelist:
            whitelist = []

        audit_db = get("agent.safety.audit_db", None)
        if not audit_db:
            # 默认落在项目 data/ 下
            try:
                audit_db = str(Path("data") / "agent_audit.db")
            except Exception:
                audit_db = None

        return cls(
            enabled=bool(get("agent.enabled", True)),
            plugins=cls._parse_plugins(get("agent.plugins", None)),
            router_provider=str(get("agent.router.provider", "hybrid")),
            confidence_threshold=float(get("agent.router.rule.confidence_threshold", 0.5)),
            llm_router_enabled=bool(get("agent.router.llm.enabled", True)),
            llm_router_timeout=float(get("agent.router.llm.timeout", 5)),
            path_whitelist=whitelist,
            audit_db=audit_db,
            audit_enabled=bool(get("agent.safety.audit_enabled", True)),
            remember_choices=bool(get("agent.safety.remember_choices", True)),
            confirm_timeout=float(get("agent.safety.confirm_timeout", 30.0)),
            pool_size=int(get("agent.executor.pool_size", 4)),
            task_timeout=float(get("agent.executor.timeout", 60)),
            tools_file=bool(get("agent.tools.file", True)),
            tools_system=bool(get("agent.tools.system", True)),
            tools_productivity=bool(get("agent.tools.productivity", True)),
            tools_browser=bool(get("agent.tools.browser", True)),
            search_engine=str(get("agent.tools.search_engine", "bing")),
            productivity_translate=bool(get("agent.productivity.translate", True)),
            productivity_weather=bool(get("agent.productivity.weather", True)),
            productivity_reminder_scheduler=bool(
                get("agent.productivity.reminder_scheduler", True)),
            weather_default_city=str(get("agent.weather.default_city", "") or ""),
            summarizer_provider=str(get("agent.summarizer.provider", "hybrid")),
            summarizer_threshold=int(get("agent.summarizer.template_threshold", 3)),
            async_refine=bool(get("agent.summarizer.async_refine", True)),
            planner_enabled=bool(get("agent.planner.enabled", True)),
            planner_provider=str(get("agent.planner.provider", "hybrid")),
            planner_max_steps=int(
                get("agent.planner.max_steps", MAX_PLAN_STEPS) or MAX_PLAN_STEPS
            ),
            planner_llm_timeout=float(get("agent.planner.llm_timeout", 20.0) or 20.0),
            memory_enabled=bool(get("agent.memory.enabled", True)),
            memory_store=get("agent.memory.store", None),
            memory_embedder=str(get("agent.memory.embedder", "hashing")),
            memory_st_model=str(
                get("agent.memory.st_model", "all-MiniLM-L6-v2")
            ),
            memory_dim=int(get("agent.memory.dim", DEFAULT_EMBED_DIM)
                           or DEFAULT_EMBED_DIM),
            memory_max_items=int(get("agent.memory.max_items", 2000) or 2000),
            memory_vector_backend=str(
                get("agent.memory.vector_backend", "auto")
            ),
            memory_min_similarity=float(
                get("agent.memory.min_similarity", 0.25)
            ),
            memory_episodes=bool(get("agent.memory.episodes", True)),
            memory_hints=bool(get("agent.memory.hints", True)),
            ack_enabled=bool(get("agent.pipeline.ack_enabled", True)),
            ack_warmup=bool(get("agent.pipeline.ack_warmup", True)),
            ack_lru_size=int(get("agent.pipeline.ack_lru_size", 64)),
            tracker_persist=bool(get("agent.pipeline.tracker_persist", True)),
            tracker_store=get("agent.pipeline.tracker_store", None),
        )


class AgentStack:
    """组装完成的 Agent 栈

    Attributes:
        bus: 事件总线（与 Voice Layer 共享）
        router / safety / executor: 三个能力提供方
        registry: 工具注册表
        ack_cache: 确认语缓存
        pipeline: 并行管线协调器
    """

    def __init__(
        self,
        bus: EventBus,
        router: RouterService,
        safety: SafetyService,
        executor: ExecutorService,
        registry: ToolRegistry,
        ack_cache: Optional[AckCache],
        pipeline: AgentPipeline,
        disposers: List[Callable[[], None]],
        summarizer: Optional[SummarizerService] = None,
        tracker: Optional[EntityTracker] = None,
        tracker_store: Optional[TrackerStore] = None,
        loader: Optional[PluginLoader] = None,
        ctx: Optional[Context] = None,
        planner: Optional[PlannerService] = None,
        memory: Optional[MemoryService] = None,
        reminder_scheduler: Optional[Any] = None,
    ) -> None:
        self.bus = bus
        self.router = router
        self.safety = safety
        self.executor = executor
        self.registry = registry
        self.ack_cache = ack_cache
        self.pipeline = pipeline
        self.summarizer = summarizer
        self.planner = planner        # 多步规划器（P3；None = 未装/已关闭）
        self.memory = memory          # 长期记忆（P3；None = 未装/已关闭）
        self.reminder_scheduler = reminder_scheduler   # 提醒调度器（F7；None = 未装/已关闭）
        self.tracker = tracker if tracker is not None else EntityTracker()
        self.tracker_store = tracker_store
        self.loader = loader          # 插件加载器（可查询已加载插件 / 热插拔）
        self.ctx = ctx                # 装配用的上下文
        self._disposers = disposers
        self._disposed = False

    def start(self) -> None:
        """启动管线（含从磁盘恢复实体栈）"""
        if self.tracker_store is not None:
            try:
                self.tracker_store.load_into(self.tracker)
            except Exception as e:
                # 恢复失败不影响启动：最差情况就是回到"无上下文"
                logger.warning("[agent] 实体栈恢复失败，按空上下文启动: %s", e)
        self.pipeline.start()

    def stop(self) -> None:
        """停止管线（不释放资源）"""
        self.pipeline.stop()

    def dispose(self) -> None:
        """释放全部资源（幂等）"""
        if self._disposed:
            return
        self._disposed = True

        try:
            self.pipeline.stop()
        except Exception as e:
            logger.warning("[agent] 停止管线失败: %s", e)

        for dispose in reversed(self._disposers):
            try:
                dispose()
            except Exception as e:
                logger.warning("[agent] 释放资源失败: %s", e)

        self._disposers.clear()
        logger.info("[agent] Agent 栈已释放")

    @property
    def disposed(self) -> bool:
        return self._disposed

    def stats(self) -> Dict[str, Any]:
        """综合状态（供 /status 或日志使用）"""
        return {
            "enabled": self.pipeline._enabled,
            "tools": self.registry.count(),
            "tool_names": self.registry.names(),
            "router": type(self.router).__name__,
            "summarizer": type(self.summarizer).__name__ if self.summarizer else None,
            "planner": type(self.planner).__name__ if self.planner else None,
            "memory": type(self.memory).__name__ if self.memory else None,
            "memory_stats": self.memory.stats() if self.memory else None,
            "pipeline": self.pipeline.stats(),
            "ack_cached": self.ack_cache.cached_count() if self.ack_cache else 0,
            "safety_whitelist": [str(p) for p in self.safety.whitelist_roots()],
            "tracked_files": self.tracker.file_count(),
            "plugins": self.loader.loaded_ids() if self.loader is not None else [],
            "tracker_store": (
                self.tracker_store.stats() if self.tracker_store is not None else None
            ),
        }

    def __repr__(self) -> str:
        return (
            f"<AgentStack tools={self.registry.count()} "
            f"enabled={self.pipeline._enabled} disposed={self._disposed}>"
        )


def build_agent_stack(
    config: AgentConfig,
    bus: EventBus,
    synthesize: Optional[Callable[[str], Optional[bytes]]] = None,
    llm_once: Optional[LLMOnceCall] = None,
    llm_route_call: Optional[LLMFunctionCall] = None,
    translate_func: Optional[TranslateFunc] = None,
    weather_func: Optional[WeatherFunc] = None,
    on_reminder_due: Optional[ReminderCallback] = None,
) -> AgentStack:
    """按配置组装 Agent 栈

    Args:
        config: Agent 配置
        bus: 事件总线（与 Voice Layer 共享）
        synthesize: TTS 合成函数（用于确认语预热）；None 则跳过预热
        llm_once: LLM 单次补全函数（用于结果润色）；None 则摘要全走模板
        llm_route_call: LLM function calling 函数（用于意图兜底）；None 则只走规则
        translate_func: 翻译函数（注入给 translate 工具）
        weather_func: 天气取数函数（注入给 weather 工具）
        on_reminder_due: 提醒到点回调

    Returns:
        AgentStack（已完成装配，但未 start）
    """
    # ── 0. 环境服务：把外部注入的依赖与配置放进上下文，供插件取用 ──
    #
    # `translate` / `weather` 为 None 时**不注册**，而不是注册一个 None：
    # 这两个能力的默认实现由 `productivity_providers` 插件提供（F7），
    # 注册 None 会把它盖掉（并打一条"覆盖已注册服务"的噪音日志）。
    # 显式传入的函数仍算覆盖 —— 调用方说了算。
    ctx = Context()
    for name, value in ambient_services(
        config=config,
        bus=bus,
        tts=synthesize,
        llm_once=llm_once,
        llm_route_call=llm_route_call,
        translate=translate_func,
        weather=weather_func,
        reminder_cb=on_reminder_due,
    ).items():
        if value is None and name in (SVC_TRANSLATE, SVC_WEATHER):
            continue
        ctx.provide(name, value)

    # ── 1. 配置驱动装配：清单决定加载哪些插件、以什么顺序 ──
    loader = PluginLoader()
    manifest = config.plugins if config.plugins else DEFAULT_MANIFEST
    ordered = loader.resolve_order(loader.parse_config(manifest))
    loaded, failed = loader.load_all(ordered, ctx)
    logger.info("[agent] 插件装配：成功 %d，失败 %d → %s",
                len(loaded), len(failed), loaded)

    # ── 2. 取回服务
    #     缺关键服务 ⇒ 抛错；由 core/app.py 捕获并降级为「纯对话模式」
    missing = [name for name in REQUIRED_SERVICES if not ctx.has(name)]
    if missing:
        _teardown_plugins(loader, ctx)
        raise RuntimeError(
            f"Agent 装配失败：缺少服务 {missing}（插件失败列表：{failed or '无'}）"
        )

    registry = ctx.use(SVC_REGISTRY)
    safety = ctx.use(SVC_SAFETY)
    router = ctx.use(SVC_ROUTER)
    summarizer = ctx.use(SVC_SUMMARIZER)
    executor = ctx.use(SVC_EXECUTOR)
    pipeline = ctx.use(SVC_PIPELINE)

    logger.info(
        "[agent] 装配完成 (enabled=%s, tools=%d, pool=%d, ack=%s, router=%s)",
        config.enabled, registry.count(), config.pool_size,
        ctx.has(SVC_ACK), config.router_provider,
    )

    return AgentStack(
        bus=bus,
        router=router,
        safety=safety,
        executor=executor,
        registry=registry,
        ack_cache=ctx.use_or(SVC_ACK),
        pipeline=pipeline,
        disposers=[lambda: _teardown_plugins(loader, ctx)],
        summarizer=summarizer,
        tracker=ctx.use(SVC_TRACKER),
        tracker_store=ctx.use_or(SVC_TRACKER_STORE),
        loader=loader,
        ctx=ctx,
        planner=ctx.use_or(SVC_PLANNER),
        memory=ctx.use_or(SVC_MEMORY),
        reminder_scheduler=ctx.use_or(SVC_REMINDER_SCHEDULER),
    )


def _teardown_plugins(loader: PluginLoader, ctx: Context) -> None:
    """逆序卸载全部插件，再释放上下文（幂等，失败不抛）"""
    try:
        loader.unload_all()
    except Exception as e:
        logger.warning("[agent] 卸载插件失败: %s", e)
    try:
        ctx.dispose()
    except Exception as e:
        logger.warning("[agent] 释放上下文失败: %s", e)

