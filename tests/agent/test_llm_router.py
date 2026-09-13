"""LLM 路由与混合路由测试

覆盖：
- LLMRouter：调用/返回形态解析/未知动作/参数清洗/闲聊判定/统计
- HybridRouter：规则命中优先/低置信转 LLM/LLM 失败兜底/开关/统计
"""

import sys
from pathlib import Path

import pytest

project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from agent.message import AgentCommand
from agent.providers.router.hybrid_router import HybridRouter
from agent.providers.router.llm_router import (
    ALLOWED_ACTIONS,
    TOOL_SCHEMAS,
    LLMRouter,
)
from agent.providers.router.rule_router import RuleRouter
from agent.seams.router import RouterService


# ══════════════════════════════════════════════════
#  LLMRouter
# ══════════════════════════════════════════════════

class TestLLMRouterAvailability:
    """可用性"""

    def test_unavailable_without_call(self):
        """未注入 LLM 调用 → 不可用，返回 chat"""
        r = LLMRouter(llm_call=None)
        assert r.available is False
        cmd = r.route("找一下合同", {})
        assert cmd.action == "chat"
        assert cmd.confidence == 0.0

    def test_available_with_call(self):
        """注入后可用"""
        r = LLMRouter(llm_call=lambda s, u, t: {"name": "file_search"})
        assert r.available is True

    def test_empty_text(self):
        """空文本直接 chat，不调用 LLM"""
        calls = []
        r = LLMRouter(llm_call=lambda s, u, t: calls.append(1) or {"name": "file_search"})
        for text in ("", "   ", None):
            cmd = r.route(text, {})
            assert cmd.action == "chat"
        assert calls == []


class TestLLMRouterParsing:
    """返回形态解析"""

    def test_flat_shape(self):
        """{"name": ..., "arguments": {...}}"""
        r = LLMRouter(llm_call=lambda s, u, t: {
            "name": "file_search", "arguments": {"pattern": "*合同*"},
        })
        cmd = r.route("找合同", {})
        assert cmd.action == "file_search"
        assert cmd.params["pattern"] == "*合同*"
        assert cmd.confidence == 0.8

    def test_nested_function_shape(self):
        """{"function": {"name": ..., "arguments": "{...}"}}"""
        r = LLMRouter(llm_call=lambda s, u, t: {
            "function": {"name": "file_delete", "arguments": '{"pattern": "*tmp*"}'},
        })
        cmd = r.route("删临时文件", {})
        assert cmd.action == "file_delete"
        assert cmd.params["pattern"] == "*tmp*"

    def test_tool_calls_shape(self):
        """{"tool_calls": [{"function": {...}}]}"""
        r = LLMRouter(llm_call=lambda s, u, t: {
            "tool_calls": [{"function": {"name": "web_open", "arguments": {"url": "x.com"}}}],
        })
        assert r.route("打开 x.com", {}).action == "web_open"

    def test_invalid_json_arguments(self):
        """arguments 不是合法 JSON → 空参数但动作保留"""
        r = LLMRouter(llm_call=lambda s, u, t: {
            "name": "file_list", "arguments": "{not json",
        })
        cmd = r.route("列出文件", {})
        assert cmd.action == "file_list"
        assert cmd.params == {}

    def test_empty_arguments_string(self):
        """arguments 为空串 → 空参数"""
        r = LLMRouter(llm_call=lambda s, u, t: {"name": "screenshot", "arguments": "  "})
        assert r.route("截图", {}).params == {}


class TestLLMRouterValidation:
    """返回值校验"""

    def test_none_response(self):
        """LLM 返回 None → chat"""
        r = LLMRouter(llm_call=lambda s, u, t: None)
        assert r.route("随便", {}).action == "chat"
        assert r.stats()["failures"] == 1

    def test_non_dict_response(self):
        """非字典 → chat"""
        r = LLMRouter(llm_call=lambda s, u, t: "plain text")
        assert r.route("随便", {}).action == "chat"

    def test_unknown_action_rejected(self):
        """未知动作被拒绝（防 LLM 幻觉）"""
        r = LLMRouter(llm_call=lambda s, u, t: {"name": "delete_everything"})
        assert r.route("随便", {}).action == "chat"

    def test_no_name(self):
        """无 name 字段 → chat"""
        r = LLMRouter(llm_call=lambda s, u, t: {"arguments": {}})
        assert r.route("随便", {}).action == "chat"

    def test_chat_chosen_returns_chat(self):
        """LLM 主动选 chat"""
        r = LLMRouter(llm_call=lambda s, u, t: {"name": "chat"})
        cmd = r.route("讲个笑话", {})
        assert cmd.action == "chat"
        assert cmd.confidence == 0.75

    def test_exception_isolated(self):
        """调用抛异常 → chat，不向上传播"""
        def boom(s, u, t):
            raise TimeoutError("timeout")

        r = LLMRouter(llm_call=boom)
        assert r.route("找文件", {}).action == "chat"
        assert r.stats()["failures"] == 1

    def test_empty_params_cleaned(self):
        """空值参数被剔除"""
        r = LLMRouter(llm_call=lambda s, u, t: {
            "name": "file_search",
            "arguments": {"pattern": "*a*", "dirs": [], "time_range": None, "x": ""},
        })
        cmd = r.route("找 a", {})
        assert cmd.params == {"pattern": "*a*"}

    def test_permanent_flag_stripped(self):
        """file_delete 的 permanent 参数被强制剔除（硬约束）"""
        r = LLMRouter(llm_call=lambda s, u, t: {
            "name": "file_delete", "arguments": {"pattern": "*", "permanent": True},
        })
        cmd = r.route("删掉", {})
        assert "permanent" not in cmd.params

    def test_source_from_context(self):
        """source 来自上下文"""
        r = LLMRouter(llm_call=lambda s, u, t: {"name": "screenshot"})
        assert r.route("截图", {"source": "hotkey"}).source == "hotkey"


class TestLLMRouterSchema:
    """工具 schema 与自省"""

    def test_allowed_actions_match_schemas(self):
        """允许动作集合来自 schema 表"""
        assert ALLOWED_ACTIONS == {t["name"] for t in TOOL_SCHEMAS}
        assert "chat" in ALLOWED_ACTIONS

    def test_schemas_have_required_fields(self):
        """每个 schema 都有 name/description/parameters"""
        for t in TOOL_SCHEMAS:
            assert t["name"] and t["description"]
            assert isinstance(t["parameters"], dict)
            assert t["parameters"].get("type") == "object"

    def test_supported_actions(self):
        r = LLMRouter()
        actions = r.supported_actions()
        assert "file_search" in actions and "chat" in actions
        assert actions == sorted(actions)

    def test_describe_intents(self):
        r = LLMRouter()
        descs = r.describe_intents()
        assert len(descs) == len(TOOL_SCHEMAS)
        assert all(d["category"] == "llm" for d in descs)

    def test_custom_tools(self):
        """可注入自定义 schema"""
        r = LLMRouter(tools=[{"name": "chat", "description": "d",
                              "parameters": {"type": "object", "properties": {}}}])
        assert r.supported_actions() == ["chat"]

    def test_build_system_prompt(self):
        """系统提示含规则与人设"""
        r = LLMRouter(system_prompt="额外要求：优先用中文")
        prompt = r._build_system()
        assert "function call" in prompt
        assert "额外要求" in prompt

    def test_stats_and_repr(self):
        """统计与 repr"""
        r = LLMRouter(llm_call=lambda s, u, t: {"name": "chat"})
        r.route("a", {})
        assert r.stats()["calls"] == 1
        assert "available=True" in repr(r)


# ══════════════════════════════════════════════════
#  HybridRouter
# ══════════════════════════════════════════════════

class StubRouter(RouterService):
    """可编程桩路由"""

    def __init__(self, cmd: AgentCommand = None, raises: Exception = None):
        self._cmd = cmd or AgentCommand(action="chat", confidence=0.0)
        self._raises = raises
        self.calls = 0

    def route(self, text, context=None):
        self.calls += 1
        if self._raises:
            raise self._raises
        return AgentCommand(
            action=self._cmd.action,
            params=dict(self._cmd.params),
            raw_text=text or "",
            confidence=self._cmd.confidence,
        )

    def supported_actions(self):
        return ["chat", "stub"]

    def describe_intents(self):
        return [{"action": "stub", "description": "桩", "category": "test", "keywords": []}]


class TestHybridRulePriority:
    """规则优先"""

    def test_high_confidence_uses_rule(self):
        """规则置信度达标 → 不调 LLM"""
        rule = StubRouter(AgentCommand(action="file_search", confidence=0.9))
        llm = StubRouter(AgentCommand(action="web_open", confidence=0.8))
        h = HybridRouter(rule=rule, llm=llm, threshold=0.5)
        cmd = h.route("找文件", {})
        assert cmd.action == "file_search"
        assert llm.calls == 0
        assert h.stats()["rule_hits"] == 1

    def test_low_confidence_uses_llm(self):
        """规则置信度不足 → 转 LLM"""
        rule = StubRouter(AgentCommand(action="chat", confidence=0.2))
        llm = StubRouter(AgentCommand(action="file_search", confidence=0.8))
        h = HybridRouter(rule=rule, llm=llm, threshold=0.5)
        cmd = h.route("那个东西", {})
        assert cmd.action == "file_search"
        assert llm.calls == 1
        assert h.stats()["llm_hits"] == 1

    def test_threshold_boundary(self):
        """恰好等于阈值 → 视为命中"""
        rule = StubRouter(AgentCommand(action="file_search", confidence=0.5))
        llm = StubRouter(AgentCommand(action="web_open"))
        h = HybridRouter(rule=rule, llm=llm, threshold=0.5)
        assert h.route("x", {}).action == "file_search"
        assert llm.calls == 0


class TestHybridFallback:
    """降级链"""

    def test_llm_chat_falls_back_to_chat(self):
        """LLM 也判成 chat → 最终 chat"""
        rule = StubRouter(AgentCommand(action="chat", confidence=0.2))
        llm = StubRouter(AgentCommand(action="chat", confidence=0.7))
        h = HybridRouter(rule=rule, llm=llm, threshold=0.5)
        cmd = h.route("随便说点什么", {})
        assert cmd.action == "chat"
        assert h.stats()["fallback_hits"] == 1

    def test_llm_exception_falls_back(self):
        """LLM 抛异常 → chat 兜底"""
        rule = StubRouter(AgentCommand(action="chat", confidence=0.2))
        llm = StubRouter(raises=RuntimeError("boom"))
        h = HybridRouter(rule=rule, llm=llm, threshold=0.5)
        cmd = h.route("x", {})
        assert cmd.action == "chat"
        assert cmd.source == "hybrid.fallback"

    def test_rule_exception_isolated(self):
        """规则抛异常 → 不崩溃，转 LLM"""
        rule = StubRouter(raises=RuntimeError("rule boom"))
        llm = StubRouter(AgentCommand(action="screenshot", confidence=0.8))
        h = HybridRouter(rule=rule, llm=llm, threshold=0.5)
        assert h.route("截图", {}).action == "screenshot"

    def test_llm_disabled_keeps_rule_result(self):
        """LLM 关闭 → 保持规则结果（不强制转 chat）"""
        rule = StubRouter(AgentCommand(action="chat", confidence=0.2))
        llm = StubRouter(AgentCommand(action="file_search"))
        h = HybridRouter(rule=rule, llm=llm, threshold=0.5, llm_enabled=False)
        cmd = h.route("x", {})
        assert cmd.action == "chat"
        assert llm.calls == 0

    def test_llm_unavailable_provider(self):
        """LLM Provider 报告不可用 → 跳过"""
        rule = StubRouter(AgentCommand(action="chat", confidence=0.2))
        llm = LLMRouter(llm_call=None)     # available=False
        h = HybridRouter(rule=rule, llm=llm, threshold=0.5)
        assert h.route("x", {}).action == "chat"

    def test_no_llm_provider(self):
        """完全没接 LLM + 规则低置信 → **转闲聊，不执行动作**（D46 修正）。

        ⚠️ 这条原先断言的是 `file_search`（即"保持规则结果"）。
        那正是缺陷本身：规则给出 **0.1** 置信度的 `file_search`，
        在 LLM 不可用时被**当成指令执行**。真实后果是纯闲聊被派成文件操作，
        用户收到"哎呀，刚才那个「Desktop」我没太听明白呢"（实测日志）。

        判据：`chat` 是安全兜底（无副作用），文件/系统类动作**有副作用**。
        没把握时宁可当闲聊，也不能拿用户的文件赌一个猜测。
        """
        rule = StubRouter(AgentCommand(action="file_search", confidence=0.1))
        h = HybridRouter(rule=rule, llm=None, threshold=0.5)
        cmd = h.route("x", {})
        assert cmd.action == "chat", (
            f"0.1 置信度的 file_search 被执行了（{cmd.action}）—— "
            f"低置信的动作类意图必须转闲聊"
        )

    def test_no_llm_provider_keeps_high_conf_action(self):
        """反方向：高置信动作在无 LLM 时照常执行。"""
        rule = StubRouter(AgentCommand(action="file_search", confidence=0.9))
        h = HybridRouter(rule=rule, llm=None, threshold=0.5)
        assert h.route("x", {}).action == "file_search"


class TestHybridRealIntegration:
    """与真实 RuleRouter 的组合"""

    def test_real_rule_fast_path(self):
        """真实规则路由的高置信指令不触发 LLM"""
        llm_calls = []
        rule = RuleRouter()
        llm = LLMRouter(llm_call=lambda s, u, t: llm_calls.append(1) or {"name": "chat"})
        h = HybridRouter(rule=rule, llm=llm, threshold=0.5)

        cmd = h.route("找一下桌面上的合同文件", {})
        assert cmd.action == "file_search"
        assert llm_calls == [], "高置信指令不应调用 LLM"

    def test_real_rule_low_confidence_triggers_llm(self):
        """真实规则的低置信输入触发 LLM 兜底"""
        rule = RuleRouter()
        llm = LLMRouter(llm_call=lambda s, u, t: {"name": "file_read",
                                                  "arguments": {"target": "那个文件"}})
        h = HybridRouter(rule=rule, llm=llm, threshold=0.5)

        cmd = h.route("就是那个东西你懂的", {})
        assert cmd.action == "file_read"
        assert h.stats()["llm_hits"] == 1

    def test_real_rule_then_chat(self):
        """LLM 判为闲聊 → 走 chat"""
        rule = RuleRouter()
        llm = LLMRouter(llm_call=lambda s, u, t: {"name": "chat"})
        h = HybridRouter(rule=rule, llm=llm, threshold=0.5)
        assert h.route("你觉得人生的意义是什么", {}).action == "chat"


class TestHybridIntrospection:
    """自省与状态"""

    def test_supported_actions_merged(self):
        """动作集合合并两级"""
        h = HybridRouter(rule=RuleRouter(), llm=LLMRouter())
        actions = h.supported_actions()
        assert "file_search" in actions and "chat" in actions

    def test_supported_actions_without_llm(self):
        """无 LLM 时只有规则的动作"""
        h = HybridRouter(rule=RuleRouter(), llm=None)
        assert "file_search" in h.supported_actions()

    def test_describe_intents_dedup(self):
        """意图描述去重合并"""
        h = HybridRouter(rule=RuleRouter(), llm=LLMRouter())
        descs = h.describe_intents()
        actions = [d["action"] for d in descs]
        assert len(actions) == len(set(actions)), "动作不应重复"

    def test_set_llm_enabled(self):
        """运行时可开关 LLM 兜底"""
        rule = StubRouter(AgentCommand(action="chat", confidence=0.2))
        llm = StubRouter(AgentCommand(action="screenshot"))
        h = HybridRouter(rule=rule, llm=llm, threshold=0.5, llm_enabled=False)
        h.set_llm_enabled(True)
        assert h.route("x", {}).action == "screenshot"

    def test_stats_shape(self):
        """统计结构"""
        h = HybridRouter(rule=RuleRouter(), llm=None)
        stats = h.stats()
        for key in ("rule_hits", "llm_hits", "fallback_hits", "llm_ratio",
                    "threshold", "llm_enabled"):
            assert key in stats

    def test_repr(self):
        """repr 含阈值与 llm 状态"""
        h = HybridRouter(rule=RuleRouter(), llm=None)
        assert "threshold" in repr(h) and "off" in repr(h)

    def test_context_kwargs_compat(self):
        """兼容以关键字传入 has_pending_confirm"""
        h = HybridRouter(rule=RuleRouter(), llm=None)
        cmd = h.route("确定", has_pending_confirm=True)
        assert cmd.action == "confirm"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
