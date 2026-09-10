"""productivity_tools 插件：4 个生产力工具（计算/翻译/提醒/天气）

外部能力（翻译 / 天气）由 `productivity_providers` 插件注册的服务提供，
未注入时工具自身会给出友好提示。开关：`agent.tools.productivity`

## 提醒：本插件还负责"让它真的响"

`ReminderTool` 提供 `due_now()`，但**没有任何东西会调用它** ——
P2 起"设个提醒"就一直是个只回答不兑现的功能。本插件因此额外负责：

1. 造工具时把 `on_due` 接上（到点 → 发 `reminder.due` 总线事件，UI 负责出声）
2. 起一个 `ReminderScheduler` 线程周期性调用 `due_now()`
3. 通过返回的 disposer 在卸载/退出时停掉该线程（不留悬挂线程）

调度器同时注册为 `SVC_REMINDER_SCHEDULER` 服务，便于 `/status` 与测试观测。
关掉它：`agent.productivity.reminder_scheduler: false`
（此时工具仍能"设置提醒"但不会响 —— 只有明确不想让它响才该这么配）。
"""

import logging
from typing import Callable, Optional

from agent.plugins import (
    SVC_BUS,
    SVC_CONFIG,
    SVC_REMINDER,
    SVC_REMINDER_SCHEDULER,
    SVC_TRANSLATE,
    SVC_WEATHER,
    register_tools,
)
from agent.providers.productivity.reminder_scheduler import ReminderScheduler
from agent.tools.productivity_tools import ReminderTool, all_productivity_tools

logger = logging.getLogger(__name__)


def _build_on_due(ctx) -> Callable[[str, str], None]:
    """构造到点回调：发总线事件（生产路径）+ 兼容外部注入的回调"""
    bus = ctx.use_or(SVC_BUS)
    external = ctx.use_or(SVC_REMINDER)

    def on_due(reminder_id: str, what: str) -> None:
        if bus is not None:
            try:
                from core.kernel.events import EventTypes
                bus.emit(
                    EventTypes.REMINDER_DUE,
                    reminder_id=reminder_id,
                    what=what,
                    text=f"提醒你{what}",
                )
            except Exception as e:
                logger.warning("[productivity_tools] 提醒事件发送失败: %s", e)
        if external is not None:
            try:
                external(reminder_id, what)
            except Exception as e:
                logger.warning("[productivity_tools] 外部提醒回调失败: %s", e)

    return on_due


def setup(ctx, **config) -> Optional[Callable[[], None]]:
    """装配生产力工具（并在需要时启动提醒调度器）"""
    cfg = ctx.use(SVC_CONFIG)
    if not cfg.tools_productivity:
        logger.info("[productivity_tools_plugin] 已按配置关闭")
        return None

    tools = all_productivity_tools(
        translate_func=ctx.use_or(SVC_TRANSLATE),
        weather_func=ctx.use_or(SVC_WEATHER),
        on_reminder_due=_build_on_due(ctx),
    )

    # 提醒调度器要拿到**工具实例本身**（`due_now()` 在它身上），
    # 所以从注册好的这组工具里挑出来，而不是另造一个
    reminder = next((t for t in tools if isinstance(t, ReminderTool)), None)

    scheduler = None
    if reminder is not None and getattr(cfg, "productivity_reminder_scheduler", True):
        scheduler = ReminderScheduler(tool=reminder)
        scheduler.start()
        ctx.provide(SVC_REMINDER_SCHEDULER, scheduler)

    logger.info("[productivity_tools_plugin] 注册 %d 个工具（提醒调度=%s）",
                len(tools), "开" if scheduler is not None else "关")

    dispose_tools = register_tools(ctx, tools)

    def dispose() -> None:
        if scheduler is not None:
            scheduler.stop()
        if dispose_tools is not None:
            dispose_tools()

    return dispose
