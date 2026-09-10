"""并行管线协调器

把「用户语音」到「结果反馈」串成事件驱动的管线，核心是**并行**：

    用户说完
      │
      ├─ 0.5s  ASR 完成 → speech.recognized
      ├─ 0.01s 路由完成（本地规则）
      │         │
      │         ├──▶ feedback.ack     立即播确认语（缓存命中 ≈0 延迟）
      │         │
      │         └──▶ task.submitted   同时提交执行（不等待 ack 播完）
      │
      └─ 2.5s  执行完成 → feedback.result

两条路径**互不等待**，把用户感知延迟从「结果延迟」降为「确认语延迟」。

线程模型：
- 事件由语音线程/执行线程发出
- 本协调器不创建线程，只做事件转译
- 回调（executor → result）运行在执行线程内；发出的事件由消费方
  （PetWindow）通过 Qt Signal 中转回 UI 线程
"""

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional

from agent.message import AgentCommand, AgentResult, CommandStatus
from agent.seams.executor import ExecutorService, TaskHandle
from agent.seams.memory import MemoryService
from agent.seams.planner import (
    Plan,
    PlannerService,
    PlanStatus,
    PlanStep,
    resolve_placeholders,
)
from agent.seams.router import RouterService
from agent.seams.safety import SafetyService
from agent.seams.summarizer import SummarizerService
from agent.tools.base import ParamError, ToolResult
from agent.tools.registry import ToolRegistry
from agent.tracker import EntityTracker
from core.kernel.events import EventBus, EventTypes
from services.ack_cache import AckCache, category_for_action

if TYPE_CHECKING:                      # 仅类型标注用，避免运行期循环导入
    from agent.tracker_store import TrackerStore

logger = logging.getLogger(__name__)

#: 会产生"一批文件"从而值得记入实体栈的工具
_FILE_PRODUCING_ACTIONS = {"file_search", "file_list"}

#: 目标来自实体栈但路由不产出目标的工具
_TRACKER_TARGET_ACTIONS = {"file_delete", "file_move", "file_read"}

#: 汇总计划结果文案时，单个步骤摘要的最长保留字数
_PLAN_SUMMARY_CHARS = 60

#: 工具名 → 可播报的动作短语
#:
#: 情景记忆的正文会被**直接念出来**（"上次让你「打开文件」，读好啦"），
#: 所以不能把 `file_read` 这种工具名塞进去。未列出的工具退回工具名 ——
#: 难看但可读，好过沉默。
_ACTION_SPEECH: Dict[str, str] = {
    "file_search": "找文件",
    "file_list": "看目录",
    "file_read": "打开文件",
    "file_rename": "改文件名",
    "file_move": "挪文件",
    "file_delete": "删文件",
    "system_info": "看电脑状态",
    "clipboard": "用剪贴板",
    "open_app": "打开应用",
    "screenshot": "截图",
    "run_command": "执行命令",
    "calculate": "算数",
    "translate": "翻译",
    "reminder": "设提醒",
    "weather": "查天气",
    "web_open": "开网页",
    "web_search": "搜网页",
    "web_read": "读网页",
    "memory_remember": "记东西",
    "memory_recall": "回忆",
    "memory_forget": "忘掉",
    "plan": "做多步任务",
}


@dataclass
class _PlanRun:
    """一次多步计划的执行状态（P3 / D5）

    计划是**顺序**执行的，所以状态机很薄：一个待执行下标 + 已完成步骤的产出。
    `results` 是步骤间传数据的载体，参数里的 `${s1.paths}` 从这里取值。

    Attributes:
        request_id: 贯穿全链路的请求 ID（也是 confirm 时找回本计划的键）
        plan: 计划本体
        index: 待执行步骤下标（0 起）
        done: 已成功完成的步骤数
        results: `{步骤ID: 该步结果数据}`，供后续步骤的占位符解析
        summaries: 已成功步骤的摘要（用于收尾汇总）
        status: 执行状态
        active_step: 正在执行的步骤（回调里需要它）
        approved_step: **已获用户批准**的步骤 ID
                       仅由 `_handle_confirm` 设置 —— 这是防止"未确认就执行"
                       的关键：安全守卫的 `check()` 是**无状态**的，同一份参数
                       永远返回 `confirm_needed=True`，若不用本字段记住批准，
                       恢复时会再次挂起，形成死循环。
        approved_params: 获批时冻结的参数（保证挂起与恢复看到的是同一份参数）
    """

    request_id: str
    plan: Plan
    index: int = 0
    done: int = 0
    results: Dict[str, Any] = field(default_factory=dict)
    summaries: List[str] = field(default_factory=list)
    status: PlanStatus = PlanStatus.RUNNING
    active_step: Optional[PlanStep] = None
    approved_step: str = ""
    approved_params: Optional[Dict[str, Any]] = None

    @property
    def total(self) -> int:
        """计划总步数"""
        return len(self.plan.steps)

    def ref_dict(self) -> Dict[str, Any]:
        """占位符取值表：各步产出 + `last` 指代前一步"""
        table = dict(self.results)
        if self.results:
            # 按计划顺序找最后一个有产出的步骤作为 last
            for step in reversed(self.plan.steps):
                if step.step_id in self.results:
                    table["last"] = self.results[step.step_id]
                    break
        return table



class AgentPipeline:
    """并行管线协调器

    用法：
        pipe = AgentPipeline(bus, router, safety, executor, registry, ack_cache)
        pipe.start()
        bus.emit(EventTypes.SPEECH_RECOGNIZED, text="找一下桌面上的合同", request_id="r1")
        # → 自动发出 feedback.ack 与 feedback.result
        pipe.stop()
    """

    def __init__(
        self,
        bus: EventBus,
        router: RouterService,
        safety: SafetyService,
        executor: ExecutorService,
        registry: ToolRegistry,
        ack_cache: Optional[AckCache] = None,
        enabled: bool = True,
        summarizer: Optional[SummarizerService] = None,
        tracker: Optional[EntityTracker] = None,
        tracker_store: Optional["TrackerStore"] = None,
        async_refine: bool = True,
        refine_timeout: float = 10.0,
        planner: Optional[PlannerService] = None,
        memory: Optional[MemoryService] = None,
    ) -> None:
        """
        Args:
            bus: 事件总线（与 Voice Layer 共享）
            router: 意图路由
            safety: 安全守卫
            executor: 任务执行器
            registry: 工具注册表
            ack_cache: 确认语缓存（可选；None 时不发即时确认语）
            enabled: 总开关（False 时全部语音转 chat，走原 LLM 路径）
            summarizer: 结果摘要器（可选；None 时直接用工具产出的 summary）
            tracker: 实体追踪器（可选；None 时不做指代消解）
            tracker_store: 实体栈持久化后端（可选；None 时仅内存）
            async_refine: 异步润色（P2-2）；False 时回退"同步等 LLM"的旧行为
            refine_timeout: 单个润色任务的最长等待（释放时 join 用）
            planner: 多步规划器（P3；可选；None 时 action="plan" 一律转闲聊）
            memory: 长期记忆（P3；可选；None 时既不记情景也不取偏好提示）
        """
        self._bus = bus
        self._router = router
        self._safety = safety
        self._executor = executor
        self._registry = registry
        self._ack = ack_cache
        self._enabled = enabled
        self._summarizer = summarizer
        self._tracker = tracker if tracker is not None else EntityTracker()
        self._tracker_store = tracker_store
        self._planner = planner
        self._memory = memory

        # ── 多步计划（P3）──
        # 按 request_id 索引在途计划；计划是顺序执行的，状态很薄
        self._plans: Dict[str, _PlanRun] = {}
        self._plan_lock = threading.Lock()


        # ── 异步润色（P2-2）──
        # 每个待润色请求起一个 daemon 线程：不占用工具执行线程池（否则后续指令会被
        # 排队拖慢），daemon 属性保证解释器退出时不被 join（避免 D7 类退出竞态）。
        self._async_refine = bool(async_refine)
        self._refine_timeout = max(0.1, float(refine_timeout))
        self._refine_stop = threading.Event()
        self._refine_lock = threading.Lock()
        self._refine_threads: Dict[str, threading.Thread] = {}
        self._refine_cancelled: set = set()

        self._disposers: List[Callable[[], None]] = []
        self._started = False

        # 统计（用于验收：延迟、成功率）
        self._stats: Dict[str, Any] = {
            "commands": 0,
            "tool_calls": 0,
            "chats": 0,
            "rejected": 0,
            "confirms": 0,
            "interrupts": 0,
            "summarized": 0,
            "refined": 0,
            "refine_skipped": 0,
            "plans": 0,
            "plan_steps": 0,
            "plan_halted": 0,
            "plan_failed": 0,
            "plan_cancelled": 0,
            "latency_ack_ms": [],
            "latency_result_ms": [],
        }
        self._request_t0: Dict[str, float] = {}

    # ══════════════════════════════════════════════
    #  生命周期
    # ══════════════════════════════════════════════

    def start(self) -> None:
        """订阅事件，启动管线"""
        if self._started:
            return

        self._disposers.append(
            self._bus.on(EventTypes.SPEECH_RECOGNIZED, self._on_speech)
        )
        self._disposers.append(
            self._bus.on(EventTypes.SPEECH_INTERRUPTED, self._on_interrupt)
        )
        self._started = True
        logger.info("[pipeline] 并行管线已启动 (enabled=%s)", self._enabled)

    def stop(self) -> None:
        """取消订阅，停止管线（含等待在途润色线程收尾）"""
        # 在途计划先作废：管线都停了，不该再有步骤被提交
        for rid in list(self._plans.keys()):
            self._cancel_plan(rid, reason="管线停止", announce=False)
        self._shutdown_refine()
        for d in self._disposers:
            try:
                d()
            except Exception:
                pass
        self._disposers.clear()
        self._started = False
        logger.info("[pipeline] 并行管线已停止")

    @property
    def started(self) -> bool:
        return self._started

    def set_enabled(self, enabled: bool) -> None:
        """开关管线（False 时全部转 chat，保留原 LLM 路径）"""
        self._enabled = bool(enabled)
        logger.info("[pipeline] enabled → %s", self._enabled)

    # ══════════════════════════════════════════════
    #  事件入口
    # ══════════════════════════════════════════════

    def _on_speech(self, event) -> None:
        """处理语音识别结果"""
        text = event.data.get("text", "")
        request_id = event.data.get("request_id") or self._new_rid()
        source = event.data.get("source", "voice")

        # 用单调高精度时钟：time.time() 在 Windows 上分辨率约 15.6ms，
        # 会让"亚毫秒即完成"的请求算出 elapsed=0，延迟分位数失去意义。
        self._request_t0[request_id] = time.perf_counter()

        try:
            self.handle_text(text, request_id, source)
        except Exception as e:
            logger.error("[pipeline] 处理语音失败: %s", e, exc_info=True)
            self._emit_result(
                request_id,
                ToolResult.fail("我这边出了点小问题，你再试一次好吗？", emotion="sad"),
                action="unknown",
            )

    def _on_interrupt(self, event) -> None:
        """处理打断：取消该请求下的全部任务"""
        request_id = event.data.get("request_id", "")
        self._stats["interrupts"] += 1

        if request_id:
            n = self._executor.cancel_by_request(request_id)
            self._cancel_refine(request_id)      # 在途润色结果作废，不再补播
            self._cancel_plan(request_id, reason="被打断", announce=False)
            logger.info("[pipeline] 打断 %s：取消 %d 个任务", request_id, n)
        else:
            n = self._executor.cancel_all()
            with self._refine_lock:
                self._refine_cancelled.update(self._refine_threads.keys())
            # 无 request_id 的打断 = 全部停下
            for rid in list(self._plans.keys()):
                self._cancel_plan(rid, reason="被打断", announce=False)
            logger.info("[pipeline] 打断（无 request_id）：取消全部 %d 个任务", n)

    # ══════════════════════════════════════════════
    #  核心流程
    # ══════════════════════════════════════════════

    def handle_text(
        self,
        text: str,
        request_id: Optional[str] = None,
        source: str = "voice",
    ) -> AgentCommand:
        """处理一段文本（可被测试直接调用）

        Returns:
            路由得到的 AgentCommand
        """
        request_id = request_id or self._new_rid()

        # 记录起始时刻（语音入口已记过；直接调用 handle_text 时在此补记），
        # 否则 elapsed_ms 会恒为 0，延迟统计失真。
        self._request_t0.setdefault(request_id, time.perf_counter())

        # ── 1. 路由 ──
        context = {
            "has_pending_confirm": self._safety.pending_count() > 0,
            "source": source,
            # 偏好提示（P3）：让路由/规划知道"用户之前说过什么"。
            # 无记忆实现时为空串，规则路由忽略该键，行为与 P2 一致。
            "memory_hint": self._memory_hint(text),
        }
        command = self._router.route(text, context)
        command.request_id = request_id
        self._stats["commands"] += 1

        logger.info("[pipeline] %s", command)

        # ── 2. 确认/取消 ──
        if command.action == "cancel":
            self._handle_cancel(request_id)
            return command
        if command.action == "confirm":
            self._handle_confirm(request_id)
            return command

        # ── 3. 闲聊 / 管线关闭 → 交回 Voice Layer 走 LLM ──
        if command.is_chat() or not self._enabled:
            self._stats["chats"] += 1
            self._bus.emit(
                "pipeline.chat",
                request_id=request_id,
                text=text,
                confidence=command.confidence,
            )
            return command

        # ── 4. 多步计划（P3 / D5）──
        #     放在安全检查**之前**：计划本身不是可执行动作，
        #     它的每一步会在 _advance_plan 里各自过一遍 check()，
        #     所以走这里不会绕过任何安全关卡。
        if command.action == "plan":
            if self._try_start_plan(request_id, command):
                return command
            # 规划不出来 → 退回闲聊，与不识别多步时的结局一致
            self._stats["chats"] += 1
            self._bus.emit(
                "pipeline.chat",
                request_id=request_id,
                text=text,
                confidence=command.confidence,
            )
            return command

        # ── 5. 指代消解（"第一个" / "那些" / 缺目标）──
        self._resolve_references(command)

        # ── 6. 安全检查 ──
        verdict = self._safety.check(command.action, command.params)

        if verdict.rejected:
            self._stats["rejected"] += 1
            self._safety.audit(
                command.action, command.params, success=False, detail=verdict.reason
            )
            self._emit_result(
                request_id,
                ToolResult.fail(verdict.reason, emotion="surprised"),
                action=command.action,
            )
            return command

        if verdict.confirm_needed:
            self._stats["confirms"] += 1
            preview = self._tool_preview(command.action, command.params)
            question = self._safety.request_confirm(
                request_id, command.action, command.params, preview=preview
            )
            # 确认问句是典型长尾短语（含文件名/大小），走 AckCache 动态 LRU：
            # 首次实时合成，之后同样的问句直接命中，不再等 TTS。
            audio = self._ack.get_or_synthesize(question) if self._ack is not None else None
            self._bus.emit(
                EventTypes.FEEDBACK_CONFIRM,
                request_id=request_id,
                question=question,
                risk=verdict.risk,
                action=command.action,
                preview=preview,
                audio=audio,          # None 表示未缓存，消费方需实时合成
            )
            logger.info("[pipeline] 待确认: %s", question)
            return command

        # ── 7. 并行发射：即时确认语 + 任务提交 ──
        self._emit_ack(request_id, command.action, command.params)
        self._submit(request_id, command)
        return command

    # ══════════════════════════════════════════════
    #  多步计划执行（P3 / D5）
    # ══════════════════════════════════════════════

    def _try_start_plan(self, request_id: str, command: AgentCommand) -> bool:
        """尝试把 action="plan" 的命令交给规划器并启动执行

        Returns:
            True=已启动（或已挂起待确认）；False=规划失败，调用方应退回闲聊
        """
        if self._planner is None:
            logger.info("[pipeline] action=plan 但未装配规划器，转闲聊")
            return False

        goal = str(command.params.get("goal") or command.raw_text or "").strip()
        if not goal:
            return False

        context = {
            "available_actions": self._registry.names(),
            "tool_schemas": self._safe_tool_schemas(),
            "has_pending_confirm": self._safety.pending_count() > 0,
            "source": command.source,
            # 偏好提示（P3）：LLM 规划器会把它写进提示词，让拆步骤贴合用户习惯
            "memory_hint": self._memory_hint(goal),
            # 目录词表：把白名单的「别名 = 真实路径」交给 LLM 规划器，
            # 否则模型会自己编绝对路径（实测 `C:/Users/Username/Downloads`），
            # 而不在白名单里的目录会被 file_search 静默跳过 → 计划第一步就落空
            "dir_aliases": self._safe_dir_aliases(),
        }

        try:
            plan = self._planner.plan(goal, context)
        except Exception as e:                      # 规划器自身已兜底，这里再拦一道
            logger.warning("[pipeline] 规划失败: %s", e)
            plan = None

        # 规划器可能产出未知工具（LLM 会编），提交前用真实注册表核对
        if plan is not None:
            unknown = plan.unknown_actions(self._registry.names())
            if unknown:
                logger.info("[pipeline] 计划包含未注册工具 %s，放弃规划", unknown)
                return False

        if plan is None or not plan.is_multi_step():
            logger.info("[pipeline] 未能拆出多步计划: %r", goal[:40])
            return False

        run = _PlanRun(request_id=request_id, plan=plan)
        with self._plan_lock:
            self._plans[request_id] = run

        self._stats["plans"] += 1
        self._emit_ack(request_id, "plan", command.params)
        self._bus.emit(
            EventTypes.PLAN_STARTED,
            request_id=request_id,
            steps=[
                {"step_id": s.step_id, "action": s.action, "description": s.description}
                for s in plan.steps
            ],
            # 注意：不能叫 `source` —— EventBus.emit(event_type, source="", **data)
            # 的第二个形参就叫 source，同名关键字会被它吃掉而不进事件负载。
            plan_source=plan.source,
            total=len(plan.steps),
        )
        logger.info("[pipeline] 计划启动 (%s, %d 步): %s",
                    plan.source, len(plan.steps), goal[:40])

        self._advance_plan(run)
        return True

    def _advance_plan(self, run: _PlanRun) -> None:
        """推进计划：提交下一步；没有剩余步骤则收尾

        计划是**严格顺序**执行的（不并发）：
        - 文件类步骤几乎必然有数据依赖（先搜到才能移），并发只会引入竞态
        - 顺序执行让"遇确认挂起/恢复"的语义简单可验证

        本方法可能被两条路径调用：初始启动（调用线程）与某步完成后的回调
        （执行线程）。因此必须容忍"计划已被取消/已完成"的情况。
        """
        if run.status != PlanStatus.RUNNING:
            return

        if run.index >= run.total:
            self._finish_plan(run, PlanStatus.SUCCESS)
            return

        step = run.plan.steps[run.index]
        params = self._resolve_step_params(run, step)

        # ── 逐步安全检查（规划器不能成为绕过确认的后门）──
        try:
            verdict = self._safety.check(step.action, params)
        except Exception as e:
            logger.warning("[pipeline] 计划步骤安全检查失败: %s", e)
            self._abort_plan(
                run, PlanStatus.FAILED,
                ToolResult.fail("这一步我没把握，先停下来啦~", emotion="sad"),
            )
            return

        if verdict.rejected:
            self._stats["rejected"] += 1
            self._safety.audit(
                step.action, params, success=False, detail=verdict.reason
            )
            self._abort_plan(
                run, PlanStatus.FAILED,
                ToolResult.fail(verdict.reason, emotion="surprised"),
            )
            return

        # ── 需要确认且本步尚未获批 → 挂起 ──
        if verdict.confirm_needed and run.approved_step != step.step_id:
            self._halt_plan(run, step, params, verdict)
            return

        # ── 提交本步 ──
        run.approved_step = ""            # 批准标记用掉即清，后续步骤需另行确认
        run.approved_params = None
        run.active_step = step
        run.index += 1
        self._stats["plan_steps"] += 1

        self._executor.submit(
            request_id=run.request_id,
            action=step.action,
            params=params,
            callback=lambda handle, result: self._on_plan_step_done(
                run, step, handle, result
            ),
        )
        logger.debug("[pipeline] 计划第 %d/%d 步已提交: %s",
                     run.index, run.total, step.action)

    def _on_plan_step_done(
        self,
        run: _PlanRun,
        step: PlanStep,
        handle: TaskHandle,
        result: ToolResult,
    ) -> None:
        """某一步执行完成（**运行在执行线程内**）"""
        if handle.cancelled or run.status != PlanStatus.RUNNING:
            return

        run.results[step.step_id] = result.data

        self._bus.emit(
            EventTypes.PLAN_STEP_DONE,
            request_id=run.request_id,
            step_id=step.step_id,
            action=step.action,
            index=run.index,
            total=run.total,
            success=bool(result.success),
            summary=result.summary,
        )

        if not result.success:
            # 首步就失败 → FAILED；中途失败 → PARTIAL（已做的部分不回滚）
            self._abort_plan(
                run,
                PlanStatus.PARTIAL if run.done > 0 else PlanStatus.FAILED,
                result,
            )
            return

        run.done += 1
        run.summaries.append(self._short_summary(step, result))
        self._advance_plan(run)

    def _resolve_step_params(
        self,
        run: _PlanRun,
        step: PlanStep,
    ) -> Dict[str, Any]:
        """解析本步参数的占位符，并做指代消解

        挂起-恢复时若该步已冻结参数（`approved_params`），直接复用冻结值 ——
        保证用户批准的和最终执行的是**同一份参数**，不给"批准后参数变了"留缝。
        """
        if run.approved_step == step.step_id and run.approved_params is not None:
            return dict(run.approved_params)

        params = resolve_placeholders(step.params, run.ref_dict())

        # 步骤参数同样支持"第一个/那些"这类指代
        probe = AgentCommand(
            action=step.action, params=params, request_id=run.request_id
        )
        self._resolve_references(probe)
        return probe.params

    def _halt_plan(
        self,
        run: _PlanRun,
        step: PlanStep,
        params: Dict[str, Any],
        verdict: Any,
    ) -> None:
        """因某步需要确认而挂起计划

        注意**不设置** `approved_step` —— 只有 `_handle_confirm` 才能设，
        否则挂起期间任何一次重入都会让该步在未获批准的情况下执行。
        """
        run.status = PlanStatus.HALTED
        run.approved_params = None
        self._stats["plan_halted"] += 1

        preview = self._tool_preview(step.action, params)
        question = self._plan_confirm_question(run, step, preview, verdict)

        # 用计划的 request_id 登记待确认项，confirm 时据此找回本计划
        self._safety.request_confirm(
            run.request_id, step.action, params, preview=preview
        )

        audio = self._ack.get_or_synthesize(question) if self._ack is not None else None
        self._bus.emit(
            EventTypes.FEEDBACK_CONFIRM,
            request_id=run.request_id,
            question=question,
            risk=verdict.risk,
            action=step.action,
            preview=preview,
            audio=audio,
        )
        logger.info("[pipeline] 计划挂起待确认（第 %d/%d 步）: %s",
                    run.index + 1, run.total, question)

    def _plan_confirm_question(
        self,
        run: _PlanRun,
        step: PlanStep,
        preview: str,
        verdict: Any,
    ) -> str:
        """组装计划场景下的确认问句

        必须说清"这是第几步、前面做了什么"，否则用户不知道自己在批准什么 ——
        单步场景下问句自带上下文，多步场景下没有。
        """
        head = f"这是第 {run.index + 1} 步，一共 {run.total} 步。"
        if run.summaries:
            head += "前面已经" + "，".join(run.summaries) + "。"

        body = preview or (step.description or f"要执行{step.action}")
        tail = "这一步比较重要，确定要继续吗？" if getattr(
            verdict, "require_double", False
        ) else "要继续吗？"
        return f"{head}{body}。{tail}"

    def _finish_plan(self, run: _PlanRun, status: PlanStatus) -> None:
        """全部步骤成功完成，发出汇总结果"""
        run.status = status
        with self._plan_lock:
            self._plans.pop(run.request_id, None)

        summary = self._aggregate_summary(run)
        self._bus.emit(
            EventTypes.PLAN_FINISHED,
            request_id=run.request_id,
            status=status.value,
            done=run.done,
            total=run.total,
            summary=summary,
        )
        logger.info("[pipeline] 计划完成 (%d/%d): %s", run.done, run.total, summary[:50])

        self._emit_result(
            run.request_id,
            ToolResult.ok(data=run.results, summary=summary, count=run.done),
            action="plan",
        )

    def _abort_plan(
        self,
        run: _PlanRun,
        status: PlanStatus,
        result: ToolResult,
    ) -> None:
        """计划中止（某步失败或被拒绝）

        已完成的步骤**不回滚** —— 移动/删除类操作不可逆，
        与其假装能撤销，不如在文案里如实说明停在了哪里。
        """
        run.status = status
        with self._plan_lock:
            self._plans.pop(run.request_id, None)
        self._stats["plan_failed"] += 1

        where = f"第 {run.done + 1} 步"
        if run.done:
            done_text = "，".join(run.summaries)
            summary = (
                f"{done_text}。但{where}没做成：{result.summary}"
                f"（前 {run.done} 步已经做完啦，改不了哦）"
            )
        else:
            summary = f"计划没走通，{where}就卡住了：{result.summary}"

        self._bus.emit(
            EventTypes.PLAN_FINISHED,
            request_id=run.request_id,
            status=status.value,
            done=run.done,
            total=run.total,
            summary=summary,
        )
        logger.info("[pipeline] 计划中止 %s (%d/%d): %s",
                    status.value, run.done, run.total, result.summary[:50])

        self._emit_result(
            run.request_id,
            ToolResult(
                success=False,
                data=run.results,
                summary=summary,
                error=result.error,
                emotion=result.emotion or "sad",
                count=run.done,
            ),
            action="plan",
        )

    def _resume_plan(self, request_id: str) -> bool:
        """用户批准后从断点续跑计划

        Returns:
            True=确实恢复了一个挂起的计划
        """
        run = self._plans.get(request_id)
        if run is None or run.status != PlanStatus.HALTED:
            return False
        if run.index >= run.total:
            return False

        step = run.plan.steps[run.index]
        # 冻结参数：用户批准的就是即将执行的那一份
        run.approved_params = self._resolve_step_params(run, step)
        run.approved_step = step.step_id
        run.status = PlanStatus.RUNNING

        logger.info("[pipeline] 计划恢复，从第 %d/%d 步继续: %s",
                    run.index + 1, run.total, step.action)
        self._advance_plan(run)
        return True

    def _cancel_plan(
        self,
        request_id: str,
        reason: str = "",
        announce: bool = True,
    ) -> bool:
        """取消（挂起中或在途的）计划

        Args:
            request_id: 计划所属请求
            reason: 追加在文案里的原因
            announce: 是否播报结果。用户主动取消时为 True（要说一声）；
                      被打断时为 False（用户已经在说话了，再播报是噪音）

        Returns:
            True=确实取消了一个计划
        """
        with self._plan_lock:
            run = self._plans.pop(request_id, None)
        if run is None:
            return False

        run.status = PlanStatus.CANCELLED
        self._stats["plan_cancelled"] += 1

        tail = f"（{reason}）" if reason else ""
        if run.done:
            summary = (
                f"好，停下啦{tail}。前 {run.done} 步已经做完了，改不了哦"
            )
        else:
            summary = f"好，那就不做啦~{tail}"

        self._bus.emit(
            EventTypes.PLAN_FINISHED,
            request_id=run.request_id,
            status=PlanStatus.CANCELLED.value,
            done=run.done,
            total=run.total,
            summary=summary,
        )
        logger.info("[pipeline] 计划取消 (%d/%d)", run.done, run.total)

        if announce:
            self._emit_result(
                run.request_id,
                ToolResult.ok(
                    data=None, summary=summary, count=run.done, emotion="talk"
                ),
                action="plan",
            )
        return True

    def _aggregate_summary(self, run: _PlanRun) -> str:
        """汇总各步摘要为一句可播报的话"""
        if not run.summaries:
            return "都做完啦~"
        return f"{run.total} 步都做完啦：" + "，".join(run.summaries)

    @staticmethod
    def _short_summary(step: PlanStep, result: ToolResult) -> str:
        """截短单步摘要（汇总时避免整句话被一步的细节撑爆）"""
        text = str(result.summary or step.description or step.action).strip()
        if len(text) > _PLAN_SUMMARY_CHARS:
            text = text[:_PLAN_SUMMARY_CHARS]
        return text.rstrip("。！？~～ ")

    def _safe_tool_schemas(self) -> List[Dict[str, Any]]:
        """导出工具 schema 供 LLM 规划器用（失败返回空表）"""
        try:
            return self._registry.to_llm_schemas()
        except Exception as e:
            logger.debug("[pipeline] 导出工具 schema 失败: %s", e)
            return []

    def _safe_dir_aliases(self) -> Dict[str, str]:
        """导出白名单目录的「别名 → 真实路径」供 LLM 规划器用（失败返回空表）

        别名取白名单根目录的**基名** —— 这正是 `file_tools._resolve_dirs`
        匹配目录名时用的键，所以提示词里给出的别名一定能被工具认出来。
        """
        try:
            return {p.name: str(p) for p in self._safety.whitelist_roots()}
        except Exception as e:
            logger.debug("[pipeline] 读取白名单目录失败: %s", e)
            return {}

    @property
    def plan_count(self) -> int:
        """在途计划数（测试与 /status 用）"""
        return len(self._plans)

    # ── 指代消解 ──

    def _resolve_references(self, command: AgentCommand) -> None:
        """把参数里的指代表达解析为具体路径

        处理三种情况：
        1. `target="第一个"` → 从实体栈取第 1 个文件
        2. `targets=["那些"]` → 展开为全部候选路径
        3. 删除/移动/读取缺目标 → 直接用实体栈的最近文件列表

        解析不出来时保持原样，由工具层给出"要操作哪个文件呀？"的追问。
        """
        if self._tracker is None:
            return

        params = command.params

        # 1/2. 显式指代
        single = params.get("target")
        if isinstance(single, str) and self._tracker.is_reference(single):
            resolved = self._tracker.resolve(single)
            path = self._first_path(resolved)
            if path:
                logger.info("[pipeline] 指代消解 %r → %s", single, path)
                params["target"] = path

        targets = params.get("targets")
        if isinstance(targets, list):
            expanded: list = []
            changed = False
            for item in targets:
                if isinstance(item, str) and self._tracker.is_reference(item):
                    paths = [p for p in (self._path_of(f) for f in self._tracker.resolve(item)) if p]
                    if paths:
                        expanded.extend(paths)
                        changed = True
                        continue
                expanded.append(item)
            if changed:
                logger.info("[pipeline] 集合指代消解 → %d 个目标", len(expanded))
                params["targets"] = expanded

        # 3. 缺目标 → 用实体栈
        if command.action in _TRACKER_TARGET_ACTIONS:
            has_target = any(
                params.get(k) for k in ("target", "targets", "pattern", "source")
            )
            if not has_target:
                # ── 破坏性操作 + 用户点名了位置 → **不猜** ──
                #
                # 「删除桌面上的报表」「删掉桌面上的安装包」这类说法：动词认得出来、
                # 目标认不出来（词不在文件类型表里），**而置信度仍是 0.95**。
                # 此时用实体栈兜底 = 把"上一次搜索到的文件"当成删除对象。
                # 语音回环实测踩到过：说「删除桌面上的截图」，确认语里预览的是
                # 两个 PDF 合同（繁简差异让 pattern 静默丢失，见 agent/text_norm.py）。
                #
                # 用户唯一的防线就是那句确认语，而"静默补错目标"正是 R1 最怕的失败方式。
                # 交给工具追问「你没说要删哪些文件呀」，比猜错安全得多。
                #
                # 注意范围刻意收窄：**只有 file_delete，且用户给了目录**才拦。
                # 用户完全没提位置时（"把那些删了"）实体栈仍是唯一指称对象，兜底保留。
                if command.action == "file_delete" and params.get("dirs"):
                    logger.info(
                        "[pipeline] 删除指令没有明确目标（只给了目录），"
                        "交由工具追问，不用实体栈猜")
                    return

                files = self._tracker.files()
                paths = [p for p in (self._path_of(f) for f in files) if p]
                if paths:
                    key = "targets" if command.action == "file_delete" else "target"
                    params[key] = paths if key == "targets" else paths[0]
                    logger.info("[pipeline] 从上下文补全目标: %s", params[key])

    @staticmethod
    def _path_of(item: Any) -> Optional[str]:
        """从实体条目里取出路径"""
        if isinstance(item, dict):
            return item.get("path") or item.get("name") or None
        if isinstance(item, str):
            return item
        return None

    @classmethod
    def _first_path(cls, items: list) -> Optional[str]:
        """取第一个实体的路径"""
        for it in items:
            p = cls._path_of(it)
            if p:
                return p
        return None

    # ── 分支处理 ──

    def _tool_preview(self, action: str, params: Dict[str, Any]) -> str:
        """向工具索取操作预览（只读，用于确认前展示影响范围）

        工具未实现 preview() 或预览失败时返回空串，不影响确认流程。

        **必须先校验参数类型**（P4-B2）：`preview()` 自己不做类型校验，而
        `execute` 走 `registry.execute` 时会先过 `validate_params`。
        两者若对同一份参数给出不同结论，确认界面展示的就是**一个永远不会执行的
        范围** —— 用户照着它点了"确认"。实测（`tools/_tmp_b2_controlled.py`）：
        `file_delete(dirs="Downloads", pattern="*.txt")` 的预览是
        "找到 3 个文件（c.txt、a.txt、b.txt）"（字符串被逐字符解析失败后落到
        默认四目录，把 Desktop 的也扫了进去），而真正执行时直接报类型错、一个都不删。
        预览说 A、执行做 B，是"确认流程撒谎"，比不展示预览危险得多。
        """
        try:
            tool = self._registry.get(action)
            if tool is None or not hasattr(tool, "preview"):
                return ""
            tool.validate_params(params)
            return tool.preview(params) or ""
        except ParamError as e:
            # 参数不合法 → 不给预览（执行时也会被同一道校验拦下，两者结论一致）
            logger.info("[pipeline] 参数不合法，跳过预览 (%s): %s", action, e)
            return ""
        except Exception as e:
            logger.warning("[pipeline] 预览生成失败 (%s): %s", action, e)
            return ""

    def _handle_confirm(self, request_id: str) -> None:
        """用户确认：找到待确认项并执行"""
        pending = self._find_pending(request_id)
        if pending is None:
            logger.info("[pipeline] 确认响应无对应待确认项")
            self._bus.emit(
                "pipeline.chat", request_id=request_id, text="", confidence=0.0
            )
            return

        self._safety.resolve_confirm(pending.request_id, approved=True)

        # 多步计划挂起中 → 从断点续跑，而不是当成单步指令执行
        if self._resume_plan(pending.request_id):
            return

        command = AgentCommand(
            action=pending.action,
            params=pending.params,
            request_id=pending.request_id,
            source="confirm",
        )
        logger.info("[pipeline] 确认通过，开始执行: %s", command.action)

        self._emit_ack(pending.request_id, command.action, command.params)
        self._submit(pending.request_id, command)

    def _handle_cancel(self, request_id: str) -> None:
        """用户取消：丢弃待确认项"""
        pending = self._find_pending(request_id)
        if pending is None:
            logger.info("[pipeline] 取消响应无对应待确认项")
            return

        self._safety.resolve_confirm(pending.request_id, approved=False)
        logger.info("[pipeline] 用户取消: %s", pending.action)

        # 多步计划挂起中 → 整个计划作废（不是只跳过这一步）
        if self._cancel_plan(pending.request_id, reason="你取消了"):
            return

        self._emit_result(
            pending.request_id,
            ToolResult.ok(data=None, summary="好，那就不做啦~", count=0, emotion="talk"),
            action=pending.action,
        )

    def _find_pending(self, request_id: str):
        """找到当前待确认项

        优先精确匹配 request_id；不匹配时取最新的一条
        （用户用新语句说"确定"时 request_id 是新的）。
        """
        pending = self._safety.get_pending(request_id)
        if pending is not None:
            return pending

        # 兜底：取任意一条待确认项（通常同时只会有一条）
        for rid in list(getattr(self._safety, "_pending", {}).keys()):
            item = self._safety.get_pending(rid)
            if item is not None:
                return item
        return None

    # ── 并行发射 ──

    def _emit_ack(
        self,
        request_id: str,
        action: str,
        params: Dict[str, Any],
    ) -> None:
        """发射即时确认语（不等待执行）"""
        if self._ack is None:
            return

        category = category_for_action(action)
        audio, text = self._ack.pick(category)

        elapsed_ms = self._elapsed_ms(request_id)
        self._stats["latency_ack_ms"].append(elapsed_ms)

        self._bus.emit(
            EventTypes.FEEDBACK_ACK,
            request_id=request_id,
            text=text,
            category=category,
            audio=audio,          # None 表示未缓存，消费方需实时合成
            elapsed_ms=elapsed_ms,
        )
        logger.info("[pipeline] ack (%.0fms): %s", elapsed_ms, text)

    def _submit(self, request_id: str, command: AgentCommand) -> None:
        """提交任务到执行器"""
        self._stats["tool_calls"] += 1

        task_id = self._executor.submit(
            request_id=request_id,
            action=command.action,
            params=command.params,
            callback=self._on_task_done,
        )

        self._bus.emit(
            EventTypes.TASK_SUBMITTED,
            request_id=request_id,
            task_id=task_id,
            action=command.action,
        )
        logger.debug("[pipeline] 已提交任务 %s (%s)", task_id, command.action)

    def _on_task_done(self, handle: TaskHandle, result: ToolResult) -> None:
        """执行完成回调（运行在执行线程内）"""
        if handle.cancelled:
            self._bus.emit(EventTypes.TASK_CANCELLED, task_id=handle.task_id)
            return

        self._emit_result(handle.request_id, result, action=handle.action)

    def _emit_result(
        self,
        request_id: str,
        result: ToolResult,
        action: str = "",
    ) -> None:
        """发射执行结果（含摘要润色与实体栈更新）"""
        elapsed_ms = self._elapsed_ms(request_id)
        self._stats["latency_result_ms"].append(elapsed_ms)

        summary = self._compose_summary(action, result)
        self._update_tracker(action, result)
        self._record_episode(action, result)

        agent_result = AgentResult(
            request_id=request_id,
            status=(
                CommandStatus.SUCCESS if result.success else CommandStatus.ERROR
            ),
            summary=summary,
            data=result.data,
            emotion=result.emotion,
            tool_name=action,
            error=result.error,
            elapsed_ms=elapsed_ms,
        )

        self._bus.emit(
            EventTypes.TASK_COMPLETED,
            request_id=request_id,
            result=agent_result,
        )
        self._bus.emit(
            EventTypes.FEEDBACK_RESULT,
            request_id=request_id,
            summary=summary,
            emotion=result.emotion,
            data=result.data,
            tool_name=action,
            success=result.success,
            elapsed_ms=elapsed_ms,
            truncated=result.truncated,
        )
        logger.info("[pipeline] result (%.0fms): %s", elapsed_ms, summary[:50])

        self._request_t0.pop(request_id, None)

        # 结果已播报完毕；若该结果值得润色，另起线程做"事后补播"（P2-2）
        self._submit_refine(request_id, action, result, summary)

    # ══════════════════════════════════════════════
    #  异步润色（P2-2）
    # ══════════════════════════════════════════════

    def _submit_refine(
        self,
        request_id: str,
        action: str,
        result: ToolResult,
        template_text: str,
    ) -> None:
        """提交润色任务（不阻塞调用方）

        与 P1 的区别：P1 是"先等 LLM 再播报"，这里是"先用模板播报，润色好了再补一句"。
        用户听到结果的时刻不再取决于 LLM 响应速度。
        """
        if not self._async_refine or self._summarizer is None:
            return
        if self._refine_stop.is_set():
            return

        try:
            worth = bool(self._summarizer.should_refine(action, result))
        except Exception as e:                       # 摘要器实现异常 → 当作不需要润色
            logger.debug("[pipeline] should_refine 失败: %s", e)
            worth = False

        if not worth:
            self._stats["refine_skipped"] += 1
            return

        def worker() -> None:
            try:
                text = self._summarizer.refine(action, result)  # type: ignore[union-attr]
            except Exception as e:
                logger.warning("[pipeline] 润色失败 %s: %s", action, e)
                text = ""

            with self._refine_lock:
                self._refine_threads.pop(request_id, None)
                cancelled = request_id in self._refine_cancelled
                self._refine_cancelled.discard(request_id)

            if cancelled or self._refine_stop.is_set():
                return
            if not text or text == template_text:
                return                                # 没改善 → 不打扰用户

            self._stats["refined"] += 1
            self._bus.emit(
                EventTypes.FEEDBACK_REFINE,
                request_id=request_id,
                summary=text,
                previous=template_text,
                action=action,
            )
            logger.info("[pipeline] 润色补播: %s", text[:50])

        thread = threading.Thread(
            target=worker,
            name=f"agent-refine-{request_id}",
            daemon=True,           # 不阻塞解释器退出（D7 教训）
        )
        with self._refine_lock:
            self._refine_threads[request_id] = thread
        thread.start()

    def _cancel_refine(self, request_id: str) -> None:
        """标记某请求的润色结果为过期（打断场景）"""
        with self._refine_lock:
            self._refine_cancelled.add(request_id)

    def _shutdown_refine(self) -> None:
        """置停止位并有界 join 全部在途润色线程（释放/停止时调用）"""
        self._refine_stop.set()
        with self._refine_lock:
            threads = list(self._refine_threads.values())
            self._refine_threads.clear()
            self._refine_cancelled.clear()

        deadline = time.perf_counter() + self._refine_timeout
        for t in threads:
            remaining = deadline - time.perf_counter()
            if remaining <= 0:
                break
            if t.is_alive() and t is not threading.current_thread():
                t.join(timeout=remaining)

        still = [t.name for t in threads if t.is_alive()]
        if still:
            logger.debug("[pipeline] 润色线程未在 %.1fs 内退出: %s",
                         self._refine_timeout, still)

    def _compose_summary(self, action: str, result: ToolResult) -> str:
        """生成**立即播报**的文案

        - 无摘要器 → 用工具产出的 summary
        - async_refine 开启且该结果值得润色 → 只取廉价模板摘要，LLM 交给事后补播
          （这是 P2-2 的核心：播报时刻不再取决于 LLM 响应速度）
        - 否则 → 走摘要器原有的「简单走模板 / 复杂走 LLM」分流
        - 摘要器失败 → 回退工具 summary（摘要器自身已兜底，这里再加一道）
        """
        if self._summarizer is None:
            return result.summary

        use_fast = False
        if self._async_refine and not self._refine_stop.is_set():
            try:
                use_fast = bool(self._summarizer.should_refine(action, result))
            except Exception as e:
                logger.debug("[pipeline] should_refine 失败，退回同步摘要: %s", e)

        try:
            text = (
                self._summarizer.fast_summary(action, result)
                if use_fast else
                self._summarizer.summarize(action, result)
            )
        except Exception as e:
            logger.warning("[pipeline] 摘要生成失败: %s", e)
            return result.summary

        if text:
            self._stats["summarized"] += 1
            return text
        return result.summary

    def _update_tracker(self, action: str, result: ToolResult) -> None:
        """把结果写入实体栈，供后续指代使用"""
        if self._tracker is None:
            return

        try:
            if result.success and action in _FILE_PRODUCING_ACTIONS:
                items = result.data if isinstance(result.data, list) else []
                if items:
                    self._tracker.push_files(items)
            self._tracker.push_action(action, {}, result.count)
            # 落盘（默认 1s 节流）；磁盘 I/O 不进热路径，失败也不影响主流程
            if self._tracker_store is not None:
                self._tracker_store.save(self._tracker)
        except Exception as e:
            logger.debug("[pipeline] 更新实体栈失败: %s", e)

    # ══════════════════════════════════════════════
    #  长期记忆（P3）
    # ══════════════════════════════════════════════

    def _record_episode(self, action: str, result: ToolResult) -> None:
        """把本次执行写进情景记忆（跨会话可被"上次那个"召回）

        约定（见 `MemoryService.record_episode`）：
        - **只记成功的操作** —— 失败经历被召回会把用户带偏
        - `action` 传**可播报的动作短语**而不是工具名（"打开文件" 而非 "file_read"），
          因为这条记忆将来会被直接念出来
        - 记忆是增强而非依赖：整层失效也不能影响 `feedback.result` 的发出
        """
        if self._memory is None or not result.success:
            return
        try:
            self._memory.record_episode(
                action=_ACTION_SPEECH.get(action, action),
                summary=str(result.summary or ""),
                params=None,
                success=True,
            )
        except Exception as e:
            logger.debug("[pipeline] 记录情景记忆失败: %s", e)

    def _memory_hint(self, text: str) -> str:
        """取偏好提示（供路由与规划参考）；无记忆时为空串"""
        if self._memory is None:
            return ""
        try:
            return self._memory.hint_for(text) or ""
        except Exception as e:
            logger.debug("[pipeline] 取记忆提示失败: %s", e)
            return ""

    @property
    def memory(self) -> Optional[MemoryService]:
        """当前长期记忆（P3）"""
        return self._memory

    def set_memory(self, memory: Optional[MemoryService]) -> None:
        """替换记忆能力（配置热更新）；置 None 即关闭记忆"""
        self._memory = memory

    # ══════════════════════════════════════════════
    #  辅助
    # ══════════════════════════════════════════════

    @staticmethod
    def _new_rid() -> str:
        from agent.message import new_request_id
        return new_request_id()

    def _elapsed_ms(self, request_id: str) -> float:
        """从请求开始到现在的毫秒数"""
        t0 = self._request_t0.get(request_id)
        return (time.perf_counter() - t0) * 1000 if t0 else 0.0

    def stats(self) -> Dict[str, Any]:
        """管线统计（延迟分位数 + 计数）"""
        ack = sorted(self._stats["latency_ack_ms"])
        res = sorted(self._stats["latency_result_ms"])
        lru = self._ack.lru_stats() if self._ack is not None else {}

        def pct(values: List[float], p: float) -> float:
            if not values:
                return 0.0
            idx = min(int(len(values) * p), len(values) - 1)
            return round(values[idx], 1)

        return {
            "commands": self._stats["commands"],
            "tool_calls": self._stats["tool_calls"],
            "chats": self._stats["chats"],
            "rejected": self._stats["rejected"],
            "confirms": self._stats["confirms"],
            "interrupts": self._stats["interrupts"],
            "summarized": self._stats["summarized"],
            "refined": self._stats["refined"],
            "refine_skipped": self._stats["refine_skipped"],
            "refine_pending": len(self._refine_threads),
            "plans": self._stats["plans"],
            "plan_steps": self._stats["plan_steps"],
            "plan_halted": self._stats["plan_halted"],
            "plan_failed": self._stats["plan_failed"],
            "plan_cancelled": self._stats["plan_cancelled"],
            "plan_pending": len(self._plans),
            "planner": type(self._planner).__name__ if self._planner else None,
            "ack_p50_ms": pct(ack, 0.5),
            "ack_p95_ms": pct(ack, 0.95),
            "result_p50_ms": pct(res, 0.5),
            "result_p95_ms": pct(res, 0.95),
            "pending_tasks": self._executor.pending_count(),
            "tracked_files": self._tracker.file_count() if self._tracker else 0,
            "ack_lru_size": self._ack.lru_size() if self._ack is not None else 0,
            "ack_lru_hits": lru.get("hits", 0),
            "ack_lru_misses": lru.get("misses", 0),
            "ack_lru_evictions": lru.get("evictions", 0),
            "tracker_restored": (
                self._tracker_store.stats().get("restored", 0)
                if self._tracker_store is not None else 0
            ),
        }

    def reset_stats(self) -> None:
        """重置统计"""
        for key in ("latency_ack_ms", "latency_result_ms"):
            self._stats[key] = []
        for key in ("commands", "tool_calls", "chats", "rejected",
                    "confirms", "interrupts", "summarized",
                    "refined", "refine_skipped",
                    "plans", "plan_steps", "plan_halted",
                    "plan_failed", "plan_cancelled"):
            self._stats[key] = 0
        self._request_t0.clear()

    def reset_context(self) -> None:
        """清空对话实体上下文（用户开始新话题时调用）"""
        if self._tracker is not None:
            self._tracker.clear()

    @property
    def tracker(self) -> EntityTracker:
        """实体追踪器（供 UI 或测试查询上下文）"""
        return self._tracker

    @property
    def summarizer(self) -> Optional[SummarizerService]:
        """当前摘要器"""
        return self._summarizer

    def set_summarizer(self, summarizer: Optional[SummarizerService]) -> None:
        """替换摘要器（配置热更新）"""
        self._summarizer = summarizer

    @property
    def planner(self) -> Optional[PlannerService]:
        """当前规划器（P3）"""
        return self._planner

    def set_planner(self, planner: Optional[PlannerService]) -> None:
        """替换规划器（配置热更新）；置 None 即关闭多步规划"""
        self._planner = planner

    def __repr__(self) -> str:
        return (
            f"<AgentPipeline started={self._started} enabled={self._enabled} "
            f"commands={self._stats['commands']}>"
        )
