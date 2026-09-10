"""提醒的 JSON 持久化（偿还债务 D13）

## 为什么必须有它

`ReminderTool` 会回答**"好的，30分钟后我会提醒你喝水"**。P3 的
`ReminderScheduler` 让这句话在**本次进程内**真的兑现了。但提醒只存在内存里
（`ReminderTool._items`），于是：

    用户设 30 分钟提醒 → 关掉程序 → 重新打开 → 时间到了 → 什么都没发生

用户**以为还等着**，实际上那条提醒已经不存在了。这是全项目唯一一个
"对用户撒谎"的功能缺口，D13 记了很久，P4-A2 偿还。

## 三条硬约定（与记忆库同源，Provider 必须遵守）

1. **永不抛异常** —— 持久化是增强能力。落盘失败、文件损坏、权限不足，
   一律降级为"纯内存"，并记日志；绝不让"提醒"因为存不下来而变成用户操作失败。
2. **不落用户数据区** —— 默认 `data/agent_reminders.json`，
   且复用 `agent/store_guard.py` 的判据拒绝写进桌面/文档/下载/图片。
3. **不信任磁盘内容** —— 逐条校验，坏条目丢弃并计数（`stats()['dropped']` 可见），
   条数超上限截断。理由：这个文件是人可编辑的，也可能是半截写入的。

## 与 `TrackerStore` 的关系

只复用它的**原子写套路**（同目录 temp 文件 + `os.replace`），**不继承它的类** ——
`TrackerStore` 与 `EntityTracker` 强耦合（`load_into(tracker)` / `save(tracker)`），
硬套会把提醒语义塞进实体栈的形状里。`agent/tracker_store.py` 的 docstring 里
"复用它的原子写"指的也是**套路**，不是类。
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent.store_guard import is_inside_forbidden_dir

logger = logging.getLogger(__name__)

#: 默认状态文件（相对项目根目录）
DEFAULT_STORE_PATH = os.path.join("data", "agent_reminders.json")

#: 状态文件格式版本
SCHEMA_VERSION = 1

#: 单文件最多接受多少条提醒。这个文件是可编辑的，也可能是半截写入的；
#: 没有上限的话，一个坏文件就能把内存与 UI 撑爆。
MAX_ITEMS = 200


class ReminderStore:
    """提醒表的 JSON 持久化后端（原子写 / 失败降级）"""

    def __init__(self, path: Optional[str] = None, enabled: bool = True) -> None:
        """
        Args:
            path: 状态文件路径；None 用 DEFAULT_STORE_PATH
            enabled: 关闭时所有操作退化为空操作（读返回 None、写返回 False）
        """
        self._enabled = bool(enabled)
        self._writes = 0
        self._reads = 0
        self._dropped = 0

        raw = Path(path or DEFAULT_STORE_PATH).expanduser()

        # 安全边界：不接受把状态写进用户数据目录（判据见 agent/store_guard.py）
        if self._enabled and is_inside_forbidden_dir(raw):
            logger.warning(
                "[reminder_store] 拒绝把提醒写入用户目录（%s），持久化已关闭", raw
            )
            self._enabled = False

        self._path = raw

    # ══════════════════════════════════════════════
    #  读
    # ══════════════════════════════════════════════

    def load(self) -> Optional[Dict[str, Any]]:
        """读取并**校验**磁盘上的提醒表

        Returns:
            清洗后的 payload（`{version, saved_at, counter, items}`）；
            无文件 / 关闭 / 读不动 / 结构不可用时返回 None（调用方按"空"处理）
        """
        if not self._enabled:
            return None

        try:
            if not self._path.exists():
                logger.debug("[reminder_store] 状态文件不存在，按无提醒启动")
                return None
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            # ValueError 覆盖 json 解析失败；文件损坏不该让程序起不来
            logger.warning("[reminder_store] 提醒文件不可用（%s），按无提醒启动", e)
            return None

        if not isinstance(data, dict):
            logger.warning("[reminder_store] 提醒文件顶层不是对象，按无提醒启动")
            return None

        items = data.get("items")
        if not isinstance(items, list):
            logger.warning("[reminder_store] 提醒文件的 items 不是数组，按无提醒启动")
            items = []

        clean: List[Dict[str, Any]] = []
        for raw in items[:MAX_ITEMS]:
            item = self._clean_item(raw)
            if item is None:
                self._dropped += 1
                continue
            clean.append(item)
        if len(items) > MAX_ITEMS:
            logger.warning(
                "[reminder_store] 提醒文件有 %d 条，超过上限 %d，只取前 %d 条",
                len(items), MAX_ITEMS, MAX_ITEMS,
            )

        counter = data.get("counter")
        if not isinstance(counter, int) or counter < 0:
            counter = 0

        self._reads += 1
        logger.info("[reminder_store] 恢复 %d 条未到点提醒（丢弃 %d 条坏数据）",
                    len(clean), self._dropped)
        return {
            "version": SCHEMA_VERSION,
            "saved_at": data.get("saved_at"),
            "counter": counter,
            "items": clean,
        }

    @staticmethod
    def _clean_item(raw: Any) -> Optional[Dict[str, Any]]:
        """校验单条提醒；不可用返回 None（**丢弃而不是抛异常**）"""
        if not isinstance(raw, dict):
            return None
        rid = raw.get("id")
        what = raw.get("what")
        due_at = raw.get("due_at")
        if not isinstance(rid, str) or not rid:
            return None
        if not isinstance(what, str) or not what:
            return None
        if not isinstance(due_at, (int, float)) or isinstance(due_at, bool):
            return None
        when_text = raw.get("when_text")
        return {
            "id": rid,
            "what": what,
            "due_at": float(due_at),
            "when_text": when_text if isinstance(when_text, str) else "",
        }

    # ══════════════════════════════════════════════
    #  写
    # ══════════════════════════════════════════════

    def save(self, payload: Dict[str, Any]) -> bool:
        """原子写（同目录临时文件 + os.replace）

        Returns:
            True=已落盘；False=未启用或失败（失败只记日志，不抛异常）
        """
        if not self._enabled:
            return False

        data = dict(payload)
        data["version"] = SCHEMA_VERSION
        data["saved_at"] = time.time()

        tmp_path: Optional[str] = None
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_path = tempfile.mkstemp(
                prefix=self._path.name + ".", suffix=".tmp", dir=str(self._path.parent)
            )
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_path, self._path)
            tmp_path = None
            self._writes += 1
            logger.debug("[reminder_store] 已落盘 %s", self._path)
            return True
        except OSError as e:
            logger.warning("[reminder_store] 落盘失败（%s），提醒仍在内存里", e)
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
        """供 `/status` 与测试观测"""
        return {
            "enabled": self._enabled,
            "path": str(self._path),
            "writes": self._writes,
            "reads": self._reads,
            "dropped": self._dropped,
        }

    def __repr__(self) -> str:
        # 不屏蔽覆盖率：`repr()` 是可调用的，屏蔽它就是把可达代码伪装成不可达
        # （项目对 pragma 的规矩见 AGENTS.md §16.4，测试里直接断言本方法）
        return f"<ReminderStore enabled={self._enabled} path={self._path}>"
