# -*- coding: utf-8 -*-
"""LLM 双提供商失败必须"说出原因"（D37）。

缺陷背景：用户报告"语音互动没有反馈"。实测根因是**两层各坏一处**：
  · 主提供商 Groq：`qwen/qwen3.8-27b` 当天 200000 token 配额耗尽 → 429
  · 备用提供商 OpenRouter：`meta-llama/llama-3.1-8b-instruct:free`
    已被上游下架 → 404 "This model is unavailable for free"

于是**两个都失败**，她一个字都答不出来。而代码在这条路上几乎是静默的：
  · `_fallback_stream` 捕获所有异常后直接 `return None`，不记任何原因
  · `_call_api` 在"没有配置备用"时也直接 `return None`，不打日志
  · `_last_error` 被后一次请求**覆盖**，只留下最后一个原因，
    两个完全不同的故障（配额 vs 模型下架）在日志里无法区分

本用例钉住三件事：
  1. 两家都失败时，`_last_error` 必须**同时**包含主与备用两个原因
  2. "没有配置备用提供商"必须显式报错（这是最容易漏配的兜底缺口）
  3. 备用模型 404（下架）时，日志必须把"模型不可用"这件事说出来，
     而不是让排查者以为"网络不通"
"""

import logging

import pytest

from plugins.llm.openrouter.plugin import UniversalLLM


def _make_llm(**over):
    """构造一个不联网的实例：所有网络请求都由测试替换。"""
    params = dict(
        api_key="fake-main-key",
        base_url="https://main.example/v1",
        model="main-model",
        fallback_api_key="fake-fb-key",
        fallback_base_url="https://fb.example/v1",
        fallback_model="fb-model",
    )
    params.update(over)
    return UniversalLLM(**params)


class TestBothProvidersFail:
    def test_last_error_contains_both_reasons(self):
        """两家都挂时，_last_error 必须同时说出主与备用各自的原因。"""
        llm = _make_llm()
        calls = []

        def fake_request(messages, base_url, api_key, model):
            calls.append(base_url)
            if "main" in base_url:
                llm._last_error = "429 配额/限流"
            else:
                llm._last_error = "HTTP 404"
            return None

        llm._request_once = fake_request
        result = llm._call_api([{"role": "user", "content": "hi"}])

        assert result is None
        assert len(calls) == 2, f"应该两家都试过，实际试了 {len(calls)} 家"
        err = llm._last_chain_error
        assert "429" in err, f"丢了主提供商的原因: {err!r}"
        assert "404" in err, f"丢了备用提供商的原因: {err!r}"
        assert "均失败" in err, f"没说明是「两家都失败」: {err!r}"

    def test_fallback_still_wins_when_it_works(self):
        """反方向保护：备用成功时必须正常返回，不能被错误记账干扰。"""
        llm = _make_llm()

        def fake_request(messages, base_url, api_key, model):
            if "main" in base_url:
                llm._last_error = "429 配额/限流"
                return None
            return "备用模型回答了"

        llm._request_once = fake_request
        assert llm._call_api([{"role": "user", "content": "hi"}]) == "备用模型回答了"


class TestMissingFallback:
    def test_missing_fallback_is_reported(self, caplog):
        """没配备用提供商时必须显式报错 —— 这是最容易被漏掉的兜底缺口。"""
        llm = _make_llm(fallback_api_key="", fallback_base_url="", fallback_model="")

        def fake_request(messages, base_url, api_key, model):
            llm._last_error = "429 配额/限流"
            return None

        llm._request_once = fake_request
        with caplog.at_level(logging.ERROR):
            result = llm._call_api([{"role": "user", "content": "hi"}])

        assert result is None
        assert any("没有配置备用提供商" in r.message for r in caplog.records), (
            "未配备用时必须 ERROR 点名，否则用户只会感到「她不理我」"
        )
        assert "未配置备用" in llm._last_chain_error


class TestFallbackStreamNotSilent:
    def test_stream_fallback_failure_is_logged(self, caplog):
        """流式降级也失败时，必须留下日志与原因，不能静默 return None。"""
        llm = _make_llm()

        def fake_call_api(messages):
            llm._last_chain_error = "主(HTTP 500) + 备用(HTTP 404) 均失败"
            return None

        llm._call_api = fake_call_api
        with caplog.at_level(logging.ERROR):
            result = llm._fallback_stream([{"role": "user", "content": "hi"}])

        assert result is None
        text = " ".join(r.message for r in caplog.records)
        assert "都失败" in text, f"降级失败必须留日志: {text!r}"
        assert "404" in text, "日志里必须带上真正的原因"

    def test_stream_fallback_success_splits_sentences(self):
        """反方向：降级成功时仍要按句切分（不能被新加的记账改坏）。"""
        llm = _make_llm()
        llm._call_api = lambda messages: "第一句。第二句。"
        out = llm._fallback_stream([{"role": "user", "content": "hi"}])
        assert out, "降级成功却返回了空"
        assert len(out) >= 1


class TestEmptyReplyIsRecorded:
    """HTTP 200 但正文为空，也是一种失败，必须记账。

    这是本轮**实测踩到**的盲区：主提供商 429、备用返回 200 但内容为空，
    于是"两家分别怎么了"里备用那半显示成**"未知"** ——
    排查者会误以为备用压根没被调用，而真相是它被调用了、只是回了空。
    成因常见于 `reasoning` 型模型把正文全放进了思维链字段。
    """

    def test_empty_content_sets_last_error(self):
        llm = _make_llm()

        class _Resp:
            status_code = 200
            text = ""

            @staticmethod
            def json():
                # choices 存在但 content 为空 —— 正是"空回复"的形状
                return {"choices": [{"message": {"content": ""}}]}

        # 直接调真实实现：把 requests.post 换成返回空回复的假响应。
        # 注意 `_request_once` 内部是 `import requests`（局部名绑定到同一模块对象），
        # 所以 patch 模块属性对它是生效的。
        import requests
        real_post = requests.post
        requests.post = lambda *a, **k: _Resp()
        try:
            out = llm._request_once(
                [{"role": "user", "content": "hi"}],
                "https://main.example/v1", "k", "m",
            )
        finally:
            requests.post = real_post

        assert out is None
        assert llm._last_error, (
            "空回复没有写 _last_error —— 上层会把它显示成'未知'，"
            "让人误判成'备用没被调用'"
        )
        assert "空" in llm._last_error

    def test_empty_reply_makes_both_reasons_visible(self):
        """端到端：主 429 + 备用空回复 => 两个原因都要能看见，不许出现'未知'。"""
        llm = _make_llm()

        def fake_request(messages, base_url, api_key, model):
            if "main" in base_url:
                llm._last_error = "429 配额/限流"
            else:
                llm._last_error = "HTTP 200 但空回复"
            return None

        llm._request_once = fake_request
        assert llm._call_api([{"role": "user", "content": "hi"}]) is None
        assert "未知" not in llm._last_chain_error, (
            f"不该出现'未知'（说明某个失败分支没记账）: {llm._last_chain_error!r}"
        )
        assert "429" in llm._last_chain_error and "空回复" in llm._last_chain_error


class TestConfigFallbackModelIsReal:
    """配置里的备用模型必须是真实在架的 —— 本缺陷就是它悄悄下架造成的。"""

    def test_config_fallback_model_not_the_retired_one(self):
        import yaml
        from pathlib import Path

        cfg = Path(__file__).resolve().parent.parent / "config.yaml"
        if not cfg.exists():          # config.yaml 未跟踪，缺了不算失败
            pytest.skip("config.yaml 不存在（未跟踪文件）")
        c = yaml.safe_load(cfg.read_text(encoding="utf-8"))
        fb = (c.get("plugins", {}).get("llm", {}).get("params", {})
              .get("fallback_model", ""))
        assert fb, "没有配置 fallback_model"
        assert fb != "meta-llama/llama-3.1-8b-instruct:free", (
            "这个 :free 模型已被上游下架（404 unavailable for free），"
            "配了等于没配"
        )
