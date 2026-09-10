"""嵌入能力接口定义（Service Definition）—— P3 长期记忆的向量化

DSH 能力 Seam 三角的 Definition 角色：
- Definition: 本模块（EmbedderService）
- Provider:   agent/providers/memory/{hashing_embedder,st_embedder}.py
- Consumer:   agent/providers/memory/recall_store.py（向量列与相似度检索）

## 为什么把「嵌入」单独拆成一个能力

记忆检索的质量几乎全由嵌入决定，而可选路线差别很大：

| Provider | 依赖 | 首次延迟 | 语义能力 | 离线 |
|----------|------|---------|---------|------|
| `hashing` | 无（纯 stdlib） | <1ms | 词形相近才行 | ✅ |
| `st` | sentence-transformers + 模型文件 | 首次加载数秒~数十秒 | 同义改写也能命中 | ❌ 需模型已下载 |

拆成 seam 之后，「换嵌入器」= 改 `config.yaml` 一行，记忆存储与工具代码一行不动 ——
这正是设计原则 7 要的效果。默认走 `hashing`：**零新依赖、离线可用、结果确定**，
保证记忆功能在任何机器上都不会因为"模型没下载"而变成不可用。

## 向量表示

统一用 `float32` 定长向量，落盘用 `array('f').tobytes()`（stdlib，无需 numpy）。
所有 Provider 的 `embed()` 输出**必须已 L2 归一化**，这样点积即余弦相似度。
"""

from abc import abstractmethod
from array import array
from typing import Any, List, Sequence

from core.kernel.service import Service

#: 默认向量维度（hashing Provider 用；深度模型 Provider 以自己的模型为准）
DEFAULT_DIM = 256


class EmbedderService(Service):
    """嵌入能力接口

    实现者约定：
    - `embed()` 输出长度必须等于 `dim`，且每个向量已 **L2 归一化**
    - 不得抛异常；无法处理时返回零向量（调用方据此跳过向量检索）
    - 构造廉价；重资源（模型加载）必须**懒加载**并在 `ready()` 中如实反映
    - `ready()` 为 False 时调用方会退回纯词法检索，不得因此报错
    """

    capability_name = "embedder"

    @property
    @abstractmethod
    def dim(self) -> int:
        """向量维度"""

    @abstractmethod
    def embed(self, texts: Sequence[str]) -> List[List[float]]:
        """批量向量化

        Args:
            texts: 待编码文本

        Returns:
            与输入等长的向量列表；每条长度等于 `dim`，已 L2 归一化
        """

    def embed_one(self, text: str) -> List[float]:
        """单条向量化（便捷方法；失败返回零向量）"""
        try:
            vectors = self.embed([text])
        except Exception:
            return [0.0] * self.dim
        if not vectors:
            return [0.0] * self.dim
        return vectors[0]

    def ready(self) -> bool:
        """当前是否可用（模型已加载 / 扩展可用）

        Returns:
            True=可用；默认 True（纯本地实现始终可用）
        """
        return True

    def describe(self) -> str:
        """一行描述（写进日志与 /status）"""
        return f"{type(self).__name__}(dim={self.dim})"

    def close(self) -> None:
        """释放资源（卸载模型）；可重复调用"""
        return None


# ══════════════════════════════════════════════
#  共用工具（Provider 与 Consumer 都可用）
# ══════════════════════════════════════════════


def normalize(vector: Sequence[float]) -> List[float]:
    """L2 归一化；零向量原样返回（避免除零）"""
    norm = 0.0
    for v in vector:
        norm += float(v) * float(v)
    if norm <= 0.0:
        return [0.0] * len(vector)
    inv = 1.0 / (norm ** 0.5)
    return [float(v) * inv for v in vector]


def dot(a: Sequence[float], b: Sequence[float]) -> float:
    """点积（输入已归一化时等于余弦相似度）"""
    total = 0.0
    for x, y in zip(a, b):
        total += float(x) * float(y)
    return total


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """余弦相似度；任一为零向量时返回 0.0"""
    na = sum(float(x) * float(x) for x in a)
    nb = sum(float(y) * float(y) for y in b)
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    return dot(a, b) / ((na ** 0.5) * (nb ** 0.5))


def pack(vector: Sequence[float]) -> bytes:
    """向量 → float32 字节串（落盘用）"""
    return array("f", [float(v) for v in vector]).tobytes()


def unpack(blob: Any, dim: int) -> List[float]:
    """字节串 → 向量；长度不符时按 dim 截断/补零（容错，不抛异常）"""
    if not isinstance(blob, (bytes, bytearray, memoryview)):
        return [0.0] * max(0, int(dim))
    buf = array("f")
    try:
        buf.frombytes(bytes(blob))
    except Exception:                     # 非法字节数 → 当作零向量
        return [0.0] * max(0, int(dim))

    values = [float(v) for v in buf]
    want = max(0, int(dim))
    if len(values) == want:
        return values
    if len(values) > want:
        return values[:want]
    return values + [0.0] * (want - len(values))
