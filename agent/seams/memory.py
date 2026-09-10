"""记忆能力接口定义（Service Definition）—— P3 长期记忆

DSH 能力 Seam 三角的 Definition 角色：
- Definition: 本模块（MemoryService / MemoryItem / MemoryKind）
- Provider:   agent/providers/memory/{recall_store,hybrid_memory}.py
- Consumer:   agent/tools/memory_tools.py、agent/pipeline.py、agent/providers/router/*

## 三类记忆

| 类别 | 含义 | 例子 | 典型用法 |
|------|------|------|---------|
| `fact` | 语义记忆：稳定的偏好与事实 | 「我喜欢用 Chrome」 | 路由前取偏好提示 |
| `episode` | 情景记忆：做过什么 | 「昨天找过合同_2025.pdf」 | 「上次那个」指代 |
| `entity` | 实体记忆：常出现的名词 | 「软件归档目录」 | 路径/应用补全 |

`EntityTracker`（会话内实体栈）与本能力**不是一回事**：
前者是"这几句话里提到了哪些文件"的短期栈，进程重启即失效（除非落盘）；
本能力是跨会话的长期知识，需要向量检索才能命中"换个说法问同一件事"。

## 职责边界

- Provider 只负责存取与检索，**不决定**什么时候记、记什么 —— 那由 Consumer 决定
- 检索必须**永不抛异常**：记忆是增强，坏了也不能拖垮主流程
- **不得存储敏感信息**：`is_sensitive()` 是本模块提供的统一判据，
  Provider 在 `remember()` 入口必须调用它（宁可少记，不可泄露）
"""

import re
import time
from abc import abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from core.kernel.service import Service

#: 单条记忆文本上限（超出截断，避免把整篇文档塞进记忆库）
MAX_MEMORY_TEXT = 512

#: 默认检索条数
DEFAULT_RECALL_LIMIT = 5

#: 默认注入提示的条数（多了会污染路由判断）
DEFAULT_HINT_LIMIT = 3


class MemoryKind(str, Enum):
    """记忆类别"""

    FACT = "fact"          # 语义记忆：偏好 / 事实
    EPISODE = "episode"    # 情景记忆：做过什么
    ENTITY = "entity"      # 实体记忆：名词、路径、应用名


def coerce_kind(kind: Any) -> MemoryKind:
    """把任意输入收敛成合法的 MemoryKind（不认识的一律当 fact）"""
    if isinstance(kind, MemoryKind):
        return kind
    try:
        return MemoryKind(str(kind).strip().lower())
    except (ValueError, AttributeError):
        return MemoryKind.FACT


@dataclass(slots=True)
class MemoryItem:
    """一条记忆

    Attributes:
        text: 记忆正文（口语化，可直接播报）
        kind: 记忆类别
        item_id: 唯一标识（空表示尚未入库）
        metadata: 附加信息（action / params / 来源等，参与持久化）
        created_at: 写入时间（Unix 秒）
        last_used_at: 最近一次被检索命中的时间
        use_count: 被命中次数（热度，参与排序衰减）
        score: 检索得分（仅检索结果上有意义）
    """

    text: str
    kind: MemoryKind = MemoryKind.FACT
    item_id: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: float = 0.0
    last_used_at: float = 0.0
    use_count: int = 0
    score: float = 0.0

    def __post_init__(self) -> None:
        self.kind = coerce_kind(self.kind)
        self.text = truncate_text(self.text)
        if not isinstance(self.metadata, dict):
            self.metadata = {}

    def to_dict(self) -> Dict[str, Any]:
        """转为可 JSON 序列化的字典"""
        return {
            "item_id": self.item_id,
            "kind": self.kind.value,
            "text": self.text,
            "metadata": self.metadata,
            "created_at": self.created_at,
            "last_used_at": self.last_used_at,
            "use_count": self.use_count,
        }

    @classmethod
    def from_dict(cls, data: Any) -> Optional["MemoryItem"]:
        """从字典还原；结构非法时返回 None（调用方跳过该条）"""
        if not isinstance(data, dict):
            return None
        text = data.get("text")
        if not isinstance(text, str) or not text.strip():
            return None

        def num(key: str) -> float:
            try:
                return float(data.get(key) or 0.0)
            except (TypeError, ValueError):
                return 0.0

        def integer(key: str) -> int:
            try:
                return int(data.get(key) or 0)
            except (TypeError, ValueError):
                return 0

        meta = data.get("metadata")
        return cls(
            text=text,
            kind=coerce_kind(data.get("kind")),
            item_id=str(data.get("item_id") or ""),
            metadata=meta if isinstance(meta, dict) else {},
            created_at=num("created_at"),
            last_used_at=num("last_used_at"),
            use_count=integer("use_count"),
        )

    def __str__(self) -> str:
        return f"[{self.kind.value}] {self.text[:40]}"


class MemoryService(Service):
    """记忆能力接口

    实现者约定：
    - 全部方法**不得抛异常**；失败时返回空值（None / [] / False / 0）
    - `remember()` 必须先过 `is_sensitive()`；命中敏感信息直接拒绝
    - `recall()` 结果按 `score` 降序，且只返回 `score > 0` 的条目
    - 检索命中时应更新 `last_used_at` / `use_count`（供后续热度排序）
    - 构造廉价：不得在 `__init__` 里建索引、连网络、加载模型（懒加载）
    """

    capability_name = "memory"

    @abstractmethod
    def remember(
        self,
        text: str,
        kind: Any = MemoryKind.FACT,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        """写入一条记忆

        Args:
            text: 记忆正文
            kind: 记忆类别
            metadata: 附加信息

        Returns:
            新条目的 item_id；被拒绝或写入失败时返回 None

        Raises:
            不得抛异常
        """

    @abstractmethod
    def recall(
        self,
        query: str,
        kind: Optional[Any] = None,
        limit: int = DEFAULT_RECALL_LIMIT,
    ) -> List[MemoryItem]:
        """按语义/词法混合检索

        Args:
            query: 查询文本
            kind: 限定类别；None 表示不限
            limit: 最多返回条数

        Returns:
            按相关度降序的记忆列表；无命中返回空列表
        """

    @abstractmethod
    def forget(self, item_id: str) -> bool:
        """删除一条记忆

        Returns:
            True=确实删掉了一条；False=不存在或删除失败
        """

    @abstractmethod
    def count(self, kind: Optional[Any] = None) -> int:
        """当前记忆条数（可按类别统计）"""

    # ── 可选覆写（默认空实现，纯内存实现无需关心）──

    def recall_text(
        self,
        query: str,
        kind: Optional[Any] = None,
        limit: int = DEFAULT_HINT_LIMIT,
    ) -> str:
        """检索并拼成一句可直接播报的提示；无命中返回空串"""
        return ""

    def record_episode(
        self,
        action: str,
        summary: str,
        params: Optional[Dict[str, Any]] = None,
        success: bool = True,
    ) -> Optional[str]:
        """记录一次工具执行的经过（情景记忆）

        Returns:
            新条目 ID；未记录时返回 None
        """
        return None

    def hint_for(self, text: str) -> str:
        """给路由用的偏好提示（如"用户偏好 Chrome"）；无则空串

        实现应保持**快速**：这是每句话都要走的路径，
        需要 LLM 参与的实现应改为检索本地已存好的偏好条目。
        """
        return ""

    def forget_all(self, kind: Optional[Any] = None) -> int:
        """清空记忆（可按类别），返回删除条数"""
        return 0

    def stats(self) -> Dict[str, Any]:
        """状态快照（供 /status 与验收证据）"""
        return {}

    def close(self) -> None:
        """释放资源（连接、后台线程）；可重复调用"""
        return None


# ══════════════════════════════════════════════
#  共用工具（Provider 与 Consumer 都可用）
# ══════════════════════════════════════════════


def truncate_text(text: Any, limit: int = MAX_MEMORY_TEXT) -> str:
    """收敛记忆正文：去首尾空白 + 压平换行 + 截断"""
    if text is None:
        return ""
    s = str(text).strip()
    s = re.sub(r"\s+", " ", s)
    if len(s) > limit:
        s = s[:limit]
    return s


#: 敏感信息判据：命中任意一条即拒绝入库
#:
#: 注意：中文词**不能**加 `\b`。Python 的 `\b` 以 `\w` 为界，而汉字本身就属于 `\w`，
#: 于是「我的密码是abc」里 `密码` 与 `是` 之间根本不存在词边界，加了 `\b` 反而永不命中。
#: 故 ASCII 关键词用 `\b` 收窄（避免误伤 passwordless 之类的长词），中文词用裸子串匹配。
SENSITIVE_PATTERNS = (
    (r"(?i)\b(?:password|passwd|pwd)\b", "密码"),
    (r"密码|口令|密钥|令牌|验证码", "密码"),
    (r"(?i)\b(?:api[_\-]?key|apikey|secret|access[_\-]?token|bearer)\b", "密钥"),
    (r"(?i)\bsk-[A-Za-z0-9_\-]{16,}", "API Key"),
    (r"\bAKIA[0-9A-Z]{16}\b", "Access Key ID"),
    (r"(?i)\bgh[pousr]_[A-Za-z0-9]{20,}", "GitHub Token"),
    (r"(?<!\d)\d{17}[\dXx](?!\d)", "身份证号"),
    (r"(?<!\d)\d{16,19}(?!\d)", "银行卡号"),
)
_SENSITIVE_RES = tuple((re.compile(p), label) for p, label in SENSITIVE_PATTERNS)


def sensitive_reason(text: Any) -> str:
    """该文本为何敏感；不敏感返回空串

    Provider 的 `remember()` 应在入口调用它并把原因写进日志 ——
    宁可少记一条，也不要把密码写进长期记忆库。
    """
    s = str(text or "")
    if not s:
        return ""
    for pattern, label in _SENSITIVE_RES:
        if pattern.search(s):
            return label
    return ""


def is_sensitive(text: Any) -> bool:
    """该文本是否含敏感信息（密码/密钥/证件号等）"""
    return bool(sensitive_reason(text))


def format_recall(items: List[MemoryItem], limit: int = DEFAULT_HINT_LIMIT) -> str:
    """把检索结果拼成一句可播报的提示

    Args:
        items: 检索结果（已按相关度降序）
        limit: 最多拼几条

    Returns:
        如「我记得你提过：喜欢用 Chrome、常用目录是 D 盘」；无命中返回空串
    """
    texts = [it.text for it in (items or []) if getattr(it, "text", "")]
    if not texts:
        return ""
    picked = texts[: max(1, int(limit))]
    return "我记得你提过：" + "、".join(picked)


def new_item_id() -> str:
    """生成记忆 ID（时间前缀便于按插入顺序排查）"""
    import uuid

    return f"m{int(time.time() * 1000):x}{uuid.uuid4().hex[:6]}"
