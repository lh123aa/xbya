# 欣雅 Agent 层 — 规格文档

> 配套文档：`AGENTS.md`（工程约定） / `acceptance.md`（验收标准） / `tasks.json`（任务清单）

---

## 一、概述

### 1.1 目标

在现有桌面宠物基础上，新增一个**并行、插件化**的 Agent 执行层，使欣雅能够：
- 理解自然语言指令 → 映射到工具调用
- 操作本地文件（搜索/读取/重命名/移动/删除）
- 并行反馈（即时确认语 + 异步结果播报）

### 1.2 架构总览

```
用户语音
   │
   ▼
┌──────────────────────── VOICE LAYER（现有，小改）─────────────────────┐
│  ASR → IntentRouter → [AgentCommand] → MessageBus                     │
│                          │                                            │
│                    即时反馈 ack ──▶ TTS（确认语，零延迟）                │
│                                                                       │
│  MessageBus → [AgentResult] → 摘要 → 情绪分析 → 气泡+TTS+动效           │
└───────────────────────────────────────────────────────────────────────┘
                             ↕ 事件总线
┌──────────────────────── AGENT LAYER（新增）──────────────────────────┐
│  MessageBus → SafetyGuard → TaskExecutor → ToolRegistry.execute()     │
│                    │              │                                   │
│              风险分级+确认      进度回调                                │
│                                                                       │
│  ToolRegistry: file_search │ file_list │ file_read │ file_rename     │
│                file_move   │ file_delete（回收站）                     │
└───────────────────────────────────────────────────────────────────────┘
                             ↕ 插件内核
┌──────────────────────── KERNEL（新增）──────────────────────────────┐
│  Context（provide/use/fork/dispose）                                  │
│  Registry（工具/服务/Provider 注册）                                   │
│  EventBus（类型化事件）                                                │
│  PluginLoader（配置驱动加载）                                          │
└───────────────────────────────────────────────────────────────────────┘
```

---

## 二、插件内核（Phase A）

### 2.1 Context — 插件上下文

**职责：** 提供服务注册、服务获取、作用域隔离、资源清理。

**接口：**

```python
class Context:
    def provide(self, name: str, impl: Any) -> Callable[[], None]:
        """注册能力实现，返回清理函数"""
    
    def use(self, name: str) -> Any:
        """获取能力实现（本地优先，未命中查父级）"""
    
    def fork(self) -> "Context":
        """派生子上下文（注册隔离，能力可继承）"""
    
    def on(self, event_type: str, handler: Callable) -> Callable[[], None]:
        """订阅事件，返回取消订阅函数"""
    
    def emit(self, event_type: str, **data) -> None:
        """发射事件"""
    
    def dispose(self) -> None:
        """逆序清理所有注册项"""
```

**状态：** 无状态机，纯容器。

**错误处理：**

| 场景 | 行为 |
|------|------|
| `use()` 找不到服务 | 抛 `ServiceNotFound(name)` |
| `provide()` 重复注册同名 | 覆盖并记录 warning（返回新 disposer） |
| `dispose()` 中某个 disposer 抛异常 | 捕获 + 记录日志，继续清理其余 |
| `fork()` 后父 context 已 dispose | 子 context 仍可用（持有服务引用快照） |

**边界条件：**
- `provide(name="")` → 抛 `ValueError`
- 嵌套 fork 深度 >10 → 记录 warning（防止误用）
- `dispose()` 幂等（重复调用不抛异常）

---

### 2.2 Registry — 通用注册表

**职责：** 管理同类型对象的注册、查询、过滤。

**接口：**

```python
class Registry(Generic[T]):
    def register(self, item: T, name: str = None) -> Callable[[], None]:
        """注册对象，返回注销函数"""
    
    def unregister(self, name: str) -> bool:
        """按名注销"""
    
    def get(self, name: str) -> Optional[T]:
        """按名获取"""
    
    def has(self, name: str) -> bool: ...
    
    def names(self) -> list[str]:
        """所有注册名（快照）"""
    
    def items(self) -> list[T]:
        """所有对象（快照）"""
    
    def filter(self, predicate: Callable[[T], bool]) -> list[T]: ...
```

**使用场景：**
- `ToolRegistry` = `Registry[BaseTool]`
- `ProviderRegistry` = `Registry[Service]`

**边界条件：**
- `names()` / `items()` 返回**快照**（遍历中修改不影响）
- `register` 时 `name=None` → 尝试从对象的 `name` 属性推导
- 无法推导 name → 抛 `ValueError`

---

### 2.3 PluginLoader — 配置驱动加载

**职责：** 从 config.yaml 读取插件清单，按依赖顺序加载/卸载。

**输入（config.yaml）：**

```yaml
agent:
  plugins:
    - id: safety
      module: agent.plugins.safety_plugin
      entry: setup
      enabled: true
      config:
        provider: basic
    - id: router
      module: agent.plugins.router_plugin
      entry: setup
      enabled: true
      depends_on: [safety]
      config:
        provider: hybrid
```

**处理流程：**

```
1. parse_config(config_dict) → list[PluginSpec]
   · 过滤 enabled=false
   · 校验必填字段（id/module/entry）
   
2. resolve_order(specs) → 有序列表
   · Kahn 拓扑排序
   · 循环依赖 → 抛 CircularDependencyError
   · 缺失依赖 → 抛 MissingDependencyError
   
3. load(spec, ctx)
   · importlib.import_module(spec.module)
   · getattr(module, spec.entry)
   · entry(ctx, **spec.config)
   · 捕获异常 → 记录 + 跳过（不中断其他插件）

4. unload(plugin_id, ctx)
   · 调用该插件的 disposer 链
```

**错误处理：**

| 场景 | 行为 |
|------|------|
| 模块不存在 | 记录 error + 跳过该插件 |
| 入口函数不存在 | 记录 error + 跳过该插件 |
| 入口函数抛异常 | 记录 error + 回滚该插件的部分注册 |
| 配置缺字段 | 加载前校验，抛 `ConfigError` |
| 循环依赖 | 抛 `CircularDependencyError`（含环路径） |

---

## 三、并行管线（Phase B）

### 3.1 事件定义

```python
# 语音层 → Agent 层
"speech.recognized"    # {text: str, wav_path: str, request_id: str}
"speech.interrupted"   # {request_id: str}

# Agent 层内部
"intent.resolved"      # {request_id, command: AgentCommand, confidence: float}
"task.submitted"       # {request_id, task_id: str}
"task.progress"        # {task_id, percent: int, message: str}
"task.completed"       # {task_id, result: AgentResult}
"task.failed"          # {task_id, error: str, request_id: str}

# Agent 层 → 反馈层
"feedback.ack"         # {request_id, text: str}          ← 即时确认
"feedback.result"      # {request_id, summary: str, emotion: str}
"feedback.confirm"     # {request_id, question: str, risk: str}
```

### 3.2 时序（关键：并行）

```
t=0.0  用户说完
t=0.5  "speech.recognized" 发射
t=0.51 路由完成 → "intent.resolved" 发射
       │
       ├──▶ 并发路径1: "feedback.ack" 发射
       │              └─▶ TTS 查缓存 → 命中即播（0.05s）
       │                                        ↑ t=0.56 开始播报 ✅
       │
       └──▶ 并发路径2: "task.submitted" 发射
                      └─▶ 安全校验 → 执行器
                                         ↓
t=2.5                                "task.completed" 发射
                                        ↓
                                     "feedback.result" 发射
                                        ↓
                                     TTS 合成 → 播报 ✅ t=2.8
```

**硬性要求：** `feedback.ack` 与 `task.submitted` **必须同时发射**，任何一方不得等待另一方。

### 3.3 确认语缓存池

```python
class AckCache:
    """确认语缓存：启动预热，命中即播（零延迟）"""
    
    PHRASES = {
        "search":  ["好的，我找找看~", "稍等，我搜一下~", "让我翻翻~"],
        "read":    ["好的，我打开看看~", "这就去看~"],
        "write":   ["好的，我这就写~", "马上处理~"],
        "delete":  ["好，我先确认一下~", "让我核对一下要删的~"],
        "system":  ["好的，我看看~", "稍等~"],
        "default": ["好的，我看看~", "稍等一下哦~", "让我处理一下~"],
    }
    
    def warm_up(self, tts) -> None:
        """启动时预合成所有短语"""
    
    def get(self, category: str) -> bytes:
        """取缓存音频（随机选一句）"""
```

**降级：** 缓存未命中 → 实时合成（+0.7s），并记录 miss 用于后续优化。

### 3.4 超时与降级

| 阶段 | 超时 | 超时行为 |
|------|------|---------|
| LLM 路由 | 5s | 降级到规则路由，若也失败 → 转闲聊 |
| 工具执行 | 60s | 取消任务 + 播报"这个操作有点久，我先停下了" |
| TTS 合成 | 15s | 跳过该句，继续下一句（现有已实现） |

### 3.5 打断处理

用户打断时（热键/点击/语音）：

```
1. 发射 "speech.interrupted"
2. Pipeline 收到 → 设置取消标志
3. 执行器检查取消标志 → 中止当前任务
4. 队列中待播的 ack/result 全部清空
5. 播放器停止 → 动效切换到 angry/surprise
```

---

## 四、意图路由（Phase C）

### 4.1 Seam 定义

```python
class RouterService(Service):
    """路由能力接口"""
    @abstractmethod
    def route(self, text: str, context: dict) -> AgentCommand: ...
```

### 4.2 支持的意图

| action | 关键词 | 参数 | 风险 |
|--------|--------|------|------|
| `file_search` | 找、搜索、查找、哪里有 | pattern, dir, time_range | low |
| `file_list` | 有什么、列出、看看 | dir | low |
| `file_read` | 打开、看看、读一下 | target | low |
| `file_rename` | 重命名、改名、改名为 | source, target | medium |
| `file_move` | 移动、移到、挪到 | source, dest | medium |
| `file_delete` | 删除、删掉、清理 | targets, permanent=false | high |
| `system_info` | 电量、内存、CPU | metric | low |
| `clipboard` | 复制、粘贴 | action, text | low/medium |
| `translate` | 翻译、用英文 | text, target_lang | low |
| `calculate` | 算一下、多少 | expression | low |
| `chat` | （兜底） | - | low |

### 4.3 规则路由算法

```
输入: text = "帮我找一下上周的合同文件"

1. 关键词扫描
   "找" → 命中 file_search (权重 2.0)
   "上周" → 时间限定词 (权重 1.0)
   
2. 参数提取（正则）
   pattern: 从 "找一下(.+?)文件" 提取 → "合同"
   time_range: "上周" → ("2025-01-06", "2025-01-12")
   dir: 未指定 → 默认 ["Desktop", "Documents", "Downloads"]
   
3. 置信度计算
   base = 0.5（关键词命中）
   + 0.2（有明确动词）
   + 0.15（提取到具体参数）
   + 0.1（句式完整）
   = 0.95
   
4. 输出 AgentCommand
   confidence >= 0.5 → 直接返回
   confidence <  0.5 → 转 LLM 路由
```

### 4.4 LLM 路由（兜底）

**只在规则路由置信度 <0.5 时调用。**

```python
# function calling schema（精简版，只给 LLM 必要信息）
tools = [
    {
        "type": "function",
        "function": {
            "name": "file_search",
            "description": "搜索文件",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "文件名匹配模式"},
                    "dir": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["pattern"]
            }
        }
    },
    # ... 其他工具
]
```

**降级链：** `hybrid → rule → chat`

### 4.5 混合路由

```python
class HybridRouter(RouterService):
    def route(self, text, context):
        cmd = self.rule.route(text, context)
        if cmd.confidence >= self.threshold:   # 默认 0.5
            return cmd                          # 快速路径（85%）
        if not self.llm_enabled:
            return cmd                          # LLM 关闭 → 用规则结果
        try:
            return self.llm.route(text, context)  # 兜底（15%）
        except Exception:
            cmd.action = "chat"                   # LLM 也失败 → 闲聊
            return cmd
```

---

## 五、安全守卫（Phase D）

### 5.1 Seam 定义

```python
class SafetyService(Service):
    @abstractmethod
    def check(self, action: str, params: dict) -> SafetyVerdict: ...
    
    @abstractmethod
    def confirm(self, request_id: str, approved: bool) -> None: ...

@dataclass
class SafetyVerdict:
    allowed: bool           # 是否可直接执行
    risk: str               # low/medium/high/critical
    confirm_needed: bool    # 是否需要确认
    require_double: bool    # 是否需要双重确认
    reason: str             # 拒绝原因（allowed=False 时）
    preview: str            # 操作预览文案
```

### 5.2 风险分级

| 风险 | 处理 | 工具 |
|------|------|------|
| **low** | 自动执行 | file_search, file_list, file_read, system_info, translate, calculate |
| **medium** | 气泡确认一次 | file_write, file_rename, file_move, clipboard_set |
| **high** | 双重确认 | file_delete, run_command |
| **critical** | 直接拒绝 | format_disk, registry_edit（未实现，仅预留） |

### 5.3 路径白名单（R1 核心防护）

```python
WHITELIST = [
    Path.home() / "Desktop",
    Path.home() / "Documents",
    Path.home() / "Downloads",
    Path.home() / "Pictures",
]

def validate_path(path: str) -> Path:
    """路径校验：白名单 + 规范化 + 符号链接解析"""
    p = Path(path).expanduser()
    
    # 1. 规范化（消除 ../ 和 ./）
    p = p.resolve()
    
    # 2. 符号链接解析（防止软链逃逸）
    real = p.resolve(strict=False)
    
    # 3. 白名单校验
    for allowed in WHITELIST:
        try:
            real.relative_to(allowed.resolve())
            return real              # ✅ 在白名单内
        except ValueError:
            continue
    
    raise PathNotAllowed(f"路径不在允许范围内: {real}")
```

**必须通过的攻击测试：**

| 攻击 | 输入 | 期望 |
|------|------|------|
| 目录穿越 | `Desktop/../../../Windows/System32/x.dll` | 拒绝 |
| 符号链接逃逸 | `Desktop/link_to_system` → `C:\Windows` | 拒绝 |
| 绝对路径越权 | `C:\Windows\System32\cmd.exe` | 拒绝 |
| UNC 路径 | `\\server\share\file` | 拒绝 |
| 大小写绕过 | `C:\WINDOWS\...` | 拒绝 |
| 8.3 短名 | `C:\PROGRA~1\...` | 拒绝 |

### 5.4 删除硬约束

```python
def file_delete(paths: list[str], permanent: bool = False) -> ToolResult:
    # 硬约束：不接受永久删除
    if permanent:
        raise SecurityError("永久删除已禁用")
    
    # 强制走回收站
    from send2trash import send2trash
    for p in paths:
        send2trash(validate_path(p))
```

**代码审查要点：** `file_delete` 中**不得存在** `os.remove` / `shutil.rmtree` / `Path.unlink` 调用。

### 5.5 确认流程

```
工具需要确认
   │
   ├─▶ 生成 request_id
   ├─▶ 创建 PendingConfirmation（含超时 30s）
   ├─▶ 发射 "feedback.confirm" {question, risk}
   │
   ▼
等待用户响应
   │
   ├─ "确定"/"好"/"嗯"  → approved=True  → 执行
   ├─ "算了"/"不用"     → approved=False → 取消
   ├─ 超时 30s          → 自动取消 + "那我先不做啦"
   └─ 其他内容          → 视为新指令，取消当前确认
```

**记忆机制：** 用户确认过的同类操作（同 action + 同目录）→ 15 分钟内不再询问（可配置）。

---

## 六、文件工具（Phase F）

### 6.1 BaseTool 接口

```python
@dataclass
class ToolResult:
    success: bool
    data: Any = None
    summary: str = ""            # 人类可读摘要
    error: str = ""
    emotion: str = "talk"        # 驱动动效
    truncated: bool = False      # 结果是否被截断

class BaseTool(ABC):
    name: str = ""
    description: str = ""
    params_schema: dict = {}
    risk_level: str = "low"
    timeout: int = 30            # 单工具超时（秒）
    
    @abstractmethod
    def execute(self, params: dict) -> ToolResult: ...
    
    def validate_params(self, params: dict) -> None:
        """JSON Schema 校验，失败抛 ParamError"""
```

### 6.2 六个文件工具

| 工具 | 输入 | 输出 | 边界 |
|------|------|------|------|
| `file_search` | pattern, dir[], time_range | 文件列表（name/path/size/mtime） | 结果上限 50，超出标记 truncated |
| `file_list` | dir, limit | 文件列表 | 上限 100 |
| `file_read` | target | 文本内容 或 "已用默认程序打开" | 文本上限 10000 字符；>10MB 拒绝 |
| `file_rename` | source, target | 新路径 | 目标已存在 → 报错，不覆盖 |
| `file_move` | source, dest_dir | 新路径 | 目标存在同名 → 报错 |
| `file_delete` | targets[] | 删除数量 | 强制回收站；上限 200 个 |

### 6.3 错误处理约定

| 错误 | summary 文案 |
|------|-------------|
| 文件不存在 | "咦，没找到这个文件呢，是不是名字记错了？" |
| 权限不足 | "这个文件我没有权限动它，可能需要管理员权限哦" |
| 参数缺失 | "你想把它改成什么名字呀？" |
| 目标已存在 | "已经有个同名文件了，换个名字吧？" |
| 文件过大 | "这个文件太大了，我读不完呢~" |
| 目录不存在 | "这个文件夹我找不到呢" |

---

## 七、实体追踪（Phase G）

```python
class EntityTracker:
    """追踪对话实体，支持指代消解"""
    
    MAX_FILES = 20      # 文件栈容量
    MAX_ACTIONS = 10    # 操作栈容量
    
    def push_files(self, files: list[dict]) -> None:
        """记录搜索结果（成为"那些文件"的候选）"""
    
    def push_action(self, action: str, params: dict) -> None:
        """记录执行的操作"""
    
    def resolve(self, reference: str) -> list[dict]:
        """
        解析指代：
          "第一个" / "第2个"  → 按序索引
          "那些" / "这些"     → 全部
          "它" / "那个"       → 最近单个
          "刚才那个"          → 最近单个（强调时间）
        """
```

**歧义处理：** 索引越界 → 返回空 + Pipeline 反问"你说的第几个呀？"  
**栈溢出：** 超过容量 → 丢弃最旧项（FIFO）

---

## 八、配置项

见 `AGENTS.md` 第九章「配置约定」。

---

## 九、非功能要求

| 项 | 要求 |
|----|------|
| 规则路由延迟 | <10ms（P95） |
| 感知延迟 | <1.5s（P95） |
| 总延迟 | <3s（P95，不含长任务） |
| 内存增量 | <50MB（Agent 层） |
| CPU 占用 | 空闲时 <1% |
| 并发任务 | 支持 4 个并行工具执行 |
| 兼容性 | 现有语音功能零回归 |

---

**文档版本：** v1.0  
**覆盖范围：** P0（Phase A~H）
