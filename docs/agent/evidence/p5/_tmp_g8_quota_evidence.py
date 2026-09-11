# -*- coding: utf-8 -*-
"""生成 G8 配额处理缺陷的证据：`docs/agent/evidence/p5/g8_quota_skip.txt`

起因：P5-C1 轮次我为查 key 状态多打了几次真实请求，把免费档 8000 TPM 用完，
随后跑验收电池拿到 **G8 = 16/18 通过、2 失败、1 跳过**。
而那 2 个失败**完全由配额造成** —— 等配额窗口过去、代码一字未改原样重跑 → **17/17**。

这件事暴露了两个问题，本文件把两个都留档：

  1. **产品侧（已修）**：F3 里那两条「工具调用」断言没有查配额观察器，
     429 一律记 FAIL。而规划器那条早就用 `skipped()` 处置过 ——
     同一个病（外部配额伪装成代码问题，即缺陷 18）只治好了一半。
  2. **验收脚本自己的话（更要紧）**：同一次运行的汇总里印着
     「凡受此影响的断言都已归入 [SKIP]，**没有**被算成通过」——
     而那次明明有 2 个 FAIL。**验收脚本在替自己做不成立的声明。**
     这与 P4 那 7 处"检查本身出错"同族。
"""
import io
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
OUT = ROOT / "docs" / "agent" / "evidence" / "p5" / "g8_quota_skip.txt"
HERE = ROOT / "docs" / "agent" / "evidence" / "p5"

L = []
w = L.append


def rule(t):
    w("")
    w("─" * 78)
    w(f" {t}")
    w("─" * 78)
    w("")


w("═" * 78)
w("证据：G8（F3 真实 LLM）的配额处理缺陷 —— 已修 + 反方向核查")
w("═" * 78)
w("")
w(f"生成时间：{datetime.now():%Y-%m-%d %H:%M:%S}")
w("可复跑：`python docs/agent/evidence/p5/_tmp_g8_quota_evidence.py`")
w("")
w("⚠️ 本文件里的失败行含 Groq 的组织 ID（`org_...`），**不含任何 key**。")

rule("★ 现场：同一次改动前后的两组数字")
w("| 运行 | 条件 | 结果 | 说明 |")
w("|------|------|------|------|")
w("| 第 1 次 | 我刚用真实请求把免费档 TPM 打满 | **16/18 通过，2 失败，1 跳过** | 2 条失败都是「工具调用」那两条 |")
w("| 第 2 次 | 配额窗口过去，**代码一字未改** | **17/17 通过，0 失败，1 跳过** | 恢复到改动前的基线 |")
w("")
w("**同一个脚本、同一份代码，只因为配额状态不同就得出相反结论。**")
w("第 1 次那 2 个 FAIL 若被当成产品结论，就会得出「function calling 坏了」——")
w("而它其实只是「模型没被真正问到」。")

rule("一、缺陷 1：工具调用断言不查配额（产品侧，已修）")
w("位置：`tools/verify_f3_real_llm.py` 的 `[F3-a]` 段。")
w("")
w("原写法：")
w("")
w("```python")
w("probe = app.route_with_tools(..., ..., TOOL_SCHEMAS)")
w('check(probe is not None and probe.get("name"), "真实函数调用返回了工具名", ...)')
w('check(probe and probe.get("name") in [...], "选中的工具在 schema 清单内")')
w("```")
w("")
w("`route_with_tools` 在 429 用尽重试后返回 `None` ⇒ 两条断言直接 FAIL。")
w("而同文件的规划器那条早就这么写了：")
w("")
w("```python")
w('skipped(f"LLM 拆出了多步计划...", "3 次全部失败，但抓到 429/限流：... —— 外部配额")')
w("```")
w("")
w("⇒ **同一个病只治了一半**：`_QuotaWatch` 在 P4 缺陷 18 时建好了，")
w("规划器接了，工具调用没接。")
w("")
w("修法：调用前后各记一次 `len(quota.hits)`，")
w("只有「返回 None **且**这次调用期间真的抓到 429」才降级为 SKIP：")
w("")
w("```python")
w("_hits_before = len(quota.hits)")
w("probe = app.route_with_tools(...)")
w("_quota_hit_here = len(quota.hits) > _hits_before")
w("if probe is None and _quota_hit_here:")
w("    skipped(...)   # 外部配额，不是本层结论")
w("else:")
w("    check(...)     # 照旧判 PASS/FAIL")
w("```")

rule("二、缺陷 2：验收脚本替自己做不成立的声明（更要紧）")
w("同一次运行（16/18 那次）的末段印着：")
w("")
w("> 凡受此影响的断言都已归入 [SKIP]，**没有**被算成通过")
w("")
w("而那次运行里明明有 **2 个 FAIL**，且它们正是配额造成的。")
w("也就是说：**这句话当时是假的**。")
w("")
w("为什么把这条排在前面：它不是「某个断言判错了」，而是")
w("**给读者一个与事实相反的整体印象** —— 与 P4 那 7 处「检查本身出错」同族，")
w("也与本项目一路在防的「纸面满足」同族。")
w("")
w("修完缺陷 1 之后，这句话才真正成立（改动后配额恢复的重跑里就是 0 FAIL / 1 SKIP）。")

rule("三、反方向核查：新分支不是死代码，也没把真失败洗成跳过")
w("429 不能按需制造，所以「改完没报错」**不能**当作「分支会走」。")
w("本文件带一支探针，把**真正有疑问的那一点**确定性地测掉：")
w("`_QuotaWatch` 到底把什么认成配额。")
w("")

p = subprocess.run([sys.executable, str(HERE / "_tmp_f3_quota_branch.py")],
                   cwd=str(ROOT), capture_output=True, text=True,
                   encoding="utf-8", errors="replace")
w(f"脚本：`docs/agent/evidence/p5/_tmp_f3_quota_branch.py`　退出码：{p.returncode}")
w("")
w("```")
w((p.stdout or "").rstrip())
w("```")
w("")
w("**真值表里第 3 行是关键**：`probe is None` 但**没有** 429 ⇒ 仍然报 FAIL。")
w("所以这个修法**不会**把所有失败都放过 —— 只有当场抓到限流证据才降级。")

rule("四、我**没有**做什么（避免把没验的说成验过）")
w("- **没有**在真实 429 现场复跑过完整的 F3。那需要先把配额重新耗尽，")
w("  而耗尽配额本身会影响后续所有真实 LLM 关卡 —— 代价大于收益。")
w("  所以：判据与观察器行为由探针确定性证明；")
w("  现场证据来自上面那组「同代码、不同配额、相反结论」的对照。")
w("  两者合起来支持「再遇到 429 会记 SKIP」，但**严格说不是同一次运行内验证的**。")
w("- **没有**动产品的重试/降级逻辑：改的只是**验收脚本怎么归类外部配额**。")
w("  产品行为一行未改。")

rule("五、边界声明")
w("- 这条缺陷只在**免费档配额用尽**时才显形，平时跑不出来 ——")
w("  所以它是「平时绿的、出事误导人」的类型")
w("- 若换成付费档或换模型，可能长期看不到；但一旦看到，")
w("  读到的会是一个**错误的产品结论**")
w("- `_QuotaWatch` 靠**日志文本**匹配（429/rate limit/quota/too many requests），")
w("  不是结构化错误码。厂商改文案就会让它失明 —— 这一点**未修**，")
w("  属已知局限（结构性修法是让插件把状态码作为字段往上抛，改动面更大）")

w("")
w("═" * 78)
w(" 一句话")
w("═" * 78)
w("")
w("**验收脚本把「外部配额用尽」报成了「产品的 function calling 失败」。**")
w("已修：工具调用那两条断言接上早就存在的配额观察器（规划器那条早就接了）；")
w("顺带让那句「凡受此影响的断言都已归入 SKIP」重新成立 ——")
w("它在修之前是一句**不成立的自述**。")

OUT.write_text("\n".join(L) + "\n", encoding="utf-8")
print(f"写入 {OUT}（{OUT.stat().st_size} bytes）")
