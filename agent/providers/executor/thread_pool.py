"""线程池执行器（Service Provider）

用 ThreadPoolExecutor 跑工具调用，避免阻塞语音管线线程。

设计要点：
- 提交立即返回（不阻塞调用方）
- 单任务超时保护（超时后结果标记为失败，线程仍在后台等待自然结束）
- 取消为协作式：任务开始前检查 cancelled 标志
- 完成回调在**执行线程**内调用——回调方需自行做线程安全处理
"""

import logging
import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable, Dict, Optional

from agent.seams.executor import ExecutorService, TaskCallback, TaskHandle
from agent.tools.base import ToolResult
from agent.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

#: 默认线程池大小（性能档 low 下不宜过多，避免抢占语音线程）
DEFAULT_POOL_SIZE = 4

#: 默认单任务超时（秒）
DEFAULT_TIMEOUT = 60


class ThreadPoolExecutorProvider(ExecutorService):
    """线程池执行器

    用法：
        ex = ThreadPoolExecutorProvider(registry)
        task_id = ex.submit(rid, "file_search", {"pattern": "*"}, on_done)
        # on_done(handle, ToolResult) 在执行线程内被调用
        ex.cancel(task_id)
    """

    capability_name = "executor"

    def __init__(
        self,
        registry: ToolRegistry,
        pool_size: int = DEFAULT_POOL_SIZE,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        """
        Args:
            registry: 工具注册表
            pool_size: 线程池大小
            timeout: 单任务超时秒数
        """
        self._registry = registry
        self._pool = ThreadPoolExecutor(
            max_workers=max(1, pool_size),
            thread_name_prefix="agent-exec",
        )
        self._timeout = timeout
        self._handles: Dict[str, TaskHandle] = {}
        self._futures: Dict[str, Future] = {}
        self._lock = threading.Lock()
        self._closed = False

        logger.info("[executor] 线程池执行器就绪 (workers=%d, timeout=%.0fs)", pool_size, timeout)

    # ══════════════════════════════════════════════
    #  提交
    # ══════════════════════════════════════════════

    def submit(
        self,
        request_id: str,
        action: str,
        params: Dict[str, Any],
        callback: TaskCallback,
    ) -> str:
        """提交任务（立即返回）"""
        if self._closed:
            logger.warning("[executor] 已关闭，拒绝新任务: %s", action)
            return ""

        task_id = uuid.uuid4().hex[:10]
        handle = TaskHandle(
            task_id=task_id,
            request_id=request_id,
            action=action,
            params=dict(params or {}),
        )

        with self._lock:
            self._handles[task_id] = handle

        try:
            future = self._pool.submit(self._run, handle, callback)
            with self._lock:
                self._futures[task_id] = future
        except RuntimeError as e:
            # 线程池已关闭
            logger.error("[executor] 提交失败: %s", e)
            with self._lock:
                self._handles.pop(task_id, None)
            return ""

        logger.debug("[executor] 提交 %s: %s(%s)", task_id, action, params)
        return task_id

    def _run(self, handle: TaskHandle, callback: TaskCallback) -> None:
        """执行体（运行在池线程内）"""
        t0 = time.perf_counter()

        # 协作式取消：开始前检查
        if handle.cancelled:
            logger.info("[executor] 任务在开始前已被取消: %s", handle.task_id)
            self._finish(handle)
            self._safe_callback(callback, handle, ToolResult.fail("任务已取消"))
            return

        try:
            result = self._registry.execute(handle.action, handle.params)
        except Exception as e:
            # 注意：`ToolRegistry.execute` 内部已把 ParamError / ToolError /
            # 裸异常统一归一化成 ToolResult（含 user_message 优先），所以这里
            # 只在"注册表本身出问题"这种非预期情况下才会命中 —— 不要在此处
            # 再补一层参数错误映射，那是死代码（P3 收尾时踩过这个坑）。
            logger.error("[executor] 任务异常 %s: %s", handle.task_id, e, exc_info=True)
            result = ToolResult.fail(f"执行出错了：{e}", emotion="sad")

        elapsed_ms = (time.perf_counter() - t0) * 1000

        if handle.cancelled:
            logger.info("[executor] 任务完成后发现已取消，丢弃结果: %s", handle.task_id)
            self._finish(handle)
            return

        if elapsed_ms > self._timeout * 1000:
            logger.warning("[executor] 任务超时 %.0fms: %s", elapsed_ms, handle.task_id)
            result = ToolResult.fail(
                f"这个操作有点久（{elapsed_ms / 1000:.0f}秒），我先停下了",
                emotion="sad",
            )

        self._finish(handle)
        self._safe_callback(callback, handle, result)

    def _safe_callback(
        self,
        callback: TaskCallback,
        handle: TaskHandle,
        result: ToolResult,
    ) -> None:
        """回调异常隔离：回调崩溃不影响执行器"""
        if callback is None:
            return
        try:
            callback(handle, result)
        except Exception as e:
            logger.error("[executor] 回调异常 %s: %s", handle.task_id, e, exc_info=True)

    def _finish(self, handle: TaskHandle) -> None:
        """清理任务记录"""
        with self._lock:
            self._handles.pop(handle.task_id, None)
            self._futures.pop(handle.task_id, None)

    # ══════════════════════════════════════════════
    #  取消
    # ══════════════════════════════════════════════

    def cancel(self, task_id: str) -> bool:
        """取消任务（尽力而为）"""
        with self._lock:
            handle = self._handles.get(task_id)
            future = self._futures.get(task_id)

        if handle is None:
            return True  # 已不存在，视为成功

        handle.cancelled = True

        # 尚未开始执行 → 尝试直接取消
        if future is not None and future.cancel():
            logger.info("[executor] 任务已取消（未开始）: %s", task_id)
            self._finish(handle)
            return True

        logger.info("[executor] 任务标记取消（执行中，结果将被丢弃）: %s", task_id)
        return True

    def cancel_by_request(self, request_id: str) -> int:
        """取消某请求下的全部任务"""
        with self._lock:
            targets = [h.task_id for h in self._handles.values() if h.request_id == request_id]

        count = 0
        for tid in targets:
            if self.cancel(tid):
                count += 1
        return count

    def cancel_all(self) -> int:
        """取消全部未完成任务"""
        with self._lock:
            targets = list(self._handles.keys())
        return sum(1 for tid in targets if self.cancel(tid))

    # ══════════════════════════════════════════════
    #  状态
    # ══════════════════════════════════════════════

    def pending_count(self) -> int:
        """未完成任务数"""
        with self._lock:
            return len(self._handles)

    def is_pending(self, task_id: str) -> bool:
        """指定任务是否仍在执行"""
        with self._lock:
            return task_id in self._handles

    def handles(self) -> list:
        """当前任务句柄快照"""
        with self._lock:
            return list(self._handles.values())

    def shutdown(self, wait: bool = False) -> None:
        """关闭执行器"""
        if self._closed:
            return
        self._closed = True

        # 标记全部取消，避免关闭后仍回调业务逻辑
        with self._lock:
            for handle in self._handles.values():
                handle.cancelled = True

        try:
            self._pool.shutdown(wait=wait, cancel_futures=True)
        except TypeError:
            # Python < 3.9 不支持 cancel_futures
            self._pool.shutdown(wait=wait)

        with self._lock:
            self._handles.clear()
            self._futures.clear()

        logger.info("[executor] 已关闭")
