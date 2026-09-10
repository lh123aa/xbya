"""F7 —— 生产力能力的**真实后端**测试

被测对象（4 个新模块 + 1 个改动的插件）：

| 模块 | 关注点 |
|------|--------|
| `agent/providers/productivity/llm_translate.py` | LLM 输出清洗 + 失败一律 None |
| `agent/providers/productivity/open_meteo.py` | Open-Meteo 两段请求 + 全链路失败兜底 |
| `agent/providers/productivity/reminder_scheduler.py` | 调度线程生命周期 + 到点派发 |
| `agent/plugins/productivity_providers_plugin.py` | translate / weather 服务的默认提供者 |
| `agent/plugins/productivity_tools_plugin.py` | 4 个工具 + 提醒调度器的生命周期归属 |

三条硬约定（本文件的测试都围着它们转）：

1. **离线**：`requests.get` 全部打桩、LLM 全部用桩，不发一个真请求。
2. **不抛异常**：查不到天气 / 翻译失败 / 回调炸了 → 一律返回 None 或 0，
   绝不让用户的语音请求失败。每条兜底分支都有对应用例。
3. **不留线程**：调度器用例要么不 `start()`、要么 `finally` 里 `stop()`；
   最后一个用例专门钉住"disposer 必须把线程停掉"（债务 D8 的同类问题）。
"""

from __future__ import annotations

import logging
import sys
import textwrap
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from agent.plugins import (
    SVC_BUS,
    SVC_CONFIG,
    SVC_LLM_ONCE,
    SVC_REGISTRY,
    SVC_REMINDER,
    SVC_REMINDER_SCHEDULER,
    SVC_TRANSLATE,
    SVC_WEATHER,
)
from agent.plugins.productivity_providers_plugin import setup as providers_setup
from agent.plugins.productivity_tools_plugin import setup as tools_setup
from agent.providers.productivity import open_meteo as om
from agent.providers.productivity.llm_translate import (
    LANG_NAMES,
    clean_translation,
    make_llm_translate,
)
from agent.providers.productivity.open_meteo import (
    WMO_CODES,
    describe_code,
    make_open_meteo_weather,
)
from agent.providers.productivity.reminder_scheduler import (
    DEFAULT_TICK,
    ReminderScheduler,
)
from agent.tools.productivity_tools import Reminder, ReminderTool
from core.kernel.context import Context


# ══════════════════════════════════════════════════
#  1. llm_translate —— 输出清洗
# ══════════════════════════════════════════════════

class TestCleanTranslation:
    """清洗模型输出：剥前缀标签 + 剥首尾成对引号"""

    @pytest.mark.parametrize("raw,expected", [
        # ── 带冒号的标签（短标签 / 长标签 / 英文 / 大小写混写）──
        ("译文：Hello", "Hello"),
        ("译文: Hello", "Hello"),
        ("以下是翻译：今天天气很好", "今天天气很好"),
        ("以下是翻译:今天天气很好", "今天天气很好"),
        ("Translation: Hello world", "Hello world"),
        ("TRANSLATION: Hello", "Hello"),
        ("Here is the translation: Hello", "Hello"),
        ("Translated: Hello", "Hello"),
        ("  译文：  空格也要清掉  ", "空格也要清掉"),
        # ── 无冒号但独占一行的标签（实测漏网的那一类）──
        ("以下是译文\nHello", "Hello"),
        ("以下是译文\r\nHello", "Hello"),
        ("译文\n\nHello", "Hello"),
        ("Translation\nHello", "Hello"),
        # ── 叠标签要**反复剥**，不能只剥最外层 ──
        # 原实现只剥一次（`re.sub` 的 `^` 只锚整串开头），于是
        # "译文：以下是翻译：\nHello" 会把第二个标签一起念给用户听 ——
        # 而这正是真实 LLM 返回过的那类前缀。现已改为循环剥到收敛。
        ("译文：以下是翻译：\nHello", "Hello"),
        ("译文：以下是翻译：Hello", "Hello"),
        # ── 成对引号（4 种配对）──
        ('"Hello"', "Hello"),
        ("'Hello'", "Hello"),
        ("“Hello”", "Hello"),
        ("「Hello」", "Hello"),
        # ── 标签与引号叠在一起（两轮清洗都要生效）──
        ('译文："Hello"', "Hello"),
    ])
    def test_strips_labels_and_paired_quotes(self, raw, expected):
        assert clean_translation(raw) == expected

    @pytest.mark.parametrize("raw", [
        "Hello",                     # 本来就干净
        "没有冒号的普通句子",
        "翻译一段中文",               # 有"翻译"但既没冒号也没换行 → 不是标签，不许吃掉正文
        "Translation is hard",       # 有 translation 但没有冒号 → 同上
        "Hello: world",              # 冒号在中间，行首不是标签格式
        '"未配对',                    # 只有左引号
        "未配对'",                    # 只有右引号
        '“方向反了"',                 # 左右引号不成对
        "「未闭合",                   # 只有左书名号
        "a",                         # 长度 1，不足以剥引号
    ])
    def test_keeps_text_that_is_not_decoration(self, raw):
        """不是装饰的内容一律原样保留（宁可多留，不可误删正文）"""
        assert clean_translation(raw) == raw

    @pytest.mark.parametrize("raw", ['"Hello"', "'Hello'", "“Hello”", "「Hello」"])
    def test_each_quote_pair_is_stripped(self, raw):
        """四种配对都能剥（循环里四对各自有效）"""
        assert clean_translation(raw) == "Hello"

    def test_unpaired_quotes_fall_through_all_pairs(self):
        """首尾引号不成对 → 四对全试完仍原样返回（宁可多留引号）"""
        assert clean_translation("“Hello'") == "“Hello'"
        assert clean_translation('「Hello"') == '「Hello"'
        assert clean_translation("“”'") == "“”'"

    def test_empty_and_none(self):
        assert clean_translation("") == ""
        assert clean_translation("   ") == ""
        assert clean_translation(None) == ""

    def test_label_only_yields_empty(self):
        """整条输出只有标签 → 清洗后为空（调用方据此判定"没有可用译文"）"""
        assert clean_translation("Translation:") == ""
        assert clean_translation("译文：") == ""

    def test_label_line_without_body_leaves_the_label(self):
        """只有"无冒号标签行"、正文为空 → 标签本身留下了

        这是 `_LABEL_LINE_RE` 的形状决定的：它要求标签行**后面还有换行**，
        所以 `"以下是译文\n"` 剥不掉。这里把现状钉住（调用方会把这种输出
        当成有效译文念出来，属于可报告的瑕疵，见测试报告"发现但未修的问题"）。
        """
        assert clean_translation("以下是译文\n") == "以下是译文"


# ══════════════════════════════════════════════════
#  2. llm_translate —— 翻译函数工厂
# ══════════════════════════════════════════════════

class TestMakeLLMTranslate:
    """`(text, target_lang) -> Optional[str]`，失败一律 None"""

    def test_without_llm_returns_none(self):
        """没有 LLM 时返回 None（工具据此给出"还没接上翻译能力"）"""
        assert make_llm_translate(None) is None

    def test_translates_and_cleans(self):
        seen = []

        def llm_once(prompt):
            seen.append(prompt)
            return "译文：Hello"

        translate = make_llm_translate(llm_once)
        assert translate("你好", "en") == "Hello"

        prompt = seen[0]
        assert "英文" in prompt, "提示词里要用语言名（英文）而不是代码（en）"
        assert "你好" in prompt, "原文必须进提示词"

    @pytest.mark.parametrize("lang,expected_name", [
        ("zh", "中文"), ("en", "英文"), ("ja", "日文"), ("ko", "韩文"),
        ("fr", "法文"), ("de", "德文"), ("es", "西班牙文"), ("ru", "俄文"),
    ])
    def test_all_declared_languages(self, lang, expected_name):
        seen = []
        translate = make_llm_translate(lambda p: seen.append(p) or "ok")
        assert translate("text", lang) == "ok"
        assert expected_name in seen[0]

    def test_language_code_is_lowercased(self):
        seen = []
        translate = make_llm_translate(lambda p: seen.append(p) or "ok")
        assert translate("text", "ZH") == "ok"
        assert "中文" in seen[0]

    @pytest.mark.parametrize("lang", ["xx", "克林贡语", ""])
    def test_unknown_language_falls_back_to_english(self, lang):
        seen = []
        translate = make_llm_translate(lambda p: seen.append(p) or "ok")
        assert translate("text", lang) == "ok"
        assert "英文" in seen[0]

    def test_none_language_falls_back_to_english(self):
        seen = []
        translate = make_llm_translate(lambda p: seen.append(p) or "ok")
        assert translate("text", None) == "ok"
        assert "英文" in seen[0]

    def test_llm_exception_is_swallowed(self, caplog):
        """LLM 抛异常 → None，绝不向上抛（翻译是增强能力）"""
        def boom(prompt):
            raise RuntimeError("额度用完了")

        translate = make_llm_translate(boom)
        with caplog.at_level(logging.WARNING):
            assert translate("你好", "en") is None
        assert any("LLM 调用失败" in r.message for r in caplog.records)

    @pytest.mark.parametrize("raw", [None, "", "   ", "\n", "译文：", "Translation:"])
    def test_empty_llm_output_is_none(self, raw, caplog):
        """空/空白/只有标签的输出 → None（不把空字符串念给用户听）"""
        translate = make_llm_translate(lambda p: raw)
        with caplog.at_level(logging.WARNING):
            assert translate("你好", "en") is None
        assert any("未返回可用译文" in r.message for r in caplog.records)


# ══════════════════════════════════════════════════
#  3. open_meteo —— WMO 码
# ══════════════════════════════════════════════════

class TestDescribeCode:
    """WMO 代码 → 中文描述"""

    @pytest.mark.parametrize("code,expected", [
        (0, "晴"), (1, "晴间多云"), (2, "多云"), (3, "阴"),
        (45, "有雾"), (48, "冻雾"),
        (51, "小毛毛雨"), (53, "毛毛雨"), (55, "大毛毛雨"),
        (56, "冻毛毛雨"), (57, "强冻毛毛雨"),
        (61, "小雨"), (63, "中雨"), (65, "大雨"),
        (66, "冻雨"), (67, "强冻雨"),
        (71, "小雪"), (73, "中雪"), (75, "大雪"), (77, "米雪"),
        (80, "小阵雨"), (81, "阵雨"), (82, "强阵雨"),
        (85, "小阵雪"), (86, "大阵雪"),
        (95, "雷阵雨"), (96, "雷阵雨伴小冰雹"), (99, "雷阵雨伴大冰雹"),
    ])
    def test_known_codes(self, code, expected):
        assert describe_code(code) == expected

    def test_table_covers_every_declared_code(self):
        """表里每个码都能查到（防止手抄漏项）"""
        assert all(describe_code(k) == v for k, v in WMO_CODES.items())

    def test_unknown_numeric_code(self):
        assert describe_code(1234) == "未知天气"
        assert describe_code(-1) == "未知天气"

    @pytest.mark.parametrize("code", [None, "abc", "", "晴", [], {}])
    def test_non_numeric_code(self, code):
        """非数字如实说"未知天气"，而不是猜一个"""
        assert describe_code(code) == "未知天气"

    def test_numeric_string_is_accepted(self):
        """字符串数字能转换（JSON 里偶尔是字符串）"""
        assert describe_code("61") == "小雨"


# ══════════════════════════════════════════════════
#  4. open_meteo —— 两段请求与失败兜底
# ══════════════════════════════════════════════════

def _geo(lat=31.22, lon=121.46, name="上海"):
    """地理编码响应：`[{"latitude", "longitude", "name"}]`"""
    return {"results": [{"latitude": lat, "longitude": lon, "name": name}]}


def _forecast(temp=23.4, code=2, highs=("26.6",), lows=("18.2",)):
    """预报响应：current + daily 两段"""
    daily = {}
    if highs is not None:
        daily["temperature_2m_max"] = list(highs)
    if lows is not None:
        daily["temperature_2m_min"] = list(lows)
    return {"current": {"temperature_2m": temp, "weather_code": code}, "daily": daily}


def _resp(payload, status=200):
    """假 requests 响应对象"""
    return SimpleNamespace(status_code=status, json=lambda: payload)


class TestMakeOpenMeteoWeather:
    """天气取数：任何失败都返回 None，且绝不抛异常"""

    def test_happy_path_two_requests(self):
        """正常情况：地理编码 → 预报，拼出契约里的 4 个字段"""
        with mock.patch("requests.get", side_effect=[_resp(_geo()), _resp(_forecast())]) as g:
            data = make_open_meteo_weather()("上海")

        assert data == {
            "city": "上海", "desc": "多云", "temp": 23, "temp_range": "18~27度",
        }
        assert g.call_count == 2

        # 第一段：城市名 → 经纬度（只把城市名发出去，不发 IP）
        first = g.call_args_list[0]
        assert first.args[0] == om._GEOCODE_URL
        assert first.kwargs["params"]["name"] == "上海"
        assert first.kwargs["params"]["count"] == 1
        assert first.kwargs["params"]["language"] == "zh"
        assert first.kwargs["timeout"] == om._TIMEOUT

        # 第二段：经纬度 → 当前天气 + 当日区间
        second = g.call_args_list[1]
        assert second.args[0] == om._FORECAST_URL
        params = second.kwargs["params"]
        assert params["latitude"] == 31.22
        assert params["longitude"] == 121.46
        assert params["timezone"] == "auto"
        assert params["forecast_days"] == 1
        assert "temperature_2m" in params["current"]
        assert "temperature_2m_max" in params["daily"]
        assert second.kwargs["timeout"] == om._TIMEOUT

    @pytest.mark.parametrize("raw,expected", [
        (23.4, 23), (23.6, 24), (-3.5, -4), (0.0, 0), ("18.2", 18),
    ])
    def test_temp_is_rounded_to_int(self, raw, expected):
        with mock.patch("requests.get", side_effect=[
            _resp(_geo()), _resp(_forecast(temp=raw)),
        ]):
            data = make_open_meteo_weather()("上海")
        assert data["temp"] == expected
        assert isinstance(data["temp"], int)

    def test_city_whitespace_is_stripped_before_lookup(self):
        """前后空格不该变成"查不到这个城市" """
        with mock.patch("requests.get", side_effect=[_resp(_geo()), _resp(_forecast())]) as g:
            make_open_meteo_weather()("  上海  ")
        assert g.call_args_list[0].kwargs["params"]["name"] == "上海"

    def test_blank_city_uses_default_city(self):
        """用户没说城市 → 用配置的默认城市"""
        with mock.patch("requests.get", side_effect=[_resp(_geo()), _resp(_forecast())]) as g:
            data = make_open_meteo_weather("上海")("   ")
        assert data["city"] == "上海"
        assert g.call_args_list[0].kwargs["params"]["name"] == "上海"

    @pytest.mark.parametrize("city", ["", None, "   "])
    def test_no_city_and_no_default_returns_none(self, city, caplog):
        """城市与默认城市都为空 → None（**有意不做 IP 定位**，交给工具追问）"""
        with mock.patch("requests.get") as g:
            with caplog.at_level(logging.INFO):
                assert make_open_meteo_weather("")(city) is None
        g.assert_not_called()          # 没有城市名就不该发任何请求（隐私取舍）
        assert any("未给城市" in r.message for r in caplog.records)

    def test_none_default_city_is_tolerated(self):
        """默认城市写 None（配置缺失）也不炸"""
        with mock.patch("requests.get") as g:
            assert make_open_meteo_weather(None)("") is None
        assert g.call_count == 0

    def test_geocode_http_error_returns_none(self, caplog):
        with mock.patch("requests.get", side_effect=[_resp({}, status=500)]) as g:
            with caplog.at_level(logging.WARNING):
                assert make_open_meteo_weather()("上海") is None
        assert g.call_count == 1, "地理编码失败就不该再发预报请求"
        assert any("地理编码 HTTP" in r.message for r in caplog.records)

    @pytest.mark.parametrize("payload", [
        {},                       # 没有 results 键
        {"results": []},          # 空结果
        {"results": None},        # 显式 null
        None,                     # json() 返回 None
    ])
    def test_geocode_no_hits_returns_none(self, payload, caplog):
        with mock.patch("requests.get", side_effect=[_resp(payload)]) as g:
            with caplog.at_level(logging.INFO):
                assert make_open_meteo_weather()("不存在的城市zzz") is None
        assert g.call_count == 1
        assert any("找不到城市" in r.message for r in caplog.records)

    @pytest.mark.parametrize("place", [
        {"name": "上海", "longitude": 121.46},     # 缺 latitude
        {"name": "上海", "latitude": 31.22},        # 缺 longitude
        {"name": "上海", "latitude": None, "longitude": 121.46},
        {"name": "上海"},
    ])
    def test_geocode_missing_coordinates_returns_none(self, place):
        with mock.patch("requests.get", side_effect=[_resp({"results": [place]})]) as g:
            assert make_open_meteo_weather()("上海") is None
        assert g.call_count == 1, "缺经纬度就不该硬发一次注定失败的预报请求"

    def test_forecast_http_error_returns_none(self, caplog):
        with mock.patch("requests.get", side_effect=[
            _resp(_geo()), _resp({}, status=503),
        ]) as g:
            with caplog.at_level(logging.WARNING):
                assert make_open_meteo_weather()("上海") is None
        assert g.call_count == 2, "预报失败是第二段的事，两段请求都发生过"
        assert any("取数 HTTP" in r.message for r in caplog.records)

    @pytest.mark.parametrize("current", [
        {},                       # 没有 current
        {"current": None},
        {"current": {}},          # 有 current 但没有温度
        {"current": {"weather_code": 2}},
        {"current": {"temperature_2m": None}},
    ])
    def test_missing_temperature_returns_none(self, current):
        with mock.patch("requests.get", side_effect=[
            _resp(_geo()), _resp({"current": current, "daily": {}}),
        ]):
            assert make_open_meteo_weather()("上海") is None

    @pytest.mark.parametrize("highs,lows", [
        (None, ("18.2",)),          # 缺 max
        (("26.6",), None),          # 缺 min
        (None, None),               # 两个都缺
        ((), ("18.2",)),            # 空列表
        (("26.6",), ()),            # 空列表
    ])
    def test_missing_daily_range_still_returns_dict_without_range(self, highs, lows):
        """daily 缺 max/min 时**仍然返回 dict**，只是没有 temp_range ——
        当日区间是锦上添花，不该因为它把整条天气弄没"""
        with mock.patch("requests.get", side_effect=[
            _resp(_geo()), _resp(_forecast(highs=highs, lows=lows)),
        ]):
            data = make_open_meteo_weather()("上海")

        assert data is not None
        assert "temp_range" not in data
        assert data["desc"] == "多云"
        assert data["temp"] == 23

    def test_non_numeric_daily_range_loses_whole_result(self):
        """daily 里的值不是数字 → 整条天气变成 None（当前实现，见测试报告）

        `float(lows[0])` 抛出的 ValueError 是被**外层**那个"网络/结构异常兜底"
        接住的，于是已经取到的 `temp`/`desc` 一起被丢掉。比"少一个区间"更重，
        属于可报告的瑕疵（未修）。
        """
        with mock.patch("requests.get", side_effect=[
            _resp(_geo()), _resp(_forecast(highs=("x",), lows=("y",))),
        ]):
            assert make_open_meteo_weather()("上海") is None

    def test_only_one_of_max_min_present(self):
        """只有 max 或只有 min → 不算完整区间，但结果不该受影响"""
        with mock.patch("requests.get", side_effect=[
            _resp(_geo()), _resp(_forecast(highs=("26.6",), lows=None)),
        ]):
            data = make_open_meteo_weather()("上海")
        assert data is not None
        assert "temp_range" not in data

    @pytest.mark.parametrize("exc", [
        TimeoutError("timed out"),
        ConnectionError("connection reset"),
        ValueError("Expecting value: line 1 column 1"),
    ])
    def test_transport_exception_returns_none(self, exc, caplog):
        """超时 / 断连 / JSON 解析失败 → None，**不向上抛**"""
        with mock.patch("requests.get", side_effect=exc):
            with caplog.at_level(logging.WARNING):
                assert make_open_meteo_weather()("上海") is None
        assert any("查询失败" in r.message for r in caplog.records)

    def test_exception_on_second_request_returns_none(self):
        """第一段成功、第二段超时 → 整体仍是 None（不留半个结果）"""
        with mock.patch("requests.get",
                        side_effect=[_resp(_geo()), TimeoutError("timed out")]):
            assert make_open_meteo_weather()("上海") is None

    def test_geocode_response_has_no_name_falls_back_to_query(self):
        """服务端没回城市名时，用用户问的那个名字（而不是空 city）"""
        place = {"latitude": 31.22, "longitude": 121.46}
        with mock.patch("requests.get", side_effect=[
            _resp({"results": [place]}), _resp(_forecast()),
        ]):
            data = make_open_meteo_weather()("上海")
        assert data["city"] == "上海"

    def test_service_city_name_wins_when_present(self):
        """服务端回了城市名时用它（"上海" → "上海市" 这种补齐）"""
        with mock.patch("requests.get", side_effect=[
            _resp(_geo(name="上海市")), _resp(_forecast()),
        ]):
            data = make_open_meteo_weather()("shanghai")
        assert data["city"] == "上海市"

    def test_unknown_weather_code_desc(self):
        with mock.patch("requests.get", side_effect=[
            _resp(_geo()), _resp(_forecast(code=1234)),
        ]):
            data = make_open_meteo_weather()("上海")
        assert data["desc"] == "未知天气"

    def test_factory_returns_callable(self):
        """工厂返回 `(city) -> Optional[dict]`"""
        weather = make_open_meteo_weather()
        assert callable(weather)


# ══════════════════════════════════════════════════
#  5. reminder_scheduler —— 派发逻辑
# ══════════════════════════════════════════════════

class _FakeReminderTool:
    """鸭子类型的提醒工具：可注入"到点几条"与"抛异常"两种行为"""

    def __init__(self, due=None, pending=None, due_raises=False, pending_raises=False):
        self._due = list(due or [])
        self._pending = list(pending or [])
        self.due_raises = due_raises
        self.pending_raises = pending_raises
        self.due_calls = 0
        self.pending_calls = 0

    def due_now(self):
        self.due_calls += 1
        if self.due_raises:
            raise RuntimeError("提醒表炸了")
        return list(self._due)

    def pending(self):
        self.pending_calls += 1
        if self.pending_raises:
            raise RuntimeError("提醒表读不了")
        return list(self._pending)


class TestReminderSchedulerDispatch:
    """`run_once()` 同步可调，测试直接用它，不必等心跳"""

    def test_no_due_items(self):
        tool = _FakeReminderTool()
        sched = ReminderScheduler(tool)
        assert sched.run_once() == 0
        assert sched.fired_count == 0
        assert tool.due_calls == 1

    def test_dispatches_due_items_and_counts(self):
        """到点项逐个派发，fired_count 累加，返回值是本轮条数"""
        items = [Reminder(id="r1", what="喝水", due_at=1.0),
                 Reminder(id="r2", what="开会", due_at=2.0)]
        seen = []
        sched = ReminderScheduler(_FakeReminderTool(due=items), on_due=seen.append)

        assert sched.run_once() == 2
        assert sched.fired_count == 2
        assert [i.id for i in seen] == ["r1", "r2"]

    def test_fired_count_accumulates_across_rounds(self):
        tool = _FakeReminderTool(due=[object()])
        sched = ReminderScheduler(tool, on_due=lambda i: None)
        for _ in range(3):
            sched.run_once()
        assert sched.fired_count == 3

    def test_without_callback_still_counts(self, caplog):
        """没接回调也要计数（调度器仍算"这一轮派发了 N 条"）"""
        due = [object(), object()]
        sched = ReminderScheduler(_FakeReminderTool(due=due))
        with caplog.at_level(logging.DEBUG):
            assert sched.run_once() == 2
        assert sched.fired_count == 2

    def test_due_now_none_is_treated_as_empty(self):
        """`due_now()` 返回 None（不是列表）也不能炸"""
        tool = _FakeReminderTool()
        tool._due = None
        sched = ReminderScheduler(tool, on_due=lambda i: None)
        assert sched.run_once() == 0
        assert sched.fired_count == 0

    def test_callback_exception_is_swallowed_and_others_continue(self, caplog):
        """一个提醒的回调炸了 → 记日志后继续派发其余提醒"""
        items = [Reminder(id="r1", what="a", due_at=1.0),
                 Reminder(id="r2", what="b", due_at=1.0),
                 Reminder(id="r3", what="c", due_at=1.0)]

        class _Boom(Exception):
            pass

        def on_due(item):
            if item.id == "r2":
                raise _Boom("这条炸了")
            seen.append(item.id)

        seen = []
        sched = ReminderScheduler(_FakeReminderTool(due=items), on_due=on_due)
        with caplog.at_level(logging.WARNING):
            assert sched.run_once() == 3

        assert seen == ["r1", "r3"], "r2 失败不该带走 r3"
        assert sched.fired_count == 3, "计数在回调之前就加了（已尝试派发）"
        assert any("到点回调失败" in r.message for r in caplog.records)

    def test_due_now_exception_returns_zero(self, caplog):
        """`due_now()` 抛异常 → 返回 0（不向上抛，调度线程靠它续命）"""
        sched = ReminderScheduler(_FakeReminderTool(due_raises=True),
                                  on_due=lambda i: None)
        with caplog.at_level(logging.WARNING):
            assert sched.run_once() == 0
        assert sched.fired_count == 0
        assert any("due_now 失败" in r.message for r in caplog.records)

    def test_pending_passthrough(self):
        items = [Reminder(id="r1", what="喝水", due_at=time.time() + 600)]
        sched = ReminderScheduler(_FakeReminderTool(pending=items))
        assert [i.id for i in sched.pending()] == ["r1"]

    def test_pending_failure_returns_empty_list(self, caplog):
        """读待提醒失败 → 返回 []（观测接口不该把调用方带崩）"""
        sched = ReminderScheduler(_FakeReminderTool(pending_raises=True))
        with caplog.at_level(logging.WARNING):
            assert sched.pending() == []
        assert any("读取待提醒失败" in r.message for r in caplog.records)

    def test_pending_returns_a_copy(self):
        """返回副本：调用方改了不该影响工具内部"""
        items = [object()]
        tool = _FakeReminderTool(pending=items)
        sched = ReminderScheduler(tool)
        got = sched.pending()
        got.append(object())
        assert len(sched.pending()) == 1

    def test_tick_is_clamped_to_minimum(self):
        """tick 有下限（防止配成 0 把 CPU 打满）"""
        assert ReminderScheduler(_FakeReminderTool(), tick=0.0)._tick == 0.05
        assert ReminderScheduler(_FakeReminderTool(), tick=-1)._tick == 0.05
        assert ReminderScheduler(_FakeReminderTool(), tick=0.5)._tick == 0.5

    def test_tick_accepts_string_number(self):
        """配置从 YAML 读出来可能是字符串"""
        assert ReminderScheduler(_FakeReminderTool(), tick="0.25")._tick == 0.25

    def test_default_tick(self):
        assert ReminderScheduler(_FakeReminderTool())._tick == DEFAULT_TICK


# ══════════════════════════════════════════════════
#  6. reminder_scheduler —— 线程生命周期
# ══════════════════════════════════════════════════

class TestReminderSchedulerLifecycle:
    """`start()` 幂等 / `stop()` 可重复 / 线程是 daemon / 不留残留

    心跳本身用极小的 tick（0.05s 是模块允许的下限），且每个用例都在
    `finally` 里 `stop()` —— 全量测试套件里绝不能留下活着的调度线程。
    """

    def test_not_running_before_start(self):
        sched = ReminderScheduler(_FakeReminderTool())
        assert sched.running is False
        assert sched._thread is None

    def test_start_sets_running_and_thread_is_daemon(self):
        sched = ReminderScheduler(_FakeReminderTool(), tick=0.05)
        try:
            sched.start()
            assert sched.running is True
            thread = sched._thread
            assert thread is not None
            assert thread.daemon is True, "daemon 线程才不会拖住进程退出"
            assert thread.name == "reminder-scheduler"
        finally:
            sched.stop()

    def test_start_is_idempotent(self):
        """连调两次 start() → 只有一个线程（不叠加心跳）"""
        sched = ReminderScheduler(_FakeReminderTool(), tick=0.05)
        try:
            sched.start()
            first = sched._thread
            sched.start()
            assert sched._thread is first, "重复 start 不该换线程"
            assert sched.running is True
            counted = [t for t in threading.enumerate()
                       if t.name == "reminder-scheduler"]
            assert len(counted) == 1
        finally:
            sched.stop()

    def test_start_after_stop_restarts(self):
        """stop 之后再 start 能重新起来（线程已置空，不是"卡死"状态）"""
        sched = ReminderScheduler(_FakeReminderTool(), tick=0.05)
        try:
            sched.start()
            sched.stop()
            assert sched.running is False
            sched.start()
            assert sched.running is True
        finally:
            sched.stop()

    def test_stop_clears_running_and_thread(self):
        sched = ReminderScheduler(_FakeReminderTool(), tick=0.05)
        sched.start()
        sched.stop()
        assert sched.running is False
        assert sched._thread is None

    def test_stop_is_repeatable(self):
        """stop() 可重复调用（disposer 可能被调用多次）"""
        sched = ReminderScheduler(_FakeReminderTool(), tick=0.05)
        sched.start()
        sched.stop()
        sched.stop()
        assert sched.running is False
        assert sched._thread is None

    def test_stop_before_start_is_noop(self):
        sched = ReminderScheduler(_FakeReminderTool())
        sched.stop()                      # 不抛
        assert sched.running is False

    def test_stop_joins_thread(self):
        """stop() 必须 join：返回后线程不能再活着（债务 D8 的教训）"""
        sched = ReminderScheduler(_FakeReminderTool(), tick=0.05)
        sched.start()
        thread = sched._thread
        sched.stop()
        assert thread.is_alive() is False, "stop() 返回时线程必须已经退出"

    def test_heartbeat_dispatches_without_waiting_manually(self, wait_until):
        """心跳真的会跑：等一个 tick 就能看到 due_now 被调过

        这是唯一一处真等心跳的用例（0.05s 一跳），其余全走 run_once()。
        """
        tool = _FakeReminderTool()
        sched = ReminderScheduler(tool, tick=0.05)
        try:
            sched.start()
            assert wait_until(lambda: tool.due_calls > 0, timeout=3.0), \
                "调度线程一个心跳都没跑"
        finally:
            sched.stop()

    def test_loop_survives_heartbeat_exception(self, wait_until):
        """心跳里 due_now 抛异常 → 线程继续跳，不永久失去提醒能力"""
        tool = _FakeReminderTool(due_raises=True)
        sched = ReminderScheduler(tool, tick=0.05)
        try:
            sched.start()
            assert wait_until(lambda: tool.due_calls >= 2, timeout=3.0), \
                "一次异常就让调度线程死了"
            assert sched.running is True
        finally:
            sched.stop()

    def test_loop_logs_and_continues_when_run_once_itself_raises(self, caplog):
        """`run_once()` **自身**抛异常（不是它内部兜住的那些）→ 心跳记日志后继续

        这是 `_loop` 里那层 `except` 存在的意义：它守的是"run_once 自己崩了"
        这种情况（比如以后有人在它里面加了不加保护的代码）。`run_once` 内部的
        `try` 已经把 `due_now`/回调的异常吃掉了，所以在不改产品代码的前提下，
        只能用替换实例方法来把这条守卫路径逼出来。
        """
        sched = ReminderScheduler(_FakeReminderTool(), tick=0.05)
        calls = []

        def boom():
            calls.append(1)
            raise RuntimeError("run_once 自己炸了")

        sched.run_once = boom            # 只替换这一个实例的方法
        try:
            with caplog.at_level(logging.WARNING):
                sched.start()
                deadline = time.time() + 3.0
                while len(calls) < 3 and time.time() < deadline:
                    time.sleep(0.05)
            assert len(calls) >= 3, "心跳在异常后没有继续跳"
            assert sched.running is True, "线程必须活着（异常不能让它退出）"
            assert any("调度心跳异常" in r.message for r in caplog.records)
        finally:
            sched.stop()


# ══════════════════════════════════════════════════
#  7. reminder_scheduler —— 播报文案
# ══════════════════════════════════════════════════

# ══════════════════════════════════════════════════
#  8. productivity_providers 插件
# ══════════════════════════════════════════════════

def _ctx(**services):
    """假上下文：预置若干服务；没预置的就是"没人提供"

    （与 `tests/agent/test_plugins.py` 一致：直接 `Context()` + `provide()`
    拼一个最小上下文，不 new 真实的 `XiaoyiApp`。）
    """
    ctx = Context()
    for name, value in services.items():
        ctx.provide(name, value)
    return ctx


def _config(**over):
    """假配置：`productivity_tools_plugin` 读的两个键没有 getattr 兜底，
    所以必须显式给全；其余键只给关心的那几个，用来验证缺字段时的容错。"""
    base = dict(tools_productivity=True, productivity_reminder_scheduler=True)
    base.update(over)
    return SimpleNamespace(**base)


def _llm(text="Hello"):
    return lambda prompt: text


class TestProvidersPlugin:
    """translate / weather 的默认提供者（没人提供时才注册）"""

    def test_registers_both_services(self):
        ctx = _ctx(**{SVC_CONFIG: _config(), SVC_LLM_ONCE: _llm()})
        disposition = providers_setup(ctx)
        try:
            assert disposition is None, "本插件没有后台资源，不需要 disposer"
            assert ctx.use(SVC_TRANSLATE) is not None
            assert callable(ctx.use(SVC_TRANSLATE))
            assert ctx.use(SVC_WEATHER) is not None
            assert callable(ctx.use(SVC_WEATHER))
            assert ctx.use(SVC_TRANSLATE)("你好", "en") == "Hello"
        finally:
            ctx.dispose()

    def test_translate_uses_the_llm_service(self):
        """translate 服务真的走 LLM（而不是别的翻译源）"""
        seen = []
        ctx = _ctx(**{SVC_CONFIG: _config(),
                      SVC_LLM_ONCE: lambda p: seen.append(p) or "以下是翻译：Hello"})
        try:
            providers_setup(ctx)
            assert ctx.use(SVC_TRANSLATE)("你好", "en") == "Hello"
            assert len(seen) == 1
            assert "你好" in seen[0]
        finally:
            ctx.dispose()

    def test_weather_uses_configured_default_city(self):
        """`agent.weather.default_city` 透传给天气取数函数"""
        ctx = _ctx(**{SVC_CONFIG: _config(weather_default_city="上海"),
                      SVC_LLM_ONCE: _llm()})
        try:
            providers_setup(ctx)
            weather = ctx.use(SVC_WEATHER)
            with mock.patch("requests.get", side_effect=[
                _resp(_geo()), _resp(_forecast()),
            ]) as g:
                assert weather("")["city"] == "上海"
            assert g.call_args_list[0].kwargs["params"]["name"] == "上海"
        finally:
            ctx.dispose()

    def test_default_city_missing_key_is_tolerated(self):
        """配置里没有 weather_default_city → 空串，空城市仍如实返回 None"""
        ctx = _ctx(**{SVC_CONFIG: _config(), SVC_LLM_ONCE: _llm()})
        try:
            providers_setup(ctx)
            with mock.patch("requests.get") as g:
                assert ctx.use(SVC_WEATHER)("") is None
            assert g.call_count == 0, "没有默认城市就不该发任何请求（不做 IP 定位）"
        finally:
            ctx.dispose()

    def test_existing_translate_is_not_overwritten(self):
        """`build_agent_stack(translate_func=...)` 的显式注入算覆盖，调用方说了算"""
        mine = lambda text, lang: "外部注入的译文"
        ctx = _ctx(**{SVC_CONFIG: _config(), SVC_TRANSLATE: mine,
                      SVC_LLM_ONCE: _llm("插件不该被用上")})
        try:
            providers_setup(ctx)
            assert ctx.use(SVC_TRANSLATE) is mine
            assert ctx.use(SVC_TRANSLATE)("你好", "en") == "外部注入的译文"
            assert ctx.use(SVC_WEATHER) is not None, "weather 仍应正常提供"
        finally:
            ctx.dispose()

    def test_existing_weather_is_not_overwritten(self):
        mine = lambda city: {"city": "外部", "desc": "晴", "temp": 1}
        ctx = _ctx(**{SVC_CONFIG: _config(), SVC_WEATHER: mine,
                      SVC_LLM_ONCE: _llm()})
        try:
            providers_setup(ctx)
            assert ctx.use(SVC_WEATHER) is mine
            assert ctx.use(SVC_WEATHER)("x")["city"] == "外部"
            assert ctx.use(SVC_TRANSLATE) is not None
        finally:
            ctx.dispose()

    def test_translate_disabled_by_config(self):
        """`agent.productivity.translate: false` → 不注册（工具退回友好提示）"""
        ctx = _ctx(**{SVC_CONFIG: _config(productivity_translate=False),
                      SVC_LLM_ONCE: _llm()})
        try:
            providers_setup(ctx)
            assert ctx.has(SVC_TRANSLATE) is False
            assert ctx.has(SVC_WEATHER) is True
        finally:
            ctx.dispose()

    def test_weather_disabled_by_config(self):
        """`agent.productivity.weather: false` → 不注册"""
        ctx = _ctx(**{SVC_CONFIG: _config(productivity_weather=False),
                      SVC_LLM_ONCE: _llm()})
        try:
            providers_setup(ctx)
            assert ctx.has(SVC_WEATHER) is False
            assert ctx.has(SVC_TRANSLATE) is True
        finally:
            ctx.dispose()

    def test_both_disabled(self):
        ctx = _ctx(**{SVC_CONFIG: _config(productivity_translate=False,
                                          productivity_weather=False),
                      SVC_LLM_ONCE: _llm()})
        try:
            providers_setup(ctx)
            assert ctx.has(SVC_TRANSLATE) is False
            assert ctx.has(SVC_WEATHER) is False
        finally:
            ctx.dispose()

    def test_no_llm_skips_translate_keeps_weather(self, caplog):
        """没有 LLM（`SVC_LLM_ONCE` 未注册或为 None）→ 只少 translate"""
        ctx = _ctx(**{SVC_CONFIG: _config()})
        try:
            with caplog.at_level(logging.INFO):
                providers_setup(ctx)
            assert ctx.has(SVC_TRANSLATE) is False
            assert ctx.has(SVC_WEATHER) is True, "天气不需要 LLM，必须照常提供"
            assert any("无 LLM" in r.message for r in caplog.records)
        finally:
            ctx.dispose()

    def test_llm_service_present_but_none(self):
        """`SVC_LLM_ONCE` 注册成了 None（装配时 llm_once=None）→ 同样不注册 translate"""
        ctx = _ctx(**{SVC_CONFIG: _config(), SVC_LLM_ONCE: None})
        try:
            providers_setup(ctx)
            assert ctx.has(SVC_TRANSLATE) is False
            assert ctx.has(SVC_WEATHER) is True
        finally:
            ctx.dispose()


# ══════════════════════════════════════════════════
#  9. productivity_tools 插件
# ══════════════════════════════════════════════════

class _FakeBus:
    """假事件总线：记录 emit 调用，并可注入"抛异常"行为"""

    def __init__(self, raises=False):
        self.raises = raises
        self.emits = []

    def emit(self, event_type, **data):
        self.emits.append((event_type, data))
        if self.raises:
            raise RuntimeError("总线炸了")


class _FakeRegistry:
    """假工具注册表：复用真实语义（register 返回注销函数）"""

    def __init__(self):
        self.tools = {}
        self.unregistered = []

    def register(self, tool):
        self.tools[tool.name] = tool

        def dispose():
            self.tools.pop(tool.name, None)
            self.unregistered.append(tool.name)

        return dispose


class TestToolsPlugin:
    """4 个生产力工具 + 提醒调度器的生命周期（F7 改动）"""

    def test_registers_four_tools(self):
        reg = _FakeRegistry()
        ctx = _ctx(**{SVC_CONFIG: _config(), SVC_REGISTRY: reg,
                      SVC_TRANSLATE: lambda t, l: "x",
                      SVC_WEATHER: lambda c: None,
                      SVC_BUS: _FakeBus()})
        disposition = tools_setup(ctx)
        try:
            assert len(reg.tools) == 4
            assert set(reg.tools) == {"calculate", "translate", "reminder", "weather"}
            assert isinstance(reg.tools["reminder"], ReminderTool)
        finally:
            disposition()
            ctx.dispose()

    def test_injected_backends_reach_the_tools(self):
        """注册的服务真的接到了工具上（不是摆设）"""
        reg = _FakeRegistry()
        ctx = _ctx(**{SVC_CONFIG: _config(), SVC_REGISTRY: reg,
                      SVC_TRANSLATE: lambda t, l: "Hello",
                      SVC_WEATHER: lambda c: {"city": "上海", "desc": "晴", "temp": 20},
                      SVC_BUS: _FakeBus()})
        disposition = tools_setup(ctx)
        try:
            assert reg.tools["translate"].execute({"text": "你好", "target_lang": "en"}).success
            weather = reg.tools["weather"].execute({"city": "上海"})
            assert weather.success and "上海" in weather.summary
        finally:
            disposition()
            ctx.dispose()

    def test_disposer_stops_reminder_thread(self):
        """**F7 的核心回归**：disposer 被调用后调度线程必须已经停止

        与债务 D8「执行器线程未显式 join」同类 —— 插件卸载/退出时留下一个
        活着的调度线程，会让进程退出挂住。这里直接钉住 `running is False`。
        """
        reg = _FakeRegistry()
        ctx = _ctx(**{SVC_CONFIG: _config(), SVC_REGISTRY: reg,
                      SVC_LLM_ONCE: _llm(), SVC_BUS: _FakeBus()})
        disposition = tools_setup(ctx)

        scheduler = ctx.use(SVC_REMINDER_SCHEDULER)
        assert scheduler.running is True, "装配后调度器应当是活的"
        assert scheduler._thread.daemon is True
        assert [t for t in threading.enumerate()
                if t.name == "reminder-scheduler"], "线程确实存在"

        disposition()

        assert scheduler.running is False, "disposer 必须停掉提醒调度线程"
        assert scheduler._thread is None
        assert not [t for t in threading.enumerate()
                    if t.name == "reminder-scheduler"], "不留残留线程"
        ctx.dispose()

    def test_scheduler_is_registered_as_service(self):
        reg = _FakeRegistry()
        ctx = _ctx(**{SVC_CONFIG: _config(), SVC_REGISTRY: reg, SVC_BUS: _FakeBus()})
        disposition = tools_setup(ctx)
        try:
            assert isinstance(ctx.use(SVC_REMINDER_SCHEDULER), ReminderScheduler)
        finally:
            disposition()
            ctx.dispose()

    def test_scheduler_can_be_disabled_by_config(self):
        """`agent.productivity.reminder_scheduler: false` → 不装调度器、不起线程"""
        reg = _FakeRegistry()
        ctx = _ctx(**{SVC_CONFIG: _config(productivity_reminder_scheduler=False),
                      SVC_REGISTRY: reg, SVC_BUS: _FakeBus()})
        disposition = tools_setup(ctx)
        try:
            assert ctx.has(SVC_REMINDER_SCHEDULER) is False
            assert len(reg.tools) == 4, "工具仍要注册（只是到点不会响）"
            assert not [t for t in threading.enumerate()
                        if t.name == "reminder-scheduler"]
        finally:
            disposition()
            ctx.dispose()

    def test_disposer_without_scheduler_still_unregisters_tools(self):
        """关掉调度器时 disposer 仍要注销工具（不能因为 scheduler is None 就提前返回）"""
        reg = _FakeRegistry()
        ctx = _ctx(**{SVC_CONFIG: _config(productivity_reminder_scheduler=False),
                      SVC_REGISTRY: reg, SVC_BUS: _FakeBus()})
        disposition = tools_setup(ctx)
        assert reg.tools
        disposition()
        assert reg.tools == {}
        assert sorted(reg.unregistered) == [
            "calculate", "reminder", "translate", "weather"]
        ctx.dispose()

    def test_tools_disabled_by_config_returns_none(self):
        """`agent.tools.productivity: false` → 返回 None 且一个工具都不注册"""
        reg = _FakeRegistry()
        ctx = _ctx(**{SVC_CONFIG: _config(tools_productivity=False), SVC_REGISTRY: reg,
                      SVC_BUS: _FakeBus()})
        try:
            assert tools_setup(ctx) is None
            assert reg.tools == {}
            assert reg.unregistered == []
            assert ctx.has(SVC_REMINDER_SCHEDULER) is False
            assert not [t for t in threading.enumerate()
                        if t.name == "reminder-scheduler"]
        finally:
            ctx.dispose()


class TestToolsPluginOnDueWiring:
    """到点回调：发 `reminder.due` 事件 + 兼容外部注入的回调

    这些用例**不启调度线程**（`productivity_reminder_scheduler: false`），
    直接从注册好的 `ReminderTool` 上取出插件构造的那个回调来调 —— 否则后台
    心跳可能抢先把提醒消费掉，让断言变得不可靠。
    """

    def _setup(self, bus=None, external=None):
        reg = _FakeRegistry()
        services = {SVC_CONFIG: _config(productivity_reminder_scheduler=False),
                    SVC_REGISTRY: reg}
        if bus is not None:
            services[SVC_BUS] = bus
        if external is not None:
            services[SVC_REMINDER] = external
        ctx = _ctx(**services)
        disposition = tools_setup(ctx)
        reminder_tool = reg.tools["reminder"]
        # 取插件装到工具上的回调本体（设置提醒与"到点喊一声"是同一件事的两半）
        return ctx, disposition, reg, reminder_tool._on_due

    def test_emits_reminder_due_event(self):
        from core.kernel.events import EventTypes

        bus = _FakeBus()
        ctx, disposition, _reg, on_due = self._setup(bus=bus)
        try:
            on_due("rem-1", "喝水")
        finally:
            disposition()
            ctx.dispose()

        assert len(bus.emits) == 1
        event_type, data = bus.emits[0]
        assert event_type == EventTypes.REMINDER_DUE
        assert data["reminder_id"] == "rem-1"
        assert data["what"] == "喝水"
        assert "喝水" in data["text"]

    def test_bus_failure_is_swallowed(self, caplog):
        """总线 emit 抛异常 → 记日志，不影响外部回调"""
        bus = _FakeBus(raises=True)
        seen = []
        ctx, disposition, _reg, on_due = self._setup(
            bus=bus, external=lambda rid, what: seen.append(what))
        try:
            with caplog.at_level(logging.WARNING):
                on_due("rem-1", "喝水")
        finally:
            disposition()
            ctx.dispose()

        assert seen == ["喝水"], "总线炸了不该带走外部回调"
        assert any("提醒事件发送失败" in r.message for r in caplog.records)

    def test_external_callback_is_called(self):
        seen = []
        ctx, disposition, _reg, on_due = self._setup(
            bus=_FakeBus(), external=lambda rid, what: seen.append((rid, what)))
        try:
            on_due("rem-2", "开会")
        finally:
            disposition()
            ctx.dispose()
        assert seen == [("rem-2", "开会")]

    def test_external_callback_failure_is_swallowed(self, caplog):
        """外部回调（`on_reminder_due`）抛异常 → 只记日志"""
        def boom(rid, what):
            raise RuntimeError("调用方的回调炸了")

        ctx, disposition, _reg, on_due = self._setup(bus=_FakeBus(), external=boom)
        try:
            with caplog.at_level(logging.WARNING):
                on_due("rem-3", "喝水")
        finally:
            disposition()
            ctx.dispose()
        assert any("外部提醒回调失败" in r.message for r in caplog.records)

    def test_without_bus_and_external_is_noop(self):
        """既没总线也没外部回调 → 什么都不做，也不抛"""
        ctx, disposition, _reg, on_due = self._setup()
        try:
            on_due("rem-4", "喝水")
        finally:
            disposition()
            ctx.dispose()

    def test_end_to_end_reminder_reaches_the_bus(self):
        """**生产接线**：设一条到期提醒 → 调度器 `run_once()` → 总线收到事件

        这是"对用户撒谎的功能"被修好的直接证据，而且这里走的是**插件真实装出
        来的那条链路**（不额外给调度器传 `on_due`）：

            scheduler.run_once() → ReminderTool.due_now()
                                 → ReminderTool._on_due(id, what)   ← 插件装的回调
                                 → bus.emit("reminder.due", ...)

        `due_at` 直接改成过去时间：`minutes` 最小值是 0.1 分钟，用 0.001 也只有
        60ms 窗口，测试会跟时钟赛跑；本用例验的是派发链路，不是计时精度。
        """
        from core.kernel.events import EventTypes

        bus = _FakeBus()
        ctx, disposition, reg, _on_due = self._setup(bus=bus)
        try:
            reminder = reg.tools["reminder"]
            res = reminder.execute({"what": "喝水", "minutes": 0.001})
            assert res.success
            assert len(reminder.pending()) == 1, "提醒进入待触发队列"
            assert reminder.pending()[0].due_at > time.time(), "刚设好时还没到点"

            reminder.pending()[0].due_at = time.time() - 1

            scheduler = ReminderScheduler(tool=reminder)     # 与插件完全一致
            assert scheduler._on_due is None, "插件确实没给调度器传回调（见下一个测试类）"
            assert scheduler.run_once() == 1
            assert scheduler.fired_count == 1
            assert reminder.pending() == [], "触发后已从待触发队列移除"
            assert scheduler.run_once() == 0, "同一条提醒不会被重复播报"
            assert scheduler.fired_count == 1
        finally:
            disposition()
            ctx.dispose()

        assert [e[0] for e in bus.emits] == [EventTypes.REMINDER_DUE]
        assert bus.emits[0][1] == {
            "reminder_id": "rem-1", "what": "喝水", "text": "提醒你喝水",
        }


class TestOnDueWiringFacts:
    """插件真实接线的**事实**（把现状钉住，免得被误以为"已经接通了"）

    两处容易看错的细节，都在这里用断言固定下来：

    1. `ReminderScheduler.on_due` 在**生产路径上永远是 None** —— 插件构造调度器时
       根本没传它（`ReminderScheduler(tool=reminder)`）。事件是靠
       `ReminderTool._on_due`（插件通过 `all_productivity_tools(on_reminder_due=...)`
       装到工具上的）发出来的。也就是说调度器只负责"按时去问"，不负责"怎么喊"。
    2. 两个回调的**形状不同**：调度器按 `(item)` 调，而 `_build_on_due` 造的是
       `(reminder_id, what)`。它们谁也没接谁，所以当前没有 TypeError；
       但**将来若有人补上 `on_due=`，那一条提醒会发两次事件**（调度器的回调一次、
       工具自己的回调一次），而且 `_build_on_due` 会被以 item 调 → TypeError。
       `ReminderScheduler` 的 docstring 已写明"二选一，别都接"。

    （原先这里还有一条针对 `make_reminder_announcer` 的用例；那个函数无人调用、
    形状又和插件真正用的回调不一致，属于"死代码 + 陷阱"，已删除。）
    """

    def test_scheduler_on_due_is_not_wired_in_production(self):
        reg = _FakeRegistry()
        ctx = _ctx(**{SVC_CONFIG: _config(), SVC_REGISTRY: reg, SVC_BUS: _FakeBus()})
        disposition = tools_setup(ctx)
        try:
            scheduler = ctx.use(SVC_REMINDER_SCHEDULER)
            assert scheduler._on_due is None, (
                "插件没给调度器传 on_due —— 事件由 ReminderTool._on_due 发出；"
                "若哪天改成传了，这条断言会失败并提醒去查是否出现重复播报")
            assert reg.tools["reminder"]._on_due is not None, "工具上必须装回调"
        finally:
            disposition()
            ctx.dispose()

    def test_scheduler_passes_one_argument_but_plugin_callback_wants_two(self):
        """调度器 `(item)` vs 插件回调 `(id, what)`：直接对接会 TypeError

        调度器把这层异常吞掉只记日志，不抛 —— 所以万一接错，症状是
        "提醒静默不响"，而不是崩掉（这正是需要用例盯着的原因）。
        """
        seen = []

        def two_arg_callback(reminder_id, what):
            seen.append((reminder_id, what))

        sched = ReminderScheduler(_FakeReminderTool(due=[object()]),
                                  on_due=two_arg_callback)
        assert sched.run_once() == 1
        assert seen == [], "item 被当成 reminder_id 传了进去，TypeError 被吞"
        assert sched.fired_count == 1, "计数照样加（已经尝试派发过）"


class TestWeatherWithoutRequests:
    """`requests` 导入失败时天气能力要优雅降级（**不是用 pragma 屏蔽掉**）

    `open_meteo.py` 里那条 `except Exception` 原先挂着 `# pragma: no cover` ——
    用屏蔽换覆盖率是项目明令禁止的。这里用 `sys.modules["requests"] = None`
    让 `import requests` 真的抛 ImportError，把它逼出来。

    这条用例同时钉住了"降级而不是抛异常"：真抛了的话，测试会直接失败。
    """

    def test_import_failure_degrades_to_none(self):
        weather = make_open_meteo_weather("上海")
        with mock.patch.dict(sys.modules, {"requests": None}):
            assert weather("上海") is None
