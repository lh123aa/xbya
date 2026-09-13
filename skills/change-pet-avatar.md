---
name: change-pet-avatar
description: 给欣雅桌宠换形象（换立绘/换角色）。走「留仓库内副本 → 写 profile → 标五官坐标 → 生成精灵帧 → 切配置 → 验证」六步，配套 tools/avatar_helper.py 一键做体检、标尺图与标注复核。Use when 用户要换立绘、换角色、换形象、换图片，或桌宠外观相关（裁切/错位/不眨眼/嘴型不对）的调整。
whenToUse: 换立绘或换角色时；或桌宠外观出问题（角色被裁、位置错、眨眼看不见、嘴型跑到鼻子）需要重调坐标时。
---

# 更换桌宠形象 SOP

> 适用项目：欣雅桌面智能管家（`xbya-vrm-worktree`）
> 配套工具：`tools/avatar_helper.py`（体检 / 标尺 / 复核，见 §3）
> 版本 2.0 —— 修正了 v1.0 的三处**与实际代码不符**的说明（见文末「v1.0 勘误」）

---

## 0. 先跑一次体检

```bash
python tools/avatar_helper.py list      # 有哪些角色、哪些能重新生成
python tools/avatar_helper.py check <角色名>
```

`list` 会明确告诉你**哪些角色已经不能再生成**——这不只是信息，
它决定你能做什么：

```
角色目录               profile 存在?      帧数合计
xbya                   ✓                 208
xinya                  ✗ 无 profile      208
```

> **为什么"有没有 profile"这么关键**：`xinya` / `cat` 有精灵帧目录，
> 但生成器里没有它们的 profile ⇒ **改不动，只能沿用现有帧**。
> 想改就得先补 profile（`init` 会给模板）。

---

## 1. 准备立绘（必做：在仓库内留副本）

```bash
python tools/avatar_helper.py init <角色名> <立绘路径>
```

它会：
1. 把立绘复制到 `resources/source/<角色名>_source.<ext>`
2. 报告尺寸、模式、**alpha 分布**（这一步决定用哪种抠图策略）
3. 打印可直接粘贴的 profile 模板 + `LOCAL_SRC` 注册行

### 1.1 抠图策略由「源图有没有 alpha」决定

`init` 会打印 alpha 统计，按这个判据选：

| alpha 情况 | `keyer` 取值 | 理由 |
|---|---|---|
| **有真实半透明边**（半透明占比 > 0.1%） | `"alpha"` | 那就是画师做好的抗锯齿边，**比任何重抠都准** |
| 只有 0 / 255（二值化） | `"alpha"` + `alpha_floor` | 边缘可能有雾状残留，用 `alpha_floor` 清掉 |
| **没有 alpha 通道** | 不填（走 rembg） | 只能靠抠图 |

> ⚠️ **这是本项目踩过的最贵的坑**（D27）。当时把一张**本来就带 alpha 的
> PNG** 误判成"白底图"并重算，造成两个用户可见的破坏：
> ① 白裙子被打穿（裙子高光 245 距纯白 255 只差 10，被色键判成背景）
> ② 胳膊与腰之间的镂空被填满（那处"背景"不与画布外边界连通，
>    泛洪认为不是背景，强行填成不透明）
>
> **判据：源图有没有 alpha 决定怎么抠，不是"背景看起来是什么颜色"。**

### 1.2 立绘本身的要求

| 项 | 建议 |
|---|---|
| 姿态 | 正面或微侧面，**双眼与嘴清晰可见**（要标坐标） |
| 分辨率 | ≥ 500px 宽；越高坐标越好标。实测 `1792×2272` 很好标 |
| 背景 | 透明或纯色皆可，关键是**别把有 alpha 的图当白底图** |
| 全身/半身 | 都行，由 profile 的 `mode` 决定 |

---

## 2. 准备「闭眼素材」（想让角色眨眼就必须做）

**这是最容易漏的一步，而且漏了不会报错**——只是永远不眨眼。

```python
# 在 profile 里加一行：指向同一张立绘的"闭眼版本"
"src_closed": ROOT / "resources" / "source" / "<角色名>_closed.webp",
```

原理：`blink_by_blend()` 把闭眼素材按 alpha 混合到睁眼图上。
**没有 `src_closed` 就原样返回 = 不眨眼**，这是刻意的设计：

> **宁可不眨，也不贴假的。** 本项目 7 种"合成闭眼"的做法全部失败 ——
> 原因是整只眼只有 **13×11 = 143 px**（虹膜才 61 px），
> 在这个尺寸上任何合成痕迹都会被放大成"贴了块创可贴"。
> 所以眨眼**必须由画师出一张闭眼图**，没有就别眨。

`check` 会提示这一点。

---

## 3. 标定五官坐标（最需要耐心的一步）

### 3.1 生成带标尺的复核图

```bash
python tools/avatar_helper.py ruler <角色名> --scale 3 --step 25
# → resources/source/_<角色名>_ruler.png
```

图上是**原图坐标系**的网格（粗线每 125px，标着数值）。
用图片查看器打开，逐项读出坐标。

### 3.2 四个坐标的判据

| 坐标 | 含义 | 判据（实测踩过的坑） |
|---|---|---|
| `eye_l` / `eye_r` | 眼睛框 `(x0,y0,x1,y1)` | 必须包住**可见眼睛**：虹膜 + 眼白 + 上下睫毛 + 外眼角；<br>⚠️ **但不能带上眉毛** —— 带上会让闭眼补丁盖到眉毛，形成一道横带 |
| `mouth` | 嘴唇框 | **只包嘴唇本身**，不含人中与下巴。<br>取太松 → 张嘴时切线跑到鼻下；取太紧 → 闭嘴时嘴唇像素被裁 |
| `neck_y` | 头/身交界 y | 头部动画的旋转轴心。<br>太高 → 身体跟着头转；太低 → 头身之间裂缝 |

> **别指望程序自动定位。** 本项目试过用亮度/颜色连通域找眼睛，
> **被"虹膜高光 + 睫毛阴影"带偏**，连错两版（v1 整体偏低 6px ⇒ 眨眼看不见；
> v2 偏高 ⇒ 补丁盖到眉毛）。最终是靠**人眼看放大图 + 画框复核**定稿的。

### 3.3 标注完必须复核

```bash
python tools/avatar_helper.py preview <角色名> idle 1
# → resources/source/_<角色名>_check.png（帧放大 + 叠上五官框）
```

**打开这张图，用眼睛确认框是否框对了**。这是标定流程里不可省的一步 ——
坐标错了不会报错，只会让动画"看起来怪"。

---

## 4. 生成精灵帧

```bash
# 先别写盘，跑一遍看坐标/画布对不对
python tools/make_pet_sprites.py <角色名> --colors     # 只打印量出的肤色/线稿色

# 正式生成
python tools/make_pet_sprites.py <角色名>

# 抠图不满意时强制重抠（慎用，见 §1.1）
python tools/make_pet_sprites.py <角色名> --rebuild-base

# 只比对不写盘（回归验证）
python tools/make_pet_sprites.py <角色名> --verify
```

### 4.1 帧数约定（自动，不用手改）

| 动画 | 帧数 | 循环 |
|---|---|---|
| idle | 24 | ✅ |
| talk | 20 | ❌ |
| listen / think / happy / sad | 20 | ✅ |
| sleep | 24 | ✅ |
| stare | 16 | ✅ |
| dance | 24 | ✅ |
| **wander** | 20 | ✅（**不在 `ANIMS` 里**，是外部动画，见 §4.3） |

### 4.2 确定性验证（改了坐标后必做）

```bash
python tools/make_pet_sprites.py <角色名> --verify
# 期望：★ 回归比对：N/N 帧逐字节相同
```

**同 profile 同源图必须生成逐字节相同的结果。** 不是全同就说明
生成里有非确定性因素（随机种子、时间戳），要查。

### 4.3 ⚠️ `manifest.json` 是「加载清单」，不是「生成清单」

加载器 `AnimationController.load_pet` 是**按 `manifest["animations"]`
逐个加载**的：

```python
for anim_name, anim_info in self.manifest.get("animations", {}).items():
```

**不在 manifest 里 = 永远不加载**，哪怕帧目录里有 20 帧。

本项目实测踩到过：`xbya/wander/` 有 20 帧，但 manifest 里没有 `wander`
—— 那些帧**一直是死的**（用户看到的是"生成了却没生效"）。

现已修复：生成器会**保留 `ANIMS` 之外、但磁盘上确有帧的动画目录**。
生成时会打印：

```
保留 ANIMS 之外的动画（磁盘上已有帧）: wander
```

改动动画集合后，务必 `check` 一次确认一致性。

---

## 5. 切换配置

```yaml
ui:
  pet_sprite: <角色名>      # 只改这一行
  render_mode: sprite       # 保持 sprite
```

> ⚠️ **不要动 `vrm_model`** —— 那是 3D 模型路径，与 2D 精灵图无关。

> ⚠️ **改配置前先停掉正在运行的欣雅**。本项目有 D21 缺陷：
> `ConfigManager.set()` 会把**整份内存配置**落盘，
> 运行中的实例可能把 `config.yaml` 覆写回去（实测把已修好的音色改回了旧值）。

---

## 6. 验证

```bash
python tools/avatar_helper.py check <角色名>    # 一致性体检
python -m pytest tests/test_avatar_pipeline.py -q   # 换形象链路守护
python -m pytest tests/ -q                       # 全量回归
python run.py                                    # 真机看效果
```

真机检查项：

| 检查项 | 预期 |
|---|---|
| 角色显示 | 完整、不裁切、不偏移 |
| 窗口尺寸 | 与 `manifest.size` 一致（脚本会核） |
| 待机动画 | 自然呼吸/微动，无卡顿、无"死动画" |
| 眨眼 | 有闭眼素材才眨；没有则完全不眨（正常） |
| 说话嘴型 | 张嘴时牙齿**不跑到人中上** |
| 字幕位置 | 在角色下方，不遮挡 |

---

## 7. 常见问题速查

| 现象 | 原因 | 修法 |
|---|---|---|
| 角色被裁掉一截 | `bottom_margin` 太小 | 加大；全身角色尤其注意 |
| 头动时身体跟着动 | `neck_y` 太高 | 增大 `neck_y` |
| 头身之间有裂缝 | `neck_y` 太低 | 减小 `neck_y` |
| **永远不眨眼** | 没有 `src_closed` 素材 | 出闭眼立绘并注册（§2） |
| 眨眼像贴了创可贴 | 眼睛框带上了眉毛 | 收窄 `eye` 框（§3.2） |
| 张嘴时牙齿跑到人中 | 嘴框 `y0` 太小 | 增大 `mouth` 的 y0 |
| 全身角色嘴看不出动 | 嘴在画布上太小 | 加 `"mouth_gain": 1.5` |
| 动作太夸张/太轻 | `amp` 不合适 | 调 `amp`（0.5~2.0） |
| 某动画"生成了却没生效" | **不在 manifest 里** | 跑 `check`，见 §4.3 |
| 白裙子被打穿/镂空被填 | 对带 alpha 的图重抠了 | `keyer: "alpha"`，见 §1.1 |
| 角色不能再生成 | 没有 profile | 补 profile（`init` 给模板） |

---

## 8. 文件清单

| 文件 | 用途 |
|---|---|
| `tools/avatar_helper.py` | 辅助工具（体检/标尺/复核/模板） |
| `tools/make_pet_sprites.py` | profile 定义 + 帧生成 |
| `resources/source/<角色名>_source.*` | 立绘仓库内副本（**必须提交**） |
| `resources/source/<角色名>_closed.*` | 闭眼素材（想要眨眼就要） |
| `resources/sprites/<角色名>/` | 生成产物（帧 + manifest） |
| `config.yaml` | `ui.pet_sprite` 切换 |

**要提交的**：profile 改动、`resources/source/` 下的图、`resources/sprites/<角色名>/`。
`resources/source/_*.png`（标尺图/复核图）是工作产物，可不提交。

---

## 9. v1.0 勘误（这三处当时写错了）

改 skill 时对着代码逐条核过，v1.0 有三处与实现不符：

1. **`LOCAL_SRC` 的实际映射写错了**
   v1.0 写的是 `"xbya": .../avatar_source.webp`，
   实际是 `"xbya": .../avatar_new.webp`（`avatar_source.webp` 属于 `xinya2`）。
   而且**源图文件名不一定等于角色名** ——
   所以 `avatar_helper.py` 一律解析 `LOCAL_SRC`，不按名字猜。

2. **漏了 `wander` 动画**
   v1.0 的帧数表只有 9 个动画，实际 `manifest` 里有 10 个。
   `wander` 不在生成器的 `ANIMS` 里，是外部动画。

3. **`xinya` 没有 profile，但 v1.0 暗示可以重新生成**
   它只有帧目录。`list` / `check` 现在会明确标出这一点。

---

## 10. 留下的判据（可复用）

1. **有现成的 alpha 就用它** —— 抠图方式由"源图有没有 alpha"决定，
   不是由"背景看起来是什么颜色"决定；
2. **`manifest` 是加载清单** —— 不在清单里的资源等于不存在，
   生成成功 ≠ 已生效；
3. **坐标不能靠程序猜** —— 高光与阴影会把连通域判据带偏，
   必须人眼看放大图 + 画框复核；
4. **测试要钉行为，不要只钉存在** —— 我在做这条 skill 时，
   第一版用例只断言"源码里有某段文字"，把功能整段删掉**照样全绿**；
   改成"真跑一次生成器，看输出里那个动画还在不在"才真正有效；
5. **改配置前先停应用** —— 运行中的实例会覆写 `config.yaml`（D21）。
