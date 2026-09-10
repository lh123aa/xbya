"""实体追踪器（指代消解）

解决"那个文件"、"第一个"、"把那些删了"这类需要上下文的表达。

设计：
- 双栈模型：文件栈（最近搜索/列出的结果）+ 操作栈（最近执行的动作）
- 纯本地、无副作用、可快照
- 容量受限（FIFO 淘汰），避免长期对话下无限增长

用法：
    tracker = EntityTracker()
    tracker.push_files(search_results)          # 记录"那些文件"
    tracker.resolve("第一个")                    # → [search_results[0]]
    tracker.resolve("那些")                      # → 全部
    tracker.resolve("它")                        # → 最近单个
"""

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

#: 文件栈容量
MAX_FILES = 20
#: 操作栈容量
MAX_ACTIONS = 10

#: 序数词 → 数字
ORDINAL_MAP = {
    "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
    "首": 1, "末": -1, "最后": -1,
}

#: 序数指代正则
#: 覆盖三种形态：
#:   "第一个" / "第2个" / "第 三 个"   → 带"第"
#:   "最后一个" / "末个"                → 无"第"，表末尾
#:   "首个"                             → 无"第"，表开头
_ORDINAL_RE = re.compile(
    r"(?:第\s*(?P<num>[一二三四五六七八九十\d]+|最后|首)\s*个?)"
    r"|(?P<bare>最后(?:一个)?|末个|首个)"
)

#: 集合指代词
_PLURAL_WORDS = ("那些", "这些", "全部", "所有", "它们", "全都", "都")

#: 单数指代词
_SINGULAR_WORDS = ("它", "那个", "这个", "刚才那个", "刚才的", "刚才", "上面那个")


@dataclass
class ActionRecord:
    """一次操作记录

    Attributes:
        action: 工具名
        params: 工具参数
        result_count: 该次操作涉及的对象数
        timestamp: 记录时刻（Unix 秒；0 表示未设置）
    """

    action: str
    params: Dict[str, Any] = field(default_factory=dict)
    result_count: int = 0
    timestamp: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        """序列化为可 JSON 化的 dict"""
        return {
            "action": self.action,
            "params": self.params,
            "result_count": self.result_count,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: Any) -> Optional["ActionRecord"]:
        """从 dict 还原；结构不合法时返回 None（调用方跳过该条）"""
        if not isinstance(data, dict):
            return None
        action = data.get("action")
        if not isinstance(action, str) or not action:
            return None

        params = data.get("params")
        if not isinstance(params, dict):
            params = {}

        count = data.get("result_count")
        count = int(count) if isinstance(count, (int, float)) else 0

        ts = data.get("timestamp")
        ts = float(ts) if isinstance(ts, (int, float)) else 0.0

        return cls(action=action, params=params, result_count=count, timestamp=ts)


class EntityTracker:
    """对话实体追踪器

    线程说明：仅在管线线程内使用，不做加锁。
    """

    def __init__(
        self,
        max_files: int = MAX_FILES,
        max_actions: int = MAX_ACTIONS,
    ) -> None:
        self._max_files = max(1, max_files)
        self._max_actions = max(1, max_actions)
        self._files: List[Dict[str, Any]] = []
        self._actions: List[ActionRecord] = []

    # ══════════════════════════════════════════════
    #  记录
    # ══════════════════════════════════════════════

    def push_files(self, files: Any) -> int:
        """记录一批文件（成为"那些文件"的候选）

        Args:
            files: 文件信息列表（dict 或路径字符串）

        Returns:
            实际记录的数量
        """
        if not files:
            return 0

        if isinstance(files, dict):
            files = [files]
        if not isinstance(files, (list, tuple)):
            return 0

        normalized: List[Dict[str, Any]] = []
        for item in files:
            if isinstance(item, dict):
                normalized.append(dict(item))
            elif isinstance(item, (str,)):
                normalized.append({"name": item.split("\\")[-1].split("/")[-1], "path": item})
            else:
                continue

        if not normalized:
            return 0

        # 新结果替换旧结果（用户刚看到的就是"那些"）
        self._files = normalized[: self._max_files]
        logger.debug("[tracker] 记录 %d 个文件实体", len(self._files))
        return len(self._files)

    def push_action(
        self,
        action: str,
        params: Optional[Dict[str, Any]] = None,
        result_count: int = 0,
    ) -> None:
        """记录一次操作"""
        if not action:
            return
        self._actions.append(
            ActionRecord(
                action=action,
                params=dict(params or {}),
                result_count=result_count,
                timestamp=time.time(),
            )
        )
        if len(self._actions) > self._max_actions:
            self._actions = self._actions[-self._max_actions:]

    # ══════════════════════════════════════════════
    #  解析
    # ══════════════════════════════════════════════

    def resolve(self, reference: str) -> List[Dict[str, Any]]:
        """解析指代，返回被指的文件列表

        Args:
            reference: 指代表达（"第一个"、"那些"、"它"）

        Returns:
            匹配的文件列表；无法解析时返回空列表
        """
        if reference is None:
            return []
        text = str(reference).strip()
        if not text:
            return []

        # 1. 序数指代
        ordinal = self.resolve_ordinal(text)
        if ordinal is not None:
            return ordinal

        # 2. 集合指代
        for word in _PLURAL_WORDS:
            if word in text:
                logger.debug("[tracker] 集合指代 %r → %d 个", text, len(self._files))
                return list(self._files)

        # 3. 单数指代
        for word in _SINGULAR_WORDS:
            if word in text:
                result = self._files[:1]
                logger.debug("[tracker] 单数指代 %r → %s", text, 
                             result[0].get("name") if result else "(空)")
                return result

        return []

    def resolve_ordinal(self, text: str) -> Optional[List[Dict[str, Any]]]:
        """解析序数指代

        Returns:
            文件列表；文本不含序数时返回 None（区别于"含序数但越界"返回 []）
        """
        m = _ORDINAL_RE.search(str(text))
        if not m:
            return None

        token = m.group("num") or m.group("bare") or ""
        if not token:
            # 不可达：_ORDINAL_RE 的两条分支各自必然填充 num 或 bare
            return []   # pragma: no cover

        if token.startswith("最后") or token.startswith("末"):
            index = -1
        elif token.startswith("首"):
            index = 1
        elif token.isdigit():
            index = int(token)
        else:
            index = ORDINAL_MAP.get(token, 0)

        if index == 0:
            return []

        if not self._files:
            logger.debug("[tracker] 序数指代 %r 无候选文件", text)
            return []

        if index < 0:
            return [self._files[index]]

        pos = index - 1
        if pos >= len(self._files):
            logger.info(
                "[tracker] 序数 %d 越界（仅有 %d 个候选）", index, len(self._files)
            )
            return []

        return [self._files[pos]]

    def is_reference(self, text: str) -> bool:
        """文本是否包含指代表达（供路由判断"这句是否依赖上文"）"""
        if not text:
            return False
        t = str(text).strip()
        if _ORDINAL_RE.search(t):
            return True
        return any(w in t for w in _PLURAL_WORDS) or any(w in t for w in _SINGULAR_WORDS)

    # ══════════════════════════════════════════════
    #  查询
    # ══════════════════════════════════════════════

    def files(self) -> List[Dict[str, Any]]:
        """当前文件栈快照"""
        return list(self._files)

    def file_count(self) -> int:
        """当前文件栈大小"""
        return len(self._files)

    def last_action(self) -> Optional[ActionRecord]:
        """最近一次操作"""
        return self._actions[-1] if self._actions else None

    def actions(self) -> List[ActionRecord]:
        """操作栈快照"""
        return list(self._actions)

    def has_context(self) -> bool:
        """是否有可用于消解的上下文"""
        return bool(self._files)

    # ══════════════════════════════════════════════
    #  清理
    # ══════════════════════════════════════════════

    def clear(self) -> None:
        """清空全部上下文"""
        self._files.clear()
        self._actions.clear()

    def clear_files(self) -> None:
        """只清空文件栈（如用户开始新话题时）"""
        self._files.clear()

    # ══════════════════════════════════════════════
    #  序列化（供持久化使用）
    # ══════════════════════════════════════════════

    #: 落盘时只保留这些文件字段（不存文件内容，也不存工具塞进来的杂项）
    PERSIST_FILE_KEYS = ("name", "path", "size", "mtime", "is_dir")

    def to_dict(self) -> Dict[str, Any]:
        """序列化为可 JSON 化的 dict

        只保留 `PERSIST_FILE_KEYS` 中的字段，并截断字段名以外的超长字符串，
        避免把工具返回的大块数据写进磁盘。
        """
        files: List[Dict[str, Any]] = []
        for item in self._files[: self._max_files]:
            if not isinstance(item, dict):
                continue
            trimmed: Dict[str, Any] = {}
            for key in self.PERSIST_FILE_KEYS:
                if key in item:
                    value = item[key]
                    if isinstance(value, str):
                        value = value[:512]
                    trimmed[key] = value
            if trimmed.get("name") or trimmed.get("path"):
                files.append(trimmed)

        return {
            "version": 1,
            "files": files,
            "actions": [a.to_dict() for a in self._actions[-self._max_actions:]],
        }

    def from_dict(self, data: Any) -> int:
        """从 dict 还原状态（覆盖现有内容）

        容错优先：任何结构异常都只跳过该部分，不抛异常。
        这样磁盘上出现半截/损坏文件时，最多丢失上下文，不会阻塞启动。

        Args:
            data: 由 to_dict() 产出的 dict（或任意脏数据）

        Returns:
            实际恢复的文件条数
        """
        if not isinstance(data, dict):
            logger.warning("[tracker] 持久化数据不是 dict，忽略")
            return 0

        raw_files = data.get("files")
        if isinstance(raw_files, list):
            restored: List[Dict[str, Any]] = []
            for item in raw_files:
                if not isinstance(item, dict):
                    continue
                name = item.get("name")
                path = item.get("path")
                if not isinstance(name, str) and not isinstance(path, str):
                    continue
                restored.append(dict(item))
                if len(restored) >= self._max_files:
                    break
            self._files = restored
        else:
            if raw_files is not None:
                logger.warning("[tracker] files 字段类型异常（%s），按空处理", type(raw_files).__name__)
            self._files = []

        raw_actions = data.get("actions")
        if isinstance(raw_actions, list):
            actions = [ActionRecord.from_dict(a) for a in raw_actions]
            self._actions = [a for a in actions if a is not None][-self._max_actions:]
        else:
            if raw_actions is not None:
                logger.warning("[tracker] actions 字段类型异常（%s），按空处理", type(raw_actions).__name__)
            self._actions = []

        return len(self._files)

    def __repr__(self) -> str:
        return f"<EntityTracker files={len(self._files)} actions={len(self._actions)}>"
