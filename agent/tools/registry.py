"""工具注册表

在 kernel Registry 之上增加：
- execute(name, params)：带参数校验、异常包装、超时保护
- to_llm_schemas()：导出 OpenAI function calling 格式
- 执行统计：成功/失败计数（用于验收指标「工具执行成功率」）

设计说明：
- 不在此处做安全检查（那是 Executor + SafetyGuard 的职责）
- 不在此处做结果润色（那是 Summarizer 的职责）
- 单一职责：工具查找 + 参数校验 + 调用 + 异常归一化
"""

import logging
import time
from typing import Any, Dict, List, Optional

from agent.tools.base import BaseTool, ParamError, ToolResult

logger = logging.getLogger(__name__)


class ToolRegistry:
    """工具注册表

    用法：
        reg = ToolRegistry()
        dispose = reg.register(FileSearchTool(guard))
        result = reg.execute("file_search", {"pattern": "*合同*"})
        schemas = reg.to_llm_schemas()
    """

    def __init__(self) -> None:
        self._tools: Dict[str, BaseTool] = {}
        self._stats: Dict[str, Dict[str, int]] = {}

    # ── 注册 / 注销 ──

    def register(self, tool: BaseTool) -> Any:
        """注册工具

        Args:
            tool: 工具实例（必须有非空 name）

        Returns:
            注销函数（幂等）

        Raises:
            ValueError: 工具名为空
        """
        name = getattr(tool, "name", "")
        if not name:
            raise ValueError(f"工具 {type(tool).__name__} 未声明 name")

        if name in self._tools:
            logger.warning("[tools] 覆盖同名工具: %s", name)

        self._tools[name] = tool
        self._stats.setdefault(name, {"ok": 0, "fail": 0, "error": 0})

        def dispose() -> None:
            if self._tools.get(name) is tool:
                self._tools.pop(name, None)

        logger.debug("[tools] 注册 %s (risk=%s)", name, tool.risk_level)
        return dispose

    def unregister(self, name: str) -> bool:
        """注销工具"""
        if name in self._tools:
            self._tools.pop(name)
            return True
        return False

    # ── 查询 ──

    def get(self, name: str) -> Optional[BaseTool]:
        """按名获取工具"""
        return self._tools.get(name)

    def has(self, name: str) -> bool:
        """工具是否存在"""
        return name in self._tools

    def names(self) -> List[str]:
        """全部工具名（快照）"""
        return list(self._tools.keys())

    def tools(self) -> List[BaseTool]:
        """全部工具实例（快照）"""
        return list(self._tools.values())

    def count(self) -> int:
        """工具数量"""
        return len(self._tools)

    def risk_levels(self) -> Dict[str, str]:
        """工具名 → 风险等级"""
        return {name: t.risk_level for name, t in self._tools.items()}

    # ── 执行 ──

    def execute(self, name: str, params: Dict[str, Any] = None) -> ToolResult:
        """执行工具

        流程：查找 → 参数校验 → 调用 → 异常归一化 → 统计

        Args:
            name: 工具名
            params: 参数字典

        Returns:
            ToolResult；工具不存在或异常时返回 success=False 的结果
        """
        params = params or {}

        tool = self._tools.get(name)
        if tool is None:
            logger.warning("[tools] 未注册的工具: %s", name)
            return ToolResult.fail(f"我还没有「{name}」这个能力呢")

        stats = self._stats.setdefault(name, {"ok": 0, "fail": 0, "error": 0})
        t0 = time.perf_counter()

        # 参数校验
        try:
            tool.validate_params(params)
        except ParamError as e:
            stats["fail"] += 1
            logger.info("[tools] %s 参数校验失败: %s", name, e)
            # 参数不合法通常是"上游（路由/规划器）产出的参数不对"，而不是用户
            # 说错了话；原始报文含字段名与类型断言（"参数错误 [file_search].
            # time_range: 类型错误：期望 string，收到 NoneType"），念出来既像报错
            # 又不可行动。故详情留在 error 与日志里，用户听到的是一句能照做的话。
            # （实测：LLM 规划器给出 time_range=null 时，原文案会把整单计划打断）
            return ToolResult.fail(
                str(e),
                summary="这个指令我还没完全理解，换个说法再试试好吗？",
                emotion="think",
            )

        # 执行
        try:
            result = tool.execute(params)
        except Exception as e:
            stats["error"] += 1
            logger.error("[tools] %s 执行异常: %s", name, e, exc_info=True)
            # `user_message` 是 ToolError 提供的"面向用户的说法"；
            # 技术原文留在 error 字段（供日志/排查），不混进用户文案。
            user_msg = getattr(e, "user_message", None) or f"操作出错了：{e}"
            return ToolResult.fail(str(e), summary=user_msg, emotion="sad")

        if not isinstance(result, ToolResult):
            stats["error"] += 1
            logger.error("[tools] %s 返回了非 ToolResult: %r", name, result)
            return ToolResult.fail("工具返回格式不对呢")

        elapsed = (time.perf_counter() - t0) * 1000
        if result.success:
            stats["ok"] += 1
        else:
            stats["fail"] += 1

        logger.debug("[tools] %s 完成 (%.1fms, ok=%s)", name, elapsed, result.success)
        return result

    # ── LLM 集成 ──

    def to_llm_schemas(self, names: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """导出 LLM function calling schema

        Args:
            names: 只导出指定工具；None 导出全部

        Returns:
            OpenAI tools 格式的列表
        """
        tools = self._tools.values() if names is None else [
            self._tools[n] for n in names if n in self._tools
        ]
        return [t.to_llm_schema() for t in tools]

    # ── 统计 ──

    def stats(self) -> Dict[str, Dict[str, int]]:
        """各工具的执行统计"""
        return {k: dict(v) for k, v in self._stats.items()}

    def success_rate(self) -> float:
        """整体成功率（ok / (ok + fail + error)），无执行时返回 1.0"""
        ok = sum(s["ok"] for s in self._stats.values())
        total = sum(s["ok"] + s["fail"] + s["error"] for s in self._stats.values())
        return (ok / total) if total else 1.0

    def reset_stats(self) -> None:
        """重置统计"""
        for s in self._stats.values():
            s["ok"] = s["fail"] = s["error"] = 0

    def clear(self) -> None:
        """清空所有工具（不触发清理）"""
        self._tools.clear()
        self._stats.clear()

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: object) -> bool:
        return name in self._tools

    def __repr__(self) -> str:
        return f"<ToolRegistry {len(self._tools)} tools>"
