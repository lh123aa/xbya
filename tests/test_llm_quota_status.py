"""LLM 插件的外部依赖状态（P4-B3）

守的是**缺陷 18** 那类事故：免费档配额打满时，HTTP 429 在插件里只留一行 error
日志，对上层表现为"模型没给出结果" —— 于是**外部配额问题伪装成了"我们的代码坏了"**，
验收脚本与用户都看不出真相。

这里把"最近一次失败是什么性质"变成可断言的状态，并钉住三条语义：
1. 429 之后 `quota_blocked` 必须为 True（这才是"现在被限流了"）
2. **任何一次成功调用都复位它** —— 这个字段回答的是"现在能不能用"，
   不是"历史上错过多少次"（后者看 `quota_hits`）
3. 非 429 的错误（500 / 连接异常）**不得**被误报成配额问题
   （否则会重演同一类错误：把 A 类问题说成 B 类）

`plugins/` 不在覆盖率口径内，所以这个文件的价值不是凑覆盖率，而是**把语义钉死**。
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from plugins.llm.openrouter.plugin import UniversalLLM  # noqa: E402


def _llm() -> UniversalLLM:
    return UniversalLLM(api_key="x" * 24, model="m", base_url="http://example.invalid")


def _resp(status: int, text: str = "", content: str = "") -> MagicMock:
    r = MagicMock()
    r.status_code = status
    r.text = text
    if status == 200:
        r.json.return_value = {"choices": [{"message": {"content": content}}]}
    return r


class TestQuotaStats:
    def test_initial_state_is_clean(self):
        s = _llm().quota_stats()
        assert s["quota_blocked"] is False
        assert s["quota_hits"] == 0
        assert s["last_error"] == ""
        assert s["ok_calls"] == 0

    def test_429_sets_quota_blocked(self):
        llm = _llm()
        with patch("requests.post", return_value=_resp(429, "rate limit reached")):
            assert llm.chat("hi") is None
        s = llm.quota_stats()
        assert s["quota_blocked"] is True
        assert s["quota_hits"] == 1
        assert s["last_quota_at"] > 0
        assert "429" in s["last_error"]

    def test_success_resets_quota_blocked(self):
        """语义要点：这个字段是"现在被限流吗"，成功一次就该复位"""
        llm = _llm()
        with patch("requests.post", return_value=_resp(429, "rate limit")):
            llm.chat("hi")
        assert llm.quota_stats()["quota_blocked"] is True

        with patch("requests.post", return_value=_resp(200, content="ok")):
            assert llm.chat("hi") == "ok"
        s = llm.quota_stats()
        assert s["quota_blocked"] is False, "成功之后不该再说自己被限流"
        assert s["ok_calls"] == 1
        assert s["quota_hits"] == 1, "历史次数要保留（排查时需要）"

    def test_http_500_is_not_reported_as_quota(self):
        """非 429 的错误不能被误报成配额问题"""
        llm = _llm()
        with patch("requests.post", return_value=_resp(500, "server error")):
            llm.chat("hi")
        s = llm.quota_stats()
        assert s["quota_blocked"] is False
        assert s["quota_hits"] == 0
        assert s["last_error"] == "HTTP 500"

    def test_connection_error_is_recorded_by_type_only(self):
        """连接异常记下来，但只记类型名（消息里可能带 URL）"""
        llm = _llm()
        with patch("requests.post", side_effect=ConnectionError("http://secret-host")):
            llm.chat("hi")
        s = llm.quota_stats()
        assert s["last_error"] == "调用异常: ConnectionError"
        assert "secret-host" not in s["last_error"], "不该把异常消息里的 URL 抄进状态"
        assert s["quota_blocked"] is False

    def test_stats_never_contain_the_key(self):
        """状态面绝不能泄露密钥（它会被打进日志/状态输出）"""
        llm = _llm()
        blob = repr(llm.quota_stats()) + repr(llm.get_model_info())
        assert "x" * 24 not in blob
        assert llm.quota_stats()["fallback_configured"] is False

    def test_fallback_configured_flag(self):
        llm = UniversalLLM(api_key="k" * 24, model="m", base_url="http://a.invalid",
                           fallback_api_key="f" * 24,
                           fallback_base_url="http://b.invalid", fallback_model="fm")
        assert llm.quota_stats()["fallback_configured"] is True
