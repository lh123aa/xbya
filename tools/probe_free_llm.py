"""免费 LLM 端点可用性探测（P4-B4）

## 为什么要写它

`docs/free-llm-api-providers.md` 里记了 12 家免费服务，但那份文档是**某一天写的快照** ——
免费端点的存活周期经常只有几周。所以：
"文档里写着能用" ≠ "现在能用"。判断依据只能是**真的发一次请求**。

本脚本按"是否需要 key""是否匿名可用"分组探测，并对可用的端点报告延迟与返回内容摘要。
**不打印任何密钥**；只打印"有/没有环境变量"。

## 与密钥卫生的关系（P4-B4 的取向变化）

原先 P4-B4 的计划是"让用户轮换两个泄露的 key"。但更彻底的做法是：
**把日常测试路径切到不需要 key 的端点上** —— 没有 key 就没有可泄露的 key。
泄露的那两个 key 仍需在控制台撤销（那是用户的事），但"必须先换 key 才能继续测试"
这个阻塞就消失了。

用法：
    python tools/probe_free_llm.py                 # 探测全部（无 key 的优先）
    python tools/probe_free_llm.py --model X       # 只测指定模型
    python tools/probe_free_llm.py --timeout 30
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

#: 匿名可用（不需要任何 key）的端点。**这是最有价值的一类**：
#: 零配置、零密钥、没有"配额打满伪装成模型不行"的问题。
ANONYMOUS = [
    {
        "name": "OVHcloud (匿名)",
        "base_url": "https://oai.endpoints.kepler.ai.cloud.ovh.net/v1",
        "api_key": "dummy",           # OVHcloud 不校验
        "models": ["Qwen3.5-397B-A17B", "gpt-oss-120b",
                   "Meta-Llama-3_3-70B-Instruct", "Qwen3.6-27B"],
        "note": "2 RPM / IP / 模型",
    },
]

#: 需要 key 的端点：只探测"环境变量在不在"，不打印值。
KEYED = [
    ("Google Gemini", "GOOGLE_API_KEY",
     "https://generativelanguage.googleapis.com/v1beta/openai", "gemini-2.0-flash"),
    ("Groq", "GROQ_API_KEY", "https://api.groq.com/openai/v1",
     "openai/gpt-oss-120b"),
    ("OpenRouter", "OPENROUTER_API_KEY", "https://openrouter.ai/api/v1",
     "nvidia/nemotron-3-super-120b-a12b:free"),
    ("SiliconFlow", "SILICONFLOW_API_KEY", "https://api.siliconflow.cn/v1",
     "Qwen/Qwen2.5-7B-Instruct"),
    ("Mistral", "MISTRAL_API_KEY", "https://api.mistral.ai/v1",
     "mistral-small-latest"),
]

PROMPT = "只回答两个字：收到"


def _call(base_url: str, api_key: str, model: str, timeout: float) -> dict:
    """发一次最小的 chat/completions 请求，返回结构化结果（不抛异常）"""
    import requests

    url = base_url.rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": PROMPT}],
        "max_tokens": 16,
        "temperature": 0.2,
    }
    headers = {"Content-Type": "application/json",
               "Authorization": f"Bearer {api_key}"}
    t0 = time.perf_counter()
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=timeout)
    except Exception as e:
        return {"ok": False, "ms": (time.perf_counter() - t0) * 1000,
                "why": f"{type(e).__name__}: {e}"[:160]}
    ms = (time.perf_counter() - t0) * 1000

    if r.status_code != 200:
        # 429 要单独标出来：它是"外部配额"，不是"我们代码坏了"（缺陷 18 的教训）
        kind = "配额/限流" if r.status_code == 429 else f"HTTP {r.status_code}"
        return {"ok": False, "ms": ms, "why": f"{kind}: {r.text[:140]}",
                "quota": r.status_code == 429}

    try:
        data = r.json()
        text = (data["choices"][0]["message"].get("content") or "").strip()
    except Exception as e:
        return {"ok": False, "ms": ms, "why": f"响应结构不可解析: {e}"[:120]}
    return {"ok": True, "ms": ms, "text": text[:60],
            "usage": data.get("usage", {})}


def main() -> int:
    ap = argparse.ArgumentParser(description="免费 LLM 端点可用性探测（P4-B4）")
    ap.add_argument("--model", default=None, help="只测指定模型")
    ap.add_argument("--timeout", type=float, default=25.0)
    ap.add_argument("--json", action="store_true", help="输出 JSON（供脚本消费）")
    args = ap.parse_args()

    report: list = []

    print("=" * 74)
    print("免费 LLM 端点可用性探测（P4-B4）—— 判断依据是真的发一次请求")
    print(f"提示词：{PROMPT!r}   超时：{args.timeout:.0f}s")
    print("=" * 74)

    print("\n【一类】匿名可用（不需要任何 key）")
    for prov in ANONYMOUS:
        print(f"\n  {prov['name']}  {prov['base_url']}   （{prov['note']}）")
        for model in prov["models"]:
            if args.model and model != args.model:
                continue
            res = _call(prov["base_url"], prov["api_key"], model, args.timeout)
            mark = "可用  " if res["ok"] else "不可用"
            detail = (f"{res['ms']:.0f}ms  回复={res.get('text')!r}"
                      if res["ok"] else res["why"])
            print(f"    [{mark}] {model:<32} {detail}")
            report.append({"provider": prov["name"], "model": model, **res})

    print("\n【二类】需要 key 的端点：只看环境变量在不在（不打印值）")
    for name, env, base, model in KEYED:
        present = bool(os.environ.get(env))
        print(f"    {name:<16} {env:<24} {'已设置 ✅' if present else '未设置'}")
        report.append({"provider": name, "model": model, "env": env,
                       "env_present": present, "probed": False})

    ok = [r for r in report if r.get("ok")]
    quota = [r for r in report if r.get("quota")]
    print("\n" + "-" * 74)
    print(f"汇总：可用 {len(ok)} 个模型；命中配额/限流 {len(quota)} 个")
    for r in ok:
        print(f"  ✅ {r['provider']} / {r['model']}  {r['ms']:.0f}ms")
    if quota:
        print("  ⚠️ 以下端点是**配额问题**（外部依赖，不是代码问题）：")
        for r in quota:
            print(f"     {r['provider']} / {r['model']}")
    print("=" * 74)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
