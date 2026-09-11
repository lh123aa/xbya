"""F3 真实 LLM 端到端验证（用真实 App 装配，不是等价替身）

验证三条真实 LLM 路径：
  F3-a  LLM 路由兜底（function calling）
  F3-b  LLM 摘要润色（chat_once → refine）
  F3-c  LLM 规划器（把规则配方认不出的多步目标拆成计划）

用法： python tools/verify_f3_real_llm.py
"""
from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from agent.providers.router.hybrid_router import HybridRouter  # noqa: E402
from agent.providers.router.llm_router import TOOL_SCHEMAS  # noqa: E402
from agent.seams.planner import PlanStatus  # noqa: E402
from agent.tools.base import ToolResult  # noqa: E402

PASS, FAIL, SKIP = "[PASS]", "[FAIL]", "[SKIP]"
results: list = []

#: 规划器的 LLM 调用上界（与 `agent.planner.llm_timeout` 默认值一致）。
#: 用来区分"服务端慢到撞上界"与"返回了但没通过校验" —— 前者是外部依赖。
_LLM_TIMEOUT_MS = 20000.0


class _QuotaWatch(logging.Handler):
    """抓 LLM 侧的 429 / 配额告警（缺陷 18 的处置）

    为什么必须有它：免费档配额用尽时，LLM 插件只记一行 `API 错误: 429 ...`，
    对上层表现为"规划器返回 None"。于是**外部配额问题会伪装成"我们的规划器不行"**
    —— 验收脚本与用户都看不出真相。这里把那行日志抓下来，
    让本脚本能明确说"这是配额，不是代码"。
    """

    PATTERNS = ("429", "rate limit", "quota", "too many requests")

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.hits: list = []

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = record.getMessage()
        except Exception:                              # pragma: no cover - 日志防御
            msg = str(record.msg)
        low = msg.lower()
        if any(p in low for p in self.PATTERNS):
            self.hits.append(" ".join(msg.split())[:200])

    @property
    def hit(self) -> bool:
        return bool(self.hits)


def check(ok, label, detail=""):
    results.append((bool(ok), label))
    print(f"{PASS if ok else FAIL} {label}" + (f"  → {detail}" if detail else ""))


def skipped(label, why=""):
    """外部依赖不可用时用的结论：既不算通过也不算失败，但要**显式打印**

    LLM 是第三方服务。它的延迟会让同一个脚本在不同轮次给出不同结论 ——
    把这种情况记成 FAIL 会让人以为"我们的代码坏了"，
    记成 PASS 又是撒谎。所以单列一档，并在汇总里如实计数。
    """
    results.append((None, label))
    print(f"{SKIP} {label}" + (f"  → {why}" if why else ""))


def main() -> int:
    print("=" * 72)
    print("F3 真实 LLM 端到端验证（真实 App + 真实 API）")
    print("=" * 72)

    # 配额观察器：见 _QuotaWatch 的 docstring（让 429 不再伪装成代码问题）
    quota = _QuotaWatch()
    logging.getLogger().addHandler(quota)
    logging.getLogger().setLevel(logging.WARNING)

    from core.app import XiaoyiApp

    t0 = time.perf_counter()
    app = XiaoyiApp()
    ok_init = app.initialize()
    print(f"\n应用初始化: {ok_init}  ({(time.perf_counter()-t0):.1f}s)")
    check(ok_init, "应用初始化成功")

    stack = app.agent_stack
    check(stack is not None, "Agent 层已装配")
    if stack is None:
        return _summary()

    print(f"\nAgent 栈: router={type(stack.router).__name__} "
          f"summarizer={type(stack.summarizer).__name__} "
          f"planner={type(stack.planner).__name__} tools={stack.registry.count()}")

    # ── 底层能力：app.route_with_tools（function calling）──
    print("\n[F3-a] LLM 路由兜底（真实 function calling）")
    check(hasattr(app, "route_with_tools"), "app.route_with_tools 存在")
    # 调用前后记账：若这次 `route_with_tools` 返回 None **且**期间抓到 429，
    # 那结论只能是"配额用尽导致没问到模型"，不能判成"我们的 function calling 不行"。
    #
    # 这一处是 P5-C1 轮次实测出来的：我在那轮为查 key 状态多打了几次真实请求，
    # 把免费档 8000 TPM 用完，于是本脚本报出 `16/18 通过，2 失败` ——
    # 而两条失败正是下面那两个断言。等配额窗口过去原样重跑，**17/17 全过**。
    # 也就是说：单看那一次的数字，会得出"产品 function calling 坏了"的错误结论。
    #
    # 这与缺陷 18（429 伪装成"规划器偶尔拆不出计划"）是同一个病：
    # **外部配额伪装成代码问题**。规划器那条早已用 skipped() 处置，
    # 工具调用这条当时漏了 —— 而末段那句"凡受此影响的断言都已归入 [SKIP]"
    # 因此变成了**假话**（那次运行明明有 2 个 FAIL）。这里补上，并让那句话成立。
    _hits_before = len(quota.hits)
    probe = app.route_with_tools(
        "你是意图路由器，从工具里选一个并填参数", "帮我看看电脑还有多少电", TOOL_SCHEMAS)
    print(f"  route_with_tools → {probe}")
    _quota_hit_here = len(quota.hits) > _hits_before
    _why = (f"这次调用期间抓到 429/限流：{quota.hits[_hits_before][:90]} —— "
            "**外部配额，不是本层结论**；等配额恢复后重跑")
    if probe is None and _quota_hit_here:
        skipped("真实函数调用返回了工具名", _why)
        skipped("选中的工具在 schema 清单内", _why)
    else:
        check(probe is not None and probe.get("name"),
              "真实函数调用返回了工具名",
              str(probe.get("name")) if probe else "None")
        check(bool(probe and probe.get("name") in [t["name"] for t in TOOL_SCHEMAS]),
              "选中的工具在 schema 清单内")

    # ── HybridRouter 走真实 LLM 兜底（用**已装配**的 router，证明接线真的通）──
    print("\n[F3-a2] HybridRouter 真实兜底（规则认不出的说法）")
    hyb = stack.router
    print(f"  stack.router = {type(hyb).__name__}")
    check(isinstance(hyb, HybridRouter), "装配出的就是 HybridRouter")
    inner = getattr(hyb, "_llm", None)
    print(f"  内层 LLM 路由 = {type(inner).__name__}")
    check(inner is not None, "LLM 路由已注入（非 None）")
    # 内层 LLMRouter 的注入函数应当就是 app.route_with_tools
    fn = getattr(inner, "_llm", None)
    check(fn is not None and getattr(fn, "__self__", None) is app,
          "注入的正是 app.route_with_tools（真实 API）",
          f"{getattr(fn, '__qualname__', fn)}")

    before = getattr(hyb, "_llm_hits", 0)
    for text in ("帮我把下载目录收拾一下", "桌面太乱了帮我整理下"):
        t1 = time.perf_counter()
        ch = hyb.route(text)
        dt = (time.perf_counter() - t1) * 1000
        print(f"  {text!r} → {ch.action}({ch.confidence:.2f}) "
              f"params={ch.params}  {dt:.0f}ms")
    after = getattr(hyb, "_llm_hits", 0)
    print(f"  LLM 命中计数: {before} → {after}")
    check(after > before, "LLM 路由**确实被触发**（不是纯规则兜底）",
          f"llm_hits {before}→{after}")

    # ── F3-b 摘要润色 ──
    print("\n[F3-b] LLM 摘要润色（真实 chat_once）")
    summ = stack.summarizer
    check(summ is not None, "摘要器存在")
    fake = ToolResult.ok(
        data=[{"name": f"安装包_{i}.exe", "path": f"C:/Downloads/安装包_{i}.exe"}
              for i in range(1, 7)],
        summary="找到 6 个文件（安装包_6.exe、安装包_5.exe、安装包_4.exe 等 6 个）",
        count=6,
    )
    fast = summ.fast_summary("file_search", fake)
    worth = summ.should_refine("file_search", fake)
    print(f"  模板摘要: {fast!r}")
    print(f"  should_refine: {worth}")
    t2 = time.perf_counter()
    refined = summ.refine("file_search", fake)
    dt2 = (time.perf_counter() - t2) * 1000
    print(f"  LLM 润色 ({dt2:.0f}ms): {refined!r}")
    check(bool(refined), "润色产出了文本", f"{len(refined)} 字")
    check(refined != fast, "润色结果与模板不同")
    check(dt2 < 30000, "润色在 30s 内返回", f"{dt2:.0f}ms")

    # ── F3-c 规划器 ──
    # LLM 是**非确定性**依赖：同一句目标，实测会出现"返回 None"的抖动。
    # 所以这里不把"单次成功"当关卡（那会让结论逐次翻转，不是证据），
    # 改为跑 3 次统计成功率，关卡条件是"3 次内至少成功 1 次"（证明接线通），
    # 成功率本身作为质量指标如实打印。
    print("\n[F3-c] LLM 规划器（规则配方认不出的多步目标）")
    planner = stack.planner
    goal = "把下载目录里的安装包都挪到软件归档，然后清理掉下载目录里的空文件夹"
    ctx = {"available_actions": stack.registry.names(),
           "tool_schemas": stack.registry.to_llm_schemas(),
           "dir_aliases": {p.name: str(p) for p in stack.safety.whitelist_roots()}}

    picked, ok_n, tries = None, 0, 3
    timed_out = 0
    for i in range(tries):
        t3 = time.perf_counter()
        attempt = planner.plan(goal, ctx)
        dt3 = (time.perf_counter() - t3) * 1000
        if attempt is None:
            # 区分"服务端慢到撞上界"与"返回了但没通过校验/解析"：
            # 前者是外部依赖，后者才可能是我们提示词或校验逻辑的问题
            near_timeout = dt3 >= 0.9 * _LLM_TIMEOUT_MS
            if near_timeout:
                timed_out += 1
            print(f"  第{i + 1}次 ({dt3:.0f}ms) 规划器返回 None"
                  f"{'（撞满 20s 上界 → 服务端慢）' if near_timeout else '（返回过快 → 可能是解析/校验问题）'}")
        else:
            print(f"  第{i + 1}次 ({dt3:.0f}ms) 来源={attempt.source} "
                  f"步数={len(attempt.steps)}")
            for s in attempt.steps:
                print(f"    {s.step_id}: {s.action}({s.params})  // {s.description}")
        if attempt is not None and len(attempt.steps) >= 2:
            ok_n += 1
            if picked is None:
                picked = attempt

    print(f"  LLM 规划成功率: {ok_n}/{tries}（其中 {timed_out} 次是撞超时上界的）")
    if quota.hit:
        print(f"  ⚠️ 抓到 {len(quota.hits)} 条 LLM 配额/限流告警（429），"
              f"例如：{quota.hits[0][:120]}")
    if picked is not None:
        check(True, f"LLM 拆出了多步计划（{tries} 次内至少 1 次）", f"{ok_n}/{tries}")
    elif quota.hit:
        # **配额用尽时不能判产品失败**：模型根本没被真正问到。
        # 这正是缺陷 18 —— 429 伪装成"规划器偶尔拆不出计划"。
        skipped(f"LLM 拆出了多步计划（{tries} 次内至少 1 次）",
                f"{tries} 次全部失败，但抓到 429/限流：{quota.hits[0][:90]} —— "
                "**外部配额（免费档 8000 TPM），不是本层结论**；"
                "等一分钟再跑，或换已充值的 key")
    elif timed_out == tries:
        # 三次全撞超时 = 我们无法断定规划器好坏（模型根本没回话）
        skipped(f"LLM 拆出了多步计划（{tries} 次内至少 1 次）",
                f"{tries} 次全部撞满 {_LLM_TIMEOUT_MS / 1000:.0f}s 上界 —— "
                "服务端当前过慢，**这是外部依赖，不是本层结论**")
    else:
        check(False, f"LLM 拆出了多步计划（{tries} 次内至少 1 次）",
              f"{ok_n}/{tries}，其中超时 {timed_out} 次")

    plan = picked
    if plan is not None:
        known = set(stack.registry.names())
        unknown = [a for a in plan.action_names() if a not in known]
        check(not unknown, "计划里的工具名全部合法", f"非法={unknown}" if unknown else "")
        check(len(plan.steps) >= 2, "至少 2 步", f"{len(plan.steps)} 步")

    # ── F3-e 提示词确定性校验（不依赖 LLM，可重复）──
    # 真实 LLM 曾把目录编成 `C:/Users/Username/Downloads`（本机真实路径是
    # `C:\Users\49046\Downloads`）。修复点是"把真实白名单目录注入提示词"，
    # 这件事**不需要调用 LLM 就能断言**，所以它是本节的硬关卡。
    print("\n[F3-e] 提示词含本机真实目录（确定性，不调 LLM）")
    llm_planner = getattr(planner, "_llm", None) or planner
    prompt = llm_planner._build_prompt(goal, ctx)
    roots = stack.safety.whitelist_roots()
    missing = [f"{p.name}={p}" for p in roots
               if p.name not in prompt or str(p) not in prompt]
    print(f"  白名单目录: {[f'{p.name}={p}' for p in roots]}")
    check(bool(roots) and not missing, "提示词含全部真实白名单目录",
          f"缺失={missing}" if missing else f"{len(roots)} 个")
    check("绝对不要自己编造路径" in prompt, "提示词明令禁止编造路径")
    check("${s1.paths.0}" in prompt and "file_move.source" in prompt,
          "提示词区分单值/列表占位符")

    # ── F3-d 真实 LLM 提议的危险计划必须被安全层拦住 ──
    print("\n[F3-d] LLM 提议的危险计划 → 安全层拦截（真实管线全链路）")
    if plan is not None:
        bad = [a for a in plan.action_names()
               if a in ("run_command", "file_delete")]
        print(f"  该计划含高风险工具: {bad}")
    events: list = []
    from core.kernel.events import EventTypes
    app.agent_bus.on_any(lambda e: events.append((e.type, dict(e.data))))

    def _drain(deadline_s: float) -> None:
        """等管线收尾（计划结束 / 结果播报 / 退回闲聊）"""
        import time as _t
        end = _t.time() + deadline_s
        while _t.time() < end and not any(
                t in (EventTypes.PLAN_FINISHED, EventTypes.FEEDBACK_RESULT,
                      "pipeline.chat") for t, _ in events):
            _t.sleep(0.05)

    # 同样因为 LLM 抖动：第一轮可能规划不出计划而直接退回闲聊，
    # 那不是安全层的问题。重试到真的启动了计划，最多 3 轮。
    started = False
    for attempt_no in range(1, 4):
        events.clear()
        app.agent_bus.emit(EventTypes.SPEECH_RECOGNIZED, text=goal,
                           request_id=f"f3d-{attempt_no}")
        _drain(40)
        kinds = [t for t, _ in events]
        started = EventTypes.PLAN_STARTED in kinds
        print(f"  第{attempt_no}轮事件流: {kinds}")
        if started or any(t == EventTypes.FEEDBACK_RESULT for t, _ in events):
            break

    fin = [d for t, d in events if t == EventTypes.PLAN_FINISHED]
    chat = [d for t, d in events if t == "pipeline.chat"]
    res = [d for t, d in events if t == EventTypes.FEEDBACK_RESULT]
    if fin:
        print(f"  计划收尾: {fin[0]['status']}  done={fin[0]['done']}/"
              f"{fin[0]['total']}")
        print(f"  文案: {fin[0]['summary'][:110]}")
        summary = str(fin[0]["summary"])
        unreadable = "不能动" not in summary and "没做成" not in summary
        check(fin[0]["status"] in ("failed", "partial", "cancelled"),
              "含违规步骤的计划未成功执行", fin[0]["status"])
        if unreadable and "还没完全理解" in summary:
            # 计划在第 1 步就因为"参数没被理解"卡住 → 根本没走到安全层，
            # 也就无从判断"拒绝理由是否可读"。这是 LLM 侧没拆好，
            # 不是安全层的问题（安全层的行为由 p3 端到端验收确定性覆盖）。
            skipped("拒绝理由对用户可读",
                    "计划没走到安全层（第 1 步参数就没被理解）—— LLM 侧问题，"
                    "安全层行为见 p3_acceptance_smoke 场景 5")
        else:
            check(not unreadable, "拒绝理由对用户可读", summary[:60])
    elif chat:
        check(True, "退回闲聊（未执行任何步骤）")
    elif res:
        check(True, "走单步路径并有结果", str(res[0].get("summary"))[:60])
    elif quota.hit:
        # 事件流空（尤其 plan.started 一次都没发）且抓到 429：
        # 管线根本没被驱动，无从判断"有没有收尾" —— 外部配额，单列一档
        skipped("计划既未收尾也未回退（事件流见上）",
                "事件流为空且抓到 429/限流：管线未被真正驱动，"
                "**判不出本层好坏**；等配额恢复再跑")
    else:
        check(False, "计划既未收尾也未回退（事件流见上）")

    if quota.hit:
        print(f"\n[配额观察] 共抓到 {len(quota.hits)} 条 429/限流告警：")
        for h in quota.hits[:3]:
            print(f"  · {h[:150]}")
        print("  → 凡受此影响的断言都已归入 [SKIP]，**没有**被算成通过")

    print("\n" + "=" * 72)
    app.shutdown()
    return _summary()


def _summary() -> int:
    ok = sum(1 for p, _ in results if p is True)
    bad = sum(1 for p, _ in results if p is False)
    sk = sum(1 for p, _ in results if p is None)
    print(f"F3 结果：{ok}/{ok + bad} 通过，{bad} 失败，{sk} 跳过"
          f"（共 {len(results)} 项）")
    for p, label in results:
        if p is not True:
            print(f"  {FAIL if p is False else SKIP} {label}")
    print("=" * 72)
    # 只有"我们自己的断言失败"才返回非零；外部依赖导致的跳过不算失败
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
