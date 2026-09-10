"""记忆工具集（DSH Consumer 角色）

三个工具，消费 `MemoryService`（默认实现 `HybridMemory`）：

| 工具 | 作用 | 风险 |
|------|------|------|
| `memory_remember` | 记住一句偏好/事实 | low |
| `memory_recall`   | 检索记忆（空查询 = 列出最近几条） | low |
| `memory_forget`   | 删除一条 / 全部记忆 | **medium**（走确认通道） |

设计约定：
- **只做"说法"不做"判断"**：这一条该不该记、算不算敏感，由 Provider 决定；
  工具层只把结果翻译成人话（`seams/memory.py` 的职责边界）
- 记忆服务缺失（装配未启用 P3）时，三个工具都返回友好失败而不是抛异常 ——
  工具集被注册进注册表就不能成为崩溃来源
- `memory_forget` 是破坏性操作，`risk_level=medium` 让管线在真正删除前
  走一次确认（`preview()` 说明将删掉什么）
"""

import logging
from typing import Any, Dict, List, Optional

from agent.seams.memory import (
    DEFAULT_RECALL_LIMIT,
    MemoryItem,
    MemoryService,
    coerce_kind,
    sensitive_reason,
    truncate_text,
)
from agent.tools.base import BaseTool, ToolResult

logger = logging.getLogger(__name__)

#: 单次检索条数上限（再多也没有播报价值，反而拖慢管线）
MAX_RECALL_LIMIT = 20

#: 摘要里最多列举几条正文
_SUMMARY_ITEMS = 3

#: 记忆类别枚举（供 LLM 选择）
_KIND_ENUM = ["fact", "episode", "entity"]


def _memory_missing() -> ToolResult:
    """记忆服务未接入时的统一回复"""
    return ToolResult.fail("我这边还没接上记忆能力呢", emotion="sad")


# ══════════════════════════════════════════════════
#  1. memory_remember
# ══════════════════════════════════════════════════


class MemoryRememberTool(BaseTool):
    """记住一条信息（偏好 / 事实 / 实体）"""

    name = "memory_remember"
    description = "记住用户说的一件稳定的事，比如偏好、习惯、常用目录，供以后使用"
    risk_level = "low"
    timeout = 5
    params_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "要记住的内容，如「喜欢用 Chrome」",
            },
            "kind": {
                "type": "string",
                "enum": _KIND_ENUM,
                "description": "记忆类别：fact=偏好事实（默认）、episode=做过什么、entity=常用名词",
            },
        },
        "required": ["text"],
    }

    def __init__(self, memory: Optional[MemoryService] = None) -> None:
        """
        Args:
            memory: 记忆能力；None 时工具返回友好提示（装配未启用记忆）
        """
        self._memory = memory

    def execute(self, params: Dict[str, Any]) -> ToolResult:
        text = truncate_text(params.get("text"))
        if not text:
            return ToolResult.fail("你想让我记住什么呀？", emotion="think")
        if self._memory is None:
            return _memory_missing()

        # 工具层先判一次敏感：这样能给出"为什么没记住"的具体原因，
        # 而 Provider 那一层仍会独立再判一次（宁可少记，不可泄露）
        reason = sensitive_reason(text)
        if reason:
            message = f"这个我不方便记哦，里面有{reason}这类信息"
            return ToolResult.fail(message, emotion="think")

        kind = coerce_kind(params.get("kind"))
        item_id = self._memory.remember(
            text, kind=kind, metadata={"source": self.name}
        )
        if not item_id:
            return ToolResult.fail("这个我暂时记不下来呢，稍后再试试~", emotion="sad")

        return ToolResult.ok(
            data={"item_id": item_id, "text": text, "kind": kind.value},
            summary=f"记住啦：{text}",
            emotion="happy",
        )


# ══════════════════════════════════════════════════
#  2. memory_recall
# ══════════════════════════════════════════════════


class MemoryRecallTool(BaseTool):
    """检索长期记忆（不带查询时列出最近几条）"""

    name = "memory_recall"
    description = "回忆之前记住的事情，比如用户的偏好、常用目录、上次做过什么"
    risk_level = "low"
    timeout = 5
    params_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "要回忆什么；留空则列出最近记住的几条",
            },
            "kind": {
                "type": "string",
                "enum": _KIND_ENUM,
                "description": "只看某一类记忆；留空则不限",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": MAX_RECALL_LIMIT,
                "description": f"最多返回几条（默认 {DEFAULT_RECALL_LIMIT}）",
            },
        },
    }

    def __init__(self, memory: Optional[MemoryService] = None) -> None:
        """
        Args:
            memory: 记忆能力；None 时工具返回友好提示
        """
        self._memory = memory

    def execute(self, params: Dict[str, Any]) -> ToolResult:
        if self._memory is None:
            return _memory_missing()

        limit = self._limit(params)
        raw_kind = params.get("kind")
        kind = coerce_kind(raw_kind) if raw_kind else None
        query = truncate_text(params.get("query"))

        # 有查询走混合检索，没查询列最近几条 —— 空查询按约定返回空列表，
        # 直接调 recall("") 会永远得到"没有记忆"，那不是用户想听的
        items = (
            self._memory.recall(query, kind=kind, limit=limit)
            if query
            else self._recent(limit, kind)
        )

        if not items:
            return ToolResult.empty("我这边没有相关的记忆呢", emotion="think")

        return ToolResult.ok(
            data=[self._to_row(item) for item in items],
            summary=self._summary(items),
            emotion="talk",
        )

    @staticmethod
    def _limit(params: Dict[str, Any]) -> int:
        """解析 limit 参数（非法值退回默认；并夹在 [1, MAX_RECALL_LIMIT]）"""
        raw = params.get("limit")
        try:
            value = int(raw) if raw is not None else DEFAULT_RECALL_LIMIT
        except (TypeError, ValueError):
            value = DEFAULT_RECALL_LIMIT
        return max(1, min(value, MAX_RECALL_LIMIT))

    def _recent(self, limit: int, kind: Optional[Any]) -> List[MemoryItem]:
        """无查询时的"最近若干条"

        `recent()` 不在 `MemoryService` 契约里（它是 Provider 的补充能力），
        因此按能力探测使用：别的实现没有它就退回空列表，而不是报错。
        """
        fetch = getattr(self._memory, "recent", None)
        if fetch is None:
            return []
        return list(fetch(limit=limit, kind=kind))

    @staticmethod
    def _to_row(item: MemoryItem) -> Dict[str, Any]:
        """条目 → 结构化数据（score 保留 4 位，避免给 LLM 一长串浮点噪声）"""
        return {
            "text": item.text,
            "kind": item.kind.value,
            "score": round(float(item.score), 4),
        }

    @staticmethod
    def _summary(items: List[MemoryItem]) -> str:
        """拼播报摘要：``我记得 3 条：A、B、C``"""
        texts = [item.text for item in items if item.text]
        head = "、".join(texts[:_SUMMARY_ITEMS])
        if len(texts) > _SUMMARY_ITEMS:
            head += f" 等 {len(texts)} 条"
        return f"我记得 {len(items)} 条：{head}"


# ══════════════════════════════════════════════════
#  3. memory_forget
# ══════════════════════════════════════════════════


class MemoryForgetTool(BaseTool):
    """删除记忆（破坏性操作，走确认通道）"""

    name = "memory_forget"
    description = "忘掉某一条记忆，或者忘掉全部记忆"
    #: 破坏性操作：medium 让管线在真正删除前先确认（见 BaseTool.confirm_required）
    risk_level = "medium"
    timeout = 5
    params_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "item_id": {
                "type": "string",
                "description": "要忘掉的那条记忆的编号（由 memory_recall 返回）",
            },
            "all": {
                "type": "boolean",
                "description": "true 表示忘掉全部记忆，需谨慎使用",
            },
        },
        # 不把 item_id 写进 required：schema 子集不支持 "item_id 或 all 二选一"，
        # 写死会让 {"all": true} 在校验阶段就被拒；改为在 execute 里判。
    }

    def __init__(self, memory: Optional[MemoryService] = None) -> None:
        """
        Args:
            memory: 记忆能力；None 时工具返回友好提示
        """
        self._memory = memory

    def execute(self, params: Dict[str, Any]) -> ToolResult:
        if self._memory is None:
            return _memory_missing()

        if params.get("all"):
            removed = self._memory.forget_all()
            if removed <= 0:
                return ToolResult.empty("记忆库本来就是空的呢", emotion="think")
            return ToolResult.ok(
                data={"removed": removed},
                summary=f"好的，已经忘掉 {removed} 条记忆啦",
                emotion="happy",
            )

        item_id = str(params.get("item_id") or "").strip()
        if not item_id:
            return ToolResult.fail(
                "要忘掉哪一条呀？给我一个编号，或者说「全部忘掉」", emotion="think"
            )

        # 先取正文再删：删完就查不到"删的是什么"了，摘要也就说不清楚
        text = self._describe(item_id)
        if not self._memory.forget(item_id):
            return ToolResult.fail("我这儿没找到这条记忆呢", emotion="think")

        summary = f"好的，忘掉「{text}」啦" if text else "好的，这条记忆我忘掉了"
        return ToolResult.ok(data={"item_id": item_id}, summary=summary, emotion="happy")

    def preview(self, params: Dict[str, Any]) -> str:
        """确认前说明将删除什么（只读，无副作用）

        Returns:
            预览文案；空串表示无从预览（既没给编号也没说"全部"）
        """
        if self._memory is None:
            return ""

        if params.get("all"):
            total = self._memory.count()
            if total <= 0:
                return "记忆库本来就是空的"
            return f"将删除全部 {total} 条记忆{self._sample()}"

        item_id = str(params.get("item_id") or "").strip()
        if not item_id:
            return ""

        text = self._describe(item_id)
        if text:
            return f"将删除这条记忆：「{text}」"
        return f"将删除这条记忆（编号 {item_id}）"

    def _describe(self, item_id: str) -> str:
        """取条目正文用于播报/预览

        `get()` 不在 `MemoryService` 契约里（Provider 的补充能力），
        按能力探测使用；拿不到就返回空串，摘要退回不带正文的说法。
        """
        getter = getattr(self._memory, "get", None)
        if getter is None:
            return ""
        item = getter(item_id)
        return item.text if item is not None else ""

    def _sample(self) -> str:
        """给"全部删除"的预览附几个例子，让用户看清范围"""
        fetch = getattr(self._memory, "recent", None)
        if fetch is None:
            return ""
        texts = [item.text for item in fetch(limit=_SUMMARY_ITEMS) if item.text]
        if not texts:
            return ""
        return "（如「" + "」「".join(texts) + "」）"


# ══════════════════════════════════════════════════
#  注册辅助
# ══════════════════════════════════════════════════


def all_memory_tools(memory: Optional[MemoryService] = None) -> List[BaseTool]:
    """构造全部记忆工具实例

    Args:
        memory: 记忆能力；None 时三个工具都会给出友好失败提示

    Returns:
        BaseTool 列表（remember / recall / forget）
    """
    return [
        MemoryRememberTool(memory),
        MemoryRecallTool(memory),
        MemoryForgetTool(memory),
    ]
