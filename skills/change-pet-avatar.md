# 更换桌宠形象 SOP（内部 Skill）

> 版本: 1.0 | 最后更新: 2026-09-12
> 适用项目: 欣雅桌面智能管家 (xbya-vrm-worktree)

---

## 一、流程总览

```
┌──────────────┐    ┌──────────────┐    ┌──────────────┐    ┌──────────────┐    ┌──────────────┐
│  1. 准备立绘  │ →  │  2. 创建Profile │ →  │  3. 生成精灵帧 │ →  │  4. 更新配置  │ →  │  5. 验证测试  │
│  (图片素材)   │    │  (坐标标注)    │    │  (运行脚本)   │    │  (config.yaml)│    │  (启动应用)   │
└──────────────┘    └──────────────┘    └──────────────┘    └──────────────┘    └──────────────┘
     ~5 min             ~15 min             ~2 min              ~1 min              ~3 min
```

**总耗时预估**: 25~30 分钟（首次）/ 10~15 分钟（复用已有 profile 微调）

---

## 二、详细步骤

### 步骤 1：准备立绘图片

#### 1.1 图片要求

| 要求 | 说明 |
|------|------|
| **格式** | PNG / WEBP / JPG 均可（推荐 PNG，透明背景最佳） |
| **背景** | 纯色背景或透明背景（rembg 会自动抠图，纯色更快更准） |
| **姿态** | 正面或微侧面，五官清晰可见 |
| **分辨率** | 建议 ≥ 500px 宽（越高五官标注越精确） |
| **内容** | 全身或半身均可，但需在 profile 中指定 `mode` |

#### 1.2 存放位置

```
resources/source/<角色名>_source.webp    # 仓库内副本（推荐，持久化）
```

> ⚠️ **必须在仓库内留副本**！`.dsh/attachments/` 是会话级缓存，会话一清就没了。

#### 1.3 抠图预检（可选但推荐）

```bash
# 安装 rembg（如果还没装）
pip install rembg

# 快速测试抠图效果
python -c "
from rembg import remove
from PIL import Image
img = Image.open('resources/source/你的图片.webp')
out = remove(img)
out.save('resources/source/你的图片_cutout.png')
print('抠图完成，检查 resources/source/你的图片_cutout.png')
"
```

---

### 步骤 2：创建 Profile（最复杂的步骤）

Profile 定义了立绘到精灵帧的映射关系，核心是**五官坐标标注**。

#### 2.1 在 `tools/make_pet_sprites.py` 中添加 Profile

找到 `PROFILES` 字典（约第 66 行），添加新条目：

```python
PROFILES: dict[str, dict] = {
    # ... 已有 profile ...

    # ★ 新角色 profile
    "你的角色名": {
        "src": r"C:\path\to\source\image.webp",  # 或用 LOCAL_SRC 引用仓库内副本
        "mode": "full",              # "full"=全身 | "crop"=裁切到半身
        "canvas_w": 128,             # 画布宽度（固定 128）
        "bottom_margin": 8,          # 底部留白（全身角色防脚被裁）
        "lm_space": "src",           # 坐标空间："src"=原图坐标 | "canvas"=画布坐标
        "lm_src": {                  # 五官坐标（原图坐标系）
            "eye_l": (x0, y0, x1, y1),    # 左眼框（含睫毛）
            "eye_r": (x0, y0, x1, y1),    # 右眼框（含睫毛）
            "mouth": (x0, y0, x1, y1),    # 嘴唇框（只包嘴唇）
            "neck_y": 317,                  # 颈部 y 坐标（头部动画的旋转轴）
        },
        "skin": (238, 205, 196),     # 肤色 RGB（用于闭眼填充）
        "line": (40, 30, 34),        # 线稿色 RGB（用于闭眼弧线）
        "amp": 1.0,                  # 动作幅度系数（1.0=标准，<1=轻柔，>1=夸张）
        "mouth_gain": 1.2,           # 嘴部开口放大倍数（全身角色嘴小时需要）
    },
}
```

同时在 `LOCAL_SRC` 字典中注册仓库内副本路径：

```python
LOCAL_SRC = {
    "xbya": ROOT / "resources" / "source" / "avatar_source.webp",
    "你的角色名": ROOT / "resources" / "source" / "你的角色名_source.webp",
}
```

#### 2.2 如何标注五官坐标（关键！）

这是最需要耐心的步骤。坐标必须在**原图**坐标系中测量。

**推荐方法：放大网格法**

1. 用 Python 生成带网格的放大图：

```python
from PIL import Image, ImageDraw, ImageFont
img = Image.open("resources/source/你的图片.webp")
# 放大 2 倍便于标注
w, h = img.size
big = img.resize((w * 2, h * 2), Image.LANCZOS)
draw = ImageDraw.Draw(big)
# 画 50px 间隔网格
for x in range(0, w * 2, 100):
    draw.line([(x, 0), (x, h * 2)], fill="red", width=1)
    draw.text((x + 2, 2), str(x // 2), fill="red")
for y in range(0, h * 2, 100):
    draw.line([(0, y), (w * 2, y)], fill="red", width=1)
    draw.text((2, y + 2), str(y // 2), fill="red")
big.save("resources/source/你的角色名_grid.png")
print("打开 resources/source/你的角色名_grid.png，用网格定位五官坐标")
```

2. 用图片查看器打开网格图，逐个读出坐标：

| 坐标 | 定义 | 判据 |
|------|------|------|
| `eye_l` | 左眼框 `(x0, y0, x1, y1)` | 盖住整个可见眼睛，含外眼角与睫毛 |
| `eye_r` | 右眼框 `(x0, y0, x1, y1)` | 同上 |
| `mouth` | 嘴唇框 `(x0, y0, x1, y1)` | **只包嘴唇本身**，不包含人中和下巴 |
| `neck_y` | 颈部 y 坐标（标量） | 头与身体的交界线，头部动画的旋转轴心 |

> ⚠️ **常见坑**：
> - 嘴框取太松 → `speak()` 的切线跑到鼻下，张嘴时牙齿被顶到人中
> - 嘴框取太紧 → 闭嘴时嘴唇像素被裁掉
> - `neck_y` 太高 → 头动时身体跟着动；太低 → 头和身体之间出现缝隙

#### 2.3 验证 Profile

```bash
# 只打印量出来的颜色，不生成帧（验证肤色/线稿色是否合理）
python tools/make_pet_sprites.py 你的角色名 --colors

# 生成底图但不生成帧（验证抠图和缩放效果）
python -c "
from tools.make_pet_sprites import *
p = PROFILES['你的角色名']
p['_src'] = resolve_src(p, '你的角色名')
from pathlib import Path
build_base(p, Path('resources/source/_base_你的角色名.png'))
print('底图已生成，检查 resources/source/_base_你的角色名.png')
"
```

---

### 步骤 3：生成精灵帧

```bash
# 生成所有动画帧（idle/talk/listen/think/happy/sad/sleep/stare/dance）
python tools/make_pet_sprites.py 你的角色名

# 如果抠图效果不满意，加 --rebuild-base 强制重新抠图
python tools/make_pet_sprites.py 你的角色名 --rebuild-base

# 只比对不写盘（回归验证用）
python tools/make_pet_sprites.py 你的角色名 --verify
```

**生成产物**：

```
resources/sprites/你的角色名/
├── manifest.json          # 动画清单（size + animations）
├── idle/                  # 待机动画（24帧）
│   ├── frame_001.png
│   ├── frame_002.png
│   └── ...
├── talk/                  # 说话动画（20帧）
├── listen/                # 聆听动画（20帧）
├── think/                 # 思考动画（20帧）
├── happy/                 # 开心动画（20帧）
├── sad/                   # 难过动画（20帧）
├── sleep/                 # 睡觉动画（24帧）
├── stare/                 # 发呆动画（16帧）
└── dance/                 # 跳舞动画（24帧）
```

**帧数固定**（由 `ANIMS` 字典定义）：

| 动画 | 帧数 | 循环 |
|------|------|------|
| idle | 24 | ✅ |
| talk | 20 | ❌ |
| listen | 20 | ✅ |
| think | 20 | ✅ |
| happy | 20 | ✅ |
| sad | 20 | ✅ |
| sleep | 24 | ✅ |
| stare | 16 | ✅ |
| dance | 24 | ✅ |

---

### 步骤 4：更新配置

编辑 `config.yaml`，修改 `ui` 段：

```yaml
ui:
  pet_sprite: 你的角色名      # 改成新角色目录名
  pet_size: 127               # 窗口边长（px），127≈1:1 不缩放
  render_mode: sprite         # 保持 sprite 模式
```

> ⚠️ **不要改 `vrm_model`**！那是 3D 模型路径，与 2D 精灵图无关。

---

### 步骤 5：验证测试

#### 5.1 启动应用验证

```bash
python run.py
```

**检查项**：

| 检查项 | 预期结果 |
|--------|---------|
| 宠物显示 | 角色正确显示，无裁切/错位 |
| 窗口大小 | 与 manifest.json 的 size 一致（考虑 pet_size 缩放） |
| 待机动画 | 自然呼吸/微动，无卡顿 |
| 点击互动 | 点击后切换到 happy/sad 等状态 |
| 说话动画 | TTS 播报时嘴型同步 |
| 字幕位置 | 字幕在角色下方，不遮挡角色 |

#### 5.2 回归测试

```bash
# 跑全量测试（确保没有破坏现有功能）
python -m pytest tests/ -v --tb=short

# 关键测试文件
python -m pytest tests/test_animation.py -v      # 动画控制器
python -m pytest tests/test_pet_window.py -v     # 宠物窗口
python -m pytest tests/test_vrm_state_map.py -v  # 状态映射
```

---

## 三、Profile 调优指南

### 3.1 常见问题与修复

| 问题 | 原因 | 修复 |
|------|------|------|
| 头动时身体跟着动 | `neck_y` 太高 | 增大 `neck_y` 值 |
| 头和身体之间有缝隙 | `neck_y` 太低 | 减小 `neck_y` 值 |
| 闭眼时像贴了创可贴 | 肤色取错 | 调整 `skin` 为鼻梁处肤色 |
| 张嘴时牙齿跑到鼻子上 | 嘴框 `y0` 太小 | 增大 `mouth` 的 y0 |
| 全身角色嘴太小看不见 | 缺少 `mouth_gain` | 添加 `"mouth_gain": 1.5` |
| 动作太夸张/太轻柔 | 幅度系数不合适 | 调整 `amp`（0.5~2.0） |
| 抠图边缘有白色残影 | rembg alpha 不纯 | 脚本已自动处理（色距补正） |

### 3.2 从已有 Profile 复制修改

最快的上手方式是复制最接近的已有 profile：

```python
# 如果新角色与 xbya（全身）类似
"new_char": {**PROFILES["xbya"], "src": r"path\to\new.webp", "lm_src": {...}}

# 如果新角色与 xinya2（半身）类似
"new_char": {**PROFILES["xinya2"], "src": r"path\to\new.webp", "lm": {...}}
```

---

## 四、文件清单

| 文件 | 用途 | 改动 |
|------|------|------|
| `tools/make_pet_sprites.py` | Profile 定义 + 帧生成脚本 | 添加新 profile |
| `resources/source/<角色名>_source.webp` | 立绘原图（仓库内副本） | 新增 |
| `resources/sprites/<角色名>/` | 生成的精灵帧目录 | 新增（脚本生成） |
| `config.yaml` | 应用配置 | 改 `ui.pet_sprite` |

---

## 五、注意事项

1. **坐标精度**：五官坐标直接影响动画质量，建议用放大网格法仔细标注
2. **肤色/线稿色**：用 `--colors` 命令先量出来，不要凭感觉猜
3. **帧数固定**：不要修改 `ANIMS` 字典，动画控制器依赖固定帧数
4. **画布宽度固定 128**：高度按角色宽高比自动计算，不要手动改
5. **备份**：修改 profile 前先备份 `make_pet_sprites.py`，方便回退
6. **版本控制**：新角色的 profile 和 source 图片都要 `git add`

---

## 六、快速参考卡片

```bash
# === 完整流程（一行命令） ===
# 1. 准备图片 → resources/source/xxx_source.webp
# 2. 添加 profile → tools/make_pet_sprites.py 的 PROFILES 字典
# 3. 生成帧 → python tools/make_pet_sprites.py xxx
# 4. 改配置 → config.yaml 的 ui.pet_sprite: xxx
# 5. 启动 → python run.py

# === 常用命令 ===
python tools/make_pet_sprites.py xxx              # 生成帧
python tools/make_pet_sprites.py xxx --rebuild-base  # 重新抠图
python tools/make_pet_sprites.py xxx --colors     # 量颜色
python tools/make_pet_sprites.py xxx --verify     # 比对帧
python run.py                                      # 启动应用
python -m pytest tests/test_animation.py -v       # 跑动画测试
```
