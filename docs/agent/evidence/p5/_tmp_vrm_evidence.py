# -*- coding: utf-8 -*-
r"""生成证据：`docs/agent/evidence/p5/vrm_facing.txt`

起因：用户报「形象是背身的、双手举过头，动作诡异」。查下去发现**四个独立问题**，
其中两个是产品缺陷、一个是我自己造成的测量错误。

本文件把整条链路留档：现象 → 假设 → 被推翻的假设 → 明文数据 → 结论。
"""
import io
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
OUT = ROOT / "docs" / "agent" / "evidence" / "p5" / "vrm_facing.txt"
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
    w(f"脚本：`docs/agent/evidence/p5/{script}`　退出码：{p.returncode}")
    w("")
    w("```")
    w((p.stdout or "").rstrip())
    w("```")


w("═" * 78)
w("证据：VRM 形象「背身 + 双手举过头」的成因")
w("═" * 78)
w("")
w(f"生成时间：{datetime.now():%Y-%m-%d %H:%M:%S}")
w("")
w("**现象（用户报告）**：桌宠形象背对用户，双手举过头顶，动作诡异。")

rule("★ 结论先行：这是 4 个独立原因叠在一起")
w("| # | 问题 | 性质 | 状态 |")
w("|---|------|------|------|")
w("| 1 | 运行中的模型从 `cat.vrm` 变成了 `AvatarSample_A.vrm` | 配置回退（**我自己造成的**） | 已解释 |")
w("| 2 | `app.js` 对所有模型一律 `rotation.y = Math.PI` | **产品缺陷**：VRM 0.x 与 1.0 正面相反，写死必错一半 | 已改成可配 |")
w("| 3 | 静态 JS 没有缓存失效参数 | **产品缺陷**：改了 `app.js` 完全不生效 | 已修 |")
w("| 4 | `AvatarSample_A` 没有尾巴骨骼，接不上猫的动画 | 模型与动画系统不匹配（选型问题） | 换回 cat.vrm |")

rule("一、问题 1：模型为什么从 cat 变成 AvatarSample_A")
w("这不是我「猜」的，是**应用日志自己说的**：")
w("")
w("- 切换后的那次启动，日志报 `配置文件加载失败: while scanning a double-quoted scalar")
w("  in \"config.yaml\", line 105, column 22`，随后 `3/5 初始化插件系统` 一路用**默认值**跑完，")
w("  并连打 5 次 `配置文件保存成功`。")
w("- `core/config_manager.DEFAULT_CONFIG` 里的 `ui.vrm_model` 是 `assets/vrm/cat.vrm`；")
w("- 而真正的 `config.yaml`（与 `config.example.yaml`、README）里写的是")
w("  `assets/vrm/AvatarSample_A.vrm`。")
w("")
w("⇒ 崩溃那次**读失败 → 回退默认值 → 存回磁盘**，于是显示的是 `cat.vrm`；")
w("   配置修好后读到了真值 `AvatarSample_A.vrm`，就「变」了。")
w("   **不是形象被改动，而是它本来就配的是 AvatarSample_A。**")
w("")
w("（为什么 `config.yaml` 会读失败：`prepare_manual_acceptance.py init` 用双引号包了")
w(" Windows 路径，`\\U` 被当成 Unicode 转义 —— 详见 `g8_quota_skip.txt` 之外的")
w(" 单独记录，属另一个缺陷。）")

rule("二、问题 2：朝向写死 180°（**产品缺陷**）")
w("`assets/vrm/js/app.js:298-299`（改动前）：")
w("")
w("```js")
w("// 让角色正面朝向镜头：VRoid 常默认面向 -Z，相机在 +Z，故绕 Y 转 180°")
w("try { vrm.scene.rotation.y = Math.PI; } catch (_) {}")
w("```")
w("")
w("注释里的「VRoid 常默认面向 -Z」**对 VRM 1.0 成立，对 VRM 0.x 不成立** ——")
w("VRM 0.x 的正面是 **+Z**。而 `assets/vrm/` 里两种格式都有：")
w("")
w("| 模型 | 规范 | 正面 |")
w("|------|------|------|")
w("| `AvatarSample_A.vrm` | VRM 0.x（VRoidStudio-0.14.0 导出） | **+Z** |")
w("| `chibi.vrm` | VRM 0.x（同为 VRoidStudio 导出） | **+Z** |")
w("| `cat.vrm` | VRM 1.0 | −Z |")
w("| `chibi_handmade.vrm` | VRM 1.0（模型名「小忆Chibi」） | −Z |")
w("")
w("⇒ 一律转 180°，**必然把其中一半的模型转成背对镜头**。")
w("   这就是「背身」的直接原因（0.x 模型被多转了 180°）。")

cap("_tmp_vrm_orientation.py",
    "二之一、先读节点旋转（这一步的结论是「节点里没有朝向信息」）")
w("")
w("**这一步的结果出乎意料**：四个模型的根节点旋转**全是 `[0,0,0,1]`（零旋转）**。")
w("也就是说朝向**不在节点层级里**，而是烘进网格顶点的。")
w("⇒ 「看根节点旋转判朝向」这条路走不通，得换判据。")

cap("_tmp_vrm_spec_orientation.py",
    "二之二、改读 VRM 规范声明的正面方向 + 面部网格实测")

rule("三、问题 3：静态 JS 没有缓存失效（**产品缺陷**）")
w("改完 `app.js` 之后，我加的那行诊断日志")
w("`console.log('[petX] 面向修正 yaw=...')` **在应用日志里完全没出现**。")
w("")
w("排查发现：`ui/pet_window.py` 只给 **模型路径** 加了防缓存时间戳：")
w("")
w("```python")
w("_query.addQueryItem('_t', str(int(time.time() * 1000)))   # 只影响 ?model=")
w("```")
w("")
w("而 `viewer.html` 引的静态脚本是**裸相对路径**：")
w("")
w("```html")
w("<script src=\"./js/bridge.js\"></script>")
w("<script type=\"module\" src=\"./js/app.js\"></script>")
w("```")
w("")
w("⇒ QWebEngine 沿用缓存的旧 `app.js`。**表现是「改了 JS 却完全没生效」，")
w("   而服务端文件、`py_compile`、grep 全部显示正确** —— 极难排查。")
w("")
w("修法：`viewer.html` 改为动态注入脚本，并把 Python 已经传过来的 `_t`")
w("拼到脚本地址上（`./js/app.js?t=<_t>`）。这样「改了 JS 立刻生效」不再依赖")
w("用户手动清缓存。")

cap("_tmp_yaw_wiring.py",
    "三之一、验证 yaw 参数确实传到了 viewer（证明问题在缓存而非接线）")

rule("四、问题 4：AvatarSample_A 接不上猫的动画")
w("`app.js` 的 `makeBoneBook()` 要的骨骼：")
w("")
w("```")
w("hips / spine / chest / upperChest / neck / head")
w("leftUpperArm / leftLowerArm / rightUpperArm / rightLowerArm")
w("+ 名字里含 tail 的骨骼（尾巴）")
w("```")
w("")
w("这是**为猫写的**动画系统（要尾巴）。日志里的佐证：")
w("")
w("| 模型 | 日志中的模型标识 | 表情数 |")
w("|------|-----------------|--------|")
w("| `AvatarSample_A.vrm` | `name=unknown` | 13 |")
w("| `cat.vrm` | `name=VRM1_Constraint_Twist_Sample` | **18** |")
w("")
w("`AvatarSample_A` 是 Unity 官方示例人形（`AvatarSample_A`），**没有尾巴**；")
w("骨骼名对不上 → 动画接不上 → **保持默认姿势**，")
w("也就是用户看到的「双手举过头」（T-pose 变体）。")

rule("五、处置")
w("| 改动 | 文件 | 理由 |")
w("|------|------|------|")
w("| 朝向角度改为可配：`ui.vrm_yaw_deg` → `?yaw=` → `MODEL_YAW_DEG` | `ui/pet_window.py`、`assets/vrm/js/app.js`、`config.yaml` | 写死必错一半；给用户一个开关 |")
w("| 静态脚本加缓存失效 | `assets/vrm/viewer.html` | 否则「改了不生效」会反复咬人 |")
w("| 模型换回 `cat.vrm` | `config.yaml`（`ui.vrm_model`） | 猫有尾巴、动画 18 个表情，是这个项目原生的形象 |")
w("")
w("### 关于 `ui.vrm_yaw_deg` 的取值")
w("")
w("**这一项我没有唯一确定** —— 依据是分歧的：")
w("")
w("- 规范说 VRM 1.0 正面朝 −Z（`cat.vrm` 属 1.0）⇒ 转 180 该是对的；")
w("- 但 `cat.vrm` 的 `head` 骨骼静止四元数算出 **绕 Y +90°**，")
w("  `Face` 网格顶点又全在 **+Z 侧**（z ∈ [0.038, 0.077]）——")
w("  骨骼旋转与网格偏移叠加后的最终朝向，**光靠读数据算不准**。")
w("")
w("所以我把它做成可配项，用**实际渲染结果**来定，而不是继续推理。")
w("当前值：**0**（在 `cat.vrm` 上试）。")

rule("六、边界声明（这份证据**不**说明什么）")
w("- 我**没有看到屏幕**。浏览器桥未连接，所以我全程靠读文件与日志，")
w("  **「哪个角度看起来是正面」这一步仍然需要人确认**。")
w("- `ui.vrm_yaw_deg` 的最终值取决于用户实际观感，本文件不代它下结论。")
w("- 问题 4 的「动画接不上」是**推断**（依据是骨骼名与日志里的 `name=unknown`），")
w("  我没有逐骨骼比对两个模型的 `humanBones` 映射表。")
w("  可反驳方式：若 `AvatarSample_A` 其实有尾巴骨骼且动画能跑，则该推断不成立。")
w("- 其余三个模型的朝向只读了规范声明，**没有逐个验证网格顶点**。")

w("")
w("═" * 78)
w(" 一句话")
w("═" * 78)
w("")
w("**「背身」是 VRM 0.x/1.0 正面方向相反、而代码一律转 180° 造成的；")
w("「双手举过头」是选的模型接不上为猫写的动画。**")
w("另外顺带查出一个更难发现的缺陷：静态 JS 没有缓存失效，")
w("导致「改了 app.js 完全不生效」而所有静态检查都显示正常。")

OUT.write_text("\n".join(L) + "\n", encoding="utf-8")
print(f"写入 {OUT}（{OUT.stat().st_size} bytes）")
