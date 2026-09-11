# -*- coding: utf-8 -*-
"""P5-C1 / C2 记账：把本轮做的"人工前置工作"如实记进账本。

**这两项的终态仍然是「待人工」，我不改它们的状态。** 改的是它们的可执行性：

  · P5-C1：原先 evidence 是空的 —— 那会让账本读起来像"什么都没做"。
    现在有 `key_rotation.txt`（轮换前实测基线：两把 key 都还活着、
    降级链路的 function calling 是通的、**以及轮换要改哪三处**）。
  · P5-C2：新增 `manual/执行清单.md`（一页可执行的核对单）+ `manual/README.md`（命名规范）。
    证据本身仍然要人来做 —— `check_manual_evidence.py` 现在如实报「还差 6 条」。

同时把两处**极易踩的坑**写进账本的 minimal_path，避免人照着手册做却做不成：
  · C1：`cloud.api_key` **覆盖** `params.api_key` —— 只改 params 等于没改
  · C2：打断热键是 `Ctrl+Alt+D`（代码注释原先写 `Ctrl+Alt+Space`，已修）

**不改状态**这一点是刻意的：终态是"待人工"，前置工作做完不改变这一点。
把状态改成 fixed/done 就是拿"准备工作"冒充"轮换完成"——
那正是本项目一路在防的"纸面满足"。
"""
import io
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
LEDGER = ROOT / "docs" / "agent" / "tasks-p5.json"

data = json.loads(io.open(LEDGER, encoding="utf-8").read())

# ── C1 ──
c1 = next(t for t in data["tasks"] if t["id"] == "P5-C1")
assert c1["state"] == "human", f"C1 状态应保持 human，实际 {c1['state']}"
kr = "docs/agent/evidence/p5/key_rotation.txt"
assert (ROOT / kr).is_file(), f"缺证据文件 {kr}"
assert (ROOT / kr).stat().st_size > 0
c1["evidence"] = [kr]
c1["planned_evidence"] = []
c1["minimal_path"] = (
    "① 厂商控制台各生成新 key（Groq `gsk_` / OpenRouter `sk-or-v1-`）；"
    "② 覆盖 config.yaml 的**三处**：`plugins.llm.cloud.api_key`（主，**它覆盖下面的**）、"
    "`plugins.llm.params.api_key`（主，一起改，否则两处长期不一致）、"
    "`plugins.llm.params.fallback_api_key`（备）；"
    "③ `python tools/probe_llm_capability.py --engine universal` 验证；"
    "④ 到厂商控制台**吊销旧 key**（只改本地不算轮换）。"
    "轮换前基线见 evidence/p5/key_rotation.txt：当前两把 key **都还有效**"
    "（主 key 报 429 限流而非 401，备 key 实测可降级成功），"
    "且降级链路的 chat_with_tools() 实测可用。"
)
c1["acceptance"] = [
    "探针输出不再出现 401/403（429 允许：那是额度不是身份）",
    "能力探测通过数 ≥ 轮换前基线 5/6",
    "厂商控制台里旧 key 显示 Revoked/Deleted（这是唯一权威判据，本地测不出来）",
    "`git check-ignore config.yaml` 仍返回 0（新 key 不进 git 历史）",
]
print(f"C1：证据已挂 {kr}，状态保持 {c1['state']}")

# ── C2 ──
c2 = next(t for t in data["tasks"] if t["id"] == "P5-C2")
assert c2["state"] == "human", f"C2 状态应保持 human，实际 {c2['state']}"
cl = "docs/agent/evidence/manual/执行清单.md"
rd = "docs/agent/evidence/manual/README.md"
for rel in (cl, rd):
    assert (ROOT / rel).is_file(), f"缺文件 {rel}"
c2["evidence"] = ["docs/agent/evidence/manual/", cl, rd]
c2["minimal_path"] = (
    "① `python tools/prepare_manual_acceptance.py init`（建沙箱 + 临时改白名单）"
    "→ `启动欣雅.bat`；"
    "② 照 `docs/agent/evidence/manual/执行清单.md` 逐条走（6 条：M1~M5 + G1），"
    "逐条存证据到 `docs/agent/evidence/manual/`；"
    "③ 填 `docs/agent/evidence/manual/结论.md`（判定词只认 通过/部分通过/不通过/未做）；"
    "④ **必做** `prepare_manual_acceptance.py restore` + `cleanup`；"
    "⑤ `python tools/check_manual_evidence.py` 校验。"
    "当前校验结果：**还差 6 条（M1, M2, M3, M4, M5, G1）**，即一条都没做。"
    "注意打断热键是 `Ctrl+Alt+D`（配置键 voice.hotkey_interrupt，"
    "清单里的值是从 config 读出来的，不是手抄）。"
)
c2["acceptance"] = [
    "`check_manual_evidence.py` 报「全部齐」且无「未做」",
    "结论.md 每条都有实测判定（通过/部分通过/不通过），不是占位",
    "M2 预览里的文件名必须是验收截图那两个 PNG（缺陷 14 的验收点）",
    "M5 审计库里能查到这次拒绝的记录",
    "结论需写明机器与日期（哪台机器、什么麦克风、什么版本）",
]
print(f"C2：证据已挂 {cl}，状态保持 {c2['state']}")

data["tasks"] = [c1 if t["id"] == "P5-C1" else c2 if t["id"] == "P5-C2" else t
                 for t in data["tasks"]]
io.open(LEDGER, "w", encoding="utf-8", newline="\n").write(
    json.dumps(data, ensure_ascii=False, indent=2) + "\n")

# 复读校验：状态没被改，且有 evidence 的项数量变了
back = json.loads(io.open(LEDGER, encoding="utf-8").read())
for tid in ("P5-C1", "P5-C2"):
    t = next(x for x in back["tasks"] if x["id"] == tid)
    assert t["state"] == "human", f"{tid} 状态被改动了"
    assert 0 < len(t["evidence"]) <= 3
    print(f"复读 {tid}: state={t['state']} evidence={t['evidence']}")

with_ev = sum(1 for t in back["tasks"] if t.get("evidence"))
print(f"有证据的登记项：{with_ev}/18（其余是「明确不做」，本就不需要证据）")
states = {}
for t in back["tasks"]:
    states[t["state"]] = states.get(t["state"], 0) + 1
print("终态分布（**未变** —— 待人工还是待人工）：", states)
