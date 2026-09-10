"""pipeline 插件：确认语缓存 + 实体栈（含持久化）+ 并行管线

这是装配链的最后一环，把前面各插件产出的能力拼成一条可运行的管线：

    确认语缓存（预热）
    实体栈（可选落盘，重启后仍可解析"那个文件"）
    并行管线（ack ∥ 执行）

清理由本插件返回的组合 disposer 负责：先停润色线程、再落盘实体栈、最后取消预热。
"""

import logging
from typing import Callable, List, Optional

from agent.plugins import (
    SVC_ACK,
    SVC_BUS,
    SVC_CONFIG,
    SVC_EXECUTOR,
    SVC_MEMORY,
    SVC_PIPELINE,
    SVC_PLANNER,
    SVC_REGISTRY,
    SVC_ROUTER,
    SVC_SAFETY,
    SVC_SUMMARIZER,
    SVC_TRACKER,
    SVC_TRACKER_STORE,
    SVC_TTS,
)
from agent.pipeline import AgentPipeline
from agent.tracker import EntityTracker
from agent.tracker_store import TrackerStore
from services.ack_cache import AckCache

logger = logging.getLogger(__name__)


def _build_ack(ctx, cfg) -> Optional[AckCache]:
    """确认语缓存（含长尾动态 LRU）"""
    if not cfg.ack_enabled:
        return None

    synthesize = ctx.use_or(SVC_TTS)
    ack = AckCache(warmup_async=True, lru_size=cfg.ack_lru_size)

    if cfg.ack_warmup and synthesize is not None:
        try:
            ack.warm_up(synthesize)
        except Exception as e:
            logger.warning("[pipeline_plugin] 确认语预热启动失败: %s", e)
    elif synthesize is None:
        logger.info("[pipeline_plugin] 未提供 TTS，跳过确认语预热")

    ctx.provide(SVC_ACK, ack)
    return ack


def _build_tracker(ctx, cfg):
    """实体栈 + 可选持久化后端"""
    tracker = EntityTracker()
    ctx.provide(SVC_TRACKER, tracker)

    store = None
    if cfg.tracker_persist:
        store = TrackerStore(path=cfg.tracker_store, enabled=True)
        ctx.provide(SVC_TRACKER_STORE, store)
        logger.info("[pipeline_plugin] 实体栈持久化已启用: %s", store.path)
    else:
        logger.info("[pipeline_plugin] 实体栈为内存模式（未启用落盘）")

    return tracker, store


def setup(ctx, **config) -> Optional[Callable[[], None]]:
    """构造确认语缓存、实体栈与并行管线"""
    cfg = ctx.use(SVC_CONFIG)

    ack = _build_ack(ctx, cfg)
    tracker, store = _build_tracker(ctx, cfg)

    pipeline = AgentPipeline(
        bus=ctx.use(SVC_BUS),
        router=ctx.use(SVC_ROUTER),
        safety=ctx.use(SVC_SAFETY),
        executor=ctx.use(SVC_EXECUTOR),
        registry=ctx.use(SVC_REGISTRY),
        ack_cache=ack,
        enabled=cfg.enabled,
        summarizer=ctx.use(SVC_SUMMARIZER),
        tracker=tracker,
        tracker_store=store,
        async_refine=cfg.async_refine,
        # 规划器是可选能力：未装（或配置关闭）时管线把 action="plan" 一律转闲聊
        planner=ctx.use_or(SVC_PLANNER),
        # 长期记忆同为可选：未装时既不记情景、也不取偏好提示
        memory=ctx.use_or(SVC_MEMORY),
    )
    ctx.provide(SVC_PIPELINE, pipeline)

    logger.info(
        "[pipeline_plugin] 装配完成 (enabled=%s, ack=%s, refine=%s, planner=%s, memory=%s)",
        cfg.enabled, ack is not None, cfg.async_refine,
        ctx.has(SVC_PLANNER), ctx.has(SVC_MEMORY),
    )

    disposers: List[Callable[[], None]] = [pipeline.stop]
    if store is not None:
        disposers.append(lambda: store.flush(tracker))
    if ack is not None:
        disposers.append(ack.close)
    return _combine(disposers)


def _combine(disposers: List[Callable[[], None]]) -> Callable[[], None]:
    """顺序执行一组 disposer（单个失败不中断其余）"""
    def dispose() -> None:
        for d in disposers:
            try:
                d()
            except Exception as e:
                logger.warning("[pipeline_plugin] 清理失败: %s", e)

    return dispose
