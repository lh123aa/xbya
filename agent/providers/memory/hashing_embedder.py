"""哈希嵌入器（Service Provider，默认实现）

零依赖、离线可用、结果确定 —— 这是本项目选择它作为默认嵌入器的全部理由：
记忆功能不能因为"模型没下载"或"没有网络"就变成不可用。

## 算法

1. **特征抽取**：ASCII 词元（小写）+ CJK 连续片段的字符 n-gram（n=2、3）。
   中文没有空格，字符 n-gram 是主力 —— 「合同」在「桌面上的合同」里
   只有靠 `合同` 这个 bigram 才能被召回。
2. **带符号哈希**：`blake2b` 摘要的低位决定符号、其余位决定桶。
   带符号（signed hashing）让同桶特征的贡献有正有负，
   期望上互相抵消而不是单方向累加，显著降低碰撞带来的相似度偏置。
3. **加权**：词元权重高于字符 n-gram，让"含相同词"比"含相同字串"更相似。
4. **L2 归一化**：归一化后点积即余弦相似度，落盘向量与查询向量同尺度。

## 为什么必须用 blake2b 而不是内置 hash()

内置 `hash()` 对 str 带**随机盐**（`PYTHONHASHSEED`），同一文本在不同进程
会落到不同桶 —— 于是昨天落盘的向量与今天查询的向量根本不在同一个空间里，
向量检索会在重启后静默失效（不报错，只是永远召回不到）。
`blake2b` 是确定性散列，跨进程、跨平台稳定，这正是"落盘向量"的前提。
"""

import hashlib
import logging
import re
from typing import Any, Dict, List, Sequence, Tuple

from agent.seams.embedder import DEFAULT_DIM, EmbedderService, normalize

logger = logging.getLogger(__name__)

#: 词元（ASCII 词）权重 —— 高于字符 n-gram：同词比同字串更能说明语义相近
TOKEN_WEIGHT = 1.0

#: 字符 n-gram 权重：越长的 n-gram 越具体，但也越容易因改字而全丢，故权重递减
_NGRAM_WEIGHTS: Dict[int, float] = {2: 0.6, 3: 0.3}

#: 参与抽取的 n-gram 长度（写进 describe()，便于 /status 自证配置）
NGRAM_SIZES: Tuple[int, ...] = (2, 3)

#: 特征来源：ASCII 词元 或 CJK 连续片段（其余字符 —— 标点/空白/emoji —— 一律丢弃）
_TOKEN_SOURCE_RE = re.compile(r"[a-z0-9]+|[\u3400-\u4dbf\u4e00-\u9fff]+")


class HashingEmbedder(EmbedderService):
    """基于特征哈希的定长向量嵌入器

    用法：
        emb = HashingEmbedder()                 # dim=256
        vec = emb.embed_one("喜欢用 Chrome")
        assert len(vec) == emb.dim

    注意：本实现是**词形级**的 —— 「喜欢」与「中意」不会因为同义而接近，
    只因为共享字符而部分接近。需要同义改写召回时应换用 `SentenceTransformerEmbedder`。
    """

    capability_name = "embedder"
    provider_name = "hashing"

    def __init__(self, dim: int = DEFAULT_DIM) -> None:
        """
        Args:
            dim: 向量维度；非法值（0、负数）收敛为 1，避免取模除零
        """
        # 构造必须廉价：这里只做一次整数收敛，不碰磁盘/网络/模型
        self._dim = max(1, int(dim))

    # ══════════════════════════════════════════════
    #  Seam 接口
    # ══════════════════════════════════════════════

    @property
    def dim(self) -> int:
        """向量维度"""
        return self._dim

    def embed(self, texts: Sequence[str]) -> List[List[float]]:
        """批量向量化

        Args:
            texts: 待编码文本；非 str 元素会被 `str()` 收敛（见 `_embed_one`）

        Returns:
            与输入等长的向量列表；每条的维度为 `dim`，已 L2 归一化
        """
        if not texts:
            return []
        return [self._embed_one(text) for text in texts]

    def describe(self) -> str:
        """一行描述（写进日志与 /status）"""
        sizes = ",".join(str(n) for n in NGRAM_SIZES)
        return f"HashingEmbedder(dim={self._dim}, ngram={sizes})"

    # ══════════════════════════════════════════════
    #  内部实现
    # ══════════════════════════════════════════════

    def _embed_one(self, text: Any) -> List[float]:
        """单条文本 → 归一化向量

        本方法**不抛异常**：非 str 输入用 `str()` 收敛，`None` 视为"没有内容"。
        `None` 不复用 `str()`（那会得到 `"None"` 这四个字母并产生一个真实向量），
        而是当空串处理 —— 调用方传 None 的本意是"这条没有文本"。
        """
        vector = [0.0] * self._dim
        if text is None:
            return vector

        source = str(text)
        if not source.strip():
            return vector

        for feature, weight in self._features(source.lower()):
            bucket, sign = self._bucket(feature)
            vector[bucket] += weight * sign
        return normalize(vector)

    @staticmethod
    def _features(text: str) -> List[Tuple[str, float]]:
        """抽取（特征名, 权重）列表

        特征名带类型前缀（`w` / `n2` / `n3`），让"同一个字符串作为词元"
        与"作为 bigram"落在不同的哈希输入上 —— 否则两处含义不同却会互相加强。

        Args:
            text: 已小写化的文本

        Returns:
            特征列表；无可用特征时返回空列表
        """
        features: List[Tuple[str, float]] = []
        for match in _TOKEN_SOURCE_RE.finditer(text):
            piece = match.group(0)
            if piece[0].isascii():
                features.append((f"w{piece}", TOKEN_WEIGHT))
                continue
            # CJK 片段：切字符 n-gram。中文没空格，这是唯一可用的"词"近似
            for size, weight in _NGRAM_WEIGHTS.items():
                for i in range(len(piece) - size + 1):
                    features.append((f"n{size}:{piece[i:i + size]}", weight))
        return features

    def _bucket(self, feature: str) -> Tuple[int, float]:
        """特征 → （桶下标, 符号）

        低位决定符号、其余位决定桶，即"带符号哈希"。
        `errors="ignore"` 让含孤立代理对的字符串（如来自乱码解码）也能编码，
        而不是抛出 UnicodeEncodeError —— 嵌入器不该成为异常来源。

        Args:
            feature: 特征名

        Returns:
            (桶下标, 符号)；符号为 +1.0 或 -1.0
        """
        digest = hashlib.blake2b(
            feature.encode("utf-8", errors="ignore"), digest_size=8
        ).digest()
        value = int.from_bytes(digest, "big")
        sign = -1.0 if value & 1 else 1.0
        return (value >> 1) % self._dim, sign

    def __repr__(self) -> str:
        return f"<HashingEmbedder dim={self._dim}>"
