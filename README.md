# 欣雅 — 桌面 AI 智能管家 v2.0

> 完全本地化、全语音交互、插件化架构的 AI 桌面助手。  
> 核心理念：**语音层负责交互，Agent 层负责执行**——像钢铁侠的 JARVIS 一样工作。

---

## 目录

- [系统概览](#系统概览)
- [整体架构](#整体架构)
  - [分层架构](#分层架构)
  - [数据流](#数据流)
- [Voice Layer — 交互层](#voice-layer--交互层)
  - [语音管线](#语音管线)
  - [情绪动效系统](#情绪动效系统)
  - [打断机制](#打断机制)
  - [VRM 3D 渲染](#vrm-3d-渲染)
- [Agent Layer — 执行层](#agent-layer--执行层)
  - [架构设计](#agent-架构设计)
  - [意图路由器](#意图路由器)
  - [工具注册表](#工具注册表)
  - [安全守卫](#安全守卫)
  - [消息协议](#消息协议)
- [插件系统](#插件系统)
- [动画系统](#动画系统)
- [性能适配](#性能适配)
- [快速开始](#快速开始)
- [项目结构](#项目结构)
- [开发指南](#开发指南)
- [路线图](#路线图)

---

## 系统概览

欣雅是一个运行在用户桌面上的 AI 助手，由一个可爱的 3D/2D 宠物形象承载。用户通过语音与她对话，她能理解指令、操作电脑、管理文件、查询信息，并用语音+动画+气泡实时反馈。

**核心能力矩阵：**

| 能力 | 状态 | 说明 |
|------|------|------|
| 语音交互闭环 | ✅ 已完成 | ASR → LLM → TTS，含唤醒词/打断/回声消除 |
| 情绪动效匹配 | ✅ 已完成 | 8种情绪检测，逐句匹配动效 |
| VRM 3D 渲染 | ✅ 已完成 | QWebEngineView 嵌入，3层合成 |
| 2D 精灵渲染 | ✅ 已完成 | 程序化帧生成，17种状态动画 |
| 插件化架构 | ✅ 已完成 | 8种插件类型，热加载 |
| 声纹识别 | ✅ 已完成 | resemblyzer，防自言自语 |
| **插件内核** | ✅ 已完成 | Context/Service/Registry/EventBus，覆盖率 100% |
| **意图路由** | ✅ 已完成 | 规则优先混合路由，73 条指令集准确率 100%，P95 0.25ms |
| **安全守卫** | ✅ 已完成 | 三级风险 + 路径白名单 + 审计日志 |
| **并行管线** | ✅ 已完成 | 即时确认语 ∥ 工具执行，感知延迟 ≈0ms |
| **文件操作工具** | ✅ 已完成 | 搜索/列出/读取/重命名/移动/删除（回收站） |
| **LLM 路由兜底** | ✅ 已完成 | rule → llm → chat 三级降级，置信度不足才转 LLM |
| **实体追踪（指代消解）** | ✅ 已完成 | 支持"打开第一个"、"把那些删了"、"它" |
| **系统操作工具** | ✅ 已完成 | 打开应用/查状态/剪贴板/截图/执行命令 |
| **生产力工具** | ✅ 已完成 | 计算/**翻译**/**提醒**/**天气** —— 三个外部能力在 F7 收尾时接上了**真后端**（此前只有工具壳，见下） |
| **浏览器工具** | ✅ 已完成 | 打开/搜索/读正文（stdlib，SSRF 防护） |
| **配置驱动插件加载** | ✅ 已完成 | 13 个插件组装 Agent 层，清单在 `config.yaml`，可热插拔 |
| **多步任务规划** | ✅ 已完成（P3） | planner seam + 规则/LLM/混合三档；断点续跑、逐步安全校验 |
| **长期记忆** | ✅ 已完成（P3） | SQLite + FTS5 中文检索 + 向量 RRF 融合；离线可用，敏感信息拒绝入库 |

> ⚠️ **关于"生产力工具"这一行的历史**：上表在 F7 收尾之前就写了"✅ 已完成"，
> 但那时 `translate` / `weather` 的注入点（`SVC_TRANSLATE` / `SVC_WEATHER`）
> **全项目没有任何 Provider**，`reminder` 的 `due_now()` **也没有任何调用者** ——
> 也就是说三个工具分别只会回答"我这边还没接上翻译能力呢"、
> "还没接上天气服务呢"，以及**承诺了却永远不会响**。
> 现在它们接的是真后端（项目 LLM / Open-Meteo / 调度线程），
> 逐项真实验证见 `AGENTS.md` §17 与 `tools/verify_f7_real_services.py`。
> 这段注释刻意保留：**"✅ 已完成"曾经是错的，而单元测试全绿**。

---

## 整体架构

### 分层架构

系统采用 **三层解耦架构**，通过事件总线和消息总线通信：

```
┌─────────────────────────────────────────────────────────────────────┐
│                         用户 (User)                                 │
└──────────────────────────┬──────────────────────────────────────────┘
                           │ 语音输入 / 鼠标操作
┌──────────────────────────▼──────────────────────────────────────────┐
│                    VOICE LAYER (交互层)                              │
│                                                                     │
│  ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────────┐  │
│  │   ASR    │──▶│ Intent   │──▶│ 情绪分析  │──▶│  Pet UI     │  │
│  │ 语音识别  │   │  NLU路由  │   │ Emotion  │   │ 气泡+动画+VRM│  │
│  └──────────┘   └────┬─────┘   └──────────┘   └──────▲───────┘  │
│                       │                                │           │
│                       │ AgentCommand                   │ AgentResult│
│                  ┌────▼────────────────────────────────┴─────┐    │
│                  │          MessageBus (消息总线)              │    │
│                  └────┬────────────────────────────────┬─────┘    │
│                       │                                │           │
│  ┌──────────┐   ┌────▼─────┐   ┌──────────┐   ┌─────▼───────┐  │
│  │   TTS    │◀──│  Safety  │◀──│  Planner │◀──│  Executor   │  │
│  │ 语音合成  │   │  安全守卫  │   │ 任务规划  │   │  工具执行    │  │
│  └──────────┘   └──────────┘   └──────────┘   └─────────────┘  │
│                                                                     │
│                    AGENT LAYER (执行层)                             │
│                                                                     │
│  ┌────────────────────── Tool Registry ─────────────────────────┐  │
│  │  📁 文件工具  │  💻 系统工具  │  🌐 浏览器工具  │  🛠️ 生产力   │  │
│  └──────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────────────┐
│                    PLUGIN LAYER (插件层)                             │
│  ASR │ TTS │ LLM │ Embedding │ VectorDB │ Voiceprint │ Monitor    │
└─────────────────────────────────────────────────────────────────────┘
```

**设计原则：**
- **Voice Layer** 只负责"听"和"说"，不知道 Agent 怎么执行
- **Agent Layer** 只负责"想"和"做"，不知道结果怎么展示
- **Plugin Layer** 提供底层能力（ASR/TTS/LLM），可独立替换
- 三层只通过 MessageBus 和 EventBus 通信

### 数据流

一次完整的语音交互：

```
用户: "帮我找桌面上的合同文件"
  │
  ▼ [Voice Layer]
ASR 识别 → "帮我找桌面上的合同文件"
  │
  ▼ [IntentRouter 本地NLU, <10ms]
AgentCommand {
  action: "file_search",
  params: {dir: "Desktop", pattern: "*合同*"},
  confidence: 0.95
}
  │
  ▼ [MessageBus]
  │
  ▼ [Agent Layer]
ToolRegistry.execute("file_search", params)
  → SearchFilesTool → ["合同2024.pdf", "合同草案.docx"]
  │
SafetyGuard: risk=low → 自动执行
  │
  ▼ [MessageBus]
AgentResult {
  status: "success",
  summary: "找到2个合同文件：合同2024.pdf、合同草案.docx",
  emotion: "happy"
}
  │
  ▼ [Voice Layer]
Pet动效: happy（爱心飘出）
气泡: "找到2个合同文件：合同2024.pdf、合同草案.docx"
TTS: "找到两个合同文件啦~"
```

---

## Voice Layer — 交互层

### 语音管线

完整的语音交互链路，包含多层保护和优化：

```
麦克风录音
  │
  ▼
VAD 静音检测 (silero-vad)
  │
  ▼
ASR 语音识别 (faster-whisper, base模型, INT8量化)
  │
  ├── 无效过滤 → "环境噪音" / "太短" → 静默忽略
  ├── 回声检测 → 宠物自己声音 → 丢弃
  └── 声纹验证 → 非注册用户 → 降级模式
  │
  ▼
情绪分析 (emotion_analyzer, 本地规则, <0.1ms)
  │
  ├── happy  → 开心眼+笑口+爱心
  ├── sad    → 垂眼+泪滴
  ├── angry  → 皱眉+颤抖+怒气符号
  ├── surprise → 大眼+弹跳+感叹号
  ├── love   → 开心眼+爱心连发
  ├── dance  → 摇摆+音符
  ├── think  → 半闭眼+问号
  ├── calm   → 深呼吸+放松
  └── talk   → 普通说话嘴型
  │
  ▼
意图路由 (IntentRouter)
  │
  ├── 高置信度(>0.5) → AgentCommand → Agent层执行
  └── 低置信度(<0.5) → LLM闲聊 → 直接语音回复
  │
  ▼
Agent 执行结果
  │
  ▼
TTS 语音合成 (Edge-TTS, zh-CN-XiaoxiaoNeural)
  │
  ├── [silent] 标记 → 只气泡，不出声（嗯/哦/好的）
  ├── 语气词附和 → 只气泡
  └── 正常回复 → 语音播报
  │
  ▼
播放 + 动效 + 字幕
  │
  ├── 逐句播放：每句同步更新字幕
  ├── 逐句动效：每句切换对应情绪动画
  ├── 播放后冷却：3秒回声屏蔽
  └── 用户打断 → 立即停止 + 切换动画
```

### 情绪动效系统

**语义情绪分析** (`services/emotion_analyzer.py`)：

基于关键词+正则+表情符号的三层匹配引擎，纯本地运行，延迟 < 0.1ms。

| 情绪 | 关键词示例 | 表情符号 | 动效 |
|------|-----------|---------|------|
| happy | 开心、恭喜、太好了、哈哈 | 😊😄🎉 | 开心眼+笑口+爱心飘出 |
| sad | 难过、伤心、遗憾、对不起 | 😢😭💔 | 垂眼+泪滴特效 |
| angry | 生气、讨厌、烦、气死 | 😤😠💢 | 皱眉+颤抖+怒气符号 |
| surprise | 天哪、不会吧、真的吗 | 😲🤯😱 | 大眼弹起+感叹号 |
| love | 喜欢、爱你、宝贝、mua | ❤️💕💖 | 开心眼+爱心连发 |
| dance | 跳舞、嗨起来、蹦迪 | 🎶🎵 | 左右摇摆+音符 |
| think | 让我想想、嗯...、分析 | 🤔💭 | 半闭眼+问号 |
| calm | 没关系、放松、别担心 | 😌🍃 | 深呼吸+温柔光晕 |
| talk | (默认) | — | 普通说话嘴型 |

**逐句情绪切换：** 播放多句回复时，每句话独立匹配情绪并切换动效。

```
LLM回复: ["太好了！", "不过让我想想...", "最后给你个惊喜！"]
播放动效: happy → think → surprise
```

**情绪优先级（并列时）：** love > angry > sad > happy > surprise > dance > think > calm

### 打断机制

三种打断方式，覆盖不同使用场景：

| 方式 | 触发条件 | 适用场景 |
|------|---------|---------|
| 热键打断 | `Ctrl+Alt+D` 全局热键 | 正在播放时想说话 |
| 点击打断 | 快速点击宠物（<0.4秒） | 不想找快捷键 |
| 语音打断 | 说了唤醒词 `嘿欣雅` | 免手操作 |

**打断流程：**
1. 检测到打断信号 → 设置 `_interrupt_requested = True`
2. 停止当前 TTS 播放（pygame.mixer.stop）
3. 清空剩余句子队列
4. 重置处理状态
5. 显示打断反馈气泡（2秒保护期）
6. Pet 切换到 angry/surprise 动效
7. 重新进入监听状态

### VRM 3D 渲染

基于 QWebEngineView 的 VRM 模型渲染：

```
┌─────────────────────────┐
│     QWebEngineView      │
│  ┌───────────────────┐  │
│  │  Three.js + VRM   │  │
│  │  ┌─────────────┐  │  │
│  │  │  VRM Model  │  │  │
│  │  │  (骨骼动画)  │  │  │
│  │  └─────────────┘  │  │
│  │  7状态: idle/talk/ │  │
│  │  think/listen/sleep│  │
│  │  /happy/sad       │  │
│  └───────────────────┘  │
│                         │
│  ← 拖拽位移上报          │
│  ← 部位点击上报(head/    │
│    body/tail/ear)        │
└─────────────────────────┘
```

**Python → JS 状态映射** (`animation/vrm_state_map.py`)：

| Python 状态 | VRM 动画 | VRM 表情 |
|------------|---------|---------|
| idle | idle | neutral |
| talk | talk | neutral (lipSync驱动) |
| think | think | neutral |
| listen | listen | neutral |
| sleep | sleep | relaxed |
| happy | happy | happy |
| sad | sad | sad |
| angry | idle (回退) | angry |
| surprise | idle (回退) | surprised |
| love | happy (复用) | happy |
| calm_down | idle (回退) | relaxed |

---

## Agent Layer — 执行层

### Agent 架构设计

Agent 层是系统的"大脑"，负责理解用户意图、执行操作、返回结果。
交互层与执行层**只通过事件总线通信**，互不感知对方实现。

```
语音文本 / 文本输入
  │
  ▼
┌──────────────────────────────────────────────────────────┐
│  AgentPipeline（并行管线协调器）                             │
│                                                            │
│   ┌─ 路由：HybridRouter ──────────────────────────┐         │
│   │  rule（本地关键词+正则，<10ms，覆盖 100%）      │         │
│   │    ↓ 置信度 < 阈值(0.5)                        │         │
│   │  llm（function calling，15 个工具 schema）     │         │
│   │    ↓ 失败/无 API                               │         │
│   │  chat（交回 Voice Layer 走原 LLM 链路）        │         │
│   └────────────────────────────────────────────────┘         │
│                         │                                   │
│   ┌─────────────────────┴─────────────────────┐             │
│   │ 并行发射（不等待下游）                      │             │
│   ▼                                           ▼             │
│  feedback.ack                            task.submitted     │
│  （确认语缓存命中 ≈0ms）                    （线程池执行）     │
│                                               │             │
│   ┌───────────────────────────────────────────┘             │
│   ▼                                                         │
│  SafetyGuard（白名单 → 操作预览 → 风险确认 → 审计）           │
│                                               │             │
│   ┌───────────────────────────────────────────┘             │
│   ▼                                                         │
│  ToolRegistry.execute(action, params) → ToolResult          │
│                                               │             │
│   ┌───────────────────────────────────────────┘             │
│   ▼                                                         │
│  EntityTracker（写入实体栈，供"第一个"/"那些"指代）           │
│   ▼                                                         │
│  HybridSummarizer（模板 / LLM 润色）→ feedback.result         │
└──────────────────────────────────────────────────────────┘
  │
  ▼
AgentResult（Qt 信号回传 UI 线程）
```

**指代消解（P1）**：`agent/tracker.py` 记录最近产生的文件与动作（FIFO，20 文件 / 10 动作），
支持序数（"第 2 个"、"最后一个"、"首个"）、集合（"那些"、"全部"）与单数（"它"、"这个"）指代。

### 意图路由器

规则优先的混合路由。规则层是本地轻量 NLU，不调用 LLM，延迟 < 10ms：

```python
IntentDef(
    action="system_info",
    description="查询系统状态（电量/内存/CPU/磁盘）",
    category="system",
    base_confidence=0.65,
    keywords=[("内存", 2.5), ("cpu", 2.5), ("电量", 2.5),
              ("电脑状态", 3.0), ("电脑怎么样", 3.0)],   # 加权关键词
)
```

**路由逻辑：**
1. 空文本 → `chat`
2. 存在待确认项时优先识别确认/取消短语
3. 未实现能力提示词 → `chat`
4. 含 URL → 按动词偏好直接分流 `web_open` / `web_read`
5. 加权关键词评分 → 取最高分意图
6. 正则提取参数（失败不影响路由，降级为空参数）
7. 置信度 = `base_confidence + 加权得分`；低于阈值 → 转 LLM → 再失败则 `chat`

### 工具注册表

每个工具是独立的 Python 类，遵循统一接口：

```python
class BaseTool:
    name: str              # 工具名
    description: str       # 给LLM看的描述
    params_schema: dict    # JSON Schema 参数定义
    risk_level: str        # low / medium / high / critical

    def execute(self, params: dict) -> ToolResult
    def preview(self, params: dict) -> str   # 确认前的影响预览（可选覆写）
    def validate_params(self, params: dict) -> None
```

**已交付工具集（21 个）：**

| 类别 | 工具 | 风险 | 说明 |
|------|------|------|------|
| 文件 | `file_search` | low | 搜索文件（名称模式 + 时间范围） |
| 文件 | `file_list` | low | 列出目录内容 |
| 文件 | `file_read` | low | 读取文本文件；非文本用默认程序打开 |
| 文件 | `file_rename` | medium | 重命名（目标已存在不覆盖） |
| 文件 | `file_move` | medium | 移动到其他白名单目录 |
| 文件 | `file_delete` | high | **强制回收站**，可从回收站恢复 |
| 系统 | `system_info` | low | CPU / 内存 / 电量 / 磁盘 |
| 系统 | `clipboard` | low | 剪贴板读写 |
| 系统 | `open_app` | medium | 用默认程序打开文件/文件夹/应用 |
| 系统 | `screenshot` | medium | 截屏保存（真实 GDI 捕获） |
| 系统 | `run_command` | high | 执行系统命令（需确认 + 危险模式拦截） |
| 生产力 | `calculate` | low | 数学计算（AST 白名单，支持中文数字/运算符） |
| 生产力 | `translate` | low | 翻译（复用项目 LLM 插件） |
| 生产力 | `reminder` | low | 定时提醒 |
| 生产力 | `weather` | low | 天气查询 |
| 浏览器 | `web_open` | low | 打开网址/常见站点（含 SSRF 防护） |
| 浏览器 | `web_search` | low | 搜索并返回摘要 |
| 浏览器 | `web_read` | low | 读取网页正文 |
| 记忆 | `memory_remember` | low | 记住一件事（偏好/事实）；含密码等敏感信息时**拒绝入库** |
| 记忆 | `memory_recall` | low | 按语义/词法检索记忆（中文可命中） |
| 记忆 | `memory_forget` | medium | 删除一条或清空记忆（走确认通道） |

### 安全守卫

三级安全机制保护用户数据和系统安全：

| 风险等级 | 处理方式 | 涉及操作 |
|---------|---------|---------|
| **low** | 自动执行 | 搜索/列出/读取文件、查看系统信息、计算、翻译 |
| **medium** | 气泡确认 | 重命名、移动、打开应用、截图 |
| **high** | 确认 + 操作预览 | 删除（回收站）、执行命令 |
| **critical** | 拒绝执行 | 格式化、系统关键文件 |

**R1 硬性规则（不可配置放宽）：**
- 路径白名单仅限桌面 / 文档 / 下载 / 图片四个目录
- 拒绝路径穿越、UNC、符号链接逃逸、空字节
- `file_delete` 源码中**不存在**永久删除分支，只有 `send2trash`
- 所有写操作写入 SQLite 审计库（`data/agent_audit.db`）

### 消息协议

Voice Layer 和 Agent Layer 之间的通信格式：

```python
@dataclass
class AgentCommand:
    """Voice → Agent：用户意图"""
    action: str           # 工具名
    params: dict          # 工具参数
    raw_text: str         # 原始语音文本
    request_id: str       # 请求ID（匹配结果）
    confidence: float     # 意图置信度

@dataclass
class AgentResult:
    """Agent → Voice：执行结果"""
    request_id: str       # 匹配请求
    status: str           # success / error / needs_confirm
    summary: str          # 人类可读摘要（给TTS）
    data: Any             # 结构化数据（给UI）
    emotion: str          # 情绪标签（驱动动效）
    follow_up: list       # 可能的后续操作建议
```

---

## 插件系统

### 插件接口

8 种插件类型，每种有对应的抽象基类：

| 接口 | 说明 | 默认实现 |
|------|------|---------|
| `ASREngine` | 语音识别 | faster-whisper (base, INT8) |
| `TTSEngine` | 语音合成 | Edge-TTS (XiaoxiaoNeural) |
| `LLMEngine` | 大语言模型 | Groq API (gpt-oss-120b) |
| `EmbeddingEngine` | 向量嵌入 | embed_anything (bge-small-zh) |
| `VectorDBEngine` | 向量数据库 | LEANN |
| `VoiceprintEngine` | 声纹识别 | resemblyzer |
| `FileMonitorEngine` | 文件监控 | watchfiles |
| `AvatarEngine` | 形象生成 | liveportrait |

### 插件注册

```python
# plugins/my_plugin/plugin.py
def register():
    return {
        "name": "my_plugin",
        "version": "1.0.0",
        "interface": "ASREngine",
        "class": "MyASR",
        "dependencies": ["dependency1"]
    }
```

### 插件目录结构

```
plugins/
├── asr/
│   └── faster_whisper/
│       └── plugin.py
├── tts/
│   └── edge_tts/
│       └── plugin.py
├── llm/
│   ├── openai_api/
│   │   └── plugin.py
│   ├── ollama/
│   │   └── plugin.py
│   └── openrouter/
│       └── plugin.py
├── embedding/
│   └── embed_anything/
│       └── plugin.py
├── vector_db/
│   └── leann/
│       └── plugin.py
├── voiceprint/
│   └── three_d_speaker/
│       └── plugin.py
├── file_monitor/
│   └── watchfiles/
│       └── plugin.py
└── avatar/
    └── liveportrait/
        └── plugin.py
```

---

## 动画系统

### 三层合成架构

Pet 窗口使用三层独立动画合成最终画面：

```
┌─────────────────────────┐
│     Overlay (特效层)      │  爱心、zzz、音符等
├─────────────────────────┤
│   Expression (表情层)     │  开心眼、垂眼、说话嘴型
├─────────────────────────┤
│      Base (基础层)        │  身体动作、走动、跳舞
└─────────────────────────┘
            │
            ▼
     最终合成画面 (128×128)
```

### 17 种状态动画

| 状态 | 类型 | 帧数 | 视觉效果 |
|------|------|------|---------|
| idle | 基础 | 36 | 呼吸起伏+眨眼+尾巴摇 |
| happy | 表情 | 24 | 开心^^眼+笑口+爱心 |
| sad | 表情 | 30 | 垂眼+泪滴+身体下沉 |
| angry | 表情 | 24 | 皱眉+颤抖+怒气符号 |
| surprise | 表情 | 24 | 大眼+弹起+感叹号 |
| love | 表情 | 30 | 开心眼+爱心连发 |
| think | 表情 | 36 | 半闭眼+问号+微摇 |
| talk | 表情 | 20 | 嘴巴交替开合 |
| sleep | 表情 | 36 | 闭眼+zzz+呼吸 |
| dance | 动作 | 36 | 摇摆+弹跳+音符 |
| wander | 动作 | 24 | 左右走动+尾巴摇 |
| stare | 动作 | 30 | 眼睛放空+微晃 |
| calm_down | 表情 | 24 | 深呼吸+闭眼 |
| comfort | 表情 | 24 | 温柔+光晕 |
| pat | 交互 | 24 | 享受+耳朵抖+爱心 |
| poke | 交互 | 18 | 弹跳+问号→生气 |
| listen | 表情 | 24 | 耳朵竖起+专注 |

### 平滑过渡

状态切换时自动进行 smoothstep 渐变混合（0.3秒过渡），旧状态最后一帧与新状态第一帧 alpha 混合，消除跳变感。

### VRM 3D 模式

当 `render_mode: vrm` 时，使用 QWebEngineView 渲染 VRM 模型：

```
PetWindow
  ├── PetCanvas (QLabel)          ← sprite 模式显示
  └── QWebEngineView (VRM)        ← vrm 模式显示
       ├── Three.js + @pixiv/three-vrm
       ├── Python → JS 状态桥接 (WebSocket)
       └── JS → Python 事件上报 (拖拽/点击)
```

---

## 性能适配

### 三档模式

| 模式 | 内存 | GPU | LLM | 动画FPS | 延迟目标 |
|------|------|-----|-----|---------|---------|
| **Low** | 4GB+ | 集显 | Groq API (云端) | 15 | ASR <5s |
| **Medium** | 12GB+ | 4GB+ | Ollama 3B INT4 | 30 | ASR <2s, LLM <3s |
| **High** | 24GB+ | 8GB+ | Ollama 7B INT4 | 60 | ASR <500ms, LLM <1s |

### 自动检测

启动时 `HardwareDetector` 自动检测硬件配置并推荐最佳模式。用户可在设置中手动切换。

---

## 快速开始

### 环境要求

- Python 3.10+
- Windows 10/11（主要平台）
- NVIDIA GPU（可选，用于本地推理加速）

### 安装

```bash
# 克隆项目
git clone <repo-url>
cd xbya-vrm-worktree

# 安装依赖
pip install -r requirements.txt

# 配置 API Key（编辑 config.yaml）
# 主要配置：
#   plugins.llm.cloud.api_key    → Groq API Key
#   plugins.llm.cloud.base_url   → Groq API 地址
#   voice.hotkey_interrupt        → 打断热键
```

### 运行

```bash
python run.py
```

### 测试

```bash
# 运行全部测试（1464 passed / 1 skipped）
pytest tests/ -q

# 运行特定模块
pytest tests/test_pet_window.py -v      # 宠物窗口 + 状态机
pytest tests/test_animation.py -v       # 动画系统
pytest tests/test_emotion_analyzer.py -v # 情绪分析
pytest tests/test_hotkey_manager.py -v  # 热键管理
pytest tests/kernel tests/agent -q      # Agent 层（覆盖率 100%）
```

**Agent 层覆盖率**

```bash
# 注意：单文件模块必须用点号形式（services.ack_cache），
# 写成 services/ack_cache 会被 coverage 静默忽略且不报错。
pytest tests/kernel tests/agent \
  --cov=core.kernel --cov=agent --cov=services.ack_cache \
  --cov-report=term-missing
# TOTAL  6581  0  100%
```

**端到端验收（真实配置 + 真实白名单）**

```bash
python tools/p1_acceptance_smoke.py   # P1：7 场景 / 18 断言 → 18/18 通过
python tools/p3_acceptance_smoke.py   # P3：12 场景 / 43 断言 → 43/43 通过
python tools/measure_acceptance_metrics.py   # 指标测量 → 8/8
```

---

## 项目结构

```
xbya-vrm-worktree/
├── run.py                          # 程序入口
├── config.yaml                     # 全局配置
├── requirements.txt                # Python 依赖
│
├── core/                           # 核心框架
│   ├── app.py                     # 应用主控（初始化所有组件）
│   ├── config_manager.py          # 配置管理（YAML + 热切换）
│   ├── plugin_loader.py           # 插件加载器（扫描+动态导入）
│   ├── hardware_detector.py       # 硬件检测（自动推荐性能档）
│   ├── event_bus.py               # 事件总线（模块间异步通信）
│   ├── text_utils.py              # 文本工具
│   └── temp_manager.py            # 临时文件管理
│
├── interfaces/                     # 抽象接口层
│   ├── asr.py                     # 语音识别接口
│   ├── tts.py                     # 语音合成接口
│   ├── llm.py                     # 大模型接口
│   ├── embedding.py               # 向量化接口
│   ├── vector_db.py               # 向量数据库接口
│   ├── voiceprint.py              # 声纹接口
│   ├── file_monitor.py            # 文件监控接口
│   └── avatar.py                  # 形象生成接口
│
├── plugins/                        # 插件实现
│   ├── asr/faster_whisper/        # 语音识别
│   ├── tts/edge_tts/              # 语音合成
│   ├── llm/
│   │   ├── openai_api/            # OpenAI兼容API (Groq/OpenRouter)
│   │   ├── ollama/                # 本地Ollama
│   │   └── openrouter/            # OpenRouter
│   ├── embedding/embed_anything/  # 向量嵌入
│   ├── vector_db/leann/           # 向量数据库
│   ├── voiceprint/three_d_speaker/# 声纹识别
│   ├── file_monitor/watchfiles/   # 文件监控
│   └── avatar/liveportrait/       # 形象生成
│
├── agent/                          # Agent 执行层 (规划中)
│   ├── message.py                 # 消息协议 (AgentCommand/AgentResult)
│   ├── bus.py                     # Agent 消息总线
│   ├── router.py                  # 意图路由器 (本地NLU)
│   ├── planner.py                 # 任务规划器
│   ├── executor.py                # 任务执行引擎
│   ├── safety.py                  # 安全守卫
│   ├── memory.py                  # 短期+长期记忆
│   └── tools/                     # 工具集
│       ├── registry.py            # 工具注册表
│       ├── base.py                # BaseTool 基类
│       ├── file_tools.py          # 文件操作工具
│       ├── system_tools.py        # 系统操作工具
│       ├── browser_tools.py       # 浏览器工具
│       └── productivity_tools.py  # 生产力工具
│
├── animation/                      # 动画系统
│   ├── controller.py              # 三层合成控制器 + 平滑过渡
│   ├── builtin.py                 # 程序化帧生成 (17种状态)
│   ├── clip.py                    # PNG序列帧加载器
│   ├── layer.py                   # 单层动画（时间驱动播放）
│   └── vrm_state_map.py           # Python→VRM 状态映射
│
├── ui/                             # 界面层
│   ├── pet_window.py              # 宠物窗口（语音管线+气泡+打断）
│   ├── state_machine.py           # 状态机（17状态+6情绪+7交互）
│   ├── settings_dialog.py         # 设置对话框
│   └── vrm_bridge.py              # VRM JS↔Python 桥接
│
├── services/                       # 业务服务
│   ├── voice_service.py           # 语音服务（TTS封装）
│   ├── file_service.py            # 文件服务
│   ├── ai_service.py              # AI服务
│   ├── hotkey_manager.py          # 全局热键管理
│   ├── microphone_service.py      # 麦克风服务
│   ├── emotion_analyzer.py        # 情绪分析（8种情绪检测）
│   ├── enthusiasm_service.py      # 热情度调节
│   └── data_export_service.py     # 数据导出
│
├── data/                           # 数据存储
│   ├── vectordb/                  # 向量数据库
│   ├── voiceprint/                # 声纹数据
│   └── cache/                     # 缓存目录
│
├── assets/                         # 静态资源
│   └── vrm/                       # VRM 模型 + JS + CSS
│       ├── AvatarSample_A.vrm
│       └── js/
│           ├── app.js             # Three.js + VRM 渲染
│           ├── styles.css
│           └── three.module.js
│
├── models/                         # AI 模型缓存
│   └── spkrec-ecapa/              # 声纹模型
│
└── tests/                          # 测试
    ├── test_pet_window.py         # 宠物窗口 + 状态机 (17项)
    ├── test_animation.py          # 动画系统 (78项)
    ├── test_emotion_analyzer.py   # 情绪分析 (19项)
    ├── test_hotkey_manager.py     # 热键管理
    ├── test_vrm_bridge.py         # VRM桥接
    ├── test_vrm_state_map.py      # VRM状态映射
    ├── test_voice_pipeline.py     # 语音管线
    ├── test_config.py             # 配置管理
    ├── test_event_bus.py          # 事件总线
    ├── test_hardware.py           # 硬件检测
    ├── test_plugin_loader.py      # 插件加载
    └── ...
```

---

## 开发指南

### 代码规范

- Python 3.10+ 语法（类型提示、dataclass、match-case）
- 中文注释，英文变量名
- 每个模块有 docstring 说明用途

### 添加新插件

1. 在 `plugins/<type>/<name>/` 创建目录
2. 实现插件类，继承 `interfaces/<type>.py` 中的抽象基类
3. 创建 `plugin.py`，实现 `register()` 函数
4. 在 `config.yaml` 中配置启用

### 添加新工具（Agent层）

```python
# agent/tools/my_tools.py
from agent.tools.base import BaseTool, ToolResult

class MyTool(BaseTool):
    name = "my_tool"
    description = "我的自定义工具"
    params_schema = {
        "type": "object",
        "properties": {
            "input": {"type": "string", "description": "输入参数"}
        },
        "required": ["input"]
    }
    risk_level = "low"

    def execute(self, params: dict) -> ToolResult:
        result = do_something(params["input"])
        return ToolResult.ok(data=result, summary=f"操作完成: {result}", count=1)

    def preview(self, params: dict) -> str:
        """可选：确认前的影响预览（如"将移动 12 个文件，共 24.3MB"）"""
        return ""
```

**接入检查清单（缺一不可）：**

1. 在对应的 `all_*_tools()` 工厂函数中导出
2. 把工具交给对应插件注册 —— 工具插件在 `agent/plugins/{file,system,productivity,browser}_tools_plugin.py`，
   它们调用 `agent.plugins.register_tools(ctx, tools)`；若是全新一类能力，则新建一个插件模块
   并加进 `config.yaml` 的 `agent.plugins` 清单
3. 在 `agent/providers/safety/basic_guard.py` 的风险表中登记风险等级
4. 在 `agent/providers/router/rule_router.py` 增加 `IntentDef` 与参数提取器
   （否则该工具只能被 LLM 路由命中）
5. 在 `agent/providers/router/llm_router.py` 的 `TOOL_SCHEMAS` 增加 schema
6. 在 `agent/providers/summarizer/template_sum.py` 增加播报模板（可选，缺省用工具 summary）
7. 补测试；`pytest tests/kernel tests/agent --cov=... --cov-report=term-missing` 应保持 100%

### 配置驱动组装（插件清单）

Agent 层由 `config.yaml` 的 `agent.plugins` 清单驱动装配，**换实现或增减能力都不改代码**：

```yaml
agent:
  plugins:                            # 删掉一项 = 摘掉该能力（13 个插件）
    - {id: kernel,     module: agent.plugins.kernel_plugin}
    - {id: safety,     module: agent.plugins.safety_plugin,     depends_on: [kernel]}
    - {id: file_tools, module: agent.plugins.file_tools_plugin, depends_on: [safety]}
    # …
    - {id: planner,    module: agent.plugins.planner_plugin,    depends_on: [kernel]}
    - {id: memory,     module: agent.plugins.memory_plugin,     depends_on: [kernel]}
    - id: pipeline
      module: agent.plugins.pipeline_plugin
      depends_on: [safety, router, summarizer, executor]
  router:     {provider: rule}         # rule | llm | hybrid      → 换路由实现
  summarizer: {provider: template}     # template | llm | hybrid  → 换摘要实现
  planner:    {provider: template}     # template | llm | hybrid  → 换规划实现
  memory:     {embedder: hashing}      # hashing | st             → 换嵌入器
  tools:      {system: false}          # 整组关掉系统工具
```

- **换 Provider**：改 `router.provider` / `summarizer.provider` / `planner.provider`（每个插件按配置选实现）
- **换嵌入器**：`memory.embedder` 从 `hashing`（默认，离线零依赖）换成 `st`（sentence-transformers，需模型文件）
- **增减能力**：从清单删条目，或给条目加 `enabled: false`
- **热插拔**：`stack.loader.unload("system_tools")` 会让那组工具真的从注册表消失
- **可观测**：`stack.stats()["plugins"]` 给出实际加载的插件 id（按拓扑顺序）
- **降级**：清单缺关键服务 → 装配抛错，`core/app.py` 捕获后降级为纯对话模式；
  `planner` / `memory` 是**可选**能力，从清单删掉后管线照常工作

`agent.tools.*` 开关（`file` / `system` / `productivity` / `browser`）依然可用，与清单互为补充。
注意记忆工具**不受** `tools.*` 管辖，它由 `agent.memory.enabled` 单独控制。

### 添加新动画状态

1. 在 `animation/builtin.py` 添加帧生成函数
2. 在 `_STATE_GENERATORS` 注册
3. 在 `animation/controller.py` 的 `state_map` 添加映射
4. 在 `ui/state_machine.py` 的 `STATES` 添加转移规则

---

## 路线图

### v2.0（当前）
- [x] 语音交互闭环（ASR → LLM → TTS）
- [x] 情绪动效匹配（8种情绪，逐句切换）
- [x] VRM 3D 渲染 + 2D 精灵双模式
- [x] 打断机制（热键/点击/语音）
- [x] 插件化架构（8种插件）
- [x] 声纹识别
- [x] 三档性能适配

### v2.1（当前）
- [x] 插件内核（Context/Service/Registry/EventBus）
- [x] 消息协议（AgentCommand / AgentResult）
- [x] 意图路由（规则路由，50 条指令集准确率 100%）
- [x] 安全守卫（三级风险 + 路径白名单 + 审计日志）
- [x] 线程池执行器（提交/取消/超时/回调隔离）
- [x] 6 个文件工具（搜索/列出/读取/重命名/移动/删除）
- [x] 确认语缓存池（命中 <10ms）
- [x] 并行管线（即时确认语 ∥ 工具执行）
- [x] 语音管线接入 Agent（含原 LLM 降级路径）
- [x] 工程文档（AGENTS.md / spec.md / acceptance.md / tasks.json）

### v2.2（当前，P1 已完成）
- [x] LLM 路由兜底（rule → llm → chat 三级降级）
- [x] 实体追踪（指代消解："第一个"、"那些"、"它"）
- [x] 结果摘要器（模板 + LLM 润色，按复杂度分流）
- [x] 配置驱动插件加载器（`agent/plugins/`，10 个插件 + 拓扑排序 + 热插拔）
- [x] 系统工具（打开应用/查状态/剪贴板/截图/执行命令）
- [x] 生产力工具（计算/翻译/提醒/天气）
- [x] 浏览器工具（打开/搜索/读正文，stdlib 实现 + SSRF 防护）
- [x] 覆盖率补齐至 **100%**（4553 语句 / 0 未覆盖）
- [x] 端到端验收脚本（`tools/p1_acceptance_smoke.py`，18/18）

### v2.3（P2 已完成）
- [x] 确认语 LRU 动态缓存（长尾确认语首次合成后命中，<10ms）
- [x] 指代消解持久化（`agent/tracker_store.py`，重启后仍可解析"那个文件"）
- [x] 摘要器异步化（先播模板 ≈3ms，LLM 润色后经 `feedback.refine` 补播）

### v2.4（P3 已完成）
- [x] **多步任务规划**（`agent/seams/planner.py` + 规则/LLM/混合三个 Provider）
  - 规则路由新增 `plan` 意图承接显式多步标记（「分三步…」/「…然后…」）
  - 管线顺序执行 + 逐步安全校验 + 确认挂起/**断点续跑** + `${s1.paths}` 占位符传数据
  - 规则配方 5 条（搜→删 / 搜→移 / 搜→读 / 读→译 / 截→开），<1ms、结果确定
- [x] **长期记忆**（`agent/seams/{memory,embedder}.py` + SQLite/FTS5/向量检索）
  - 中文可检索（自产 token 串绕过 FTS5 `unicode61` 把连续汉字当单一词元的坑）
  - 词法 + 向量双路 **RRF 融合**；向量后端 `auto` 优先 sqlite-vec、失败自动退 Python
  - 默认嵌入器纯 stdlib 特征哈希（**离线可用**），可换 sentence-transformers
  - 3 个记忆工具（`memory_remember` / `memory_recall` / `memory_forget`）
  - 敏感信息（密码/密钥/证件号）**拒绝入库**，且日志不写原文
- [x] 覆盖率保持 **100%**（6581 语句 / 0 未覆盖，全量 2138 passed）
- [x] 端到端验收脚本（`tools/p3_acceptance_smoke.py`，43/43）

### v3.0
- [ ] Computer Use（视觉LLM + 截图操控）
- [ ] DeepSeek Harness 集成（重型任务）
- [ ] 多设备协同（手机+电脑）
- [ ] 自定义工作流（用户可编程）

---

## 许可证

MIT License

---

## 联系方式

- 项目主页：https://github.com/your-username/xbya-desktop-pet
- 问题反馈：https://github.com/your-username/xbya-desktop-pet/issues
