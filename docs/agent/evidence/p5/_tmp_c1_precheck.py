# -*- coding: utf-8 -*-
"""P5-C1 前置：查清"轮换 key 到底要改哪几处"，以及**两个 key 现在是否还活着**。

为什么要先做这一步：`config.yaml` 里 Groq 的 key 出现在**两个位置**
（`plugins.llm.cloud.api_key` 与 `plugins.llm.params.api_key`），
而 `core/plugin_params.py` 的规则是"cloud 覆盖 params"。
如果只改一处，**另一处的旧 key 仍然会被用**，用户会以为轮换成功了。
这种"改了但没生效"必须先说清楚，否则轮换动作是假的。

本脚本**只读**（不改 config、不发会花钱的请求），并且**绝不打印 key 内容**——
只打印长度与"是否同一个值"。
"""
import io
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))

cfg = yaml.safe_load(io.open(ROOT / "config.yaml", encoding="utf-8").read())
llm = ((cfg.get("plugins") or {}).get("llm") or {})
prm = llm.get("params") or {}
cloud = llm.get("cloud") or {}


def fingerprint(v) -> str:
    """只暴露"长度 + 前缀类别"，够判断是不是同一个，不足以泄露"""
    if not isinstance(v, str) or not v:
        return "（未设置）"
    kind = ("gsk_（Groq）" if v.startswith("gsk_")
            else "sk-or-v1-（OpenRouter）" if v.startswith("sk-or-v1-")
            else "其它前缀")
    return f"{kind}，len={len(v)}"


print("=" * 76)
print("P5-C1 前置核查：轮换 key 要动哪几处（只读，不打印 key 内容）")
print("=" * 76)
print()
print(f"引擎                : {llm.get('engine')}")
print(f"主（primary） base  : {prm.get('base_url')}")
print(f"主（primary） model : {prm.get('model')}")
print(f"备（fallback） base : {prm.get('fallback_base_url')}")
print(f"备（fallback） model: {prm.get('fallback_model')}")
print()

# ── 关键：同一个值出现在几处？只改一处会不会白改？──
sites = [
    ("plugins.llm.cloud.api_key", cloud.get("api_key")),
    ("plugins.llm.params.api_key", prm.get("api_key")),
    ("plugins.llm.params.fallback_api_key", prm.get("fallback_api_key")),
]
print("key 出现的位置：")
for name, val in sites:
    print(f"  {name:38s} = {fingerprint(val)}")
print()

cloud_eq_params = cloud.get("api_key") == prm.get("api_key")
print(f"`cloud.api_key` 与 `params.api_key` 是**同一个值**吗？"
      f"{'是' if cloud_eq_params else '**不是**'}")
print()

# ── 用产品自己的取参函数确认"最终生效的是哪一个" ──
from core.plugin_params import plugin_params                     # noqa: E402
import inspect                                                    # noqa: E402

effective = None
try:
    class _CM:
        def __init__(self, data):
            self._d = data

        def get(self, key, default=None):
            cur = self._d
            for part in str(key).split("."):
                if isinstance(cur, dict) and part in cur:
                    cur = cur[part]
                else:
                    return default
            return cur

    effective = plugin_params(_CM(cfg), "llm", "openrouter")
except Exception as e:                                            # noqa: BLE001
    print(f"（调用 plugin_params 失败，跳过生效值核对：{type(e).__name__}: {e}）")

if isinstance(effective, dict):
    print("**产品实际生效的值**（`core/plugin_params.plugin_params` 的返回）：")
    print(f"  api_key         = {fingerprint(effective.get('api_key'))}")
    print(f"  base_url        = {effective.get('base_url')}")
    print(f"  model           = {effective.get('model')}")
    print(f"  fallback_api_key= {fingerprint(effective.get('fallback_api_key'))}")
    print(f"  fallback_model  = {effective.get('fallback_model')}")
    print()
    same_as_cloud = effective.get("api_key") == cloud.get("api_key")
    print(f"生效的 api_key 取自 cloud 吗？{'**是** —— 只改 params.api_key 不会生效' if same_as_cloud else '否（取自 params）'}")
print()

print("=" * 76)
print("结论（轮换时要动的地方）")
print("=" * 76)
print()
print("1. 主 key（Groq，`gsk_`）：")
print("   · 必须改 `plugins.llm.cloud.api_key`（**它覆盖 params**）")
print("   · 同时也改 `plugins.llm.params.api_key`，否则两处长期不一致，")
print("     下次有人改 `cloud` 段时会突然「变回旧 key」，排查成本极高")
print("2. 备 key（OpenRouter，`sk-or-v1-`）：只有一处")
print("   · `plugins.llm.params.fallback_api_key`")
print("3. `config.yaml` 已被 `.gitignore` 忽略 ⇒ 改它**不会**进 git 历史（G13 会查这个）")
print("4. 改完用**产品自己的探针**验证，而不是自己写请求：")
print("   `python tools/probe_llm_capability.py --engine universal`")
