"""提醒调度器（`agent.productivity.reminder_scheduler`）

## 为什么必须有这个模块

`ReminderTool` 从 P2 起就能"设置提醒"并回答
**"好的，30分钟后我会提醒你喝水"**，但**全项目没有任何一处调用它的
`due_now()`** —— 也没有注入 `on_reminder_due`。于是：

    工具对用户说"我会提醒你" → 时间到了 → 什么都没发生（永远）

这比"没有提醒功能"更糟：**它是一个对用户撒谎的功能**。
本模块补上被漏掉的那一环：一个周期性调用 `due_now()` 的调度线程，
把到点的提醒变成总线上的 `reminder.due` 事件（由 UI 负责出声）。

## 线程与生命周期

- 后台 **daemon** 线程，默认 1s 一跳（到点误差 ≤1s，对提醒足够）
- `stop()` 置位 + `join(超时)`，`AgentStack.dispose()` 会经由插件 disposer 调到它，
  不会留下悬挂线程（对应债务 D8 的教训）
- 线程里任何异常都不允许炸掉线程：包一层 `except` 记日志后继续下一跳，
  否则一次异常就永久失去提醒能力，而用户毫不知情
- 与 `ReminderTool.execute()` 的并发：`execute` 在工具线程池里跑，
  调度线程只做 `due_now()`。两者都用 `threading.Lock` 串行化，
  不依赖"dict 操作恰好是原子的"这种 CPython 实现细节

## 未做（登记为遗留）

提醒只存在内存里 —— **进程重启后未到点的提醒会丢**。
要修需要落盘（可复用 `agent/tracker_store.py` 的原子写套路），
本轮不做；在文档里如实登记，不在代码里假装做到。
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, List, Optional

logger = logging.getLogger(__name__)

#: 调度心跳间隔（秒）
DEFAULT_TICK = 1.0


class ReminderScheduler:
    """周期性检查提醒是否到点，并把到点提醒发到事件总线

    用法：
        sched = ReminderScheduler(tool, on_due=lambda item: bus.emit(...))
        sched.start()
        ...
        sched.stop()
    """

    def __init__(
        self,
        tool: Any,
        on_due: Optional[Callable[[Any], None]] = None,
        tick: float = DEFAULT_TICK,
    ) -> None:
        """
        Args:
            tool: 带 `due_now()` / `pending()` 的提醒工具（鸭子类型，便于测试）
            on_due: 到点回调，收到 Reminder 对象；异常会被吞掉并记日志。
                ⚠️ **生产接线走的是 `ReminderTool.on_due`（插件注入），不是这里** ——
                调度器只负责"按时去问"，不负责"怎么喊"。若两边都接上，
                同一条提醒会**被派发两次**（调度器的回调一次、工具自己的回调一次），
                而且工具那个回调的形状是 `(id, what)`，按 `(item)` 调会 TypeError。
                所以：**二选一，别都接**。
            tick: 心跳间隔（秒）
        """
        self._tool = tool
        self._on_due = on_due
        self._tick = max(0.05, float(tick))
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._fired = 0

    # ── 生命周期 ──

    def start(self) -> None:
        """启动调度线程（重复调用无副作用）"""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._loop, name="reminder-scheduler", daemon=True,
            )
            self._thread.start()
        logger.info("[reminder] 调度器已启动（每 %.1fs 一跳）", self._tick)

    def stop(self, timeout: float = 2.0) -> None:
        """停止调度线程并等待其退出"""
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)
        with self._lock:
            self._thread = None
        logger.info("[reminder] 调度器已停止（累计播报 %d 条）", self._fired)

    @property
    def running(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    @property
    def fired_count(self) -> int:
        """累计播报条数（供 /status 与测试断言）"""
        return self._fired

    def pending(self) -> List[Any]:
        """当前未到点的提醒（透传工具，便于观测）"""
        try:
            return list(self._tool.pending())
        except Exception as e:
            logger.warning("[reminder] 读取待提醒失败: %s", e)
            return []

    # ── 内部 ──

    def _loop(self) -> None:
        while not self._stop.wait(self._tick):
            try:
                self.run_once()
            except Exception as e:                     # 心跳绝不因异常退出
                logger.warning("[reminder] 调度心跳异常（继续运行）: %s", e)

    def run_once(self) -> int:
        """检查一轮并派发到点提醒（同步可调，测试直接用它，不必等心跳）

        Returns:
            本轮派发出的条数
        """
        try:
            due = self._tool.due_now()
        except Exception as e:
            logger.warning("[reminder] due_now 失败: %s", e)
            return 0

        for item in due or []:
            self._fired += 1
            if self._on_due is None:
                continue
            try:
                self._on_due(item)
            except Exception as e:                     # 回调失败不影响其它提醒
                logger.warning("[reminder] 到点回调失败: %s", e)
        return len(due or [])
