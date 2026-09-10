"""P2-1 确认语 LRU 动态缓存测试（偿还技术债 D2）

覆盖：
- LRU 基本语义：命中 / 未命中 / 提升优先级
- 淘汰：超容量淘汰最久未用，evictions 计数
- 边界：maxsize=0 直通、空文本、合成失败不缓存
- 预热短语反查命中
- 线程安全：并发不炸、容量不越界
- 管线接入：确认问句第二次起命中，延迟 <10ms
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import pytest

project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from agent.pipeline import AgentPipeline
from agent.providers.executor.thread_pool import ThreadPoolExecutorProvider
from agent.providers.router.rule_router import RuleRouter
from agent.providers.safety.basic_guard import BasicGuard
from agent.tools.file_tools import all_file_tools
from agent.tools.registry import ToolRegistry
from core.kernel.events import EventBus, EventTypes
from services.ack_cache import DEFAULT_LRU_SIZE, AckCache


# ══════════════════════════════════════════════════
#  Fixtures
# ══════════════════════════════════════════════════

@pytest.fixture
def synth():
    """计数型假 TTS"""
    calls = []

    def _s(text: str):
        calls.append(text)
        return f"AUDIO::{text}".encode("utf-8")

    _s.calls = calls
    return _s


@pytest.fixture
def cache(synth):
    """无预热短语、纯动态 LRU 的缓存（预热是为了拿到 _synthesize）"""
    c = AckCache(phrases={}, warmup_async=False, lru_size=4)
    c.warm_up(synth)
    return c


@pytest.fixture
def sandbox(tmp_path):
    d = tmp_path / "Desktop"
    d.mkdir()
    return d


@pytest.fixture
def guard(sandbox):
    g = BasicGuard(whitelist=[str(sandbox)], audit_enabled=False)
    yield g
    g.close()


# ══════════════════════════════════════════════════
#  LRU 基本语义
# ══════════════════════════════════════════════════

class TestLruBasics:

    def test_miss_then_hit(self, cache, synth):
        """首次合成，二次命中，synthesize 只调一次"""
        a1 = cache.get_or_synthesize("找到 1 个文件，要删吗？")
        a2 = cache.get_or_synthesize("找到 1 个文件，要删吗？")

        assert a1 == a2
        assert len(synth.calls) == 1, "第二次不应再合成"
        st = cache.lru_stats()
        assert st["hits"] == 1 and st["misses"] == 1 and st["size"] == 1

    def test_distinct_texts_are_separate_entries(self, cache, synth):
        cache.get_or_synthesize("甲")
        cache.get_or_synthesize("乙")

        assert cache.lru_size() == 2
        assert len(synth.calls) == 2

    def test_empty_text_returns_none(self, cache, synth):
        assert cache.get_or_synthesize("") is None
        assert cache.get_or_synthesize("   ") is None
        assert cache.get_or_synthesize(None) is None
        assert synth.calls == [], "空文本不应触发合成"

    def test_default_maxsize_constant(self):
        assert AckCache(phrases={}).lru_maxsize == DEFAULT_LRU_SIZE
        assert DEFAULT_LRU_SIZE == 64

    def test_whitespace_is_normalized(self, cache, synth):
        """前后空白不同但内容相同 → 命中同一条"""
        cache.get_or_synthesize("  确定要删除吗？  ")
        cache.get_or_synthesize("确定要删除吗？")
        assert len(synth.calls) == 1
        assert cache.lru_size() == 1


# ══════════════════════════════════════════════════
#  淘汰
# ══════════════════════════════════════════════════

class TestEviction:

    def test_evicts_least_recently_used(self, cache, synth):
        """容量 4：塞 5 条 → 最久未用的「甲」被淘汰"""
        for t in ("甲", "乙", "丙", "丁", "戊"):
            cache.get_or_synthesize(t)

        st = cache.lru_stats()
        assert st["size"] == 4
        assert st["evictions"] == 1

        # 「甲」已被淘汰 → 再取会重新合成
        before = len(synth.calls)
        cache.get_or_synthesize("甲")
        assert len(synth.calls) == before + 1, "被淘汰的条目应重新合成"

    def test_access_refreshes_priority(self, cache, synth):
        """取用「甲」后它变成最近使用 → 应淘汰「乙」而不是「甲」"""
        for t in ("甲", "乙", "丙", "丁"):
            cache.get_or_synthesize(t)
        cache.get_or_synthesize("甲")          # 甲 → 最近使用
        cache.get_or_synthesize("戊")          # 触发淘汰，应淘汰乙

        before = len(synth.calls)
        cache.get_or_synthesize("甲")
        assert len(synth.calls) == before, "甲 应还在缓存里（命中不合成）"

        cache.get_or_synthesize("乙")
        assert len(synth.calls) == before + 1, "乙 应已被淘汰"

    def test_size_never_exceeds_maxsize(self, synth):
        c = AckCache(phrases={}, warmup_async=False, lru_size=3)
        c.warm_up(synth)
        for i in range(20):
            c.get_or_synthesize(f"短语{i}")
            assert c.lru_size() <= 3
        assert c.lru_stats()["evictions"] == 17

    def test_maxsize_zero_is_passthrough(self, synth):
        """maxsize=0 → 关闭缓存，每次都合成，且不产生淘汰"""
        c = AckCache(phrases={}, warmup_async=False, lru_size=0)
        c.warm_up(synth)

        for _ in range(3):
            assert c.get_or_synthesize("同一条") is not None

        assert len(synth.calls) == 3, "关闭缓存后每次都要合成"
        st = c.lru_stats()
        assert st["size"] == 0 and st["hits"] == 0 and st["misses"] == 3
        assert st["maxsize"] == 0

    def test_negative_maxsize_clamped(self, synth):
        c = AckCache(phrases={}, warmup_async=False, lru_size=-5)
        c.warm_up(synth)
        assert c.lru_maxsize == 0
        assert c.get_or_synthesize("x") is not None
        assert c.lru_size() == 0


# ══════════════════════════════════════════════════
#  失败路径
# ══════════════════════════════════════════════════

class TestSynthFailures:

    def test_none_result_not_cached(self, cache):
        assert cache.get_or_synthesize("坏短语", lambda t: None) is None
        assert cache.lru_size() == 0

        # 后续再来仍会尝试合成（未被"负缓存"污染）
        assert cache.get_or_synthesize("坏短语", lambda t: b"ok") == b"ok"
        assert cache.lru_size() == 1

    def test_empty_bytes_not_cached(self, cache):
        assert cache.get_or_synthesize("空音频", lambda t: b"") is None
        assert cache.lru_size() == 0

    def test_exception_swallowed(self, cache, caplog):
        def boom(t):
            raise RuntimeError("TTS 挂了")

        with caplog.at_level("WARNING"):
            assert cache.get_or_synthesize("会炸", boom) is None

        assert cache.lru_size() == 0
        assert any("动态合成失败" in r.message for r in caplog.records)

    def test_no_synthesize_available(self):
        """既未预热也无传入合成器 → 返回 None，不抛异常"""
        c = AckCache(phrases={}, warmup_async=False, lru_size=4)
        assert c.get_or_synthesize("随便") is None

    def test_injected_synthesize_overrides_stored(self, cache, synth):
        """显式传入的合成器优先于 warm_up 存的那个"""
        got = cache.get_or_synthesize("覆盖", lambda t: b"OVERRIDE")
        assert got == b"OVERRIDE"
        assert synth.calls == []


# ══════════════════════════════════════════════════
#  预热短语反查
# ══════════════════════════════════════════════════

class TestWarmedLookup:

    def test_warmed_phrase_hits_without_synth(self, synth):
        """预热过的短语走反查命中，不再合成"""
        phrases = {"default": ["好的，我看看~", "稍等哦~"]}
        c = AckCache(phrases=phrases, warmup_async=False, lru_size=4)
        c.warm_up(synth)
        warmed_calls = len(synth.calls)
        assert warmed_calls == 2, "预热应合成两条"

        audio = c.get_or_synthesize("好的，我看看~")
        assert audio is not None
        assert len(synth.calls) == warmed_calls, "预热短语不应再次合成"

        st = c.lru_stats()
        assert st["hits"] == 1 and st["misses"] == 0
        assert st["size"] == 1, "反查命中后应提升进 LRU"

    def test_warmed_hit_respects_maxsize(self, synth):
        """反查命中也会触发淘汰，容量不越界"""
        phrases = {"default": [f"短语{i}" for i in range(3)]}
        c = AckCache(phrases=phrases, warmup_async=False, lru_size=2)
        c.warm_up(synth)
        for i in range(3):
            c.get_or_synthesize(f"短语{i}")
        assert c.lru_size() == 2


# ══════════════════════════════════════════════════
#  线程安全
# ══════════════════════════════════════════════════

class TestThreadSafety:

    def test_concurrent_access_keeps_bound(self, synth):
        """8 线程 × 50 次并发：不抛异常，容量不越界"""
        c = AckCache(phrases={}, warmup_async=False, lru_size=8)
        c.warm_up(synth)

        errors = []

        def worker(tid):
            try:
                for i in range(50):
                    c.get_or_synthesize(f"线程{tid}-短语{i % 20}")
            except Exception as e:                      # pragma: no cover - 失败才记录
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert not errors, f"并发异常: {errors}"
        assert c.lru_size() <= 8
        st = c.lru_stats()
        assert st["size"] == st["maxsize"], "并发写满后应恰好等于容量"
        assert st["hits"] + st["misses"] > 0


# ══════════════════════════════════════════════════
#  生命周期
# ══════════════════════════════════════════════════

class TestLifecycle:

    def test_clear_resets_lru_and_stats(self, cache):
        cache.get_or_synthesize("甲")
        cache.get_or_synthesize("甲")
        assert cache.lru_size() == 1

        cache.clear()

        assert cache.lru_size() == 0
        st = cache.lru_stats()
        assert st == {"hits": 0, "misses": 0, "evictions": 0, "size": 0, "maxsize": 4}
        assert cache.cached_count() == 0
        assert cache.warmed is False

    def test_stats_snapshot_is_a_copy(self, cache):
        st = cache.lru_stats()
        st["hits"] = 999
        assert cache.lru_stats()["hits"] == 0


# ══════════════════════════════════════════════════
#  预热生命周期（此前未被统计覆盖）
# ══════════════════════════════════════════════════

class TestWarmupLifecycle:

    def test_warm_up_without_synthesize_returns_early(self):
        """warm_up(None) → _do_warm_up 直接返回，不抛异常"""
        c = AckCache(phrases={"default": ["甲"]}, warmup_async=False, lru_size=4)
        c.warm_up(None)                     # type: ignore[arg-type]

        assert c.warmed is False
        assert c.cached_count() == 0

    def test_cancel_before_warmup_skips_everything(self, synth):
        """预热前取消 → 一条都不合成"""
        c = AckCache(phrases={"default": ["甲", "乙"]}, warmup_async=False, lru_size=4)
        c.cancel()
        c.warm_up(synth)

        assert synth.calls == []
        assert c.cached_count() == 0

    def test_cancel_during_warmup_stops_immediately(self):
        """合成途中取消（应用关闭场景）→ 内层循环立即收手"""
        phrases = {"default": ["甲", "乙", "丙"]}
        calls = []

        c = AckCache(phrases=phrases, warmup_async=False, lru_size=4)

        def cancelling_synth(text):
            calls.append(text)
            c.cancel()                      # 第一次合成后立刻取消
            return b"AUDIO"

        c.warm_up(cancelling_synth)

        assert len(calls) == 1, f"取消后不应继续合成，实际合成了 {calls}"
        assert c.warmed is False, "取消后不应标记为已预热"

    def test_cancel_sets_flag(self, cache):
        assert cache._cancelled is False
        cache.cancel()
        assert cache._cancelled is True

    def test_wait_ready_without_thread(self, synth):
        """同步预热（无后台线程）→ wait_ready 直接返回 warmed"""
        c = AckCache(phrases={"default": ["甲"]}, warmup_async=False, lru_size=4)
        assert c.wait_ready(timeout=1.0) is False, "未预热时应为 False"

        c.warm_up(synth)
        assert c.wait_ready(timeout=1.0) is True


# ══════════════════════════════════════════════════
#  close：取消并 join 预热线程（防 teardown 竞态）
# ══════════════════════════════════════════════════

class TestClose:

    def test_close_without_thread_is_safe(self, cache):
        """从未异步预热 → close 只置标志，不抛异常"""
        cache.close()
        assert cache._cancelled is True

    def test_close_joins_warmup_thread(self):
        """异步预热中 close → 线程被 join 退出"""
        release = threading.Event()
        started = threading.Event()

        def blocking_synth(text):
            started.set()
            release.wait(timeout=5)          # 模拟慢 TTS，直到 release 才返回
            return b"AUDIO"

        c = AckCache(phrases={"default": ["甲", "乙"]}, warmup_async=True, lru_size=4)
        c.warm_up(blocking_synth)
        assert started.wait(timeout=5), "预热线程应已启动"

        thread = c._warmup_thread
        release.set()                        # 让合成返回，线程得以推进/退出
        c.close(timeout=3.0)

        assert not thread.is_alive(), "close 后预热线程不应仍存活"
        assert c._cancelled is True

    def test_close_timeout_bounded(self):
        """合成长时间不返回 → close 在 timeout 后返回，不永久阻塞"""
        hold = threading.Event()

        def stuck_synth(text):
            hold.wait(timeout=10)
            return b"AUDIO"

        c = AckCache(phrases={"default": ["甲"]}, warmup_async=True, lru_size=4)
        c.warm_up(stuck_synth)
        time.sleep(0.05)

        t0 = time.perf_counter()
        c.close(timeout=0.2)
        elapsed = time.perf_counter() - t0

        assert elapsed < 2.0, f"close 应受 timeout 约束，实际 {elapsed:.2f}s"
        hold.set()                           # 放行，避免线程悬挂影响后续测试

    def test_close_from_warmup_thread_does_not_deadlock(self):
        """预热线程内部调用 close（自 join）→ 直接返回，不死锁"""
        c = AckCache(phrases={"default": ["甲"]}, warmup_async=False, lru_size=4)
        snapshot = {}

        def synth(text):
            c._warmup_thread = threading.current_thread()   # 模拟"自己在预热线程里"
            c.close(timeout=1.0)                            # 自 join 必须被拦住
            snapshot["ok"] = True
            return b"AUDIO"

        c.warm_up(synth)                     # warmup_async=False → 同一线程执行
        assert snapshot.get("ok") is True


# ══════════════════════════════════════════════════
#  管线接入
# ══════════════════════════════════════════════════

class TestPipelineIntegration:

    @pytest.fixture
    def pipeline(self, guard, sandbox, synth):
        """带动态 LRU 的管线（合成器故意慢，便于区分命中/未命中）"""

        def slow_synth(text):
            time.sleep(0.02)
            return f"AUDIO::{text}".encode("utf-8")

        slow_synth.calls = synth.calls

        cache = AckCache(phrases={}, warmup_async=False, lru_size=16)
        cache.warm_up(slow_synth)

        bus = EventBus()
        reg = ToolRegistry()
        for t in all_file_tools(guard):
            reg.register(t)
        ex = ThreadPoolExecutorProvider(reg, pool_size=2, timeout=5)
        p = AgentPipeline(bus, RuleRouter(), guard, ex, reg, cache)
        p.start()
        yield p, bus, slow_synth
        p.stop()
        ex.shutdown(wait=True)

    def _confirm_once(self, pipeline, bus, sandbox):
        """触发一次删除确认，返回 (耗时ms, 事件数据)"""
        got = []
        dispose = bus.on(EventTypes.FEEDBACK_CONFIRM, lambda e: got.append(e.data))
        t0 = time.perf_counter()
        pipeline.handle_text("删除截图", None)
        elapsed = (time.perf_counter() - t0) * 1000
        dispose()
        assert got, "应发出确认请求"
        return elapsed, got[0]

    def test_confirm_event_carries_audio(self, pipeline, sandbox):
        """确认事件带 audio 字段（首次为合成结果）"""
        p, bus, slow = pipeline
        (sandbox / "截图_1.png").write_text("x", encoding="utf-8")

        _, data = self._confirm_once(p, bus, sandbox)
        assert "audio" in data
        assert data["audio"] is not None, "有合成器时应产出音频"

    def test_second_confirm_is_cached_and_fast(self, pipeline, sandbox):
        """重复确认第二次命中 LRU → 延迟 <10ms 且不再合成"""
        p, bus, slow = pipeline
        (sandbox / "截图_1.png").write_text("x", encoding="utf-8")

        first_ms, first = self._confirm_once(p, bus, sandbox)
        calls_after_first = len(slow.calls)

        second_ms, second = self._confirm_once(p, bus, sandbox)

        assert second["question"] == first["question"], "两次问句应一致才能命中"
        assert second["audio"] == first["audio"]
        assert len(slow.calls) == calls_after_first, "第二次不应再合成"
        assert second_ms < 10, f"命中延迟应 <10ms，实际 {second_ms:.1f}ms"
        assert first_ms >= 20, f"首次应走合成（>20ms），实际 {first_ms:.1f}ms"

    def test_stats_expose_lru(self, pipeline, sandbox):
        """pipeline.stats() 暴露 LRU 指标"""
        p, bus, slow = pipeline
        (sandbox / "截图_1.png").write_text("x", encoding="utf-8")
        self._confirm_once(p, bus, sandbox)
        self._confirm_once(p, bus, sandbox)

        st = p.stats()
        for key in ("ack_lru_size", "ack_lru_hits", "ack_lru_misses", "ack_lru_evictions"):
            assert key in st, f"缺少统计键 {key}"
        assert st["ack_lru_hits"] >= 1
        assert st["ack_lru_size"] >= 1

    def test_pipeline_without_ack_cache(self, guard, sandbox):
        """无 AckCache 时确认流程照常（audio=None，不抛异常）"""
        bus = EventBus()
        reg = ToolRegistry()
        for t in all_file_tools(guard):
            reg.register(t)
        ex = ThreadPoolExecutorProvider(reg, pool_size=2, timeout=5)
        p = AgentPipeline(bus, RuleRouter(), guard, ex, reg, None)
        p.start()
        try:
            (sandbox / "截图_1.png").write_text("x", encoding="utf-8")
            got = []
            dispose = bus.on(EventTypes.FEEDBACK_CONFIRM, lambda e: got.append(e.data))
            p.handle_text("删除截图", None)
            dispose()

            assert got, "应发出确认请求"
            assert got[0]["audio"] is None
            assert p.stats()["ack_lru_size"] == 0
        finally:
            p.stop()
            ex.shutdown(wait=True)
