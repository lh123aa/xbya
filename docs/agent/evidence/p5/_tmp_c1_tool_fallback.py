# -*- coding: utf-8 -*-
"""P5-C1 前置核查 3：**降级路径上的 function calling 能不能用？**

前一支探针证明了备 key 是活的 —— 但那测的是 `chat()`（纯文本）。
Agent 层真正依赖的是 `chat_with_tools()`：**LLM 路由兜底**走的就是它。

这条必须单独测，理由是 D16 教过的：`chat_with_tools` 是"接错就静默失效"的地方 ——
形制不对时端点返回 400/空 tool_calls，插件返回 `None`，
而调用方把 `None` 读成「模型这次用文字回答」，于是**整条兜底悄悄失效**。
主 key 限流时我们**正是**落到这条路上，所以它不能是"平时没走过"的状态。

测法：
  1. 用真主 key 测 `chat_with_tools`（可能 429，如实记录）
  2. 用假主 key + 真备 key 测 `chat_with_tools` → **逼出降级 + 工具调用**
  3. 断言返回的 tool_calls 里**工具名真的是那个名字**（D16 的判据），
     而不是 None、不是空、不是嵌套错位

绝不打印 key。
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


def redact(s) -> str:
    return re.sub(r"(gsk_|sk-or-v1-)[A-Za-z0-9_\-]+", r"\1<REDACTED>", str(s))


# 扁平形制（LLMRouter.TOOL_SCHEMAS 的形态）
TOOLS_FLAT = [{
    "name": "file_search",
    "description": "在指定目录里按关键词搜索文件",
    "parameters": {
        "type": "object",
        "properties": {"keyword": {"type": "string", "description": "搜索关键词"},
                       "directory": {"type": "string", "description": "目录别名"}},
        "required": ["keyword"],
    },
}]

PROMPT = "用户说：帮我找一下桌面上的 PDF 文件。请调用工具。"


def probe(label: str, **kw) -> dict:
    print(f"### {label}")
    engine = UniversalLLM(**kw)
    try:
        calls = engine.chat_with_tools(
            system="你是欣雅，一个住在用户桌面上的助手。",
            user=PROMPT,
            tools=TOOLS_FLAT,
        )
    except Exception as e:                                        # noqa: BLE001
        print(f"  抛异常：{type(e).__name__}: {redact(e)[:180]}")
        print()
        return {"ok": False, "why": f"异常 {type(e).__name__}"}

    if calls is None:
        print("  返回 **None** —— 按 D16 的教训，这是「静默失效」的形态：")
        print("    调用方会把 None 读成「模型这次用文字回答」，于是兜底悄悄不生效")
        print()
        return {"ok": False, "why": "返回 None"}

    # 契约（plugin.py 的 Returns 段）：{"name": 工具名, "arguments": {...}}
    # —— 是**单个 dict**，不是列表。第一版探针把它当列表遍历，于是遍历到了
    # dict 的**键**（字符串），报 `'str' object has no attribute 'get'`。
    # 那不是产品缺陷，是我读错了契约。这里按契约断言。
    if not isinstance(calls, dict):
        print(f"  返回类型不是 dict 而是 {type(calls).__name__} —— 契约不符")
        print()
        return {"ok": False, "why": f"返回类型 {type(calls).__name__}"}

    name = calls.get("name")
    args = calls.get("arguments")
    print(f"  返回 dict，键={sorted(calls)}")
    print(f"    name={name!r}")
    print(f"    arguments={redact(args)[:160]!r}")
    ok = bool(name)
    if not ok:
        print("  ⚠️ 拿到了 dict 但**没有工具名** —— 这正是 D16 说的静默失效形态")
    print()
    return {"ok": ok, "why": "" if ok else "dict 里没有 name"}


print("=" * 78)
print("P5-C1 前置核查 3：降级路径上的 chat_with_tools()")
print("=" * 78)
print()

r1 = probe("主 key（Groq，真实值）—— 可能 429",
           api_key=prm.get("api_key"),
           base_url=prm.get("base_url"),
           model=prm.get("model"),
           fallback_api_key=prm.get("fallback_api_key"),
           fallback_base_url=prm.get("fallback_base_url"),
           fallback_model=prm.get("fallback_model"),
           max_tokens=512)

r2 = probe("假主 key + 真备 key —— 逼出降级",
           api_key="INVALID-KEY-FOR-TOOL-PROBE",
           base_url=prm.get("base_url"),
           model=prm.get("model"),
           fallback_api_key=prm.get("fallback_api_key"),
           fallback_base_url=prm.get("fallback_base_url"),
           fallback_model=prm.get("fallback_model"),
           max_tokens=512)

print("=" * 78)
print("结论")
print("=" * 78)
print()
print(f"- 主 key（Groq）工具调用：{'✅ 成功' if r1['ok'] else '❌ 失败（' + r1['why'] + '）'}")
print(f"- 降级（OpenRouter 备 key）工具调用：{'✅ 成功' if r2['ok'] else '❌ 失败（' + r2['why'] + '）'}")
print()
print("怎么读这两个结果：")
print("  · 若降级那格**成功** ⇒ 主 key 限流时兜底仍然能调度工具，链路上没有静默空洞")
print("  · 若降级那格**失败** ⇒ 这是 D16 同族的缺陷（换了端点就丢 function calling），")
print("    必须登记成新的债务项，而不是当成「配额问题」放过去")
