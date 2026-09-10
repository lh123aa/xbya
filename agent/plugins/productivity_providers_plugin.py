"""productivity_providers 插件：给生产力工具接上**真实**后端

## 这个插件修的是什么

`productivity_tools_plugin` 从 P2 起就用
`ctx.use_or(SVC_TRANSLATE)` / `SVC_WEATHER` / `SVC_REMINDER` 取注入，
但**全项目没有任何一处 provide 这三个服务** —— 于是生产环境里：

| 工具 | 之前对用户说 | 修复后 |
|------|-------------|--------|
| `translate` | "我这边还没接上翻译能力呢" | 真的翻译（复用项目 LLM） |
| `weather` | "我这边还没接上天气服务呢" | 真的查天气（Open-Meteo，免密钥） |
| `reminder` | "好的，30分钟后我会提醒你" → **永远不响** | 到点真的播报 |

第三个最严重：它不是"能力缺失"，而是**对用户撒谎**。
根因是 `ReminderTool.due_now()` 写好了却没有任何调用者。

## 装配顺序

本插件的 `requires` 让它在 `productivity_tools` **之前**加载，
这样工具构造时 `use_or` 才拿得到实现。清单顺序在 `config.yaml`
的 `agent.plugins` 里，加载器按 Kahn 稳定排序。

## 可关闭 / 可替换

- `agent.productivity.translate: false` → 不注册翻译，工具退回友好提示
- `agent.productivity.weather: false` → 同上
- `agent.weather.default_city` → 用户没说城市时的默认城市
- `agent.productivity.reminder_scheduler: false` → 不装调度器
  （工具仍可设提醒，但不会响 —— 只有在明确不想让它响时才该这么配）
"""

import logging
from typing import Callable, Optional

from agent.plugins import (
    SVC_BUS,
    SVC_CONFIG,
    SVC_LLM_ONCE,
    SVC_REMINDER_SCHEDULER,
    SVC_TRANSLATE,
    SVC_WEATHER,
)
from agent.providers.productivity.llm_translate import make_llm_translate
from agent.providers.productivity.open_meteo import make_open_meteo_weather

logger = logging.getLogger(__name__)


def setup(ctx, **config) -> Optional[Callable[[], None]]:
    """注册 translate / weather 两个能力服务

    只在**没人提供**时才注册：`build_agent_stack(translate_func=...)` 这类
    显式注入算覆盖，调用方说了算；本插件只是默认提供者。
    """
    cfg = ctx.use(SVC_CONFIG)

    if getattr(cfg, "productivity_translate", True):
        if ctx.has(SVC_TRANSLATE):
            logger.info("[productivity_providers] translate 已有显式注入，不覆盖")
        else:
            translate = make_llm_translate(ctx.use_or(SVC_LLM_ONCE))
            if translate is None:
                logger.info("[productivity_providers] 无 LLM，translate 保持未接入")
            else:
                ctx.provide(SVC_TRANSLATE, translate)
                logger.info("[productivity_providers] translate 已接入（复用项目 LLM）")
    else:
        logger.info("[productivity_providers] translate 已按配置关闭")

    if getattr(cfg, "productivity_weather", True):
        if ctx.has(SVC_WEATHER):
            logger.info("[productivity_providers] weather 已有显式注入，不覆盖")
        else:
            default_city = str(getattr(cfg, "weather_default_city", "") or "")
            ctx.provide(SVC_WEATHER, make_open_meteo_weather(default_city))
            logger.info(
                "[productivity_providers] weather 已接入（Open-Meteo，默认城市=%r）",
                default_city)
    else:
        logger.info("[productivity_providers] weather 已按配置关闭")

    return None            # 无后台资源；调度器由 tools 插件负责生命周期
