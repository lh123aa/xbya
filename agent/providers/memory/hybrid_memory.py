"""长期记忆的默认实现（Service Provider）—— 把 RecallStore 包成 MemoryService

分层：`RecallStore` 管"怎么存怎么找"，本类管"什么该记、什么该拒绝、怎么说话"。

## 三条硬约定（见 `seams/memory.py`）

1. **永不抛异常**：全部方法用 `never_raises` 兜底。记忆是增强能力，
   检索失败应当表现为"想不起来"，而不是让用户的操作失败。
2. **敏感信息一律拒绝入库**：`remember()` 第一件事就是 `sensitive_reason()`，
   命中直接返回 None 并记 warning（**日志里只写命中的类别，不写原文** ——
   否则密码会从记忆库漏进日志文件，等于换个地方泄露）。
3. **构造廉价**：本类不加载模型、不建索引，重资源都在 store / embedder 内部懒加载。
"""

import logging
import time
from typing import Any, Dict, List, Optional

from agent.providers.memory.recall_store import (
    RRF_K,
    RecallStore,
    never_raises,
)
from agent.seams.embedder import EmbedderService
from agent.seams.memory import (
    DEFAULT_HINT_LIMIT,
    DEFAULT_RECALL_LIMIT,
    MemoryItem,
    MemoryKind,
    MemoryService,
    coerce_kind,
    format_recall,
    new_item_id,
    sensitive_reason,
    truncate_text,
)

logger = logging.getLogger(__name__)

#: `hint_for()` 的分数门槛（RRF 尺度）
#:
#: RRF 单路第 1 名得 `1/(k+1) ≈ 0.0164`，两路都第 1 名约 0.0328。
#: 提示会参与路由判断，**宁可不给也不能给错**：一条噪声提示会把整句话
#: 路由到错误的能力上。故门槛定在"至少在某一路排进前 2 名"（`1/(k+2)`），
#: 即近乎榜首的相关度 —— 只命中一个泛化单字的条目达不到这个分。
DEFAULT_HINT_MIN_SCORE = 1.0 / (RRF_K + 2)

#: 情景记忆文本里动作短语的长度上限（`action` 应是可播报的短语，不是整段参数）
_MAX_ACTION_LEN = 64


class HybridMemory(MemoryService):
    """长期记忆默认实现（混合检索）

    用法：
        store = RecallStore("data/agent_memory.db", embedder=emb)
        mem = HybridMemory(store, embedder=emb)
        item_id = mem.remember("喜欢用 Chrome", MemoryKind.FACT)
        mem.recall("浏览器偏好")
        mem.hint_for("帮我打开浏览器")
    """

    capability_name = "memory"
    provider_name = "hybrid"

    def __init__(
        self,
        store: RecallStore,
        embedder: Optional[EmbedderService] = None,
        episode_enabled: bool = True,
        hint_enabled: bool = True,
        hint_min_score: float = DEFAULT_HINT_MIN_SCORE,
        hint_limit: int = DEFAULT_HINT_LIMIT,
    ) -> None:
        """
        Args:
            store: 记忆存储（必备；本类不自己建库，便于测试注入）
            embedder: 嵌入器；None 时退回复用 store 的嵌入器
            episode_enabled: 是否记录情景记忆
            hint_enabled: 是否给路由提供偏好提示
            hint_min_score: `hint_for()` 的分数门槛
            hint_limit: `hint_for()` 最多拼几条
        """
        self._store = store
        # store 通常已持有同一个嵌入器（装配层只建一个），复用可避免重复加载模型
        self._embedder = embedder if embedder is not None else store.embedder
        self._episode_enabled = bool(episode_enabled)
        self._hint_enabled = bool(hint_enabled)
        self._hint_min_score = float(hint_min_score)
        self._hint_limit = max(1, int(hint_limit))

        self._remembered = 0
        self._rejected = 0
        self._recall_hits = 0
        self._recall_misses = 0
        self._episodes = 0
        self._hint_hits = 0

    # ══════════════════════════════════════════════
    #  写
    # ══════════════════════════════════════════════

    @never_raises(lambda: None)
    def remember(
        self,
        text: str,
        kind: Any = MemoryKind.FACT,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        """写入一条记忆

        顺序有意为之：**先判敏感、再截断**。截断会把超长文本的尾部丢掉，
        若先截断，一个藏在 600 字之后的密码就会溜进记忆库。

        Args:
            text: 记忆正文（口语化，可直接播报）
            kind: 记忆类别
            metadata: 附加信息

        Returns:
            新条目的 item_id；被拒绝（空文本 / 含敏感信息）或写入失败时返回 None
        """
        raw = "" if text is None else str(text)
        reason = sensitive_reason(raw)
        if reason:
            # 只记类别不记原文：日志同样是不该泄露密码的地方
            logger.warning("[memory] 拒绝记住含「%s」的内容（原文不入日志）", reason)
            self._rejected += 1
            return None

        body = truncate_text(raw)
        if not body:
            logger.debug("[memory] 空文本，未写入")
            return None

        now = time.time()
        item = MemoryItem(
            text=body,
            kind=coerce_kind(kind),
            item_id=new_item_id(),
            metadata=dict(metadata or {}),
            created_at=now,
            last_used_at=now,
        )
        if not self._store.add(item, embedding=self._vectorize(body)):
            logger.warning("[memory] 写入失败（存储不可用？）")
            return None

        self._remembered += 1
        logger.debug("[memory] 已记住 %s [%s]", item.item_id, item.kind.value)
        return item.item_id

    def _vectorize(self, text: str) -> Optional[List[float]]:
        """文本 → 向量；嵌入器缺失/不可用/零向量时返回 None

        返回 None 时该条只参与词法检索 —— 这是设计内的降级，
        不是错误（`hashing` 之外的嵌入器都可能"暂时不可用"）。
        """
        if self._embedder is None or not self._embedder.ready():
            return None
        vector = self._embedder.embed_one(text)
        if not vector or not any(vector):
            return None
        return list(vector)

    @never_raises(lambda: False)
    def forget(self, item_id: str) -> bool:
        """删除一条记忆

        Returns:
            True=确实删掉了一条；False=不存在或删除失败
        """
        return self._store.remove(item_id)

    @never_raises(lambda: 0)
    def forget_all(self, kind: Optional[Any] = None) -> int:
        """清空记忆（可按类别），返回删除条数"""
        return self._store.remove_all(kind)

    # ══════════════════════════════════════════════
    #  读
    # ══════════════════════════════════════════════

    @never_raises(list)
    def recall(
        self,
        query: str,
        kind: Optional[Any] = None,
        limit: int = DEFAULT_RECALL_LIMIT,
    ) -> List[MemoryItem]:
        """按语义/词法混合检索，并刷新命中条目的热度

        Args:
            query: 查询文本
            kind: 限定类别；None 表示不限
            limit: 最多返回条数

        Returns:
            按相关度降序的记忆列表；无命中返回空列表
        """
        items = self._store.search(query, kind=kind, limit=limit)
        if not items:
            self._recall_misses += 1
            return []
        self._recall_hits += 1
        # 命中即算"用过"：热度参与后续容量淘汰，常用的记忆才留得住
        self._store.touch([it.item_id for it in items if it.item_id])
        return items

    @never_raises(str)
    def recall_text(
        self,
        query: str,
        kind: Optional[Any] = None,
        limit: int = DEFAULT_HINT_LIMIT,
    ) -> str:
        """检索并拼成一句可直接播报的提示；无命中返回空串"""
        return format_recall(self.recall(query, kind=kind, limit=limit), limit)

    @never_raises(list)
    def recent(
        self, limit: int = DEFAULT_RECALL_LIMIT, kind: Optional[Any] = None
    ) -> List[MemoryItem]:
        """最近写入的记忆（新的在前）

        给"你还记得什么"这类**无查询**列举用：`recall()` 需要一个查询串，
        而空查询按约定返回空列表。
        """
        return self._store.recent(limit=limit, kind=kind)

    @never_raises(lambda: None)
    def get(self, item_id: str) -> Optional[MemoryItem]:
        """按 ID 取一条记忆（供删除前的预览等场景）；不存在返回 None"""
        return self._store.get(item_id)

    @never_raises(lambda: 0)
    def count(self, kind: Optional[Any] = None) -> int:
        """当前记忆条数（可按类别统计）"""
        return self._store.count(kind)

    # ══════════════════════════════════════════════
    #  情景记忆 / 路由提示
    # ══════════════════════════════════════════════

    @never_raises(lambda: None)
    def record_episode(
        self,
        action: str,
        summary: str,
        params: Optional[Dict[str, Any]] = None,
        success: bool = True,
    ) -> Optional[str]:
        """记录一次工具执行经过（情景记忆）

        **只记成功的操作**：失败的经历写进情景记忆后，会在"上次那个"这类
        指代里被当作"做过的事"召回，反而把用户带偏。
        metadata 里仍保留 `success` 字段，将来若要放开失败记录无需改结构。

        Args:
            action: 动作短语（可播报，如「找合同」；不是工具名）
            summary: 结果摘要（如「找到 3 个文件」）
            params: 调用参数（存进 metadata，便于回溯）
            success: 本次是否成功；False 时不记录

        Returns:
            新条目 ID；未记录（关闭/失败/文本为空）时返回 None
        """
        if not self._episode_enabled or not success:
            return None

        action_text = truncate_text(action, _MAX_ACTION_LEN)
        summary_text = truncate_text(summary)
        if not action_text or not summary_text:
            return None

        item_id = self.remember(
            f"上次让你「{action_text}」，{summary_text}",
            kind=MemoryKind.EPISODE,
            metadata={
                "action": action_text,
                "success": bool(success),
                "params": params if isinstance(params, dict) else {},
            },
        )
        if item_id:
            self._episodes += 1
        return item_id

    @never_raises(str)
    def hint_for(self, text: str) -> str:
        """给路由用的偏好提示（如「你之前说过：喜欢用 Chrome」）；无则空串

        **只查本地已存好的 fact 条目，绝不调 LLM** —— 每句话都要走这条路径，
        一次网络往返就能把感知延迟指标打穿。门槛也刻意偏高（见
        `DEFAULT_HINT_MIN_SCORE`）：宁可少给提示，也不能用噪声把路由带错。
        """
        if not self._hint_enabled or not truncate_text(text):
            return ""

        items = self._store.search(
            text, kind=MemoryKind.FACT, limit=self._hint_limit
        )
        picked = [
            it.text for it in items if it.text and it.score >= self._hint_min_score
        ]
        if not picked:
            return ""

        self._hint_hits += 1
        return "你之前说过：" + "、".join(picked)

    # ══════════════════════════════════════════════
    #  状态
    # ══════════════════════════════════════════════

    @never_raises(dict)
    def stats(self) -> Dict[str, Any]:
        """状态快照：存储统计（含 count / by_kind / vector_backend / degraded 等）与本层计数"""
        merged = dict(self._store.stats() or {})
        merged.update(
            {
                "provider": self.provider_name,
                "remembered": self._remembered,
                "rejected_sensitive": self._rejected,
                "recall_hits": self._recall_hits,
                "recall_misses": self._recall_misses,
                "episodes": self._episodes,
                "hint_hits": self._hint_hits,
                "episode_enabled": self._episode_enabled,
                "hint_enabled": self._hint_enabled,
            }
        )
        return merged

    @property
    def store(self) -> RecallStore:
        """底层存储（供装配层诊断与测试观察）"""
        return self._store

    @never_raises(lambda: None)
    def close(self) -> None:
        """关闭底层存储；可重复调用

        **不关嵌入器**：嵌入器由装配层持有并作为独立服务注册，
        可能被其它消费者共享 —— 记忆关闭不该顺手卸掉别人的模型。
        """
        self._store.close()

    def __repr__(self) -> str:
        return (
            f"<HybridMemory episodes={self._episode_enabled} "
            f"hints={self._hint_enabled} store={self._store!r}>"
        )
