"""memory 插件：长期记忆（P3）

装配三样东西并注册为服务：

| 服务名 | 实现 | 说明 |
|--------|------|------|
| `embedder` | `HashingEmbedder`（默认）或 `SentenceTransformerEmbedder` | 文本向量化；后者懒加载、失败自动不可用 |
| `memory` | `HybridMemory`（包 `RecallStore`） | 长期记忆：SQLite + FTS5 + 向量 RRF 混合检索 |
| 3 个工具 | `memory_remember` / `memory_recall` / `memory_forget` | 暴露给用户与 LLM |

## 依赖与降级

- 只依赖 `kernel`（要 `tool_registry` 来注册工具）
- **嵌入器单独注册成服务**：它可能被别的消费者复用，且「换嵌入器 = 改配置一行」
  需要一个独立的名字可指
- 记忆整体是**可选能力**：本插件不在 `REQUIRED_SERVICES` 里，
  从清单删掉它或 `agent.memory.enabled=false`，管线照常工作（只是没有记忆）

## 为什么默认嵌入器不是神经模型

`sentence-transformers` 需要下载模型文件，在没网的机器上会直接不可用；
而记忆功能"因为环境而整个失效"是不可接受的。故默认走纯 stdlib 的特征哈希
（字符 n-gram + 词元，带符号哈希 → 定长归一化向量），零依赖、离线、确定。
想要真正的语义召回时把 `agent.memory.embedder` 改成 `st` 即可 ——
换的是配置，不是代码（设计原则 7）。
"""

import logging
from typing import Callable, Optional

from agent.plugins import (
    SVC_CONFIG,
    SVC_EMBEDDER,
    SVC_MEMORY,
    register_tools,
)
from agent.providers.memory import (
    DEFAULT_DB_PATH,
    DEFAULT_MAX_ITEMS,
    DEFAULT_MIN_SIMILARITY,
    HashingEmbedder,
    HybridMemory,
    RecallStore,
    SentenceTransformerEmbedder,
)
from agent.seams.embedder import DEFAULT_DIM, EmbedderService
from agent.tools.memory_tools import all_memory_tools

logger = logging.getLogger(__name__)


def _build_embedder(cfg) -> EmbedderService:
    """按配置构造嵌入器

    未知取值一律退回默认（哈希），并告警 —— 配错名字不该让记忆整个不可用。
    """
    provider = str(getattr(cfg, "memory_embedder", "hashing") or "hashing").lower()
    if provider in ("st", "sentence_transformers", "sentence-transformers"):
        model = str(getattr(cfg, "memory_st_model", "") or "all-MiniLM-L6-v2")
        logger.info("[memory_plugin] 嵌入器 = sentence-transformers(%s)（懒加载）", model)
        return SentenceTransformerEmbedder(model_name=model)

    if provider != "hashing":
        logger.warning("[memory_plugin] 未知嵌入器 %r，退回 hashing", provider)

    return HashingEmbedder(dim=int(getattr(cfg, "memory_dim", DEFAULT_DIM) or DEFAULT_DIM))


def setup(ctx, **config) -> Optional[Callable[[], None]]:
    """装配嵌入器、记忆存储与三个记忆工具"""
    cfg = ctx.use(SVC_CONFIG)

    if not getattr(cfg, "memory_enabled", True):
        logger.info("[memory_plugin] 长期记忆已关闭（agent.memory.enabled=false）")
        return None

    embedder = _build_embedder(cfg)
    ctx.provide(SVC_EMBEDDER, embedder)

    store = RecallStore(
        path=getattr(cfg, "memory_store", None) or DEFAULT_DB_PATH,
        embedder=embedder,
        max_items=int(getattr(cfg, "memory_max_items", DEFAULT_MAX_ITEMS)
                      or DEFAULT_MAX_ITEMS),
        vector_backend=str(getattr(cfg, "memory_vector_backend", "auto") or "auto"),
        min_similarity=float(getattr(cfg, "memory_min_similarity",
                                     DEFAULT_MIN_SIMILARITY)
                             or DEFAULT_MIN_SIMILARITY),
    )
    memory = HybridMemory(
        store,
        embedder=embedder,
        episode_enabled=bool(getattr(cfg, "memory_episodes", True)),
        hint_enabled=bool(getattr(cfg, "memory_hints", True)),
    )
    ctx.provide(SVC_MEMORY, memory)

    dispose_tools = register_tools(ctx, all_memory_tools(memory))

    stats = memory.stats()
    logger.info(
        "[memory_plugin] 装配完成 (embedder=%s, backend=%s, path=%s, degraded=%s)",
        type(embedder).__name__, stats.get("vector_backend"),
        stats.get("path"), stats.get("degraded"),
    )

    def dispose() -> None:
        """先摘工具、再关记忆、最后放嵌入器

        顺序有讲究：工具持有 memory 引用，先摘掉它们可以保证关闭期间
        不会再有调用进来；嵌入器最后关（memory 关闭时不碰它，见 HybridMemory.close）。
        """
        dispose_tools()
        memory.close()
        embedder.close()

    return dispose
