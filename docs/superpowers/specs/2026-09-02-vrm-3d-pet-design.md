# 3D 互动桌宠（VRM）设计文档

> 日期：2026-09-02
> 状态：已批准（用户确认）
> 前置：语音交互全链路已通（ASR→LLM→TTS），本设计仅替换渲染层与增加互动

## Goal

将"序列帧精灵图"渲染替换为 **VRM 3D 模型**渲染，并实现进阶互动（部位触摸反应、心情系统、自动行为），保留全部现有语音交互能力。

## 架构决策（已与用户确认）

| 决策 | 选择 | 理由 |
|---|---|---|
| 模型格式 | VRM（VRoid Studio/VRoid Hub 免费生态） | 标准3D虚拟形象格式，支持骨骼/表情/口型，资源免费 |
| 渲染方案 | QtWebEngine + three.js + @pixiv/three-vrm | 生态最成熟，实现快；接受 exe 体积增至 ~200MB |
| 互动深度 | 进阶互动（部位触碰+心情系统+自动行为） | 用户明确要求 |
| 降级策略 | WebEngine/模型失败 → 回退现有精灵图 | 风险可控，原功能零损失 |

## 整体架构

```
当前:  PySide6 QWidget + QPainter 绘制序列帧长图
改后:  PySide6 QWidget(透明置顶)
        └─ QWebEngineView(透明)
             └─ three.js + three-vrm 渲染 VRM 模型
Python ◄──QWebChannel桥接──► JavaScript
```

- 窗口/拖拽/置顶/气泡逻辑不变，**只替换渲染层**
- 语音链路完全保留（ASR→LLM→TTS + 口型同步）

## 组件

### Web 层（打包在 assets/vrm/）
| 文件 | 职责 |
|---|---|
| `assets/vrm/viewer.html` | three.js 场景骨架：渲染循环、透明背景、灯光 |
| `assets/vrm/js/app.js` | VRM 加载(three-vrm)、动画状态机、程序化动作（眨眼/呼吸/摇尾/点头）、表情(morph)管理 |
| `assets/vrm/js/bridge.js` | QWebChannel 桥：注册 Python→JS 指令处理器，上报 JS→Python 事件 |
| `assets/vrm/cat.vrm` | 模型文件（demo 用 VRoid Hub 免费猫系角色，用户可替换） |

### Python 层
| 文件 | 职责 |
|---|---|
| `ui/vrm_bridge.py`（新） | 桥接器：初始化 QWebChannel、Python↔JS 双向消息 |
| `ui/pet_window.py`（改） | 渲染层替换：inner QWebEngineView 替代 paintEvent 精灵绘制；互动事件接入 |
| `animation/vrm_state_map.py`（新） | 旧状态名→VRM 动画/表情映射（idle/talk/think/listen/sleep/happy/sad） |
| `config.yaml`（改） | `ui.render_mode: vrm|sprite`，模型路径 `ui.vrm_model: assets/vrm/cat.vrm` |

## 数据流

### Python → JS（指令）
```
PetStateMachine 状态变化 → vrm_bridge.send("set_state", state)
语音管线 speak 开始/结束 → send("lip_sync", true/false)
心情变化 → send("set_emotion", "happy|normal|bored|lonely")
互动触发 → send("play_action", "hello|pet|feed|hit_tail")
```

### JS → Python（事件）
```
JS bridge.on("body_part_clicked", part) → pet_window._on_body_click(part)
JS bridge.on("ready") → 桥接器标记就绪，完成状态同步
JS bridge.on("error", msg) → 触发降级回退
```

## 互动设计（P2 范围）

### 鼠标互动
- 点击头部/身体/尾巴 → 不同反应（蹭手/抖毛/甩尾+喵叫）
- 拖拽移动（现有逻辑保留，3D 模型跟随窗口）
- 双击 → 摸头特效（爱心飘出）

### 心情系统
- 心情值：开心/平常/无聊/孤单
- 互动增加心情值；长时间不互动漂移下降
- 心情驱动表情(morph)与自动行为

### 自动行为（心情+时间驱动）
- 打盹(有声提示) / 伸懒腰 / 好奇张望 / 空中爪扑
- 与语音状态无缝切换（说话→口型、思考→歪头、倾听→耳朵竖起）

### 语音联动
- TTS 播放时口型同步（lip sync）
- 监听时耳朵竖起+惊讶表情

## 错误处理与降级

| 场景 | 处理 |
|---|---|
| WebEngine 初始化失败 | 回退 sprite 模式，日志记录 |
| VRM 模型缺失/加载失败 | 回退 sprite 模式 |
| JS 桥未就绪时收到指令 | 缓存 state 指令，ready 后一次性同步 |
| 打包环境缺 QtWebEngineProcess | 打包时验证；运行时检测降级 |

## 测试策略

- `tests/test_vrm_bridge.py`（新）：桥接消息序列化/状态映射/未就绪缓存
- `tests/test_vrm_state_map.py`（新）：状态名→动画映射表完整性（7状态全覆盖）
- 降级回退测试：无模型/WebEngine 异常 → sprite 模式路径
- 回归：现有 215 测试不回归（仅剩 2 个预存失败）

## 实施阶段

| 阶段 | 内容 | 验收 |
|---|---|---|
| **P0 渲染替换** | Web 视图嵌入+VRM 显示+透明窗口+状态映射（idle/talk/think/listen/sleep/happy/sad） | 真 3D 猫在桌面活动，状态随语音切换 |
| **P1 基础互动** | 点击反应、拖拽、口型同步、喂食/聊天动作 | 可摸可说话，口型与 TTS 同步 |
| **P2 进阶互动** | 部位触摸、心情系统、自动行为 | 完整情感化互动 |

每阶段独立验收 + 独立提交；P0 完成后重打包 exe 供实际体验，再决定是否继续 P1/P2。

## 风险与缓解

| 风险 | 缓解 |
|---|---|
| QtWebEngine 透明窗口在部分 Windows 黑底 | 用标准 hack（transparent viewport + 页面透明背景 CSS + Windows 10+ 已验证组合）；失败则降级 sprite |
| 首次启动加载慢 1-2s | WebEngine 初始化放后台线程预热；显示"加载中"精灵占位 |
| three-vrm 版本兼容 | 锁定已知兼容版本（three@0.16x + three-vrm@2.x），本地打包 js 不依赖 CDN |
| MX250 渲染性能 | VRM 面数控制（<50k）、抗锯齿关闭、像素比 cap 1.0；low 模式 fps 上限 15 保持 |
