"""LLM 规划器（LLMPlanner）

DSH 能力 Seam 三角的 Provider：
- Definition: agent/seams/planner.py
- Provider:   本模块（LLM 分解 + 严格校验）
- Consumer:   agent/pipeline.py（经 HybridPlanner 消费）

## 职责

规则配方覆盖不到的说法（"把下载目录收拾一下"、"整理并归档旧合同"）需要真语义
理解，交给 LLM 拆步骤。但 **LLM 的输出必须当作不可信输入**处理：

| 风险 | 本模块的处置 |
|------|-------------|
| 返回非 JSON / 包了 Markdown 代码块 | 剥围栏 + 大括号扫描救回；仍失败即放弃 |
| 编造不存在的工具名 | 按可用工具表白名单丢弃该步骤 |
| 步骤数失控（拆出 20 步） | 截断到 max_steps |
| 参数不是对象 | 收敛为空 dict（由工具层给出友好的缺参追问） |
| 一句话拆不出多步 | 返回 None，调用方退回单步路由 |
| 抛异常 / 超时 | 一律返回 None，绝不向上抛 |

关键设计：**校验失败一律"丢弃该步"而不是"整单放弃"**。LLM 常常前两步对、
第三步编造一个工具名 —— 丢掉坏的那步仍然能完成用户目标，整单放弃则等于白花
一次调用。只有当剩余步骤不足 2 步时才判定为规划失败。
"""

import json
import logging
import re
import threading
from typing import Any, Callable, Dict, List, Optional, Sequence

from agent.providers.router.rule_router import (
    PLAN_CONNECTORS,
    PLAN_CONNECTOR_MIN_SIDE,
    PLAN_COUNT_MARKERS,
    PLAN_COUNT_RE,
)
from agent.seams.planner import (
    MAX_PLAN_STEPS,
    Plan,
    PlannerService,
    PlanStep,
)

logger = logging.getLogger(__name__)

#: LLM 单次补全签名：(prompt) -> Optional[str]
LLMOnceCall = Callable[[str], Optional[str]]

#: Markdown 代码围栏（```json ... ```）
_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)

#: 提示词模板
_PROMPT_TEMPLATE = """你是一个任务规划器。把用户的一句话拆成**有序的工具调用步骤**。

可用工具：
{tools}
{dirs}
输出要求（严格遵守）：
1. 只输出一个 JSON 对象，不要解释、不要 Markdown 代码块
2. 格式：{{"steps":[{{"action":"工具名","params":{{}},"description":"这一步做什么"}}]}}
3. 步骤按执行顺序排列，最多 {max_steps} 步
4. 后一步需要使用前一步的产出时，在参数值里写占位符：
   - "${{s1.paths}}"   第1步找到的文件路径列表（**只能**喂给收列表的参数，如 file_delete.targets）
   - "${{s1.paths.0}}" 其中第一个（喂给只收单个路径的参数，如 file_move.source）
   - "${{s1.content}}" 第1步读到的文本内容
   - "${{last.paths}}" 上一步的产出
5. **参数类型要对**：收列表的参数（file_search.dirs / file_delete.targets / file_search.patterns）
   必须给**数组**，例如 `"dirs": ["Downloads"]` —— 写成字符串 `"Downloads"` 会被逐字符解析，
   结果是"静默换成了默认目录"，用户只会觉得"没搜到我说的目录"
6. action 必须是上面列出的工具名之一，不得编造
7. 路径参数只能用上面列出的目录别名/路径，或来自占位符；
   **绝对不要自己编造路径** —— 猜出来的路径（例如 C:/Users/Someone/Downloads）在真实机器上
   不存在，会让整个计划落空
8. **文件操作一律用专用工具，不要用 run_command**：
   搜索用 file_search、移动用 file_move、删除用 file_delete、读取用 file_read。
   尤其**禁止**用 run_command 执行 del / rmdir / rm / Remove-Item 之类的删除命令 ——
   `file_delete` 强制走回收站（可恢复），而命令删除是**永久删除**，项目里绝对不允许
9. 如果这句话不需要多步完成，输出 {{"steps":[]}}

已支持的常见组合（可直接套用）：
{recipes}
{memory}
用户：{text}
JSON："""

#: 未提供目录词表时的兜底文案（仍比让模型自由编路径安全）
_DIRS_FALLBACK = (
    "\n可用目录：未提供（请直接用 Desktop / Documents / Downloads / Pictures "
    "这类目录名，不要编造绝对路径）\n"
)


class LLMPlanner(PlannerService):
    """LLM 规划器（1~3s，需 API）

    用法：
        planner = LLMPlanner(llm_call=llm_once, available_actions=registry.names())
        plan = planner.plan("把下载目录收拾一下")
    """

    capability_name = "planner"
    provider_name = "llm"

    def __init__(
        self,
        llm_call: Optional[LLMOnceCall] = None,
        available_actions: Optional[Sequence[str]] = None,
        tool_schemas: Optional[List[Dict[str, Any]]] = None,
        recipes: Optional[List[Dict[str, Any]]] = None,
        max_steps: int = MAX_PLAN_STEPS,
        max_tools_in_prompt: int = 24,
        llm_timeout: float = 20.0,
    ) -> None:
        """
        Args:
            llm_call: LLM 单次补全；None 时规划器不可用（plan 恒返回 None）
            available_actions: 可用工具名（白名单，用于丢弃编造的步骤）
            tool_schemas: 工具 schema 列表（来自 ToolRegistry.to_llm_schemas()）
            recipes: 规则配方描述（来自 TemplatePlanner.describe_recipes()），
                     作为提示里的few-shot 参考，降低 LLM 拆错概率
            max_steps: 步骤数上限
            max_tools_in_prompt: 提示词里最多列几个工具（防止提示词过长）
            llm_timeout: 单次 LLM 调用的等待上限（秒）——**必须有**，理由见
                         `_call_bounded()`。默认 20s：实测正常路径 2.6s、
                         主端点失败转备用端点 6~12s，偶发劣化曾达 42s
        """
        self._llm = llm_call
        self._available = list(available_actions) if available_actions else []
        self._schemas = list(tool_schemas or [])
        self._recipes = list(recipes or [])
        self._max_steps = max(1, int(max_steps))
        self._max_tools = max(1, int(max_tools_in_prompt))
        self._llm_timeout = max(0.1, float(llm_timeout))

    # ══════════════════════════════════════════════
    #  PlannerService
    # ══════════════════════════════════════════════

    def plan(
        self,
        text: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> Optional[Plan]:
        """让 LLM 把一句话拆成计划

        Returns:
            计划；LLM 不可用 / 输出无法解析 / 有效步骤不足 2 步时返回 None
        """
        if self._llm is None:
            return None

        goal = str(text or "").strip()
        if not goal:
            return None

        ctx = context or {}

        raw = self._call_bounded(self._build_prompt(goal, ctx))
        if not raw:
            return None

        data = _loads_lenient(raw)
        if data is None:
            logger.info("[planner/llm] 输出无法解析为 JSON，放弃规划")
            return None

        available = list(ctx.get("available_actions") or self._available)
        steps = self._validated_steps(data, available)
        if len(steps) < 2:
            logger.info("[planner/llm] 有效步骤不足 2 步（原始 %d 步），放弃规划",
                        len(data.get("steps") or []) if isinstance(data, dict) else 0)
            return None

        plan = Plan(goal=goal, steps=steps, source=self.provider_name)
        plan.truncate(self._max_steps)
        logger.info("[planner/llm] %s", plan)
        return plan

    def can_plan(self, text: str) -> bool:
        """廉价预判：文本是否**看起来**是多步请求

        判定复用规则路由的多步标记词表（连接词 + 计数式标记）——
        「多步」这件事在两个 Provider 里必须有同一个定义，否则
        HybridPlanner 会在"规则说不像、LLM 说像"之间反复横跳。

        这里**刻意宽松**：只要出现"然后/接着/分N步"这类标记就放行，
        精确判定交给 LLM。目的是挡掉"你好呀"这种纯闲聊，而不是替 LLM 做决定。
        """
        norm = re.sub(r"\s+", " ", str(text or "")).strip().lower()
        if not norm:
            return False

        if PLAN_COUNT_RE.search(norm) or any(m in norm for m in PLAN_COUNT_MARKERS):
            return True

        for conn in PLAN_CONNECTORS:
            idx = norm.find(conn)
            while idx >= 0:
                left = norm[:idx].strip()
                right = norm[idx + len(conn):].strip()
                if (len(left) >= PLAN_CONNECTOR_MIN_SIDE
                        and len(right) >= PLAN_CONNECTOR_MIN_SIDE):
                    return True
                idx = norm.find(conn, idx + 1)
        return False

    def describe_recipes(self) -> List[Dict[str, Any]]:
        """本 Provider 持有的配方描述（由构造时注入）"""
        return list(self._recipes)

    # ══════════════════════════════════════════════
    #  内部：受限调用
    # ══════════════════════════════════════════════

    def _call_bounded(self, prompt: str) -> Optional[str]:
        """带超时地调用注入的 LLM 函数

        **为什么必须自己加超时**：注入的 `llm_once` 来自
        `core.app.chat_once` → `chat_stream`，其单次 `requests` 超时是 60s，
        且失败后还会再打一次备用端点。实测一次失败的规划调用耗时 **42 秒**
        —— 而规划发生在**调用线程**里（语音回调 = Qt UI 线程），
        等于把界面冻住 42 秒。

        这里用 daemon 线程 + 有界 `wait()` 把上限收到 `llm_timeout`：
        超时即放弃规划（调用方退回单步/闲聊），线程靠 daemon 属性保证
        不阻塞解释器退出（D7 教训）。

        Returns:
            模型输出；超时/异常/为空时返回 None
        """
        box: Dict[str, Any] = {}
        done = threading.Event()

        def worker() -> None:
            try:
                box["raw"] = self._llm(prompt)          # type: ignore[misc]
            except Exception as e:                       # 实现抛异常 → 放弃规划
                box["err"] = e
            finally:
                done.set()

        thread = threading.Thread(
            target=worker, name="agent-plan-llm", daemon=True,
        )
        thread.start()

        if not done.wait(self._llm_timeout):
            logger.warning(
                "[planner/llm] 调用超过 %.1fs 未返回，放弃规划（避免阻塞调用线程）",
                self._llm_timeout,
            )
            return None

        if "err" in box:
            logger.warning("[planner/llm] 调用失败: %s", box["err"])
            return None
        return box.get("raw")

    # ══════════════════════════════════════════════
    #  内部：提示词
    # ══════════════════════════════════════════════

    def _build_prompt(self, goal: str, context: Dict[str, Any]) -> str:
        """组装提示词"""
        return _PROMPT_TEMPLATE.format(
            tools=self._format_tools(context),
            dirs=self._format_dirs(context),
            max_steps=self._max_steps,
            recipes=self._format_recipes(),
            text=goal,
            memory=self._format_memory(context),
        )

    @staticmethod
    def _format_dirs(context: Dict[str, Any]) -> str:
        """把白名单目录的「别名 = 真实路径」写进提示词

        为什么必须有这一段：不告诉模型真实目录时，它会**自己编一个看起来合理的绝对路径** ——
        真实 LLM 实测产出过 `C:/Users/Username/Downloads`（本机真实路径是
        `C:\\Users\\49046\\Downloads`）。而这种错误**不会报错**：
        `file_search._resolve_dirs` 对不在白名单里的目录是「静默跳过」，
        于是计划第一步就落空，用户只看到"没找到文件"，根本不知道是路径编错了。

        目录词表由管线从安全层白名单注入（`context["dir_aliases"]`），
        所以它永远是这台机器上的真实路径，而不是提示词里写死的示例。
        """
        raw = context.get("dir_aliases")
        pairs = list(raw.items()) if isinstance(raw, dict) else []

        lines: List[str] = []
        for name, path in pairs:
            alias = str(name).strip()
            if not alias:
                continue
            real = str(path).strip()
            lines.append(f"- {alias} = {real}" if real else f"- {alias}")

        if not lines:
            return _DIRS_FALLBACK
        return "\n可用目录（路径参数请用这些别名或路径，不要自己编造）：\n" + "\n".join(lines) + "\n"

    @staticmethod
    def _format_memory(context: Dict[str, Any]) -> str:
        """把长期记忆的偏好提示拼进提示词（无提示时整行省略）

        长期记忆在这里的价值：用户说"整理下下载目录"时，若记忆里有
        "下载的安装包都放软件归档"，拆出的目标目录就能贴合他的习惯，
        而不是泛泛地挪到文档。
        """
        hint = str(context.get("memory_hint") or "").strip()
        return f"\n用户偏好（来自长期记忆，供参考）：{hint}" if hint else ""

    def _format_tools(self, context: Dict[str, Any]) -> str:
        """把工具 schema 压成紧凑的工具清单

        优先用调用方临时传入的 schema（工具集可能在装配后变化）。
        """
        schemas = context.get("tool_schemas") or self._schemas
        lines: List[str] = []

        for schema in schemas[: self._max_tools]:
            fn = schema.get("function") if isinstance(schema, dict) else None
            fn = fn if isinstance(fn, dict) else schema
            if not isinstance(fn, dict):
                continue
            name = str(fn.get("name") or "").strip()
            if not name:
                continue
            desc = str(fn.get("description") or "").strip()
            props = _param_names(fn.get("parameters"))
            args = f"（参数：{', '.join(props)}）" if props else "（无参数）"
            lines.append(f"- {name}{args} — {desc}")

        if lines:
            return "\n".join(lines)

        # 没有 schema 时退化成纯工具名清单
        names = list(context.get("available_actions") or self._available)
        if names:
            return "\n".join(f"- {n}" for n in names[: self._max_tools])
        return "（无可用工具信息）"

    def _format_recipes(self) -> str:
        """把规则配方压成一行一条的参考"""
        if not self._recipes:
            return "（无）"
        lines: List[str] = []
        for r in self._recipes[:6]:
            if not isinstance(r, dict):
                continue
            name = str(r.get("name") or r.get("recipe_id") or "").strip()
            desc = str(r.get("description") or "").strip()
            req = r.get("requires") or []
            req_text = f"[{' → '.join(str(x) for x in req)}]" if req else ""
            lines.append(f"- {name}：{desc}{req_text}")
        return "\n".join(lines) if lines else "（无）"

    # ══════════════════════════════════════════════
    #  内部：输出校验
    # ══════════════════════════════════════════════

    def _validated_steps(
        self,
        data: Any,
        available: List[str],
    ) -> List[PlanStep]:
        """把 LLM 输出收敛成合法步骤（丢弃坏步骤，保留好步骤）"""
        if not isinstance(data, dict):
            return []

        raw_steps = data.get("steps")
        if not isinstance(raw_steps, list):
            return []

        steps: List[PlanStep] = []
        for idx, entry in enumerate(raw_steps, start=1):
            if not isinstance(entry, dict):
                continue

            action = str(entry.get("action") or "").strip()
            if not action or not self._action_allowed(action, available):
                logger.debug("[planner/llm] 丢弃步骤 %d：非法工具名 %r", idx, action)
                continue

            params = entry.get("params")
            if not isinstance(params, dict):
                params = {}

            depends = entry.get("depends_on")
            depends_on = (
                [str(d) for d in depends if isinstance(d, (str, int))]
                if isinstance(depends, list) else []
            )

            steps.append(
                PlanStep(
                    action=action,
                    params=dict(params),
                    step_id=f"s{idx}",
                    description=str(entry.get("description") or "").strip(),
                    depends_on=depends_on,
                )
            )
        return steps

    def _action_allowed(self, action: str, available: List[str]) -> bool:
        """工具名是否在可用表内

        可用表为空表示"不知道有哪些工具"（未注入 registry），
        此时放行并告警 —— 交给管线在提交前用真实注册表拦下，
        比在这里静默吞掉用户的请求更容易排查。
        """
        if not available:
            logger.debug("[planner/llm] 未注入可用工具表，放行 action=%s", action)
            return True
        return action in available


# ══════════════════════════════════════════════
#  模块级辅助
# ══════════════════════════════════════════════


def _param_names(parameters: Any) -> List[str]:
    """从 JSON Schema 里取参数名列表"""
    if not isinstance(parameters, dict):
        return []
    props = parameters.get("properties")
    if not isinstance(props, dict):
        return []
    return [str(k) for k in props.keys()]


def _loads_lenient(raw: str) -> Optional[Dict[str, Any]]:
    """尽最大努力把 LLM 输出解析成 JSON 对象

    依次尝试：
    1. 直接 `json.loads`
    2. 剥掉 Markdown 代码围栏后再解析
    3. 从文本里扫出一个**括号配平**的 `{...}` 片段解析
       （LLM 常在 JSON 前后加"好的，这是计划："之类的废话）

    Returns:
        解析出的字典；全部失败返回 None
    """
    text = str(raw or "").strip()
    if not text:
        return None

    for candidate in _json_candidates(text):
        try:
            data = json.loads(candidate)
        except (ValueError, TypeError):
            continue
        if isinstance(data, dict):
            return data
    return None


def _json_candidates(text: str):
    """产出若干候选 JSON 字符串（按可信度降序）"""
    yield text

    for m in _FENCE_RE.finditer(text):
        body = m.group(1).strip()
        if body:
            yield body

    scanned = _scan_object(text)
    if scanned:
        yield scanned


def _scan_object(text: str) -> Optional[str]:
    """扫出第一个括号配平的对象字面量（跳过字符串内的括号）

    手写扫描而不是正则，因为正则无法正确处理
    `{"pattern": "*.{zip,tar}"}` 这种参数值里带花括号的情况。
    """
    start = text.find("{")
    if start < 0:
        return None

    depth = 0
    in_string = False
    escaped = False

    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue

        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start: i + 1]
    return None
