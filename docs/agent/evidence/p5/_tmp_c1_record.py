# -*- coding: utf-8 -*-
"""把 P5-C1 **这次实际做的事**追加进 `key_rotation.txt`，并更新账本。

为什么关键信息是"哪几把被吊销"：换本地文件这件事**不是轮换**。
泄露出去的那把只要还有效，敞口就还在。而"旧 key 是否已废"的**唯一权威判据
在厂商控制台**，本地无论怎么测都证明不了。

所以本脚本的写法是：**能由本机证明的写死（换了、能认证），
只有控制台能证明的如实标"未见凭据"** —— 不替用户宣布已完成吊销。
"""
import io
import json
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
EVID = ROOT / "docs" / "agent" / "evidence" / "p5" / "key_rotation.txt"
LEDGER = ROOT / "docs" / "agent" / "tasks-p5.json"

# ── 1. 追加记录到证据文件 ──
append = f"""

{"=" * 78}
【追加】轮换执行记录（{datetime.now():%Y-%m-%d %H:%M:%S}）
{"=" * 78}

## 一、主 key（Groq）已换

| 项 | 结果 | 谁证明的 |
|----|------|---------|
| 写入 `config.yaml` 第 149 行（`cloud.api_key`） | ✅ | 回读校验 |
| 写入 `config.yaml` 第 154 行（`params.api_key`） | ✅ | 回读校验 |
| 两处值一致 | ✅ | 回读校验 |
| **产品实际生效值** = 新值 | ✅ | 调 `core/plugin_params.plugin_params()` 的返回值，不是读文件 |
| 新 key 认证通过 | ✅ | `tools/probe_llm_capability.py --engine universal` 输出里 **0 次** 401/403 |
| 其余键逐字未变 | ✅ | 逐行比对（`base_url` / `model` / `fallback_*` / `max_tokens` / `provider` 共 8 行） |

> 为什么"两处一致"要单独验：`cloud.api_key` **覆盖** `params.api_key`。
> 只改一处的话，回读单看被改的那处是"对的"，而生效的仍是旧值 ——
> 所以必须去问**产品的取参函数**，不能只看文件。

### 过程中我自己出的一处矛盾（留档，不掩盖）

我先用一支探针报「要改 149 / 154 / 156 行」，随后另一支脚本反过来报
「cloud 在第 154、params 在第 149」—— **对同一件事给出相反标注**。
处置：另写一支脚本把 140~165 行原文打出来（值遮蔽），按 YAML 缩进确定归属，
结论是 **149 = cloud 段、154 = params 段**（第一支探针是对的）。
教训：**两支脚本说法矛盾时，不要挑一个信，去把原始结构打出来看。**

## 二、备 key（OpenRouter）本次未换

`config.yaml` 第 156 行 `fallback_api_key` 保持原值。
它当前**仍然有效**（实测可成功接管降级），因此**它也仍是一个敞口**。

## 三、⚠️ 本机**证明不了**的一件事：旧 key 是否已吊销

| 待确认 | 唯一权威判据 | 本机能否验证 |
|--------|-------------|-------------|
| 旧 Groq key 已 Revoke | 厂商控制台该 key 显示 Revoked/Deleted | ❌ 不能 |
| 旧 OpenRouter key 已 Revoke | 同上 | ❌ 不能 |

本机把旧 key 从 `config.yaml` 里删掉，**只说明"我们不用它了"**，
**不说明"别人不能用它了"**。这两件事经常被混为一谈，而它们完全不同。

所以本文件**不写"轮换完成"**，只写"本地已切到新 key 且新 key 可用；
旧 key 的吊销状态待用户在控制台确认"。

## 四、当前额度状态（与 key 无关，别误判）

探针输出全是 `429 … tokens per day (TPD): Limit reached`，
组织 ID 与换 key 之前**完全相同**（`org_01khx8sbageatsv0gjp3xbfzpe`）。

⇒ 两点结论：
  1. **额度和 key 是两码事**：429 是按**组织**算的日额度，换 key 不会重置。
     换 key 解决的是"泄露"，不是"限流"。
  2. 因此"能力探测有没有全绿"**不能**用来判断 key 换得好不好 ——
     判据是 **0 次 401/403**（身份），而不是 429（额度）。
     把这两个混起来会把"额度用尽"误读成"key 配错了"。
"""

io.open(EVID, "a", encoding="utf-8", newline="\n").write(append)
print(f"已追加到 {EVID.relative_to(ROOT)}（现 {EVID.stat().st_size} bytes）")

# ── 2. 更新账本 C1 ──
data = json.loads(io.open(LEDGER, encoding="utf-8").read())
c1 = next(t for t in data["tasks"] if t["id"] == "P5-C1")
c1["conclusion"] = (
    "**主 key 已轮换**（2026-09-11）：新 Groq key 写入 config.yaml 第 149（cloud）"
    "与第 154（params）两行，两处一致，且经 `core/plugin_params.plugin_params()` "
    "确认**产品生效值**即是新值；探针输出 0 次 401/403 ⇒ 认证通过。"
    "其余键逐字未变（8 行比对）。"
    "**备 key（OpenRouter，第 156 行）本次未换，仍然有效 ⇒ 仍是敞口。**"
    "**旧 key 是否已吊销，本机无法证明** —— 那需要在厂商控制台看 "
    "Revoked/Deleted；本机把它从配置里删掉只等于「我们不用它了」，"
    "不等于「别人不能用它了」。所以这一项**保持「待人工」**，"
    "不写成完成。另注：当前探针的失败项全是 429 日额度（TPD，按组织计），"
    "与 key 无关 —— 换 key 解决泄露，不解决限流。"
)
data["tasks"] = [c1 if t["id"] == "P5-C1" else t for t in data["tasks"]]
io.open(LEDGER, "w", encoding="utf-8", newline="\n").write(
    json.dumps(data, ensure_ascii=False, indent=2) + "\n")
print(f"账本已更新 P5-C1 的 conclusion（状态保持 {c1['state']}）")
print("  ⚠️ 状态**没有**改成完成：管理员侧吊销未确认前，它仍是「待人工」")
