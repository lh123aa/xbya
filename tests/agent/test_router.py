"""规则路由测试

核心验收：50 条指令集准确率 >90%，延迟 P95 <10ms
"""

import sys
import time
from pathlib import Path

import pytest

project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from agent.message import AgentCommand
from agent.providers.router.rule_router import (
    CANCEL_PHRASES,
    CONFIRM_PHRASES,
    DEFAULT_INTENTS,
    DIR_ALIASES,
    EXT_ALIASES,
    RuleRouter,
)
from tests.agent.router_testset import ROUTER_TESTSET, group_by_action


@pytest.fixture
def router():
    return RuleRouter()


# ══════════════════════════════════════════════════════
#  准确率验收（核心指标）
# ══════════════════════════════════════════════════════

class TestRouterAccuracy:
    """50 条指令集准确率"""

    def test_action_accuracy_above_90_percent(self, router):
        """action 识别准确率 >90%"""
        correct = 0
        failures = []

        for text, expected_action, _ in ROUTER_TESTSET:
            cmd = router.route(text, {})
            if cmd.action == expected_action:
                correct += 1
            else:
                failures.append(f"{text!r} 期望 {expected_action} 实际 {cmd.action}")

        accuracy = correct / len(ROUTER_TESTSET)
        assert accuracy > 0.90, (
            f"准确率 {accuracy:.1%}（{correct}/{len(ROUTER_TESTSET)}）\n"
            + "\n".join(failures)
        )

    def test_param_extraction_on_correct_routes(self, router):
        """正确路由的用例中，参数提取也应正确"""
        checked = 0
        failures = []

        for text, expected_action, expected_params in ROUTER_TESTSET:
            cmd = router.route(text, {})
            if cmd.action != expected_action or not expected_params:
                continue
            checked += 1
            for key, expected in expected_params.items():
                actual = cmd.params.get(key)
                if actual != expected:
                    failures.append(
                        f"{text!r} 参数 {key}: 期望 {expected!r} 实际 {actual!r}"
                    )

        assert not failures, (
            f"参数提取失败 {len(failures)} 处（共检查 {checked} 条）:\n"
            + "\n".join(failures)
        )

    def test_testset_size(self):
        """测试集规模只增不减（P0 收尾 64 条，P1 回归后 73 条）"""
        assert len(ROUTER_TESTSET) >= 73, f"测试集被削到 {len(ROUTER_TESTSET)} 条了"

    def test_testset_covers_all_actions(self):
        """测试集覆盖全部意图"""
        counts = group_by_action()
        for action in ("file_search", "file_list", "file_read",
                       "file_rename", "file_move", "file_delete",
                       "open_app", "screenshot", "run_command",
                       "reminder", "weather", "web_open", "web_search", "web_read",
                       "calculate", "translate", "system_info", "clipboard", "chat"):
            assert counts.get(action, 0) > 0, f"缺少 {action} 的用例"


class TestRouterLatency:
    """延迟验收"""

    def test_p95_latency_below_10ms(self, router):
        """P95 延迟 <10ms"""
        # 预热
        for text, _, _ in ROUTER_TESTSET[:5]:
            router.route(text, {})

        times = []
        for text, _, _ in ROUTER_TESTSET:
            t0 = time.perf_counter()
            router.route(text, {})
            times.append((time.perf_counter() - t0) * 1000)

        times.sort()
        p95 = times[int(len(times) * 0.95)]
        avg = sum(times) / len(times)
        assert p95 < 10.0, f"P95 延迟 {p95:.2f}ms（平均 {avg:.2f}ms）"

    def test_average_latency_below_5ms(self, router):
        """平均延迟 <5ms"""
        router.route("预热", {})
        times = []
        for text, _, _ in ROUTER_TESTSET:
            t0 = time.perf_counter()
            router.route(text, {})
            times.append((time.perf_counter() - t0) * 1000)
        avg = sum(times) / len(times)
        assert avg < 5.0, f"平均延迟 {avg:.2f}ms"


# ══════════════════════════════════════════════════════
#  边界与特殊场景
# ══════════════════════════════════════════════════════

class TestRouterEdgeCases:
    """边界条件"""

    def test_empty_text(self, router):
        """空文本 → chat，置信度 0"""
        for empty in ("", "   ", None):
            cmd = router.route(empty, {})
            assert cmd.action == "chat"
            assert cmd.confidence == 0.0

    def test_whitespace_normalized(self, router):
        """多余空白被归一化"""
        cmd = router.route("找  一下   桌面上的   合同", {})
        assert cmd.action == "file_search"

    def test_case_insensitive_extension(self, router):
        """扩展名大小写不敏感"""
        for text in ("找一下PDF文件", "找一下pdf文件", "找一下 Pdf 文件"):
            cmd = router.route(text, {})
            assert cmd.params.get("pattern") == "*.pdf", f"失败: {text}"

    def test_longest_alias_wins_for_time(self, router):
        """时间词长匹配优先（前两天 不应被 今天 影响）"""
        cmd = router.route("找一下前两天的工作报告", {})
        assert cmd.params.get("time_range") == "last_2_days"

    def test_unsupported_hints_go_to_chat(self, router):
        """未实现能力的关键词转闲聊（网页/天气已支持，不再计入本表）"""
        for text in ("帮我发个邮件", "帮我看看股票", "放首歌听听",
                     "帮我把电脑关机", "微信发个消息"):
            cmd = router.route(text, {})
            assert cmd.action == "chat", f"未转闲聊: {text}"

    def test_supported_web_and_weather_now_route(self, router):
        """P1 起网页与天气已支持，应路由到对应工具而非闲聊"""
        assert router.route("打开百度", {}).action == "web_open"
        assert router.route("北京天气怎么样", {}).action == "weather"

    def test_url_prefers_read_when_only_reading(self, router):
        """含 URL 且动词是"读/看"时走 web_read"""
        cmd = router.route("读一下 https://example.com 的内容", {})
        assert cmd.action == "web_read"
        assert cmd.params.get("url") == "https://example.com"

    def test_url_prefers_open_when_opening(self, router):
        """含 URL 且动词是"打开/访问"时走 web_open"""
        cmd = router.route("打开 https://github.com", {})
        assert cmd.action == "web_open"

    def test_docx_path_not_treated_as_url(self, router):
        """普通文件名不会被误判为网址"""
        cmd = router.route("打开报告.docx", {})
        assert cmd.action == "file_read"

    def test_confidence_range(self, router):
        """置信度始终在 [0, 1] 区间"""
        for text, _, _ in ROUTER_TESTSET:
            cmd = router.route(text, {})
            assert 0.0 <= cmd.confidence <= 1.0, f"越界: {text} → {cmd.confidence}"

    def test_chat_confidence_low(self, router):
        """闲聊兜底置信度低"""
        cmd = router.route("你好呀", {})
        assert cmd.confidence < 0.5

    def test_matched_intent_confidence_high(self, router):
        """明确指令置信度高"""
        cmd = router.route("帮我找一下桌面上的合同文件", {})
        assert cmd.confidence >= 0.5
        assert cmd.confidence >= 0.9, f"置信度偏低: {cmd.confidence}"

    def test_raw_text_preserved(self, router):
        """原始文本被完整保留"""
        text = "帮我找一下桌面上的合同文件"
        assert router.route(text, {}).raw_text == text

    def test_request_id_unique(self, router):
        """每次路由产生独立 request_id"""
        a = router.route("找一下文件", {})
        b = router.route("找一下文件", {})
        assert a.request_id != b.request_id

    def test_source_from_context(self, router):
        """source 可从上下文传入"""
        cmd = router.route("找一下文件", {"source": "hotkey"})
        assert cmd.source == "hotkey"


class TestConfirmCancelRouting:
    """确认/取消路由（仅在有待确认项时生效）"""

    def test_no_pending_means_no_confirm_action(self, router):
        """无待确认项时，确认短语不路由为 confirm"""
        cmd = router.route("好的", {})
        assert cmd.action != "confirm"

    def test_confirm_with_pending(self, router):
        """有待确认项时，确认短语路由为 confirm"""
        for phrase in ("确定", "好的", "可以", "嗯", "继续", "删吧"):
            cmd = router.route(phrase, {"has_pending_confirm": True})
            assert cmd.action == "confirm", f"失败: {phrase}"

    def test_cancel_with_pending(self, router):
        """有待确认项时，取消短语路由为 cancel"""
        for phrase in ("算了", "不用了", "取消", "别删", "不做了"):
            cmd = router.route(phrase, {"has_pending_confirm": True})
            assert cmd.action == "cancel", f"失败: {phrase}"

    def test_cancel_takes_priority_over_confirm(self, router):
        """取消优先于确认（"不用了" 不应被 "好" 干扰）"""
        cmd = router.route("不用了", {"has_pending_confirm": True})
        assert cmd.action == "cancel"

    def test_confirm_high_confidence(self, router):
        """确认/取消置信度高"""
        cmd = router.route("确定", {"has_pending_confirm": True})
        assert cmd.confidence >= 0.9

    def test_unrelated_speech_with_pending_is_not_confirm(self, router):
        """有待确认项时，无关语音不误判为确认"""
        cmd = router.route("帮我找一下合同", {"has_pending_confirm": True})
        assert cmd.action == "file_search"


class TestRouterIntrospection:
    """路由自省接口"""

    def test_supported_actions(self, router):
        """支持的 action 列表"""
        actions = router.supported_actions()
        assert "file_search" in actions
        assert "confirm" in actions
        assert "chat" in actions

    def test_describe_intents(self, router):
        """意图描述可导出（供 LLM 复用）"""
        descs = router.describe_intents()
        assert len(descs) == len(DEFAULT_INTENTS)
        assert all("action" in d and "description" in d for d in descs)

    def test_describe_intents_has_keywords(self, router):
        """意图描述含关键词（LLM 可参考）"""
        for d in router.describe_intents():
            assert isinstance(d["keywords"], list)


class TestRouterDeterminism:
    """确定性（纯函数）"""

    def test_same_input_same_action(self, router):
        """同样输入产生同样 action 与 params"""
        text = "把报告改成年终总结"
        results = [router.route(text, {}) for _ in range(5)]
        actions = {r.action for r in results}
        params = {str(sorted(r.params.items())) for r in results}
        assert len(actions) == 1
        assert len(params) == 1

    def test_stateless_across_calls(self, router):
        """路由无隐藏状态（调用顺序不影响结果）"""
        text = "找一下桌面上的合同"
        first = router.route(text, {})
        router.route("删掉这个文件", {})
        router.route("你好", {})
        again = router.route(text, {})
        assert first.action == again.action
        assert first.params == again.params


class TestWordTables:
    """词表完整性"""

    def test_dir_aliases_complete(self):
        """目录别名覆盖四个白名单目录"""
        values = set(DIR_ALIASES.values())
        assert values == {"Desktop", "Documents", "Downloads", "Pictures"}

    def test_ext_aliases_mapped_to_glob(self):
        """扩展名别名全部映射为 glob 模式"""
        for alias, pattern in EXT_ALIASES.items():
            assert pattern.startswith("*"), f"{alias} → {pattern} 不是 glob"

    def test_confirm_cancel_no_overlap(self):
        """确认与取消短语无重叠（避免歧义）"""
        overlaps = set(CONFIRM_PHRASES) & set(CANCEL_PHRASES)
        assert not overlaps, f"重叠短语: {overlaps}"


class TestTraditionalChineseRouting:
    """ASR 输出繁体时也要路由正确（回归钉子）

    这是语音回环抓出来的真实缺陷：faster_whisper 把「删除桌面上的截图」
    转写成**混着繁体**的「删除桌面上的截圖。」，于是意图匹配上了、
    文件类型关键词**静默丢失**，而置信度仍是 0.95 没被阈值拦下 ——
    后台便把"上一次搜索到的文件"当成了删除对象。

    修法是把繁简归一化接在 `RuleRouter._normalize()` 这个单点入口上
    （见 `agent/text_norm.py`），所有词表查表一次性受益。
    """

    @pytest.mark.parametrize("text,action", [
        ("删除桌面上的截圖。", "file_delete"),
        ("刪除桌面上的截圖。", "file_delete"),
        ("找一下桌面上的PDF文件。", "file_search"),
        ("打開桌面上的圖片", "file_read"),
    ])
    def test_traditional_input_routes(self, router, text, action):
        cmd = router.route(text, {})
        assert cmd.action == action, f"{text!r} → {cmd.action}"

    def test_traditional_file_type_still_yields_pattern(self, router):
        """核心断言：繁体文件类型必须产出 pattern

        没有 pattern 时，管线就会用实体栈兜底补目标 —— 也就是"删错文件"的入口。
        """
        cmd = router.route("删除桌面上的截圖。", {})
        assert cmd.params.get("pattern") == "*截图*", cmd.params
        assert cmd.params.get("dirs") == ["Desktop"]

    def test_raw_text_keeps_user_wording(self, router):
        """归一化只作用于匹配文本：raw_text 必须是用户原话（播报要用）"""
        cmd = router.route("删除桌面上的截圖。", {})
        assert cmd.raw_text == "删除桌面上的截圖。"

    def test_simplified_behaviour_is_unchanged(self, router):
        """简体输入的结果必须与加归一化之前**完全一致**"""
        a = router.route("删除桌面上的截图。", {})
        b = router.route("删除桌面上的截圖。", {})
        assert a.action == b.action
        assert a.params == b.params
        assert a.confidence == b.confidence


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
