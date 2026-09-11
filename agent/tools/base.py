"""工具框架：BaseTool / ToolResult / 参数校验

工具的三种角色定位（DSH Consumer）：
- 工具消费能力（SafetyService / 文件系统等）
- 工具被 ToolRegistry 注册
- 工具的 params_schema 可导出为 LLM function calling schema

依赖约束：本模块只依赖标准库，不引入 jsonschema
（AGENTS.md 4.1：P0 阶段依赖最小化）。
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class ParamError(ValueError):
    """工具参数校验错误

    Attributes:
        tool_name: 出错工具名
        field: 出错字段（可选）
    """

    def __init__(self, tool_name: str, message: str, field_name: str = "") -> None:
        self.tool_name = tool_name
        self.field = field_name
        prefix = f"参数错误 [{tool_name}]"
        if field_name:
            prefix += f".{field_name}"
        super().__init__(f"{prefix}: {message}")


class ToolError(RuntimeError):
    """工具执行错误

    Attributes:
        tool_name: 出错工具名
        user_message: 面向用户的友好文案（可直接播报）
    """

    def __init__(self, tool_name: str, message: str, user_message: str = "") -> None:
        self.tool_name = tool_name
        self.user_message = user_message or message
        super().__init__(message)


@dataclass(slots=True)
class ToolResult:
    """工具执行结果

    这是工具层的返回值，会被 Executor 转换为 AgentResult。

    Attributes:
        success: 是否成功
        data: 结构化数据（列表/字典/标量）
        summary: 人类可读摘要（模板或 LLM 润色前的原始描述）
        error: 错误信息（success=False 时）
        emotion: 建议的情绪标签（驱动动效）
        truncated: 结果是否被截断（超出上限）
        count: 结果条目数（便于摘要器统计）
    """

    success: bool
    data: Any = None
    summary: str = ""
    error: str = ""
    emotion: str = "talk"
    truncated: bool = False
    count: int = 0

    @classmethod
    def ok(
        cls,
        data: Any = None,
        summary: str = "",
        count: Optional[int] = None,
        **kwargs: Any,
    ) -> "ToolResult":
        """构造成功结果

        Args:
            data: 结构化数据
            summary: 摘要文案
            count: 条目数；为 None 时自动从 data 推导
        """
        if count is None:
            if isinstance(data, (list, tuple)):
                count = len(data)
            elif data is None:
                count = 0
            else:
                count = 1
        return cls(success=True, data=data, summary=summary, count=count, **kwargs)

    @classmethod
    def fail(cls, error: str, summary: str = "", **kwargs: Any) -> "ToolResult":
        """构造失败结果"""
        return cls(
            success=False,
            error=error,
            summary=summary or error,
            emotion=kwargs.pop("emotion", "sad"),
            **kwargs,
        )

    @classmethod
    def empty(cls, summary: str, **kwargs: Any) -> "ToolResult":
        """构造空结果（成功但无数据）"""
        return cls.ok(data=[], summary=summary, count=0, **kwargs)


#: JSON Schema 子集 → Python 类型
#:
#: 布尔是 int 的子类，所以 `integer` 要单独先判（见 `describe_value_problem`）。
SCHEMA_TYPE_MAP: Dict[str, Any] = {
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "array": list,
    "object": dict,
}


def describe_value_problem(value: Any, spec: Any) -> Optional[str]:
    """按 schema 检查一个值，返回**可读的问题说明**；没问题返回 `None`

    ## 为什么是一个模块级函数（P5-A2 / D17）

    "这个值符不符合 schema"这件事，现在有**两个调用方**：

    | 调用方 | 时机 | 用途 |
    |--------|------|------|
    | `BaseTool.validate_params` | **执行期** | 不符就抛 `ParamError`，工具不执行 |
    | `LLMPlanner._validated_steps` | **规划期** | 不符就丢弃该步骤，避免计划带着坏参数往下走 |

    两处必须用**同一套判据**。刻意不写两份 —— 本项目吃过"复刻一份判据"的亏：
    `tools/measure_asr_stimulus.py` 曾经自己复刻了一份"确认语子串匹配"，
    于是**报出产品已经不会犯的错**，还把假象记在了别的东西账上（见 P4-C2）。
    判据一旦复刻就会漂移，而漂移的检测成本远高于共用。

    Args:
        value: 待检查的值
        spec: 该字段的 schema 片段（`{"type": ..., "enum": ..., ...}`）。

            **不是 `dict` 时一律放行**（返回 `None`）：这个判断放在这里而不是留给
            两个调用方各写一遍 —— 写两遍就会出现"两处对畸形 schema 的处理不一致"，
            而规划期的容错取向是**不确定时不假装能判**（与"不知道有哪些工具就放行"
            同一条）。`spec=None` / `spec="string"` 这类畸形输入来自 LLM 侧，
            不能让它们变成"计划被静默丢掉"。

    Returns:
        问题说明（面向开发者，会被 `ParamError` 拼上工具名与字段名）；合法时 `None`
    """
    if not isinstance(spec, dict):
        return None

    # 显式 `None` 一律视为"未提供"。
    #
    # 这是 F 系列缺陷 4 的回归保护，且必须放在这里（共用判据的入口）而不是
    # 留给调用方：LLM 给的 `{"time_range": null}` 曾经让整条计划在第 1 步中止，
    # 而"null = 没填"是 function calling 里最常见的一种表达。
    # `BaseTool.validate_params` 在更上游就把 `None` 当缺省值摘掉了，
    # 所以这条分支在执行期路径上不会被走到。
    if value is None:
        return None

    expected = spec.get("type")

    if expected and expected in SCHEMA_TYPE_MAP:
        py_type = SCHEMA_TYPE_MAP[expected]
        if expected == "integer" and isinstance(value, bool):
            return "期望整数，收到布尔值"
        # `array` 收到字符串要**显式拒绝**，且文案要能照做（P4-B2 / 缺陷 17b）。
        #
        # 为什么单独开一条分支而不是靠通用的类型报错：字符串是**可迭代**的，
        # 一个 "Downloads" 传进 `for item in dirs` 会被逐字符拆开，最后
        # "一个字符都匹配不上" → 调用方静默回退默认目录。也就是说
        # 类型错了却**不报错**，只是范围悄悄变大（实测见 docs/agent/p4-plan.md §B2）。
        # 通用文案只说"期望 array，收到 str"，看不懂的人会去猜；这里直接给出
        # 一个可照抄的写法。逗号串（"Downloads,Documents"）走同一条分支 ——
        # **不拆**，因为"把一句话拆成多个待删目标"正是最危险的宽松转换。
        if expected == "array" and isinstance(value, str):
            return (f'期望数组，收到字符串 {value!r}；即使只有一个值也请写成数组，'
                    f'例如 ["{value}"]')
        if not isinstance(value, py_type):
            return f"类型错误：期望 {expected}，收到 {type(value).__name__}"

    if "enum" in spec and value not in spec["enum"]:
        return f"取值必须是 {spec['enum']} 之一，收到 {value!r}"

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in spec and value < spec["minimum"]:
            return f"小于最小值 {spec['minimum']}"
        if "maximum" in spec and value > spec["maximum"]:
            return f"超过最大值 {spec['maximum']}"

    if isinstance(value, list):
        if "minItems" in spec and len(value) < spec["minItems"]:
            return f"元素少于 {spec['minItems']} 个"
        if "maxItems" in spec and len(value) > spec["maxItems"]:
            return f"元素超过 {spec['maxItems']} 个"

    return None


class BaseTool(ABC):
    """所有工具的基类

    子类必须声明：
        name          工具名（唯一，也是 LLM function 名）
        description   给 LLM 看的描述
        params_schema JSON Schema 风格参数定义
        risk_level    风险等级（low/medium/high/critical）

    子类必须实现：
        execute(params) -> ToolResult
    """

    #: 工具名（唯一标识，供 LLM 调用与注册表索引）
    name: str = ""

    #: 工具描述（供 LLM 理解用途）
    description: str = ""

    #: 参数 schema（JSON Schema 子集：type/properties/required）
    params_schema: Dict[str, Any] = {}

    #: 风险等级（"low" / "medium" / "high" / "critical"）
    risk_level: str = "low"

    #: 单次执行超时秒数
    timeout: int = 30

    #: 是否需要用户确认（由 risk_level 决定，可覆写）
    _confirm_override: Optional[bool] = None

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if not cls.name:
            from core.kernel.service import _to_snake

            cls.name = _to_snake(cls.__name__.removesuffix("Tool"))

    # ── 必须实现 ──

    @abstractmethod
    def execute(self, params: Dict[str, Any]) -> ToolResult:
        """执行工具

        Args:
            params: 已校验的参数

        Returns:
            ToolResult

        Raises:
            ToolError: 执行失败且无法用 ToolResult.fail 表达时
        """
        raise NotImplementedError

    # ── 参数校验 ──

    def validate_params(self, params: Dict[str, Any]) -> None:
        """校验参数（JSON Schema 子集）

        支持的校验：
        - required：必填字段
        - type：string / integer / number / boolean / array / object
        - enum：枚举值
        - items：数组元素类型
        - minimum / maximum：数值范围
        - minItems / maxItems：数组长度

        Args:
            params: 待校验参数

        Raises:
            ParamError: 校验失败
        """
        schema = self.params_schema or {}
        if not schema:
            return

        # 1. 必填字段
        for key in schema.get("required", []):
            if key not in params or params[key] is None:
                raise ParamError(self.name, "缺少必填参数", key)

        # 2. 字段类型与约束
        properties = schema.get("properties", {})
        for key, value in params.items():
            spec = properties.get(key)
            if spec is None or value is None:
                # - 未声明的字段 → 放行（宽松策略）
                # - **显式 None 的可选字段 → 视为"未提供"**：JSON null 在
                #   function calling 里是极常见的"这个参数我没填"表达方式，
                #   若按类型错误拒绝，LLM 给出的 `{"time_range": null}` 会让整条
                #   工具调用失败（实测：LLM 规划器的第 1 步因此报
                #   `期望 string，收到 NoneType`，把计划整单打断）。
                #   必填字段的 None 已在上一步拦下，这里放行是安全的。
                continue
            self._validate_field(key, value, spec)

    def _validate_field(self, key: str, value: Any, spec: Dict[str, Any]) -> None:
        """校验单个字段

        判据本体在模块级 `describe_value_problem()` —— 规划期也会调它，
        两处必须同一套规则（见该函数的说明）。
        """
        problem = describe_value_problem(value, spec)
        if problem:
            raise ParamError(self.name, problem, key)

    # ── 风险与确认 ──

    def preview(self, params: Dict[str, Any]) -> str:
        """生成"将要做什么"的预览文案（用于确认前展示）

        默认实现返回空串。需要用户确认的操作（写/删/移动）应覆写本方法，
        在**不产生副作用**的前提下说明影响范围
        （如删除操作先扫描一遍，报告"找到 12 个文件：a、b、c"）。

        Args:
            params: 工具参数

        Returns:
            预览文案；空串表示无法预览
        """
        return ""

    def confirm_required(self) -> bool:
        """本次调用是否需要用户确认

        默认按风险等级判定：medium 及以上需要确认。
        """
        if self._confirm_override is not None:
            return self._confirm_override
        return self.risk_level in ("medium", "high", "critical")

    # ── LLM 集成 ──

    def to_llm_schema(self) -> Dict[str, Any]:
        """导出为 OpenAI function calling 格式"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.params_schema or {"type": "object", "properties": {}},
            },
        }

    def __repr__(self) -> str:
        return f"<{type(self).__name__} name={self.name!r} risk={self.risk_level}>"
