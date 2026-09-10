"""Agent 层插件包 —— 配置驱动装配（偿还 F2/D12）

每个插件只负责**一个关注点**的构造与注册：

    def setup(ctx, **config) -> Optional[Callable[[], None]]

依赖有两个来源：

1. **环境服务** — 由 `build_agent_stack` 在加载插件前预置：
   `agent.config` / `agent.bus` / `agent.tts` / `agent.llm_once` /
   `agent.llm_route_call` / `agent.translate` / `agent.weather` / `agent.reminder_cb`
2. **其他插件提供的服务** — `safety` / `tool_registry` / `router` / `executor` /
   `summarizer` / `ack_cache` / `tracker` / `tracker_store` / `pipeline`，
   顺序由清单里的 `depends_on` 保证（`PluginLoader.resolve_order` 拓扑排序）

于是「换实现 / 增减能力」= 改 `config.yaml` 的 `agent.plugins` 清单，**不改代码**。
`build_agent_stack` 自身不再 new 任何 Provider，只负责预置环境服务、
跑加载器、再把服务取回来拼成 `AgentStack`。
"""

from typing import Any, Dict, List

# ── 环境服务名（由 build_agent_stack 预置）──
SVC_CONFIG = "agent.config"
SVC_BUS = "agent.bus"
SVC_TTS = "agent.tts"
SVC_LLM_ONCE = "agent.llm_once"
SVC_LLM_ROUTE = "agent.llm_route_call"
SVC_TRANSLATE = "agent.translate"
SVC_WEATHER = "agent.weather"
SVC_REMINDER = "agent.reminder_cb"

# ── 插件产出服务名 ──
SVC_REGISTRY = "tool_registry"
SVC_SAFETY = "safety"
SVC_ROUTER = "router"
SVC_EXECUTOR = "executor"
SVC_SUMMARIZER = "summarizer"
SVC_ACK = "ack_cache"
SVC_TRACKER = "tracker"
SVC_TRACKER_STORE = "tracker_store"
SVC_PIPELINE = "pipeline"
SVC_PLANNER = "planner"          # P3：多步任务规划
SVC_EMBEDDER = "embedder"        # P3：文本向量化
SVC_MEMORY = "memory"            # P3：长期记忆
SVC_REMINDER_SCHEDULER = "reminder_scheduler"   # F7：提醒到点调度器

#: 需要由 AgentStack 持有的服务（缺一个就装配失败 → 上层降级为纯对话模式）
REQUIRED_SERVICES = (
    SVC_SAFETY, SVC_REGISTRY, SVC_ROUTER, SVC_EXECUTOR, SVC_SUMMARIZER, SVC_PIPELINE,
)


#: 默认插件清单
#:
#: 未在 config.yaml 里声明 `agent.plugins` 时使用本清单，
#: 保证既有配置与既有测试行为不变（向后兼容）。
DEFAULT_MANIFEST: List[Dict[str, Any]] = [
    {
        "id": "kernel",
        "module": "agent.plugins.kernel_plugin",
    },
    {
        "id": "safety",
        "module": "agent.plugins.safety_plugin",
        "depends_on": ["kernel"],
    },
    {
        "id": "file_tools",
        "module": "agent.plugins.file_tools_plugin",
        "depends_on": ["safety"],
    },
    {
        "id": "system_tools",
        "module": "agent.plugins.system_tools_plugin",
        "depends_on": ["safety"],
    },
    {
        "id": "productivity_providers",
        "module": "agent.plugins.productivity_providers_plugin",
        "depends_on": ["kernel"],
    },
    {
        "id": "productivity_tools",
        "module": "agent.plugins.productivity_tools_plugin",
        "depends_on": ["kernel", "productivity_providers"],
    },
    {
        "id": "browser_tools",
        "module": "agent.plugins.browser_tools_plugin",
        "depends_on": ["kernel"],
    },
    {
        "id": "router",
        "module": "agent.plugins.router_plugin",
        "depends_on": ["kernel"],
    },
    {
        "id": "planner",
        "module": "agent.plugins.planner_plugin",
        "depends_on": ["kernel"],
    },
    {
        "id": "memory",
        "module": "agent.plugins.memory_plugin",
        "depends_on": ["kernel"],
    },
    {
        "id": "summarizer",
        "module": "agent.plugins.summarizer_plugin",
        "depends_on": ["kernel"],
    },
    {
        "id": "executor",
        "module": "agent.plugins.executor_plugin",
        "depends_on": [
            "file_tools", "system_tools", "productivity_tools", "browser_tools",
        ],
    },
    {
        "id": "pipeline",
        "module": "agent.plugins.pipeline_plugin",
        "depends_on": ["safety", "router", "summarizer", "executor"],
    },
]


def ambient_services(
    config: Any,
    bus: Any,
    tts: Any = None,
    llm_once: Any = None,
    llm_route_call: Any = None,
    translate: Any = None,
    weather: Any = None,
    reminder_cb: Any = None,
) -> Dict[str, Any]:
    """构造环境服务表（供 build_agent_stack 预置进 Context）"""
    return {
        SVC_CONFIG: config,
        SVC_BUS: bus,
        SVC_TTS: tts,
        SVC_LLM_ONCE: llm_once,
        SVC_LLM_ROUTE: llm_route_call,
        SVC_TRANSLATE: translate,
        SVC_WEATHER: weather,
        SVC_REMINDER: reminder_cb,
    }


def register_tools(ctx, tools) -> Any:
    """把一批工具注册进 `tool_registry`，返回组合注销函数

    Args:
        ctx: 插件上下文
        tools: BaseTool 列表

    Returns:
        注销函数（逆序注销本批工具）；卸载插件时被自动调用，
        因此"动态摘掉一组工具"是有效操作，而不是只把服务引用清空。
    """
    import logging

    logger = logging.getLogger(__name__)
    registry = ctx.use(SVC_REGISTRY)
    disposers = [registry.register(t) for t in tools]

    def dispose() -> None:
        for d in reversed(disposers):
            try:
                d()
            except Exception as e:            # 单个注销失败不影响其余
                logger.debug("[plugins] 注销工具失败: %s", e)

    return dispose

