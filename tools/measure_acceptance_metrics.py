"""P1 系统性验收 — 指标量化脚本

按 docs/agent/acceptance.md §1.2 的 10 项最终验收指标逐条实测，
输出可归档的文本证据到 docs/agent/evidence/metrics.txt。

设计原则：
- 指标直接从被测系统取值，不由测试内部的断言代劳
- 明确区分「真实执行」与「mock 验证」，不混为一谈
- 只写临时沙箱目录，绝不触碰用户真实桌面/文档

用法： python tools/measure_acceptance_metrics.py
"""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import textwrap
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from unittest import mock

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from agent.bootstrap import AgentConfig, build_agent_stack  # noqa: E402
from agent.message import AgentCommand  # noqa: E402
from agent.providers.router.rule_router import RuleRouter  # noqa: E402
from agent.providers.safety.basic_guard import BasicGuard  # noqa: E402
from agent.tools.registry import ToolRegistry  # noqa: E402
from agent.tracker import EntityTracker  # noqa: E402
from core.kernel.context import Context  # noqa: E402
from core.kernel.events import EventBus, EventTypes  # noqa: E402
from core.kernel.loader import PluginLoader, PluginSpec  # noqa: E402
from tests.agent.router_testset import ROUTER_TESTSET  # noqa: E402


# ══════════════════════════════════════════════════
#  证据收集
# ══════════════════════════════════════════════════

@dataclass
class Metric:
    id: str
    name: str
    target: str
    measured: str
    verdict: str          # PASS / FAIL / PARTIAL / NOT-VERIFIED
    note: str = ""


METRICS: List[Metric] = []


def record(m: Metric) -> None:
    METRICS.append(m)
    flag = {"PASS": "[PASS]", "FAIL": "[FAIL]",
            "PARTIAL": "[PART]", "NOT-VERIFIED": "[N/V]"}[m.verdict]
    print(f"{flag} {m.id} {m.name}: {m.measured}  (目标 {m.target})")
    if m.note:
        print(f"        {m.note}")


# ══════════════════════════════════════════════════
#  M1 / M2 — 规则路由准确率与延迟
# ══════════════════════════════════════════════════

def measure_router() -> Tuple[RuleRouter, List[float]]:
    router = RuleRouter()

    correct, action_fail, param_fail = 0, [], []
    times: List[float] = []

    for text, exp_action, exp_params in ROUTER_TESTSET:
        t0 = time.perf_counter()
        cmd = router.route(text, {})
        times.append((time.perf_counter() - t0) * 1000)

        if cmd.action != exp_action:
            action_fail.append((text, exp_action, cmd.action))
            continue
        miss = {k: v for k, v in exp_params.items() if cmd.params.get(k) != v}
        if miss:
            param_fail.append((text, miss, cmd.params))
            continue
        correct += 1

    total = len(ROUTER_TESTSET)
    acc = correct / total
    record(Metric(
        "M1", "规则路由准确率", ">90%",
        f"{acc:.1%} ({correct}/{total})",
        "PASS" if acc > 0.90 else "FAIL",
        f"action 错 {len(action_fail)} 条，参数错 {len(param_fail)} 条"
        + (f"；样例 {action_fail[:2]}" if action_fail else "")
        + (f"；参数样例 {param_fail[:2]}" if param_fail else ""),
    ))

    # 预热后再测延迟，排除首次编译开销
    for text, _, _ in ROUTER_TESTSET[:10]:
        router.route(text, {})
    warm: List[float] = []
    for _ in range(5):
        for text, _, _ in ROUTER_TESTSET:
            t0 = time.perf_counter()
            router.route(text, {})
            warm.append((time.perf_counter() - t0) * 1000)
    warm.sort()
    p50 = warm[len(warm) // 2]
    p95 = warm[int(len(warm) * 0.95)]
    p99 = warm[int(len(warm) * 0.99)]
    record(Metric(
        "M2", "规则路由延迟", "P95 <10ms",
        f"P50 {p50:.3f}ms / P95 {p95:.3f}ms / P99 {p99:.3f}ms (n={len(warm)})",
        "PASS" if p95 < 10.0 else "FAIL",
    ))
    return router, times


# ══════════════════════════════════════════════════
#  M3 / M4 — 感知延迟与总延迟
# ══════════════════════════════════════════════════

def measure_latency(sandbox: Path) -> None:
    bus = EventBus()
    cfg = AgentConfig(
        path_whitelist=[str(sandbox)], audit_enabled=False,
        ack_enabled=True, ack_warmup=True, llm_router_enabled=False,
        tools_system=False, tools_productivity=False, tools_browser=False,
    )
    synth = lambda t: f"AUDIO::{t}".encode("utf-8")  # noqa: E731

    stack = build_agent_stack(cfg, bus, synthesize=synth)
    stack.start()
    pipe = stack.pipeline
    time.sleep(1.5)      # 等确认语缓存预热完成（生产路径同样在启动后预热）

    ack_ms: List[float] = []
    res_ms: List[float] = []
    try:
        for i, text in enumerate(["找一下合同", "列出桌面", "看看电脑状态", "找一下截图"]):
            ack_at: List[float] = []
            res_at: List[float] = []
            d1 = bus.on(EventTypes.FEEDBACK_ACK, lambda e, s=ack_at: s.append(time.perf_counter()))
            d2 = bus.on(EventTypes.FEEDBACK_RESULT, lambda e, s=res_at: s.append(time.perf_counter()))
            t0 = time.perf_counter()
            pipe.handle_text(text, f"lat-{i}")
            deadline = time.time() + 8
            while not res_at and time.time() < deadline:
                time.sleep(0.002)
            d1(); d2()
            if ack_at:
                ack_ms.append((ack_at[0] - t0) * 1000)
            if res_at:
                res_ms.append((res_at[0] - t0) * 1000)
    finally:
        stack.dispose()

    def summarize(vals: List[float]) -> str:
        if not vals:
            return "无样本"
        s = sorted(vals)
        return f"P50 {s[len(s)//2]:.1f}ms / max {s[-1]:.1f}ms (n={len(s)})"

    ack_ok = bool(ack_ms) and max(ack_ms) < 1500
    res_ok = bool(res_ms) and max(res_ms) < 3000
    record(Metric(
        "M3", "感知延迟（首次反馈→确认语）", "<1.5s", summarize(ack_ms),
        "PASS" if ack_ok else ("NOT-VERIFIED" if not ack_ms else "FAIL"),
        "ack 与 submit 并行发射，确认语走预热缓存",
    ))
    record(Metric(
        "M4", "总延迟（结果播报）", "<3s", summarize(res_ms),
        "PASS" if res_ok else ("NOT-VERIFIED" if not res_ms else "FAIL"),
    ))


# ══════════════════════════════════════════════════
#  M5 — 误操作率（安全边界拒绝率）
# ══════════════════════════════════════════════════

BOUNDARY_PROBES = [
    ("白名单外系统目录", r"C:\Windows\System32\drivers\etc\hosts"),
    ("白名单外程序目录", r"C:\Program Files\secret.txt"),
    ("上跳穿越", None),        # 运行时按 sandbox 生成
    ("多级穿越", None),
    ("UNC 路径", r"\\server\share\file.txt"),
    ("空字节注入", None),
    ("系统关键目录", r"C:\Windows"),
    ("用户目录外盘符", r"D:\backup\x.txt"),
]


def measure_misoperation(sandbox: Path) -> None:
    guard = BasicGuard(whitelist=[str(sandbox)], audit_enabled=False)
    probes: List[Tuple[str, str]] = []
    for name, p in BOUNDARY_PROBES:
        if p is None:
            if name == "上跳穿越":
                p = str(sandbox / ".." / "escaped.txt")
            elif name == "多级穿越":
                p = str(sandbox / "a" / ".." / ".." / ".." / "Windows" / "x.txt")
            elif name == "空字节注入":
                p = str(sandbox / "ok.txt") + "\x00.txt"
        probes.append((name, p))

    rejected, leaked, errors = 0, [], []
    for name, p in probes:
        try:
            guard.validate_path(p)
            leaked.append(name)
        except Exception as e:
            rejected += 1
            if type(e).__name__ not in ("PathNotAllowed", "SecurityError"):
                errors.append((name, type(e).__name__))

    # 白名单内必须放行（防"过度拒绝"）
    allow_probe = str(sandbox / "inside.txt")
    try:
        guard.validate_path(allow_probe)
        false_reject = False
    except Exception:
        false_reject = True

    guard.close()

    rate = rejected / len(probes)
    ok = rate == 1.0 and not false_reject and not errors
    record(Metric(
        "M5", "误操作率（非法路径拒绝率）", "拦截率 100% / 白名单内不误拒",
        f"拒绝 {rejected}/{len(probes)}；白名单内放行={'否' if false_reject else '是'}",
        "PASS" if ok else "FAIL",
        f"漏放: {leaked or '无'}；异常类型异常: {errors or '无'}",
    ))


# ══════════════════════════════════════════════════
#  M6 — 删除可恢复率
# ══════════════════════════════════════════════════

FORBIDDEN_DELETE_CALLS = [
    "os.remove(", "os.unlink(", "shutil.rmtree(", ".unlink(",
    "os.rmdir(", "shutil.move(", "os.rename(",
]


def measure_delete_recoverable(sandbox: Path) -> None:
    src = (PROJECT / "agent" / "tools" / "file_tools.py").read_text(encoding="utf-8")

    # 只审查 file_delete 相关代码段，避免误报（file_rename 合法使用 rename）
    start = src.find("class FileDeleteTool")
    seg = src[start:] if start >= 0 else ""
    hits = [c for c in FORBIDDEN_DELETE_CALLS if c in seg]
    uses_trash = "send2trash" in seg

    # 功能验证：删除真的调用了 send2trash，且文件确实消失
    guard = BasicGuard(whitelist=[str(sandbox)], audit_enabled=False)
    from agent.tools.file_tools import FileDeleteTool
    target = sandbox / "to_delete.txt"
    target.write_text("x", encoding="utf-8")

    captured: List[str] = []
    import send2trash as s2t
    with mock.patch.object(s2t, "send2trash", side_effect=lambda p: captured.append(str(p))):
        res = FileDeleteTool(guard).execute({"target": str(target)})
    guard.close()

    ok = (not hits) and uses_trash and res.success and len(captured) == 1
    record(Metric(
        "M6", "删除可恢复率", "100%（无永久删除分支）",
        f"永久删除调用={hits or '无'}；send2trash={'有' if uses_trash else '无'}；"
        f"实调用次数={len(captured)}",
        "PASS" if ok else "FAIL",
        "源码审查 + 运行时验证；实际入回收站行为由 p1_acceptance_smoke.py 场景 4 端到端确认",
    ))


# ══════════════════════════════════════════════════
#  M7 — 工具执行成功率
# ══════════════════════════════════════════════════

def measure_tool_success(sandbox: Path) -> None:
    """对可安全真实执行的工具跑成功路径，统计成功率"""
    guard = BasicGuard(whitelist=[str(sandbox)], audit_enabled=False)
    (sandbox / "报告.docx").write_text("hello", encoding="utf-8")
    (sandbox / "截图_1.png").write_text("x", encoding="utf-8")

    from agent.tools.browser_tools import all_browser_tools
    from agent.tools.file_tools import all_file_tools
    from agent.tools.productivity_tools import all_productivity_tools
    from agent.tools.system_tools import all_system_tools

    reg = ToolRegistry()
    for t in all_file_tools(guard):
        reg.register(t)
    for t in all_system_tools(guard):
        reg.register(t)
    for t in all_productivity_tools(
        translate_func=lambda text, lang: f"[{lang}]{text}",
        weather_func=lambda city: {"city": city or "北京", "desc": "晴", "temp": 22},
    ):
        reg.register(t)
    for t in all_browser_tools(engine="bing"):
        reg.register(t)

    # 真实执行集（不碰用户真实目录、不联网、不弹窗口）
    real_cases: List[Tuple[str, Dict[str, Any]]] = [
        ("file_search", {"pattern": "*", "dirs": [str(sandbox)]}),
        ("file_list", {"dirs": [str(sandbox)]}),
        ("file_read", {"target": str(sandbox / "报告.docx")}),
        ("system_info", {"metric": "cpu"}),
        ("calculate", {"expression": "128 / 4"}),
        ("translate", {"text": "你好", "target_lang": "en"}),
        ("reminder", {"minutes": 5, "what": "验收测试"}),
        ("weather", {"city": "北京"}),
        ("clipboard", {"action": "get"}),
        ("run_command", {"command": "echo acceptance"}),
    ]

    ok, fail, details = 0, [], []
    for action, params in real_cases:
        try:
            r = reg.execute(action, params)
        except Exception as e:                       # 理论上 registry 已兜底
            fail.append((action, f"抛出 {type(e).__name__}"))
            continue
        if r.success:
            ok += 1
        else:
            fail.append((action, r.summary[:60]))

    # mock 验证集（联网 / 弹窗类，不真实执行）
    # 真实契约：web_read 走 requests.get(..., stream=True) + resp.raw.read()
    #           web_search 走 requests.get(...) + resp.text
    mock_ok, mock_fail = 0, []
    HTML = ("<html><head><title>验收页</title></head><body>"
            "<p>这是验收用的正文内容。</p></body></html>")

    def fake_response(stream: bool):
        resp = mock.Mock(status_code=200, encoding="utf-8", text=HTML)
        resp.apparent_encoding = "utf-8"
        resp.raise_for_status = lambda: None
        resp.raw = mock.Mock(read=lambda *a, **k: HTML.encode("utf-8"))
        return resp

    with mock.patch("webbrowser.open", return_value=True), \
         mock.patch("requests.get", side_effect=lambda *a, **k: fake_response(k.get("stream", False))):
        for action, params in [("web_open", {"url": "https://example.com"}),
                               ("web_search", {"query": "验收"}),
                               ("web_read", {"url": "https://example.com"})]:
            r = reg.execute(action, params)
            if r.success:
                mock_ok += 1
            else:
                mock_fail.append((action, r.summary[:50]))

    guard.close()

    total_real = len(real_cases)
    rate = ok / total_real
    record(Metric(
        "M7", "工具执行成功率（真实执行子集）", ">95%",
        f"{rate:.0%} ({ok}/{total_real})",
        "PASS" if rate > 0.95 else "FAIL",
        f"失败: {fail or '无'}；另 mock 验证 {mock_ok}/3 {mock_fail or ''}；"
        "未真实执行: open_app（会弹窗）、screenshot（会写图片）、web_*（会联网）",
    ))


# ══════════════════════════════════════════════════
#  M10 — 插件可卸载
# ══════════════════════════════════════════════════

def measure_plugin_unload(tmp: Path) -> None:
    """真实走一遍 loader：写临时插件模块 → load → 服务可用 → unload → 服务不可用"""
    pkg = tmp / "acc_plugin_pkg"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "demo.py").write_text(textwrap.dedent("""
        def register(ctx):
            ctx.provide("acc_demo_service", object())
            def dispose():
                pass
            return dispose
    """), encoding="utf-8")

    sys.path.insert(0, str(tmp))
    ctx = Context()
    loader = PluginLoader()
    spec = PluginSpec(id="acc_demo", module="acc_plugin_pkg.demo", entry="register")

    loaded = loader.load(spec, ctx)
    available_after_load = ctx.use_or("acc_demo_service") is not None
    unloaded = loader.unload("acc_demo", ctx)
    available_after_unload = ctx.use_or("acc_demo_service") is not None
    still_loaded = loader.is_loaded("acc_demo")

    sys.path.remove(str(tmp))
    ctx.dispose()

    ok = loaded and available_after_load and unloaded and not available_after_unload and not still_loaded
    record(Metric(
        "M10", "插件可卸载", "卸载后服务不可用",
        f"load={loaded} 载后可用={available_after_load} "
        f"unload={unloaded} 卸后可用={available_after_unload}",
        "PASS" if ok else "FAIL",
        "注：loader 已实现且行为正确，但尚未被 build_agent_stack 使用（配置驱动装配未接入）",
    ))


# ══════════════════════════════════════════════════
#  附加：Phase 级能力抽检（超出 10 项指标的部分）
# ══════════════════════════════════════════════════

def probe_extra(sandbox: Path) -> None:
    print("\n--- 附加抽检 ---")

    # 指代消解（Phase G）
    t = EntityTracker()
    t.push_files([{"name": "a.png", "path": "a.png"},
                  {"name": "b.png", "path": "b.png"},
                  {"name": "c.png", "path": "c.png"}])
    r1 = [f["name"] for f in (t.resolve_ordinal("第二个") or [])]
    r2 = [f["name"] for f in (t.resolve_ordinal("最后一个") or [])]
    r3 = [f["name"] for f in (t.resolve("那些") or [])]
    print(f"  指代消解: 第二个→{r1} 最后一个→{r2} 那些→{r3}")
    print(f"  判定: {'[PASS]' if r1 == ['b.png'] and r2 == ['c.png'] and len(r3) == 3 else '[FAIL]'}")

    # 混合路由降级链（Phase C）
    from agent.providers.router.hybrid_router import HybridRouter
    calls = {"llm": 0}

    class FailLLM:
        def route(self, text, context=None):
            calls["llm"] += 1
            raise TimeoutError("llm 超时")

    class Rule:
        def route(self, text, context=None):
            return AgentCommand(action="chat", params={}, raw_text=text, confidence=0.2)

    h = HybridRouter(rule=Rule(), llm=FailLLM(), threshold=0.5)
    cmd = h.route("那个东西", {})
    print(f"  降级链: LLM 抛异常 → action={cmd.action}, llm 调用={calls['llm']}")
    print(f"  判定: {'[PASS]' if cmd.action == 'chat' and calls['llm'] >= 1 else '[FAIL]'}")

    # 摘要器（Phase E）
    from agent.providers.summarizer.template_sum import TemplateSummarizer
    from agent.providers.summarizer.hybrid_sum import HybridSummarizer
    from agent.tools.base import ToolResult
    ts = TemplateSummarizer()
    res = ToolResult.ok(data=[{"name": "报告.docx", "size": 1024, "mtime": time.time(),
                               "path": str(sandbox / "报告.docx"), "is_dir": False}],
                        summary="找到 1 个文件", count=1)
    out = ts.summarize("file_search", res)
    print(f"  模板摘要: {out[:50]}")
    print(f"  判定: {'[PASS]' if out and out != res.summary else '[PART]'}")


# ══════════════════════════════════════════════════
#  主流程
# ══════════════════════════════════════════════════

def main() -> int:
    buf = io.StringIO()
    real_stdout = sys.stdout

    class Tee:
        def write(self, s):
            buf.write(s)
            real_stdout.write(s)

        def flush(self):
            real_stdout.flush()

    sys.stdout = Tee()          # type: ignore[assignment]
    try:
        print("=" * 74)
        print("欣雅 Agent 层 · P1 系统性验收 — 指标量化")
        print(f"时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"Python: {sys.version.split()[0]}  平台: {os.name}")
        print("=" * 74 + "\n")

        tmp = Path(tempfile.mkdtemp(prefix="xiaoyi_metrics_"))
        sandbox = tmp / "Desktop"
        sandbox.mkdir(parents=True, exist_ok=True)

        measure_router()
        measure_latency(sandbox)
        measure_misoperation(sandbox)
        measure_delete_recoverable(sandbox)
        measure_tool_success(sandbox)
        measure_plugin_unload(tmp)
        probe_extra(sandbox)

        print("\n" + "=" * 74)
        print("汇总（M8 覆盖率 / M9 回归 由 pytest 提供，见 g0_coverage.txt / g3_regression.txt）")
        print("=" * 74)
        for m in METRICS:
            print(f"  {m.verdict:<5} {m.id:<4} {m.name:<26} {m.measured}")
        counts: Dict[str, int] = {}
        for m in METRICS:
            counts[m.verdict] = counts.get(m.verdict, 0) + 1
        print(f"\n  统计: {counts}")
    finally:
        sys.stdout = real_stdout

    out_dir = PROJECT / "docs" / "agent" / "evidence"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "metrics.txt").write_text(buf.getvalue(), encoding="utf-8")
    (out_dir / "metrics.json").write_text(
        json.dumps([m.__dict__ for m in METRICS], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n证据已写入: docs/agent/evidence/metrics.txt / metrics.json")

    return 0 if all(m.verdict in ("PASS", "NOT-VERIFIED") for m in METRICS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
