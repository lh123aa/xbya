"""免费 LLM 端点矩阵探测（P4-B4 工具）

## 为什么要专门做这个

本项目的 LLM 走免费档：主端点 Groq 免费档有**每日 token 上限**（实测 200,000 TPD，
打满后整天不可用），备用端点 OpenRouter 的 `:free` 模型有 **20 RPM / 50 RPD**。
于是"今天能不能跑验收"取决于**配额**，而不是代码。

`probe_llm_capability.py` 只探**一个**端点。配额打满时它只会给出一条
"拆出多步计划 FAIL"，看起来像模型/代码坏了 —— 但真相是外部配额。
这个脚本负责把"到底哪个免费端点现在能用、能干什么活"变成一次可复跑的事实。

## 三类活（与 probe_llm_capability.py 同一套判据）

| 任务 | 判据 |
|------|------|
| ① 闲聊 | 返回非空中文 |
| ② 多步规划 | 真实 `LLMPlanner` 吐出计划、工具名全在注册表内、步数 ≥2 |
| ③ 工具选择 | `chat_with_tools` 返回了 schema 清单内的工具名 |

## 用法

    python tools/probe_free_llm_matrix.py --list                # 只列 OpenRouter 现有 :free 模型
    python tools/probe_free_llm_matrix.py --probe --max-models 6
    python tools/probe_free_llm_matrix.py --probe --models a,b
    python tools/probe_free_llm_matrix.py --probe --engine ollama --models qwen2.5:1.5b

**绝不打印密钥**：只读 `config.yaml` 的 fallback 凭据进内存，输出里只出现模型名与状态码。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
EVIDENCE = PROJECT / "docs" / "agent" / "evidence" / "p4"


def _load_fallback_creds() -> dict:
    """从 config.yaml 读备用端点凭据（只进内存，不打印）"""
    import yaml
    cfg = yaml.safe_load((PROJECT / "config.yaml").read_text(encoding="utf-8"))
    params = ((cfg.get("plugins") or {}).get("llm") or {}).get("params") or {}
    return {
        "api_key": params.get("fallback_api_key") or "",
        "base_url": (params.get("fallback_base_url") or "").rstrip("/"),
        "fallback_model": params.get("fallback_model") or "",
    }


def _list_free_models() -> list:
    """公开端点，无需鉴权：列出 OpenRouter 当前的免费模型"""
    import requests
    r = requests.get(OPENROUTER_MODELS_URL, timeout=30)
    r.raise_for_status()
    out = []
    for m in r.json().get("data") or []:
        mid = m.get("id") or ""
        pricing = m.get("pricing") or {}
        # `:free` 后缀是权威标记；同时也接受价格全 0 的（有些模型两种都算免费）
        is_free = mid.endswith(":free") or (
            str(pricing.get("prompt", "1")) in ("0", "0.0")
            and str(pricing.get("completion", "1")) in ("0", "0.0")
        )
        if is_free:
            out.append({
                "id": mid,
                "ctx": m.get("context_length"),
                "name": m.get("name"),
                "tools": "tools" in (m.get("supported_parameters") or []),
            })
    out.sort(key=lambda d: d["id"])
    return out


def _make_engine(model: str, creds: dict, engine: str):
    """构造待测引擎（不打印任何密钥）"""
    if engine == "ollama":
        from plugins.llm.ollama.plugin import OllamaLLM
        return OllamaLLM(base_url="http://localhost:11434", model=model)

    from plugins.llm.openrouter.plugin import UniversalLLM
    if not creds["api_key"] or not creds["base_url"]:
        raise RuntimeError("config.yaml 里没有备用端点凭据，无法探测 OpenRouter 免费模型")
    # 刻意**不接备用**：探测时必须让"这个模型自己"的成败显形，
    # 否则主端点 429 会被备用端点悄悄兜住，测出来的结论属于别的模型。
    return UniversalLLM(
        api_key=creds["api_key"],
        base_url=creds["base_url"],
        model=model,
        provider="openrouter",
        max_tokens=1536,
    )


def _flatten_schemas(schemas: list) -> list:
    """把工具 schema 统一成 `chat_with_tools` 要的**扁平**格式

    ⚠️ 项目里同时存在两种"llm schema"，**名字一样、形制不同**：

    | 来源 | 形制 |
    |------|------|
    | `LLMRouter.TOOL_SCHEMAS` | 扁平：`{"name":..., "description":..., "parameters":...}` |
    | `ToolRegistry.to_llm_schemas()` → `BaseTool.to_llm_schema()` | 已包好：`{"type":"function","function":{...}}` |

    而 `UniversalLLM._tools_once` 会**再包一层** `{"type":"function","function":t}`
    （对扁平的 TOOL_SCHEMAS 是对的）。于是把注册表的 schema 直接喂进去会得到
    `{"function":{"function":{...}}}` —— `function.name` 不存在。

    第一版矩阵探针就这么干了，后果是**六个免费模型的 ③ 全被判 FAIL**，
    而报错原文其实已经点明了真相（nvidia：``missing field `name` ``；
    cohere：``0.function.name: ... received undefined``）。
    这就是"探针没在测它声称测的东西"的第 3 处（P4-B4 自查）。
    """

    out = []
    for s in schemas:
        if not isinstance(s, dict):
            continue
        fn = s.get("function")
        out.append(fn if isinstance(fn, dict) else s)
    return out


def _probe_one(model: str, creds: dict, engine: str, goal: str, schemas: list,
               valid_names: set, dir_aliases: dict) -> dict:
    """探一个模型的三类活，返回结构化结论（不含密钥）"""
    res = {"model": model, "chat": False, "plan": False, "tools": False,
           "chat_ms": None, "plan_ms": None, "reason": "", "steps": None,
           "unknown_actions": [], "picked": None}
    try:
        llm = _make_engine(model, creds, engine)
    except Exception as e:
        res["reason"] = f"构造失败: {type(e).__name__}: {e}"
        return res

    # ① 闲聊
    t0 = time.perf_counter()
    try:
        text = llm.chat("你好呀，用一句话介绍你自己")
    except Exception as e:
        text = None
        res["reason"] = f"chat 异常: {type(e).__name__}"
    res["chat_ms"] = round((time.perf_counter() - t0) * 1000)
    res["chat"] = bool(text and text.strip())
    if text:
        res["chat_sample"] = text.strip()[:40]

    # ② 多步规划（真实规划器 + 真实注册表快照）
    try:
        from agent.providers.planner.llm_planner import LLMPlanner
        planner = LLMPlanner(llm_call=llm.chat)
        ctx = {"available_actions": sorted(valid_names), "tool_schemas": schemas,
               "dir_aliases": dict(dir_aliases)}
        t1 = time.perf_counter()
        plan = planner.plan(goal, ctx)
        res["plan_ms"] = round((time.perf_counter() - t1) * 1000)
        if plan is not None:
            names = plan.action_names()
            res["unknown_actions"] = [a for a in names if a not in valid_names]
            res["steps"] = len(plan.steps)
            res["plan"] = (not res["unknown_actions"]) and len(plan.steps) >= 2
            res["plan_source"] = plan.source
    except Exception as e:
        res["plan_ms"] = None
        res["reason"] = (res["reason"] + " | " if res["reason"] else "") + \
            f"plan 异常: {type(e).__name__}"

    # ③ 工具选择（必须喂**扁平**格式，见 _flatten_schemas 的说明）
    flat = _flatten_schemas(schemas)
    if callable(getattr(llm, "chat_with_tools", None)):
        try:
            picked = llm.chat_with_tools(
                "你是意图路由器，从工具里选一个并填参数",
                "帮我看看电脑还有多少电", flat)
            res["picked"] = (picked or {}).get("name")
            res["tools"] = bool(res["picked"] and
                                res["picked"] in [t["name"] for t in flat])
        except Exception as e:
            res["reason"] = (res["reason"] + " | " if res["reason"] else "") + \
                f"tools 异常: {type(e).__name__}"
    else:
        res["reason"] = (res["reason"] + " | " if res["reason"] else "") + \
            "无 chat_with_tools（路由兜底会静默失效）"

    stats = None
    if callable(getattr(llm, "quota_stats", None)):
        try:
            stats = llm.quota_stats()
        except Exception:
            stats = None
    if stats:
        res["quota_blocked"] = stats.get("quota_blocked")
        res["quota_hits"] = stats.get("quota_hits")
        res["last_error"] = stats.get("last_error")
    return res


def main() -> int:
    ap = argparse.ArgumentParser(description="免费 LLM 端点矩阵探测（P4-B4）")
    ap.add_argument("--list", action="store_true", help="只列 OpenRouter 当前 :free 模型")
    ap.add_argument("--probe", action="store_true", help="探测候选模型")
    ap.add_argument("--models", default="", help="逗号分隔的模型名（不填则按 :free 清单自动选）")
    ap.add_argument("--max-models", type=int, default=6, help="自动选时最多探几个（省配额）")
    ap.add_argument("--engine", choices=["openrouter", "ollama"], default="openrouter")
    ap.add_argument("--goal", default="把下载目录里的安装包都挪到软件归档，然后清理掉下载目录里的空文件夹")
    ap.add_argument("--json-out", default="", help="结论落盘路径（默认写证据目录）")
    args = ap.parse_args()

    if not args.list and not args.probe:
        args.list = args.probe = True

    creds = {}
    if args.engine == "openrouter":
        try:
            creds = _load_fallback_creds()
            print(f"备用端点凭据: api_key=<{len(creds['api_key'])} 字符> "
                  f"base_url={creds['base_url']} fallback_model={creds['fallback_model']}")
        except Exception as e:
            print(f"读 config.yaml 失败: {type(e).__name__}: {e}")
            return 1

    free = []
    if args.engine == "openrouter":
        print("\n" + "=" * 78)
        print("OpenRouter 当前免费模型（公开端点，无鉴权）")
        print("=" * 78)
        try:
            free = _list_free_models()
        except Exception as e:
            print(f"列举失败: {type(e).__name__}: {e}")
            return 1
        print(f"共 {len(free)} 个免费模型")
        for m in free:
            print(f"   {m['id']:<52} ctx={m['ctx']} tools={'Y' if m['tools'] else 'n'}")
        if not args.probe:
            return 0

    # 真实注册表快照（dispose 之前取，见 tools/probe_llm_capability.py 的同类坑）
    from agent.bootstrap import AgentConfig, build_agent_stack
    from core.kernel.events import EventBus
    stack = build_agent_stack(AgentConfig(), EventBus())
    try:
        valid_names = set(stack.registry.names())
        schemas = stack.registry.to_llm_schemas()
        # 与真实规划器一致地把白名单目录别名注入提示词（缺它模型会编造路径）
        dir_aliases = {p.name: str(p) for p in stack.safety.whitelist_roots()}
    finally:
        stack.dispose()
    print(f"\n注册表工具 {len(valid_names)} 个 / schema {len(schemas)} 条（已快照）"
          f" / 目录别名 {len(dir_aliases)} 个")

    if args.models:
        models = [m.strip() for m in args.models.split(",") if m.strip()]
    elif args.engine == "ollama":
        models = ["qwen2.5:1.5b"]
    else:
        models = [m["id"] for m in free][:args.max_models]

    print("\n" + "=" * 78)
    print(f"逐个探测 {len(models)} 个模型 × 3 类活")
    print("=" * 78)
    rows = []
    for i, mid in enumerate(models, 1):
        print(f"\n[{i}/{len(models)}] {mid}")
        r = _probe_one(mid, creds, args.engine, args.goal, schemas, valid_names,
                       dir_aliases)
        rows.append(r)
        print(f"   ①闲聊={'PASS' if r['chat'] else 'FAIL'} {r['chat_ms']}ms  "
              f"②规划={'PASS' if r['plan'] else 'FAIL'} {r['plan_ms']}ms  "
              f"③工具={'PASS' if r['tools'] else 'FAIL'} picked={r['picked']}")
        if r.get("unknown_actions"):
            print(f"   非法工具名: {r['unknown_actions']}")
        if r.get("reason"):
            print(f"   原因: {r['reason']}")
        if r.get("quota_blocked"):
            print(f"   配额被挡: hits={r.get('quota_hits')} last={r.get('last_error')}")
        time.sleep(2)   # 免费档 RPM 很低（20 RPM），留间隔避免自己把自己限流

    print("\n" + "-" * 78)
    print("结论汇总")
    print("-" * 78)
    print(f"{'模型':<46} {'①':<4} {'②':<4} {'③':<4} 分")
    for r in sorted(rows, key=lambda d: -(d["chat"] + d["plan"] + d["tools"])):
        score = r["chat"] + r["plan"] + r["tools"]
        mark = lambda b: "PASS" if b else "FAIL"
        print(f"{r['model']:<46} {mark(r['chat']):<4} {mark(r['plan']):<4} "
              f"{mark(r['tools']):<4} {score}/3")
    best = [r for r in rows if r["chat"] and r["plan"] and r["tools"]]
    print(f"\n三项全通: {[r['model'] for r in best] or '无'}")
    print(f"可当多步规划测试替身（②③通）: "
          f"{[r['model'] for r in rows if r['plan'] and r['tools']] or '无'}")

    out = Path(args.json_out) if args.json_out else (EVIDENCE / "free_llm_matrix.json")
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(
            {"generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
             "engine": args.engine, "free_model_count": len(free),
             "free_models": free, "results": rows},
            ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n结论已落盘: {out}")
    except Exception as e:
        print(f"\n落盘失败: {type(e).__name__}: {e}")

    return 0 if best else 1


if __name__ == "__main__":
    raise SystemExit(main())
