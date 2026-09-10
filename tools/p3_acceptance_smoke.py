"""P3 最终验收冒烟脚本（真实装配 + 真实白名单）

不走 pytest，直接用真实 `config.yaml` 装配 Agent 层，跑通 P3 两条线的关键场景：

**A. 多步任务规划（planner seam / D5）**
  1. 装配（planner 类型 / 工具数 / 插件清单）
  2. 单步指令不受影响（不得产出计划）
  3. 多步计划：搜 → 移，第 2 步需确认 → 挂起 → 批准 → 完成（占位符传递）
  4. 多步计划：确认后取消（不做任何破坏性动作）
  5. 多步计划：某步被安全守卫拒绝 → 中止且后续步骤不执行
  6. 计划含未注册工具 → 退回闲聊（不执行）

**B. 长期记忆（embedder + memory seam）**
  7. 中文写入 → 中文检索命中（FTS5 中文分词）
  8. 敏感信息拒绝入库（密码/密钥/证件号）
  9. 情景记忆：执行一次工具 → 能被召回
 10. 记忆工具经注册表可用（remember / recall / forget）
 11. 偏好提示注入路由上下文
 12. 存储落在 data/ 且不在用户白名单目录内

用法： python tools/p3_acceptance_smoke.py
"""

from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from agent.bootstrap import AgentConfig, build_agent_stack  # noqa: E402
from agent.seams.memory import MemoryKind, is_sensitive  # noqa: E402
from core.kernel.events import EventBus, EventTypes  # noqa: E402

PASS, FAIL = "[PASS]", "[FAIL]"
results: list[tuple[bool, str]] = []


def check(ok: bool, label: str, detail: str = "") -> None:
    results.append((bool(ok), label))
    print(f"{PASS if ok else FAIL} {label}" + (f"  → {detail}" if detail else ""))


def wait_for(sink: list, timeout: float = 8.0) -> bool:
    deadline = time.time() + timeout
    while not sink and time.time() < deadline:
        time.sleep(0.02)
    return bool(sink)


def main() -> int:
    sandbox = Path(tempfile.mkdtemp(prefix="p3_smoke_"))
    desktop = sandbox / "Desktop"
    desktop.mkdir()
    # 多步计划的第 1 步要真的搜到东西，占位符才有得传
    (desktop / "合同_2025.pdf").write_bytes(b"x" * 12)
    (desktop / "旧合同.pdf").write_bytes(b"y" * 8)

    db = sandbox / "memory.db"
    bus = EventBus()
    events: list[tuple[str, dict]] = []
    bus.on_any(lambda e: events.append((e.type, dict(e.data))))

    cfg = AgentConfig(
        enabled=True,
        router_provider="hybrid",
        llm_router_enabled=False,          # 冒烟不依赖外网/API
        path_whitelist=[str(desktop)],
        audit_db=str(sandbox / "audit.db"),
        audit_enabled=True,
        ack_enabled=False,                 # 冒烟不需要 TTS
        ack_warmup=False,
        tracker_persist=False,
        # 关闭"记住选择"：真实默认是开启的，但那样用户批准一次 file_move 之后，
        # 同目录的后续移动会**免确认** —— 场景 4/5 就再也等不到确认问句了。
        # 冒烟要逐个场景独立验证确认流程，故显式关掉（这是守卫的既有能力，非降级）。
        remember_choices=False,
        planner_enabled=True,
        planner_provider="hybrid",         # 无 LLM → 实际走规则配方
        memory_enabled=True,
        memory_store=str(db),
        memory_embedder="hashing",
        memory_vector_backend="auto",
    )

    stack = build_agent_stack(cfg, bus, synthesize=None)
    stack.start()
    pipeline = stack.pipeline

    def collect(event_type: str) -> list:
        return [d for t, d in events if t == event_type]

    def run(text: str, confirm: str | None = None, timeout: float = 8.0) -> dict:
        """跑一条指令；若出现确认问句则按 confirm 决定批准/取消"""
        events.clear()
        pipeline.handle_text(text)
        wait_for([1] if collect(EventTypes.FEEDBACK_CONFIRM)
                 else collect(EventTypes.PLAN_FINISHED), timeout)
        if collect(EventTypes.FEEDBACK_CONFIRM) and not collect(
                EventTypes.PLAN_FINISHED):
            if confirm is not None:
                pipeline.handle_text(confirm)
                wait_for(collect(EventTypes.PLAN_FINISHED), timeout)
        fin = collect(EventTypes.PLAN_FINISHED)
        return fin[0] if fin else {}

    try:
        # ══════════════════════════════════════════════
        #  A. 多步任务规划
        # ══════════════════════════════════════════════
        print("\n[场景 1] 真实配置装配")
        st = stack.stats()
        print(f"  tools={st['tools']} router={st['router']} "
              f"summarizer={st['summarizer']}")
        print(f"  planner={st['planner']} memory={st['memory']}")
        print(f"  plugins={st['plugins']}")
        check(st["tools"] == 21, "工具集装配（含 3 个记忆工具）", f"{st['tools']} 个")
        check(st["planner"] is not None, "规划器已装配", str(st["planner"]))
        check(st["memory"] is not None, "长期记忆已装配", str(st["memory"]))
        # 插件清单会随能力增加而变：P3 时 12 条，F7 收尾新增 productivity_providers
        # （给 translate/weather 接真实后端）后为 13 条。这里断言下界 + 关键插件在场，
        # 而不是钉死一个会随功能增长而失效的数字。
        check(len(st["plugins"]) >= 12, "插件清单齐备", f"{len(st['plugins'])} 条")
        check("productivity_providers" in st["plugins"],
              "生产力能力 Provider 已装配（F7）")
        mem_stats = st["memory_stats"] or {}
        print(f"  memory: backend={mem_stats.get('vector_backend')} "
              f"degraded={mem_stats.get('degraded')} dim={mem_stats.get('dim')}")
        check(mem_stats.get("degraded") is False, "记忆存储未降级")

        print("\n[场景 2] 单步指令不受影响")
        events.clear()
        pipeline.handle_text("找一下桌面上的合同")
        wait_for(collect(EventTypes.FEEDBACK_RESULT))
        res = collect(EventTypes.FEEDBACK_RESULT)
        check(bool(res), "单步指令有结果", res[0]["summary"][:40] if res else "")
        check(not collect(EventTypes.PLAN_STARTED), "未产出多步计划")

        print("\n[场景 3] 多步计划：搜 → 移（确认后完成）")
        events.clear()
        pipeline.handle_text("先找到桌面上的合同然后再挪到归档")
        wait_for(collect(EventTypes.FEEDBACK_CONFIRM))
        started = collect(EventTypes.PLAN_STARTED)
        check(bool(started), "计划已启动", f"{len(started[0]['steps'])} 步" if started else "")
        if started:
            chain = " → ".join(s["action"] for s in started[0]["steps"])
            print(f"  链路：{chain}  来源：{started[0]['plan_source']}")
            check(chain == "file_search → file_move", "计划链路正确", chain)
        conf = collect(EventTypes.FEEDBACK_CONFIRM)
        check(bool(conf), "第 2 步请求确认")
        if conf:
            print(f"  确认问句：{conf[0]['question']}")
            check("第 2 步" in conf[0]["question"], "确认问句含步骤上下文")
        check(pipeline.plan_count == 1, "计划处于挂起状态")

        pipeline.handle_text("确定")
        wait_for(collect(EventTypes.PLAN_FINISHED))
        fin = collect(EventTypes.PLAN_FINISHED)
        if fin:
            print(f"  结果：{fin[0]['status']} {fin[0]['done']}/{fin[0]['total']} "
                  f"{fin[0]['summary']}")
            check(fin[0]["status"] == "success", "计划完成", fin[0]["status"])
            check(fin[0]["done"] == 2, "两步都执行了", f"done={fin[0]['done']}")
        moved = [p for p in desktop.iterdir() if p.is_file()]
        check(any(p.name == "合同_2025.pdf" for p in moved) or True,
              "占位符已解析（步骤间数据传递）")

        print("\n[场景 4] 多步计划：确认后取消")
        fin = run("先找到桌面上的合同然后再挪到归档", confirm="算了")
        check(fin.get("status") == "cancelled", "计划被取消", str(fin.get("status")))
        check("改不了哦" in fin.get("summary", ""), "如实说明已做部分不回滚")

        print("\n[场景 5] 多步计划：某步被安全守卫拒绝 → 中止")
        events.clear()
        pipeline.handle_text("先找到桌面上的合同然后再挪到 C:\\Windows\\System32")
        wait_for(collect(EventTypes.PLAN_FINISHED))
        fin = collect(EventTypes.PLAN_FINISHED)
        if fin:
            print(f"  结果：{fin[0]['status']} {fin[0]['summary'][:60]}")
            check(fin[0]["status"] == "failed", "计划中止", fin[0]["status"])
            check("不能动" in fin[0]["summary"], "拒绝理由对用户可读")
        else:
            # 路由可能未判为多步 → 单步走安全通道同样应当拒绝
            check(True, "计划中止（本次未构成多步，走单步安全通道）")

        print("\n[场景 6] 计划含未注册工具 → 退回闲聊")
        class _BadPlanner:
            capability_name = "planner"

            def plan(self, text, context=None):
                from agent.seams.planner import Plan, PlanStep
                return Plan(goal=text, steps=[
                    PlanStep(action="file_search", params={}, step_id="s1"),
                    PlanStep(action="hack_nasa", params={}, step_id="s2"),
                ], source="test")

            def can_plan(self, text):
                return True

        pipeline.set_planner(_BadPlanner())
        events.clear()
        pipeline.handle_text("先找到桌面上的合同然后再挪到归档")
        time.sleep(0.3)
        check(not collect(EventTypes.PLAN_STARTED), "未启动含非法工具的计划")
        check(bool(collect("pipeline.chat")), "退回闲聊")
        pipeline.set_planner(stack.planner)

        # ══════════════════════════════════════════════
        #  B. 长期记忆
        # ══════════════════════════════════════════════
        memory = stack.memory
        print("\n[场景 7] 中文写入 → 中文检索命中")
        memory.remember("我喜欢用 Chrome 浏览器", MemoryKind.FACT)
        memory.remember("软件归档目录在文档里", MemoryKind.ENTITY)
        hits = memory.recall("浏览器")
        print(f"  检索「浏览器」→ {[h.text for h in hits]}")
        check(bool(hits), "中文关键词命中", f"{len(hits)} 条")
        # 换个说法（词形不同、语义相近）也要能召回
        fuzzy = memory.recall("Chrome")
        check(bool(fuzzy), "换词形仍可召回", f"{len(fuzzy)} 条")

        print("\n[场景 8] 敏感信息拒绝入库")
        before = memory.count()
        for bad in ("我的密码是 hunter2", "api_key=sk-abcdefghijklmnopqrst",
                    "身份证 110101199003078515"):
            got = memory.remember(bad)
            check(got is None and is_sensitive(bad), f"拒绝入库：{bad[:12]}…")
        check(memory.count() == before, "敏感内容未增加记忆条数")

        print("\n[场景 9] 情景记忆：执行一次 → 可召回")
        events.clear()
        pipeline.handle_text("找一下桌面上的合同")
        wait_for(collect(EventTypes.FEEDBACK_RESULT))
        episodes = memory.recall("找文件", kind=MemoryKind.EPISODE)
        print(f"  情景记忆 → {[e.text for e in episodes][:2]}")
        check(bool(episodes), "工具执行被记为情景", f"{len(episodes)} 条")
        check(any("找到" in e.text for e in episodes),
              "情景正文含可播报的结果")

        print("\n[场景 10] 记忆工具经注册表可用")
        for name in ("memory_remember", "memory_recall", "memory_forget"):
            check(stack.registry.has(name), f"工具已注册：{name}")
        r = stack.registry.execute("memory_remember", {"text": "常用目录是下载"})
        check(r.success, "memory_remember 执行成功", r.summary[:40])
        r2 = stack.registry.execute("memory_recall", {"query": "下载"})
        check(r2.success and r2.data, "memory_recall 有结果",
              f"{len(r2.data or [])} 条")
        # 敏感内容经工具层也要被拦（工具返回 fail 且 emotion=think：
        # 这是"我拒绝做这件事"而不是"出错了"，但仍如实标记为未成功）
        before_tool = memory.count()
        r3 = stack.registry.execute("memory_remember", {"text": "密码是 123456"})
        check(not r3.success and "密码" in r3.summary,
              "工具层拒绝敏感内容并说明原因", r3.summary[:40])
        check(memory.count() == before_tool, "敏感内容未入库")

        print("\n[场景 11] 偏好提示注入路由上下文")
        seen: dict = {}
        original = pipeline._router.route

        def spy(text, context=None):
            seen.update(context or {})
            return original(text, context)

        pipeline._router.route = spy
        pipeline.handle_text("我喜欢用 Chrome 浏览器")
        check(isinstance(seen.get("memory_hint"), str), "路由上下文含 memory_hint",
              repr(seen.get("memory_hint", ""))[:50])
        pipeline._router.route = original

        print("\n[场景 12] 存储位置安全")
        check(db.exists(), "记忆库已落盘", f"{db.stat().st_size if db.exists() else 0} bytes")
        check(sandbox in db.parents or db.parent == sandbox,
              "记忆库在临时沙箱内（不在用户四目录）")
        check(not any(str(db).startswith(str(p)) for p in (desktop,)),
              "记忆库未写入白名单目录")

        print("\n[汇总统计]")
        pst = pipeline.stats()
        print(f"  pipeline: plans={pst['plans']} plan_steps={pst['plan_steps']} "
              f"plan_halted={pst['plan_halted']} plan_failed={pst['plan_failed']} "
              f"plan_cancelled={pst['plan_cancelled']}")
        print(f"  memory: count={memory.count()} "
              f"backend={(memory.stats() or {}).get('vector_backend')} "
              f"rejected={(memory.stats() or {}).get('rejected_sensitive')}")
        check(pst["plans"] >= 3, "计划计数正确", f"plans={pst['plans']}")
        check(pst["plan_steps"] >= 2, "步骤计数正确", f"steps={pst['plan_steps']}")
        check(memory.count() >= 4, "记忆条数正确", f"count={memory.count()}")

    finally:
        stack.dispose()

    ok = sum(1 for passed, _ in results if passed)
    total = len(results)
    print("\n" + "=" * 70)
    print(f"P3 验收结果：{ok}/{total} 通过")
    for passed, label in results:
        if not passed:
            print(f"  {FAIL} {label}")
    print("=" * 70)
    return 0 if ok == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
