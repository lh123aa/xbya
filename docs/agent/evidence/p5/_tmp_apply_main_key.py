# -*- coding: utf-8 -*-
"""把用户给的新 Groq key 写进 config.yaml 的两处（第 149 / 154 行）。

为什么要写成脚本而不是让用户手改：
  1. 主 key 在**两个位置**（cloud 覆盖 params）。手改容易只改一处 ——
     那样"看起来换了"，生效的还是旧 key。脚本两处一起改并**断言两处一致**。
  2. 脚本改完立刻回读校验：① 两处值相同 ② 前缀是 gsk_ ③ 长度合理
     ④ **产品实际生效的值**（`plugin_params()` 的返回）就是新值。
     第 ④ 条是关键——前三条都过了但没生效是可能的（就是 cloud/params 那个坑）。

**本脚本不打印任何 key 内容**：只打印长度、前缀类别，以及"是否与预期一致"。
key 从命令行参数传入（由调用方提供），不写进本文件。
"""
import io
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
CFG = ROOT / "config.yaml"

new_key = (sys.argv[1] if len(sys.argv) > 1 else "").strip()
if not new_key:
    print("FAIL 没拿到新 key")
    raise SystemExit(1)

if not new_key.startswith("gsk_"):
    print(f"FAIL 前缀不对：期望 gsk_ 开头，实际 {new_key[:8]}…")
    raise SystemExit(1)
if len(new_key) < 40:
    print(f"FAIL 长度可疑：{len(new_key)}")
    raise SystemExit(1)

lines = io.open(CFG, encoding="utf-8").read().splitlines(keepends=True)
print(f"config.yaml 共 {len(lines)} 行")

# ── 定位两处 api_key：按"往上找最近的 cloud:/params: 段"判断归属 ──
targets = {}
for i, l in enumerate(lines):
    s = l.strip()
    if s.startswith("#") or ":" not in s:
        continue
    if s.split(":", 1)[0].strip() != "api_key":
        continue
    for j in range(i - 2, max(-1, i - 8), -1):
        m = re.match(r"\s*(cloud|params):\s*$", lines[j])
        if m:
            targets[m.group(1)] = i
            break

if set(targets) != {"cloud", "params"}:
    print(f"FAIL 没能同时定位 cloud/params 两处 api_key，找到的是 {sorted(targets)}")
    raise SystemExit(1)

print(f"定位到：cloud 段第 {targets['cloud'] + 1} 行、params 段第 {targets['params'] + 1} 行")

old_lens = {}
for which, idx in targets.items():
    m = re.match(r"(\s*api_key:\s*)(\S.*?)(\s*)$", lines[idx])
    if not m:
        print(f"FAIL 第 {idx + 1} 行格式不认识，未改动")
        raise SystemExit(1)
    old_lens[which] = len(m.group(2))
    # 保留原来的缩进与键名，只换值；行尾换行符保留
    nl = "\n" if lines[idx].endswith("\n") else ""
    lines[idx] = f"{m.group(1)}{new_key}{nl}"

io.open(CFG, "w", encoding="utf-8", newline="").writelines(lines)
print(f"已写入两处（旧值长度：cloud={old_lens['cloud']} params={old_lens['params']}）")

# ── 回读校验 ──
back = io.open(CFG, encoding="utf-8").read().splitlines()
v_cloud = None
v_params = None
for i, l in enumerate(back):
    s = l.strip()
    if s.startswith("#") or ":" not in s:
        continue
    if s.split(":", 1)[0].strip() != "api_key":
        continue
    for j in range(i - 2, max(-1, i - 8), -1):
        m = re.match(r"\s*(cloud|params):\s*$", back[j])
        if m:
            val = s.split(":", 1)[1].strip()
            if m.group(1) == "cloud":
                v_cloud = val
            else:
                v_params = val
            break

assert v_cloud == new_key, "回读：cloud 段不是新值"
assert v_params == new_key, "回读：params 段不是新值"
assert v_cloud == v_params, "回读：两处不一致"
print(f"回读校验：两处都是新值且一致（len={len(new_key)}，gsk_ 前缀）")

# ── 最关键的一条：产品**实际生效**的值 ──
import yaml                                                        # noqa: E402
from core.plugin_params import plugin_params                       # noqa: E402

cfg = yaml.safe_load(io.open(CFG, encoding="utf-8").read())


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
eff_key = eff.get("api_key")
print()
print(f"产品实际生效的 api_key：{'就是新值 ✔' if eff_key == new_key else '**不是新值 ✘**'}"
      f"（len={len(eff_key) if isinstance(eff_key, str) else 'N/A'}）")
if eff_key != new_key:
    print("FAIL 生效值不是新 key —— 说明还有别处在覆盖它，先别继续")
    raise SystemExit(1)
print("OK：两处已改、一致，且产品生效值确认为新 key")
