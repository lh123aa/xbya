"""LLM 能力探测：某个模型能不能胜任**我们实际让它干的活**（P4-B4）

## 为什么"能连上"不等于"能用"

`probe_free_llm.py` 只回答"端点活着吗"。但本项目对 LLM 有三类**具体**要求，
一个 1.5B 的本地小模型可以轻松通过"能回话"，却在下面三件事上翻车：

| 任务 | 要求 | 失败的后果 |
|------|------|-----------|
| ① 闲聊 | 返回一句自然的中文 | 降级路径失效（用户说话没反应） |
| ② 多步规划 | 吐**合法 JSON**、工具名在注册表内、步骤 ≥2 | 规划器"偶尔拆不出来"（看起来像模型不行，实际是能力不够） |
| ③ 工具选择 | 实现 `chat_with_tools` 并选对工具 | **LLM 路由兜底静默失效** —— 用户说法超出规则词表时不再有人接手 |

③ 尤其重要：本项目已经因为"接错总线"吃过一次**静默降级**的亏（债务 D14）。
"换了个引擎，结果兜底没了，而所有测试仍然全绿"是同一类事故。
所以这里把"引擎是否具备该能力"变成**测得出来的事实**，而不是假设。

## 用法

    python tools/probe_llm_capability.py --local                  # 本地 Ollama
    python tools/probe_llm_capability.py --engine ollama --model qwen2.5:1.5b
    python tools/probe_llm_capability.py --engine universal       # 用 config.yaml 里的云端配置
    python tools/probe_llm_capability.py --engine universal --base-url ... --model ... --api-key-env K
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

PASS, FAIL, SKIP = "[PASS]", "[FAIL]", "[SKIP]"
results: list = []


def check(ok, label, detail=""):
    results.append((bool(ok), label))
    print(f"{PASS if ok else FAIL} {label}" + (f"  → {detail}" if detail else ""))


def skipped(label, why=""):
    results.append((None, label))
    print(f"{SKIP} {label}" + (f"  → {why}" if why else ""))


def _build_engine(args):
    """按参数构造 LLM 引擎（不打印任何密钥）

    `universal` 模式**从 config.yaml 读项目真实配置**（含 api_key），
    只覆盖显式给出的字段 —— 否则会退到环境变量、拿不到项目在用的 key，
    探测结论就与真实运行无关了。
    """
    if args.engine == "ollama":
        from plugins.llm.ollama.plugin import OllamaLLM
        return OllamaLLM(base_url=args.base_url or "http://localhost:11434",
                         model=args.model or "qwen2.5:1.5b")

    from plugins.llm.openrouter.plugin import UniversalLLM
    kw: dict = {}
    try:
        import yaml
        cfg = yaml.safe_load((PROJECT / "config.yaml").read_text(encoding="utf-8"))
        params = ((cfg.get("plugins") or {}).get("llm") or {}).get("params") or {}
        # 只透传插件认识的参数；api_key 只进内存，绝不打印
        # ⚠️ `model` 必须在这一串里。第一版漏了它，于是探针自报"用 config.yaml
        # 里的云端配置"，实际用的是插件默认模型（llama-3.1-8b-instant）——
        # **探针没有在测它声称测的东西**，而输出看不出来（P4-B4 第 2 处自查修掉）。
        for k in ("api_key", "model", "base_url", "provider", "system_prompt",
                  "max_tokens", "temperature", "fallback_api_key",
                  "fallback_base_url", "fallback_model"):
            if params.get(k) not in (None, ""):
                kw[k] = params[k]
    except Exception as e:
        print(f"[注意] 读 config.yaml 失败，改用环境变量: {type(e).__name__}: {e}")

    if args.base_url:
        kw["base_url"] = args.base_url
    if args.model:
        kw["model"] = args.model
    return UniversalLLM(**kw)


def main() -> int:
    ap = argparse.ArgumentParser(description="LLM 能力探测（P4-B4）")
    ap.add_argument("--engine", choices=["ollama", "universal"], default="ollama")
    ap.add_argument("--model", default=None)
    ap.add_argument("--base-url", default=None)
    ap.add_argument("--local", action="store_true", help="等价于 --engine ollama")
    ap.add_argument("--planner-goal", default=None, help="规划任务的目标句")
    args = ap.parse_args()
    if args.local:
        args.engine = "ollama"

    print("=" * 74)
    print("LLM 能力探测（能不能干我们实际让它干的活）")
    print("=" * 74)

    try:
        engine = _build_engine(args)
    except Exception as e:
        print(f"构造引擎失败: {type(e).__name__}: {e}")
        return 1

    info = {}
    try:
        info = engine.get_model_info() or {}
    except Exception:
        pass
    # 只打印安全的元信息（不打印 api_key）
    safe = {k: v for k, v in info.items() if "key" not in k.lower()}
    print(f"引擎: {type(engine).__name__}   元信息: {json.dumps(safe, ensure_ascii=False)[:200]}")

    avail = False
    try:
        avail = bool(engine.is_available())
    except Exception as e:
        print(f"is_available() 抛异常: {e}")
    check(avail, "引擎自报可用（is_available）")

    # ── ① 闲聊（连测 3 次：区分"冷启动把模型读进内存"与"热态速度"）──
    #
    # 为什么必须分开测：第一次调用常包含模型加载（本机 1.5B 也要读 ~1GB），
    # 把冷启动耗时当成"模型慢"会得出错误结论；反过来把热态耗时当成"首句体验"
    # 也是错的。项目的指标是"感知延迟 <1.5s / 总延迟 <3s"，两个数都要看。
    print("\n[①] 闲聊：能不能回一句自然的中文（连测 3 次，区分冷/热）")
    texts, times = [], []
    for i in range(3):
        t0 = time.perf_counter()
        try:
            text = engine.chat("你好呀，介绍一下你自己", context=None)
        except Exception as e:
            text = None
            print(f"  第{i + 1}次调用异常: {type(e).__name__}: {e}")
        times.append((time.perf_counter() - t0) * 1000)
        texts.append(text)
        print(f"  第{i + 1}次 {times[-1]:.0f}ms  回复={(text or '')[:48]!r}")
    check(bool(texts[0] and texts[0].strip()), "闲聊有回复（首次）",
          f"{len(texts[0] or '')} 字")
    if len(times) >= 2:
        print(f"  冷启动 {times[0]:.0f}ms → 热态 {min(times[1:]):.0f}ms")

    # ── ② 多步规划（用项目真实的 LLM 规划器，不手搓提示词）──
    print("\n[②] 多步规划：能不能吐出**合法**的计划（工具名合法 + ≥2 步）")
    goal = args.planner_goal or "把下载目录里的安装包都挪到软件归档，然后清理掉下载目录里的空文件夹"
    try:
        # 用**真实装配**拿注册表与 schema，而不是手搓工具列表 ——
        # 第一版手搓时踩了 `all_file_tools() missing 1 required positional argument: 'guard'`：
        # 工具工厂的参数会随实现变化，手搓的探针会先坏掉，然后给出误导性的 SKIP。
        from agent.bootstrap import AgentConfig, build_agent_stack
        from core.kernel.events import EventBus
        from agent.providers.planner.llm_planner import LLMPlanner

        cfg = AgentConfig()
        bus = EventBus()
        stack = build_agent_stack(cfg, bus)
        try:
            reg = stack.registry
            # ⚠️ 必须在 dispose() **之前**把合法工具名快照下来。
            # 第一版在 finally 之后才调 `reg.names()` 去比对，而 dispose() 会卸载全部插件，
            # 于是注册表变成空集 ⇒ **每一个工具名都被判成"非法"**，
            # 探针稳定输出一条假 FAIL（"非法=['file_search','file_move','file_delete']"），
            # 而这三个名字明明都在 21 个工具里（P4-B4 第 1 处自查修掉）。
            valid_names = set(reg.names())
            ctx = {"available_actions": sorted(valid_names),
                   "tool_schemas": reg.to_llm_schemas(),
                   # 与真实规划器一致：把白名单目录注入提示词（缺它模型会编造路径）
                   "dir_aliases": {p.name: str(p) for p in stack.safety.whitelist_roots()}}
            # 参数名是 `llm_call`（不是 `llm`）—— 手写探针容易记错签名；
            # 记住教训：探针要贴着真实签名写，否则它自己先坏掉并输出误导性的 SKIP。
            planner = LLMPlanner(llm_call=engine.chat)
            t1 = time.perf_counter()
            plan = planner.plan(goal, ctx)
            dt1 = (time.perf_counter() - t1) * 1000
        finally:
            stack.dispose()

        if plan is None:
            check(False, "拆出多步计划", f"{dt1:.0f}ms 内返回 None（模型没给出合法 JSON）")
        else:
            names = plan.action_names()
            unknown = [a for a in names if a not in valid_names]
            print(f"  {dt1:.0f}ms  来源={plan.source} 步数={len(plan.steps)}")
            for s in plan.steps:
                print(f"    {s.step_id}: {s.action}({s.params})")
            check(not unknown, "计划里的工具名全部合法", f"非法={unknown}" if unknown else "")
            check(len(plan.steps) >= 2, "至少 2 步", f"{len(plan.steps)} 步")
    except Exception as e:
        import traceback
        skipped("多步规划", f"探测过程异常: {type(e).__name__}: {e}")
        if "--trace" in sys.argv:
            traceback.print_exc()

    # ── ③ 工具选择（function calling）──
    print("\n[③] 工具选择：引擎有没有 chat_with_tools，且能不能选对工具")
    has_tools = callable(getattr(engine, "chat_with_tools", None))
    if not has_tools:
        # 这条是**结论**不是失败：它决定"能不能把这个引擎当唯一的 LLM"
        check(False, "引擎实现 chat_with_tools（LLM 路由兜底的前提）",
              f"{type(engine).__name__} 没有这个方法 → 换到这个引擎会让路由兜底**静默失效**")
    else:
        check(True, "引擎实现 chat_with_tools")
        try:
            from agent.providers.router.llm_router import TOOL_SCHEMAS
            probe = engine.chat_with_tools(
                "你是意图路由器，从工具里选一个并填参数", "帮我看看电脑还有多少电", TOOL_SCHEMAS)
            print(f"  返回: {probe}")
            check(bool(probe and probe.get("name")), "真实函数调用返回了工具名",
                  str((probe or {}).get("name")))
            check(bool(probe and probe.get("name") in [t["name"] for t in TOOL_SCHEMAS]),
                  "选中的工具在 schema 清单内")
        except Exception as e:
            check(False, "function calling 调用成功", f"{type(e).__name__}: {e}")

    ok = sum(1 for p, _ in results if p)
    bad = sum(1 for p, _ in results if p is False)
    sk = sum(1 for p, _ in results if p is None)
    print("\n" + "-" * 74)
    print(f"能力探测：{ok}/{ok + bad} 通过，{bad} 失败，{sk} 跳过")
    for p, label in results:
        if p is not True:
            print(f"  {FAIL if p is False else SKIP} {label}")
    print("=" * 74)
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
