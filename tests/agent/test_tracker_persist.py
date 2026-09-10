"""P2-3 实体栈持久化测试（偿还技术债 D1）

覆盖：
- EntityTracker / ActionRecord 序列化与还原（含脏数据容错）
- TrackerStore 原子写、节流、容错、安全边界
- 管线接入：变更后落盘、stats 暴露 restored
- 装配接入：start 恢复 / dispose 强制落盘 / 关闭开关不落盘
"""

from __future__ import annotations

import json
import os
import sys
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
from agent.tools.base import ToolResult
from agent.tools.file_tools import all_file_tools
from agent.tools.registry import ToolRegistry
from agent.tracker import MAX_ACTIONS, MAX_FILES, ActionRecord, EntityTracker
from agent.tracker_store import SCHEMA_VERSION, TrackerStore
from core.kernel.events import EventBus


def _files(n: int) -> list:
    return [
        {"name": f"f{i}.png", "path": f"C:/x/f{i}.png", "size": i * 10,
         "mtime": float(i), "is_dir": False}
        for i in range(1, n + 1)
    ]


@pytest.fixture
def store_path(tmp_path):
    return tmp_path / "data" / "agent_entities.json"


@pytest.fixture
def sandbox(tmp_path):
    d = tmp_path / "Desktop"
    d.mkdir()
    return d


# ══════════════════════════════════════════════════
#  序列化
# ══════════════════════════════════════════════════

class TestActionRecordSerialization:

    def test_round_trip(self):
        rec = ActionRecord(action="file_search", params={"pattern": "*"}, result_count=3,
                           timestamp=123.5)
        back = ActionRecord.from_dict(rec.to_dict())
        assert back == rec

    def test_from_dict_rejects_non_dict(self):
        assert ActionRecord.from_dict("nope") is None
        assert ActionRecord.from_dict(None) is None
        assert ActionRecord.from_dict([]) is None

    def test_from_dict_requires_action(self):
        assert ActionRecord.from_dict({}) is None
        assert ActionRecord.from_dict({"action": ""}) is None
        assert ActionRecord.from_dict({"action": 123}) is None

    def test_from_dict_coerces_bad_fields(self):
        rec = ActionRecord.from_dict({"action": "x", "params": "bad",
                                      "result_count": "bad", "timestamp": "bad"})
        assert rec is not None
        assert rec.params == {}
        assert rec.result_count == 0
        assert rec.timestamp == 0.0


class TestTrackerSerialization:

    def test_round_trip_preserves_resolution(self):
        t1 = EntityTracker()
        t1.push_files(_files(3))
        t1.push_action("file_search", {"pattern": "*"}, 3)

        t2 = EntityTracker()
        n = t2.from_dict(t1.to_dict())

        assert n == 3
        assert [f["name"] for f in t2.resolve_ordinal("第二个") or []] == ["f2.png"]
        assert [f["name"] for f in t2.resolve_ordinal("最后一个") or []] == ["f3.png"]
        assert len(t2.resolve("那些")) == 3
        assert t2.last_action() is not None

    def test_to_dict_only_keeps_whitelisted_keys(self):
        t = EntityTracker()
        t.push_files([{"name": "a.txt", "path": "C:/a.txt", "content": "机密" * 1000,
                       "token": "secret"}])
        data = t.to_dict()
        entry = data["files"][0]
        assert "content" not in entry
        assert "token" not in entry
        assert entry["name"] == "a.txt"

    def test_to_dict_truncates_long_strings(self):
        t = EntityTracker()
        t.push_files([{"name": "a" * 5000, "path": "b" * 5000}])
        entry = t.to_dict()["files"][0]
        assert len(entry["name"]) <= 512
        assert len(entry["path"]) <= 512

    def test_to_dict_skips_entries_without_name_or_path(self):
        t = EntityTracker()
        t._files = [{"size": 1}, {"name": "ok.txt"}]
        data = t.to_dict()
        assert len(data["files"]) == 1
        assert data["files"][0]["name"] == "ok.txt"

    def test_to_dict_skips_non_dict_entries(self):
        t = EntityTracker()
        t._files = ["not-a-dict", {"name": "ok.txt"}]     # type: ignore[list-item]
        assert len(t.to_dict()["files"]) == 1

    def test_to_dict_has_version(self):
        assert EntityTracker().to_dict()["version"] == 1

    def test_from_dict_ignores_non_dict(self):
        t = EntityTracker()
        assert t.from_dict("nope") == 0
        assert t.file_count() == 0

    def test_from_dict_wrong_field_types(self):
        t = EntityTracker()
        assert t.from_dict({"files": "oops", "actions": 42}) == 0
        assert t.file_count() == 0
        assert t.actions() == []

    def test_from_dict_none_fields_are_silent(self):
        """字段缺失（None）不算异常，不打 warning"""
        t = EntityTracker()
        assert t.from_dict({}) == 0

    def test_from_dict_skips_bad_file_entries(self):
        t = EntityTracker()
        n = t.from_dict({"files": ["bad", {"size": 1}, {"name": "ok.txt"}]})
        assert n == 1
        assert t.files()[0]["name"] == "ok.txt"

    def test_from_dict_caps_at_max_files(self):
        t = EntityTracker()
        n = t.from_dict({"files": _files(MAX_FILES + 15)})
        assert n == MAX_FILES

    def test_from_dict_caps_actions(self):
        t = EntityTracker(max_actions=3)
        t.from_dict({"actions": [{"action": f"a{i}"} for i in range(10)]})
        assert len(t.actions()) == 3
        assert t.actions()[-1].action == "a9"

    def test_from_dict_drops_bad_actions(self):
        t = EntityTracker()
        t.from_dict({"actions": [{"action": "ok"}, "bad", {"nope": 1}]})
        assert [a.action for a in t.actions()] == ["ok"]

    def test_from_dict_overwrites_existing(self):
        t = EntityTracker()
        t.push_files(_files(5))
        t.from_dict({"files": _files(2)})
        assert t.file_count() == 2

    def test_push_action_records_timestamp(self):
        t = EntityTracker()
        before = time.time()
        t.push_action("file_read", {}, 1)
        rec = t.last_action()
        assert rec is not None and rec.timestamp >= before


# ══════════════════════════════════════════════════
#  TrackerStore：写
# ══════════════════════════════════════════════════

class TestStoreWrite:

    def test_flush_creates_file(self, store_path):
        t = EntityTracker()
        t.push_files(_files(2))
        s = TrackerStore(str(store_path), throttle=0.0)

        assert s.flush(t) is True
        assert store_path.exists()

        data = json.loads(store_path.read_text(encoding="utf-8"))
        assert data["version"] == SCHEMA_VERSION
        assert len(data["files"]) == 2
        assert "saved_at" in data

    def test_creates_parent_directory(self, store_path):
        s = TrackerStore(str(store_path), throttle=0.0)
        assert s.flush(EntityTracker()) is True
        assert store_path.parent.is_dir()

    def test_atomic_write_leaves_no_tmp(self, store_path):
        s = TrackerStore(str(store_path), throttle=0.0)
        s.flush(EntityTracker())
        leftovers = [p.name for p in store_path.parent.iterdir() if p.name.endswith(".tmp")]
        assert leftovers == [], f"残留临时文件: {leftovers}"

    def test_throttle_skips_second_write(self, store_path):
        t = EntityTracker()
        s = TrackerStore(str(store_path), throttle=60.0)

        assert s.save(t) is True, "首次应写入"
        t.push_files(_files(1))
        assert s.save(t) is False, "节流窗口内应跳过"
        assert s.stats()["writes"] == 1

    def test_force_bypasses_throttle(self, store_path):
        t = EntityTracker()
        s = TrackerStore(str(store_path), throttle=60.0)
        s.save(t)
        t.push_files(_files(3))

        assert s.save(t, force=True) is True
        assert s.stats()["writes"] == 2
        assert len(json.loads(store_path.read_text(encoding="utf-8"))["files"]) == 3

    def test_disabled_store_writes_nothing(self, store_path):
        s = TrackerStore(str(store_path), enabled=False)
        assert s.save(EntityTracker()) is False
        assert s.flush(EntityTracker()) is False
        assert not store_path.exists()

    def test_write_failure_is_swallowed(self, tmp_path):
        """父路径是个文件 → mkdir 失败 → 返回 False 且不抛异常"""
        blocker = tmp_path / "blocker"
        blocker.write_text("x", encoding="utf-8")
        s = TrackerStore(str(blocker / "sub" / "state.json"), throttle=0.0)

        assert s.flush(EntityTracker()) is False
        assert s.stats()["writes"] == 0

    def test_forbidden_user_dir_disables_store(self):
        desktop = Path(os.path.expanduser("~")) / "Desktop" / "agent_entities.json"
        s = TrackerStore(str(desktop))
        assert s.enabled is False
        assert s.save(EntityTracker()) is False

    def test_repr_and_path(self, store_path):
        s = TrackerStore(str(store_path))
        assert str(store_path) in repr(s)
        assert s.path == store_path

    def test_replace_failure_cleans_tmp(self, store_path, monkeypatch):
        """os.replace 失败 → 返回 False 且不留临时文件"""
        def boom(src, dst):
            raise OSError("换名失败")

        monkeypatch.setattr(os, "replace", boom)
        s = TrackerStore(str(store_path), throttle=0.0)

        assert s.flush(EntityTracker()) is False
        assert s.stats()["writes"] == 0
        if store_path.parent.exists():
            leftovers = [p.name for p in store_path.parent.iterdir()]
            assert leftovers == [], f"残留: {leftovers}"

    def test_unlink_failure_is_ignored(self, store_path, monkeypatch):
        """连临时文件都删不掉 → 仍返回 False，不抛异常"""
        monkeypatch.setattr(os, "replace", lambda src, dst: (_ for _ in ()).throw(OSError("boom")))
        monkeypatch.setattr(os, "unlink", lambda p: (_ for _ in ()).throw(OSError("删不掉")))

        s = TrackerStore(str(store_path), throttle=0.0)
        assert s.flush(EntityTracker()) is False


# ══════════════════════════════════════════════════
#  TrackerStore：读
# ══════════════════════════════════════════════════

class TestStoreRead:

    def test_missing_file_returns_zero(self, store_path):
        s = TrackerStore(str(store_path))
        assert s.load_into(EntityTracker()) == 0

    def test_disabled_store_loads_nothing(self, store_path):
        store_path.parent.mkdir(parents=True, exist_ok=True)
        store_path.write_text(json.dumps({"version": 1, "files": _files(2)}), encoding="utf-8")
        s = TrackerStore(str(store_path), enabled=False)
        assert s.load_into(EntityTracker()) == 0

    def test_corrupt_json_returns_zero(self, store_path):
        store_path.parent.mkdir(parents=True, exist_ok=True)
        store_path.write_text("{ not json at all", encoding="utf-8")
        s = TrackerStore(str(store_path))

        t = EntityTracker()
        assert s.load_into(t) == 0
        assert t.file_count() == 0

    def test_top_level_not_object(self, store_path):
        store_path.parent.mkdir(parents=True, exist_ok=True)
        store_path.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
        assert TrackerStore(str(store_path)).load_into(EntityTracker()) == 0

    def test_newer_schema_version_ignored(self, store_path):
        store_path.parent.mkdir(parents=True, exist_ok=True)
        store_path.write_text(
            json.dumps({"version": SCHEMA_VERSION + 1, "files": _files(2)}),
            encoding="utf-8",
        )
        assert TrackerStore(str(store_path)).load_into(EntityTracker()) == 0

    def test_read_failure_is_swallowed(self, tmp_path):
        """路径是目录 → read_text 抛 OSError → 按空上下文启动"""
        dir_path = tmp_path / "state.json"
        dir_path.mkdir()
        assert TrackerStore(str(dir_path)).load_into(EntityTracker()) == 0

    def test_restores_and_reports(self, store_path):
        t1 = EntityTracker()
        t1.push_files(_files(4))
        t1.push_action("file_list", {}, 4)
        s = TrackerStore(str(store_path), throttle=0.0)
        s.flush(t1)

        t2 = EntityTracker()
        assert s.load_into(t2) == 4
        assert s.stats()["restored"] == 4
        assert t2.last_action().action == "file_list"

    def test_round_trip_survives_new_store_instance(self, store_path):
        """模拟重启：新进程/新实例从同一文件恢复"""
        t1 = EntityTracker()
        t1.push_files(_files(3))

        TrackerStore(str(store_path), throttle=0.0).flush(t1)

        restored = EntityTracker()
        TrackerStore(str(store_path)).load_into(restored)
        assert [f["name"] for f in restored.resolve_ordinal("第一个") or []] == ["f1.png"]

    def test_empty_state_round_trip(self, store_path):
        s = TrackerStore(str(store_path), throttle=0.0)
        s.flush(EntityTracker())

        t = EntityTracker()
        assert s.load_into(t) == 0


# ══════════════════════════════════════════════════
#  管线接入
# ══════════════════════════════════════════════════

class TestPipelineIntegration:

    @pytest.fixture
    def guard(self, sandbox):
        g = BasicGuard(whitelist=[str(sandbox)], audit_enabled=False)
        yield g
        g.close()

    def _pipeline(self, guard, store=None):
        bus = EventBus()
        reg = ToolRegistry()
        for t in all_file_tools(guard):
            reg.register(t)
        ex = ThreadPoolExecutorProvider(reg, pool_size=2, timeout=5)
        tracker = EntityTracker()
        p = AgentPipeline(bus, RuleRouter(), guard, ex, reg, None,
                          tracker=tracker, tracker_store=store)
        p.start()
        return p, ex, tracker

    def test_update_tracker_persists(self, guard, store_path):
        """工具结果写入实体栈后立即落盘（节流=0）"""
        store = TrackerStore(str(store_path), throttle=0.0)
        p, ex, tracker = self._pipeline(guard, store)
        try:
            result = ToolResult.ok(data=_files(2), summary="找到 2 个文件", count=2)
            p._update_tracker("file_search", result)

            assert store_path.exists(), "应已落盘"
            data = json.loads(store_path.read_text(encoding="utf-8"))
            assert len(data["files"]) == 2
        finally:
            p.stop()
            ex.shutdown(wait=True)

    def test_stats_exposes_restored(self, guard, store_path):
        store_path.parent.mkdir(parents=True, exist_ok=True)
        store_path.write_text(
            json.dumps({"version": 1, "files": _files(3), "actions": []}),
            encoding="utf-8",
        )
        store = TrackerStore(str(store_path))
        store.load_into(EntityTracker())          # 让 restored 有值

        p, ex, tracker = self._pipeline(guard, store)
        try:
            assert p.stats()["tracker_restored"] == 3
        finally:
            p.stop()
            ex.shutdown(wait=True)

    def test_stats_without_store(self, guard):
        p, ex, _ = self._pipeline(guard, None)
        try:
            assert p.stats()["tracker_restored"] == 0
        finally:
            p.stop()
            ex.shutdown(wait=True)

    def test_store_failure_does_not_break_pipeline(self, guard, tmp_path):
        """落盘失败（父路径是文件）不应影响实体栈更新"""
        blocker = tmp_path / "blocker"
        blocker.write_text("x", encoding="utf-8")
        store = TrackerStore(str(blocker / "sub" / "s.json"), throttle=0.0)

        p, ex, tracker = self._pipeline(guard, store)
        try:
            p._update_tracker("file_search", ToolResult.ok(data=_files(2), summary="ok", count=2))
            assert tracker.file_count() == 2, "实体栈仍应更新"
        finally:
            p.stop()
            ex.shutdown(wait=True)


# ══════════════════════════════════════════════════
#  装配接入
# ══════════════════════════════════════════════════

class TestBootstrapIntegration:

    def test_start_restores_and_dispose_flushes(self, sandbox, store_path):
        """完整闭环：第一次装配写入 → dispose 落盘 → 第二次装配恢复"""
        cfg = AgentConfig(
            path_whitelist=[str(sandbox)], audit_enabled=False, ack_enabled=False,
            tools_system=False, tools_productivity=False, tools_browser=False,
            tracker_persist=True, tracker_store=str(store_path),
        )

        s1 = build_agent_stack(cfg, EventBus(), synthesize=None)
        s1.start()
        s1.tracker.push_files(_files(3))
        s1.dispose()
        assert store_path.exists(), "dispose 应强制落盘"

        s2 = build_agent_stack(cfg, EventBus(), synthesize=None)
        s2.start()
        try:
            assert s2.tracker.file_count() == 3, "start 应恢复实体栈"
            assert [f["name"] for f in s2.tracker.resolve_ordinal("第二个") or []] == ["f2.png"]
            assert s2.stats()["tracker_store"]["restored"] == 3
        finally:
            s2.dispose()

    def test_disabled_persist_writes_nothing(self, sandbox, store_path):
        cfg = AgentConfig(
            path_whitelist=[str(sandbox)], audit_enabled=False, ack_enabled=False,
            tools_system=False, tools_productivity=False, tools_browser=False,
            tracker_persist=False, tracker_store=str(store_path),
        )

        s = build_agent_stack(cfg, EventBus(), synthesize=None)
        s.start()
        try:
            s.tracker.push_files(_files(2))
            assert s.tracker_store is None
            assert s.stats()["tracker_store"] is None
        finally:
            s.dispose()

        assert not store_path.exists(), "关闭持久化时不应产生任何文件"

    def test_corrupt_store_does_not_block_start(self, sandbox, store_path):
        store_path.parent.mkdir(parents=True, exist_ok=True)
        store_path.write_text("}}} broken", encoding="utf-8")

        cfg = AgentConfig(
            path_whitelist=[str(sandbox)], audit_enabled=False, ack_enabled=False,
            tools_system=False, tools_productivity=False, tools_browser=False,
            tracker_persist=True, tracker_store=str(store_path),
        )

        s = build_agent_stack(cfg, EventBus(), synthesize=None)
        s.start()
        try:
            assert s.tracker.file_count() == 0
            assert s.pipeline._enabled is True, "损坏文件不应阻塞启动"
        finally:
            s.dispose()

    def test_default_store_path_is_under_project_data(self):
        """默认路径落在项目 data/ 下，不写入用户目录"""
        cfg = AgentConfig()
        s = TrackerStore(path=cfg.tracker_store)
        assert s.enabled is True, "默认路径不应被判为违规"
        assert s.path.parts[0] == "data" or "data" in s.path.parts

    def test_unexpected_restore_error_does_not_block_start(self, sandbox, store_path, monkeypatch):
        """store.load_into 抛意外异常 → 记录告警后继续启动"""
        cfg = AgentConfig(
            path_whitelist=[str(sandbox)], audit_enabled=False, ack_enabled=False,
            tools_system=False, tools_productivity=False, tools_browser=False,
            tracker_persist=True, tracker_store=str(store_path),
        )
        s = build_agent_stack(cfg, EventBus(), synthesize=None)

        def boom(_tracker):
            raise RuntimeError("恢复时炸了")

        monkeypatch.setattr(s.tracker_store, "load_into", boom)
        s.start()
        try:
            assert s.tracker.file_count() == 0
            assert s.pipeline._started is True, "管线仍应启动"
        finally:
            s.dispose()
