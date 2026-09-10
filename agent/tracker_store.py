"""实体栈持久化（P2-3 / 偿还技术债 D1）

把 `EntityTracker` 的文件栈与操作栈落盘，使"那个文件""第一个"这类指代
在**应用重启后**仍然可解析 —— 此前实体栈纯内存，重启即失忆。

设计取舍：
- **JSON 而非 SQLite**：数据量极小（≤20 文件 + ≤10 动作），无需查询能力
- **原子写**：先写 `*.tmp` 再 `os.replace`，避免掉电/崩溃留下半截文件
- **节流写**：默认 1s 内至多落盘一次，磁盘 I/O 不进热路径
- **不新增线程**：节流写是同步的，单次写量极小；避免再引入 teardown 竞态（D7 教训）
- **容错优先**：文件缺失/损坏/字段类型错一律退化为空栈，绝不阻塞启动
- **安全边界**：拒绝把状态写进用户桌面/文档/下载/图片（与 R1 白名单策略一致）

用法：
    store = TrackerStore(Path("data/agent_entities.json"))
    store.load_into(tracker)          # 启动时恢复
    ...
    store.save(tracker)               # 变更后（节流）
    store.flush(tracker)              # 释放时（强制）
"""

import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Optional

from agent.tracker import EntityTracker
from agent.store_guard import FORBIDDEN_DIR_NAMES, is_inside_forbidden_dir

logger = logging.getLogger(__name__)

#: 默认状态文件（相对项目根目录）
DEFAULT_STORE_PATH = os.path.join("data", "agent_entities.json")

#: 节流间隔（秒）
DEFAULT_THROTTLE = 1.0

#: 状态文件格式版本
SCHEMA_VERSION = 1

#: 禁止写入的用户目录名（与安全白名单同源，防状态文件污染用户数据区）
#:
#: P4-A2：定义已抽到 `agent/store_guard.py`（原先在 tracker_store 与 recall_store
#: 各写了一遍，提醒存储又要写第三遍 —— 安全规则三份拷贝必然漂移）。
#: 这里保留同名别名，避免破坏已有引用（文档与源码注释里都提到过这个名字）。
_FORBIDDEN_DIR_NAMES = FORBIDDEN_DIR_NAMES
_is_inside_forbidden_dir = is_inside_forbidden_dir


class TrackerStore:
    """实体栈的 JSON 持久化后端

    线程说明：与 `EntityTracker` 相同，约定只在管线线程内使用。
    """

    def __init__(
        self,
        path: Optional[str] = None,
        throttle: float = DEFAULT_THROTTLE,
        enabled: bool = True,
    ) -> None:
        """
        Args:
            path: 状态文件路径；None 用 DEFAULT_STORE_PATH
            throttle: 两次落盘的最小间隔秒数
            enabled: 关闭时所有操作退化为空操作
        """
        self._enabled = bool(enabled)
        self._throttle = max(0.0, float(throttle))
        self._last_save = 0.0
        self._writes = 0
        self._restored = 0

        raw = Path(path or DEFAULT_STORE_PATH).expanduser()

        # 安全边界：不接受把状态写进用户数据目录
        if self._enabled and _is_inside_forbidden_dir(raw):
            logger.warning(
                "[tracker_store] 拒绝把状态写入用户目录（%s），持久化已关闭", raw
            )
            self._enabled = False

        self._path = raw

    # ══════════════════════════════════════════════
    #  读
    # ══════════════════════════════════════════════

    def load_into(self, tracker: EntityTracker) -> int:
        """从磁盘恢复状态到 tracker

        Returns:
            恢复的文件条数（0 表示无可用状态）
        """
        if not self._enabled:
            return 0

        try:
            if not self._path.exists():
                logger.debug("[tracker_store] 状态文件不存在，按空上下文启动")
                return 0
            raw = self._path.read_text(encoding="utf-8")
        except OSError as e:
            logger.warning("[tracker_store] 读取失败（%s），按空上下文启动", e)
            return 0

        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning("[tracker_store] 状态文件损坏（%s），按空上下文启动", e)
            return 0

        if not isinstance(data, dict):
            logger.warning("[tracker_store] 状态文件顶层不是对象，按空上下文启动")
            return 0

        version = data.get("version")
        if isinstance(version, int) and version > SCHEMA_VERSION:
            logger.warning(
                "[tracker_store] 状态文件版本 %s 高于当前支持的 %s，忽略",
                version, SCHEMA_VERSION,
            )
            return 0

        count = tracker.from_dict(data)
        self._restored = count
        if count:
            logger.info("[tracker_store] 已恢复 %d 个文件实体与 %d 条操作记录",
                        count, len(tracker.actions()))
        return count

    # ══════════════════════════════════════════════
    #  写
    # ══════════════════════════════════════════════

    def save(self, tracker: EntityTracker, force: bool = False) -> bool:
        """落盘（默认节流）

        Args:
            tracker: 要保存的实体栈
            force: True 忽略节流立即写

        Returns:
            True=本次确实写了；False=被节流跳过或写入失败
        """
        if not self._enabled:
            return False

        now = time.time()
        if not force and (now - self._last_save) < self._throttle:
            return False

        return self._write(tracker.to_dict())

    def flush(self, tracker: EntityTracker) -> bool:
        """强制落盘（释放时调用）"""
        return self.save(tracker, force=True)

    def _write(self, payload: Dict[str, Any]) -> bool:
        """原子写：临时文件 + os.replace"""
        payload = dict(payload)
        payload["version"] = SCHEMA_VERSION
        payload["saved_at"] = time.time()

        tmp_path: Optional[str] = None
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)

            # 同目录建临时文件，保证 os.replace 是同分区原子操作
            fd, tmp_path = tempfile.mkstemp(
                prefix=self._path.name + ".", suffix=".tmp", dir=str(self._path.parent)
            )
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False)
                fh.flush()
                os.fsync(fh.fileno())

            os.replace(tmp_path, self._path)
            tmp_path = None

            self._last_save = time.time()
            self._writes += 1
            logger.debug("[tracker_store] 已落盘 %s", self._path)
            return True
        except OSError as e:
            logger.warning("[tracker_store] 落盘失败（%s），不影响主流程", e)
            return False
        finally:
            if tmp_path is not None:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

    # ══════════════════════════════════════════════
    #  状态
    # ══════════════════════════════════════════════

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def path(self) -> Path:
        return self._path

    def stats(self) -> Dict[str, Any]:
        """持久化状态快照"""
        return {
            "enabled": self._enabled,
            "path": str(self._path),
            "writes": self._writes,
            "restored": self._restored,
        }

    def __repr__(self) -> str:
        return f"<TrackerStore enabled={self._enabled} path={self._path}>"
