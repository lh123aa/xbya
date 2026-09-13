"""
配置管理器测试
"""

import pytest
import tempfile
import os
import yaml
from pathlib import Path

import sys
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from core.config_manager import ConfigManager


class TestConfigManager:
    """配置管理器测试类"""
    
    def setup_method(self):
        """测试前设置"""
        self.config_path = project_root / "config.yaml"
        self.manager = ConfigManager(str(self.config_path))
    
    def test_load_config(self):
        """测试加载配置"""
        config = self.manager.config
        assert config is not None
        assert "app" in config
        assert "system" in config
        assert "plugins" in config
    
    def test_get_performance_mode(self):
        """测试获取性能模式"""
        mode = self.manager.get_performance_mode()
        assert mode in ["low", "medium", "high"]
    
    def test_set_performance_mode(self, tmp_path):
        """测试设置性能模式

        ⚠️ 用**临时配置**，不碰真实 config.yaml。
        根因（实测踩到）：这里原先读真实 `config.yaml`，而
        `set_performance_mode` 会立刻落盘，且会连带改写
        `plugins.asr.params.model_size`。一旦某次还原失败，
        用户的配置就被永久改成测试值 —— 表现是 ASR 突然变慢，
        而所有日志看起来都正常。
        """
        p = tmp_path / "cfg.yaml"
        p.write_text("system:\n  performance_mode: low\n", encoding="utf-8")
        mgr = ConfigManager(str(p))

        mgr.set_performance_mode("high")
        assert mgr.get_performance_mode() == "high"

        mgr.set_performance_mode("low")
        assert mgr.get_performance_mode() == "low"
    
    def test_get_plugin_config(self):
        """测试获取插件配置"""
        asr_config = self.manager.get_plugin_config("asr")
        assert asr_config is not None
        assert "engine" in asr_config
        assert "params" in asr_config
    
    def test_get_ui_config(self):
        """测试获取UI配置"""
        ui_config = self.manager.get_ui_config()
        assert ui_config is not None
        assert "pet_size" in ui_config
    
    def test_get_voice_config(self):
        """测试获取语音配置"""
        voice_config = self.manager.get_voice_config()
        assert voice_config is not None
        assert "wake_word" in voice_config
    
    def test_invalid_performance_mode(self):
        """测试无效性能模式"""
        with pytest.raises(ValueError):
            self.manager.set_performance_mode("invalid")
    
    def test_default_config_creation(self):
        """测试默认配置创建"""
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.yaml') as f:
            temp_path = f.name
        
        try:
            # 删除临时文件
            os.unlink(temp_path)
            
            # 创建新的配置管理器
            manager = ConfigManager(temp_path)
            
            # 验证默认配置被创建
            assert os.path.exists(temp_path)
            assert manager.config is not None
            
        finally:
            if os.path.exists(temp_path):
                os.unlink(temp_path)


class TestPerformanceModeASRChoice:
    """性能档位选 ASR 模型时，必须**先确认 GPU 真能用**。

    实测故障：`high` 档原来无条件写 `model_size: medium`。本机有 MX250 但
    缺 cublas64_12.dll，medium 只能跑 CPU —— 一段 4 秒音频要 **16 秒** 才出结果，
    用户感知就是"反应慢到没法用"。CPU 上 medium 与 small 的中文准确率差距
    远小于 16s vs 4s 的可用性差距。
    """

    def _mk(self, tmp_path):
        p = tmp_path / "cfg.yaml"
        p.write_text("system:\n  performance_mode: low\n", encoding="utf-8")
        return ConfigManager(str(p)), p

    def test_high_falls_back_to_small_when_gpu_unusable(self, tmp_path, monkeypatch):
        """GPU 不可用 → high 档必须落 small+cpu，而不是 medium"""
        mgr, _ = self._mk(tmp_path)
        monkeypatch.setattr(ConfigManager, "_gpu_usable_for_asr",
                            staticmethod(lambda: False))
        mgr.set_performance_mode("high")
        assert mgr.get("plugins.asr.params.model_size") == "small", \
            "GPU 不可用时不该上 medium（CPU 上要 16 秒）"
        assert mgr.get("plugins.asr.params.device") == "cpu"

    def test_high_uses_medium_when_gpu_usable(self, tmp_path, monkeypatch):
        """反方向：GPU+CUDA 真的可用时，high 档才上 medium+cuda"""
        mgr, _ = self._mk(tmp_path)
        monkeypatch.setattr(ConfigManager, "_gpu_usable_for_asr",
                            staticmethod(lambda: True))
        mgr.set_performance_mode("high")
        assert mgr.get("plugins.asr.params.model_size") == "medium"
        assert mgr.get("plugins.asr.params.device") == "cuda"

    def test_medium_and_low_modes_unchanged(self, tmp_path, monkeypatch):
        """medium/low 档行为不受影响"""
        mgr, _ = self._mk(tmp_path)
        monkeypatch.setattr(ConfigManager, "_gpu_usable_for_asr",
                            staticmethod(lambda: True))   # 即便 GPU 可用
        mgr.set_performance_mode("medium")
        assert mgr.get("plugins.asr.params.model_size") == "small"
        assert mgr.get("plugins.asr.params.device") == "cpu"

        mgr.set_performance_mode("low")
        assert mgr.get("plugins.asr.params.model_size") == "base"
        assert mgr.get("plugins.asr.params.device") == "cpu"

    def test_gpu_probe_returns_bool_and_does_not_raise(self):
        """探测函数必须安全：任何环境下都返回 bool，不抛异常"""
        assert isinstance(ConfigManager._gpu_usable_for_asr(), bool)


class TestD21ExternalKeyGuard:
    """D21 守护：`set()` 整份落盘时，不能抹掉外部新增的配置键。

    背景（实测发生过三次）：程序运行期间外部往 config.yaml 加了键
    （如 `voice.mic_device` / `voice.hotkey_mic_next`），
    随后程序调用 `set()` → `_save_config()` 把**内存快照**整份写回，
    外来键被静默抹掉，且 `hotkey_interrupt` 被写成 `\\`。
    """

    def _mk(self, tmp_path, initial: dict):
        p = tmp_path / "cfg.yaml"
        with open(p, "w", encoding="utf-8") as f:
            yaml.dump(initial, f, allow_unicode=True)
        return ConfigManager(str(p)), p

    def test_external_new_key_survives_set(self, tmp_path):
        """外部新增的键必须活过后续 set()"""
        mgr, path = self._mk(tmp_path, {"voice": {"hotkey_toggle": "Ctrl+Alt+M"}})
        # 模拟"外部编辑器"在程序运行期间加了一个键
        raw = yaml.safe_load(open(path, encoding="utf-8"))
        raw["voice"]["mic_device"] = 8
        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(raw, f, allow_unicode=True)

        # 程序此时 set 别的值（会整份落盘）
        mgr.set("ui.fps.current", 30)

        after = yaml.safe_load(open(path, encoding="utf-8"))
        assert after["voice"].get("mic_device") == 8, "外部新增的键被整份落盘抹掉了（D21 复发）"
        assert after["ui"]["fps"]["current"] == 30, "本次 set 的值也必须落盘"

    def test_memory_value_wins_over_disk(self, tmp_path):
        """内存里刚 set 过的值，不能被磁盘旧值反覆盖"""
        mgr, path = self._mk(tmp_path, {"voice": {"hotkey_toggle": "Ctrl+Alt+M"}})
        mgr.set("voice.hotkey_toggle", "Ctrl+Shift+M")
        after = yaml.safe_load(open(path, encoding="utf-8"))
        assert after["voice"]["hotkey_toggle"] == "Ctrl+Shift+M"

    def test_nested_external_key_survives(self, tmp_path):
        """嵌套层级的外部新增键同样要被保住"""
        mgr, path = self._mk(tmp_path, {"ui": {"fps": {"current": 60}}})
        raw = yaml.safe_load(open(path, encoding="utf-8"))
        raw["ui"]["fps"]["new_key"] = 15          # 外部加的嵌套键
        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(raw, f, allow_unicode=True)

        mgr.set("ui.pet_size", 127)
        after = yaml.safe_load(open(path, encoding="utf-8"))
        assert after["ui"]["fps"]["new_key"] == 15
        assert after["ui"]["fps"]["current"] == 60, "已有值不应被改动"

    def test_merge_missing_reports_paths(self):
        """_merge_missing 应返回被补入的键路径，便于日志点名"""
        dst = {"a": {"b": 1}}
        added = ConfigManager._merge_missing({"a": {"b": 9, "c": 2}, "d": 3}, dst)
        assert sorted(added) == ["a.c", "d"]
        assert dst["a"]["b"] == 1, "已存在的键不得被 src 覆盖"
        assert dst["a"]["c"] == 2
        assert dst["d"] == 3

    def test_corrupt_disk_does_not_break_save(self, tmp_path):
        """磁盘文件损坏时，落盘仍应成功（外部键合并只是尽力而为）"""
        mgr, path = self._mk(tmp_path, {"voice": {}})
        path.write_text("this: [is: not: valid: yaml", encoding="utf-8")
        mgr.set("voice.hotkey_toggle", "Ctrl+Alt+M")   # 不应抛异常
        after = yaml.safe_load(open(path, encoding="utf-8"))
        assert after["voice"]["hotkey_toggle"] == "Ctrl+Alt+M"


class TestConfigSaveThrottle:
    """保存节流：启动期十几处 set() 不该刷出 30+ 次整份 YAML 落盘。

    背景（实测）：启动日志里连续出现 30 多行"配置文件保存成功"，
    每行背后都是一次**整份读写 YAML**（含回读磁盘做外部键合并），
    既拖慢启动又淹没真正有用的日志。

    ⚠️ 节流的红线：不能因此丢配置。所以判据是三条同时成立 ——
      ① 首次 set 必落盘 ② 窗口内内存值仍是最新 ③ 有 flush() 兜底
    """

    def _mk(self, tmp_path):
        p = tmp_path / "cfg.yaml"
        with open(p, "w", encoding="utf-8") as f:
            yaml.dump({"ui": {"fps": {"current": 60}}}, f, allow_unicode=True)
        return ConfigManager(str(p)), p

    def test_first_set_always_saves(self, tmp_path):
        """★ 红线一：某个 key 的首次 set 必须落盘（否则用户改了设置却没保存）"""
        mgr, path = self._mk(tmp_path)
        mgr.set("voice.hotkey_toggle", "Ctrl+Alt+M")
        after = yaml.safe_load(open(path, encoding="utf-8"))
        assert after["voice"]["hotkey_toggle"] == "Ctrl+Alt+M"

    def test_repeated_set_same_key_is_throttled(self, tmp_path, monkeypatch):
        """★ 核心：同一 key 在窗口内重复 set 只落盘一次"""
        mgr, path = self._mk(tmp_path)
        saves = []
        orig = mgr._save_config
        monkeypatch.setattr(
            mgr, "_save_config",
            lambda: (saves.append(1), orig())[1]
        )
        for i in range(10):
            mgr.set("ui.fps.current", 30 + i)
        assert len(saves) == 1, \
            f"同一 key 连续 set 10 次触发了 {len(saves)} 次落盘（应被节流到 1 次）"

    def test_memory_value_is_always_latest(self, tmp_path):
        """★ 红线二：节流只影响**落盘**，内存值必须始终是最新的"""
        mgr, _ = self._mk(tmp_path)
        for i in range(5):
            mgr.set("ui.fps.current", 30 + i)
        assert mgr.get("ui.fps.current") == 34, \
            "节流期间内存值没有更新（用户会读到旧值）"

    def test_flush_forces_save(self, tmp_path):
        """★ 红线三：flush() 必须无视节流立即落盘（退出路径靠它兜底）"""
        mgr, path = self._mk(tmp_path)
        mgr.set("ui.fps.current", 30)          # 首次落盘
        mgr.set("ui.fps.current", 45)          # 被节流
        mgr.flush()
        after = yaml.safe_load(open(path, encoding="utf-8"))
        assert after["ui"]["fps"]["current"] == 45, \
            "flush() 没有强制落盘 → 被节流的值会丢"

    def test_different_keys_do_not_block_each_other(self, tmp_path, monkeypatch):
        """不同 key 各自独立计时：A 的节流不应挡住 B 的首次保存"""
        mgr, path = self._mk(tmp_path)
        mgr.set("ui.fps.current", 30)          # 首次落盘
        mgr.set("voice.hotkey_toggle", "Ctrl+Alt+M")   # 另一个 key 的首次
        after = yaml.safe_load(open(path, encoding="utf-8"))
        assert after["voice"]["hotkey_toggle"] == "Ctrl+Alt+M", \
            "不同 key 的首次 save 被误节流"

    def test_save_success_log_is_debug_not_info(self):
        """结构判据：保存成功不该按 INFO 刷日志（那是刷屏的根源）"""
        import inspect
        from core import config_manager as mod

        src = inspect.getsource(mod.ConfigManager._save_config)
        assert "logger.debug" in src, \
            "保存成功仍按 INFO 打印（启动时会刷出几十行）"
        assert 'logger.info(f"配置文件保存成功' not in src, \
            "保存成功仍是 INFO 级"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
