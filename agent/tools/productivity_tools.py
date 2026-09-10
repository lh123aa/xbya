"""生产力工具集

四个工具：
- calculate  数学计算（AST 安全求值，不用 eval）
- translate  翻译（需要注入翻译函数，缺省时降级）
- reminder   设置提醒（需要注入调度回调）
- weather    天气查询（需要注入 HTTP 取数函数）

外部能力以**函数注入**方式提供，既解耦又可测：
测试注入桩函数即可，无需真实网络。
"""

import ast
import logging
import operator
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from agent.tools.base import BaseTool, ToolResult

logger = logging.getLogger(__name__)

#: 表达式长度上限
MAX_EXPRESSION_LEN = 200

#: 幂运算指数上限（防 9**9**9 卡死）
MAX_POWER_EXPONENT = 100

# ── 安全计算：AST 白名单 ──

_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}

_UNARY_OPS = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}

# ── 中文数字 → 阿拉伯数字 ──

_CN_DIGITS = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
              "六": 6, "七": 7, "八": 8, "九": 9}
_CN_UNITS = {"十": 10, "百": 100, "千": 1000, "万": 10000}

# ── 中文运算符 → 符号 ──

_CN_OPERATORS = [
    ("加上", "+"), ("加", "+"),
    ("减去", "-"), ("减", "-"),
    ("乘以", "*"), ("乘", "*"),
    ("除以", "/"), ("除", "/"),
    ("的平方", "**2"), ("平方", "**2"),
    ("×", "*"), ("÷", "/"), ("（", "("), ("）", ")"),
]


def cn_to_number(text: str) -> str:
    """把中文数字片段转成阿拉伯数字（支持"二十五"、"三百"、"一千零五"）"""
    if not text:
        return text

    def convert(segment: str) -> str:
        total = 0
        section = 0
        number = 0
        for ch in segment:
            if ch in _CN_DIGITS:
                number = _CN_DIGITS[ch]
            elif ch in _CN_UNITS:
                unit = _CN_UNITS[ch]
                if unit == 10000:
                    section = (section + (number or 1)) * unit
                    total += section
                    section = 0
                else:
                    section += (number or 1) * unit
                number = 0
            else:
                # 不可达：convert 只被下面的正则喂纯中文数字串
                return segment   # pragma: no cover
        return str(total + section + number)

    return re.sub(r"[零一二两三四五六七八九十百千万]+", lambda m: convert(m.group(0)), text)


def normalize_expression(raw: str) -> str:
    """把口语化算式规范化为可求值表达式"""
    expr = str(raw or "").strip()
    if not expr:
        return ""
    for cn, sym in _CN_OPERATORS:
        expr = expr.replace(cn, sym)
    expr = cn_to_number(expr)
    expr = expr.replace("等于多少", "").replace("等于几", "").replace("是多少", "")
    expr = re.sub(r"[?？。！!，,]", "", expr)
    expr = expr.replace("^", "**")
    return expr.strip()


# ══════════════════════════════════════════════════════
#  1. calculate
# ══════════════════════════════════════════════════════

class CalculateTool(BaseTool):
    """数学计算（AST 安全求值）"""

    name = "calculate"
    description = "计算一个数学表达式，支持 + - * / // % ** 与括号"
    risk_level = "low"
    timeout = 5
    params_schema = {
        "type": "object",
        "properties": {
            "expression": {"type": "string", "description": "算式，如 (25+5)*4"},
        },
        "required": ["expression"],
    }

    def execute(self, params: Dict[str, Any]) -> ToolResult:
        raw = str(params.get("expression") or "")
        expr = normalize_expression(raw)

        if not expr:
            return ToolResult.fail("你想算什么呀？", emotion="think")
        if len(expr) > MAX_EXPRESSION_LEN:
            return ToolResult.fail("这个算式太长啦，我算不过来呢", emotion="surprised")

        try:
            tree = ast.parse(expr, mode="eval")
        except SyntaxError:
            return ToolResult.fail(f"「{raw}」这个算式我看不太懂呢", emotion="think")

        try:
            value = self._eval(tree.body)
        except ZeroDivisionError:
            return ToolResult.fail("除数不能是 0 哦", emotion="surprised")
        except ValueError as e:
            return ToolResult.fail(str(e), emotion="think")
        except Exception as e:
            logger.debug("[calculate] 求值失败: %s", e)
            return ToolResult.fail(f"「{raw}」我算不出来呢", emotion="think")

        value = self._round(value)
        return ToolResult.ok(
            data={"expression": expr, "result": value},
            summary=f"{expr} 等于 {value}",
            emotion="happy",
        )

    def _eval(self, node: ast.AST) -> float:
        """递归求值（仅允许白名单节点）"""
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
                raise ValueError("算式里只能有数字哦")
            return node.value

        if isinstance(node, ast.BinOp):
            op = _BIN_OPS.get(type(node.op))
            if op is None:
                raise ValueError("这个运算符我不认识呢")
            left = self._eval(node.left)
            right = self._eval(node.right)
            if op is operator.pow and abs(right) > MAX_POWER_EXPONENT:
                raise ValueError("指数太大了，我不敢算")
            return op(left, right)

        if isinstance(node, ast.UnaryOp):
            op = _UNARY_OPS.get(type(node.op))
            if op is None:
                raise ValueError("这个运算符我不认识呢")
            return op(self._eval(node.operand))

        raise ValueError("算式里有我看不懂的东西呢")

    @staticmethod
    def _round(value: Any) -> Any:
        """规整结果：浮点误差收敛、整数去掉小数点"""
        try:
            if isinstance(value, float):
                if value.is_integer():
                    return int(value)
                return round(value, 6)
        except Exception:
            pass
        return value


# ══════════════════════════════════════════════════════
#  2. translate
# ══════════════════════════════════════════════════════

#: 翻译调用签名：(text, target_lang) -> Optional[str]
TranslateFunc = Callable[[str, str], Optional[str]]

LANG_NAMES = {
    "zh": "中文", "en": "英文", "ja": "日文", "ko": "韩文",
    "fr": "法文", "de": "德文", "es": "西班牙文", "ru": "俄文",
}


class TranslateTool(BaseTool):
    """翻译文本（翻译能力由外部注入）"""

    name = "translate"
    description = "把文本翻译成指定语言"
    risk_level = "low"
    timeout = 20
    params_schema = {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "要翻译的文本"},
            "target_lang": {
                "type": "string",
                "enum": list(LANG_NAMES.keys()),
                "description": "目标语言，默认英文",
            },
        },
        "required": ["text"],
    }

    def __init__(self, translate_func: Optional[TranslateFunc] = None) -> None:
        """
        Args:
            translate_func: 翻译函数；None 时工具不可用（返回友好提示）
        """
        self._translate = translate_func

    def execute(self, params: Dict[str, Any]) -> ToolResult:
        text = str(params.get("text") or "").strip()
        if not text:
            return ToolResult.fail("你想翻译什么内容呀？", emotion="think")

        target = str(params.get("target_lang") or "en").lower()
        if target not in LANG_NAMES:
            target = "en"

        if self._translate is None:
            return ToolResult.fail("我这边还没接上翻译能力呢", emotion="sad")

        try:
            translated = self._translate(text, target)
        except Exception as e:
            logger.warning("[translate] 调用失败: %s", e)
            return ToolResult.fail("翻译出错了呢，稍后再试试~", emotion="sad")

        if not translated:
            return ToolResult.fail("没翻出来呢，换个说法试试？", emotion="sad")

        lang = LANG_NAMES[target]
        return ToolResult.ok(
            data={"text": text, "translated": translated, "target_lang": target},
            summary=f"翻成{lang}是：{translated}",
            emotion="talk",
        )


# ══════════════════════════════════════════════════════
#  3. reminder
# ══════════════════════════════════════════════════════

#: 到点回调签名：(reminder_id, what) -> None
ReminderCallback = Callable[[str, str], None]


@dataclass
class Reminder:
    """一条提醒

    Attributes:
        id: 提醒唯一标识
        what: 提醒内容
        due_at: 到期时间戳
        when_text: 人类可读的时间描述
        fired: 是否已触发
    """

    id: str
    what: str
    due_at: float
    when_text: str = ""
    fired: bool = False


class ReminderTool(BaseTool):
    """设置提醒（调度由外部注入的回调负责）"""

    name = "reminder"
    description = "在指定时间后提醒用户做某事"
    risk_level = "low"
    timeout = 5
    params_schema = {
        "type": "object",
        "properties": {
            "what": {"type": "string", "description": "提醒内容"},
            "minutes": {"type": "number", "minimum": 0.1, "maximum": 1440,
                        "description": "多少分钟后提醒"},
        },
        "required": ["what"],
    }

    #: 默认提醒间隔（未给出 minutes 时）
    DEFAULT_MINUTES = 30.0

    def __init__(
        self,
        on_due: Optional[ReminderCallback] = None,
        default_minutes: float = DEFAULT_MINUTES,
        store: Optional[Any] = None,
    ) -> None:
        """
        Args:
            on_due: 到点回调（由插件注入，负责播报）
            default_minutes: 未给 minutes 时的默认提前量
            store: 持久化后端（P4-A2 / D13）。鸭子类型，只需
                `load() -> payload|None` 与 `save(payload) -> bool`；
                传 None = 纯内存（与加持久化之前的行为完全一致）。
                ⚠️ 存储层的异常必须自己吞掉 —— 这里不替它兜底，
                因为"存不下来"不该让用户设的提醒失败。
        """
        self._on_due = on_due
        self._default_minutes = default_minutes
        self._store = store
        self._items: Dict[str, Reminder] = {}
        self._counter = 0
        #: 提醒表会被两个线程碰：`execute()` 在工具线程池里加，调度线程里 `due_now()` 取。
        #: 用锁显式串行化，而不是依赖"dict 操作恰好是原子的"这种实现细节。
        self._lock = threading.Lock()

    def execute(self, params: Dict[str, Any]) -> ToolResult:
        what = str(params.get("what") or "").strip()
        if not what:
            return ToolResult.fail("你想让我提醒你什么呀？", emotion="think")

        raw_minutes = params.get("minutes")
        try:
            minutes = float(raw_minutes) if raw_minutes is not None else self._default_minutes
        except (TypeError, ValueError):
            minutes = self._default_minutes
        if minutes <= 0:
            return ToolResult.fail("提醒时间得是正数哦", emotion="think")

        self._counter += 1
        reminder_id = f"rem-{self._counter}"
        due_at = time.time() + minutes * 60
        when_text = self._format_when(minutes)

        item = Reminder(id=reminder_id, what=what, due_at=due_at, when_text=when_text)
        with self._lock:
            self._items[reminder_id] = item
            snapshot = self.to_payload()
        self._persist(snapshot)

        return ToolResult.ok(
            data={
                "id": reminder_id,
                "what": what,
                "minutes": minutes,
                "when_text": when_text,
                "due_at": due_at,
            },
            summary=f"好的，{when_text}我会提醒你{what}",
            emotion="happy",
        )

    @staticmethod
    def _format_when(minutes: float) -> str:
        """分钟数 → 口语化时间（"30分钟后"、"1个半小时后"）"""
        if minutes < 1:
            return "马上"
        if minutes < 60:
            return f"{int(minutes)}分钟后"
        hours = minutes / 60
        if abs(hours - round(hours)) < 0.01:
            return f"{int(round(hours))}小时后"
        return f"{hours:.1f}小时后"

    # ── 调度接口（由外部定时器驱动）──

    def due_now(self) -> List[Reminder]:
        """取出并标记所有已到期的提醒"""
        now = time.time()
        fired: List[Reminder] = []
        with self._lock:
            for item in list(self._items.values()):
                if not item.fired and item.due_at <= now:
                    item.fired = True
                    fired.append(item)
            for item in fired:
                self._items.pop(item.id, None)
            # 到点即出表，落盘要跟上 —— 否则重启后一条已经响过的提醒会再响一次
            snapshot = self.to_payload() if fired else None
        if snapshot is not None:
            self._persist(snapshot)
        for item in fired:
            if self._on_due is not None:
                try:
                    self._on_due(item.id, item.what)
                except Exception as e:
                    logger.warning("[reminder] 到点回调失败: %s", e)
        return fired

    def pending(self) -> List[Reminder]:
        """当前未到期的提醒（快照）"""
        with self._lock:
            return [i for i in self._items.values() if not i.fired]

    def cancel(self, reminder_id: str) -> bool:
        """取消一条提醒"""
        with self._lock:
            removed = self._items.pop(reminder_id, None) is not None
            snapshot = self.to_payload() if removed else None
        if snapshot is not None:
            self._persist(snapshot)
        return removed

    # ── 持久化（P4-A2 / D13）──
    #
    # 三个要点：
    #   ① 落盘在**锁外**做 —— 文件 I/O 不该占着锁，否则调度线程会被磁盘卡住
    #   ② 快照在**锁内**取 —— 保证写出去的是某一时刻的一致状态
    #   ③ 任何失败只记日志 —— 存不下来也得让用户把提醒设上（内存里还有效）

    def to_payload(self) -> Dict[str, Any]:
        """当前提醒表的可序列化快照（含 `counter`，避免重启后 id 复用）"""
        return {
            "counter": self._counter,
            "items": [
                {"id": i.id, "what": i.what, "due_at": i.due_at,
                 "when_text": i.when_text, "fired": i.fired}
                for i in self._items.values()
            ],
        }

    def apply_payload(self, payload: Optional[Dict[str, Any]]) -> int:
        """把恢复出来的提醒并回内存表

        Returns:
            实际恢复的条数

        语义选择：**已到点但没响过的提醒保留原 due_at** —— 调度器下一跳就会看到
        它过期并立刻播报（"你上次让我提醒的事"），而不是悄悄丢掉。
        用户关着程序的这段时间到点了，开机后补一声，比什么都不发生更接近"提醒"的本意。
        """
        if not payload:
            return 0
        items = payload.get("items") or []
        restored = 0
        with self._lock:
            for raw in items:
                if not isinstance(raw, dict):
                    continue
                rid = str(raw.get("id") or "")
                if not rid or rid in self._items:
                    continue
                try:
                    due_at = float(raw.get("due_at"))
                except (TypeError, ValueError):
                    continue
                self._items[rid] = Reminder(
                    id=rid,
                    what=str(raw.get("what") or ""),
                    due_at=due_at,
                    when_text=str(raw.get("when_text") or ""),
                    fired=bool(raw.get("fired")),
                )
                restored += 1
                # id 形如 rem-3 → 把计数器顶到 3，避免重启后 rem-1 撞车
                suffix = rid.rsplit("-", 1)[-1]
                if suffix.isdigit():
                    self._counter = max(self._counter, int(suffix))
            counter = payload.get("counter")
            if isinstance(counter, int) and counter >= 0:
                self._counter = max(self._counter, counter)
        if restored:
            logger.info("[reminder] 已恢复 %d 条未到点提醒", restored)
        return restored

    def restore(self) -> int:
        """从 store 恢复（装配时调用一次）

        Returns:
            恢复条数；无 store / 无文件 / 读失败都返回 0
        """
        if self._store is None:
            return 0
        try:
            payload = self._store.load()
        except Exception as e:                          # 存储层违约也不能炸装配
            logger.warning("[reminder] 恢复失败（按无提醒启动）: %s", e)
            return 0
        return self.apply_payload(payload)

    def _persist(self, snapshot: Dict[str, Any]) -> bool:
        """落盘（失败只记日志，绝不上抛）"""
        if self._store is None:
            return False
        try:
            return bool(self._store.save(snapshot))
        except Exception as e:
            logger.warning("[reminder] 落盘失败（提醒仍在内存里）: %s", e)
            return False


# ══════════════════════════════════════════════════════
#  4. weather
# ══════════════════════════════════════════════════════

#: 天气取数签名：(city) -> Optional[dict]
WeatherFunc = Callable[[str], Optional[Dict[str, Any]]]


class WeatherTool(BaseTool):
    """查询天气（取数能力由外部注入）"""

    name = "weather"
    description = "查询某个城市的天气"
    risk_level = "low"
    timeout = 15
    params_schema = {
        "type": "object",
        "properties": {
            "city": {"type": "string", "description": "城市名，默认当地"},
        },
    }

    def __init__(self, weather_func: Optional[WeatherFunc] = None) -> None:
        self._weather = weather_func

    def execute(self, params: Dict[str, Any]) -> ToolResult:
        city = str(params.get("city") or "").strip()

        if self._weather is None:
            return ToolResult.fail("我这边还没接上天气服务呢", emotion="sad")

        try:
            data = self._weather(city)
        except Exception as e:
            logger.warning("[weather] 查询失败: %s", e)
            return ToolResult.fail("天气查不到呢，稍后再试试~", emotion="sad")

        if not data:
            return ToolResult.fail("没查到天气信息呢", emotion="sad")

        parts = []
        if data.get("city"):
            parts.append(str(data["city"]))
        if data.get("desc"):
            parts.append(str(data["desc"]))
        if data.get("temp") is not None:
            parts.append(f"{data['temp']}度")
        if data.get("temp_range"):
            parts.append(str(data["temp_range"]))

        summary = "，".join(parts) if parts else "查到天气了"
        return ToolResult.ok(data=data, summary=summary, emotion="talk")


# ══════════════════════════════════════════════════════
#  注册辅助
# ══════════════════════════════════════════════════════

def all_productivity_tools(
    translate_func: Optional[TranslateFunc] = None,
    weather_func: Optional[WeatherFunc] = None,
    on_reminder_due: Optional[ReminderCallback] = None,
    reminder_store: Optional[Any] = None,
) -> List[BaseTool]:
    """构造全部生产力工具实例

    Args:
        reminder_store: 提醒持久化后端（P4-A2 / D13）；None = 纯内存，与加持久化前一致
    """
    return [
        CalculateTool(),
        TranslateTool(translate_func),
        ReminderTool(on_due=on_reminder_due, store=reminder_store),
        WeatherTool(weather_func),
    ]
