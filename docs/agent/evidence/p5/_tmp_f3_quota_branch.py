# -*- coding: utf-8 -*-
"""验证 F3 新增配额分支：**只测真正有疑问的那一点**。

我不想把"分支是对的"建立在一个我自己拼出来的 AgentApp 上 ——
那样测出来的绿是假的。所以拆成两半：

【确定性的那一半，本脚本测】`_QuotaWatch` 到底把什么认成"配额"。
  这是新分支唯一的判据来源。若它把 401 也认成配额，那么"key 失效"会被
  悄悄跳过 —— 那比原来的 bug 更坏。所以必须钉住：**只认 429/限流，不认 401**。

【不需要再测的那一半】`if probe is None and _quota_hit_here: skipped(...)`。
  这行是平铺直叙的布尔分支，没有隐蔽路径；而且它的两个输入都有实测支撑：
    · `probe is None` —— P5-C1 那轮真的返回了 None（打印为 `route_with_tools → None`）
    · `_quota_hit_here` —— 同一次运行真的记到了 9 条 429
  更重要的是那段现场数据本身就是**端到端的证据**：
    同样的代码、同样的两条断言失败 + 9 条 429 → 报 16/18（2 FAIL）
    等配额窗口过去、代码**一字未改**原样重跑 → 17/17（1 SKIP，0 FAIL）
  所以那 2 个 FAIL 的成因是配额，这一点不靠我构造任何东西。

本脚本还会做一件自查：**把改动后的判据在各输入下的结果列出来**，
让"什么时候会跳过、什么时候该报失败"一目了然，而不是靠读代码想象。
"""
import importlib.util
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))

spec = importlib.util.spec_from_file_location(
    "f3mod", str(ROOT / "tools" / "verify_f3_real_llm.py"))
mod = importlib.util.module_from_spec(spec)
try:
    spec.loader.exec_module(mod)
except SystemExit:
    pass

QuotaWatch = mod._QuotaWatch

print("=" * 76)
print("F3 配额分支核查")
print("=" * 76)
print()

# ── 一、观察器认什么、不认什么 ──
CASES = [
    ("tools 调用失败: 429 - Rate limit reached", True, "真实限流（P5-C1 那轮的原文）"),
    ("API 错误: 429 - too many requests", True, "限流"),
    ("quota exceeded for this billing period", True, "配额（小写 quota）"),
    ("API 错误: 401 - Invalid API Key", False, "**身份错误：绝不能被当成配额**"),
    ("API 错误: 403 - Forbidden", False, "**权限错误：同上**"),
    ("连接超时", False, "网络问题：单独一类"),
    ("ASR 片段合并后为空", False, "无关日志：不该被收进来"),
]

print("| 日志内容 | 期望认成配额 | 实际 | 结论 |")
print("|---------|-------------|------|------|")
bad = 0
for msg, want, note in CASES:
    qw = QuotaWatch()
    qw.emit(logging.LogRecord(name="t", level=logging.WARNING, pathname=__file__,
                              lineno=1, msg=msg, args=(), exc_info=None))
    got = qw.hit
    ok = got == want
    if not ok:
        bad += 1
    print(f"| `{msg[:44]}` | {want} | {got} | {'✔' if ok else '✘ 不符'} {note} |")
print()
print(f"观察器行为核对：{len(CASES) - bad}/{len(CASES)} 项符合预期")
if bad:
    print("✘ 观察器行为与新分支的假设不符 —— 需要先改观察器")
    raise SystemExit(1)
print("✔ 它只认 429/限流/配额；401/403 不会被误当成配额。")
print("  这一点很关键：若把 401 也认成配额，「key 失效」就会被悄悄跳过 ——")
print("  那比原来的 bug 更坏（原来的 bug 是误报失败，改错方向会变成漏报真问题）。")
print()

# ── 二、把改动后的判据在各输入下的结果列出来（真值表，不靠想象）──
print("=" * 76)
print("改动后的判据真值表：`probe is None and quota_hit_here`")
print("=" * 76)
print()
print("| probe | 期间抓到 429 | → 走哪条 | 结论 |")
print("|-------|-------------|---------|------|")
for p_none, hit in [(False, False), (False, True), (True, False), (True, True)]:
    if p_none and hit:
        branch, concl = "skipped()", "SKIP —— 配额所致，不计失败"
    else:
        branch = "check()"
        concl = ("FAIL —— 真·调不通（要修代码）" if p_none
                 else "PASS —— 正常")
    print(f"| {p_none} | {hit} | {branch} | {concl} |")
print()
print("注意第 3 行：`probe is None` 但**没有** 429 ⇒ 仍然报 FAIL。")
print("这正是我们要的 —— 不能因为「反正可能是配额」就把所有失败都放过。")
print("只有**当场抓到限流证据**时才降级为 SKIP。")

print()
print("=" * 76)
print("结论")
print("=" * 76)
print()
print("1. 观察器只认 429/限流/配额（上表逐条核对过）⇒ 新分支不会放过 401/403。")
print("2. `probe is None` 但无 429 时**仍报 FAIL** ⇒ 没有把真失败洗成跳过。")
print("3. 两个输入都有现场实测支撑（P5-C1 那轮：probe=None + 9 条 429 → 2 FAIL；")
print("   配额恢复后一字未改重跑 → 17/17、0 FAIL、1 SKIP）。")
print()
print("⚠️ 边界：**没有**在真实 429 现场复跑过一次完整 F3（那需要先耗尽配额）。")
print("   本脚本证明的是判据与观察器行为；现场证据来自 P5-C1 那轮的对照，")
print("   两者合起来支持「再遇到 429 时会记 SKIP」，但严格说不是同一次运行内验证的。")
