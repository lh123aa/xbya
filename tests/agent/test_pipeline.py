"""并行管线与执行器测试

覆盖：
- ThreadPoolExecutorProvider（提交/取消/超时/回调隔离）
- AckCache（预热/命中/降级）
- AgentPipeline（路由→并行 ack+执行→结果 / 确认流程 / 打断 / 闲聊回退）
"""

import sys
import threading
import time
from pathlib import Path

import pytest

project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from agent.message import AgentCommand, AgentResult, CommandStatus
from agent.pipeline import AgentPipeline
from agent.providers.executor.thread_pool import ThreadPoolExecutorProvider
from agent.providers.router.rule_router import RuleRouter
from agent.providers.safety.basic_guard import BasicGuard
from agent.tools.base import BaseTool, ToolResult
from agent.tools.file_tools import all_file_tools
from agent.tools.registry import ToolRegistry
from core.kernel.events import EventBus, EventTypes
from services.ack_cache import AckCache, ACK_PHRASES, category_for_action


# ══════════════════════════════════════════════════════
#  Fixtures
# ══════════════════════════════════════════════════════

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


@pytest.fixture
def registry(guard):
    reg = ToolRegistry()
    for t in all_file_tools(guard):
        reg.register(t)
    return reg


@pytest.fixture
def executor(registry):
    ex = ThreadPoolExecutorProvider(registry, pool_size=2, timeout=5)
    yield ex
    ex.shutdown(wait=True)


@pytest.fixture
def bus():
    return EventBus()


@pytest.fixture
def fake_tts():
    """假 TTS：返回可识别的字节"""
    def synth(text):
        return f"AUDIO::{text}".encode("utf-8")
    return synth


@pytest.fixture
def ack(fake_tts):
    cache = AckCache(warmup_async=False)
    cache.warm_up(fake_tts)
    return cache


@pytest.fixture
def pipeline(bus, guard, executor, registry, ack):
    p = AgentPipeline(bus, RuleRouter(), guard, executor, registry, ack)
    p.start()
    yield p
    p.stop()


def wait_for(bus, event_type, timeout=5.0, collect=None):
    """等待某个事件（返回事件数据）"""
    box = {"data": None}
    done = threading.Event()

    def handler(e):
        box["data"] = e.data
        if collect is not None:
            collect.append(e.data)
        done.set()

    dispose = bus.on(event_type, handler)
    try:
        done.wait(timeout)
    finally:
        dispose()
    return box["data"]


class SleepyTool(BaseTool):
    """可控制耗时的测试工具"""

    name = "sleepy"
    description = "测试用"
    risk_level = "low"
    params_schema = {
        "type": "object",
        "properties": {"delay": {"type": "number"}, "fail": {"type": "boolean"}},
    }

    def __init__(self, default_delay=0.05):
        self.default_delay = default_delay
        self.calls = []

    def execute(self, params):
        delay = float(params.get("delay", self.default_delay))
        self.calls.append(params)
        time.sleep(delay)
        if params.get("fail"):
            return ToolResult.fail("故意失败")
        return ToolResult.ok(data=["x"], summary=f"睡了{delay}秒", emotion="happy")


# ══════════════════════════════════════════════════════
#  执行器
# ══════════════════════════════════════════════════════

class TestThreadPoolExecutor:
    """线程池执行器"""

    def test_submit_returns_immediately(self, registry):
        """提交立即返回（不阻塞调用线程）"""
        tool = SleepyTool(default_delay=0.5)
        registry.register(tool)
        ex = ThreadPoolExecutorProvider(registry, pool_size=2, timeout=5)
        try:
            t0 = time.perf_counter()
            task_id = ex.submit("r1", "sleepy", {"delay": 0.5}, lambda h, r: None)
            elapsed = time.perf_counter() - t0
            assert task_id
            assert elapsed < 0.2, f"submit 阻塞了 {elapsed:.2f}s"
        finally:
            ex.shutdown(wait=True)

    def test_callback_invoked_with_result(self, registry):
        """回调收到工具结果"""
        registry.register(SleepyTool())
        ex = ThreadPoolExecutorProvider(registry, pool_size=2, timeout=5)
        try:
            got = {}
            done = threading.Event()

            def cb(handle, result):
                got["handle"] = handle
                got["result"] = result
                done.set()

            ex.submit("r1", "sleepy", {"delay": 0.01}, cb)
            assert done.wait(3.0)
            assert got["result"].success is True
            assert got["handle"].action == "sleepy"
            assert got["handle"].request_id == "r1"
        finally:
            ex.shutdown(wait=True)

    def test_pending_count(self, registry):
        """pending_count 反映未完成任务"""
        registry.register(SleepyTool(default_delay=0.3))
        ex = ThreadPoolExecutorProvider(registry, pool_size=2, timeout=5)
        try:
            ex.submit("r1", "sleepy", {"delay": 0.3}, lambda h, r: None)
            assert ex.pending_count() >= 1
            time.sleep(0.6)
            assert ex.pending_count() == 0
        finally:
            ex.shutdown(wait=True)

    def test_cancel_before_start(self, registry):
        """任务开始前取消生效"""
        registry.register(SleepyTool(default_delay=0.2))
        ex = ThreadPoolExecutorProvider(registry, pool_size=1, timeout=5)
        try:
            results = []
            # 占满线程池
            ex.submit("r0", "sleepy", {"delay": 0.4}, lambda h, r: None)
            tid = ex.submit("r1", "sleepy", {"delay": 0.01}, lambda h, r: results.append(r))
            assert ex.cancel(tid) is True
            time.sleep(0.8)
            assert results == [], "被取消的任务不应回调结果"
        finally:
            ex.shutdown(wait=True)

    def test_cancel_by_request(self, registry):
        """按 request_id 批量取消"""
        registry.register(SleepyTool(default_delay=0.2))
        ex = ThreadPoolExecutorProvider(registry, pool_size=1, timeout=5)
        try:
            ex.submit("r0", "sleepy", {"delay": 0.4}, lambda h, r: None)
            ex.submit("rx", "sleepy", {"delay": 0.1}, lambda h, r: None)
            ex.submit("rx", "sleepy", {"delay": 0.1}, lambda h, r: None)
            n = ex.cancel_by_request("rx")
            assert n >= 1
        finally:
            ex.shutdown(wait=True)

    def test_cancel_unknown_task_returns_true(self, registry):
        """取消不存在的任务返回 True（视为已达成）"""
        ex = ThreadPoolExecutorProvider(registry, pool_size=1, timeout=5)
        try:
            assert ex.cancel("nonexistent") is True
        finally:
            ex.shutdown(wait=True)

    def test_callback_exception_isolated(self, registry):
        """回调抛异常不影响执行器"""
        registry.register(SleepyTool())
        ex = ThreadPoolExecutorProvider(registry, pool_size=2, timeout=5)
        try:
            def bad_cb(handle, result):
                raise RuntimeError("回调炸了")

            tid = ex.submit("r1", "sleepy", {"delay": 0.01}, bad_cb)
            time.sleep(0.3)
            # 执行器仍可用
            got = []
            done = threading.Event()

            def good_cb(h, r):
                got.append(r)
                done.set()

            ex.submit("r2", "sleepy", {"delay": 0.01}, good_cb)
            assert done.wait(3.0)
            assert len(got) == 1
        finally:
            ex.shutdown(wait=True)

    def test_unknown_tool_returns_failure(self, registry):
        """未注册工具返回失败结果（不抛异常）"""
        ex = ThreadPoolExecutorProvider(registry, pool_size=1, timeout=5)
        try:
            got = {}
            done = threading.Event()

            def cb(h, r):
                got["r"] = r
                done.set()

            ex.submit("r1", "no_such_tool", {}, cb)
            assert done.wait(3.0)
            assert got["r"].success is False
        finally:
            ex.shutdown(wait=True)

    def test_shutdown_rejects_new_tasks(self, registry):
        """关闭后拒绝新任务"""
        ex = ThreadPoolExecutorProvider(registry, pool_size=1, timeout=5)
        ex.shutdown(wait=True)
        assert ex.submit("r1", "sleepy", {}, lambda h, r: None) == ""

    def test_shutdown_idempotent(self, registry):
        """重复关闭不抛异常"""
        ex = ThreadPoolExecutorProvider(registry, pool_size=1, timeout=5)
        ex.shutdown(wait=True)
        ex.shutdown(wait=True)


# ══════════════════════════════════════════════════════
#  确认语缓存
# ══════════════════════════════════════════════════════

class TestAckCache:
    """确认语缓存池"""

    def test_warm_up_sync(self, fake_tts):
        """同步预热后可命中"""
        cache = AckCache(warmup_async=False)
        cache.warm_up(fake_tts)
        assert cache.warmed is True
        assert cache.cached_count() > 0

    def test_get_returns_bytes(self, ack):
        """命中返回音频字节"""
        audio = ack.get("search")
        assert isinstance(audio, bytes)
        assert audio.startswith(b"AUDIO::")

    def test_get_unknown_category_falls_back_default(self, ack):
        """未知分类回退 default"""
        audio = ack.get("no_such_category")
        assert isinstance(audio, bytes)

    def test_get_before_warmup_returns_none(self):
        """未预热返回 None（调用方降级）"""
        cache = AckCache(warmup_async=False)
        assert cache.get("search") is None

    def test_pick_returns_matching_text(self, ack):
        """pick 返回的文本与音频对应"""
        audio, text = ack.pick("search")
        assert audio is not None
        assert text in ACK_PHRASES["search"]
        assert audio == f"AUDIO::{text}".encode("utf-8")

    def test_pick_before_warmup_returns_text_only(self):
        """未预热时只返回文本"""
        cache = AckCache(warmup_async=False)
        audio, text = cache.pick("delete")
        assert audio is None
        assert isinstance(text, str) and text

    def test_warm_up_handles_tts_failure(self):
        """TTS 失败时部分预热，不崩溃"""
        def flaky(text):
            if "搜" in text:
                raise RuntimeError("TTS 炸了")
            return b"OK"

        cache = AckCache(warmup_async=False)
        cache.warm_up(flaky)
        assert cache.warmed is True
        # search 分类全部失败 → 回退 default
        assert cache.get("search") in (b"OK", None)

    def test_warm_up_async(self, fake_tts):
        """异步预热可等待完成"""
        cache = AckCache(warmup_async=True)
        cache.warm_up(fake_tts)
        assert cache.wait_ready(timeout=5.0) is True
        assert cache.get("default") is not None

    def test_clear(self, ack):
        """清空缓存"""
        ack.clear()
        assert ack.cached_count() == 0
        assert ack.warmed is False

    def test_categories(self, ack):
        """已缓存分类列表"""
        cats = ack.categories()
        assert "default" in cats

    def test_ack_latency_is_fast(self, ack):
        """缓存命中延迟 <10ms（目标 0.05s）"""
        ack.get("default")  # 预热
        t0 = time.perf_counter()
        for _ in range(100):
            ack.get("default")
        avg_ms = (time.perf_counter() - t0) / 100 * 1000
        assert avg_ms < 10.0, f"缓存读取平均 {avg_ms:.3f}ms"


class TestCategoryForAction:
    """工具名 → 确认语分类"""

    @pytest.mark.parametrize("action,expected", [
        ("file_search", "search"),
        ("file_list", "search"),
        ("file_read", "read"),
        ("file_delete", "delete"),
        ("file_rename", "write"),
        ("file_move", "write"),
        ("system_info", "system"),
        ("totally_unknown", "default"),
        ("", "default"),
    ])
    def test_mapping(self, action, expected):
        assert category_for_action(action) == expected


# ══════════════════════════════════════════════════════
#  并行管线
# ══════════════════════════════════════════════════════

class TestPipelineRouting:
    """路由与分流"""

    def test_tool_command_executes(self, pipeline, bus, sandbox):
        """工具类指令被执行并反馈结果"""
        (sandbox / "合同.pdf").write_text("x")
        results = []
        dispose = bus.on(EventTypes.FEEDBACK_RESULT, lambda e: results.append(e.data))
        try:
            pipeline.handle_text("找一下桌面上的合同", "r1")
            deadline = time.time() + 5
            while not results and time.time() < deadline:
                time.sleep(0.02)
        finally:
            dispose()
        assert results, "未收到结果事件"
        assert "合同" in results[0]["summary"] or "找到" in results[0]["summary"]

    def test_chat_command_falls_through(self, pipeline, bus):
        """闲聊走 pipeline.chat（交回 Voice Layer 调 LLM）"""
        chats = []
        dispose = bus.on("pipeline.chat", lambda e: chats.append(e.data))
        try:
            pipeline.handle_text("你好呀", "r1")
        finally:
            dispose()
        assert len(chats) == 1
        assert chats[0]["request_id"] == "r1"

    def test_disabled_pipeline_all_chat(self, bus, guard, executor, registry, ack):
        """管线关闭时全部转 chat（保留原 LLM 路径）"""
        p = AgentPipeline(bus, RuleRouter(), guard, executor, registry, ack, enabled=False)
        p.start()
        try:
            chats = []
            dispose = bus.on("pipeline.chat", lambda e: chats.append(e.data))
            try:
                p.handle_text("找一下桌面上的合同", "r1")
            finally:
                dispose()
            assert len(chats) == 1
        finally:
            p.stop()

    def test_rejected_command_returns_failure(self, pipeline, bus, tmp_path):
        """安全拒绝 → 直接返回失败结果，不执行工具"""
        outside = tmp_path / "outside"
        outside.mkdir()
        f = outside / "secret.txt"
        f.write_text("x")

        results = []
        dispose = bus.on(EventTypes.FEEDBACK_RESULT, lambda e: results.append(e.data))
        try:
            pipeline.handle_text(f"删除 {f}", "r1")
            time.sleep(0.3)
        finally:
            dispose()
        assert results, "未收到拒绝结果"
        assert results[0]["success"] is False
        assert f.exists(), "被拒绝的文件不应被删除"


class TestPipelineParallelism:
    """并行性验证（核心指标）"""

    def test_ack_and_submit_are_parallel(self, pipeline, bus, sandbox):
        """ack 与 task.submitted 必须几乎同时发出（不互相等待）"""
        (sandbox / "a.txt").write_text("x")
        marks = {}

        dispose1 = bus.on(EventTypes.FEEDBACK_ACK,
                          lambda e: marks.setdefault("ack", time.perf_counter()))
        dispose2 = bus.on(EventTypes.TASK_SUBMITTED,
                          lambda e: marks.setdefault("submit", time.perf_counter()))
        try:
            pipeline.handle_text("找一下桌面上的文件", "r1")
        finally:
            dispose1()
            dispose2()

        assert "ack" in marks and "submit" in marks, "两个事件都应发出"
        gap_ms = abs(marks["ack"] - marks["submit"]) * 1000
        assert gap_ms < 50, f"ack 与 submit 间隔 {gap_ms:.1f}ms，未达并行"

    def test_ack_emitted_before_result(self, pipeline, bus, sandbox):
        """ack 必须先于 result 到达（用户先听到反馈）"""
        (sandbox / "a.txt").write_text("x")
        order = []
        dispose1 = bus.on(EventTypes.FEEDBACK_ACK, lambda e: order.append("ack"))
        dispose2 = bus.on(EventTypes.FEEDBACK_RESULT, lambda e: order.append("result"))
        try:
            pipeline.handle_text("找一下桌面上的文件", "r1")
            deadline = time.time() + 5
            while "result" not in order and time.time() < deadline:
                time.sleep(0.02)
        finally:
            dispose1()
            dispose2()
        assert order and order[0] == "ack", f"顺序错误: {order}"

    def test_ack_carries_cached_audio(self, pipeline, bus, sandbox):
        """ack 事件携带缓存音频（消费方无需实时合成）"""
        (sandbox / "a.txt").write_text("x")
        got = {}
        dispose = bus.on(EventTypes.FEEDBACK_ACK, lambda e: got.update(e.data))
        try:
            pipeline.handle_text("找一下桌面上的文件", "r1")
        finally:
            dispose()
        assert got.get("audio") is not None
        assert got.get("text")

    def test_ack_is_fast(self, pipeline, bus, sandbox):
        """ack 延迟目标 <50ms（缓存命中）"""
        (sandbox / "a.txt").write_text("x")
        got = {}
        dispose = bus.on(EventTypes.FEEDBACK_ACK, lambda e: got.update(e.data))
        try:
            pipeline.handle_text("找一下桌面上的文件", "r1")
        finally:
            dispose()
        assert got["elapsed_ms"] < 50, f"ack 延迟 {got['elapsed_ms']:.1f}ms"


class TestPipelineConfirmFlow:
    """确认流程"""

    def test_delete_requires_confirm(self, pipeline, bus, sandbox):
        """删除操作先请求确认，不直接执行"""
        f = sandbox / "shot.png"
        f.write_text("x")

        confirms = []
        results = []
        dispose1 = bus.on(EventTypes.FEEDBACK_CONFIRM, lambda e: confirms.append(e.data))
        dispose2 = bus.on(EventTypes.FEEDBACK_RESULT, lambda e: results.append(e.data))
        try:
            pipeline.handle_text("删除桌面上的截图", "r1")
            time.sleep(0.3)
        finally:
            dispose1()
            dispose2()

        assert len(confirms) == 1, "应发出确认请求"
        assert confirms[0]["risk"] == "high"
        assert "回收站" in confirms[0]["question"] or "确定" in confirms[0]["question"]
        assert results == [], "确认前不应执行"
        assert f.exists(), "确认前文件应仍在"

    def test_confirm_approved_executes(self, pipeline, bus, sandbox, monkeypatch):
        """用户确认后执行删除"""
        import send2trash as s2t
        deleted = []
        monkeypatch.setattr(s2t, "send2trash", lambda p: deleted.append(p))

        # 文件名需匹配"截图"（路由从"删除桌面上的截图"提取 pattern=*截图*）
        f = sandbox / "截图_1.png"
        f.write_text("x")

        pipeline.handle_text("删除桌面上的截图", "r1")
        time.sleep(0.2)

        results = []
        dispose = bus.on(EventTypes.FEEDBACK_RESULT, lambda e: results.append(e.data))
        try:
            pipeline.handle_text("确定", "r2")
            deadline = time.time() + 5
            while not results and time.time() < deadline:
                time.sleep(0.02)
        finally:
            dispose()

        assert results, "确认后应有结果"
        assert len(deleted) == 1, f"应调用回收站删除，实际 {deleted}"

    def test_confirm_cancelled_does_nothing(self, pipeline, bus, sandbox, monkeypatch):
        """用户取消后不执行"""
        import send2trash as s2t
        deleted = []
        monkeypatch.setattr(s2t, "send2trash", lambda p: deleted.append(p))

        f = sandbox / "shot.png"
        f.write_text("x")

        pipeline.handle_text("删除桌面上的截图", "r1")
        time.sleep(0.2)

        results = []
        dispose = bus.on(EventTypes.FEEDBACK_RESULT, lambda e: results.append(e.data))
        try:
            pipeline.handle_text("算了", "r2")
            time.sleep(0.3)
        finally:
            dispose()

        assert deleted == [], "取消后不应删除"
        assert f.exists()
        assert results and "不做" in results[0]["summary"]

    def test_medium_risk_also_confirms(self, pipeline, bus, sandbox):
        """中风险（重命名）也需确认"""
        (sandbox / "a.txt").write_text("x")
        confirms = []
        dispose = bus.on(EventTypes.FEEDBACK_CONFIRM, lambda e: confirms.append(e.data))
        try:
            pipeline.handle_text("把 a.txt 改成 b.txt", "r1")
            time.sleep(0.2)
        finally:
            dispose()
        assert len(confirms) == 1
        assert confirms[0]["risk"] == "medium"


class TestPipelineInterrupt:
    """打断处理"""

    def test_interrupt_cancels_tasks(self, pipeline, bus, registry):
        """打断取消该请求下的任务"""
        registry.register(SleepyTool(default_delay=0.5))
        results = []
        dispose = bus.on(EventTypes.FEEDBACK_RESULT, lambda e: results.append(e.data))
        try:
            pipeline.handle_text("算一下 1 加 1", "r1")
            # 直接提交一个慢任务用于取消
            pipeline._executor.submit("r1", "sleepy", {"delay": 0.5}, lambda h, r: None)
            time.sleep(0.05)
            assert pipeline._executor.pending_count() >= 1

            bus.emit(EventTypes.SPEECH_INTERRUPTED, request_id="r1")
            time.sleep(0.1)
        finally:
            dispose()

        assert pipeline.stats()["interrupts"] == 1

    def test_interrupt_without_request_cancels_all(self, pipeline, bus, registry):
        """无 request_id 的打断取消全部任务"""
        registry.register(SleepyTool(default_delay=0.4))
        pipeline._executor.submit("rx", "sleepy", {"delay": 0.4}, lambda h, r: None)
        time.sleep(0.05)
        bus.emit(EventTypes.SPEECH_INTERRUPTED)
        time.sleep(0.1)
        assert pipeline.stats()["interrupts"] == 1


class TestPipelineLifecycle:
    """生命周期与统计"""

    def test_start_stop(self, bus, guard, executor, registry, ack):
        """启动与停止"""
        p = AgentPipeline(bus, RuleRouter(), guard, executor, registry, ack)
        assert p.started is False
        p.start()
        assert p.started is True
        p.start()  # 幂等
        p.stop()
        assert p.started is False
        p.stop()  # 幂等

    def test_stopped_pipeline_ignores_events(self, bus, guard, executor, registry, ack):
        """停止后不再处理事件"""
        p = AgentPipeline(bus, RuleRouter(), guard, executor, registry, ack)
        p.start()
        p.stop()
        bus.emit(EventTypes.SPEECH_RECOGNIZED, text="找一下文件", request_id="r1")
        time.sleep(0.2)
        assert p.stats()["commands"] == 0

    def test_stats_shape(self, pipeline, bus, sandbox):
        """统计结构完整"""
        (sandbox / "a.txt").write_text("x")
        pipeline.handle_text("找一下桌面上的文件", "r1")
        time.sleep(0.3)
        stats = pipeline.stats()
        for key in ("commands", "tool_calls", "chats", "rejected", "confirms",
                    "interrupts", "ack_p50_ms", "ack_p95_ms",
                    "result_p50_ms", "result_p95_ms", "pending_tasks"):
            assert key in stats, f"缺少统计项 {key}"

    def test_reset_stats(self, pipeline, bus, sandbox):
        """统计可重置"""
        (sandbox / "a.txt").write_text("x")
        pipeline.handle_text("找一下桌面上的文件", "r1")
        time.sleep(0.2)
        pipeline.reset_stats()
        assert pipeline.stats()["commands"] == 0
        assert pipeline.stats()["ack_p50_ms"] == 0.0

    def test_event_driven_entry(self, pipeline, bus, sandbox):
        """通过事件总线驱动的完整闭环"""
        (sandbox / "报告.docx").write_text("x")
        results = []
        dispose = bus.on(EventTypes.FEEDBACK_RESULT, lambda e: results.append(e.data))
        try:
            bus.emit(EventTypes.SPEECH_RECOGNIZED,
                     text="找一下桌面上的报告", request_id="r-evt")
            deadline = time.time() + 5
            while not results and time.time() < deadline:
                time.sleep(0.02)
        finally:
            dispose()
        assert results
        assert results[0]["request_id"] == "r-evt"


class TestDeleteWithoutTargetIsNotGuessed:
    """删除指令没有明确目标时，**不许用实体栈猜**

    回归保护：原先 `_resolve_references` 的兜底是"缺目标 → 直接用实体栈的最近文件"。
    对 `file_delete` 来说这是最危险的那类失败 —— 用户说「删除桌面上的报表」
    （动词认得、目标认不出，而置信度仍是 0.95），后台就把"上一次搜索到的文件"
    当成了删除对象。语音回环实测踩到过：说「删除桌面上的截图」，
    确认语里预览的是两个 PDF 合同（繁体「截圖」让 pattern 静默丢失）。

    护栏只收窄 `file_delete` + **用户给了目录** 这一种情况：
    完全没提位置的指代（"把那些删了"）仍走实体栈，那是唯一可用的指称对象。
    """

    @staticmethod
    def _pipe_with_files(pipeline, files):
        from agent.tracker import EntityTracker
        tracker = EntityTracker()
        tracker.push_files(files)
        pipeline._tracker = tracker
        return pipeline

    def test_delete_with_dir_does_not_borrow_previous_search(self, pipeline):
        self._pipe_with_files(pipeline, [
            {"name": "合同_2025.pdf", "path": r"C:\tmp\合同_2025.pdf"},
            {"name": "旧合同.pdf", "path": r"C:\tmp\旧合同.pdf"},
        ])
        cmd = AgentCommand(action="file_delete", params={"dirs": ["Desktop"]},
                           raw_text="删除桌面上的报表")
        pipeline._resolve_references(cmd)
        assert "targets" not in cmd.params, "不该把上一次搜索到的文件当成删除目标"
        assert "target" not in cmd.params
        assert cmd.params.get("dirs") == ["Desktop"], "目录约束要保持原样交给工具"

    def test_delete_without_any_location_still_uses_tracker(self, pipeline):
        """纯指代（没提位置）仍走实体栈 —— 兜底不能被一刀切掉"""
        self._pipe_with_files(pipeline, [
            {"name": "合同_2025.pdf", "path": r"C:\tmp\合同_2025.pdf"},
        ])
        cmd = AgentCommand(action="file_delete", params={}, raw_text="把那些删了")
        pipeline._resolve_references(cmd)
        assert cmd.params.get("targets") == [r"C:\tmp\合同_2025.pdf"]

    def test_move_and_read_fallbacks_are_untouched(self, pipeline):
        """护栏只针对 file_delete；移动/读取的兜底保持不变"""
        self._pipe_with_files(pipeline, [
            {"name": "报告.docx", "path": r"C:\tmp\报告.docx"},
        ])
        for action in ("file_move", "file_read"):
            cmd = AgentCommand(action=action, params={"dirs": ["Desktop"]},
                               raw_text="打开桌面上的报表")
            pipeline._resolve_references(cmd)
            assert cmd.params.get("target") == r"C:\tmp\报告.docx", action


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
