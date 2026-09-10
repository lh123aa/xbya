"""生产力工具测试

覆盖 productivity_tools.py：
- calculate：安全 AST 求值、中文数字、中文运算符、危险表达式拒绝
- translate：注入翻译函数、语言映射、失败兜底
- reminder：时长解析、到点回调、取消
- weather：注入取数函数、结果拼装
"""

import sys
import time
from pathlib import Path

import pytest

project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from agent.tools.productivity_tools import (
    MAX_EXPRESSION_LEN,
    CalculateTool,
    ReminderTool,
    TranslateTool,
    WeatherTool,
    all_productivity_tools,
    cn_to_number,
    normalize_expression,
)


# ══════════════════════════════════════════════════
#  表达式规范化
# ══════════════════════════════════════════════════

class TestNormalizeExpression:
    """口语化算式规范化"""

    @pytest.mark.parametrize("raw,expected", [
        ("1+2", "1+2"),
        ("1 加 2", "1 + 2"),
        ("10 减去 3", "10 - 3"),
        ("6 乘以 7", "6 * 7"),
        ("8 除以 2", "8 / 2"),
        ("2 的平方", "2 **2"),
        ("3^2", "3**2"),
        ("（1+2）*3", "(1+2)*3"),
        ("25×4", "25*4"),
        ("10÷2", "10/2"),
        ("1+2 等于多少", "1+2"),
        ("1+2是多少？", "1+2"),
    ])
    def test_operator_and_punctuation(self, raw, expected):
        assert normalize_expression(raw) == expected

    def test_empty(self):
        assert normalize_expression("") == ""
        assert normalize_expression(None) == ""


class TestChineseNumbers:
    """中文数字转换"""

    @pytest.mark.parametrize("raw,expected", [
        ("二十五", "25"),
        ("一百", "100"),
        ("三百五十", "350"),
        ("一千零五", "1005"),
        ("十", "10"),
        ("两", "2"),
        ("二十五加五", "25加5"),        # 只换数字，不动运算符
    ])
    def test_convert(self, raw, expected):
        assert cn_to_number(raw) == expected

    def test_non_numeric_untouched(self):
        """含非数字字符的片段不转换"""
        assert cn_to_number("abc") == "abc"
        assert cn_to_number("") == ""

    def test_ten_thousand(self):
        """万位"""
        assert cn_to_number("一万") == "10000"


class TestCalculate:
    """计算工具"""

    @pytest.fixture
    def tool(self):
        return CalculateTool()

    @pytest.mark.parametrize("expr,expected", [
        ("1+2", 3),
        ("10-3", 7),
        ("6*7", 42),
        ("8/2", 4),
        ("7//2", 3),
        ("7%3", 1),
        ("2**10", 1024),
        ("-(3+4)", -7),
        ("+5", 5),
        ("1.5+2.5", 4),
        ("10/4", 2.5),
    ])
    def test_basic_math(self, tool, expr, expected):
        r = tool.execute({"expression": expr})
        assert r.success is True
        assert r.data["result"] == expected

    def test_chinese_operators(self, tool):
        """中文运算符"""
        r = tool.execute({"expression": "二十五加五"})
        assert r.success is True
        assert r.data["result"] == 30

    def test_float_precision(self, tool):
        """浮点误差收敛"""
        r = tool.execute({"expression": "0.1+0.2"})
        assert r.data["result"] == 0.3

    def test_integer_result_no_decimal(self, tool):
        """整数结果不带小数点"""
        r = tool.execute({"expression": "4/2"})
        assert r.data["result"] == 2
        assert isinstance(r.data["result"], int)

    def test_empty_expression(self, tool):
        """空表达式"""
        r = tool.execute({"expression": ""})
        assert r.success is False
        assert "算什么" in r.summary

    def test_too_long(self, tool):
        """超长表达式"""
        expr = "1+" * (MAX_EXPRESSION_LEN // 2 + 10)   # 明确超过上限
        assert len(expr) > MAX_EXPRESSION_LEN
        r = tool.execute({"expression": expr})
        assert r.success is False
        assert "太长" in r.summary

    def test_syntax_error(self, tool):
        """语法错误"""
        r = tool.execute({"expression": "1++*2"})
        assert r.success is False

    def test_division_by_zero(self, tool):
        """除零"""
        r = tool.execute({"expression": "1/0"})
        assert r.success is False
        assert "0" in r.summary

    def test_huge_exponent_rejected(self, tool):
        """超大指数被拒绝（防卡死）"""
        r = tool.execute({"expression": "2**99999"})
        assert r.success is False

    # ── 安全：拒绝非算术输入 ──

    @pytest.mark.parametrize("expr", [
        "__import__('os').system('echo hi')",
        "open('/etc/passwd')",
        "[1,2,3]",
        "{'a': 1}",
        "(lambda: 1)()",
        "1 if True else 2",
        "print(1)",
        "a+1",
    ])
    def test_non_arithmetic_rejected(self, tool, expr):
        """非算术表达式一律拒绝（AST 白名单）"""
        r = tool.execute({"expression": expr})
        assert r.success is False, f"危险表达式未被拒绝: {expr}"

    def test_metadata(self, tool):
        assert tool.name == "calculate"
        assert tool.risk_level == "low"


# ══════════════════════════════════════════════════
#  翻译
# ══════════════════════════════════════════════════

class TestTranslate:
    """翻译工具"""

    def test_no_func(self):
        """未注入翻译能力 → 友好提示"""
        r = TranslateTool(None).execute({"text": "你好"})
        assert r.success is False
        assert "翻译能力" in r.summary

    def test_empty_text(self):
        """空文本"""
        r = TranslateTool(lambda t, l: "x").execute({"text": ""})
        assert r.success is False
        assert "翻译什么" in r.summary

    def test_basic(self):
        """正常翻译"""
        calls = []

        def fake(text, lang):
            calls.append((text, lang))
            return "hello"

        r = TranslateTool(fake).execute({"text": "你好", "target_lang": "en"})
        assert r.success is True
        assert r.data["translated"] == "hello"
        assert "hello" in r.summary
        assert calls == [("你好", "en")]

    def test_default_lang_en(self):
        """默认目标语言英文"""
        got = {}
        TranslateTool(lambda t, l: got.setdefault("lang", l) or "x").execute({"text": "a"})
        assert got["lang"] == "en"

    def test_unknown_lang_falls_back_en(self):
        """未知语言回退英文"""
        got = {}
        TranslateTool(lambda t, l: got.setdefault("lang", l) or "x").execute(
            {"text": "a", "target_lang": "klingon"})
        assert got["lang"] == "en"

    @pytest.mark.parametrize("lang", ["zh", "en", "ja", "ko", "fr", "de", "es", "ru"])
    def test_all_supported_langs(self, lang):
        """支持的语言都能用"""
        r = TranslateTool(lambda t, l: f"out-{l}").execute(
            {"text": "x", "target_lang": lang})
        assert r.success is True
        assert r.data["target_lang"] == lang

    def test_func_raises(self):
        """翻译函数异常被兜底"""
        def boom(t, l):
            raise RuntimeError("api down")

        r = TranslateTool(boom).execute({"text": "x"})
        assert r.success is False
        assert "出错" in r.summary

    def test_func_returns_none(self):
        """翻译返回空"""
        r = TranslateTool(lambda t, l: None).execute({"text": "x"})
        assert r.success is False

    def test_metadata(self):
        t = TranslateTool()
        assert t.name == "translate"
        assert t.risk_level == "low"


# ══════════════════════════════════════════════════
#  提醒
# ══════════════════════════════════════════════════

class TestReminder:
    """提醒工具"""

    def test_empty_what(self):
        """空内容"""
        r = ReminderTool().execute({"what": ""})
        assert r.success is False
        assert "提醒你什么" in r.summary

    def test_basic(self):
        """基本设置"""
        r = ReminderTool().execute({"what": "开会", "minutes": 30})
        assert r.success is True
        assert r.data["what"] == "开会"
        assert r.data["minutes"] == 30
        assert "30分钟后" in r.data["when_text"]
        assert "开会" in r.summary

    def test_default_minutes(self):
        """未给分钟数用默认值"""
        r = ReminderTool(default_minutes=15).execute({"what": "喝水"})
        assert r.data["minutes"] == 15

    def test_invalid_minutes_uses_default(self):
        """非法分钟数回退默认"""
        r = ReminderTool().execute({"what": "x", "minutes": "abc"})
        assert r.success is True
        assert r.data["minutes"] == ReminderTool.DEFAULT_MINUTES

    def test_negative_minutes(self):
        """负数被拒绝"""
        r = ReminderTool().execute({"what": "x", "minutes": -5})
        assert r.success is False
        assert "正数" in r.summary

    @pytest.mark.parametrize("minutes,expected", [
        (0.5, "马上"),
        (5, "5分钟后"),
        (60, "1小时后"),
        (120, "2小时后"),
        (90, "1.5小时后"),
    ])
    def test_when_format(self, minutes, expected):
        """时间描述格式"""
        assert ReminderTool._format_when(minutes) == expected

    def test_pending_and_cancel(self):
        """待办列表与取消"""
        tool = ReminderTool()
        r = tool.execute({"what": "开会", "minutes": 30})
        rid = r.data["id"]
        assert len(tool.pending()) == 1

        assert tool.cancel(rid) is True
        assert tool.pending() == []
        assert tool.cancel(rid) is False

    def test_due_now_fires(self):
        """到点触发回调"""
        fired = []
        tool = ReminderTool(on_due=lambda rid, what: fired.append((rid, what)))
        r = tool.execute({"what": "开会", "minutes": 30})

        # 手动把到期时间提前
        tool._items[r.data["id"]].due_at = time.time() - 1
        result = tool.due_now()

        assert len(result) == 1
        assert fired == [(r.data["id"], "开会")]
        assert tool.pending() == []

    def test_due_now_no_callback(self):
        """无回调也不报错"""
        tool = ReminderTool()
        r = tool.execute({"what": "x", "minutes": 1})
        tool._items[r.data["id"]].due_at = time.time() - 1
        assert len(tool.due_now()) == 1

    def test_due_now_callback_exception(self):
        """回调异常被隔离"""
        def boom(rid, what):
            raise RuntimeError("boom")

        tool = ReminderTool(on_due=boom)
        r = tool.execute({"what": "x", "minutes": 1})
        tool._items[r.data["id"]].due_at = time.time() - 1
        assert len(tool.due_now()) == 1      # 不抛异常

    def test_sequential_ids(self):
        """ID 递增"""
        tool = ReminderTool()
        a = tool.execute({"what": "a", "minutes": 1}).data["id"]
        b = tool.execute({"what": "b", "minutes": 1}).data["id"]
        assert a != b and a.endswith("1") and b.endswith("2")

    def test_metadata(self):
        assert ReminderTool().name == "reminder"


# ══════════════════════════════════════════════════
#  天气
# ══════════════════════════════════════════════════

class TestWeather:
    """天气工具"""

    def test_no_func(self):
        """未注入取数能力"""
        r = WeatherTool(None).execute({})
        assert r.success is False
        assert "天气服务" in r.summary

    def test_basic(self):
        """正常查询"""
        r = WeatherTool(lambda c: {
            "city": "北京", "desc": "晴", "temp": 22,
        }).execute({"city": "北京"})
        assert r.success is True
        assert "北京" in r.summary and "晴" in r.summary and "22" in r.summary

    def test_temp_range(self):
        """温度区间也展示"""
        r = WeatherTool(lambda c: {
            "city": "上海", "temp_range": "18~25度",
        }).execute({})
        assert "18~25度" in r.summary

    def test_empty_result(self):
        """取数返回空"""
        r = WeatherTool(lambda c: None).execute({})
        assert r.success is False
        assert "没查到" in r.summary

    def test_func_raises(self):
        """取数异常被兜底"""
        def boom(c):
            raise RuntimeError("network")

        r = WeatherTool(boom).execute({})
        assert r.success is False
        assert "查不到" in r.summary

    def test_minimal_data(self):
        """最少字段也能出摘要"""
        r = WeatherTool(lambda c: {"desc": "多云"}).execute({})
        assert r.success is True
        assert "多云" in r.summary

    def test_metadata(self):
        assert WeatherTool().name == "weather"


# ══════════════════════════════════════════════════
#  注册辅助
# ══════════════════════════════════════════════════

class TestRegistryHelper:
    """all_productivity_tools"""

    def test_returns_four_tools(self):
        tools = all_productivity_tools()
        assert len(tools) == 4
        assert {t.name for t in tools} == {"calculate", "translate", "reminder", "weather"}

    def test_injections_passed(self):
        """注入的能力被传递到对应工具"""
        tools = all_productivity_tools(
            translate_func=lambda t, l: "x",
            weather_func=lambda c: {"desc": "晴"},
            on_reminder_due=lambda r, w: None,
        )
        by_name = {t.name: t for t in tools}
        assert by_name["translate"]._translate is not None
        assert by_name["weather"]._weather is not None
        assert by_name["reminder"]._on_due is not None

    def test_all_low_risk(self):
        """生产力工具均为低风险"""
        assert all(t.risk_level == "low" for t in all_productivity_tools())


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
