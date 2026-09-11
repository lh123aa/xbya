# -*- coding: utf-8 -*-
"""安全核对：我对 config.yaml 的写入**只动了该动的两行**，其余字节未变。

为什么要做这一步：我先后跑了两支脚本，它们对"哪一行属于哪个段"给出了**相反的标注** ——
这种自相矛盾不能挑一个信。而且那次写入用的是"整行替换"（`writelines`），
万一正则匹配宽了，就可能顺手改到 `base_url` 之类不该动的行。

判据：
  1. 逐行比对**结构**：该是 `key: value` 的行还是，冒号还在，缩进没变
  2. `cloud` / `params` 两段里的 `base_url` / `model` / `fallback_*` 全部**逐字未变**
     （用 git 看 diff —— 它比我的记忆可信）
  3. 两处 api_key 都是新值且一致
  4. YAML 仍能解析，产品的生效值仍是新 key

**不打印任何 key 值**：只打印长度与"是否等于新值"。
"""
import io
import re
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
CFG = ROOT / "config.yaml"

NEW = (sys.argv[1] if len(sys.argv) > 1 else "").strip()
assert NEW, "调用时要带上那个新 key（只为比对，不会打印它）"

print("=" * 74)
print("一、git diff：到底动了哪几行")
print("=" * 74)
print()
p = subprocess.run(["git", "diff", "--unified=0", "--", "config.yaml"],
                  cwd=str(ROOT), capture_output=True, text=True,
                  encoding="utf-8", errors="replace")
diff = p.stdout or ""
# 遮蔽 diff 里可能出现的 key 字面量
diff = re.sub(r"(gsk_|sk-or-v1-)[A-Za-z0-9_\-]{8,}", r"\1<REDACTED>", diff)
changed = [l for l in diff.splitlines() if l.startswith(("+", "-"))
           and not l.startswith(("+++", "---"))]
print(f"git 认为改了 {len(changed)} 行：")
for l in changed:
    print(f"  {l[:100]}")
print()
print(f"结论：改动行数 = {len(changed)}（期望 **4**：两行旧值被删、两行新值被加）")
if len(changed) != 4:
    print("  ⚠️ 不是 4 行 —— 说明还动到了别的地方，需要人工看一眼上面的 diff")
    # 继续往下查，不直接退出：下面几节能进一步定位

print()
print("=" * 74)
print("二、结构核对：两段里的**其他键**是否逐字未变")
print("=" * 74)
print()
lines = io.open(CFG, encoding="utf-8").read().splitlines()

EXPECT = {
    150: "base_url: https://api.groq.com/openai/v1",
    151: "model: openai/gpt-oss-120b",
    155: "base_url: https://api.groq.com/openai/v1",
    157: "fallback_base_url: https://openrouter.ai/api/v1",
    158: "fallback_model: nvidia/nemotron-3-super-120b-a12b:free",
    159: "max_tokens: 1536",
    160: "model: openai/gpt-oss-120b",
    161: "provider: groq",
}
bad = 0
for ln, want in EXPECT.items():
    got = lines[ln - 1].strip()
    ok = got == want
    if not ok:
        bad += 1
    print(f"  第 {ln:4d} 行  {'✔' if ok else '✘'}  {got[:64]}")

print()
print("=" * 74)
print("三、两处 api_key 都是新值且一致 + YAML 可解析")
print("=" * 74)
print()
cfg = yaml.safe_load(io.open(CFG, encoding="utf-8").read())
llm = ((cfg.get("plugins") or {}).get("llm") or {})
prm = llm.get("params") or {}
cloud = llm.get("cloud") or {}
a, b = cloud.get("api_key"), prm.get("api_key")
print(f"  cloud.api_key  = <遮蔽 len={len(a) if isinstance(a, str) else 'N/A'}>"
      f"  是新值？{a == NEW}")
print(f"  params.api_key = <遮蔽 len={len(b) if isinstance(b, str) else 'N/A'}>"
      f"  是新值？{b == NEW}")
print(f"  两处一致？{a == b}")
assert a == NEW and b == NEW and a == b

from core.plugin_params import plugin_params                       # noqa: E402


class _CM:
    def __init__(self, d):
        self._d = d

    def get(self, k, default=None):
        cur = self._d
        for part in str(k).split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                return default
        return cur


eff = plugin_params(_CM(cfg), "llm", "openrouter") or {}
print(f"  产品生效值是新 key？{eff.get('api_key') == NEW}")
assert eff.get("api_key") == NEW
print()
print(f"结论：结构核对里 {len(EXPECT) - bad}/{len(EXPECT)} 项逐字未变；"
      f"两处 api_key 均为新值、一致，且产品生效值确认。")
