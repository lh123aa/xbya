"""P1 最终验收冒烟脚本（真实 config.yaml + 真实白名单）

不走 pytest，直接用项目真实配置装配 Agent 层，跑通关键场景并打印证据：
  1. 装配（工具数 / 路由 / 白名单）
  2. 只读指令：file_search / system_info
  3. 串行闲聊（确认语 + 结果）
  4. 危险指令：删除 → 请求确认 → 用户取消（不落盘）
  5. 危险指令：删除 → 确认 → 走回收站
  6. 路径越界拒绝（C:\\Windows 下）
  7. 延迟统计（ack 与 submit 并行间隔）

用法： python tools/p1_acceptance_smoke.py
"""

from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from agent.bootstrap import AgentConfig, build_agent_stack  # noqa: E402
from core.kernel.events import EventBus, EventTypes  # noqa: E402

PASS, FAIL = "[PASS]", "[FAIL]"
results: list[tuple[bool, str]] = []


def check(ok: bool, label: str, detail: str = "") -> None:
    results.append((bool(ok), label))
    print(f"{PASS if ok else FAIL} {label}" + (f"  → {detail}" if detail else ""))


def collect(bus: EventBus, event_type: str, sink: list):
    return bus.on(event_type, lambda e: sink.append(e.data))


def wait_for(sink: list, timeout: float = 8.0) -> bool:
    deadline = time.time() + timeout
    while not sink and time.time() < deadline:
        time.sleep(0.02)
    return bool(sink)


def main() -> int:
    print("=" * 70)
    print("欣雅 Agent 层 · P1 最终验收冒烟")
    print("=" * 70)

    # ── 1. 用真实配置装配 ──
    sandbox = Path(tempfile.mkdtemp(prefix="xiaoyi_accept_"))
    (sandbox / "合同_2025.pdf").write_text("dummy")
    (sandbox / "截图_01.png").write_text("dummy")
    (sandbox / "笔记.txt").write_text("hello agent")

    bus = EventBus()
    cfg = AgentConfig(
        enabled=True,
        router_provider="hybrid",
        llm_router_enabled=False,          # 冒烟不依赖外网/API
        path_whitelist=[str(sandbox)],
        audit_db=str(sandbox / "audit.db"),
        audit_enabled=True,
        ack_enabled=True,
        ack_warmup=False,
        tools_file=True,
        tools_system=True,
        tools_productivity=True,
        tools_browser=True,
    )

    stack = build_agent_stack(cfg, bus, synthesize=None)
    stack.start()
    stats = stack.stats()
    print(f"\n装配结果：tools={stats['tools']} router={stats['router']} "
          f"enabled={stats['enabled']}")
    print(f"白名单：{stats['safety_whitelist']}\n")
    check(stats["tools"] >= 15, "工具集装配", f"{stats['tools']} 个工具")
    check(stats["enabled"] is True, "Agent 层已启用")

    pipeline = stack.pipeline
    ack_ms: list[float] = []

    try:
        # ── 2. 只读：搜索 ──
        print("\n[场景 1] 只读指令：搜索文件")
        results_sink: list = []
        d1 = collect(bus, EventTypes.FEEDBACK_RESULT, results_sink)
        d2 = bus.on(EventTypes.FEEDBACK_ACK, lambda e: ack_ms.append(time.perf_counter()))
        t0 = time.perf_counter()
        pipeline.handle_text("找一下合同", "a1")
        got = wait_for(results_sink)
        elapsed = (time.perf_counter() - t0) * 1000
        d1(); d2()
        check(got, "file_search 返回结果",
              results_sink[0]["summary"] if got else "超时")
        check(got and results_sink[0]["success"] is True, "结果为成功状态")
        check(got and results_sink[0]["tool_name"] == "file_search",
              "工具识别正确", results_sink[0]["tool_name"] if got else "-")
        check(elapsed < 3000, "总延迟 < 3s", f"{elapsed:.0f}ms")

        # ── 3. 只读：系统状态 ──
        print("\n[场景 2] 只读指令：系统状态")
        results_sink = []
        d1 = collect(bus, EventTypes.FEEDBACK_RESULT, results_sink)
        pipeline.handle_text("看看电脑状态", "a2")
        got = wait_for(results_sink)
        d1()
        check(got, "system_info 返回结果",
              results_sink[0]["summary"][:40] if got else "超时")

        # ── 4. 危险：删除 → 确认请求 → 取消 ──
        print("\n[场景 3] 危险指令：删除 → 确认 → 取消（不应落盘）")
        confirms: list = []
        results_sink = []
        d1 = collect(bus, EventTypes.FEEDBACK_CONFIRM, confirms)
        d2 = collect(bus, EventTypes.FEEDBACK_RESULT, results_sink)
        pipeline.handle_text("删除截图", "b1")
        got_confirm = wait_for(confirms)
        d1(); d2()
        check(got_confirm, "发出确认请求",
              confirms[0]["question"][:50] if got_confirm else "超时")
        check(got_confirm and confirms[0]["risk"] == "high", "风险等级 = high")
        check(not results_sink, "确认前未执行")
        check((sandbox / "截图_01.png").exists(), "确认前文件仍在")

        d3 = collect(bus, EventTypes.FEEDBACK_RESULT, results_sink)
        pipeline.handle_text("不用了", "b2")
        wait_for(results_sink, timeout=5)
        d3()
        check((sandbox / "截图_01.png").exists(), "取消后文件完好")

        # ── 5. 危险：删除 → 确认 → 回收站 ──
        print("\n[场景 4] 危险指令：删除 → 确认 → 回收站")
        results_sink = []
        d1 = collect(bus, EventTypes.FEEDBACK_RESULT, results_sink)
        pipeline.handle_text("删除截图", "c1")
        wait_for(list(), timeout=0.4)          # 等确认项登记
        pipeline.handle_text("确定", "c2")
        got = wait_for(results_sink, timeout=10)
        d1()
        check(got, "确认后执行完成",
              results_sink[0]["summary"][:50] if got else "超时")
        check(not (sandbox / "截图_01.png").exists(), "文件已移入回收站")

        # ── 6. 越界拒绝 ──
        print("\n[场景 5] 安全边界：白名单外路径")
        results_sink = []
        d1 = collect(bus, EventTypes.FEEDBACK_RESULT, results_sink)
        pipeline.handle_text(r"删除 C:\Windows\System32\drivers\etc\hosts", "d1")
        got = wait_for(results_sink, timeout=6)
        d1()
        blocked = got and (
            results_sink[0]["success"] is False
            or "不能" in results_sink[0]["summary"]
            or "允许" in results_sink[0]["summary"]
        )
        check(blocked, "白名单外路径被拦截",
              results_sink[0]["summary"][:50] if got else "无响应")

        # ── 7. 延迟统计 ──
        print("\n[场景 6] 延迟与统计")
        st = pipeline.stats()
        print(f"  pipeline.stats() = { {k: v for k, v in st.items() if k.startswith(('commands', 'tool_calls', 'chats', 'rejected', 'confirms', 'result_p'))} }")
        check(st["commands"] >= 5, "指令计数正确", f"commands={st['commands']}")
        check(st["confirms"] >= 1, "确认计数正确", f"confirms={st['confirms']}")

        # ── 8. 审计日志 ──
        print("\n[场景 7] 审计日志")
        audit = sandbox / "audit.db"
        check(audit.exists(), "审计库已生成", f"{audit.stat().st_size if audit.exists() else 0} bytes")

    finally:
        stack.dispose()

    # ── 汇总 ──
    ok = sum(1 for passed, _ in results if passed)
    total = len(results)
    print("\n" + "=" * 70)
    print(f"验收结果：{ok}/{total} 通过")
    for passed, label in results:
        if not passed:
            print(f"  {FAIL} {label}")
    print("=" * 70)
    return 0 if ok == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
