# -*- coding: utf-8 -*-
"""插件参数的**唯一取法** —— 修掉「两条加载路径取参数不一致」（P5-B2）

## 问题

同一个 ASR 插件有**两条加载路径**：

| 路径 | 代码 | 是否传 `params` |
|------|------|----------------|
| 主应用启动 | `core/app.py::_load_plugins` | ✅ `params=plugin_params` |
| 语音服务 | `services/voice_service.py::initialize` | ❌ 只传引擎名 |

而 `PluginLoader.load(name, params=None)` 在 `params` 为空时走
`plugin_class()` —— **不带任何配置**。于是 `config.yaml` 里的

```yaml
plugins:
  asr:
    params:
      model_size: medium          # ← 静默失效
      initial_prompt: "算了 不用了 停止 取消 确定 确认"   # ← 静默失效
```

在语音服务那条路径上**完全没被读到**，插件退回自己 `__init__` 的默认值
（`model_size="base"`、`initial_prompt=""`）。

这能解释一条一直没讲通的旧记录：**"config 写 medium，语音回环实际用的是 small"**
（见 P4-C2）。回环脚本走的是语音服务这条路径 —— 它拿到的从来就不是 config 里的值。

## 为什么抽成函数而不是两边各写一遍

"从 config 里取某插件的初始化参数"是一条**判据**，不是两处巧合。
本项目已经因为"同一判据写两份"吃过亏（`plugins/llm/tool_schemas.py` 的由来、以及
`measure_asr_stimulus.py` 自己复刻确认语匹配导致假红）。两份判据一定会漂移，
而漂移的表现恰好是"某一条路径悄悄不工作"——正是这里发生的事。

## 约定

- `plugins.<type>.params` 是参数的唯一来源；
- 唯一的特例：`llm` + `openai_api` 还要并上 `plugins.llm.cloud`
  （云端连接信息单独放一层的既有约定，**cloud 覆盖 params**）；
- 返回**新字典**（可安全被调用方继续改），且永远不是 `None`。
"""

from __future__ import annotations

from typing import Any, Dict


def plugin_params(config_manager: Any, plugin_type: str, engine_name: str = "") -> Dict[str, Any]:
    """取出某个插件应有的初始化参数（两条加载路径共用）

    Args:
        config_manager: 具备 `get(key, default)` 的配置管理器
        plugin_type: `asr` / `tts` / `llm` / …
        engine_name: 引擎名；只有 `llm` + `openai_api` 这个组合有额外一层

    Returns:
        参数字典（**永不为 None**）
    """
    params: Dict[str, Any] = dict(
        config_manager.get(f"plugins.{plugin_type}.params") or {}
    )

    if plugin_type == "llm" and engine_name == "openai_api":
        cloud = config_manager.get("plugins.llm.cloud") or {}
        params.update(cloud)

    return params
