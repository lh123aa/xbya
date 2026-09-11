# -*- coding: utf-8 -*-
"""把 AGENTS.md 末尾「验收阶段 …」整段移到 docs/agent/acceptance-history.md，
并补上 P5 的「完成度（连口径）」一节。

为什么必须做：AGENTS.md 有 65536 字节的工作区指令预算，超了会被**静默截断**
（本次已经真的发生了一次：65927 → 65203，指令里少了 700 多字节）。
做法与 §11~§14 → `phases.md`、原 §17 → `f-closure.md` 一致：历史细节移出，一条不丢。
"""
import sys
from pathlib import Path

ROOT = Path(r"E:\程序\桌面宠物\xiaoyi-vrm-worktree")
AGENTS = ROOT / "AGENTS.md"
HIST = ROOT / "docs" / "agent" / "acceptance-history.md"

text = AGENTS.read_text(encoding="utf-8")
before = len(text.encode("utf-8"))

START = "## 验收阶段（`tools/run_acceptance.py` 一次跑完 **13 关**）"
i = text.find(START)
if i < 0:
    print("FAIL 找不到验收阶段段首")
    sys.exit(1)
moved = text[i:]
assert "G13 仓库卫生" in moved and "未修 / 待下轮" in moved, "切片内容不对，拒绝执行"

NEW = """## 完成度（连口径 —— 不许给没有分母的百分比）

**先说结论：全工程没有一个可信的百分比，因为"工程"没有分母。**
所以这里只报**有分母的那几个**，以及**为什么剩下的算不出来**。

| 口径 | 分母 | 已完成 | 说明 |
|------|------|--------|------|
| 覆盖率（唯一有硬分母的） | `core/kernel + agent + services.ack_cache` 共 **7238 语句** | **7238 / 7238 = 100%** | `ui/` / `plugins/` / `core/app.py` / 大部分 `services/` **不在口径内** —— 别把这个 100% 读成"工程 100%" |
| 验收关卡（内部） | G1~G7 + G13 = **7 关** | **7 / 7** | 每关都有原始输出 |
| 验收关卡（外部依赖） | G8~G12 = **6 关** | **6 / 6 退出码 0** | ⚠️ G8 依赖免费配额，**有跳过项时按设计不算通过**，报告里单独标出 |
| P5 登记项 | **17 项** | 见下表 | 闭环判据不是"做完"，而是"每项都有终态" |
| 测试用例 | 全量 `pytest tests` | **2668 passed / 1 skipped / 0 failed** | skipped 的那 1 项是环境相关，不是"忽略失败" |

**P5 登记项逐条终态**（四种终态，没有第五种）：

| 终态 | 项数 | 具体 |
|------|------|------|
| ✅ 已修 | 2 | P5-A2（D17 + 顺带 D18） |
| ✅ 已实测/已完成 | 3 | P5-A1（账本刷新 + 完成度）、P5-A3（D14 终态登记）、P5-AUDIT（收口审计） |
| ⬜ 明确不做 | 6 | P5-C3 D3 / C4 天气 IP / C5 D7 / C6 繁简边界 / C7 诱饵页 / C8 历史账本 |
| 🟡 待人工 | 2 | P5-C1 轮换 key、P5-C2 麦克风+GUI+人耳验收（各附最小操作路径与判据） |
| ⏳ 未到终态（P1 可选增强） | 5 | P5-B1 D16 守护 / B2 voice_service 传参 / B3 ST+vec0 / B4 medium 长句 / B5 SERP fixture |

**为什么剩下的算不出百分比**：P5-B1~B5 是**可选增强**，不是已登记缺陷的偿还 ——
它们没有"完成定义"上的分母（做 3 项和做 5 项都满足"闭环"）。硬凑一个百分比，
就是在给一个不存在分母的东西编数字。

**口径内的边界（什么不算"已验证"）**：

| 不算 | 原因 |
|------|------|
| G12 语音回环通过 | 它吃**固定测激**，测的是**管线**；**麦克风采集 / GUI 动效 / 人耳听感**只有人能判（P5-C2） |
| 覆盖率 100% | 只覆盖上表那一行写明的模块；`ui/` 等**没测** |
| "适配层不做" | 是**人工决定**，不是"已统一" —— 旧 `core.event_bus.EventBus` **仍存在且在服务语音层** |
| G8/G9/G10 退出码 0 | 依赖免费配额与第三方站点；G8 配额耗尽时会**报跳过**，报告单独标出 |

演示与运维脚本、历史阶段的立项背景 / 7 处"检查本身出错" / 3 处"纸面满足" /
验收轮 4 个假绿假红（19~22）/ 13 关明细：见 `docs/agent/p4-history.md` 与
`docs/agent/acceptance-history.md`（**原文一字未删，只是换位置**）。

---
"""

text = text[:i] + NEW
AGENTS.write_text(text, encoding="utf-8")
after = len(text.encode("utf-8"))

HIST.write_text(
    "# 验收阶段明细（原 AGENTS.md 末段）\n\n"
    "> 从 `AGENTS.md` 移出：该文件有 **65536 字节的工作区指令预算**，超了会**静默截断**。\n"
    "> 本次真的发生过一次（65927 → 65203，指令里少了 700 多字节）——\n"
    "> 权威文档被悄悄截尾比内容长危险得多。移出的是**历史明细**，原文一字未改。\n"
    "> 当前约定、完成度与 P5 终态见 `AGENTS.md`。\n\n---\n\n" + moved,
    encoding="utf-8",
)
print(f"AGENTS.md: {before} -> {after} bytes (delta {after - before:+d})")
print(f"预算 65536 剩余：{65536 - after}")
print(f"acceptance-history.md: {HIST.stat().st_size} bytes")
