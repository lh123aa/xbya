"""句向量嵌入器（Service Provider，可选实现）

用 `sentence-transformers` 做真正的语义嵌入：同义改写（「我喜欢 Chrome」/
「浏览器我习惯用谷歌的」）也能互相召回，这是哈希嵌入器做不到的。

## 为什么必须懒加载

`EmbedderService` 的硬约定是"构造廉价"。加载模型要读几百 MB 权重、还要
导入 torch（数秒），若放在 `__init__` 里，装配阶段就会被拖住 —— 而记忆只是
增强能力，不该让应用启动变慢。因此：

- `__init__` 只记配置，不碰模型
- 首次 `embed()` 才在锁内加载，加载一次后常驻
- `ready()` 在加载前如实返回 False，调用方据此退回词法检索

## 为什么必须优雅降级

模型没下载 / 没网 / 依赖没装，都是**正常**的部署形态（见 AGENTS.md 依赖最小化）。
这些情况下 `embed()` 返回零向量而不是抛异常：调用方看到零向量就知道
"这一路没有信号"，词法检索仍然可用，记忆功能整体不失效。
"""

import logging
import threading
from typing import Any, List, Optional, Sequence

from agent.seams.embedder import EmbedderService, normalize

logger = logging.getLogger(__name__)

#: 默认模型（小而快，384 维，英文为主但中文可用）
DEFAULT_MODEL_NAME = "all-MiniLM-L6-v2"

#: 模型未加载时对外申报的维度（与 DEFAULT_MODEL_NAME 一致，避免调用方拿不到 dim）
DEFAULT_FALLBACK_DIM = 384


class SentenceTransformerEmbedder(EmbedderService):
    """sentence-transformers 句向量嵌入器（懒加载 + 失败降级）

    用法：
        emb = SentenceTransformerEmbedder()      # 廉价，不加载模型
        assert emb.ready() is False
        vec = emb.embed_one("喜欢用 Chrome")     # 此刻才加载（成功则 ready() 变 True）
        emb.close()                              # 释放模型，可重复调用
    """

    capability_name = "embedder"
    provider_name = "st"

    def __init__(
        self,
        model_name: str = DEFAULT_MODEL_NAME,
        fallback_dim: int = DEFAULT_FALLBACK_DIM,
    ) -> None:
        """
        Args:
            model_name: 模型名或本地路径
            fallback_dim: 模型未加载时申报的维度（通常等于该模型的真实维度）
        """
        self._model_name = str(model_name or DEFAULT_MODEL_NAME)
        self._fallback_dim = max(1, int(fallback_dim))
        self._model: Optional[Any] = None
        self._model_dim = 0
        self._failed = False
        # 可能被多个执行线程同时调用（记忆检索在池线程里跑），加载必须串行化
        self._lock = threading.Lock()

    # ══════════════════════════════════════════════
    #  Seam 接口
    # ══════════════════════════════════════════════

    @property
    def dim(self) -> int:
        """向量维度：已加载用模型真实维度，否则用 fallback_dim"""
        if self._model is None:
            return self._fallback_dim
        return self._model_dim or self._fallback_dim

    def embed(self, texts: Sequence[str]) -> List[List[float]]:
        """批量向量化（首次调用会触发模型加载）

        Args:
            texts: 待编码文本

        Returns:
            与输入等长的向量列表；模型不可用时每条都是零向量
        """
        if not texts:
            return []

        model = self._ensure_model()
        if model is None:
            return [[0.0] * self.dim for _ in texts]

        try:
            raw = model.encode([str(t) for t in texts])
            return [self._to_vector(v) for v in raw]
        except Exception as e:
            # 编码失败（显存不足、输入异常等）不应拖垮主流程：这一路当作没有信号
            logger.warning("[embedder] 编码失败，返回零向量: %s", e)
            return [[0.0] * self.dim for _ in texts]

    def ready(self) -> bool:
        """模型是否已成功加载（未尝试加载时返回 False）"""
        return self._model is not None

    def describe(self) -> str:
        """一行描述（写进日志与 /status）"""
        state = "ready" if self.ready() else "lazy"
        return (
            f"SentenceTransformerEmbedder(model={self._model_name}, "
            f"dim={self.dim}, state={state})"
        )

    def close(self) -> None:
        """释放模型并复位状态；可重复调用

        复位 `_failed` 是有意的：`close()` 之后再 `embed()` 会重新尝试加载，
        这符合"关闭后重新启用"的直觉，也方便测试与热插拔。
        """
        with self._lock:
            self._model = None
            self._model_dim = 0
            self._failed = False

    # ══════════════════════════════════════════════
    #  懒加载
    # ══════════════════════════════════════════════

    def _ensure_model(self) -> Optional[Any]:
        """确保模型已加载；返回模型或 None（失败时不抛异常）"""
        if self._model is not None:
            return self._model

        with self._lock:
            # 双检：可能在等锁期间已被别的线程加载
            if self._model is not None:
                return self._model
            # 已失败过就不再反复尝试 —— 每次尝试都要 import torch，代价高
            if self._failed:
                return None
            try:
                model = self._load_model()
            except Exception as e:
                self._failed = True
                logger.warning(
                    "[embedder] 模型 %s 加载失败（%s），退回词法检索", self._model_name, e
                )
                return None

            self._model = model
            self._model_dim = self._detect_dim(model)
            logger.info(
                "[embedder] 模型就绪: %s (dim=%d)", self._model_name, self._model_dim
            )
            return self._model

    def _load_model(self) -> Any:
        """真正加载模型（重活都在这里；隔离出来便于测试替换）

        Returns:
            模型对象

        Raises:
            ImportError: 未安装 sentence-transformers
            Exception: 模型不存在 / 无网络 / 权重损坏
        """
        from sentence_transformers import SentenceTransformer

        return SentenceTransformer(self._model_name)

    @staticmethod
    def _detect_dim(model: Any) -> int:
        """探测模型的输出维度；探不到时返回 0（由 dim 属性退回 fallback）

        ⚠️ 方法名**换过**：老版本叫 `get_sentence_embedding_dimension`，
        新版本改叫 `get_embedding_dimension`（真模型实测会打 FutureWarning，
        P4-C3 加载真模型时才暴露出来 —— 假模块测不到这件事）。
        两个名字都试，**新的优先**：只用老名字的话将来被删就探不到维度，
        于是 `dim` 静默退回 `fallback_dim`，而 vec0 索引会按**错误宽度**建表。
        """
        getter = (getattr(model, "get_embedding_dimension", None)
                  or getattr(model, "get_sentence_embedding_dimension", None))
        if getter is None:
            return 0
        try:
            value = getter()
        except Exception as e:
            logger.warning("[embedder] 无法探测模型维度: %s", e)
            return 0
        try:
            return max(1, int(value))
        except (TypeError, ValueError):
            return 0

    def _to_vector(self, raw: Any) -> List[float]:
        """把模型输出收敛成归一化的 float 列表（兼容 numpy / list / tensor）"""
        values = raw.tolist() if hasattr(raw, "tolist") else list(raw)
        return normalize([float(v) for v in values])

    def __repr__(self) -> str:
        return (
            f"<SentenceTransformerEmbedder model={self._model_name!r} "
            f"ready={self.ready()}>"
        )
