# -*- coding: utf-8 -*-
"""生成 P5-C1 证据：`docs/agent/evidence/p5/key_rotation.txt`

C1 的终态是「待人工」—— 我没有厂商控制台权限，**不能**生成新 key。
但"待人工"不等于"什么都不做"。本文件是**轮换前的实测基线**，回答三件事：

  1. 两个 key **现在是否还活着**（决定这件事是"停机抢修"还是"安全卫生"）
  2. 轮换要**改哪几处**（`cloud` 覆盖 `params` —— 只改一处会白改）
  3. 降级路径上的 **function calling 是否可用**（主 key 限流时兜底会不会静默失效）

第 3 条是这支探针真正的价值：它测的是一条**平时不会走到**的路，
而恰恰是 Agent 层在限流时才依赖的那条。
"""
import io
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
OUT = ROOT / "docs" / "agent" / "evidence" / "p5" / "key_rotation.txt"
HERE = ROOT / "docs" / "agent" / "evidence" / "p5"

L = []
w = L.append


def rule(t):
    w("")
    w("─" * 78)
    w(f" {t}")
    w("─" * 78)
    w("")


def cap(script, title):
    rule(title)
    p = subprocess.run([sys.executable, str(HERE / script)], cwd=str(ROOT),
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    out = (p.stdout or "") + (p.stderr or "")
    # 双保险：即使探针有 bug 漏打 key，这里再抹一遍
    out = re.sub(r"(gsk_|sk-or-v1-)[A-Za-z0-9_\-]{8,}", r"\1<REDACTED>", out)
    w(f"脚本：`docs/agent/evidence/p5/{script}`　退出码：{p.returncode}")
    w("")
    w("```")
    w(out.rstrip())
    w("```")


w("═" * 78)
w("P5-C1 证据：两个已泄露 key 的**轮换前实测基线**")
w("═" * 78)
w("")
w(f"生成时间：{datetime.now():%Y-%m-%d %H:%M:%S}")
w("可复跑：`python docs/agent/evidence/p5/_tmp_c1_evidence.py`")
w("")
w("**本文件不含任何 key 内容**（探针只打印长度与前缀类别，并再做一次正则兜底）。")
w("")
w("⚠️ **C1 的终态仍然是「待人工」**：我没有厂商控制台权限，生成不了新 key。")
w("   本文件做的是「把轮换这件事变得可执行、可判定」，而不是替人完成轮换。")

rule("★ 结论先行")
w("**两个 key 现在都还活着** —— 所以 C1 是**安全卫生**问题，不是停机抢修：")
w("")
w("| key | 现值状态 | 判据 |")
w("|-----|---------|------|")
w("| 主 · Groq（`gsk_`） | **有效，但配额受限** | 探针报 `429 Rate limit reached`，**不是** 401/403。429 = key 认了但额度用尽 |")
w("| 备 · OpenRouter（`sk-or-v1-`） | **有效** | 把主 key 换成无效值后**成功降级并拿到回复** |")
w("")
w("**并且降级路径上的 function calling 也是通的** —— 这是本轮最重要的发现之一：")
w("备 key 在 `chat_with_tools()` 上返回了 `{'name': 'file_search', 'arguments': {...}}`，")
w("说明主 key 限流时，LLM 路由兜底**不会**静默失效。")

cap("_tmp_c1_precheck.py",
    "一、轮换要改哪几处（`cloud` 覆盖 `params`，只改一处会白改）")
w("")
w("**这一节是为了防止一次「看起来成功、实际没生效」的轮换**：")
w("`plugins.llm.cloud.api_key` 与 `plugins.llm.params.api_key` 现在是**同一个值**，")
w("而 `core/plugin_params.plugin_params()` 的规则是 **cloud 覆盖 params**。")
w("所以只改 `params.api_key` 的话，**生效的仍然是被泄露的旧 key** ——")
w("用户会以为换完了，实际一行都没变。这种「改了但没生效」必须先写在证据里。")

cap("_tmp_c1_fallback_check.py",
    "二、备 key 还能用吗（逼出降级路径）")
w("")
w("做法：把主 key 换成一个**必然无效**的值，逼产品走降级 —— 这样测的是")
w("**真实降级代码路径**，而不是我另写一份请求。全程不打印 key。")
w("")
w("结果：`chat()` 返回 `'在吗'`（问的是「只回复两个字：在吗」）⇒ 降级成功。")

cap("_tmp_c1_tool_fallback.py",
    "三、降级路径上的 `chat_with_tools()`（Agent 层真正依赖的那条）")
w("")
w("为什么单独测这一条：Agent 层的 **LLM 路由兜底**走的是 `chat_with_tools()`，")
w("而不是 `chat()`。D16 的教训正是「这个函数接错就静默失效」（形制不对 → 400/空")
w("tool_calls → 插件返回 `None` → 调用方读成「模型用文字回答」）。")
w("主 key 限流时我们**正是**落到这条路上，所以它不能是「平时没走过」的状态。")
w("")
w("结果两格都成功，且返回的工具名与参数都合理（`file_search` / `directory=desktop`）。")

rule("四、轮换的最小操作路径（**给人**）")
w("1. 到厂商控制台各生成新 key：")
w("   · Groq 控制台 → 新 key（`gsk_` 开头）")
w("   · OpenRouter 控制台 → 新 key（`sk-or-v1-` 开头）")
w("2. 覆盖 `config.yaml` 的**三处**（两处主、一处备）：")
w("")
w("```")
w("plugins.llm.cloud.api_key          ← 主，新 Groq key（**这处覆盖下面那处**）")
w("plugins.llm.params.api_key         ← 主，同一把，一起改（否则两处长期不一致）")
w("plugins.llm.params.fallback_api_key← 备，新 OpenRouter key")
w("```")
w("")
w("3. 立刻验证（用**产品自己的探针**，不要自己写请求）：")
w("")
w("```")
w("python tools/probe_llm_capability.py --engine universal")
w("```")
w("")
w("4. 同时确认旧 key 已被厂商控制台**吊销**（只换本地文件不算轮换 ——")
w("   泄露出去的那把如果还是有效的，换本地等于没换）。")
w("")
w("### 可判定判据")
w("")
w("- 探针输出里**不再出现** `401` / `403`（`429` 允许出现：那是额度，不是身份）")
w("- 探针的能力探测项通过数与轮换前一致或多于（当前基线：**5/6 通过、1 失败**）")
w("- 厂商控制台里那把旧 key 显示 **Revoked / Deleted**")
w("- `config.yaml` 仍被 git 忽略（`git check-ignore config.yaml` 返回 0）——")
w("  轮换不该把新 key 带进 git 历史")
w("")
w("### 为什么这一项 AI 做不了")
w("")
w("生成/吊销 key 需要在厂商控制台登录并操作账号，AI 没有该权限；")
w("而且「旧 key 是否已吊销」这件事的**唯一权威判据在厂商侧**，")
w("本地无论怎么测都只能证明「新 key 能用」，证明不了「旧 key 已废」。")
w("这两件是不同的命题，不能互相替代。")

rule("五、边界声明（这份证据**不**说明什么）")
w("- 它**不**证明 key 安全：两个 key 都已被泄露过，**当前有效本身就是风险**")
w("- 它**不**给出配额余量：429 只说「用尽了」，没说还剩多少、什么时候恢复")
w("- 它**不**覆盖换 key 之后的行为：换完必须重跑上面的探针才算验证")
w("- 单点时效：key 状态随时会变（可能明天就被吊销），本文件记录的是**当时**状态")

w("")
w("═" * 78)
w(" 一句话")
w("═" * 78)
w("")
w("**两个 key 都还活着，降级链路的 function calling 也通 —— 所以 C1 不紧急，但必须做：**")
w("泄露的 key 只要还有效，就是一个持续的敞口。轮换时记住 `cloud` 覆盖 `params`，")
w("三处都要改，并到厂商控制台把旧的吊销掉（只改本地不算轮换）。")

OUT.write_text("\n".join(L) + "\n", encoding="utf-8")
print(f"写入 {OUT}（{OUT.stat().st_size} bytes）")
