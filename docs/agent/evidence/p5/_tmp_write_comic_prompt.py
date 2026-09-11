# -*- coding: utf-8 -*-
r"""生成**美漫风**上身立绘提示词：`docs/agent/evidence/manual/绘图提示词-美漫风.md`

为什么要单独出一份而不是改原来那份：
  1. 风格换了（动漫 → 欧美漫画），提示词几乎完全不同
  2. 用户明确要求"不要背影" —— 原文档只写了"正面或接近正面"，
     这次要写成**硬约束**并进负面提示词
  3. 美漫风与"缩到 128px 显示"之间存在一个**必须处理的冲突**：
     密集排线/网点会在缩放后变成脏灰，所以要求平涂+大块阴影

配色在原表基础上做了美漫化调整（高对比）：
  · 阴影偏冷（美漫常用冷灰/蓝灰而不是暖褐）
  · 最暗处压到近黑，作为大面积实心暗部
  · 线稿色另给一个（真正的纯黑会让 128px 下糊成一团，用深棕黑更稳）
"""
import io
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
OUT = ROOT / "docs" / "agent" / "evidence" / "manual" / "绘图提示词-美漫风.md"

# 美漫化配色（在照片取色的基础上调对比与色温）
PAL = [
    ("hair_base",   "#1C1618",  "✔ 量出", "底色，保持不变（照片里就是近黑）"),
    ("hair_solid",  "#0E0B0C",  "~ 参考", "**实心暗部**（美漫的大块黑），比底色再压暗"),
    ("hair_hi",     "#5A6B7A",  "~ 调整", "高光**改冷蓝灰** —— 美漫头发高光常用冷色，"
                                          "暖褐会显得像日漫"),
    ("skin",        "#E8C9B8",  "✔ 量出", "比原取值略提亮，为了与黑发拉开对比"),
    ("skin_shadow", "#B98F7E",  "~ 调整", "阴影**偏冷偏红**，不做暖褐"),
    ("skin_black",  "#3A2A26",  "~ 参考", "最暗处（下巴下方/颈部投影），美漫常直接压近黑"),
    ("blush",       "#D98A7A",  "~ 参考", "腮红（美漫里更克制，可选）"),
    ("dress",       "#DCDEE0",  "✔ 量出", "白裙，略偏冷"),
    ("dress_shade", "#A8AEB4",  "~ 调整", "裙褶阴影**偏冷灰**"),
    ("dress_line",  "#3A3F45",  "~ 参考", "裙子的线稿色"),
    ("line_art",    "#241A18",  "~ 参考", "**线稿色**：不要用纯黑 —— 缩到 128px 会糊成脏块"),
    ("eye_iris",    "#4A3226",  "✗ 推断", "照片里眼睛太小，不是量出来的"),
    ("lips",        "#C0705F",  "~ 调整", "美漫唇色更实、更红一点"),
]

L = []
w = L.append

w("# 绘图提示词 —— 欧美漫画风 · 上半身")
w("")
w("> **用途**：拿去 AI 绘图工具生成桌宠形象的上半身立绘。")
w("> **产出的图怎么用**：见 `精灵图交接规格.md`；本文件只管「怎么画出来」。")
w("")
w("---")
w("")
w("## 一、先说一个必须处理的冲突")
w("")
w("**欧美漫画的招牌技法是密集排线和网点（halftone）**，")
w("但这些是**高频细节**，而桌宠里这张图最终要**缩到 128×128 显示**。")
w("实测：现有精灵图的角色只有 **52×96 像素**。")
w("")
w("高频排线缩到这个尺寸会发生什么：")
w("")
w("| 原图特征 | 缩到 128px 后 |")
w("|---------|--------------|")
w("| 细密交叉排线 | 糊成一片**脏灰** |")
w("| 网点（halftone dots） | 变成**摩尔纹**（一圈圈波纹） |")
w("| 发丝级细线 | 断线、看不见 |")
w("| 锐利细线稿 | 变粗、糊边 |")
w("")
w("**所以要的是「美漫的造型与配色」，不是「美漫的排线技法」**：")
w("")
w("- ✅ 要：**大块平面色 + 硬边阴影（cel shading）**、粗轮廓线、夸张的体块概括")
w("  —— 参考 Mike Mignola（《地狱男爵》）、Bruce Timm（《蝙蝠侠动画版》）、")
w("  美式动画角色设计稿")
w("- ❌ 不要：Jim Lee 那种密集交叉排线、四色印刷网点、写实厚涂")
w("")
w("这两者**都是欧美漫画风**，区别只在技法密度。选前者，图才能用。")
w("")

w("---")
w("")
w("## 二、配色（已在照片取色基础上做美漫化调整）")
w("")
w("| 变量 | HEX | 可信度 | 说明 |")
w("|------|-----|--------|------|")
for n, h, c, note in PAL:
    w(f"| `{n}` | `{h}` | {c} | {note} |")
w("")
w("调整逻辑（不是随手改的）：")
w("")
w("1. **高光改冷色**（`#5A6B7A`）：美漫上色用冷色打头发/皮肤高光，")
w("   暖褐高光会立刻变成日漫观感")
w("2. **阴影偏冷**：美漫阴影常用冷灰/蓝灰，`skin_shadow` 与 `dress_shade` 都往冷里调")
w("3. **最暗处压近黑**：美漫敢用大面积实心黑，`hair_solid` / `skin_black` 就是为此")
w("4. **线稿不用纯黑**（`#241A18`）：纯黑线在 128px 下会和暗部粘成一团")
w("")
w("⚠️ `eye_iris` 是**推断值**（照片里眼睛太小），不是测量结果。")
w("")

w("---")
w("")
w("## 三、提示词 —— 英文（推荐，SD / MJ / NovelAI 效果更稳）")
w("")
w("```")
w("American comic book style character design, upper body portrait, waist-up crop,")
w("young woman facing directly forward, looking at viewer, centered composition,")
w("")
w("bold clean ink outlines, flat cel-shaded coloring, large graphic shadow shapes,")
w("high contrast, cool-toned shadows, minimalist shading, no hatching, no halftone dots,")
w("")
w("long wavy hair past chest, near-black hair with cool blue-grey highlights,")
w("large solid black hair masses, side-swept bangs, hair framing the face but not")
w("covering the eyes or mouth,")
w("")
w("oval face, slightly pointed chin, warm light skin, cool red-brown shading,")
w("large expressive dark-brown eyes, clearly visible and unobstructed,")
w("strong dark eyebrows, confident friendly expression, subtle blush,")
w("")
w("white sleeveless V-neck dress, cool grey shading in the folds,")
w("bare shoulders and arms, simple graphic fabric folds,")
w("")
w("plain flat solid background, fully transparent or single flat color,")
w("no scenery, no text, no watermark, no signature,")
w("")
w("vertical composition, 4:5 aspect ratio, centered, character fills the frame height")
w("")
w("--no photorealistic face, no hatching, no cross-hatching, no halftone, no screentone")
w("```")
w("")
w("### 若工具支持权重语法（SD / ComfyUI）")
w("")
w("```")
w("(American comic book style:1.3), (bold ink outlines:1.2), (flat cel shading:1.3),")
w("(large graphic shadow shapes:1.2), (cool shadows:1.1),")
w("(upper body, waist-up:1.4), (facing forward, front view:1.5),")
w("(eyes and mouth clearly visible:1.3),")
w("(simple clean shapes:1.2),")
w("(halftone:0), (cross-hatching:0), (screentone:0), (photorealistic:0),")
w("(complex background:0), (full body:0), (back view:0), (side view:0)")
w("```")
w("")

w("---")
w("")
w("## 四、提示词 —— 中文（即梦 / 通义万相 / 文心一格）")
w("")
w("```")
w("欧美漫画风格角色设计，上半身立绘，画到腰部以上。")
w("年轻女性，正面直视前方，居中构图。")
w("")
w("粗而干净的墨线轮廓，平涂上色（cel shading），大块面硬边阴影，高对比度，")
w("冷色调阴影，极简上色。**不要**密集排线，**不要**网点，**不要**厚涂写实。")
w("")
w("长卷发过胸，近乎黑色，高光用冷蓝灰，头发大块实心黑面，斜分刘海；")
w("头发围住脸但**不能遮住眼睛和嘴**。")
w("")
w("鹅蛋脸、下巴略尖；暖白肤色，阴影用冷红棕；")
w("大眼睛、深褐色，**完整可见不被遮挡**；眉毛浓深；表情自信友善；淡腮红。")
w("")
w("白色无袖 V 领连衣裙，裙褶用冷灰阴影；露肩露臂；衣褶概括成简单大块。")
w("")
w("背景**纯色或完全透明**，不要场景、不要文字、不要水印、不要签名。")
w("")
w("竖构图，4:5 画幅，居中，角色占满画面高度。")
w("```")
w("")

w("---")
w("")
w("## 五、负面提示词（两边都用）")
w("")
w("```")
w("背面, 背影, 侧脸, 侧身, 回头, 不看镜头,")
w("全身, 双腿, 复杂背景, 场景, 水印, 文字, 签名, logo,")
w("多个人物, 写实照片风, 3D渲染, 厚涂,")
w("密集排线, 交叉排线, 网点, 网屏, 半色调,")
w("头发遮住脸, 刘海盖住眼睛, 嘴巴被挡,")
w("畸形手指, 多余手臂, 不对称眼睛, 模糊, 低分辨率, 过曝, 死黑一团")
w("```")
w("```")
w("back view, from behind, side view, profile, looking away, head turned,")
w("full body, legs, complex background, scenery, watermark, text, signature, logo,")
w("multiple characters, photorealistic, 3D render, oil painting,")
w("cross-hatching, hatching, halftone, screentone, dense linework,")
w("hair covering face, bangs over eyes, mouth obscured,")
w("deformed hands, extra arms, asymmetric eyes, blurry, lowres, overexposed, crushed blacks")
w("```")
w("")

w("---")
w("")
w("## 六、生成参数建议")
w("")
w("| 项 | 建议 | 理由 |")
w("|----|------|------|")
w("| 画幅 | **4:5 或 3:4 竖构图** | 上半身是竖的；方形会有很多空白 |")
w("| 分辨率 | **≥ 1024×1280** | 最终显示 127px，还要做多帧缩放 |")
w("| 采样步数 | 28~35 | 平涂风格不需要太高，但太低线会断 |")
w("| CFG / 引导强度 | 7~9 | 太低风格跑偏，太高线稿变脏 |")
w("| 批量 | **一次生成 8 张** | 挑一张五官位置规整、没被头发遮的 |")
w("| 模型倾向 | 美漫/动画类 checkpoint | 通用写实模型很难出对风格 |")
w("| ControlNet | 可用照片做 **pose/构图**参考，强度 **0.3~0.5** | 高了会画成照片风；只借大形，不借长相 |")
w("")
w("### 关于「像不像照片里的人」")
w("")
w("**不要把照片喂进去做 img2img 高强度重绘** —— 那样出来的会偏写实，")
w("和美漫风冲突。用 ControlNet 借**构图与姿势**，长相让模型按美漫风自己发挥。")
w("")
w("如果你确实要「像她」，那需要的是**写实风格**的形象，与美漫画风二选一。")
w("这一点请先定下来，否则会来回返工。")
w("")

w("---")
w("")
w("## 七、拿到图后先过这份清单")
w("")
w("- [ ] **正面朝前**（不是背影、不是侧脸 —— 这是硬约束）")
w("- [ ] 上半身，腰部以上，没有全身")
w("- [ ] **眼睛与嘴完整可见**，没被头发遮住")
w("- [ ] 背景纯色或透明，没有场景")
w("- [ ] 没有水印、文字、签名")
w("- [ ] 看样子是**平涂**而不是密集排线（放大看图，线条之间不该有细密斜纹）")
w("- [ ] 分辨率 ≥ 1024×1280")
w("- [ ] 角色居中，上下留白对称")
w("")
w("**任何一条不过就重新生成** —— 拿回去重画比后面修补便宜得多。")
w("")
w("> 最后一关（缩到 128px 看还认不认得出）我来做：")
w("> 我会真的把它缩到 128×128 再放大检查，糊掉的会告诉你。")
w("")

w("---")
w("")
w("## 八、成品交付给我时")
w("")
w("1. **原图**（1024×1280 以上，PNG 最好）")
w("2. 如果生成工具有**种子/参数**，一起给我 —— 这样要微调时可以复现同一张")
w("3. 若你生成了多张，把候选都给我，我来挑**五官位置最规整**的那张做动画")
w("")
w("接下来我会：抠图（`rembg`）→ 缩到 128×128 并对位到模板 → ")
w("局部重绘眼与嘴做眨眼/说话帧 → 接进程序 → 跑验收。")

OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text("\n".join(L) + "\n", encoding="utf-8")
print(f"写出 {OUT.relative_to(ROOT)}（{OUT.stat().st_size} bytes，{len(L)} 行）")
