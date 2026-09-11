# -*- coding: utf-8 -*-
"""工具 schema 形制归一 —— 「两种同名不同形制」缺陷（D16）的唯一收敛点

## 问题（D16 登记的那个坑）

项目里同时存在两种叫"llm schema"的东西，**名字一样、形制不同**：

| 来源 | 形制 | 例子 |
|------|------|------|
| `LLMRouter.TOOL_SCHEMAS`（手写常量） | **扁平** | `{"name": "file_search", "description": …, "parameters": {…}}` |
| `ToolRegistry.to_llm_schemas()`（经 `BaseTool.to_llm_schema()`） | **已包好** | `{"type": "function", "function": {"name": …, …}}` |

而 OpenAI 兼容端点要的是**已包好**的那种。两个插件原先都这么写：

```python
"tools": [{"type": "function", "function": t} for t in tools]
```

把注册表 schema 递进来就会变成：

```json
{"type": "function", "function": {"type": "function", "function": {"name": "file_search", …}}}
```

⇒ 请求体里**根本没有 `function.name`**。端点的反应是 400/空 tool_calls，
插件返回 `None`，`route_with_tools` 把 `None` 理解成"模型这次用文字回答"，
于是**整条 LLM 路由兜底静默失效**，而日志里除了一个状态码什么都没有。

P4-B4 的免费模型矩阵探针正是这么踩的：六个免费模型的第 ③ 项被这个形制问题
**误判成"全都不支持 function calling"**（结论错在测量侧，不在模型侧）。

## 这个模块做什么

**两种形制都收**，并归一成端点要的那一种。这不是"宽容"，是**消除歧义**：
包好的一层被显式剥掉，而不是靠巧合让 `function.name` 恰好存在。

**但畸形输入一律显式报错**（`ToolSchemaError`），绝不静默变出一个"没有名字的工具"：

| 输入 | 结果 |
|------|------|
| 扁平且带 `name` | ✅ 包一层 |
| 已包好、内层带 `name` | ✅ 剥掉多余层后原样返回 |
| 两层都带 `name`（畸形套娃） | ❌ 报错并说明 |
| 没有 `name` / `name` 非字符串 / 空串 | ❌ 报错并说明 |

## 为什么单独一个模块

与 D16 同族的问题在这个项目里已经出现过两次（D14 两个 EventBus、D16 两种 schema），
共同点是"**同一个名字在两处含义不同**"。治理方式也一样：**收敛成一个点**，
`openai_api` 与 `openrouter` 两个插件都 import 它 —— 多写一份判据就会漂移，
而漂移的表现恰好是"某个插件悄悄不工作"。
"""

from __future__ import annotations

from typing import Any, Dict, List, Sequence


class ToolSchemaError(ValueError):
    """工具 schema 形制不合法（缺名字 / 套娃 / 不是 dict）

    继承 `ValueError` 而不是自定义基类：调用方若只写 `except Exception`
    （`core/app.py::route_with_tools` 就是）也能兜住，不会把整个应用带崩。
    """


def _name_of(raw: Any) -> str:
    """取出一个工具条目的名字；畸形就抛 `ToolSchemaError`（不返回空串！）

    **为什么不能返回空串**：空名字正是"静默失败"的载体 —— 请求体照样发出去、
    端点照样返回 400、插件照样返回 `None`，而上层看到的只是"模型没选工具"。
    宁可在这里炸，也不要让一个匿名工具走到网络上。
    """
    if not isinstance(raw, dict):
        raise ToolSchemaError(
            f"工具条目必须是 dict，收到 {type(raw).__name__}：{raw!r}"
        )
    name = raw.get("name")
    if name is None:
        raise ToolSchemaError(
            f"工具条目缺少 name：{ {k: type(v).__name__ for k, v in raw.items()} }"
            f"（若这是 ToolRegistry.to_llm_schemas() 的输出，它已经是 "
            f'{{"type":"function","function":{{…}}}} 形制，不该再包一层）'
        )
    if not isinstance(name, str) or not name.strip():
        raise ToolSchemaError(f"工具的 name 必须是非空字符串，收到 {name!r}")
    return name.strip()


def unwrap(raw: Any) -> Dict[str, Any]:
    """把一个工具条目归一成**扁平**形制（`{name, description, parameters}`）

    Raises:
        ToolSchemaError: 条目不是 dict、套娃、或缺名字
    """
    if not isinstance(raw, dict):
        raise ToolSchemaError(
            f"工具条目必须是 dict，收到 {type(raw).__name__}：{raw!r}"
        )

    flat_name = raw.get("name")
    inner = raw.get("function")

    if isinstance(inner, dict):
        # 已包好（或被包了两层）—— 内层才是真身
        inner_name = inner.get("name")
        if flat_name is not None and inner_name is not None and flat_name != inner_name:
            # 两层都有名字且不一致：**不能猜**。猜错的代价是调用一个用户没要求的工具
            raise ToolSchemaError(
                f"工具条目两层都带 name 且不一致（外层 {flat_name!r} / 内层 "
                f"{inner_name!r}）；请只保留一层"
            )
        if flat_name is not None and inner_name is None:
            raise ToolSchemaError(
                f"工具条目形制混用：外层有 name={flat_name!r} 而内层没有 name"
            )
        return unwrap(inner)          # 递归剥掉多余层，尾层没有 function

    _name_of(raw)                     # 校验 name（畸形直接抛）
    return raw


def to_openai_tools(tools: Sequence[Any]) -> List[Dict[str, Any]]:
    """归一 + 包成 OpenAI 兼容端点要的 `tools` 数组

    Args:
        tools: 扁平或已包好的工具 schema 序列（两种混用也允许，逐条判定）

    Returns:
        `[{"type": "function", "function": {name, description, parameters}}, …]`

    Raises:
        ToolSchemaError: 任一条目不合法。**整批拒绝，不做部分保留** ——
            少一个工具会让模型"看不见"某个能力，而它不会告诉你。
    """
    if not tools:
        return []

    bad: List[str] = []
    out: List[Dict[str, Any]] = []
    for raw in tools:
        try:
            out.append({"type": "function", "function": unwrap(raw)})
        except ToolSchemaError as e:
            bad.append(str(e))

    if bad:
        raise ToolSchemaError(
            f"{len(bad)}/{len(tools)} 条工具 schema 形制不合法，整批拒绝："
            + "；".join(bad[:3])
            + ("…" if len(bad) > 3 else "")
        )
    return out
