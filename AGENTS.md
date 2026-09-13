# AGENTS.md — 欣雅 Agent 层工程文档

> 本文件由「驾驭工程」方法论产出，是 Agent 层开发的**唯一权威约定**。
> 任何任务开始前必须阅读；任何架构变更必须同步更新本文件。

---

## 一、项目目标

### 1.1 一句话定义

在现有桌面宠物「欣雅」之上，构建一个**并行、插件化、可升级**的 Agent 执行层，使欣雅从「能聊天」升级为「能干活」。

### 1.2 核心价值

| 维度 | 现状 | 目标 |
|------|------|------|
| 能力 | 只能对话 | 能操作电脑（文件/系统/网络） |
| 延迟 | 串行 3.5s | 并行 1.2s（感知延迟） |
| 架构 | 单体服务 | 插件化，能力可替换 |
| 升级 | 改代码 | 改配置 |

### 1.3 设计原则（不可违背）

1. **交互层与执行层完全解耦** — 只通过事件总线通信，互不感知对方实现
2. **并行优先** — 任何阶段都不等待下游，只发事件
3. **能力 Seam 三角** — 每个能力必须有 Definition / Provider / Consumer
4. **注册即副作用** — 每次注册返回 disposer，可热插拔
5. **本地规则优先** — 85% 请求走规则（<10ms），15% 走 LLM 兜底
6. **永不永久删除** — 删除操作强制走回收站，无例外
7. **配置驱动组装** — 换实现 = 改配置，不改代码
8. **向后兼容** — 现有语音功能必须保持可用，Agent 是增强而非替换

---

## 二、已确认的决策清单

| # | 决策项 | 结论 | 影响 |
|---|--------|------|------|
| 1 | 流程模式 | 压缩模式（8~10 关键问题） | 文档精简 |
| 2 | 兼容策略 | 保留现有语音管线作降级路径 | 风险最低 |
| 3 | P0 范围 | 内核 + 管线 + 路由/安全 + 文件工具 | 5 个核心模块 |
| 4 | 仓库策略 | 现有仓库内新增 `core/kernel/` + `agent/` | 复用现有工具链 |
| 5 | 开发节奏 | 每天 2~4 小时，单任务 30~90 分钟 | 总周期 ~3 周 |
| 6 | 访问范围 | 限制在桌面/文档/下载/图片四个目录 | 零风险 |
| 7 | 删除策略 | 强制走回收站（send2trash） | 可恢复 |
| 8 | 验收方式 | pytest 自动化 + 关键流程手动验证 | 可回归 |
| 9 | P1 优先级 | 系统工具（打开应用/查状态/剪贴板/截图） | 最快见效 |
| 10 | 风险重点 | 误操作损坏文件 | 安全设计重心 |
| 11 | LLM 路由 | 允许 15% 请求走 LLM | 成本可控 |

---

## 三、仓库结构

```
xbya-vrm-worktree/
│
├── core/kernel/                     ← 🆕 插件内核（DSH Cordis 的 Python 简体版）
│   ├── __init__.py
│   ├── events.py                   # 类型化事件总线
│   ├── service.py                  # Service 基类 + ServiceNotFound
│   ├── context.py                  # Context（provide/use/fork/dispose）
│   ├── registry.py                 # 通用注册表（服务/工具/Provider）
│   └── loader.py                   # 配置驱动的插件加载
│
├── agent/                           ← 🆕 Agent 层
│   ├── __init__.py
│   ├── message.py                  # AgentCommand / AgentResult
│   ├── pipeline.py                 # 并行管线协调器
│   ├── tracker.py                  # EntityTracker 指代消解
│   ├── text_norm.py                # 繁→简归一化（整词表 + 无歧义字级表；缺陷 14/22）
│   │
│   ├── seams/                      # 能力定义（Service Definition）
│   │   ├── router.py              #   路由能力接口
│   │   ├── safety.py              #   安全能力接口
│   │   ├── executor.py            #   执行能力接口
│   │   ├── summarizer.py          #   摘要能力接口
│   │   ├── planner.py             #   规划能力接口（P3 / D5）+ 占位符解析
│   │   ├── embedder.py            #   嵌入能力接口（P3）+ 向量工具
│   │   └── memory.py              #   记忆能力接口（P3）+ 敏感判据
│   │
│   ├── providers/                  # 能力实现（Service Provider）
│   │   ├── router/
│   │   │   ├── rule_router.py     #   规则路由（<10ms，含 plan 多步预检）
│   │   │   ├── llm_router.py      #   LLM 路由（1~3s）
│   │   │   └── hybrid_router.py   #   混合路由（默认）
│   │   ├── safety/
│   │   │   └── basic_guard.py     #   三级风险 + 路径白名单
│   │   ├── executor/
│   │   │   └── thread_pool.py     #   线程池执行器
│   │   ├── summarizer/
│   │   │   ├── template_sum.py    #   模板摘要
│   │   │   └── llm_sum.py         #   LLM 润色
│   │   ├── planner/               #   多步规划（P3 / D5）
│   │   │   ├── template_planner.py #    规则配方（<1ms，结果确定）
│   │   │   ├── llm_planner.py     #    LLM 分解 + 严格校验
│   │   │   └── hybrid_planner.py  #    规则优先，必要时转 LLM（默认）
│   │   ├── productivity/          #   生产力能力真实后端（F7）
│   │   │   ├── llm_translate.py   #    翻译（复用项目 LLM + 输出清洗）
│   │   │   ├── open_meteo.py      #    天气（免密钥、不做 IP 定位）
│   │   │   └── reminder_scheduler.py # 提醒到点调度线程（让"我会提醒你"不再撒谎）
│   │   └── memory/                #   长期记忆（P3）
│   │       ├── hashing_embedder.py #    默认嵌入器（纯 stdlib、离线、确定）
│   │       ├── st_embedder.py     #    sentence-transformers（懒加载 + 降级）
│   │       ├── recall_store.py    #    SQLite + FTS5 中文分词 + 向量 RRF 融合
│   │       └── hybrid_memory.py   #    MemoryService 实现（管"什么该记"）
│   │
│   ├── plugins/                    # 插件装配（配置驱动，见 §15 与 phases.md §14）
│   │   ├── __init__.py            #   服务名常量 + DEFAULT_MANIFEST + register_tools
│   │   ├── kernel_plugin.py       #   tool_registry
│   │   ├── safety_plugin.py       #   safety
│   │   ├── file_tools_plugin.py   #   6 个文件工具
│   │   ├── system_tools_plugin.py #   5 个系统工具
│   │   ├── productivity_providers_plugin.py  # F7：translate/weather 真实后端
│   │   ├── productivity_tools_plugin.py     # 4 个工具 + 提醒调度器生命周期
│   │   ├── browser_tools_plugin.py
│   │   ├── router_plugin.py       #   router（rule/llm/hybrid 由配置选）
│   │   ├── planner_plugin.py      #   planner（template/llm/hybrid 由配置选）
│   │   ├── memory_plugin.py       #   embedder + memory + 3 个记忆工具
│   │   ├── summarizer_plugin.py   #   summarizer（template/llm/hybrid 由配置选）
│   │   ├── executor_plugin.py     #   线程池执行器
│   │   └── pipeline_plugin.py     #   ack_cache / tracker / tracker_store / pipeline
│   │
│   ├── tracker_store.py            # 实体栈 JSON 持久化（P2-3）
│   │
│   └── tools/                      # 工具（Consumer，共 21 个）
│       ├── base.py                #   BaseTool + ToolResult + @tool
│       ├── registry.py            #   ToolRegistry（含 to_llm_schemas）
│       ├── file_tools.py          #   6 个文件工具
│       ├── system_tools.py        #   5 个系统工具（P1）
│       ├── browser_tools.py       #   3 个浏览器工具（P2）
│       ├── productivity_tools.py  #   4 个生产力工具（P2）
│       └── memory_tools.py        #   3 个记忆工具（P3）
│
├── services/
│   ├── ack_cache.py                ← 🆕 确认语缓存池（含 LRU）
│   └── ...（现有）
│
├── docs/agent/                      ← 🆕 工程文档
│   ├── spec.md                     #   规格文档
│   ├── acceptance.md               #   验收标准
│   ├── acceptance-report-p1.md     #   P1 系统性验收报告
│   ├── phases.md                   #   历史阶段实施记录（P0/P1/P2/F2）
│   ├── tasks.json                  #   P0 任务清单
│   ├── tasks-p2.json / tasks-p3.json
│   └── evidence/                   #   证据归档
│
├── tests/                           ← 实际文件（P0 计划里的拆分方案已合并，以现状为准）
│   ├── kernel/
│   │   ├── test_kernel.py           # 内核（事件/上下文/注册表/服务）
│   │   ├── test_kernel_final.py
│   │   └── test_loader.py           # 插件加载器
│   └── agent/
│       ├── test_pipeline.py test_router.py test_safety.py test_tracker.py
│       ├── test_file_tools.py test_system_tools.py test_browser_tools.py
│       ├── test_productivity_tools.py test_summarizer.py test_llm_router.py
│       ├── test_integration.py test_plugins.py
│       ├── test_ack_lru.py test_async_refine.py test_tracker_persist.py
│       ├── test_planner.py           # P3：多步规划
│       ├── test_memory_store.py test_memory_tools.py test_memory_wiring.py  # P3：长期记忆
│       ├── test_productivity_providers.py  # F7：翻译/天气/提醒的真实后端
│       └── test_coverage_*.py test_edge_cases.py   # 覆盖率收尾用例
│
├── tools/                           ← 验收与运维脚本
│   ├── run_acceptance.py            # 一次跑完 13 关（G1~G13）+ 逐关留原始输出
│   ├── check_repo_hygiene.py        # G13：版本控制/忽略规则/密钥/模板落后/证据路径（20 项）
│   ├── prepare_manual_acceptance.py # 人工验收：建沙箱+临时改白名单+打印第 1 句
│   ├── check_manual_evidence.py     # 人工验收：证据与结论齐不齐
│   ├── reminders_cli.py             # 提醒状态文件的读写（跨进程验收入口/运维）
│   ├── make_config_example.py       # 从 config.yaml 生成无密钥模板（--check 查落后）
│   ├── p1_acceptance_smoke.py       # P1 端到端验收（18 项断言）
│   ├── p3_acceptance_smoke.py       # P3 + F7 端到端验收（44 项断言）
│   ├── measure_acceptance_metrics.py
│   ├── verify_f3_real_llm.py        # F3：真实 LLM（20 项；抓到 429 记 SKIP 而非 FAIL）
│   ├── verify_f4_f5_real.py         # F4/F5：真实网络 + 真机系统调用（35 项）
│   ├── verify_f7_real_services.py   # F7：真实翻译/天气/提醒 + 跨进程（32 项）
│   ├── verify_gui_launch.py         # 真实 GUI 启动与优雅退出（16 项）
│   ├── voice_scenarios_loopback.py  # 语音 5 场景回环（TTS→ASR→管线；默认吃固定测激）
│   └── measure_asr_stimulus.py      # 测激稳定性测量（替身抖动 vs 解码确定性）
│
├── docs/agent/examples/kernel_demo.py  # G0 内核示例（113 条自断言）
├── config.yaml                      ← agent 配置段（配置的单一事实来源）
├── AGENTS.md                        ← 本文件（当前约定 + 当前阶段）
└── README.md                        ← 已有
```

---

## 四、开发约束

### 4.1 技术约束

| 约束 | 说明 |
|------|------|
| Python 版本 | 3.10+（现有项目） |
| UI 框架 | PySide6（现有） |
| 线程模型 | 所有跨线程通信走 Qt Signal/Slot，不共享状态 |
| 依赖最小化 | P0 阶段不引入新依赖（send2trash 除外） |
| 性能目标 | 规则路由 <10ms，感知延迟 <1.5s，总延迟 <3s |
| 硬件适配 | 兼容 MX250 / 16GB / performance_mode=low |

### 4.2 代码规范

```python
# 1. 类型提示完整
def route(self, text: str, ctx: dict) -> AgentCommand: ...

# 2. 中文注释，英文标识符
class RuleRouter(RouterService):
    """规则路由：关键词匹配，延迟 <10ms"""
    
# 3. 每个模块有 docstring
# 4. 每个公开函数有类型标注和参数说明
# 5. 异常不吞掉，要么处理要么上抛
```

### 4.3 测试要求

| 模块类型 | 测试要求 | 覆盖率目标 |
|---------|---------|-----------|
| 内核（kernel/） | 必须，纯逻辑易测 | >90% |
| Seam 接口 | 必须 | >85% |
| Provider | 必须 | >80% |
| 工具（tools/） | 必须 | >75% |
| 管线/集成 | 必须（端到端） | 关键路径 100% |

**每个任务的完成定义：**
1. ✅ 代码通过语法检查
2. ✅ 对应测试用例通过
3. ✅ 无回归（现有测试全绿）
4. ✅ 关键流程手动验证

---

## 五、风险识别

### 5.1 风险矩阵

| # | 风险 | 概率 | 影响 | 应对措施 | 负责人 |
|---|------|------|------|---------|--------|
| R1 | **误操作损坏文件**（重点） | 中 | 极高 | ①路径白名单 ②三级风险确认 ③强制回收站 ④操作预览 ⑤审计日志 | 安全模块 |
| R2 | LLM API 不稳定/超配额 | 中 | 中 | ①规则路由兜底 ②无 API 时降级到规则 ③本地缓存 | 路由模块 |
| R3 | 并行管线引入竞态 | 中 | 高 | ①Qt Signal 通信 ②不共享状态 ③单线程执行器串行化 | 管线模块 |
| R4 | 延迟反而增加 | 低 | 中 | ①确认语缓存预热 ②实时监控端到端延迟 ③超时降级 | 管线模块 |
| R5 | 架构复杂后期难维护 | 中 | 中 | ①插件卸载清理 ②配置驱动 ③单元测试覆盖 >85% | 内核模块 |
| R6 | 与现有语音管线冲突 | 中 | 高 | ①保留降级路径 ②配置开关 ③充分回归测试 | 集成阶段 |
| R7 | 指代消解错误 | 中 | 中 | ①歧义时主动询问 ②显示候选列表 ③实体栈容量限制 | 追踪模块 |

### 5.2 R1 的详细防护设计（最高优先级）

```
用户："删除桌面上的截图"
  │
  ├─1─▶ 路径解析
  │      Desktop/*.png → 绝对路径
  │
  ├─2─▶ 白名单校验
  │      C:\Users\xxx\Desktop\screenshot.png ✅ 在白名单内
  │      C:\Windows\System32\... ❌ 拒绝
  │
  ├─3─▶ 操作预览
  │      "找到 12 张截图，共 24.3MB，要移到回收站吗？"
  │      [列出前 3 个文件名]
  │
  ├─4─▶ 风险确认
  │      file_delete → risk=high → 双重确认
  │      "确认删除这 12 个文件吗？"
  │
  ├─5─▶ 执行
  │      send2trash(path)  ← 强制，无永久删除选项
  │
  └─6─▶ 审计
         写入 agent_audit.db: {时间, 操作, 文件列表, 结果, 可撤销}
```

**硬性规则：**
- 任何路径操作前必须经过白名单校验
- `file_delete` 的实现中**不存在**永久删除分支
- 所有写操作（写/改/移/删）都记录审计日志
- 审计日志保留 30 天，支持按时间回溯

---

## 六、技术债务登记

| # | 债务项 | 产生原因 | 影响 | 偿还计划 |
|---|--------|---------|------|---------|
| ~~D1~~ | ~~无指代消解的持久化~~ | — | — | ✅ **已偿还（P2-3）**：新增 `agent/tracker_store.py`，实体栈 JSON 原子落盘 + 启动恢复；重启后仍可解析"那个文件" |
| ~~D2~~ | ~~确认语缓存静态~~ | — | — | ✅ **已偿还（P2-1）**：`AckCache` 增加有界 text→audio LRU（`get_or_synthesize`），长尾短语首次合成后命中；确认问句已接入 |
| D3 | 单机单用户假设 | 未做多用户隔离 | 无法支持多用户 | 框架支持 `ctx.fork()`，暂不启用 |
| ~~D4~~ | ~~摘要器同步调用 LLM~~ | — | — | ✅ **已偿还（P2-2）**：`feedback.refine` 异步补播；先播模板（实测 <100ms），LLM 润色完成后单独补一句 |
| ~~D5~~ | ~~无多步任务规划~~ | — | — | ✅ **已偿还（P3-A）**：新增 `agent/seams/planner.py` + `providers/planner/{template,llm,hybrid}_planner.py`；规则路由新增 `plan` 意图承接显式多步标记；管线支持顺序执行、逐步安全校验、确认挂起/断点续跑、`${s1.paths}` 占位符传数据。换 Provider = 改 `agent.planner.provider` |
| ~~D6~~ | ~~Playwright 未引入~~ | ✅ **已关闭（方案变更）**：浏览器能力用 stdlib `webbrowser` + 已有 `requests` 实现（`agent/tools/browser_tools.py`），不引入 Playwright | — | 已完成（3 个工具 + SSRF 防护） |
| D7 | 测试套件退出时间歇性崩溃 | `tests/test_vrm_bridge.py` 的 `make_bridge` fixture 无 teardown，约 30 个带活跃 `QTimer` + 真实 `QWebChannel` 的桥只能等 GC 随机回收，残留定时器/队列事件在后续嵌套事件循环被投递即崩（故障帧恒为 `test_vrm_bridge.py:310 loop.exec()`）。已加确定性收尾（停表→deleteLater→processEvents） | 修复前 11 轮复现 2 次（≈1/5.5）；修复后累计 **28 轮 0 次** | 🟢 **已有效消除**（P3 判定，见 §16.3）。保留登记直到有跨机器/跨版本样本 |
| ~~D8~~ | ~~执行器线程未显式 join~~ | ✅ **已偿还**：`AgentStack.dispose()` 关线程池；`conftest` 等待非 daemon 线程 | — | 已完成 |
| ~~D9~~ | ~~管线回调运行在执行线程~~ | — | — | ✅ **已偿还**：`AgentEventBridge` 统一转 Qt 信号 |
| ~~D10~~ | ~~LLM 路由未实现~~ | — | — | ✅ **已偿还**：`llm_router.py` + `hybrid_router.py`，规则优先、置信度不足才转 LLM |
| ~~D11~~ | ~~无实体追踪~~ | — | — | ✅ **已偿还**：`agent/tracker.py`，序数/集合/单数指代 + FIFO 容量限制 |
| ~~D12~~ | ~~插件加载器未接入~~ | — | — | ✅ **已偿还（P2/F2）**：`build_agent_stack` 不再 new 任何 Provider，改为 `PluginLoader` 驱动 10 个插件装配；清单存在于 `config.yaml` 的 `agent.plugins`；换实现=改 provider 配置、增减能力=改清单 |
| ~~D13~~ | ~~提醒不持久化~~ | — | — | ✅ **已偿还（P4-A2）**：新增 `agent/reminder_store.py`（原子写 / 逐条校验 / 条数上限 / 失败降级为纯内存 / 不落用户数据区）+ `agent/store_guard.py`（把原先在 `tracker_store` 与 `recall_store` 各写一遍的安全守卫抽成唯一事实来源）；`ReminderTool` 接 `store`，`execute`/`cancel`/`due_now` 后落盘、启动时 `restore()`，且**恢复早于调度器启动**（有用例钉住顺序）。`tools/verify_f7_real_services.py` 新增 F7-e：**三个独立进程** add/list/clear 真实验证"重启不丢"（F7 断言 27 → 32） |
| D14 | **两个不兼容的 EventBus 并存** | `core/event_bus.py`（枚举 + `subscribe/emit(EventType, dict)`，handler 约定未验证）与 `core/kernel/events.py`（字符串 + `on/off/emit(str, source="", **data)`，handler 收**一个 `Event` 位置参数**）同名不同契约 | 极易再次接错 —— 本轮修掉的第一个缺陷就是"把前者传给了只认后者的 Agent 层"，代价是 Agent 层**自 P0 起在真实应用里全程静默降级**，而所有脚本级验收都测不出来 | ⬜ **终态：守护已交付 + 适配层明确不做（P5-A3，2026-09-11）**。① 守护已交付（P4-B1）：`agent/bootstrap.py::assert_kernel_bus` 接在 `build_agent_stack()` 第 0 步，契约不符立刻 `TypeError` 并点名两个类；`tests/agent/test_eventbus_contract.py` 12 项双向覆盖；`core/app.py::_setup_agent_layer` 用的就是内核总线，`tests/test_app.py:229` 断言 `app.agent_bus` 是内核总线类型 —— **契约一致这一条已经成立**。② **适配层不做**，3 条理由：（a）旧总线在 Agent 层路径上**已经没有使用者**，统一它属于**架构重构**而非闭环必需项；（b）`p4-plan.md` §六 把"是否接受该改动面"列为**人工决定**，用户已选择保留守护；（c）收益（消除一个不再被误用的类）< 风险（它会碰受 G12 保护的语音路径）。⚠️ **注意措辞边界**：这不等于"两个总线已统一"—— **旧类 `core.event_bus.EventBus` 仍然存在，并且仍然在服务语音层**，只是 Agent 层不再用它、且接错会立刻报错 |
| ~~D15~~ | ~~仓库无版本控制~~ | — | — | ✅ **已偿还（P4-A1）**：悬空 `.git` 指针文件 → `git init -b main`，7 个提交；`.gitignore` 补 `models/`（85MB ckpt）、`data/` 整目录、`.coverage`；新增 `tools/check_repo_hygiene.py` 并接入为**验收关卡 G13**（内部关卡 6 → 7）——它专门防"再次失去版本控制""运行时数据库混进历史""密钥被写进 git 历史" |
| **D16** | **两种"llm schema"同名不同形制** | `LLMRouter.TOOL_SCHEMAS` 是**扁平** `{name,description,parameters}`；`ToolRegistry.to_llm_schemas()`（经 `BaseTool.to_llm_schema()`）返回**已包好**的 `{"type":"function","function":{…}}`；而 `chat_with_tools` 原先无条件再包一层 | 与 D14 同族：把注册表 schema 递给 `chat_with_tools` → 请求体里**根本没有 `function.name`** → 端点 400/空 tool_calls → 插件返回 `None` → `route_with_tools` 把 `None` 读成"模型这次用文字回答" ⇒ **整条 LLM 路由兜底静默失效**。P4-B4 的矩阵探针正是这么踩的（**六个免费模型的第 ③ 项被误判成"全都不支持 function calling"** —— 错在测量侧） | ✅ **已偿还（P5-B1，2026-09-11）**：新增唯一收敛点 **`plugins/llm/tool_schemas.py`**，`openai_api` 与 `openrouter` **两个插件都走它**。① **两种形制都收**（剥掉多余层，不靠巧合让 `function.name` 恰好存在）；② **畸形显式报错**（`ToolSchemaError`，继承 `ValueError`）—— 缺名字/空名字/两层名字冲突/形制混用**一律整批拒绝**，绝不静默变出"没有名字的工具"（空名字正是静默失败的载体）；③ **形制错误必须穿透宽口 `except Exception`**（两个插件都加了 `except ToolSchemaError: raise`，否则它退化成 `return None`，接错又变回静默失效）；④ `core/app.py::route_with_tools` 单独 `except ToolSchemaError` 按 **ERROR** 级点名 D16。守护用例 `tests/test_tool_schema_shape.py` **26 项**，含**抓真实出网报文**（断言 `function.name` 真的是名字、且没有再嵌一层）。**反方向验证过不是空转**：把原始写法塞回去 → 守卫变红；拿掉 `except ToolSchemaError: raise` → 守卫变红（两次都留档 `evidence/p5/d16_schema_shape.txt`） |
| **D17** | **`LLMPlanner._validated_steps` 只校验工具名、不校验参数类型** | 计划校验止于"工具名在表内" | LLM 产出的参数类型常错（实测 `pattern` 给列表、`file_move.source` 给列表）。这类计划能"成功拆出来"，却必然在执行期被 `validate_params` 拦下 → 用户听到"这个指令我还没完全理解"。与 P4-B2 同族的**上游成因** | ✅ **已偿还（P5-A2，2026-09-11）**：判据抽成模块级 `agent/tools/base.py::describe_value_problem`，`BaseTool.validate_params` 与 `LLMPlanner._validated_steps` **共用同一份**（不另写第二份 —— 本项目吃过"复刻判据"的亏）。规划期现在查三类：①工具名在表内 ②参数类型合工具真实 schema ③`${sN}` 引用**不指向被丢弃/不存在的步骤**。不符则丢该步 + `WARNING` 说明"哪个参数、期望什么、收到什么"。取向与执行期一致：**未声明字段放行**、**显式 `None` 视为未提供**（F 系列缺陷 4 回归保护）；畸形 schema（`spec` 不是 dict）一律放行 —— 不确定时不假装能判。证据 `docs/agent/evidence/p5/d17_plan_param_validation.txt` |
| **D18** | **`file_delete` 把 `targets` 里的非字符串项 `str()` 之后照删** | `_collect_targets` 原写 `[str(t) for t in explicit if t]` | `targets=[["C:\\a.txt"]]` → 字符串 `"['C:\\\\a.txt']"` —— 一个**看起来像列表的路径**。危险不在"通常会删不到"（那种假路径落在白名单外，用户听到的是「删除失败了，可能是权限不够呢」，**真正原因永不出现**，排查被引到权限上），而在"**位置决定后果**"：若 `str(t)` 恰好拼出一个白名单内且真实存在的名字，它就会安静地删掉那个文件 | ✅ **已修（P5-A2 顺带，2026-09-11）**：逐项必须是 `str`，否则整条拒绝并说明**真正的原因是输入畸形**。`preview`/`execute` 结论一致（都拒绝）；空串/`None` 这类"空项"仍按缺省处理（只收紧非字符串，没顺手改空值语义）。5 个参数化用例 + 2 条反方向 + 1 条"错误里不许出现「权限」"。证据 `docs/agent/evidence/p5/d18_items_probe.txt`（含**沙箱内真实探查**：三种畸形输入各自 `validate_params`/`preview`/`_collect_targets` 的原文输出） |

| **D19** | **`vad_filter=True` 在部分长音频上静默截断** | `plugins/asr/faster_whisper/plugin.py:105` 硬编码 `vad_filter=True`（**不可配置**），VAD 把音频后半段判成噪音直接切掉 | 用户说一句长指令，产品**只听前半句**就去执行 —— 而且**看不出来**：残缺文本与原句前缀天然相似，按相似度判据甚至可能比完整版「更高分」。实测 30 次调用命中 1 次（medium、8.26s 音频只覆盖 3.03s = 39%）| 🟡 **终态：已实测定性，默认值处置待人工（P5-B4，2026-09-11）**。① **已测**：`evidence/p5/asr_long_sentence.txt` 第二~八节记录完整归因链（先排除合成截断与静音，再定位到 VAD，判据是「`vad=on` 片段末点 < 音频时长−1.0s **且** `vad=off` 末点 ≥ 音频时长−1.0s」）。② **为什么不直接改默认值**：实测关掉 VAD 后 `small` 会在低幅白噪上幻觉出「字幕by索兰娅」（原本为空）—— 改 `False` 是把「长句截断」换成「噪音被听成话」，两者都是用户可见故障。③ **最小操作路径（人工）**：用**真实房间噪声**录音跑一遍 vad on/off 对照（合成噪音不能替代，本机无此录音），据此决定是 (a) 保留 VAD 但加「转写覆盖率异常」告警、(b) 把 `vad_filter` 提为可配置项并在配置里显式选、还是 (c) 维持现状。④ **可判定判据**：真实噪声下 vad=off 的**误听率**与 vad=on 的**截断率**两个数字同时给出，才能做取舍 —— 只报一个不够。⑤ **措辞边界**：这是**低频**缺陷（1/30 样本），**不足以给出发生率**，只能说「确实会发生」；且它**与 small/medium 档位无关**（两档共用该参数）|

| **D21** | **`ConfigManager.set()` 会把「整份内存配置」落盘，含合并进来的默认值与任何程序化改动** | `config_manager.py:204` 的 `set()` 末尾无条件 `self._save_config()`，而 `_save_config()` 是 `yaml.dump(self.config)` **整份写**。于是：① 任何**内存里被改过**的值都会在下一次 `set()` 被顺手持久化；② `_merge_config()` 补进来的默认键也被写进文件；③ **注释全被 `yaml.dump` 吃掉**（本项目 `config.yaml` 的注释是给人看的重要信息）。触发点现成就有：`core/app.py:147` 启动时必调 `set("system.perf_evaluated", True)` | 我在改 `tests/test_app.py` 时**实测踩中**：测试为钉住 `ui.pet_sprite` 而直接改了内存字典，随后 `app.run()` 里那句 `set(...)` 把测试值 **`unit_pet` 写进了真实 `config.yaml`**，并且整个文件被重排、注释丢失。这与 P5-C1 轮次那次「配置被打坏」同族（都是"配置被静默回写"），但机理不同：那次是**写坏了 YAML 语法**导致回退默认值，这次是**语法合法但内容被污染** —— 更隐蔽，因为程序照常能跑，只有盯着文件才看得出 | 🟡 **本轮已做两件事，根因未修（登记见下）**：① **测试侧堵住**：`test_run_injects_app_and_loads_pet` 改为用 `monkeypatch.setattr(ConfigManager, "get", ...)` 拦读，不碰内存字典；并加了「改完比对 MD5，确认 `config.yaml` 未被改动」的现场核对（**实测通过**）。② **`config.yaml` 已恢复**（219 行，6 个顶层键、密钥长度、`path_whitelist`、`agent.plugins` 13 项逐条核对）。**根因未修的理由**：把 `set()` 改成"只改内存、显式 `save()` 才落盘"是**行为变更**，而 `set()` 的现有调用点（`ui/pet_window.py` 四处菜单开关、`core/app.py` 一处）目前都**依赖"改完即持久化"**才在重启后保留 —— 直接改会让这些开关变得重启即失效。正确的做法是**先给调用点补上显式 `save()`**，再收窄 `set()`；那是独立一轮的改动面，不塞进本轮。判据：改完后「切换字幕开关 → 重启 → 仍生效」且「测试跑完 `config.yaml` 的 MD5 不变」两条同时成立。另外 `.gitignore` 已补 `config.yaml.before_*` / `config.yaml.manual_backup`（**这些备份含明文密钥**，而 G13 的密钥扫描只管已跟踪内容，拦不住"即将被 `git add -A` 加进去"的文件） |
| **D22** | **`ui.render_mode` 这个配置项从未被读取** | `PetWindow.render_mode` 在 `ui/pet_window.py:124` **硬编码为 `"sprite"`**，全仓再无一处读 `ui.render_mode`；而 `core/app.py` 启动时**无条件**调 `enable_vrm()`。于是配置里写 `vrm` 只是"碰巧因为无条件调用而生效"，写 `sprite` 也**关不掉** VRM —— 配置与行为不一致 | 用户把 `ui.render_mode` 设成 `sprite` 却仍然显示 3D 模型时，**没有任何报错**，只能靠读代码发现；反过来想"只换精灵图不换渲染模式"也无从表达。属本项目反复出现的同一族：**配置项看起来存在、实际不接线** | ✅ **已修（本轮）**：`core/app.py` 改为按 `ui.render_mode` 决定是否 `enable_vrm()`，非 `vrm` 时打印「渲染模式: sprite（配置 ui.render_mode=…）」；`core/config_manager.DEFAULT_CONFIG` 补上 `ui.render_mode`（默认 `sprite`，与硬编码原值一致）与 `ui.pet_sprite`（默认 `cat`）。**真机验证**：`tools/verify_gui_launch.py` **16/16**，日志实测输出 `精灵图角色: xinya` / `渲染模式: sprite（配置 ui.render_mode=sprite）` / `加载宠物: xinya, 10个动画` |
| **D23** | **宠物角色名写死在 `core/app.py`：`pet_window.load_pet("cat")`** | 与 D22 同族，同一行代码的另一个后果 | 换角色必须改代码、重跑测试；而 `tests/test_app.py:81` 又把这个字符串**断言死了**（`win.loaded == "cat"`），于是"换角色"这件事在测试层面被永久锁住 —— 本轮把配置改成 `xinya` 时，这条用例立刻变红（**它红得对，是断言本身在拦**） | ✅ **已修（本轮）**：`core/app.py` 改读 `ui.pet_sprite`；`tests/test_app.py` 那条断言由"写死 `cat`"改为"钉一个测试值再断言读到它"，从**硬编码耦合**变成**验证读配置**（顺带避开 D21 的落盘陷阱）。现场核对：`config.yaml` 在测试前后 **MD5 不变** |

| **D24** | **`apply_settings` 把窗口写死成正方形：`setFixedSize(size+100, size+100)`** | `ui/pet_window.py` 里 `load_pet` 是**按精灵画布**算窗口（`size[0]+100, size[1]+100`），而保存设置走的另一条路径却把两个方向都写死成 `pet_size+100`。方形画布（cat / 半身像 128×128）下两条路径碰巧一致，所以一直没暴露；换成**非方形画布**（全身立绘 128×289）立刻分裂 | 触发场景是"打开设置→保存"：窗口被改成 227×227，而画布高 289 ⇒ ① 角色从 y=0 画起，**脚被窗口底裁掉**；② 44px 字幕条落在 y[186..221]，**重新压回角色身上** —— 正是 `_relayout_pet` 修掉的那个现象，从另一条路径原样复发。属本项目反复出现的那一族：**同一件事有两个算法，只有一个被改** | ✅ **已修（本轮）**：改为按**精灵自身宽高比**算高（`ui.pet_size` 调的是画布宽，高跟比例走），方形画布结果不变（仍是 227×227）。`tests/test_pet_window.py` 新增 2 条：`test_apply_settings_keeps_sprite_aspect_for_tall_canvas`（判据用结构不变量「画布整体在字幕区之上」，不看像素）+ `test_apply_settings_square_sprite_unchanged`（反方向保护：cat 必须还是 227×227）。**反方向验证过**：把 `setFixedSize(size+100, size+100)` 塞回去 → 新用例变红并报出 `assert 227 == 387`，方形那条仍绿（说明红的是对的那条） |
| **D25** | **桌宠立绘的来源只存在于 `.dsh/attachments/` 会话缓存里** | `_tmp_make_frames2.py` 的 profile 直接把 `src` 写成附件缓存路径。那是**会话级**目录：会话一清，`xinya` / `xinya2` 两个 profile 都再也跑不起来 —— 而仓库里存着它们的**产物**（`resources/sprites/xinya*/`），下一个想改角色的人只能对着一堆帧文件猜当初是怎么生成的 | "能跑，但只在那台机器那天" | ✅ **已修（本轮）**：立绘在仓库内留副本 `resources/source/avatar_source.webp`，`tools/make_pet_sprites.py` **仓库内副本优先、附件路径兜底**。现场核对：用仓库内副本 `--rebuild-base` 重建底图后重新生成，**208/208 帧与磁盘上逐字节相同** —— 既证明来源等价，也证明整条链是确定的 |
| **D26** | **麦克风"自动挑选"在多声源环境下选错设备，且用户无从纠正** | `MicrophoneService._pick_mic_index` 排除回环类设备后**取第一个**，并把结果写进 INFO 日志就结束了。多声源是**常态**而非例外：本机实测有 **14 个输入设备**（本机 Realtek、UU远程虚拟麦克风×3、声音映射器、立体声混音、扬声器回环…）。远程桌面场景下"第一个真实麦克风"恰是**收不到用户声音的那个**，而正确设备（远程虚拟麦克风）排在其后 | 用户感知是"**她听不见我说话**"，但**所有日志都显示正常**：麦克风有触发、ASR 有输出、Agent 有回复、TTS 有播放。真相是录进来的是**平坦底噪**（包络动态范围 1.4x，语音应 >6x），Whisper 只能拿 `initial_prompt` 里的词硬编 —— 于是"取消 确认""字幕by索兰娅"这类**幻觉文本**被当成用户指令送进管线。**最危险的不是听不见，是听见了错的东西**：一个假"取消"会让 Agent 真的去取消任务 | ✅ **已修（本轮）**：① `list_input_devices()` 枚举并区分 `mic`/`loopback`（回环收到的是系统播放声，不是人声）；② `find_device_by_name()` 按名字片段匹配，**同名时优先非回环**；③ `probe_device_levels()` 逐设备短采样测音量（串行 —— 并行开多个 PyAudio 流会触发 **0xC0000005 访问违例**，已实测踩到）；④ 新增 `voice.mic_device` 配置（null=自动 / 数字=索引 / 字符串=名字片段），`_pick_mic_index` 三级优先级；⑤ 新增 `tools/list_mic_devices.py`（`--probe` 试听找出有声音的设备 / `--set` 写回配置）；⑥ 新增热键 `Ctrl+Alt+N` 运行时轮换设备，**切换即重启监听，无需重启应用**；⑦ 每次 `start_voice_monitor` 都重读配置，用户改完配置不必重启进程。**守护用例**：`tests/test_microphone_listen.py` 新增 11 项（含"自动挑选必须跳过回环""名字匹配优先非回环""无效配置回退自动挑选且不崩"），全套 17 passed。**反方向验证过**：假设备表刻意把回环排在索引 0，自动挑选若退化为"取第一个"会立刻选中它 → 用例变红 |

**债务管理原则：**
- 每项债务必须登记，不允许"隐性债务"
- P0 阶段允许产生债务，但必须记录偿还计划
- 每个 Phase 结束时回顾债务清单，至少偿还 1 项

### P5 完全闭环（2026-09-11 立项）· P4 历史见 `docs/agent/p4-history.md`

> P4 的立项背景、**7 处"检查本身出错"**、**3 处"纸面满足"**、验收轮的
> **4 个假绿/假红（19~22）** 明细已移到 **`docs/agent/p4-history.md`**
> （原因同 §11~§14 → `phases.md`：权威文档有 65536 字节预算，超了会**静默截断**）。

**P5 的目标不是"100% 完成"，而是"每条登记项都有终态"** —— 四种，没有第五种：

| 终态 | 判定要求 |
|------|---------|
| ✅ 已修 | 有代码改动 + 有用例 + 过门禁 |
| ✅ 已实测 | 有原始输出留档，结论明确（**允许结论是"不可为"**） |
| ⬜ 明确不做 | 写明理由，且理由能被反驳 |
| 🟡 待人工 | 附**最小操作路径** + **可判定判据** |

计划 `docs/agent/p5-closure-plan.md`；任务 `docs/agent/tasks-p5.json`（**18 项**，P0 = 3 项）。

| 编号 | 项 | 状态 | 证据 |
|------|----|------|------|
| P5-A1 | 账本不再说谎（`f-closure.md` §17.4 的 7 处 + 完成度连口径） | ✅ 完成 | `f-closure.md` §17.4.1 复核段；本节"完成度" |
| P5-A2 | D17 计划参数类型校验（+ 顺带修掉 D18） | ✅ 完成 | `evidence/p5/d17_plan_param_validation.txt`、`d18_items_probe.txt` |
| P5-A3 | D14 适配层终态登记（明确不做 + 3 条理由） | ✅ 完成 | 债务表 D14 行 |
| P5-AUDIT | G13 证据检查改为自动发现 `tasks*.json`（原写死四个文件名 ⇒ 新阶段悄悄不查） | ✅ 完成 | `evidence/p5/g13_hygiene.txt`（18 项 → 20 项） |
| P5-AUDIT-CLOSURE | 收口审计：逐条列终态 + 证据是否存在 + 非终态措辞扫描 + 关卡原样复跑 | ✅ 完成 | `evidence/p5/closure_audit.txt`（**可复跑**） |
| P5-B1 | D16 工具 schema 形制归一（收敛点 + 显式拒绝匿名工具） | ✅ 完成 | `evidence/p5/d16_schema_shape.txt`（26 项守卫 + **反方向验证**） |
| P5-B2 | voice_service 加载 ASR/TTS 时透传 params（取参数判据收敛到一处） | ✅ 完成 | `evidence/p5/voice_service_params.txt`（含**反方向验证**） |
| P5-B3 | 真 ST + 真 vec0 端到端（含维度变化时索引被重建） | ✅ 完成 | `evidence/p5/st_vec0_e2e.txt`（含 **vec0 建表 SQL 原文**这一硬证据） |
| P5-B5 | C4 解析器离线 fixture（必应/百度/谷歌各一份，取自真实抓取字节） | ✅ 完成 | `evidence/p5/serp_parser.txt` + `serp_parser_raw_probes.txt`（8 份 fixture 带 MD5，**并纠正了 docstring 里一句被我实测证伪的说法**） |
| P5-B4 | ASR medium/small 长句对比（补 C2 只测短句的缺口） | ✅ 完成 | `evidence/p5/asr_long_sentence.txt`（8 支探针的完整归因链 + **顺带查出的 D19**） |
| P5-C1~C2 | 轮换 key / 人工验收 M1~M5 | 🟡 待人工 | 见 §六 末"能力边界" |
| P5-C3~C8 | 6 项明确不做（D3 / 天气 IP / D7 / 繁简边界 / 诱饵页 / 历史账本） | ⬜ 已登记 | 各附可反驳理由 |

> **"未到终态"必须写明它是什么，不许用"可选增强"给没做的事开脱**：
> P5-B1~B5 在 `p5-closure-plan.md` §2.2/§2.3 里是**已登记的清单项**
> （B1/B2 有代码可改、B3~B5 从未实测），所以**它们算闭环缺口，是我欠的活儿**。
> 写这段时我先给它们贴了"可选增强、不构成缺口"——那是**把没做完说成不用做**，
> 与本项目禁的那类事同族，已改回：**必须做完才算闭环**。
> 账本上它们的证据走 `planned_evidence`（G13 不查），一旦落档要挪进 `evidence`——
> 这个动作就是"完成"的可检查定义。

**口径说明（不许把 13 关整体当"工程完成度"）**：`run_acceptance.py` 的 13 关里，
G1~G7 + G13 是内部关卡（7 项），G8~G12 是外部依赖关卡（6 项，退出码 0 但 G8
可能因配额报"跳过"——按设计 G8 有跳过时**不算通过**）；覆盖率的"100%"只覆盖
`core/kernel + agent + services.ack_cache`（**7238 语句**），`ui/` / `plugins/` /
`core/app.py` / 大部分 `services/` **不在口径内**。任何"工程 X%"的说法都必须带上分母。

---


## 七、验收标准总览

### 7.1 P0 验收（必须全部通过）

| 指标 | 目标值 | 测量方式 |
|------|--------|---------|
| 规则路由准确率 | >90% | 50 条测试指令集 |
| 规则路由延迟 | <10ms | 单元测试计时 |
| 感知延迟（首次反馈） | <1.5s | 端到端计时 |
| 总延迟（结果播报） | <3s | 端到端计时 |
| 误操作率 | 0 | 审计日志 + 白名单测试 |
| 删除可恢复率 | 100% | 回收站验证 |
| 工具执行成功率 | >95% | 执行成功/总执行 |
| 内核测试覆盖率 | >90% | pytest --cov |
| 现有功能回归 | 0 失败 | 全量 pytest |
| 插件可卸载 | 是 | 卸载后服务不可用测试 |

### 7.2 证据分级

| 优先级 | 要求证据 |
|--------|---------|
| **P0** | pytest 测试报告 + 手动验证截图/录屏 |
| **P1** | pytest 测试报告 或 手动验证截图 |
| **P2** | 截图或录屏 |

---

## 八、任务执行协议

### 8.1 任务状态机

```
pending ──▶ in_progress ──▶ verifying ──▶ done
                │                │
                └────────────────┴──▶ blocked（需人工介入）
```

### 8.2 任务完成检查清单（每个任务必须逐项确认）

```markdown
- [ ] 代码文件已创建/修改
- [ ] 语法检查通过（python -m py_compile）
- [ ] 单元测试已编写
- [ ] 单元测试通过（pytest -v）
- [ ] 全量回归通过（pytest tests/）
- [ ] 手动验证关键流程（如适用）
- [ ] 文档已更新（如接口变更）
- [ ] AGENTS.md 已同步（如架构变更）
```

### 8.3 阻塞上报协议

遇到以下情况必须停止并上报，不得自行决策：

1. 需要超出已确认决策范围的架构变更
2. 需要引入未登记的新依赖
3. 现有测试出现非预期失败
4. 安全边界需要放宽
5. 单个任务耗时超过预估 3 倍

### 8.4 并行执行规则

```
可并行：
  ├── 无依赖关系的任务（如 T1.1 和 T2.1）
  ├── 独立模块的测试编写
  └── 文档编写

必须串行：
  ├── 有 depends_on 关系的任务
  ├── 修改同一文件的任务
  └── 集成阶段任务
```

---

## 九、配置约定

> **单一事实来源是 `config.yaml`**。本节原先手抄了一份完整 YAML，但它在实现推进后
> 已经落后（例如列过一个并不存在的 `agent.kernel.plugin_config`），留着反而误导。
> 下面只列**键的层级与语义**，取值一律以 `config.yaml` 为准。

```yaml
ui:                   # 桌宠外观（2026-09-11 补 —— 本节原先完全没写 ui，
                      #   这正是 D22「ui.render_mode 从未被读取」能藏那么久的原因：
                      #   文档里没有这个键，就没人去核对它到底接没接线）
  render_mode:        # sprite（2D 序列帧）| vrm（3D 模型）。**真的接线了**（D22 已修）
  pet_sprite:         # 精灵图角色目录名 → resources/sprites/<这个名字>/
                      #   换角色 = 改这一项，不用改代码（D23 已修）
  pet_size:           # 宠物窗口边长（px）。128 的画布在 127 下接近 1:1，不缩放
  vrm_model:          # render_mode=vrm 时的模型路径
  vrm_yaw_deg:        # 面向镜头的修正角；VRM 0.x 正面朝 +Z、1.0 朝 −Z（见 vrm_facing.txt）
  always_on_top / subtitle_enabled / subtitle_bg_color / subtitle_fg_color
  position.x / position.y / fps.{low,medium,high,current}

agent:
  enabled:            # 总开关（false 时全部转闲聊，走原语音管线）
  plugins:            # 插件清单：决定加载哪些插件、以什么顺序（见 §15 与 phases.md §14）

  router:
    provider:         # rule | llm | hybrid
    rule.confidence_threshold:
    llm.enabled / llm.timeout:

  safety:
    provider:         # basic
    path_whitelist:   # 硬性白名单，不可配置放宽（空 = 用默认四目录）
    audit_db / audit_enabled / audit_retention_days:
    remember_choices: # 记住用户确认选择（开启后同目录同类操作免二次确认）
    confirm_timeout:

  executor:
    pool_size / timeout:

  summarizer:
    provider:         # template | llm | hybrid
    template_threshold:
    async_refine:     # true = 先播模板、LLM 润色后补播（P2-2）

  planner:            # P3 / D5
    enabled:
    provider:         # template | llm | hybrid
    max_steps:

  memory:             # P3
    enabled:
    embedder:         # hashing（默认，离线可用）| st（需模型文件）
    st_model:
    dim:              # hashing 嵌入维度
    store:            # SQLite 路径（默认 ./data/agent_memory.db）
    vector_backend:   # auto | python | sqlite_vec
    min_similarity:
    max_items:        # 超限后按热度淘汰
    episodes:         # 是否记录情景记忆
    hints:            # 是否给路由提供偏好提示

  pipeline:
    ack_enabled / ack_warmup / ack_lru_size:
    tracker_persist / tracker_store:
```

**硬性约束**（写在守卫里，配置改不动）：删除强制走回收站、**无永久删除分支**；
任何路径操作前必须过白名单。

---

## 十、参考资源

| 资源 | 用途 |
|------|------|
| `README.md` | 项目总体架构说明与路线图 |
| `config.yaml` | **配置的单一事实来源** |
| `docs/agent/spec.md` | 逐功能详细规格 |
| `docs/agent/acceptance.md` | 逐功能验收测试用例 |
| `docs/agent/phases.md` | **历史阶段实施记录**（P0 / P1 / P2 / F2 细节） |
| `docs/agent/acceptance-report-p1.md` | P1 系统性验收报告（含开放发现 F1~F8） |
| `docs/agent/evidence/` | P1 证据归档（崩溃转储、覆盖率输出等） |
| `docs/agent/tasks.json` | P0 任务清单（供编排系统消费） |
| `docs/agent/tasks-p2.json` / `tasks-p3.json` / **`tasks-p4.json`** | P2 / P3 / **P4** 任务分解 |
| **`docs/agent/p4-plan.md`** | **P4 计划**：未完成项与已发现问题、P0/P1/P2 分级、风险（技术/业务/运营）、逐项代码化验收标准、能力边界、关卡与监督机制 |
| `tools/p1_acceptance_smoke.py` / `p3_acceptance_smoke.py` | 端到端验收脚本 |
| `tools/measure_acceptance_metrics.py` | 指标测量脚本 |
| DSH 架构文档 | 插件设计参考（`D:\DeepSeekHNS\docs\architecture.zh.md`） |

---

**文档版本：** v3.2（P5 完全闭环；历史阶段见 `phases.md` / `p3-history.md` / `f-closure.md` / `p4-history.md`）
**下次回顾：** P5 收口结论被推翻时；或"待人工"3 项（P5-C1 / C2 / D19）被推进时
> 上一版这里写的是「P4 立项时」—— P4 早已结束，这行**已经说谎很久了**。
> 与 P5-A1 修的那 7 处同族：账本上的"下次回顾"也要指向真实的下一个动作。


## 十一、历史阶段索引（P0 / P1 / P2 / F2）

> 逐阶段细节已移到 **`docs/agent/phases.md`**（信息完整保留，只是换位置 —— 见该文件开头的说明）。
>
> 编号刻意从 §11 跳到 §15：`phases.md` 沿用 §11~§14 的原编号，
> 本文件的 §15 / §16 也是原编号 —— 全项目的交叉引用（含源码注释）因此不用改。

| 阶段 | 主题 | 关键结论 | 细节 |
|------|------|---------|------|
| **P0** | 内核 + 管线 + 路由/安全 + 文件工具 | 规则路由准确率 100% / P95 0.25ms；内核覆盖率 99%；669 passed；偿还 D7/D8 | `phases.md` §11 |
| **P1** | LLM 路由 / 实体追踪 / 6 类工具集 / 摘要器 | 18 个工具；覆盖率 100%（4078 语句）；1464 passed；端到端 18/18；**G3 有条件通过**（D7 间歇性崩溃） | `phases.md` §12 + `docs/agent/acceptance-report-p1.md` |
| **P2** | 技术债偿还：D2 确认语 LRU / D4 摘要器异步化 / D1 指代消解持久化 | 结果播报从 ~305ms 降到 **3ms**；覆盖率 100%（4366 语句）；1579 passed | `phases.md` §13 |
| **F2** | 插件加载器真正接入运行时（偿还 D12，P1 收尾） | `build_agent_stack` 不再 new 任何 Provider；10 个插件配置驱动；1617 passed | `phases.md` §14 |
| **P3** | 多步任务规划（D5）+ 长期记忆 | 覆盖率 100%（6602 语句）；2139 passed；端到端 43/43 | **`p3-history.md` §15 / §16** |

**P1 遗留的开放项**（不属后续阶段范围，需真机与人）：

| # | 条件 | 状态 |
|---|------|------|
| 1 | 修复或规避 D7 间歇性崩溃 | ✅ 修后累计 28 轮 0 次（见 §16.3） |
| 2 | 5 项人工语音验证（搜索 / 删除确认 / 打断 / 降级路径 / 安全边界） | 🟡 **已自动回环验证**（TTS→ASR→真实管线，见 §17）。**麦克风硬件采集仍需人** |
| 3 | 真实环境启动一次应用（完整 GUI 内验证 Agent 层） | ✅ **已完成**：`tools/verify_gui_launch.py` 16/16，退出码 0（见 §17.2） |
| 4 | 真实 LLM API 跑通 LLM 路由与润色 | ✅ **已完成**：`tools/verify_f3_real_llm.py` **20/20**（另有配额受限的轮次报 `16/16 + 2 跳过`，见 §17 验收阶段一节） |
| 5 | loader 接入 `build_agent_stack` | ✅ 已完成（F2） |
| 6 | 修正 `tasks.json` 状态与 `acceptance.md` 文件名漂移 | ✅ 已完成（F2） |
| 7 | F4 / F5 / F7（浏览器联网、系统工具真机执行、真实服务） | ✅ **已完成**：35/35 与 27/27（见 §17.2） |

## 十五、P3 实施进度 · 十六、P3 验收关卡

> **这两节已整段搬到 `docs/agent/p3-history.md`**（原文一字未删，只换位置）。
> 原因同 §11~§14 → `phases.md`：`AGENTS.md` 有 **65536 字节**的工作区指令
> 硬预算，超了会被**静默截断** —— 本轮实测触发过一次（加完 D19 后 66697 字节，
> 被截掉 1161 字节）。权威文档被悄悄截尾，比内容长本身危险得多。

**P3 一句话结论**：交付了 D5 多步任务规划与长期记忆两大块；
全量回归 2139 passed；覆盖率 100%（6602 语句）；端到端 43/43。
**逐项细节、关卡表、`# pragma: no cover` 清单的两次勘误、P3 期间修掉的 6 个缺陷
（含 `EventBus.emit` 的 `source` 吞负载字段、`_extract_dest` 丢掉用户显式路径）
全部见 `docs/agent/p3-history.md`。**

> ⚠️ 注意：`# pragma: no cover` 的**当前**总数与逐处理由也维护在那里，
> 但它属**当前约定**，任何新增/删除屏蔽必须同步回本节所在的文档体系 ——
> 现在的权威口径是：**5 处，每处都必须写出结构上不可达的理由**。

## 十七、F 系列开放项收尾（摘要）

> **明细已移到 `docs/agent/f-closure.md`**（原 §17 全文，信息完整保留）。
> 移出去的原因：它让本文件超过了 65536 字节的工作区指令预算，触发**静默截断** ——
> 权威文档被悄悄截尾比内容长更危险。处理方式与 §11~§14 → `phases.md` 一致。

一句话结论：**逐项跑真实服务、真实操作系统、真实 GUI，抓出 22 个"纸面已交付、
实际不工作 / 静默做错事 / 假绿 / 假红"的缺陷，其中 19 个修掉、3 类如实登记待下轮**
（收列表参数的宽松转换、D13 提醒持久化、D14 两个 EventBus；另有若干"外部条件不具备"
的项在 §17.4 逐条写明缺什么）。

## 完成度（连口径 —— 不许给没有分母的百分比）

**先说结论：全工程没有一个可信的百分比，因为"工程"没有分母。**
所以这里只报**有分母的那几个**，以及**为什么剩下的算不出来**。

| 口径 | 分母 | 已完成 | 说明 |
|------|------|--------|------|
| 覆盖率（唯一有硬分母的） | `core/kernel + agent + services.ack_cache` 共 **7238 语句** | **7238 / 7238 = 100%** | `ui/` / `plugins/` / `core/app.py` / 大部分 `services/` **不在口径内** —— 别把这个 100% 读成"工程 100%" |
| 验收关卡（内部） | G1~G7 + G13 = **7 关** | **7 / 7** | 每关都有原始输出 |
| 验收关卡（外部依赖） | G8~G12 = **6 关** | **6 / 6 退出码 0** | ⚠️ G8 依赖免费配额，**有跳过项时按设计不算通过**，报告里单独标出 |
| P5 登记项 | **18 项** | 见下表 | 闭环判据不是"做完"，而是"每项都有终态" |
| 测试用例 | 全量 `pytest tests` | **2920 passed / 1 skipped / 0 failed**（`test_run_injects_app_and_loads_pet` 在**欣雅正在运行**时必红 —— 单实例锁按设计拦下 `app.run()`，已用 `git stash` 实测确认该失败在 HEAD 上完全相同，属环境互斥而非回归；实例关闭后即通过）。2779 → 2920 = D27~D35 新增 | skipped 的那 1 项是环境相关，不是"忽略失败" |

**P5 登记项逐条终态**（四种终态，没有第五种；机器可读的账本在 `tasks-p5.json`）：

| 终态 | 项数 | 具体 |
|------|------|------|
| ✅ 已修 | 4 | P5-A2（D17 + 顺带 D18）、P5-AUDIT（G13 清单改自动发现）、P5-B1（D16 形制归一）、P5-B2（voice_service 传参） |
| ✅ 已完成 | 3 | P5-A1（账本刷新 + 完成度）、P5-A3（D14 终态登记）、P5-AUDIT-CLOSURE（收口审计） |
| ✅ 已实测 | 3 | P5-B3（真 ST + 真 vec0 端到端，含维度变化时索引重建的**建表 SQL 原文**）、P5-B4（ASR 长短句对比，**并查出 D19**）、P5-B5（8 份真实 SERP 原文做成离线 fixture，含 MD5 判据与**版本控制层面的字节保全**） |
| ⬜ 明确不做 | 6 | P5-C3 D3 / C4 天气 IP / C5 D7 / C6 繁简边界 / C7 诱饵页 / C8 历史账本 |
| 🟡 待人工 | 2 | P5-C1 轮换 key、P5-C2 麦克风+GUI+人耳验收（各附最小操作路径与判据） |
| ⏳ 未到终态（**闭环缺口**） | 0 | 无 —— 全部到终态 |

> **债务表也要连口径**（上面那张表只覆盖 P5 的 18 项登记项；
> 技术债是另一条线，容易漏读）：
>
> | 债务 | 终态 |
> |------|------|
> | D1~D15（除 D3 / D7 / D14） | ✅ 已偿还 |
> | D3 单用户假设 / D7 间歇崩溃 / D14 双 EventBus | ⬜ 明确不做或已有效消除（见各行） |
> | D16 形制归一 / D17 计划参数校验 / D18 非字符串 targets | ✅ 已修 |
> | **D19 `vad_filter` 静默截断** | 🟡 **待人工**（默认值取舍需真实噪声录音） |
> | **D20 验收脚本把配额报成产品失败** | ✅ **已修**（P5-C1 轮次发现的） |
> | **D21 `ConfigManager.set()` 整份落盘** | 🟡 **待人工/下轮**（测试侧已堵，根因需连同调用点一起改） |
> | **D22 `ui.render_mode` 从未被读取** / **D23 角色名写死** | ✅ **已修**（本轮接线 + 真机 16/16） |
> | **D24 `apply_settings` 写死正方形窗口** | ✅ **已修（换全身立绘那一轮）**：非方形画布下"保存设置"会把脚裁掉并让字幕压回角色；2 条新用例 + 反方向验证 |
> | **D25 立绘来源只在会话缓存里** | ✅ **已修**：仓库内留副本 + 工具优先读它；`--rebuild-base` 后 208/208 帧逐字节相同 |
> | **D26 麦克风多声源选错设备** | ✅ **已修**：设备枚举/名字匹配/试听工具/`voice.mic_device` 配置/热键轮换；11 条新用例；本机 14 个输入设备实测 |
> | **D27 抠图毛刺／裙子镂空／臂腰间隙被填满** | ✅ **已修**。硬二值化把抗锯齿压成 0/255 → 毛刺；**我一度误判源图是"无 alpha 的白底图"并重算 alpha**，反而打穿白裙子。源图**本就是带 alpha 的 PNG**（256 级 / 58% 透明），改用 `cutout_source_alpha()` + `keyer:"alpha"`（**有现成 alpha 就用，绝不重算**）。明细见 `sprite-history.md` |
> | **D28 眨眼位置不准，合成路线走不通** | 🟡 **合成路线已实测否决，待人工出图**。整只眼仅 **13×11 = 143 px**（虹膜 61 px），**7 种合成做法全部失败**。改为 `blink_by_blend()`：**有闭眼素材就逐像素混合，无素材则不眨眼**（宁可不眨也不贴假的）。`_load_closed()` + `src_closed` 已备好，**只差一张闭眼立绘**。明细见 `sprite-history.md` |
> | **D29 点头幅度过大 + 两个动画是"死"的** | ✅ **已修**。真正可见的是**纵向位移**（`nod` 旋转只有 0.088°、根本不可见）；`sleep`/`sad` 位移**恒为正**、整像素取整后每帧相同 = **死动画**。修后八种动画全在 1~2px 且**都有 ≥2 个位移档位**。规则：**逐帧摆动必须围绕 0**。明细见 `sprite-history.md` |
> | **D30 字幕不显示（跨线程投递被静默丢弃）** | ✅ **已修**。`_invoke_on_main` 用 `QTimer.singleShot(0, fn)` 跨线程投递 —— Qt 在**调用线程**建定时器，工作线程无事件循环，**回调被静默丢弃**（受控实验：主线程 1 次 ✓ / 后台线程 0 次 ✗）。改用 `QMetaObject.invokeMethod(…, Qt.QueuedConnection)`。**新增 2 条用例 + 反方向验证**（塞回旧实现 → 2 failed）。明细见 `sprite-history.md` |
> | **D31 位置记忆写进"屏幕外坐标"＋ VRM 模式整段跳过恢复** | ✅ **已修**（用户反馈"位置跑到屏幕左上角了"）。三处独立缺陷：① `_edge_snap()` 先把窗口移到屏幕外后 `_save_position()` **无条件**存下该坐标（**实测存进 `(1911,300)`**）；② `_restore_position` 判据**单向**（只挡右下、不挡左上）；③ `load_pet` 在 VRM 模式**提前 return** ⇒ 恢复整段跳过、窗口停在 `(0,0)` = 左上角。修复：`_pre_snap_pos` 记吸附前位置 + **对称**判据 `_is_position_usable()`（按所有屏幕算可见交集 ≥60px）+ VRM 分支补恢复。**端到端实测**：合法 `(760,366)`→原样恢复；屏幕外 `(-900,100)`→落到 `(1631,120)`；贴边重启→恢复到 `(1687,300)`。11 条用例 + 逐条反方向验证。明细见 `sprite-history.md` |
>
> | **D32 字幕动态更新（整段闪现后被逐句擦成碎片／控制标记漏屏／截断无提示）** | ✅ **已修**。滚动窗口（当前句+上一句）、清洗 `[silent]`/`[thinking]` 控制标记、截断显式补 `…`；另删一处**穷举证明不可达**的死代码。明细见 `sprite-history.md` |
> | **D33 麦克风关了还能听到声音（开关只改内存 + 闸门缺失）** | ✅ **已修**（用户报告"🎤 麦克风关了，她还能听见我说话并回应"）。① `_toggle_mic(False)` **只设内存、不写 `config.voice.listening`** ⇒ 下次「保存设置」或**重启**都会把麦克风重开，菜单却仍显示"已关闭"；② `_on_speech_captured` **没有 `_monitor_enabled` 闸门**。修复：开关**落盘** + 捕获入口加**用户意图闸门** + 热键与菜单**收敛到同一条路径**。9 条用例 + 逐条反方向验证。明细见 `sprite-history.md` |
> | **D34 设置里的「人设提示词」没被加载（被 reply_style 分支绕过）** | ✅ **已修**（用户报告"人设没真正加载到角色中"）。`UniversalLLM.__init__` 按 `reply_style` **二选一**挑 prompt，而**设置界面编辑的正是 `system_prompt`** —— 出厂 `reply_style: concise` ⇒ 用户写的人设被**整段绕过**：实测设置里 **1935 字**，模型只收到内置 **388 字**。修复：`system_prompt` 是**人设本体**（必生效），concise/detailed 段作为**附加**拼在其后。**只有 openrouter 有此缺陷**。⚠️ **测试侧踩坑**：第一版用例直接调 helper，反向验证时退回旧实现**照样全绿**（bug 在"谁被调用"的接线层）；改经**构造函数**后 **4 条**变红。明细见 `sprite-history.md` |
> | **D35 TTS 完全没声音（"推荐音色"根本不存在）** | ✅ **已修**（用户报告"怎么又听不清了" —— **根因不在麦克风**）。设置界面「晓伊（**欣雅推荐**）」的音色写的是 `zh-CN-xbyaNeural`，即把项目名 `xbya` 塞进 `zh-CN-<name>Neural` 模板**拼出来的假名字**，Edge 里**不存在**。危险在于**不报错**：Edge 对未知音色只回**空音频流**，于是界面能选中能保存、用户**一个字都听不到**，日志只有 14 条 `No audio was received`。**14 个菜单音色逐个实测：13 个可用，只有它 0 字节**；而它是**菜单"推荐"项 + 设置 fallback + 插件构造函数默认值**三处默认值 ⇒ 什么都不改的用户默认就踩中。修复：改名 `zh-CN-XiaoyiNeural` ＋ 插件新增**空音频判失败**守卫（点名音色，返回 `None`，不再让调用方拿到 `b""` 当成功）。真机：TTS **成功 0→14 / 失败 14→0**。新增 7 项用例；**反方向验证**：塞回假音色 → 4 red，拿掉守卫 → 1 red。⚠️ **判据**："名字看起来对"≠"东西存在"（假名字完全符合命名模板，能骗过任何格式校验）；**空结果必须显式判失败并点名参数**；排障先分清"她没说"与"我没听见"。明细见 `docs/agent/sprite-history.md` |
> | **D36 麦克风自动挑选在"电平接近"时放弃** | ✅ **已修**（同轮）。`_pick_by_level()` 原本在"最高与次高电平接近"时 `return None` **放弃自动挑选**，退回"第一个非回环设备" —— 而本机 **14 个输入设备**且同一物理麦克风出现三次（Realtek 在 idx 1/7/15），"第一个"正好是**收不到声音的那个**。改为**即便区分度不高也仍按实测最高电平选**并打 WARNING 提示显式配置。另新增 `_can_open_at_rate()` 前置检查：**本机 8/14 个设备无法在应用采样率 16000Hz 打开**（原生 48000Hz），原先它们被探测到"电平很高"而排到最前，实际**根本不能用于监听**，现于探测阶段即标为不可用。真机：自动挑选落到 idx=8 并成功捕获语音。明细见 `docs/agent/sprite-history.md` |
>
> **D27~D35 的完整明细**（逐轮过程、实测数字、误判判据）见 **`docs/agent/sprite-history.md`**
> —— 与 §11~§17 移到 `phases.md` 同一处理方式（**原文一字未删，只是换位置**），
> 原因同：本文件有 **65536 字节**硬预算，超了会被**静默截断**。
>
> 这四项我**各修错过一次**（误判列表见 `sprite-history.md` 末节）。
>
> ⚠️ D19 / D20 都是**本轮执行中查出来的**，不在最初 18 项里 ——
> D21~D23 又是**再下一轮**查出来的（改角色那一轮）。D27~D30 是**再再下一轮**，
> D31~D34 是**用户逐轮反馈**查出来的（"位置跑到左上角了""字幕动态更新没做好"
> "麦克风关了还能听到声音""人设没真正加载到角色中"）。
> 这些项我**各修错过一次**，明细与误判判据见 `sprite-history.md` 末节。
> 留下的可复用判据：
> · 抠图方式该由"源图有没有 alpha"决定，不是由"背景看起来是什么颜色"决定；
> · 短循环动画要"不规律"，该把**节拍表写乱**，不是堆频率；
> · **逐帧摆动必须围绕 0**（位移是整像素），且"调幅度"先量清楚哪个分量真可见；
> · **Qt 跨线程投递不能用 `QTimer.singleShot`**；
> · **测量"动作是否生效"前，先确认被测量的东西真的在运行**（否则"没变化"会被
>   误读成"关不掉"，而真相是"压根没开"）；
> · **开关类功能的判据是"用户意图"，不是"底层状态"** —— 意图要持久化，
>   执行要按意图拒绝；
> · **用例要钉住最终对象的状态，不能只测 helper** —— 否则"谁被调用"的接线
>   错误测不出来（D34 的第一次回归就是这么假绿的）。
> 这说明"闭环"之后仍可能有新问题，登记表要一直能长大。

> **上表的数字是审计脚本数出来的，不是我数出来的**：`evidence/p5/closure_audit.txt`
> 由 `_tmp_closure_audit.py` 生成，逐条列终态、逐条核对证据文件是否存在。
> 它一跑就照出过一次账本与现实不一致（`AGENTS.md` 写着完成、`tasks-p5.json` 还是
> `pending`）—— 机器只读后者，这正是"文档说做完了"与"账本记着做完了"的区别。

**为什么剩下的算不出百分比**：它们**是闭环缺口，不是"可选项"** —— 计划里它们是
"有代码可改 / 从未实测"的已登记项。**闭环已成立**：18 项登记项全部到终态（0 项未到终态）。但要注意——**终态不等于做完**：6 项明确不做、2 项待人工，这些是**如实登记的未完成**，不是完成。
把它们写成"可选增强"等于**用措辞把欠的活儿抹掉**，已改回。
（这条更正本身也是本轮审计的产物：审计脚本读 `tasks-p5.json` 的 `state` 字段，
而措辞写在 `AGENTS.md` 里 —— **两个地方不一致时，机器只信前者**。）

**口径内的边界（什么不算"已验证"）**：

| 不算 | 原因 |
|------|------|
| G12 语音回环通过 | 它吃**固定测激**，测的是**管线**；**麦克风采集 / GUI 动效 / 人耳听感**只有人能判（P5-C2） |
| 覆盖率 100% | 只覆盖上表那一行写明的模块；`ui/` 等**没测** |
| "适配层不做" | 是**人工决定**，不是"已统一" —— 旧 `core.event_bus.EventBus` **仍存在且在服务语音层** |
| G8/G9/G10 退出码 0 | 依赖免费配额与第三方站点；G8 配额耗尽时会**报跳过**，报告单独标出；G8 的配额跳过原先还会被**误报成 2 个 FAIL**，已由 D20 修好 |

演示与运维脚本、历史阶段的立项背景 / 7 处"检查本身出错" / 3 处"纸面满足" /
验收轮 4 个假绿假红（19~22）/ 13 关明细：见 `docs/agent/p4-history.md` 与
`docs/agent/acceptance-history.md`（**原文一字未删，只是换位置**）。

---
