# -*- coding: utf-8 -*-
"""生成 P5-C2 的**一页执行清单**：`docs/agent/evidence/manual/执行清单.md`

为什么单独生成一份：`docs/agent/manual-acceptance.md` 是**手册**（讲清为什么、
失败时抓什么、每条的设计意图），信息全但需要来回翻。
人坐在电脑前、麦克风开着的时候，需要的是**一页能照着走的东西**：
先跑哪条命令、念哪句话、看什么、截图叫什么名字、怎么算过。

两份文件的分工：
  · `manual-acceptance.md` = 白皮书（为什么这么做、失败怎么排查）
  · 本文件（执行清单）      = 走一遍用的核对单（按顺序、可勾）

本脚本**不复制**手册里的判据细节，只做压缩与排序，并在关键处指回手册。
"""
import io
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "docs" / "agent" / "evidence" / "manual" / "执行清单.md"

# 从产品里**读**热键，不手抄 —— 本轮就是靠这个发现注释写的是 Ctrl+Alt+Space
# 而实际绑的是 Ctrl+Alt+D（写死一个可配置的值必然过期）。
import yaml                                                        # noqa: E402

cfg = {}
try:
    cfg = yaml.safe_load(io.open(ROOT / "config.yaml", encoding="utf-8").read()) or {}
except Exception:                                                  # noqa: BLE001
    pass
voice = (cfg.get("voice") or {})
HK_TOGGLE = voice.get("hotkey_toggle", "Ctrl+Alt+M")
HK_MUTE = voice.get("hotkey_mute", "Ctrl+Alt+S")
HK_INTR = voice.get("hotkey_interrupt", "Ctrl+Alt+D")

# 素材清单也从准备脚本里读，不手抄
try:
    from tools.prepare_manual_acceptance import SEED_FILES, WHITELIST_DIRS, SANDBOX
    seed_names = sorted(SEED_FILES)
except Exception:                                                  # noqa: BLE001
    seed_names, WHITELIST_DIRS, SANDBOX = [], [], "（读不到）"

L = []
w = L.append

w("# 人工验收执行清单（P5-C2）")
w("")
w(f"> 生成时间：{datetime.now():%Y-%m-%d %H:%M:%S}　")
w("> 可重新生成：`python docs/agent/evidence/p5/_tmp_c2_checklist.py`　")
w("> **配套手册**：`docs/agent/manual-acceptance.md`（失败时抓什么、每条的设计意图）")
w("")
w("⚠️ **这份清单里的判定必须由人来做。** AI 不能替你听、不能替你看动效、")
w("不能替你确认麦克风录到了什么。清单只负责把步骤、命令、证据文件名固定下来。")
w("")

w("---")
w("")
w("## 零、开跑前 4 项检查（不满足就先解决）")
w("")
w("| # | 检查 | 怎么确认 |")
w("|---|------|---------|")
w("| 1 | 麦克风可用 | 系统录音机能录到自己；欣雅界面麦克风状态是开的 |")
w("| 2 | 控制台可见 | 启动它的那个终端在刷日志（判失败要靠它抓现场） |")
w("| 3 | 没有挂起的确认 | 宠物若正问「要删吗？」，先说「不用了」取消掉 |")
w("| 4 | 沙箱素材齐 | `%TEMP%\\xbya_manual\\Desktop` 下有 5 个 `验收*` 文件 |")
w("")
if seed_names:
    w("本次会播种的素材（来自 `prepare_manual_acceptance.py`，非手抄）：")
    w("")
    for n in seed_names:
        w(f"- `{n}`")
    w("")

w("## 一、准备（2 条命令 + 启动）")
w("")
w("```bat")
w("python tools/prepare_manual_acceptance.py init      :: 建沙箱 + 临时改白名单 + 打印第 1 句")
w("python tools/prepare_manual_acceptance.py status     :: 确认「沙箱模式: 开」")
w("启动欣雅.bat")
w("```")
w("")
w("> `init` 会**临时**把 `config.yaml` 的白名单指到沙箱，做完**必须** `restore`。")
w("> 若上次没还原干净，`init` 会拒绝并提示 —— 那不是 bug，是防覆盖。")
w("")

w("## 二、热键（**从配置里读出来的**，不是手抄）")
w("")
w("| 用途 | 当前配置值 | 配置键 |")
w("|------|-----------|--------|")
w(f"| 麦克风开关 | `{HK_TOGGLE}` | `voice.hotkey_toggle` |")
w(f"| 静音 | `{HK_MUTE}` | `voice.hotkey_mute` |")
w(f"| **打断**（M3 要用） | **`{HK_INTR}`** | `voice.hotkey_interrupt` |")
w("")
w("> ⚠️ 本轮查出一处**文档与代码不一致**：`ui/pet_window.py` 的注释原先写")
w("> 「主打断方式为热键 `Ctrl+Alt+Space`」，而实际绑定是 `Ctrl+Alt+D`。")
w("> 照着那句注释按会按到没绑定的组合，M3 就测不出来。已修，并改成指回配置项 ——")
w("> **写死一个可配置的值，迟早会过期。**")

w("")
w("## 三、6 条场景（逐条做，做完再下一条）")
w("")

SCENES = [
    ("M1", "搜索文件", "「找一下桌面上的 PDF 文件」",
     ["确认语 **≤1.5s** 先出声/出气泡", "随后报出 **2 个**：`验收报告.pdf`、`验收合同_2025.pdf`",
      "没有把 `验收笔记.txt` / `验收截图_*.png` 报进来"],
     ["`M1-搜索-结果气泡.png`（整个欣雅窗口，气泡文字要能看清）"],
     "控制台 `[ASR]` 转写行 + `ack:`/`result:` 的 `elapsed_ms` + 截图"),
    ("M2", "删除确认（P0 最关键）", "先说「删除桌面上的验收截图」，**看预览**，再「确定」",
     ["预览里列的是 **`验收截图_01.png`、`验收截图_02.png`**（不是 PDF、不是 txt）",
      "说「确定」后回答已移到回收站", "去回收站能看到这两个 PNG"],
     ["`M2-删除-预览气泡.png`（**预览里的文件名必须看得清**）",
      "`M2-删除-回收站核实.png`"],
     "预览截图 + 控制台 `route:`（看 `pattern` 是否为空）+ `confirm:` 行 + 审计库记录"),
    ("M3", "打断", "说「找一下所有文件」，**刚开始播报时立刻按打断热键**",
     ["播报**当场停住**", "之后**不再**冒出结果播报", "宠物没卡死，随后对话正常"],
     ["`M3-打断-现象.mp4`（录屏最直接；不便录屏就 `M3-打断-说明.txt`，写清看到什么 + 贴控制台）"],
     "控制台 `[interrupt]` 行 + 打断前后的 `feedback.result` 行"),
    ("M4", "降级路径", "先说「给我讲个笑话」，再说「你好呀」",
     ["两句都走**闲聊**", "**没有**任何文件操作/截屏/弹确认框", "语气自然"],
     ["`M4-降级-笑话.png`", "`M4-降级-问候.png`"],
     "控制台 `route:` 行（`action` 不是 `chat` 就是误判）+ 截图"),
    ("M5", "安全边界", "说「打开 C 盘 Windows 文件夹」",
     ["**明确拒绝**，理由对用户可读", "系统目录**没有被打开**", "审计库里有这条拒绝记录"],
     ["`M5-安全-拒绝气泡.png`", "`M5-安全-审计记录.txt`"],
     "控制台 `safety:`/`reject:` 行 + 审计查询结果"),
]

for code, name, action, expect, evid, fail in SCENES:
    w(f"### {code} {name}")
    w("")
    w(f"- **先说**：{action}")
    w("- **看什么**：")
    for e in expect:
        w(f"  - [ ] {e}")
    w("- **证据文件**（放 `docs/agent/evidence/manual/`）：")
    for e in evid:
        # 注意：这里**不要再包一层反引号** —— 第一版写成 f"- [ ] `{e}`"，
        # 而 e 自己已经带反引号，渲染出来就是 ``M3-...``（双反引号）。
        # 生成清单的脚本把我自己的排版坑了一遍，已改。
        w(f"  - [ ] {e}")
    w(f"- **失败时抓**：{fail}")
    w("")

w("### G1 GUI 观察点（与上面 5 条**同时**看即可，不用单独做）")
w("")
w("| # | 看什么 | 是/否 |")
w("|---|--------|-------|")
w("| 1 | 说话时宠物**动了**（待机/思考/说话状态切换） | [ ] |")
w("| 2 | 确认气泡位置不挡视线、字看得清 | [ ] |")
w("| 3 | 气泡**自动消失**，不会一直挂着 | [ ] |")
w("| 4 | 播报与口型/动作**大致**同步 | [ ] |")
w("| 5 | 长时间不操作后回到待机，不僵在某个姿势 | [ ] |")
w("")
w("证据：`G1-动效-<描述>.mp4`（10~20 秒录屏足够）或 `G1-动效-说明.txt`")
w("")
w("> 1/4/5 是**主观项**：看着别扭就记「否」并写一句原因 —— 主观项的价值就在这里。")

w("")
w("## 四、审计核实命令（M5 用，直接复制）")
w("")
w("```bat")
w("""python -c "import sqlite3;c=sqlite3.connect('data/agent_audit.db');print(c.execute('select ts,action,decision,reason from agent_audit order by ts desc limit 5').fetchall())" """.rstrip())
w("```")
w("")
w("> 表名/字段不一致就先 `.tables` / `.schema` 看一眼 ——")
w("> 本条只要求「有一条关于这次拒绝的记录」，不要求字段名。")

w("")
w("## 五、收尾（**必做，别漏**）")
w("")
w("```bat")
w("python tools/prepare_manual_acceptance.py restore    :: 还原 config.yaml（靠备份，不靠手改回去）")
w("python tools/prepare_manual_acceptance.py cleanup    :: 删沙箱")
w("python tools/check_manual_evidence.py                :: 校验证据齐不齐")
w("```")
w("")
w("然后填 `docs/agent/evidence/manual/结论.md`，一条一行：")
w("")
w("```")
w("M1 搜索文件 | 通过 | 确认语很快，报出的两个 PDF 都对")
w("M2 删除确认 | 通过 | 预览只有那两个 PNG，回收站能查到")
w("M3 打断     | 不通过 | 播报停了，但随后又念了一遍结果")
w("G1 GUI      | 部分通过 | 动效有，但气泡停留偏久")
w("```")
w("")
w("判定词只认四个：`通过` / `部分通过` / `不通过` / `未做`。")
w("校验脚本**只查有没有，不替你判过不过** —— 判定权在人。")

w("")
w("## 六、有一条特别提醒（安全类）")
w("")
w("**M2 如果预览里出现了沙箱以外的文件（尤其是你自己的文件），立刻说「不用了」**，")
w("然后把「预览混进无关文件」当成**发现**记下来。")
w("那是一个真实缺陷，比「删对了」更值得报告。")
w("")
w("> 沙箱是为了让「删除截图」不会命中你真桌面上的截图 ——")
w("> 规则路由把「截图」映射成通配符 `*截图*`，在真桌面上会同时命中你自己的文件。")

OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text("\n".join(L) + "\n", encoding="utf-8")
print(f"写入 {OUT}（{OUT.stat().st_size} bytes）")
