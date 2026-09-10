"""F7 真实服务验证：翻译 / 天气 / 提醒

对应验收报告里的开放项 **F7**（`translate` / `weather` 一直"依赖注入桩，未接真实服务"）。

## 这一轮之前，这三个工具在生产里是什么状态

| 工具 | 之前 | 根因 |
|------|------|------|
| `translate` | 永远回"我这边还没接上翻译能力呢" | `SVC_TRANSLATE` **全项目无人 provide** |
| `weather` | 永远回"我这边还没接上天气服务呢" | `SVC_WEATHER` **全项目无人 provide** |
| `reminder` | 回"好的，30分钟后我会提醒你" → **永远不响** | `due_now()` **全项目无调用者** |

第三个是最严重的：它不是缺能力，而是**对用户撒谎**。

## 本脚本验什么

- **翻译**：走真实 LLM 真的翻出译文（不是桩），并验证提示词确实被调用
- **天气**：走真实 Open-Meteo 拿到真实温度（不是桩），并验证失败路径不抛异常
- **提醒**：真的设一个短提醒 → 等它到点 → 断言总线收到了 `reminder.due`
  （**这是"不撒谎"的直接证据**），并断言调度器可被干净停掉

用法：
    python tools/verify_f7_real_services.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

PASS, FAIL, SKIP = "[PASS]", "[FAIL]", "[SKIP]"
results: list = []


def check(ok: bool, label: str, detail: str = "") -> None:
    results.append((bool(ok), label))
    print(f"{PASS if ok else FAIL} {label}" + (f"  → {detail}" if detail else ""))


def skipped(label: str, why: str) -> None:
    results.append((None, label))
    print(f"{SKIP} {label}  → {why}")


def main() -> int:
    print("=" * 72)
    print("F7 真实服务验证（translate / weather / reminder）")
    print(f"时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 72)

    from core.app import XiaoyiApp
    from core.kernel.events import EventTypes

    app = XiaoyiApp()
    if not app.initialize():
        print(f"{FAIL} 应用初始化失败")
        return 1

    try:
        stack = app.agent_stack
        if stack is None:
            print(f"{FAIL} Agent 层未装配")
            return 1
        reg = stack.registry
        bus = stack.bus

        # ── 服务是否真的被注册 ──
        print("\n[F7-a] 三个能力服务是否真的接上了")
        from agent.plugins import (
            SVC_REMINDER_SCHEDULER,
            SVC_TRANSLATE,
            SVC_WEATHER,
        )

        ctx = getattr(stack, "ctx", None)
        if ctx is None:
            check(False, "AgentStack 暴露装配上下文（用于查服务）")
        else:
            check(ctx.has(SVC_TRANSLATE), f"{SVC_TRANSLATE} 已注册（不再是 None 占位）")
            check(ctx.has(SVC_WEATHER), f"{SVC_WEATHER} 已注册（不再是 None 占位）")
            check(ctx.use_or(SVC_TRANSLATE) is not None, "translate 服务非 None")
            check(ctx.use_or(SVC_WEATHER) is not None, "weather 服务非 None")
            sched = ctx.use_or(SVC_REMINDER_SCHEDULER)
            check(sched is not None and sched.running,
                  f"{SVC_REMINDER_SCHEDULER} 已注册且线程在跑",
                  f"running={getattr(sched, 'running', None)}")
            check(getattr(stack, "reminder_scheduler", None) is sched,
                  "AgentStack 暴露同一个调度器实例（可观测）")

        # ── F7-b 翻译：真实 LLM ──
        print("\n[F7-b] translate 走真实 LLM")
        t = reg.get("translate")
        check(t is not None, "translate 已注册")
        t0 = time.perf_counter()
        res = t.execute({"text": "今天天气不错，我们去公园散步吧。",
                         "target_lang": "en"})
        dt = (time.perf_counter() - t0) * 1000
        print(f"  ({dt:.0f}ms) success={res.success}")
        print(f"  summary: {res.summary[:120]!r}")
        if not res.success:
            # 未接入时工具会回固定的"还没接上"文案 —— 这就是失败证据
            check(False, "translate 真的翻译了（而不是回'还没接上'）",
                  res.summary[:60])
        else:
            translated = ""
            if isinstance(res.data, dict):
                translated = str(res.data.get("translated") or "")
            check(not res.summary.startswith("我这边还没接上"),
                  "translate 不再回'还没接上翻译能力'")
            check(bool(translated), "拿到了译文", f"{translated[:70]!r}")
            check(any(c.isascii() and c.isalpha() for c in translated),
                  "译文是英文（含 ASCII 字母）", translated[:60])
            check("译文：" not in res.summary and not translated.startswith('"'),
                  "装饰性前缀/引号已清洗", translated[:50])

        # ── F7-c 天气：真实 Open-Meteo ──
        print("\n[F7-c] weather 走真实 Open-Meteo")
        w = reg.get("weather")
        check(w is not None, "weather 已注册")
        t0 = time.perf_counter()
        res = w.execute({"city": "上海"})
        dt = (time.perf_counter() - t0) * 1000
        print(f"  ({dt:.0f}ms) success={res.success}")
        print(f"  summary: {res.summary[:120]!r}")
        if not res.success:
            skipped("weather 拿到真实天气",
                    f"服务不可达或未接入（{res.summary[:50]!r}）")
        else:
            check(not res.summary.startswith("我这边还没接上"),
                  "weather 不再回'还没接上天气服务'")
            d = res.data if isinstance(res.data, dict) else {}
            temp = d.get("temp")
            print(f"  data: {d}")
            check(temp is not None and -60 <= float(temp) <= 60,
                  "温度在合理区间（真实观测值）", f"{temp}度")
            check(bool(d.get("desc")), "有天气描述", str(d.get("desc")))
            check(bool(d.get("city")), "有城市名", str(d.get("city")))

        # 未知城市要走可读失败，而不是抛异常
        res = w.execute({"city": "这个城市名一定不存在zzzz"})
        check(not res.success, "未知城市返回可读失败", res.summary[:50])
        # 空城市 + 无默认城市 → 也应可读失败（不做 IP 定位）
        res = w.execute({})
        check(not res.success, "没说城市时如实失败（不做 IP 定位）",
              res.summary[:50])

        # ── F7-d 提醒：真的会响 ──
        print("\n[F7-d] reminder 到点真的发事件（「不撒谎」的直接证据）")
        r = reg.get("reminder")
        check(r is not None, "reminder 已注册")

        events: list = []
        bus.on(EventTypes.REMINDER_DUE,
               lambda e: events.append(dict(e.data)))

        # 设一个 0.1 分钟（6 秒）后的提醒 —— 工具 schema 的最小值是 0.1
        res = r.execute({"what": "喝水", "minutes": 0.1})
        check(res.success, "提醒设置成功", res.summary[:50])
        rid = ""
        if isinstance(res.data, dict):
            rid = str(res.data.get("id") or "")
        print(f"  提醒 id={rid} 到期时间={res.data.get('when_text') if isinstance(res.data, dict) else '?'}")

        pending = r.pending()
        check(any(x.id == rid for x in pending), "提醒进入待触发队列",
              f"pending={len(pending)}")

        # 等它到点（调度器 1s 一跳，6 秒的提醒最多等 20 秒）
        deadline = time.time() + 20
        while time.time() < deadline and not events:
            time.sleep(0.25)

        if events:
            ev = events[0]
            print(f"  收到事件: {ev}")
            check(True, "到点发出了 reminder.due 事件（不再是空承诺）")
            check(ev.get("what") == "喝水", "事件带对了提醒内容",
                  str(ev.get("what")))
            check(bool(ev.get("text")), "事件带可播报文案", str(ev.get("text")))
            check(not any(x.id == rid for x in r.pending()),
                  "触发后已从待触发队列移除")
        else:
            check(False, "到点发出了 reminder.due 事件（不再是空承诺）",
                  "20 秒内未收到事件")

        sched = getattr(stack, "reminder_scheduler", None)
        if sched is None:
            skipped("调度器统计可读", "未装配调度器")
        else:
            check(sched.fired_count >= 1, "调度器统计到已播报条数",
                  f"fired={sched.fired_count}")

    finally:
        # dispose 会停掉调度线程；停不掉的话这里会留下悬挂线程（D8 的教训）
        app._teardown_agent_layer()

    import threading
    alive = [t.name for t in threading.enumerate()
             if t.name == "reminder-scheduler" and t.is_alive()]
    check(not alive, "调度线程已随 Agent 层释放而停止", f"存活={alive}")

    ok = sum(1 for x, _ in results if x is True)
    bad = sum(1 for x, _ in results if x is False)
    sk = sum(1 for x, _ in results if x is None)
    print("\n" + "=" * 72)
    print(f"F7 结果：{ok}/{ok + bad} 通过，{bad} 失败，{sk} 跳过")
    for x, label in results:
        if x is not True:
            print(f"  {FAIL if x is False else SKIP} {label}")
    print("=" * 72)
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
