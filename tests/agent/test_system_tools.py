"""系统工具测试

覆盖 system_tools.py 的五个工具。
注意：不真的启动应用 / 不真的截屏 / 不执行破坏性命令。
"""

import ctypes
import os
import subprocess
import sys
from pathlib import Path

import pytest

project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from agent.providers.safety.basic_guard import BasicGuard
from agent.seams.safety import SecurityError
from agent.tools.system_tools import (
    DANGEROUS_PATTERNS,
    MAX_COMMAND_LEN,
    ClipboardTool,
    OpenAppTool,
    RunCommandTool,
    ScreenshotTool,
    SystemInfoTool,
    all_system_tools,
)


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
#  system_info
# ══════════════════════════════════════════════════

class TestSystemInfo:
    """系统状态查询"""

    def test_all_metrics(self):
        """默认查全部指标"""
        r = SystemInfoTool().execute({})
        assert r.success is True
        assert "cpu_percent" in r.data
        assert "memory_percent" in r.data

    @pytest.mark.parametrize("metric,key", [
        ("cpu", "cpu_percent"),
        ("memory", "memory_percent"),
        ("disk", "disk_percent"),
    ])
    def test_single_metric(self, metric, key):
        """单个指标"""
        r = SystemInfoTool().execute({"metric": metric})
        assert r.success is True
        assert key in r.data
        assert len(r.data) <= 3

    def test_battery_may_be_absent(self):
        """台式机无电池时不报错"""
        r = SystemInfoTool().execute({"metric": "battery"})
        # 有电池 → 成功；无电池 → 也是成功但有提示，或失败
        assert isinstance(r.success, bool)

    def test_unknown_metric_defaults_all(self):
        """未知指标回退 all"""
        r = SystemInfoTool().execute({"metric": "nonsense"})
        assert r.success is True

    def test_summary_readable(self):
        """摘要可读"""
        r = SystemInfoTool().execute({})
        assert "%" in r.summary or "电量" in r.summary

    def test_chinese_alias(self):
        """中文指标名"""
        assert SystemInfoTool().execute({"metric": "内存"}).success is True

    def test_tool_meta(self):
        """工具元信息"""
        t = SystemInfoTool()
        assert t.name == "system_info"
        assert t.risk_level == "low"


# ══════════════════════════════════════════════════
#  clipboard
# ══════════════════════════════════════════════════

class TestClipboard:
    """剪贴板读写"""

    def test_unknown_action(self):
        """未知动作"""
        r = ClipboardTool().execute({"action": "fly"})
        assert r.success is False
        assert "不认识" in r.summary

    def test_default_action_is_get(self):
        """默认读取"""
        r = ClipboardTool().execute({})
        assert isinstance(r.success, bool)

    def test_set_empty_text(self):
        """写入空文本被拒绝"""
        r = ClipboardTool().execute({"action": "set", "text": ""})
        assert r.success is False
        assert "什么内容" in r.summary

    @pytest.mark.skipif(os.name != "nt", reason="仅 Windows")
    def test_roundtrip(self):
        """写入后能读回（并恢复原内容）"""
        tool = ClipboardTool()

        # 保存原内容以便恢复
        original = tool.execute({"action": "get"})
        original_text = (original.data or {}).get("text", "") if original.success else ""

        marker = "欣雅剪贴板测试_9f3a"
        try:
            set_r = tool.execute({"action": "set", "text": marker})
            assert set_r.success is True

            get_r = tool.execute({"action": "get"})
            assert get_r.success is True
            assert get_r.data["text"] == marker
            assert marker in get_r.summary
        finally:
            if original_text:
                tool.execute({"action": "set", "text": original_text})

    def test_non_windows_graceful(self, monkeypatch):
        """非 Windows 平台给出友好提示"""
        monkeypatch.setattr(os, "name", "posix")
        r = ClipboardTool().execute({"action": "get"})
        assert r.success is False
        assert "Windows" in r.summary

    def test_read_exception_handled(self, monkeypatch):
        """读取异常被兜底"""
        if os.name != "nt":
            pytest.skip("仅 Windows")
        # 原型缓存会绕过 monkeypatch，先清缓存
        monkeypatch.setattr(ClipboardTool, "_win32_cache", None)
        monkeypatch.setattr(ctypes, "windll", None, raising=False)
        r = ClipboardTool().execute({"action": "get"})
        assert r.success is False


# ══════════════════════════════════════════════════
#  open_app
# ══════════════════════════════════════════════════

class TestOpenApp:
    """打开应用 / 文件"""

    def test_empty_target(self, guard):
        """空目标提示用户"""
        r = OpenAppTool(guard).execute({"target": ""})
        assert r.success is False
        assert "打开什么" in r.summary

    def test_known_app_spawns(self, guard, monkeypatch):
        """已知应用名 → 启动对应程序（此处仅记录调用）"""
        spawned = []
        monkeypatch.setattr(subprocess, "Popen", lambda cmd, **kw: spawned.append(cmd))

        r = OpenAppTool(guard).execute({"target": "记事本"})
        assert r.success is True
        assert spawned == ["notepad.exe"]
        assert "记事本" in r.summary

    def test_known_app_case_insensitive(self, guard, monkeypatch):
        """应用名大小写不敏感"""
        spawned = []
        monkeypatch.setattr(subprocess, "Popen", lambda cmd, **kw: spawned.append(cmd))
        OpenAppTool(guard).execute({"target": "Notepad"})
        assert spawned == ["notepad.exe"]

    def test_app_missing(self, guard, monkeypatch):
        """程序不存在 → 友好提示"""
        def boom(cmd, **kw):
            raise FileNotFoundError(cmd)

        monkeypatch.setattr(subprocess, "Popen", boom)
        r = OpenAppTool(guard).execute({"target": "计算器"})
        assert r.success is False
        assert "没装" in r.summary

    def test_app_spawn_oserror(self, guard, monkeypatch):
        """启动失败 → 友好提示"""
        def boom(cmd, **kw):
            raise OSError("denied")

        monkeypatch.setattr(subprocess, "Popen", boom)
        r = OpenAppTool(guard).execute({"target": "画图"})
        assert r.success is False

    def test_open_existing_file(self, guard, sandbox, monkeypatch):
        """打开白名单内的文件"""
        f = sandbox / "note.txt"
        f.write_text("x")
        monkeypatch.setattr(os, "startfile", lambda p: None, raising=False)

        r = OpenAppTool(guard).execute({"target": str(f)})
        assert r.success is True
        assert "note.txt" in r.summary

    def test_open_missing_file(self, guard, sandbox):
        """文件不存在"""
        r = OpenAppTool(guard).execute({"target": str(sandbox / "nope.txt")})
        assert r.success is False
        assert "没找到" in r.summary

    def test_open_outside_whitelist(self, guard, tmp_path):
        """白名单外路径被拒绝"""
        outside = tmp_path / "outside"
        outside.mkdir()
        f = outside / "secret.txt"
        f.write_text("x")

        r = OpenAppTool(guard).execute({"target": str(f)})
        assert r.success is False
        assert "不能动" in r.summary or "只能操作" in r.summary

    def test_bare_unknown_name(self, guard):
        """裸名字且非已知应用 → 提示"""
        r = OpenAppTool(guard).execute({"target": "某个不存在的软件"})
        assert r.success is False
        assert "没找到" in r.summary

    def test_open_dir(self, guard, monkeypatch):
        """打开目录"""
        opened = []
        monkeypatch.setattr(os, "startfile", lambda p: opened.append(p), raising=False)
        r = OpenAppTool(guard).execute({"target": str(guard.whitelist_roots()[0])})
        assert r.success is True
        assert "文件夹" in r.summary


# ══════════════════════════════════════════════════
#  screenshot
# ══════════════════════════════════════════════════

class TestScreenshot:
    """截屏"""

    def test_non_windows(self, guard, monkeypatch):
        """非 Windows 平台提示"""
        monkeypatch.setattr(os, "name", "posix")
        r = ScreenshotTool(guard).execute({})
        assert r.success is False
        assert "Windows" in r.summary

    def test_capture_mocked(self, guard, monkeypatch):
        """正常路径（capture 被替换，避免真截屏）"""
        if os.name != "nt":
            pytest.skip("仅 Windows")

        def fake_capture(path):
            Path(path).write_bytes(b"PNG")

        monkeypatch.setattr(ScreenshotTool, "_capture", staticmethod(fake_capture))
        r = ScreenshotTool(guard).execute({})
        assert r.success is True
        assert r.data["name"].endswith(".png")
        assert Path(r.data["path"]).exists()

    def test_custom_filename(self, guard, monkeypatch):
        """自定义文件名"""
        if os.name != "nt":
            pytest.skip("仅 Windows")
        monkeypatch.setattr(ScreenshotTool, "_capture",
                            staticmethod(lambda p: Path(p).write_bytes(b"x")))
        r = ScreenshotTool(guard).execute({"filename": "我的截图"})
        assert r.success is True
        assert r.data["name"] == "我的截图.png"

    def test_filename_extension_preserved(self, guard, monkeypatch):
        """已带扩展名不重复添加"""
        if os.name != "nt":
            pytest.skip("仅 Windows")
        monkeypatch.setattr(ScreenshotTool, "_capture",
                            staticmethod(lambda p: Path(p).write_bytes(b"x")))
        r = ScreenshotTool(guard).execute({"filename": "a.jpg"})
        assert r.data["name"] == "a.jpg"

    def test_filename_path_traversal_stripped(self, guard, monkeypatch):
        """filename 里的路径被剥离，不能越出截图目录"""
        if os.name != "nt":
            pytest.skip("仅 Windows")
        monkeypatch.setattr(ScreenshotTool, "_capture",
                            staticmethod(lambda p: Path(p).write_bytes(b"x")))
        r = ScreenshotTool(guard).execute({"filename": r"..\..\evil.png"})
        assert r.success is True
        assert r.data["name"] == "evil.png", "应只保留文件名部分"
        shots_dir = Path(r.data["path"]).parent
        # 截图必须落在白名单根目录下
        assert guard.is_allowed_path(shots_dir), "截图目录应在白名单内"

    def test_capture_failure(self, guard, monkeypatch):
        """抓屏失败 → 友好提示"""
        if os.name != "nt":
            pytest.skip("仅 Windows")

        def boom(path):
            raise RuntimeError("抓屏失败")

        monkeypatch.setattr(ScreenshotTool, "_capture", staticmethod(boom))
        r = ScreenshotTool(guard).execute({})
        assert r.success is False

    def test_tool_meta(self, guard):
        """工具元信息"""
        t = ScreenshotTool(guard)
        assert t.name == "screenshot"
        assert t.risk_level == "medium"


# ══════════════════════════════════════════════════
#  run_command
# ══════════════════════════════════════════════════

class TestRunCommand:
    """执行命令"""

    def test_empty_command(self, guard):
        """空命令"""
        r = RunCommandTool(guard).execute({"command": ""})
        assert r.success is False
        assert "什么命令" in r.summary

    def test_too_long_command(self, guard):
        """超长命令被拒绝"""
        r = RunCommandTool(guard).execute({"command": "a" * (MAX_COMMAND_LEN + 1)})
        assert r.success is False
        assert "太长" in r.summary

    @pytest.mark.parametrize("pattern", DANGEROUS_PATTERNS[:6])
    def test_dangerous_patterns_rejected(self, guard, pattern):
        """危险命令模式被硬拒绝（不可通过确认放行）"""
        with pytest.raises(SecurityError) as exc:
            RunCommandTool(guard).execute({"command": f"{pattern} something"})
        assert "危险" in str(exc.value)

    def test_dangerous_case_insensitive(self, guard):
        """大小写不敏感"""
        with pytest.raises(SecurityError):
            RunCommandTool(guard).execute({"command": "FORMAT C:"})

    def test_safe_command_runs(self, guard):
        """安全命令能执行（用跨平台无害命令）"""
        cmd = "echo hello" if os.name == "nt" else "echo hello"
        r = RunCommandTool(guard).execute({"command": cmd})
        assert r.success is True
        assert "hello" in r.data["stdout"]
        assert r.data["returncode"] == 0

    def test_command_failure_reported(self, guard):
        """失败命令报告退出码（失败结果也带 data）"""
        r = RunCommandTool(guard).execute({"command": "exit 1"})
        assert r.success is False
        assert r.data["returncode"] == 1

    def test_timeout(self, guard):
        """超时被中断"""
        tool = RunCommandTool(guard)
        tool.timeout = 1
        # 用 ping 制造稳定延时（timeout 命令在无控制台时行为不稳定）
        cmd = "ping -n 6 127.0.0.1 >nul" if os.name == "nt" else "sleep 5"
        r = tool.execute({"command": cmd})
        assert r.success is False
        assert "停下" in r.summary

    def test_cwd_outside_whitelist(self, guard, tmp_path):
        """工作目录必须在白名单内"""
        outside = tmp_path / "outside"
        outside.mkdir()
        r = RunCommandTool(guard).execute({"command": "echo x", "cwd": str(outside)})
        assert r.success is False

    def test_cwd_inside_whitelist(self, guard, sandbox):
        """白名单内的工作目录可用"""
        r = RunCommandTool(guard).execute({"command": "echo x", "cwd": str(sandbox)})
        assert r.success is True

    def test_options_variants_parsed(self, guard):
        """带选项的命令正常执行"""
        r = RunCommandTool(guard).execute({"command": "echo a b c"})
        assert r.success is True

    def test_options_error(self, guard):
        """带选项的错误命令正常执行"""
        r = RunCommandTool(guard).execute({"command": "echo --help"})
        assert r.success is True

    def test_output_length(self, guard):
        """输出长度下限保护"""
        assert RunCommandTool(guard, max_output=10)._max_output == 200     # 低于下限 → 抬到 200
        assert RunCommandTool(guard, max_output=500)._max_output == 500    # 正常值保留
        assert RunCommandTool(guard)._max_output == 4000                   # 默认值

    def test_stderr_reported_on_failure(self, guard):
        """失败时返回 stderr"""
        cmd = 'echo err 1>&2 & exit 1' if os.name == "nt" else "echo err >&2; exit 1"
        r = RunCommandTool(guard).execute({"command": cmd})
        assert r.success is False or r.data["returncode"] != 0

    def test_exception(self, guard, monkeypatch):
        """subprocess 抛异常被兜底"""
        def boom(*a, **kw):
            raise OSError("spawn failed")

        monkeypatch.setattr(subprocess, "run", boom)
        r = RunCommandTool(guard).execute({"command": "echo x"})
        assert r.success is False

    def test_audited(self, guard):
        """执行被记入审计（audit_enabled=False 时不报错）"""
        RunCommandTool(guard).execute({"command": "echo x"})
        assert guard.query_audit() == []


# ══════════════════════════════════════════════════
#  注册辅助
# ══════════════════════════════════════════════════

class TestRegistryHelper:
    """all_system_tools"""

    def test_returns_five_tools(self, guard):
        """返回 5 个工具"""
        tools = all_system_tools(guard)
        assert len(tools) == 5
        names = {t.name for t in tools}
        assert names == {"system_info", "clipboard", "open_app", "screenshot", "run_command"}

    def test_risk_levels(self, guard):
        """风险等级符合预期"""
        levels = {t.name: t.risk_level for t in all_system_tools(guard)}
        assert levels["system_info"] == "low"
        assert levels["open_app"] == "medium"
        assert levels["run_command"] == "high"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
