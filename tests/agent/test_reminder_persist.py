"""提醒持久化测试（P4-A2 / 债务 D13）

这个模块修的是一个**具体的、会让用户被骗的失败**：

    用户："30 分钟后提醒我喝水" → 欣雅："好的，30分钟后我会提醒你喝水"
    → 用户关掉程序 → 重新打开 → 时间到了 → **什么都没发生**

而用户以为提醒还等着。所以本文件既测存储层自身（原子写/损坏降级/不落用户目录），
也测**那个真实场景已经不复现**（跨进程恢复后仍会到点播报）。
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from agent.reminder_store import (  # noqa: E402
    DEFAULT_STORE_PATH,
    MAX_ITEMS,
    SCHEMA_VERSION,
    ReminderStore,
)
from agent.tools.productivity_tools import ReminderTool  # noqa: E402


class FakeStore:
    """鸭子类型的存储桩：记录调用，可选地抛异常"""

    def __init__(self, payload=None, save_ok=True,
                 load_raises=False, save_raises=False):
        self.payload = payload
        self.save_ok = save_ok
        self.load_raises = load_raises
        self.save_raises = save_raises
        self.saved = []
        self.loads = 0

    def load(self):
        self.loads += 1
        if self.load_raises:
            raise RuntimeError("storage boom")
        return self.payload

    def save(self, payload):
        if self.save_raises:
            raise RuntimeError("storage boom")
        self.saved.append(payload)
        return self.save_ok

    @property
    def enabled(self):
        return True


# ══════════════════════════════════════════════════
#  1. 存储层：读
# ══════════════════════════════════════════════════

class TestReminderStoreLoad:
    def test_missing_file_returns_none(self, tmp_path):
        store = ReminderStore(path=str(tmp_path / "nope.json"))
        assert store.load() is None
        assert store.stats()["reads"] == 0

    def test_disabled_store_does_nothing(self, tmp_path):
        store = ReminderStore(path=str(tmp_path / "x.json"), enabled=False)
        assert store.enabled is False
        assert store.load() is None
        assert store.save({"items": []}) is False

    def test_refuses_to_write_inside_user_data_dirs(self, tmp_path):
        """**安全边界**：状态文件不许落进桌面/文档/下载/图片"""
        bad = tmp_path / "Desktop" / "reminders.json"
        store = ReminderStore(path=str(bad))
        assert store.enabled is False
        assert store.save({"items": []}) is False

    def test_valid_file_roundtrip(self, tmp_path):
        p = tmp_path / "r.json"
        p.write_text(json.dumps({
            "version": 1, "counter": 2,
            "items": [{"id": "rem-1", "what": "喝水", "due_at": 123.0,
                       "when_text": "30分钟后", "fired": False}],
        }), encoding="utf-8")
        got = ReminderStore(path=str(p)).load()
        assert got["version"] == SCHEMA_VERSION
        assert got["counter"] == 2
        assert got["items"] == [{"id": "rem-1", "what": "喝水", "due_at": 123.0,
                                 "when_text": "30分钟后"}]

    def test_corrupt_json_is_not_fatal(self, tmp_path):
        """文件坏了 = 按无提醒启动，绝不抛异常（持久化是增强能力）"""
        p = tmp_path / "bad.json"
        p.write_text("{ this is not json", encoding="utf-8")
        store = ReminderStore(path=str(p))
        assert store.load() is None

    def test_unreadable_path_is_not_fatal(self, tmp_path):
        """把路径指到一个目录上 → OSError 分支"""
        d = tmp_path / "adir"
        d.mkdir()
        assert ReminderStore(path=str(d)).load() is None

    @pytest.mark.parametrize("body", ["[1, 2, 3]", '"just a string"', "42"])
    def test_non_dict_top_level(self, tmp_path, body):
        p = tmp_path / "t.json"
        p.write_text(body, encoding="utf-8")
        assert ReminderStore(path=str(p)).load() is None

    def test_items_not_a_list(self, tmp_path):
        p = tmp_path / "t.json"
        p.write_text(json.dumps({"items": "oops"}), encoding="utf-8")
        got = ReminderStore(path=str(p)).load()
        assert got["items"] == []
        assert got["counter"] == 0          # 非法的 counter 也归零

    def test_negative_counter_becomes_zero(self, tmp_path):
        p = tmp_path / "t.json"
        p.write_text(json.dumps({"counter": -5, "items": []}), encoding="utf-8")
        assert ReminderStore(path=str(p)).load()["counter"] == 0

    @pytest.mark.parametrize("bad", [
        "not-a-dict",                                   # 条目不是对象
        {"what": "喝水", "due_at": 1.0},                # 缺 id
        {"id": "", "what": "喝水", "due_at": 1.0},      # 空 id
        {"id": "r", "due_at": 1.0},                     # 缺 what
        {"id": "r", "what": "", "due_at": 1.0},         # 空 what
        {"id": "r", "what": "喝水"},                    # 缺 due_at
        {"id": "r", "what": "喝水", "due_at": "soon"},  # due_at 不是数字
        {"id": "r", "what": "喝水", "due_at": True},    # bool 不算数字
    ])
    def test_malformed_items_are_dropped_not_raised(self, tmp_path, bad):
        """**不信任磁盘内容**：坏条目丢弃并计数，绝不因为一条坏数据丢掉全部提醒"""
        p = tmp_path / "t.json"
        p.write_text(json.dumps({"items": [bad, {"id": "rem-9", "what": "好的",
                                                 "due_at": 5.0}]}),
                     encoding="utf-8")
        store = ReminderStore(path=str(p))
        got = store.load()
        assert [i["id"] for i in got["items"]] == ["rem-9"]
        assert store.stats()["dropped"] == 1

    def test_non_string_when_text_becomes_empty(self, tmp_path):
        p = tmp_path / "t.json"
        p.write_text(json.dumps({"items": [
            {"id": "r1", "what": "喝水", "due_at": 1.0, "when_text": 123},
        ]}), encoding="utf-8")
        assert ReminderStore(path=str(p)).load()["items"][0]["when_text"] == ""

    def test_item_cap(self, tmp_path):
        """上限：文件是可编辑的，没有上限的话一个坏文件就能把内存撑爆"""
        p = tmp_path / "t.json"
        p.write_text(json.dumps({"items": [
            {"id": f"r{i}", "what": "喝水", "due_at": 1.0} for i in range(MAX_ITEMS + 5)
        ]}), encoding="utf-8")
        assert len(ReminderStore(path=str(p)).load()["items"]) == MAX_ITEMS


# ══════════════════════════════════════════════════
#  2. 存储层：写
# ══════════════════════════════════════════════════

class TestReminderStoreSave:
    def test_save_writes_atomic_json(self, tmp_path):
        p = tmp_path / "sub" / "r.json"        # 父目录不存在 → 会被创建
        store = ReminderStore(path=str(p))
        assert store.save({"counter": 1, "items": []}) is True
        data = json.loads(p.read_text(encoding="utf-8"))
        assert data["version"] == SCHEMA_VERSION
        assert data["counter"] == 1
        assert "saved_at" in data
        assert store.stats()["writes"] == 1

    def test_save_failure_is_swallowed_and_tmp_cleaned(self, tmp_path, monkeypatch):
        """落盘失败只记日志：提醒仍在内存里，不该让用户设提醒这件事失败"""
        p = tmp_path / "r.json"
        store = ReminderStore(path=str(p))

        def boom(*a, **kw):
            raise OSError("disk full")

        monkeypatch.setattr("agent.reminder_store.os.replace", boom)
        assert store.save({"items": []}) is False
        # 临时文件必须被清掉，不能在 data/ 里留一地 .tmp
        assert list(tmp_path.glob("*.tmp")) == []

    def test_tmp_cleanup_failure_is_also_swallowed(self, tmp_path, monkeypatch):
        """连清理临时文件都失败时也不能抛异常

        这条分支是**真的可达**的：Windows 上另一个进程占着句柄时 `os.unlink` 会失败。
        （所以它不该被 `# pragma: no cover` 屏蔽掉 —— 项目对 pragma 的规矩见
         AGENTS.md §16.4：屏蔽可达代码就是拿覆盖率换假绿。）
        """
        p = tmp_path / "r.json"
        store = ReminderStore(path=str(p))

        def boom_replace(*a, **kw):
            raise OSError("disk full")

        def boom_unlink(*a, **kw):
            raise OSError("file in use")

        monkeypatch.setattr("agent.reminder_store.os.replace", boom_replace)
        monkeypatch.setattr("agent.reminder_store.os.unlink", boom_unlink)
        assert store.save({"items": []}) is False     # 不该抛出

    def test_stats_and_path_and_repr(self, tmp_path):
        store = ReminderStore(path=str(tmp_path / "r.json"))
        st = store.stats()
        assert st["path"].endswith("r.json")
        assert st["reads"] == 0 and st["writes"] == 0 and st["dropped"] == 0
        assert "ReminderStore" in repr(store)

    def test_default_path(self):
        assert ReminderStore().path.as_posix().endswith(DEFAULT_STORE_PATH.replace("\\", "/"))


# ══════════════════════════════════════════════════
#  3. 工具接线
# ══════════════════════════════════════════════════

class TestReminderToolPersistence:
    def test_without_store_behaves_like_before(self):
        """不传 store = 与加持久化之前**完全一致**（向后兼容）"""
        tool = ReminderTool()
        res = tool.execute({"what": "喝水", "minutes": 30})
        assert res.ok
        assert tool.pending()
        assert tool.restore() == 0

    def test_execute_persists(self):
        store = FakeStore()
        tool = ReminderTool(store=store)
        tool.execute({"what": "喝水", "minutes": 30})
        assert len(store.saved) == 1
        assert store.saved[0]["items"][0]["what"] == "喝水"

    def test_cancel_persists_only_when_something_removed(self):
        store = FakeStore()
        tool = ReminderTool(store=store)
        rid = tool.execute({"what": "喝水", "minutes": 30}).data["id"]
        assert len(store.saved) == 1
        assert tool.cancel("rem-不存在") is False
        assert len(store.saved) == 1           # 没删掉东西就不该写盘
        assert tool.cancel(rid) is True
        assert len(store.saved) == 2
        assert store.saved[1]["items"] == []

    def test_due_now_persists_only_when_something_fired(self):
        store = FakeStore()
        tool = ReminderTool(store=store)
        tool.execute({"what": "喝水", "minutes": 30})
        assert len(store.saved) == 1
        assert tool.due_now() == []            # 还没到点 → 不写盘
        assert len(store.saved) == 1
        # 直接把 due_at 拨到过去，而不是 sleep 等它到点 ——
        # 靠 sleep 的测试会在慢机器上假失败（本轮第一版就踩了：0.06s 的窗口没等到）
        tool.pending()[0].due_at = time.time() - 1
        assert len(tool.due_now()) == 1
        assert len(store.saved) == 2
        # 响过的提醒必须从盘上消失，否则重启后会再响一次
        assert store.saved[1]["items"] == []

    def test_persist_failure_does_not_break_the_tool(self):
        store = FakeStore(save_raises=True)
        tool = ReminderTool(store=store)
        res = tool.execute({"what": "喝水", "minutes": 30})
        assert res.ok and tool.pending()       # 存不下来也得让提醒设上

    def test_save_returning_false_is_fine(self):
        store = FakeStore(save_ok=False)
        tool = ReminderTool(store=store)
        assert tool.execute({"what": "喝水", "minutes": 30}).ok

    def test_restore_from_store(self):
        store = FakeStore(payload={"counter": 2, "items": [
            {"id": "rem-1", "what": "喝水", "due_at": time.time() + 60,
             "when_text": "1分钟后"},
            {"id": "rem-2", "what": "站起来", "due_at": time.time() + 120},
        ]})
        tool = ReminderTool(store=store)
        assert tool.restore() == 2
        assert {i.what for i in tool.pending()} == {"喝水", "站起来"}
        assert store.loads == 1

    def test_restore_survives_broken_store(self):
        """存储层违约（抛异常）也不能炸掉装配"""
        tool = ReminderTool(store=FakeStore(load_raises=True))
        assert tool.restore() == 0

    def test_restore_with_none_payload(self):
        tool = ReminderTool(store=FakeStore(payload=None))
        assert tool.restore() == 0

    def test_apply_payload_skips_junk_entries(self):
        tool = ReminderTool()
        n = tool.apply_payload({"items": [
            "not-a-dict",
            {"id": "", "what": "x", "due_at": 1.0},
            {"id": "ok", "what": "喝水", "due_at": 1.0},
            {"id": "ok", "what": "重复 id 应被跳过", "due_at": 2.0},
            {"id": "badtime", "what": "喝水", "due_at": "later"},
        ]})
        assert n == 1
        assert [i.id for i in tool.pending()] == ["ok"]

    def test_counter_advances_so_ids_do_not_collide(self):
        """重启后 id 不能与已用过的撞车（否则取消会作用到错误的提醒）"""
        tool = ReminderTool()
        tool.apply_payload({"items": [{"id": "rem-7", "what": "喝水", "due_at": 1.0}]})
        rid = tool.execute({"what": "新的", "minutes": 5}).data["id"]
        assert rid == "rem-8"

    def test_counter_from_payload_field(self):
        """没有可推断 id 时，用 payload 里的 counter（例如提醒都已响过）"""
        tool = ReminderTool()
        tool.apply_payload({"counter": 12, "items": []})
        assert tool.execute({"what": "新的", "minutes": 5}).data["id"] == "rem-13"

    def test_already_fired_item_is_not_restored_as_pending(self):
        """手改/残留文件里标了 fired 的条目：当作已响过，不再排进待提醒"""
        tool = ReminderTool()
        tool.apply_payload({"items": [
            {"id": "rem-1", "what": "已响过", "due_at": 1.0, "fired": True},
        ]})
        assert tool.pending() == []
        assert tool.due_now() == []


# ══════════════════════════════════════════════════
#  4. 真实场景：跨进程（这正是 D13 说的那个失败）
# ══════════════════════════════════════════════════

class TestCrossProcessRestore:
    def test_reminder_survives_restart_and_still_fires(self, tmp_path):
        """**D13 的原始场景**：设提醒 → 关程序 → 重开 → 到点仍然响

        这里用"丢掉第一个工具实例、新建一个 store 与工具"来模拟重启
        （同一进程内换掉对象图，磁盘那一步是真的）。
        """
        path = tmp_path / "agent_reminders.json"
        fired: list = []

        # ── 第一次运行：设一条 30 分钟后的提醒（默认值就是 30 分钟）──
        tool1 = ReminderTool(store=ReminderStore(path=str(path)),
                             on_due=lambda rid, what: fired.append((rid, what)))
        rid = tool1.execute({"what": "喝水", "minutes": 30}).data["id"]
        assert rid == "rem-1"
        del tool1                                  # 模拟关掉程序

        # ── 第二次运行：新 store、新工具，从磁盘恢复 ──
        tool2 = ReminderTool(store=ReminderStore(path=str(path)),
                             on_due=lambda rid, what: fired.append((rid, what)))
        assert tool2.restore() == 1
        assert [i.what for i in tool2.pending()] == ["喝水"]
        assert fired == []                          # 还没到点，不该响

        # 到点后仍然真的响（这正是 P3 补上、P4 让它跨重启也成立的那一环）。
        # 拨指针而不是 sleep —— 测试不该依赖时序运气。
        tool2.pending()[0].due_at = time.time() - 1
        assert len(tool2.due_now()) == 1
        assert fired == [("rem-1", "喝水")]

        # 响过之后盘上也空了 → 第三次启动不会再响一遍
        tool3 = ReminderTool(store=ReminderStore(path=str(path)))
        assert tool3.restore() == 0

    def test_expired_while_closed_still_fires_on_restart(self, tmp_path):
        """关机期间到点的提醒：开机后补响一次，而不是悄悄消失

        语义选择写在这里：用户关着程序的这段时间到点了，开机后补一声，
        比"什么都没发生"更接近"提醒"的本意。
        """
        path = tmp_path / "r.json"
        path.write_text(json.dumps({"counter": 1, "items": [
            {"id": "rem-1", "what": "喝水", "due_at": time.time() - 3600,
             "when_text": "30分钟后"},
        ]}), encoding="utf-8")
        fired: list = []
        tool = ReminderTool(store=ReminderStore(path=str(path)),
                            on_due=lambda rid, what: fired.append(what))
        assert tool.restore() == 1
        assert len(tool.due_now()) == 1
        assert fired == ["喝水"]

    def test_disabled_persistence_restores_nothing(self, tmp_path):
        """配置关掉持久化 = 退回老行为（提醒不落盘、重启即丢）"""
        path = tmp_path / "r.json"
        tool1 = ReminderTool(store=ReminderStore(path=str(path), enabled=False))
        tool1.execute({"what": "喝水", "minutes": 30})
        assert not path.exists()
        tool2 = ReminderTool(store=ReminderStore(path=str(path)))
        assert tool2.restore() == 0


# ══════════════════════════════════════════════════
#  5. 插件接线（隔离到 tmp_path —— 不许写仓库的 data/）
# ══════════════════════════════════════════════════

class _FakeRegistry:
    """最小注册表：只实现插件用到的 `register`（返回注销函数）"""

    def __init__(self):
        self.tools: dict = {}

    def register(self, tool):
        self.tools[tool.name] = tool
        return lambda: self.tools.pop(tool.name, None)


def _setup_plugin(path, persist=True, scheduler=False, monkeypatch=None):
    """用一个最小上下文装配 productivity_tools 插件（状态隔离到给定路径）"""
    from types import SimpleNamespace

    from agent.plugins import SVC_CONFIG, SVC_REGISTRY
    from agent.plugins.productivity_tools_plugin import setup as tools_setup
    from core.kernel.context import Context

    reg = _FakeRegistry()
    cfg = SimpleNamespace(
        tools_productivity=True,
        productivity_reminder_scheduler=scheduler,
        productivity_reminder_persist=persist,
        productivity_reminder_store=str(path),
    )
    ctx = Context()
    ctx.provide(SVC_CONFIG, cfg)
    ctx.provide(SVC_REGISTRY, reg)
    return ctx, tools_setup(ctx), reg


class TestPluginWiringPersistence:
    """插件真的把持久化接上了（且**恢复发生在调度器启动之前**）"""

    def test_setup_persists_then_restores(self, tmp_path):
        path = tmp_path / "agent_reminders.json"

        # ── 第一次装配：设一条提醒 → 应当落盘 ──
        ctx1, dispose1, reg1 = _setup_plugin(path)
        try:
            reg1.tools["reminder"].execute({"what": "喝水", "minutes": 30})
            assert path.exists(), "插件装配出来的提醒应当落盘（否则 D13 没修好）"
        finally:
            dispose1()
            ctx1.dispose()

        # ── 第二次装配：同一路径 → 应当恢复出来 ──
        ctx2, dispose2, reg2 = _setup_plugin(path)
        try:
            assert [i.what for i in reg2.tools["reminder"].pending()] == ["喝水"]
        finally:
            dispose2()
            ctx2.dispose()

    def test_setup_with_persistence_disabled_writes_nothing(self, tmp_path):
        path = tmp_path / "agent_reminders.json"
        ctx, dispose, reg = _setup_plugin(path, persist=False)
        try:
            reg.tools["reminder"].execute({"what": "喝水", "minutes": 30})
            assert not path.exists()
        finally:
            dispose()
            ctx.dispose()

    def test_restore_runs_before_scheduler_starts(self, tmp_path, monkeypatch):
        """顺序是**设计决定**，不是实现细节：恢复必须早于调度器启动

        否则调度器先跑的那一跳看不到磁盘上的提醒，用户会以为提醒丢了 ——
        而其实只是恢复晚了一步。这里把顺序钉住，防止以后有人"顺手"调换两行。
        """
        from agent.plugins import productivity_tools_plugin as mod

        order: list = []
        monkeypatch.setattr(ReminderTool, "restore",
                            lambda self: order.append("restore") or 1)
        monkeypatch.setattr(mod.ReminderScheduler, "start",
                            lambda self: order.append("start"))

        ctx, dispose, _reg = _setup_plugin(tmp_path / "r.json", scheduler=True)
        try:
            assert order == ["restore", "start"]
        finally:
            dispose()
            ctx.dispose()
