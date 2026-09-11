"""ASR 插件参数的两条加载路径必须一致（P5-B2）

## 守的是什么

同一个 ASR 插件有两条加载路径：

| 路径 | 代码 |
|------|------|
| 主应用启动 | `core/app.py::_load_plugins` |
| 语音服务 | `services/voice_service.py::initialize` |

`PluginLoader.load(name, params=None)` 在 `params` 为空时走 `plugin_class()` ——
**一个配置都不带**。语音服务那条路径原先正是这样：

```python
self.asr = self.plugin_loader.load(asr_engine)      # ← 没有 params=
```

于是 `config.yaml` 里的

```yaml
plugins:
  asr:
    params:
      model_size: medium
      initial_prompt: "算了 不用了 停止 取消 确定 确认"
```

在这条路径上**完全没被读到**，插件退回 `__init__` 默认值
（`model_size="base"`、`initial_prompt=""`）。**而且是静默的** —— 日志里只写
"ASR插件加载成功: faster_whisper"。

这解释了 P4-C2 里那条一直没讲通的旧记录：**"config 写 medium，语音回环实际用的是
small"**。回环脚本走语音服务这条路径，拿到的从来就不是 config 里的值。

## 这个文件钉住什么

1. 两条路径**取到同一份参数**（不是"都传了"这么含糊 —— 是逐键相等）；
2. `plugins.llm.cloud` 那个特例还在（云端连接信息单独一层，cloud 覆盖 params）；
3. 返回的是**新字典**（调用方可以随手改，不会污染配置管理器）；
4. 源码里**不许**再出现不带 `params=` 的 ASR 加载（那种写法行为上"能跑"，
   所以只有源码断言抓得住它 —— 与 D16 的守卫同一思路）。

`services/` 不在覆盖率口径内，所以这个文件的价值不是凑覆盖率，而是**把语义钉死**。
"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from core.plugin_params import plugin_params  # noqa: E402


class _Cfg:
    """最小配置桩：只实现 `get(key, default)`"""

    def __init__(self, data):
        self.data = data

    def get(self, key, default=None):
        cur = self.data
        for part in key.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return default
            cur = cur[part]
        return cur


#: 与真实 config.yaml 同形的片段（含 P4-B5 加进去的解码偏置）
ASR_PARAMS = {
    "model_size": "medium",
    "device": "cpu",
    "compute_type": "int8",
    "initial_prompt": "算了 不用了 停止 取消 确定 确认",
}
CFG = _Cfg({
    "plugins": {
        "asr": {"engine": "faster_whisper", "params": ASR_PARAMS},
        "tts": {"engine": "edge_tts", "params": {"voice": "zh-CN-XiaoxiaoNeural"}},
        "llm": {
            "engine": "openrouter",
            "params": {"model": "openai/gpt-oss-120b"},
            "cloud": {"api_key": "k" * 24, "base_url": "http://example.invalid"},
        },
    }
})


class TestParamsAreActuallyReturned:
    def test_asr_params_keep_every_key(self):
        got = plugin_params(CFG, "asr", "faster_whisper")
        assert got == ASR_PARAMS, f"ASR 参数被改动/丢失：{got}"

    def test_initial_prompt_survives(self):
        """偏置是 P4-B5 花了一整轮才加上的东西，最不该在另一条路径上丢"""
        got = plugin_params(CFG, "asr", "faster_whisper")
        assert got["initial_prompt"] == ASR_PARAMS["initial_prompt"]
        assert got["model_size"] == "medium"

    def test_missing_params_returns_empty_dict_not_none(self):
        """没有 params 时返回 `{}` 而不是 None —— 调用方要能直接解引用"""
        cfg = _Cfg({"plugins": {"asr": {"engine": "x"}}})
        got = plugin_params(cfg, "asr", "x")
        assert got == {} and got is not None

    def test_returns_a_fresh_dict(self):
        """改返回值不该污染配置（配置管理器是单例，被改会牵连整机行为）"""
        got = plugin_params(CFG, "asr", "faster_whisper")
        got["model_size"] = "tiny"
        again = plugin_params(CFG, "asr", "faster_whisper")
        assert again["model_size"] == "medium", "返回值与配置共享了同一个 dict"

    def test_llm_cloud_overrides_params(self):
        """唯一特例：`llm` + `openai_api` 要并上 `plugins.llm.cloud`"""
        got = plugin_params(CFG, "llm", "openai_api")
        assert got["api_key"] == "k" * 24
        assert got["base_url"] == "http://example.invalid"
        assert got["model"] == "openai/gpt-oss-120b", "cloud 不该把 params 整个替换掉"

    def test_llm_without_cloud_special_case(self):
        """别的引擎不并 cloud —— 特例只属于那一个组合"""
        got = plugin_params(CFG, "llm", "openrouter")
        assert "api_key" not in got
        assert got["model"] == "openai/gpt-oss-120b"


class TestBothLoadingPathsUseTheSameHelper:
    """两条路径都必须走这个函数 —— 多写一份判据就会漂移"""

    def test_voice_service_imports_the_helper(self):
        import services.voice_service as vs

        assert hasattr(vs, "plugin_params_for"), (
            "voice_service 没有引入 plugin_params_for —— 它会退回「不传 params」"
        )

    def test_core_app_imports_the_helper(self):
        import core.app as app

        assert hasattr(app, "plugin_params_for"), (
            "core/app.py 没有引入 plugin_params_for"
        )

    def test_voice_service_passes_params_to_the_loader(self):
        """源码断言：ASR 加载**必须**带 `params=`（P5-B2 修的就是这一行）"""
        src = (project_root / "services/voice_service.py").read_text(encoding="utf-8")
        assert "self.plugin_loader.load(asr_engine, params=asr_params)" in src, (
            "语音服务的 ASR 加载又变回不带 params 了"
        )
        assert "self.plugin_loader.load(asr_engine)" not in src, (
            "voice_service 里仍有不带 params 的 ASR 加载"
        )

    def test_voice_service_passes_params_to_tts_too(self):
        src = (project_root / "services/voice_service.py").read_text(encoding="utf-8")
        assert "self.plugin_loader.load(tts_engine, params=tts_params)" in src

    def test_voice_service_initialize_signature_still_simple(self):
        """`initialize()` 不带参数 —— 它自己从 config 取，调用方不需要知道细节"""
        from services.voice_service import VoiceService

        sig = inspect.signature(VoiceService.initialize)
        assert list(sig.parameters) == ["self"]
