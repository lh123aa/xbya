"""长期记忆能力实现（Service Provider）

| 模块 | 角色 |
|------|------|
| `hashing_embedder` | 默认嵌入器：纯 stdlib 特征哈希，离线、确定、零依赖 |
| `st_embedder` | 可选嵌入器：sentence-transformers，懒加载 + 失败降级 |
| `recall_store` | SQLite + FTS5 + float32 向量列的混合检索存储 |
| `hybrid_memory` | `MemoryService` 实现：把 store 包成 seam，管"什么该记" |

设计要点：
- 默认路线（`hashing` + `recall_store`）**零新依赖**，任何机器上都能跑
- 每一层都能单独替换：换嵌入器 = 改 config.yaml 一行
- 全部公开方法遵守 "永不抛异常" 约定（见 `recall_store.never_raises`）

这里做重导出是为了装配层好写：`from agent.providers.memory import RecallStore`。
代价可以忽略 —— 只有 stdlib 被顺带导入，`sentence_transformers` 仍在
`st_embedder._load_model()` 里懒加载。
"""

from agent.providers.memory.hashing_embedder import HashingEmbedder
from agent.providers.memory.hybrid_memory import HybridMemory
from agent.providers.memory.recall_store import (
    DEFAULT_DB_PATH,
    DEFAULT_MAX_ITEMS,
    DEFAULT_MIN_SIMILARITY,
    RRF_K,
    RecallStore,
    fts_match_expression,
    rrf_fuse,
    tokenize,
)
from agent.providers.memory.st_embedder import SentenceTransformerEmbedder

__all__ = [
    "HashingEmbedder",
    "HybridMemory",
    "RecallStore",
    "SentenceTransformerEmbedder",
    "DEFAULT_DB_PATH",
    "DEFAULT_MAX_ITEMS",
    "DEFAULT_MIN_SIMILARITY",
    "RRF_K",
    "fts_match_expression",
    "rrf_fuse",
    "tokenize",
]
