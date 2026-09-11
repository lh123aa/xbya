# -*- coding: utf-8 -*-
"""P5-A2 / D17 证据生成：规划期参数类型校验

这个脚本是**证据生成器**，不是测试（判定在 tests/agent/test_planner.py）。
它做三件事，每件都打印可核对的原始输出：

1. 用**真实工具注册表**的 schema，把三种实测出现过的坏参数喂进 `LLMPlanner`，
   打印"哪一步被丢、为什么"；
2. 反方向：合法三步计划一步不少；
3. 把坏参数直接喂给**工具自己**（`validate_params`），证明规划期与执行期的
   说法来自同一条判据（`describe_value_problem`）。

用法：python docs/agent/evidence/p5/_tmp_d17_demo.py
"""
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
for sub in ("agent", "core", "services"):
    sys.path.insert(0, str(ROOT / sub))

logging.basicConfig(level=logging.DEBUG, format="%(levelname)s %(name)s: %(message)s")

from agent.providers.planner.llm_planner import LLMPlanner            # noqa: E402
from agent.tools.base import describe_value_problem                   # noqa: E402

# ── 真实工具 schema（取自真实注册表，不是手写）──
from agent.providers.safety.basic_guard import BasicGuard              # noqa: E402
from agent.tools.file_tools import FileSearchTool, FileMoveTool, FileDeleteTool  # noqa: E402

_GUARD = BasicGuard()
TOOLS = [FileSearchTool(_GUARD), FileMoveTool(_GUARD), FileDeleteTool(_GUARD)]
SCHEMAS = [t.to_llm_schema() for t in TOOLS]
NAMES = [t.name for t in TOOLS]

print("=" * 72)
print("真实工具 schema（取自 to_llm_schema）")
print("=" * 72)
for fn in SCHEMAS:
    body = fn.get("function", fn)
    props = (body.get("parameters") or {}).get("properties") or {}
    types = {k: v.get("type") for k, v in props.items()}
    print(f"  {body['name']}: {types}")

# 逐条列出这几个工具到底声明了哪些字段是 string / array —— 坏参数的判据就来自这里
print()
print("其中 target 相关字段的真实类型：")
for t in TOOLS:
    schema = t.params_schema if hasattr(t, "params_schema") else t.to_llm_schema()["function"]["parameters"]
    for key, spec in (schema.get("properties") or {}).items():
        if spec.get("type") in ("string", "array"):
            print(f"  {t.name}.{key}: {spec.get('type')}")


def make_planner(payload):
    """构造一个只会返回固定 JSON 的规划器"""
    return LLMPlanner(
        llm_call=lambda messages, **kw: json.dumps(payload, ensure_ascii=False),
        available_actions=list(NAMES),
        tool_schemas=SCHEMAS,
    )


# ── 1. 三种实测坏参数 ──
BAD_PLANS = {
    "A. pattern 给了列表（该字段是 string）": [
        {"action": "file_search", "params": {"pattern": "*安装包*"}},
        {"action": "file_search", "params": {"pattern": ["*.exe", "*.msi"]}},
        {"action": "file_move", "params": {"source": "a.txt", "dest": "Documents"}},
    ],
    "B. file_move.source 给了列表（该字段是 string）": [
        {"action": "file_search", "params": {"pattern": "*a*"}},
        {"action": "file_move", "params": {"source": ["a.txt", "b.txt"], "dest": "Documents"}},
    ],
    "C. targets 又套了一层列表（该字段是 array）": [
        {"action": "file_search", "params": {"pattern": "*a*"}},
        {"action": "file_delete", "params": {"targets": [["a.txt", "b.txt"]]}},
    ],
}
for label, steps in BAD_PLANS.items():
    print()
    print("=" * 72)
    print(label)
    print("=" * 72)
    plan = make_planner({"steps": steps}).plan("先找再动", {})
    kept = [s.step_id + ":" + s.action for s in plan.steps] if plan else None
    print(f"  [结果] 计划={'保留 ' + str(kept) if plan else '判为规划失败（有效步骤不足 2 步）'}")

# ── 2. 反方向：合法计划一步不少 ──
print()
print("=" * 72)
print("D. 反方向：类型全对的三步计划")
print("=" * 72)
good = [
    {"action": "file_search", "params": {"pattern": "*a*", "dirs": ["Downloads"]}},
    {"action": "file_move", "params": {"source": "${s1.paths.0}", "dest": "Documents"}},
    {"action": "file_delete", "params": {"targets": ["${s1.paths}"]}},
]
plan = make_planner({"steps": good}).plan("整理", {})
print(f"  [结果] 步骤数={len(plan.steps)}：{[s.step_id + ':' + s.action for s in plan.steps]}")

# ── 3. 与执行期同判据 ──
print()
print("=" * 72)
print("E. 执行期（工具 validate_params）对同样三个坏值怎么说")
print("=" * 72)
cases = [
    (FileSearchTool(_GUARD), {"pattern": ["*.exe"]}),
    (FileMoveTool(_GUARD), {"source": ["a.txt"], "dest": "Documents"}),
    (FileDeleteTool(_GUARD), {"targets": [["a.txt"]]}),
]
for tool, params in cases:
    try:
        tool.validate_params(params)
        print(f"  {tool.name}: 未报错（不该发生）")
    except Exception as exc:                       # noqa: BLE001 —— 就要打印原文
        print(f"  {tool.name}: {exc}")

print()
print("=" * 72)
print("F. 判据本体（describe_value_problem）—— 两处共用同一个函数")
print("=" * 72)
for value, spec in ((["*.exe"], {"type": "string"}),
                    (["a.txt"], {"type": "string"}),
                    ([["a.txt"]], {"type": "array"}),
                    ("Downloads", {"type": "array"}),
                    (None, {"type": "string"}),
                    ("*a*", "string")):
    print(f"  value={value!r} spec={spec!r} -> {describe_value_problem(value, spec)!r}")
print()
print("DONE")
