# -*- coding: utf-8 -*-
"""P5-C1 前置核查 2：**备 key 现在还能用吗？**

上一支探针证明主 key（Groq）是活的（429 = 限流，不是 401/403），
并且发生了一次「降级到备用模型」。但探针输出没有告诉我**降级之后到底成了没有**。

为什么这条必须查清：C1 是"轮换两个已泄露的 key"。如果备 key 已经死了，
那"轮换"的紧迫性和范围都不同（主 key 一限流就全线不可用）。
而且 `fallback` 这条路径平时**不会走到**，只有主 key 限流/失败时才会 ——
这正是"平时绿的、出事才发现坏了"的典型。

做法：**不碰 config.yaml**，在内存里构造两个实例各测一次：
  · 真主 key（Groq）—— 已知限流，跳过
  · 假主 key（必然 401）+ 真备 key（OpenRouter）→ **逼出降级路径**，
    看真实结果

绝不打印任何 key；只看 HTTP 状态与返回文本的**结论部分**。
"""
import io
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))

cfg = yaml.safe_load(io.open(ROOT / "config.yaml", encoding="utf-8").read())
prm = (((cfg.get("plugins") or {}).get("llm") or {}).get("params") or {})

from plugins.llm.openrouter.plugin import UniversalLLM             # noqa: E402

print("=" * 76)
print("P5-C1 前置核查 2：备用（OpenRouter）key 还能用吗")
print("=" * 76)
print()
print("做法：把**主 key 换成一个必然无效的值**，逼产品走降级路径，")
print("      看备 key 真实结果。全程不打印任何 key。")
print()

# 主 key 故意给假的 → 一定会失败 → 触发降级
engine = UniversalLLM(
    api_key="INVALID-KEY-FOR-PROBING",
    base_url=prm.get("base_url"),
    model=prm.get("model"),
    fallback_api_key=prm.get("fallback_api_key"),
    fallback_base_url=prm.get("fallback_base_url"),
    fallback_model=prm.get("fallback_model"),
    max_tokens=256,
)
print(f"构造了 {type(engine).__name__}，主 key='INVALID-KEY-FOR-PROBING'（故意无效）")
print()

ok = False
try:
    text = engine.chat("只回复两个字：在吗", context=None)
    print(f"chat() 返回：{text!r}")
    ok = bool(text) and "INVALID" not in str(text)
except Exception as e:                                            # noqa: BLE001
    print(f"chat() 抛异常：{type(e).__name__}: {str(e)[:200]}")

print()
print("=" * 76)
if ok:
    print("结论：**备 key 是活的** —— 主 key 无效时成功降级并拿到了回复。")
    print("      也就是说 fallback 这条平时不走的路，现在经得起一次实测。")
else:
    print("结论：**备 key 没能完成降级** —— 需要看上面那段错误原文。")
    print("      若错误里有 401/403 则备 key 也已失效（C1 的紧迫性上升）；")
    print("      若是 429/网络问题则只说明当下受限，不等于 key 失效。")
print("=" * 76)

# 顺带把"限流"与"失效"分开说清楚：主 key 真实状态
print()
print("参考：主 key（Groq）的真实状态由 tools/probe_llm_capability.py 给出 ——")
print("  它报的是 429（Rate limit reached），**不是** 401/403 ⇒ key 仍有效，只是配额受限。")
print("  这两件事在处置上完全不同：失效要立刻换，限流可以等或换档位。")

# 把降级时的日志行捞出来（产品自己打的），这比我的判断更硬
print()
print("=" * 76)
print("产品自己打的降级日志（若有）")
print("=" * 76)
import logging                                                     # noqa: E402

records = []


class _Cap(logging.Handler):
    def emit(self, record):
        msg = record.getMessage()
        if re.search(r"降级|fallback|备用", msg):
            records.append(re.sub(r"(gsk_|sk-or-v1-)[A-Za-z0-9_\-]+",
                                  r"\1<REDACTED>", msg)[:200])


logging.getLogger().addHandler(_Cap())
engine2 = UniversalLLM(
    api_key="INVALID-KEY-FOR-PROBING-2",
    base_url=prm.get("base_url"),
    model=prm.get("model"),
    fallback_api_key=prm.get("fallback_api_key"),
    fallback_base_url=prm.get("fallback_base_url"),
    fallback_model=prm.get("fallback_model"),
    max_tokens=64,
)
try:
    engine2.chat("说一个字", context=None)
except Exception:                                                 # noqa: BLE001
    pass
logging.getLogger().removeHandler(logging.getLogger().handlers[-1])
for r in records[-6:]:
    print(f"  · {r}")
if not records:
    print("  （没有捕获到降级日志 —— 说明它可能用 print 而不是 logging）")
