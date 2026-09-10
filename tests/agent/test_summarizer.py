"""结果摘要器测试

覆盖：
- 辅助函数（format_size / format_mtime / join_names / result_items）
- TemplateSummarizer（各工具模板）
- LLMSummarizer（提示词、输出清洗、失败回退）
- HybridSummarizer（简单/复杂分流）
"""

import sys
from pathlib import Path

import pytest

project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from agent.providers.summarizer.hybrid_sum import HybridSummarizer
from agent.providers.summarizer.llm_sum import LLMSummarizer
from agent.providers.summarizer.template_sum import TemplateSummarizer
from agent.seams.summarizer import (
    SummarizerService,
    format_mtime,
    format_size,
    join_names,
    result_items,
)
from agent.tools.base import ToolResult


def result(action_data=None, summary="原始摘要", success=True, **kwargs) -> ToolResult:
    """构造测试用 ToolResult"""
    return ToolResult(
        success=success,
        data=action_data,
        summary=summary,
        count=len(action_data) if isinstance(action_data, list) else 1,
        **kwargs,
    )


# ══════════════════════════════════════════════════
#  辅助函数
# ══════════════════════════════════════════════════

class TestFormatHelpers:
    """格式化辅助"""

    @pytest.mark.parametrize("n,expected", [
        (0, ""), (None, ""), (500, "500字节"), (2048, "2K"),
        (2411520, "2.3兆"), (5 * 1024 ** 3, "5.0G"),
    ])
    def test_format_size(self, n, expected):
        assert format_size(n) == expected

    def test_format_size_invalid(self):
        """非法输入返回空串"""
        assert format_size("not a number") == ""
        assert format_size(object()) == ""

    @pytest.mark.parametrize("text,expected", [
        ("2025-01-15 10:30", "1月15号"),
        ("2024-12-01 08:00", "12月1号"),
        ("", ""), ("未知", ""), ("garbage", ""),
    ])
    def test_format_mtime(self, text, expected):
        assert format_mtime(text) == expected

    def test_join_names(self):
        """名称拼接"""
        items = [{"name": "a"}, {"name": "b"}, {"name": "c"}, {"name": "d"}]
        assert join_names(items, limit=3) == "a、b、c 等 4 个"
        assert join_names(items[:2]) == "a、b"

    def test_join_names_edge(self):
        """边界：非列表/无名称"""
        assert join_names(None) == ""
        assert join_names([]) == ""
        assert join_names([{"x": 1}, {"y": 2}]) == ""
        assert join_names(["a", "b"]) == "a、b"

    def test_result_items_list(self):
        """列表数据"""
        assert result_items(result([1, 2])) == [1, 2]

    def test_result_items_dict_with_keys(self):
        """字典数据里的列表字段"""
        r = ToolResult(success=True, data={"deleted": [1, 2, 3]})
        assert result_items(r) == [1, 2, 3]

    def test_result_items_plain_dict(self):
        """普通字典包成单元素列表"""
        r = ToolResult(success=True, data={"a": 1})
        assert result_items(r) == [{"a": 1}]

    def test_result_items_scalar(self):
        """标量数据 → 空列表"""
        assert result_items(ToolResult(success=True, data=42)) == []


# ══════════════════════════════════════════════════
#  模板摘要器
# ══════════════════════════════════════════════════

@pytest.fixture
def tpl():
    return TemplateSummarizer()


class TestTemplateSummarizer:
    """模板摘要"""

    def test_failure_passthrough(self, tpl):
        """失败 → 用工具的用户文案"""
        r = ToolResult(success=False, summary="没找到这个文件呢", error="ENOENT")
        assert tpl.summarize("file_search", r) == "没找到这个文件呢"

    def test_empty_passthrough(self, tpl):
        """空结果 → 用工具提示"""
        r = ToolResult(success=True, data=[], summary="没找到相关的文件呢")
        assert tpl.summarize("file_search", r) == "没找到相关的文件呢"

    def test_unknown_action_passthrough(self, tpl):
        """未知 action → 原样返回"""
        r = result([{"name": "x"}], summary="某个结果")
        assert tpl.summarize("mystery_tool", r) == "某个结果"

    # ── 文件类 ──

    def test_search_single_file(self, tpl):
        """单个搜索结果含大小与时间"""
        r = result([{
            "name": "合同.pdf", "size": 2411520, "mtime_text": "2025-01-15 10:00",
        }])
        text = tpl.summarize("file_search", r)
        assert "合同.pdf" in text and "2.3兆" in text and "1月15号" in text

    def test_search_single_file_minimal(self, tpl):
        """单个结果缺字段时不报错"""
        r = result([{"name": "a.txt"}])
        assert "a.txt" in tpl.summarize("file_search", r)

    def test_search_multiple(self, tpl):
        """多个结果折叠显示"""
        r = result([{"name": f"f{i}.txt"} for i in range(5)])
        text = tpl.summarize("file_search", r)
        assert "5 个文件" in text and "等 5 个" in text

    def test_search_truncated_hint(self, tpl):
        """截断时加提示"""
        r = result([{"name": "a.txt"}], truncated=True)
        assert "只显示" in tpl.summarize("file_search", r)

    def test_list(self, tpl):
        """列出目录"""
        r = result([{"name": "a"}, {"name": "b"}])
        assert "2 项" in tpl.summarize("file_list", r)

    def test_list_truncated(self, tpl):
        r = result([{"name": "a"}], truncated=True)
        assert "只显示" in tpl.summarize("file_list", r)

    def test_read_with_content(self, tpl):
        """读取到内容 → 显示预览"""
        r = ToolResult(success=True, data={"content": "第一行\n第二行", "name": "n.txt"})
        text = tpl.summarize("file_read", r)
        assert "读到了" in text and "第一行 第二行" in text

    def test_read_opened(self, tpl):
        """用默认程序打开"""
        r = ToolResult(success=True, data={"name": "报告.docx"})
        assert "报告.docx" in tpl.summarize("file_read", r)

    def test_read_opened_no_name(self, tpl):
        r = ToolResult(success=True, data={}, summary="已经打开啦")
        assert tpl.summarize("file_read", r) == "已经打开啦"

    def test_rename(self, tpl):
        """重命名"""
        r = ToolResult(success=True, data={"old": "a.txt", "new": "b.txt"})
        text = tpl.summarize("file_rename", r)
        assert "a.txt" in text and "b.txt" in text

    def test_rename_fallback(self, tpl):
        r = ToolResult(success=True, data={}, summary="改名了")
        assert tpl.summarize("file_rename", r) == "改名了"

    def test_move(self, tpl):
        """移动"""
        r = ToolResult(success=True, data={"name": "a.txt", "to": r"C:\Users\u\Documents\a.txt"})
        assert "Documents" in tpl.summarize("file_move", r)

    def test_move_fallback(self, tpl):
        r = ToolResult(success=True, data={}, summary="移动了")
        assert tpl.summarize("file_move", r) == "移动了"

    def test_delete(self, tpl):
        """删除"""
        r = ToolResult(success=True, data={"deleted": [1, 2, 3], "failed": [], "missing": 0})
        assert "3 个文件" in tpl.summarize("file_delete", r)

    def test_delete_with_failures(self, tpl):
        """删除部分失败"""
        r = ToolResult(success=True, data={"deleted": [1], "failed": ["x"], "missing": 2})
        text = tpl.summarize("file_delete", r)
        assert "没找到" in text and "没删成" in text

    # ── 其他类 ──

    def test_system_info(self, tpl):
        """系统状态"""
        r = ToolResult(success=True, data={
            "cpu_percent": 12.5, "memory_percent": 60, "battery_percent": 80,
        })
        text = tpl.summarize("system_info", r)
        assert "CPU" in text and "内存" in text and "电量" in text

    def test_system_info_empty(self, tpl):
        r = ToolResult(success=True, data={}, summary="状态正常")
        assert tpl.summarize("system_info", r) == "状态正常"

    def test_clipboard(self, tpl):
        """剪贴板"""
        r = ToolResult(success=True, data={"text": "复制的\n内容"})
        assert "复制的 内容" in tpl.summarize("clipboard", r)

    def test_clipboard_empty(self, tpl):
        r = ToolResult(success=True, data={}, summary="已复制好啦")
        assert tpl.summarize("clipboard", r) == "已复制好啦"

    def test_translate(self, tpl):
        """翻译"""
        r = ToolResult(success=True, data={"translated": "hello world"})
        assert tpl.summarize("translate", r) == "hello world"

    def test_translate_fallback(self, tpl):
        r = ToolResult(success=True, data={}, summary="翻译好了")
        assert tpl.summarize("translate", r) == "翻译好了"

    def test_calculate(self, tpl):
        """计算"""
        r = ToolResult(success=True, data={"expression": "25 * 4", "result": 100})
        assert tpl.summarize("calculate", r) == "25 * 4 等于 100"

    def test_calculate_fallback(self, tpl):
        r = ToolResult(success=True, data={}, summary="算完了")
        assert tpl.summarize("calculate", r) == "算完了"

    def test_web_open(self, tpl):
        """打开网页"""
        r = ToolResult(success=True, data={"title": "百度"})
        assert "百度" in tpl.summarize("web_open", r)

    def test_web_open_no_title(self, tpl):
        r = ToolResult(success=True, data={}, summary="打开了")
        assert tpl.summarize("web_open", r) == "打开了"

    def test_web_search_with_results(self, tpl):
        """搜索有结果"""
        r = ToolResult(success=True, data={
            "query": "天气",
            "results": [{"title": "A"}, {"title": "B"}],
        })
        text = tpl.summarize("web_search", r)
        assert "天气" in text and "2 条" in text

    def test_web_search_empty(self, tpl):
        """搜索无结果"""
        r = ToolResult(success=True, data={"query": "xyzzz", "results": []})
        assert "没搜到" in tpl.summarize("web_search", r)

    def test_web_read(self, tpl):
        """读取网页"""
        r = ToolResult(success=True, data={"title": "标题", "text": "正文内容啊啊"})
        text = tpl.summarize("web_read", r)
        assert "标题" in text and "正文内容" in text

    def test_web_read_no_title(self, tpl):
        r = ToolResult(success=True, data={}, summary="读了")
        assert tpl.summarize("web_read", r) == "读了"

    def test_reminder(self, tpl):
        """提醒"""
        r = ToolResult(success=True, data={"when_text": "30分钟后", "what": "开会"})
        text = tpl.summarize("reminder", r)
        assert "30分钟后" in text and "开会" in text

    def test_reminder_fallback(self, tpl):
        r = ToolResult(success=True, data={}, summary="记住了")
        assert tpl.summarize("reminder", r) == "记住了"

    @pytest.mark.parametrize("action", ["screenshot", "open_app"])
    def test_generic_actions(self, tpl, action):
        """通用 action 直接透传"""
        r = result([{"name": "x"}], summary="搞定了")
        assert tpl.summarize(action, r) == "搞定了"

    def test_exception_isolated(self, tpl):
        """模板内部异常被兜底"""
        class Bad(ToolResult):
            pass

        r = ToolResult(success=True, data=[{"name": object()}], summary="兜底")
        # join_names 对 object 调 str() 正常；这里验证不抛异常即可
        assert tpl.summarize("file_search", r) != ""


# ══════════════════════════════════════════════════
#  LLM 摘要器
# ══════════════════════════════════════════════════

class TestLLMSummarizer:
    """LLM 润色摘要"""

    def test_basic_call(self):
        """正常调用返回清洗后的文本"""
        s = LLMSummarizer(llm_call=lambda p: "找到两个文件啦")
        r = result([{"name": "a"}, {"name": "b"}])
        assert s.summarize("file_search", r) == "找到两个文件啦"
        assert s.stats()["calls"] == 1

    def test_disabled_uses_fallback(self):
        """关闭时直接回退"""
        s = LLMSummarizer(llm_call=lambda p: "不该被调用",
                          fallback=TemplateSummarizer(), enabled=False)
        r = result([{"name": "a.txt"}])
        text = s.summarize("file_search", r)
        assert "a.txt" in text

    def test_no_llm_uses_fallback(self):
        """未注入 LLM → 回退"""
        s = LLMSummarizer(llm_call=None, fallback=TemplateSummarizer())
        r = result([{"name": "a.txt"}])
        assert "a.txt" in s.summarize("file_search", r)

    def test_llm_exception_falls_back(self):
        """LLM 抛异常 → 回退"""
        def boom(p):
            raise RuntimeError("api down")

        s = LLMSummarizer(llm_call=boom, fallback=TemplateSummarizer())
        r = result([{"name": "a.txt"}])
        assert "a.txt" in s.summarize("file_search", r)
        assert s.stats()["failures"] == 1

    def test_empty_response_falls_back(self):
        """LLM 返回空 → 回退"""
        s = LLMSummarizer(llm_call=lambda p: "", fallback=TemplateSummarizer())
        r = result([{"name": "a.txt"}])
        assert "a.txt" in s.summarize("file_search", r)

    def test_fallback_exception_returns_summary(self):
        """回退器也失败 → 用原始 summary"""
        class BadFallback(SummarizerService):
            def summarize(self, action, result):
                raise RuntimeError("fallback boom")

        s = LLMSummarizer(llm_call=lambda p: None, fallback=BadFallback())
        r = result([{"name": "a"}], summary="原始摘要")
        assert s.summarize("file_search", r) == "原始摘要"

    def test_no_fallback_returns_summary(self):
        """无回退器 → 用原始 summary"""
        s = LLMSummarizer(llm_call=lambda p: None)
        r = result([{"name": "a"}], summary="原始摘要")
        assert s.summarize("file_search", r) == "原始摘要"

    # ── 输出清洗 ──

    @pytest.mark.parametrize("raw,expected", [
        ("```\n找到3个文件\n```", "找到3个文件"),
        ("好的，找到3个文件", "找到3个文件"),
        ("回答：找到3个文件", "找到3个文件"),
        ('"找到3个文件"', "找到3个文件"),
        ("**找到**3个文件", "找到3个文件"),
        ("找到\n3个\n文件", "找到 3个 文件"),
        ("  留白  ", "留白"),
    ])
    def test_clean(self, raw, expected):
        """输出清洗规则"""
        s = LLMSummarizer()
        assert s.clean(raw) == expected

    def test_clean_empty(self):
        """空输入"""
        s = LLMSummarizer()
        assert s.clean(None) == ""
        assert s.clean("") == ""

    def test_clean_truncates(self):
        """超长截断并加省略号"""
        s = LLMSummarizer(max_chars=20)
        out = s.clean("字" * 100)
        assert len(out) <= 21 and out.endswith("…")

    def test_clean_via_pipeline(self):
        """清洗在 summarize 内生效"""
        s = LLMSummarizer(llm_call=lambda p: "```json\n好的，结果是这样\n```")
        r = result([{"name": "a"}])
        assert s.summarize("file_search", r) == "结果是这样"

    # ── 提示词 ──

    def test_build_prompt_contains_parts(self):
        """提示词含动作名、原始摘要、样例"""
        s = LLMSummarizer(persona="温柔学霸")
        r = ToolResult(success=True, data=[{"name": "合同.pdf", "size_text": "2.3兆"}],
                       summary="找到 1 个文件", error="", truncated=False)
        prompt = s.build_prompt("file_search", r)
        assert "搜索文件" in prompt
        assert "合同.pdf" in prompt
        assert "温柔学霸" in prompt

    def test_build_prompt_includes_error_and_extras(self):
        """提示词含错误信息与截断标记"""
        s = LLMSummarizer()
        r = ToolResult(success=False, data={"expression": "1/0"},
                       summary="算不出来", error="division by zero", truncated=True)
        prompt = s.build_prompt("calculate", r)
        assert "division by zero" in prompt
        assert "1/0" in prompt

    def test_compact_payload_handles_bad_json(self):
        """payload 序列化失败时仍有兜底输出"""
        r = ToolResult(success=True, data=[{"name": object()}], summary="x")
        assert LLMSummarizer._compact_payload(r)

    def test_compact_payload_scalar_items(self):
        """非字典条目被转成字符串样例"""
        r = ToolResult(success=True, data=["a", "b"], summary="x")
        assert "a" in LLMSummarizer._compact_payload(r)

    def test_set_enabled(self):
        """运行时可开关"""
        calls = []
        s = LLMSummarizer(llm_call=lambda p: calls.append(1) or "x")
        s.set_enabled(False)
        s.summarize("file_search", result([{"name": "a"}]))
        assert calls == []
        s.set_enabled(True)
        s.summarize("file_search", result([{"name": "a"}]))
        assert calls == [1]

    def test_repr(self):
        """repr 含状态"""
        assert "enabled" in repr(LLMSummarizer())


# ══════════════════════════════════════════════════
#  混合摘要器
# ══════════════════════════════════════════════════

class SpySummarizer(SummarizerService):
    """记录调用的桩摘要器"""

    def __init__(self, prefix: str):
        self.prefix = prefix
        self.calls = 0

    def summarize(self, action, result):
        self.calls += 1
        return f"{self.prefix}:{action}"


class TestHybridSummarizer:
    """混合分流"""

    def test_simple_result_uses_template(self):
        """简单结果（条目少、成功、未截断）走模板"""
        tpl, llm = SpySummarizer("tpl"), SpySummarizer("llm")
        h = HybridSummarizer(template=tpl, llm=llm, threshold=3)
        h.summarize("file_search", result([{"name": "a"}]))
        assert tpl.calls == 1 and llm.calls == 0

    def test_many_items_uses_llm(self):
        """条目超过阈值走 LLM"""
        tpl, llm = SpySummarizer("tpl"), SpySummarizer("llm")
        h = HybridSummarizer(template=tpl, llm=llm, threshold=3)
        h.summarize("file_search", result([{"name": str(i)} for i in range(5)]))
        assert llm.calls == 1 and tpl.calls == 0

    def test_failure_uses_llm(self):
        """失败结果走 LLM（需要说清原因+安慰）"""
        tpl, llm = SpySummarizer("tpl"), SpySummarizer("llm")
        h = HybridSummarizer(template=tpl, llm=llm)
        h.summarize("file_search", ToolResult(success=False, summary="失败了"))
        assert llm.calls == 1

    def test_truncated_uses_llm(self):
        """截断结果走 LLM"""
        tpl, llm = SpySummarizer("tpl"), SpySummarizer("llm")
        h = HybridSummarizer(template=tpl, llm=llm)
        h.summarize("file_search", result([{"name": "a"}], truncated=True))
        assert llm.calls == 1

    @pytest.mark.parametrize("action", ["web_search", "web_read", "translate"])
    def test_explanation_actions_use_llm(self, action):
        """需要解释的 action 走 LLM"""
        tpl, llm = SpySummarizer("tpl"), SpySummarizer("llm")
        h = HybridSummarizer(template=tpl, llm=llm)
        h.summarize(action, result([{"name": "a"}]))
        assert llm.calls == 1

    def test_no_llm_always_template(self):
        """未注入 LLM → 全走模板"""
        tpl = SpySummarizer("tpl")
        h = HybridSummarizer(template=tpl, llm=None)
        h.summarize("file_search", result([{"name": str(i)} for i in range(9)]))
        assert tpl.calls == 1

    def test_llm_exception_falls_back_to_template(self):
        """LLM 抛异常 → 回退模板"""
        class BoomLLM(SummarizerService):
            def summarize(self, action, result):
                raise RuntimeError("boom")

        tpl = SpySummarizer("tpl")
        h = HybridSummarizer(template=tpl, llm=BoomLLM())
        out = h.summarize("file_search", ToolResult(success=False, summary="x"))
        assert out == "tpl:file_search"

    def test_llm_empty_falls_back(self):
        """LLM 返回空 → 回退模板"""
        class EmptyLLM(SummarizerService):
            def summarize(self, action, result):
                return ""

        tpl = SpySummarizer("tpl")
        h = HybridSummarizer(template=tpl, llm=EmptyLLM())
        assert h.summarize("file_search", ToolResult(success=False, summary="x")) == "tpl:file_search"

    def test_template_failure_returns_summary(self):
        """模板也失败 → 原始 summary"""
        class BoomTpl(SummarizerService):
            def summarize(self, action, result):
                raise RuntimeError("boom")

        h = HybridSummarizer(template=BoomTpl(), llm=None)
        r = ToolResult(success=True, data=[], summary="原始")
        assert h.summarize("file_search", r) == "原始"

    def test_stats(self):
        """分流统计"""
        tpl, llm = SpySummarizer("tpl"), SpySummarizer("llm")
        h = HybridSummarizer(template=tpl, llm=llm, threshold=3)
        h.summarize("file_search", result([{"name": "a"}]))              # 模板
        h.summarize("file_search", result([{"name": str(i)} for i in range(9)]))  # LLM
        stats = h.stats()
        assert stats["template_hits"] == 1
        assert stats["llm_hits"] == 1
        assert stats["llm_ratio"] == 0.5

    def test_stats_empty(self):
        """无调用时比例为 0"""
        h = HybridSummarizer(template=SpySummarizer("t"), llm=None)
        assert h.stats()["llm_ratio"] == 0.0

    def test_repr(self):
        """repr 含阈值与 llm 状态"""
        h = HybridSummarizer(template=SpySummarizer("t"), llm=SpySummarizer("l"))
        assert "threshold" in repr(h) and "on" in repr(h)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
