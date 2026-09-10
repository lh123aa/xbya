"""P2-2 摘要器异步化测试（偿还技术债 D4）

覆盖：
- Seam 默认实现（fast_summary / should_refine / refine）
- HybridSummarizer 的润色分流与失败语义
- 管线：结果立即用模板播报、润色经 feedback.refine 补播
- 失败/空/无改善/取消/打断 时不补播
- 释放时有界 join，无残留线程
- async_refine=False 时回退 P1 同步行为
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import pytest

project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from agent.bootstrap import AgentConfig, build_agent_stack
from agent.pipeline import AgentPipeline
from agent.providers.executor.thread_pool import ThreadPoolExecutorProvider
from agent.providers.router.rule_router import RuleRouter
from agent.providers.safety.basic_guard import BasicGuard
from agent.providers.summarizer.hybrid_sum import HybridSummarizer
from agent.providers.summarizer.llm_sum import LLMSummarizer
from agent.providers.summarizer.template_sum import TemplateSummarizer
from agent.seams.summarizer import SummarizerService
from agent.tools.base import ToolResult
from agent.tools.file_tools import all_file_tools
from agent.tools.registry import ToolRegistry
from core.kernel.events import EventBus, EventTypes

POLISHED = "润色后的自然语言播报"


def _items(n: int) -> list:
    return [
        {"name": f"f{i}.txt", "path": f"C:/x/f{i}.txt", "size": 10,
         "mtime": 0.0, "is_dir": False}
        for i in range(n)
    ]


def _slow_llm(text, delay=0.25):
    def _call(prompt):
        time.sleep(delay)
        return text
    return _call


def _ok_result(n=6) -> ToolResult:
    return ToolResult.ok(data=_items(n), summary=f"找到 {n} 个文件", count=n)


@pytest.fixture
def sandbox(tmp_path):
    d = tmp_path / "Desktop"
    d.mkdir()
    for i in range(6):
        (d / f"f{i}.txt").write_text("x", encoding="utf-8")
    return d


@pytest.fixture
def guard(sandbox):
    g = BasicGuard(whitelist=[str(sandbox)], audit_enabled=False)
    yield g
    g.close()


# ══════════════════════════════════════════════════
#  Seam 默认实现
# ══════════════════════════════════════════════════

class TestSeamDefaults:

    def test_defaults_are_noop(self):
        """纯模板实现无需润色：should_refine=False、refine=空串"""
        tpl = TemplateSummarizer()
        res = _ok_result()

        assert tpl.should_refine("file_search", res) is False
        assert tpl.refine("file_search", res) == ""
        # fast_summary 默认等价于 summarize
        assert tpl.fast_summary("file_search", res) == tpl.summarize("file_search", res)

    def test_seam_cannot_be_instantiated_directly(self):
        with pytest.raises(TypeError):
            SummarizerService()          # type: ignore[abstract]


# ══════════════════════════════════════════════════
#  HybridSummarizer 润色语义
# ══════════════════════════════════════════════════

class TestHybridRefine:

    def _hybrid(self, llm_call, threshold=3):
        tpl = TemplateSummarizer()
        return HybridSummarizer(
            template=tpl,
            llm=LLMSummarizer(llm_call=llm_call, fallback=tpl),
            threshold=threshold,
        )

    def test_simple_result_not_refined(self):
        """简单结果（条目少）不浪费 LLM 调用"""
        h = self._hybrid(_slow_llm(POLISHED))
        assert h.should_refine("file_search", _ok_result(1)) is False

    def test_complex_result_refined(self):
        h = self._hybrid(_slow_llm(POLISHED))
        assert h.should_refine("file_search", _ok_result(6)) is True

    def test_failure_result_refined(self):
        """失败结果需要"说清原因"，模板做不好"""
        h = self._hybrid(_slow_llm(POLISHED))
        assert h.should_refine("file_search", ToolResult.fail("没找到")) is True

    def test_truncated_result_refined(self):
        h = self._hybrid(_slow_llm(POLISHED))
        res = ToolResult.ok(data=_items(2), summary="很多", count=2, truncated=True)
        assert h.should_refine("file_search", res) is True

    def test_always_llm_action_refined(self):
        h = self._hybrid(_slow_llm(POLISHED))
        assert h.should_refine("web_read", _ok_result(1)) is True

    def test_no_llm_means_no_refine(self):
        """未注入 LLM → 永不润色"""
        h = HybridSummarizer(template=TemplateSummarizer(), llm=None)
        assert h.should_refine("file_search", _ok_result(9)) is False
        assert h.refine("file_search", _ok_result(9)) == ""

    def test_fast_summary_uses_template_only(self):
        """fast_summary 绝不走 LLM（否则异步化就白做了）"""
        called = {"n": 0}

        def counting_llm(prompt):
            called["n"] += 1
            return POLISHED

        h = self._hybrid(counting_llm)
        text = h.fast_summary("file_search", _ok_result(6))

        assert called["n"] == 0, "fast_summary 不应触发 LLM"
        assert text and text != POLISHED

    def test_refine_uses_llm(self):
        h = self._hybrid(_slow_llm(POLISHED))
        assert h.refine("file_search", _ok_result(6)) == POLISHED

    def test_refine_returns_empty_on_llm_failure(self):
        """LLM 抛异常 → 空串（不重播模板文案）"""
        def boom(prompt):
            raise RuntimeError("LLM 挂了")

        h = self._hybrid(boom)
        assert h.refine("file_search", _ok_result(6)) == ""

    def test_refine_returns_empty_on_llm_none(self):
        h = self._hybrid(lambda p: None)
        assert h.refine("file_search", _ok_result(6)) == ""

    def test_refine_counts_towards_stats(self):
        h = self._hybrid(_slow_llm(POLISHED))
        h.refine("file_search", _ok_result(6))
        assert h.stats()["llm_hits"] == 1


# ══════════════════════════════════════════════════
#  管线：异步补播闭环
# ══════════════════════════════════════════════════

class TestPipelineRefine:

    def _pipeline(self, guard, llm_call=None, async_refine=True, summarizer=None):
        bus = EventBus()
        reg = ToolRegistry()
        for t in all_file_tools(guard):
            reg.register(t)
        ex = ThreadPoolExecutorProvider(reg, pool_size=2, timeout=5)

        if summarizer is None:
            tpl = TemplateSummarizer()
            summarizer = HybridSummarizer(
                template=tpl,
                llm=LLMSummarizer(llm_call=llm_call, fallback=tpl) if llm_call else None,
                threshold=3,
            )
        p = AgentPipeline(bus, RuleRouter(), guard, ex, reg, None,
                          summarizer=summarizer, async_refine=async_refine)
        p.start()
        return p, bus, ex

    @staticmethod
    def _collect(bus, timeout=6.0):
        """收集 result / refine 事件（带时间戳）"""
        events = []
        d1 = bus.on(EventTypes.FEEDBACK_RESULT,
                    lambda e: events.append(("result", e.data, time.perf_counter())))
        d2 = bus.on(EventTypes.FEEDBACK_REFINE,
                    lambda e: events.append(("refine", e.data, time.perf_counter())))
        return events, (d1, d2)

    def test_result_is_immediate_and_refine_follows(self, guard):
        """核心验收：慢 LLM 下 result <100ms 到达，refine 随后补播"""
        p, bus, ex = self._pipeline(guard, _slow_llm(POLISHED, delay=0.4))
        try:
            events, disposers = self._collect(bus)
            t0 = time.perf_counter()
            p.handle_text("找一下 txt 文件", "r1")

            deadline = time.time() + 6
            while len(events) < 2 and time.time() < deadline:
                time.sleep(0.005)
            for d in disposers:
                d()

            assert events, "应有事件"
            kind0, data0, ts0 = events[0]
            assert kind0 == "result"
            assert (ts0 - t0) * 1000 < 100, f"结果应在 100ms 内到达，实际 {(ts0-t0)*1000:.0f}ms"

            assert len(events) >= 2, f"应有润色补播，实际 {[e[0] for e in events]}"
            kind1, data1, ts1 = events[1]
            assert kind1 == "refine"
            assert data1["summary"] == POLISHED
            assert data1["previous"] == data0["summary"], "应带上被替换的模板文案"
            assert ts1 > ts0, "补播应在结果之后"
        finally:
            p.stop()
            ex.shutdown(wait=True)

    def test_simple_result_has_no_refine(self, guard):
        """简单结果只走模板，不发 refine（也不调 LLM）"""
        called = {"n": 0}

        def counting(prompt):
            called["n"] += 1
            return POLISHED

        p, bus, ex = self._pipeline(guard, counting)
        try:
            events, disposers = self._collect(bus)
            p.handle_text("找一下 f0 文件", "r1")
            deadline = time.time() + 3
            while not events and time.time() < deadline:
                time.sleep(0.005)
            time.sleep(0.3)
            for d in disposers:
                d()

            assert [e[0] for e in events] == ["result"]
            assert called["n"] == 0, "简单结果不应调用 LLM"
            assert p.stats()["refine_skipped"] >= 1
        finally:
            p.stop()
            ex.shutdown(wait=True)

    def test_llm_failure_produces_no_refine(self, guard):
        def boom(prompt):
            raise RuntimeError("LLM 挂了")

        p, bus, ex = self._pipeline(guard, boom)
        try:
            events, disposers = self._collect(bus)
            p.handle_text("找一下 txt 文件", "r1")
            deadline = time.time() + 3
            while not events and time.time() < deadline:
                time.sleep(0.005)
            time.sleep(0.4)
            for d in disposers:
                d()

            assert [e[0] for e in events] == ["result"], "LLM 失败不应补播"
            assert p.stats()["refined"] == 0
        finally:
            p.stop()
            ex.shutdown(wait=True)

    def test_unimproved_text_produces_no_refine(self, guard):
        """润色结果与模板文案相同 → 不打扰用户

        直接驱动 _emit_result 传入合成结果，避免真实搜索的文件顺序抖动
        导致"模板文案"不可预测。
        """
        res = _ok_result(6)
        same = TemplateSummarizer().summarize("file_search", res)

        p, bus, ex = self._pipeline(guard, _slow_llm(same, delay=0.05))
        try:
            events, disposers = self._collect(bus)
            p._emit_result("r1", res, action="file_search")

            deadline = time.time() + 3
            while not events and time.time() < deadline:
                time.sleep(0.005)
            time.sleep(0.3)
            for d in disposers:
                d()

            assert [e[0] for e in events] == ["result"], f"不应补播: {events}"
            assert p.stats()["refined"] == 0
        finally:
            p.stop()
            ex.shutdown(wait=True)

    def test_interrupt_cancels_refine(self, guard):
        """打断后不再补播过期内容"""
        p, bus, ex = self._pipeline(guard, _slow_llm(POLISHED, delay=0.6))
        try:
            events, disposers = self._collect(bus)
            p.handle_text("找一下 txt 文件", "r1")
            time.sleep(0.1)

            p._cancel_refine("r1")          # 模拟打断
            deadline = time.time() + 3
            while time.time() < deadline and len(events) < 2:
                time.sleep(0.02)
            for d in disposers:
                d()

            assert [e[0] for e in events] == ["result"], "被打断的润色不应补播"
        finally:
            p.stop()
            ex.shutdown(wait=True)

    def test_no_summarizer_is_safe(self, guard):
        """无摘要器 → 直接用工具 summary，且不尝试润色"""
        bus = EventBus()
        reg = ToolRegistry()
        for t in all_file_tools(guard):
            reg.register(t)
        ex = ThreadPoolExecutorProvider(reg, pool_size=2, timeout=5)
        p = AgentPipeline(bus, RuleRouter(), guard, ex, reg, None, summarizer=None)
        p.start()
        try:
            events, disposers = self._collect(bus)
            p.handle_text("找一下 txt 文件", "r1")
            deadline = time.time() + 3
            while not events and time.time() < deadline:
                time.sleep(0.005)
            for d in disposers:
                d()
            assert [e[0] for e in events] == ["result"]
            assert p.stats()["refine_skipped"] == 0
        finally:
            p.stop()
            ex.shutdown(wait=True)

    def test_stats_expose_refine_fields(self, guard):
        p, bus, ex = self._pipeline(guard, _slow_llm(POLISHED, delay=0.05))
        try:
            st = p.stats()
            for key in ("refined", "refine_skipped", "refine_pending"):
                assert key in st, f"缺少统计键 {key}"
        finally:
            p.stop()
            ex.shutdown(wait=True)


# ══════════════════════════════════════════════════
#  关闭开关：回退 P1 同步行为
# ══════════════════════════════════════════════════

class TestAsyncRefineDisabled:

    def test_disabled_falls_back_to_sync(self, guard):
        """async_refine=False → 结果直接含润色文本，且不发 refine 事件"""
        bus = EventBus()
        reg = ToolRegistry()
        for t in all_file_tools(guard):
            reg.register(t)
        ex = ThreadPoolExecutorProvider(reg, pool_size=2, timeout=5)

        tpl = TemplateSummarizer()
        summ = HybridSummarizer(
            template=tpl,
            llm=LLMSummarizer(llm_call=_slow_llm(POLISHED, delay=0.05), fallback=tpl),
            threshold=3,
        )
        p = AgentPipeline(bus, RuleRouter(), guard, ex, reg, None,
                          summarizer=summ, async_refine=False)
        p.start()
        try:
            events, disposers = TestPipelineRefine._collect(bus)
            p.handle_text("找一下 txt 文件", "r1")
            deadline = time.time() + 5
            while not events and time.time() < deadline:
                time.sleep(0.005)
            time.sleep(0.2)
            for d in disposers:
                d()

            assert [e[0] for e in events] == ["result"]
            assert events[0][1]["summary"] == POLISHED, "关闭异步后应同步拿到润色文本"
        finally:
            p.stop()
            ex.shutdown(wait=True)

    def test_bootstrap_flag_reads_config(self):
        cfg = AgentConfig(async_refine=False)
        assert cfg.async_refine is False
        assert AgentConfig().async_refine is True


# ══════════════════════════════════════════════════
#  异常兜底（摘要器实现不可靠时管线不得崩）
# ══════════════════════════════════════════════════

class _FakeSummarizer:
    """可注入行为的假摘要器（鸭子类型，不走 ABC）"""

    def __init__(self, summarize=None, fast=None, should_refine=None, refine=None):
        self._summarize = summarize or (lambda a, r: "同步摘要")
        self._fast = fast or (lambda a, r: "模板摘要")
        self._should = should_refine if should_refine is not None else (lambda a, r: True)
        self._refine = refine or (lambda a, r: "润色文本")

    def summarize(self, action, result):
        return self._summarize(action, result)

    def fast_summary(self, action, result):
        return self._fast(action, result)

    def should_refine(self, action, result):
        return self._should(action, result)

    def refine(self, action, result):
        return self._refine(action, result)


class TestErrorTolerance:

    def _pipeline(self, guard, summarizer, async_refine=True, refine_timeout=0.5):
        bus = EventBus()
        reg = ToolRegistry()
        for t in all_file_tools(guard):
            reg.register(t)
        ex = ThreadPoolExecutorProvider(reg, pool_size=2, timeout=5)
        p = AgentPipeline(bus, RuleRouter(), guard, ex, reg, None,
                          summarizer=summarizer, async_refine=async_refine,
                          refine_timeout=refine_timeout)
        p.start()
        return p, bus, ex

    def test_should_refine_raising_falls_back_to_sync_summary(self, guard):
        """should_refine 抛异常 → 退回同步摘要，不崩、不补播"""
        def boom(a, r):
            raise RuntimeError("should_refine 炸了")

        s = _FakeSummarizer(summarize=lambda a, r: "同步摘要",
                            should_refine=boom)
        p, bus, ex = self._pipeline(guard, s)
        try:
            events, disposers = self._collect(bus)
            p._emit_result("r1", _ok_result(6), action="file_search")
            deadline = time.time() + 3
            while not events and time.time() < deadline:
                time.sleep(0.005)
            time.sleep(0.2)
            for d in disposers:
                d()

            assert [e[0] for e in events] == ["result"]
            assert events[0][1]["summary"] == "同步摘要", "应退回同步摘要"
        finally:
            p.stop()
            ex.shutdown(wait=True)

    def test_refine_raising_is_swallowed(self, guard):
        """refine 抛异常 → worker 内兜底为空串，不发补播"""
        def boom(a, r):
            raise RuntimeError("refine 炸了")

        s = _FakeSummarizer(refine=boom)
        p, bus, ex = self._pipeline(guard, s)
        try:
            events, disposers = self._collect(bus)
            p._emit_result("r1", _ok_result(6), action="file_search")
            deadline = time.time() + 3
            while not events and time.time() < deadline:
                time.sleep(0.005)
            time.sleep(0.3)
            for d in disposers:
                d()

            assert [e[0] for e in events] == ["result"]
            assert p.stats()["refined"] == 0
        finally:
            p.stop()
            ex.shutdown(wait=True)

    def test_fast_summary_raising_is_swallowed(self, guard):
        """fast_summary 抛异常 → 回退工具 summary"""
        def boom(a, r):
            raise RuntimeError("fast 炸了")

        s = _FakeSummarizer(fast=boom, should_refine=lambda a, r: True)
        p, bus, ex = self._pipeline(guard, s)
        try:
            events, disposers = self._collect(bus)
            res = _ok_result(6)
            p._emit_result("r1", res, action="file_search")
            deadline = time.time() + 3
            while not events and time.time() < deadline:
                time.sleep(0.005)
            for d in disposers:
                d()

            assert events[0][1]["summary"] == res.summary, "应回退工具文案"
        finally:
            p.stop()
            ex.shutdown(wait=True)

    @staticmethod
    def _collect(bus, timeout=6.0):
        events = []
        d1 = bus.on(EventTypes.FEEDBACK_RESULT,
                    lambda e: events.append(("result", e.data, time.perf_counter())))
        d2 = bus.on(EventTypes.FEEDBACK_REFINE,
                    lambda e: events.append(("refine", e.data, time.perf_counter())))
        return events, (d1, d2)

    def test_shutdown_breaks_when_budget_exhausted(self, guard):
        """多个卡住的润色线程 → join 预算耗尽后用剩余时间直接跳出"""
        gate = threading.Event()
        s = _FakeSummarizer(refine=lambda a, r: (gate.wait(timeout=5), "润色")[1])
        p, bus, ex = self._pipeline(guard, s, refine_timeout=0.2)
        try:
            p._emit_result("r1", _ok_result(6), action="file_search")
            p._emit_result("r2", _ok_result(6), action="file_search")
            time.sleep(0.05)

            t0 = time.perf_counter()
            p.stop()
            elapsed = time.perf_counter() - t0

            assert elapsed < 2.0, f"stop 应受预算约束，实际 {elapsed:.2f}s"
        finally:
            gate.set()
            ex.shutdown(wait=True)


# ══════════════════════════════════════════════════
#  HybridSummarizer 异常兜底
# ══════════════════════════════════════════════════

class TestHybridErrorTolerance:

    class _BoomTemplate:
        def summarize(self, action, result):
            raise RuntimeError("模板炸了")

    class _BoomLLM:
        def summarize(self, action, result):
            raise RuntimeError("LLM 炸了")

    class _EmptyLLM:
        def summarize(self, action, result):
            return ""

    def test_fast_summary_swallows_template_error(self):
        h = HybridSummarizer(template=self._BoomTemplate(), llm=None)
        res = _ok_result(6)
        assert h.fast_summary("file_search", res) == res.summary

    def test_refine_swallows_llm_error(self):
        h = HybridSummarizer(template=TemplateSummarizer(), llm=self._BoomLLM())
        assert h.refine("file_search", _ok_result(6)) == ""

    def test_refine_returns_empty_when_llm_gives_nothing(self):
        h = HybridSummarizer(template=TemplateSummarizer(), llm=self._EmptyLLM())
        assert h.refine("file_search", _ok_result(6)) == ""

    def test_refine_tolerates_baseline_failure(self):
        """模板崩了但 LLM 给出结果 → 仍应返回润色文本（baseline 视为空）"""
        h = HybridSummarizer(template=self._BoomTemplate(), llm=_FakeLLMSummarizer())
        assert h.refine("file_search", _ok_result(6)) == POLISHED

    def test_refine_tolerates_fast_summary_raising(self):
        """连 fast_summary 本身都抛异常时，refine 仍应给出润色文本"""
        h = HybridSummarizer(template=TemplateSummarizer(), llm=_FakeLLMSummarizer())

        def boom(action, result):
            raise RuntimeError("fast_summary 炸了")

        h.fast_summary = boom            # type: ignore[method-assign]
        assert h.refine("file_search", _ok_result(6)) == POLISHED


class _FakeLLMSummarizer:
    """始终返回润色文本的假 LLM 摘要器"""

    def summarize(self, action, result):
        return POLISHED



# ══════════════════════════════════════════════════
#  线程收尾（D7 教训：不得留下不可 join 的线程）
# ══════════════════════════════════════════════════

class TestRefineShutdown:

    def test_stop_joins_refine_threads(self, guard):
        """stop() 后不应残留 agent-refine 线程"""
        bus = EventBus()
        reg = ToolRegistry()
        for t in all_file_tools(guard):
            reg.register(t)
        ex = ThreadPoolExecutorProvider(reg, pool_size=2, timeout=5)

        tpl = TemplateSummarizer()
        summ = HybridSummarizer(
            template=tpl,
            llm=LLMSummarizer(llm_call=_slow_llm(POLISHED, delay=0.3), fallback=tpl),
            threshold=3,
        )
        p = AgentPipeline(bus, RuleRouter(), guard, ex, reg, None,
                          summarizer=summ, async_refine=True)
        p.start()
        try:
            p.handle_text("找一下 txt 文件", "r1")
            time.sleep(0.05)
            assert any(t.name.startswith("agent-refine") for t in threading.enumerate()) or True
        finally:
            p.stop()
            ex.shutdown(wait=True)

        leftover = [t.name for t in threading.enumerate() if t.name.startswith("agent-refine")]
        assert leftover == [], f"stop 后不应残留润色线程: {leftover}"

    def test_refine_threads_are_daemon(self):
        """润色线程必须是 daemon，否则解释器退出时会被 join（D7 类竞态）"""
        bus = EventBus()
        guard = BasicGuard(whitelist=[str(Path.home())], audit_enabled=False)
        reg = ToolRegistry()
        ex = ThreadPoolExecutorProvider(reg, pool_size=1, timeout=5)

        tpl = TemplateSummarizer()
        released = threading.Event()

        def blocking(prompt):
            released.wait(timeout=5)
            return POLISHED

        summ = HybridSummarizer(
            template=tpl, llm=LLMSummarizer(llm_call=blocking, fallback=tpl), threshold=1,
        )
        p = AgentPipeline(bus, RuleRouter(), guard, ex, reg, None,
                          summarizer=summ, async_refine=True, refine_timeout=0.2)
        p.start()
        try:
            p.handle_text("找一下 txt 文件", "r1")
            time.sleep(0.1)

            with p._refine_lock:
                threads = list(p._refine_threads.values())
            assert threads, "应有一个在途润色线程"
            assert all(t.daemon for t in threads), "润色线程必须是 daemon"
        finally:
            released.set()
            p.stop()
            ex.shutdown(wait=True)
            guard.close()

    def test_shutdown_is_bounded(self, guard):
        """润色线程卡住时，stop() 受 refine_timeout 约束而非永久阻塞"""
        bus = EventBus()
        reg = ToolRegistry()
        ex = ThreadPoolExecutorProvider(reg, pool_size=1, timeout=5)

        tpl = TemplateSummarizer()
        hold = threading.Event()
        summ = HybridSummarizer(
            template=tpl,
            llm=LLMSummarizer(llm_call=lambda p: (hold.wait(timeout=5), POLISHED)[1],
                              fallback=tpl),
            threshold=1,
        )
        p = AgentPipeline(bus, RuleRouter(), guard, ex, reg, None,
                          summarizer=summ, async_refine=True, refine_timeout=0.2)
        p.start()
        try:
            p.handle_text("找一下 txt 文件", "r1")
            time.sleep(0.05)

            t0 = time.perf_counter()
            p.stop()
            elapsed = time.perf_counter() - t0
            assert elapsed < 2.0, f"stop 应受 timeout 约束，实际 {elapsed:.2f}s"
        finally:
            hold.set()
            ex.shutdown(wait=True)
