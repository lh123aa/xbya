"""规划能力接口定义（Service Definition）—— 偿还 D5（多步任务规划）

DSH 能力 Seam 三角的 Definition 角色：
- Definition: 本模块（PlannerService / Plan / PlanStep）
- Provider:   agent/providers/planner/{template_planner,llm_planner,hybrid_planner}.py
- Consumer:   agent/pipeline.py（多步执行）

## 为什么需要单独一层

`RouterService` 的契约是「一句话 → 一个 AgentCommand」，这在单步指令上极准
（规则路由 100%），但「把下载目录里的安装包都挪到软件归档」这类目标天然是
**多步**的：先搜、再筛、再移。硬塞进一个 action 只会让工具参数越来越玄学。

于是把「拆步骤」独立成能力：Router 仍负责「这是什么意图」，Planner 负责
「这件事要分几步做、每步用什么工具、步与步之间怎么传数据」。

## 职责边界（重要）

Planner **只拆步骤，不执行**，也**不做安全检查**：
- 执行顺序、并发、确认挂起/恢复 —— 全部由管线负责
- 每一步仍要各自过 `SafetyService.check()`，与单步指令走**完全相同**的
  安全通道。规划器不能成为绕过确认的后门。

## 数据在步骤间怎么流动

参数值里可以写占位符 `${步骤ID.字段}`，由管线在提交该步前解析：

    PlanStep(step_id="s1", action="file_search", params={"pattern": "*.zip"})
    PlanStep(step_id="s2", action="file_move",
             params={"targets": "${s1.paths}", "dest": "归档"})

`${last}` 指代前一步。解析规则见 `resolve_placeholders()`。
"""

import re
from abc import abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from core.kernel.service import Service

#: 单个计划的步骤数上限（防止 LLM 拆出几十步把用户拖死）
MAX_PLAN_STEPS = 8

#: 占位符语法：`${步骤ID.字段}`；步骤 ID 允许字母数字与下划线/连字符
PLACEHOLDER_RE = re.compile(r"\$\{([A-Za-z0-9_\-]*(?:\.[A-Za-z0-9_\-]+)*)\}")


class PlanStatus(str, Enum):
    """计划执行状态"""

    PENDING = "pending"        # 已规划，未开始
    RUNNING = "running"        # 执行中
    SUCCESS = "success"        # 全部步骤成功
    PARTIAL = "partial"        # 部分步骤失败后中止
    FAILED = "failed"          # 首步即失败
    HALTED = "halted"          # 因某步需要确认而挂起
    CANCELLED = "cancelled"    # 被用户取消或打断


@dataclass(slots=True)
class PlanStep:
    """计划中的一步

    Attributes:
        action: 工具名
        params: 工具参数（可含 `${...}` 占位符）
        step_id: 步骤标识（占位符引用用）；为空时由管线按序号补 `s1`/`s2`
        description: 人类可读的步骤说明（播报与日志用）
        depends_on: 依赖的步骤 ID（仅用于展示与校验，执行仍是顺序的）
    """

    action: str
    params: Dict[str, Any] = field(default_factory=dict)
    step_id: str = ""
    description: str = ""
    depends_on: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not isinstance(self.params, dict):
            self.params = {}

    def refs(self) -> List[str]:
        """本步参数里引用的全部步骤 ID（去重，保持出现顺序）"""
        found: List[str] = []
        for value in _walk_values(self.params):
            if not isinstance(value, str):    # pragma: no cover - _walk_values 只产出 str
                continue
            for m in PLACEHOLDER_RE.finditer(value):
                head = m.group(1).split(".", 1)[0]
                if head and head not in found:
                    found.append(head)
        return found


@dataclass
class Plan:
    """一个多步计划

    Attributes:
        goal: 用户目标原文
        steps: 顺序执行的步骤列表
        source: 产出该计划的 Provider（"template" / "llm"）
        plan_id: 计划标识
    """

    goal: str = ""
    steps: List[PlanStep] = field(default_factory=list)
    source: str = "template"
    plan_id: str = ""

    def __post_init__(self) -> None:
        self.steps = [s for s in (self.steps or []) if isinstance(s, PlanStep)]
        self._assign_ids()

    def _assign_ids(self) -> None:
        """给缺 ID 的步骤补 `s1`/`s2`…（保持调用方传入的不变）"""
        for idx, step in enumerate(self.steps, start=1):
            if not step.step_id:
                step.step_id = f"s{idx}"

    def is_multi_step(self) -> bool:
        """是否真的不止一步（只有一步时管线退回普通单步路径）"""
        return len(self.steps) > 1

    def action_names(self) -> List[str]:
        """计划涉及的工具名（去重，保持顺序）"""
        names: List[str] = []
        for step in self.steps:
            if step.action and step.action not in names:
                names.append(step.action)
        return names

    def truncate(self, max_steps: int = MAX_PLAN_STEPS) -> "Plan":
        """截断到最多 max_steps 步（原地修改并返回自身）"""
        limit = max(1, int(max_steps))
        if len(self.steps) > limit:
            self.steps = self.steps[:limit]
            self._assign_ids()
        return self

    def unknown_actions(self, available: List[str]) -> List[str]:
        """找出计划里当前工具表不认识的工具名"""
        known = set(available or ())
        return [a for a in self.action_names() if a not in known]

    def __len__(self) -> int:
        return len(self.steps)

    def __str__(self) -> str:
        chain = " → ".join(f"{s.action}" for s in self.steps)
        return f"<Plan {self.source} {len(self.steps)}步: {chain}>"


class PlannerService(Service):
    """规划能力接口

    实现者约定：
    - `plan()` 不得抛异常；拆不出来就返回 None（调用方退回单步路由）
    - `plan()` 不得执行任何工具、不得产生副作用（纯函数）
    - 返回值中的 action **必须**是当前工具表里真实存在的工具名
      （调用方会丢弃未知步骤，但不该指望它兜底）
    - 不得返回空计划；单步计划是合法的，由调用方决定是否值得走多步通道
    """

    capability_name = "planner"

    @abstractmethod
    def plan(self, text: str, context: Optional[Dict[str, Any]] = None) -> Optional[Plan]:
        """把一句话拆成多步计划

        Args:
            text: 用户输入原文
            context: 上下文，可含 `available_actions`（可用工具名列表）等

        Returns:
            计划；无法拆解或无需拆解时返回 None
        """

    def can_plan(self, text: str) -> bool:
        """廉价预判：这段文本是否值得尝试规划

        Consumer（管线）在只有 LLM 规划器可用时用它挡掉绝大部分请求，
        避免每句话都花一次 LLM 调用。

        Returns:
            True=值得规划；默认 False（纯保守）
        """
        return False

    def describe_recipes(self) -> List[Dict[str, Any]]:
        """导出配方描述（供 LLM 规划器复用，避免两处维护词表）"""
        return []


# ══════════════════════════════════════════════
#  占位符解析（Provider 与 Consumer 共用）
# ══════════════════════════════════════════════


def _walk_values(params: Any):
    """深度遍历参数里的全部字符串值（含列表/字典嵌套）"""
    if isinstance(params, str):
        yield params
    elif isinstance(params, dict):
        for v in params.values():
            yield from _walk_values(v)
    elif isinstance(params, (list, tuple)):
        for v in params:
            yield from _walk_values(v)


def _item_path(item: Any) -> Optional[str]:
    """从实体条目里取出路径（兼容 dict / str）"""
    if isinstance(item, dict):
        return item.get("path") or item.get("name") or None
    if isinstance(item, str):
        return item
    return None


def extract_field(value: Any, field_name: str) -> Any:
    """从步骤产出里取出字段

    支持的取值形态与字段：
    - dict：直接按键取值（`${s1.count}`）
    - list：`paths`→各项路径列表、`names`→各项名称列表、纯数字→第 N 项
    - 其它（标量）：字段名无意义时原样返回该标量

    Returns:
        取到的值；取不到返回 None
    """
    if not field_name:
        return value

    if isinstance(value, dict):
        return value.get(field_name)

    if isinstance(value, (list, tuple)):
        if field_name == "paths":
            return [p for p in (_item_path(it) for it in value) if p]
        if field_name == "names":
            return [
                it.get("name") if isinstance(it, dict) else str(it)
                for it in value
            ]
        if field_name.isdigit():
            idx = int(field_name)
            return value[idx] if 0 <= idx < len(value) else None
        return None

    return value


def resolve_placeholders(
    params: Dict[str, Any],
    results: Dict[str, Any],
) -> Dict[str, Any]:
    """把参数里的 `${步骤ID.字段}` 替换为前序步骤的产出

    Args:
        params: 工具参数（**不修改**原对象，返回新字典）
        results: `{步骤ID: 该步结果数据}`；额外支持键 `"last"` 指代前一步

    解析规则：
    - 整个取值恰好是一个占位符 → **保留原生类型**（列表仍是列表）
    - 占位符嵌在更长文本中 → 转成字符串拼接
    - 解析不出来（步骤不存在/字段取不到）→ 保留占位符原文，
      交由工具层报错或追问，**绝不抛异常**

    Returns:
        替换后的新参数字典
    """
    if not isinstance(params, dict):
        return {}
    if not results:
        results = {}

    def resolve_one(value: Any) -> Any:
        if isinstance(value, dict):
            return {k: resolve_one(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [resolve_one(v) for v in value]
        if not isinstance(value, str) or "${" not in value:
            return value

        whole = PLACEHOLDER_RE.fullmatch(value)
        if whole is not None:
            resolved = _lookup(whole.group(1), results)
            return value if resolved is _MISSING else resolved

        def sub(m: "re.Match") -> str:
            resolved = _lookup(m.group(1), results)
            return m.group(0) if resolved is _MISSING else _stringify(resolved)

        return PLACEHOLDER_RE.sub(sub, value)

    return {k: resolve_one(v) for k, v in params.items()}


class _Missing:
    """解析失败的哨兵（区别于"字段值恰好是 None"）"""

    __slots__ = ()

    def __repr__(self) -> str:
        return "<MISSING>"


_MISSING = _Missing()


def _lookup(path: str, results: Dict[str, Any]) -> Any:
    """按 `步骤ID.字段.子字段…` 逐段取值"""
    parts = [p for p in path.split(".") if p]
    if not parts:
        return _MISSING

    step_id, fields = parts[0], parts[1:]
    if step_id not in results:
        return _MISSING

    value: Any = results[step_id]
    for fname in fields:
        value = extract_field(value, fname)
        if value is None:
            return _MISSING
    return value


def _stringify(value: Any) -> str:
    """把解析结果转成可嵌入文本的字符串"""
    if isinstance(value, (list, tuple)):
        return "、".join(str(v) for v in value)
    return str(value)
