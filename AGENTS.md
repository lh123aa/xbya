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
xiaoyi-vrm-worktree/
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
| **D16** | **两种"llm schema"同名不同形制** | `LLMRouter.TOOL_SCHEMAS` 是**扁平** `{name,description,parameters}`；`ToolRegistry.to_llm_schemas()`（经 `BaseTool.to_llm_schema()`）返回**已包好**的 `{"type":"function","function":{…}}`；而 `UniversalLLM.chat_with_tools` 期望扁平（它自己会再包一层） | 与 D14 同族：把注册表 schema 递给 `chat_with_tools` → `function.name` 缺失 → **工具"没有名字"的静默失败**。P4-B4 的矩阵探针正是这么踩的（六个免费模型的 ③ 被误判成全失败）。产品当前未踩到，只因 `LLMPlanner._format_tools` 两种格式都容忍 | 短期：两处 docstring 写明形制差异 + 探针侧 `_flatten_schemas()`（**已做**）。长期：二选一，让类型来约束 |
| **D17** | **`LLMPlanner._validated_steps` 只校验工具名、不校验参数类型** | 计划校验止于"工具名在表内" | LLM 产出的参数类型常错（实测 `pattern` 给列表、`file_move.source` 给列表）。这类计划能"成功拆出来"，却必然在执行期被 `validate_params` 拦下 → 用户听到"这个指令我还没完全理解"。与 P4-B2 同族的**上游成因** | ✅ **已偿还（P5-A2，2026-09-11）**：判据抽成模块级 `agent/tools/base.py::describe_value_problem`，`BaseTool.validate_params` 与 `LLMPlanner._validated_steps` **共用同一份**（不另写第二份 —— 本项目吃过"复刻判据"的亏）。规划期现在查三类：①工具名在表内 ②参数类型合工具真实 schema ③`${sN}` 引用**不指向被丢弃/不存在的步骤**。不符则丢该步 + `WARNING` 说明"哪个参数、期望什么、收到什么"。取向与执行期一致：**未声明字段放行**、**显式 `None` 视为未提供**（F 系列缺陷 4 回归保护）；畸形 schema（`spec` 不是 dict）一律放行 —— 不确定时不假装能判。证据 `docs/agent/evidence/p5/d17_plan_param_validation.txt` |
| **D18** | **`file_delete` 把 `targets` 里的非字符串项 `str()` 之后照删** | `_collect_targets` 原写 `[str(t) for t in explicit if t]` | `targets=[["C:\\a.txt"]]` → 字符串 `"['C:\\\\a.txt']"` —— 一个**看起来像列表的路径**。危险不在"通常会删不到"（那种假路径落在白名单外，用户听到的是「删除失败了，可能是权限不够呢」，**真正原因永不出现**，排查被引到权限上），而在"**位置决定后果**"：若 `str(t)` 恰好拼出一个白名单内且真实存在的名字，它就会安静地删掉那个文件 | ✅ **已修（P5-A2 顺带，2026-09-11）**：逐项必须是 `str`，否则整条拒绝并说明**真正的原因是输入畸形**。`preview`/`execute` 结论一致（都拒绝）；空串/`None` 这类"空项"仍按缺省处理（只收紧非字符串，没顺手改空值语义）。5 个参数化用例 + 2 条反方向 + 1 条"错误里不许出现「权限」"。证据 `docs/agent/evidence/p5/d18_items_probe.txt`（含**沙箱内真实探查**：三种畸形输入各自 `validate_params`/`preview`/`_collect_targets` 的原文输出） |

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
| P5-B1~B5 | D16 守护 / voice_service 传参 / ST+vec0 / medium 长句 / SERP fixture | ⏳ **未到终态 —— 闭环缺口，尚未做完** | 计划路径见 `tasks-p5.json` 的 `planned_evidence` |
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

**文档版本：** v3.1（F 系列开放项收尾 —— 见 §17；P3 阶段记录见 §15 / §16）
**下次回顾：** P4 立项时；或 §17.4「仍然未做」项被推进时


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
| **P3** | 多步任务规划（D5）+ 长期记忆 | **当前阶段，见 §15 / §16** | 本文件 |

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

## 十五、P3 实施进度

> 任务分解见 `docs/agent/tasks-p3.json`。本轮把「能力」从单步扩到多步、从无状态扩到有长期记忆。
> **验收关卡见 §16。**

### 15.1 P3 范围

| 任务 | 债务/目标 | 状态 |
|------|----------|------|
| P3-A | D5 多步任务规划（planner seam + 3 个 Provider + 管线多步执行） | ✅ **已完成** |
| P3-B | 长期记忆（embedder seam + memory seam + SQLite/FTS5/向量检索 + 3 个记忆工具） | ✅ **已完成** |

**明确不在 P3 范围**：D3 多用户隔离（框架已支持 `ctx.fork()`，无产品需求）、
Computer Use / 屏幕理解 / DSH 集成 / 多设备协同（v3.0 产品愿景）、
记忆的端到端加密（只做「不落用户目录 + 不记敏感信息」两条硬规则）、
P1 遗留的人工项（5 项语音验证 / 真实 GUI 启动 / 真实 LLM API）。

### 15.2 P3-A 交付详情（多步任务规划 / D5）

**问题**：`RouterService` 的契约是「一句话 → 一个 AgentCommand」。这在单步指令上极准
（规则路由 100%），但「把下载目录里的安装包都挪到软件归档」天然是多步的 ——
硬塞进一个 action 只会让工具参数越来越玄学。

| 模块 | 文件 | 规格 |
|------|------|------|
| 规划 seam | `agent/seams/planner.py` | `PlannerService` / `Plan` / `PlanStep` / `PlanStatus` + **占位符解析**（Provider 与 Consumer 共用） |
| 规则规划器 | `agent/providers/planner/template_planner.py` | 5 条配方（搜→删 / 搜→移 / 搜→读 / 读→译 / 截→开），<1ms，结果确定 |
| LLM 规划器 | `agent/providers/planner/llm_planner.py` | JSON 分解 + 严格校验（围栏剥离 / 大括号配平扫描 / 工具名白名单 / 步数截断） |
| 混合规划器 | `agent/providers/planner/hybrid_planner.py` | 规则优先 → 未命中且确实像多步才转 LLM（默认） |
| 规则路由扩展 | `agent/providers/router/rule_router.py` | 新增 `plan` 意图 + `_route_multi_step()` 专用预检 |
| 管线多步执行 | `agent/pipeline.py` | `_PlanRun` 状态机 + 顺序执行 + 逐步安全校验 + 确认挂起/断点续跑 + 中断取消 |
| 插件/配置 | `planner_plugin.py` + `config.yaml` 的 `agent.planner` 段 | 换 Provider = 改一行配置 |

**为什么另起一层而不是塞进 Router**（设计决策 DD-1）：
把 Planner 塞进 Router 会污染已被 73 条测试集钉死的规则路由。改为
「规则路由新增 `plan` 意图承接**显式**多步标记 → 命中则转 Planner」，
未命中时 Planner 完全不参与，单步快路径一行代码都没动。

**`plan` 意图为什么不做成打分意图**：打分制下它会跟单步意图抢分
（「挪到」权重 2.5 会赢过任何多步标记），于是改由专用预检判定；
`IntentDef` 仍留在表里，目的只是让 `describe_intents()` 把 `plan` 这个动作
**告知 LLM 路由**，使它也能产出多步意图。

**多步判定分两档（宁可漏判也不误判）**

| 档 | 触发 | 附加条件 | 理由 |
|----|------|---------|------|
| 计数式 | 「分三步…」 | 无（用户已明说分步） | 此时不必认出工具：动词不在规则词表内（"整理"），但 LLM 规划器认得；最坏结果只是规划器也拆不出来 → 退回闲聊，与不判 plan 的结局一致 |
| 连接式 | 「…然后…」 | ① 命中 ≥2 个**不同**意图 ② 连接词两侧各有 ≥2 字 | 挡掉「先看看电脑状态」（只有一个真实动作）与「最后找一下合同」（连接词左侧为空） |

**步骤间数据流动**：参数里写 `${步骤ID.字段}`，由管线在提交该步前解析。
- 整值占位符 **保留原生类型**（列表仍是列表）→ 才能喂给 `file_delete.targets`
- 嵌在文本中则字符串化拼接
- 解析不出来时**保留原文**交由工具追问，绝不抛异常
- `${s1.paths.0}` 取列表首项 —— `file_move` 只收单个 `source`，故配方这样写；
  而 `file_delete` 收列表 `targets`，配方直接给 `${s1.paths}`。**配方贴着工具真实 schema 写**。

**实施中发现并修复的两个缺陷**（均由测试暴露，见 §15.5）

**新增事件**（`core/kernel/events.py`）

| 事件 | 负载 |
|------|------|
| `plan.started` | `{request_id, steps, plan_source, total}` |
| `plan.step.done` | `{request_id, step_id, action, index, total, success, summary}` |
| `plan.finished` | `{request_id, status, done, total, summary}` |

> ⚠️ 负载字段不能命名成 `source` —— `EventBus.emit(event_type, source="", **data)`
> 的第二个形参就叫 `source`，同名关键字会被它吃掉而不进事件负载（P3 踩过，故用 `plan_source`）。

**统计**：`pipeline.stats()` 新增 `plans` / `plan_steps` / `plan_halted` / `plan_failed` /
`plan_cancelled` / `plan_pending` / `planner`。

### 15.3 P3-B 交付详情（长期记忆）

**三条硬约定**（写在 `agent/seams/memory.py` 的接口文档里，Provider 必须遵守）

1. **永不抛异常** —— 记忆是增强能力，检索失败应表现为"想不起来"，而不是让用户的操作失败
2. **敏感信息一律拒绝入库** —— `remember()` 第一件事就是敏感判据，
   命中直接拒绝且**日志只写命中的类别、不写原文**（否则密码会从记忆库漏进日志文件）
3. **构造廉价** —— 重资源（模型加载）必须懒加载并在 `ready()` 里如实反映

| 模块 | 文件 | 规格 |
|------|------|------|
| 记忆 seam | `agent/seams/memory.py` | `MemoryService` / `MemoryItem` / `MemoryKind` + `is_sensitive()`（密码/密钥/证件号/卡号）+ `format_recall()` |
| 嵌入 seam | `agent/seams/embedder.py` | `EmbedderService` + `normalize`/`dot`/`cosine`/`pack`/`unpack`（`array('f')`，不依赖 numpy） |
| 默认嵌入器 | `agent/providers/memory/hashing_embedder.py` | 词元 + 字符 n-gram 带符号哈希（**blake2b**，非内置 `hash()`）→ 定长归一化向量 |
| 可选嵌入器 | `agent/providers/memory/st_embedder.py` | sentence-transformers，懒加载 + 加载失败自动不可用 |
| 存储与检索 | `agent/providers/memory/recall_store.py` | SQLite + FTS5 + float32 向量列；词法/向量双路 → RRF 融合 |
| seam 实现 | `agent/providers/memory/hybrid_memory.py` | 管"什么该记"：敏感拦截、情景记忆、偏好提示 |
| 记忆工具（3） | `agent/tools/memory_tools.py` | `memory_remember` / `memory_recall` / `memory_forget`（`medium` 风险，走确认通道） |
| 插件/配置 | `memory_plugin.py` + `config.yaml` 的 `agent.memory` 段 | `embedder` 与 `memory` 各自注册为服务，可单独替换 |

**为什么默认嵌入器不是神经模型**（设计决策 DD-4）：
`sentence-transformers` 需下载模型，没网的机器上直接不可用，而记忆功能
"因为环境而整个失效"是不可接受的。故默认走纯 stdlib 的特征哈希 ——
零依赖、离线、确定、**跨进程稳定**（`blake2b` 而非内置 `hash()`：后者有随机盐，
会让落盘的向量在重启后失去意义）。代价是只能做词形相近匹配，换来的是
记忆功能在任何机器上都不失效。想要真语义召回时把 `agent.memory.embedder` 改成 `st`。

**中文检索的关键坑（DD-6）**：SQLite FTS5 默认的 `unicode61` 分词器把
**连续汉字当作一个词元** —— 「桌面上的合同」会成为单一 token，于是检索「合同」
永远命中不了（已实测确认）。方案：**不使用 FTS5 自带分词器切中文**，
改为自产 token 串存进 FTS 列：

```
"桌面上的合同.pdf" → 桌 面 上 的 合 同 桌面 面上 上的 的合 合同 pdf
```

检索时对 query 做同样 tokenize，token 加双引号并用 `OR` 连接
（中文单字很泛，用 OR + bm25 排序而不是 AND 收窄，否则多词查询会全灭）；
`"` 必须双写转义（实测 `MATCH '"a"b"'` 报 `unterminated string`）。

**向量后端两档（DD-5）**：`auto`（默认，优先 sqlite-vec，失败静默退 python）| `python` | `sqlite_vec`。
`stats()` 如实报告**实际生效**的后端。本机实测生效 `sqlite_vec`（已装，零新依赖）。
两个实测坑：`k` 在 JOIN 的 kind 条件**之前**生效（故按类别检索要过取 ×4）；
vec0 重复主键 INSERT 报错（故写入走"先删后插"的 upsert）。

**降级阶梯（全部不抛异常）**：用户四白名单目录 / 损坏库 / 表结构异常 / connect 失败
→ 纯内存 + `degraded=True`；扩展缺失 → 退 python（`auto` 下**不算**降级）；
单路检索失败只丢那一路；连接被外部关闭后 13 个公开方法全部返回空值。

**管线接入**：结果产出后写**情景记忆**（只记成功的操作 —— 失败经历被召回会把用户带偏；
`action` 传可播报的动作短语而非工具名，因为这条记忆将来会被直接念出来）；
偏好提示注入路由上下文与 LLM 规划器提示词。

### 15.4 回归与覆盖率

| 指标 | 结果 |
|------|------|
| 全量回归 | **2139 passed / 1 skipped / 0 failed / exit=0** |
| 覆盖率 | 内核+Agent+ack_cache **6602 语句 / 0 未覆盖 / 100%** |
| planner 专项 | `tests/agent/test_planner.py` **218 项**；`seams/planner` + `providers/planner` + `planner_plugin` 覆盖率 **100%** |
| memory 专项 | `test_memory_store.py` 173 + `test_memory_tools.py` 62 + `test_memory_wiring.py` 68 = **303 项**；`providers/memory` + `tools/memory_tools` + `seams/memory` + `seams/embedder` + `memory_plugin` 覆盖率 **100%** |
| 端到端 | `p3_acceptance_smoke.py` **43/43**；`p1_acceptance_smoke.py` **18/18**（未回退）；`measure_acceptance_metrics.py` **8/8** |

**新增测试 522 项**（2140 collected vs F2 基线 1618）。
测试数历史：669（P0）→ 1464（P1）→ 1545 → 1579（P2）→ 1617（F2）→ **2140（P3）**。

> **只新增 1 处 `# pragma: no cover`**（累计 4 处，详见 §16.4）：
> `agent/seams/planner.py` 中 `refs()` 的"取值非字符串"防御分支 ——
> `_walk_values()` 的契约决定它只产出字符串，该分支结构上不可达。

**工具集从 18 增至 21**（新增 3 个记忆工具）。这使 `test_plugins.py` /
`test_integration.py` / `test_coverage_final.py` 里 5 处硬编码的 18 需要更新 ——
其中 3 处改为显式关闭记忆（`memory_enabled=False`）而不是把数字改大：
那些用例的真实意图是"验证四类工具集开关生效"，而记忆工具由 `agent.memory.enabled`
单独管辖、不受 `tools.*` 影响，留着它会掩盖断言。

### 15.5 P3 实施中发现并修复的缺陷

1. **`EventBus.emit` 的 `source` 形参吞掉同名负载字段**（端到端装配验证暴露）
   `self._bus.emit(EventTypes.PLAN_STARTED, ..., source=plan.source)` 的 `source`
   绑定到了 `emit` 的第二个形参而不是 `**data`，于是事件负载里根本没有计划来源。
   测试读 `started["source"]` 直接 `KeyError`。
   修复：负载字段改名 `plan_source`，并在 `EventTypes` 处写明这个坑。
2. **`_extract_dest` 会丢掉用户的显式路径、退回目录别名去猜**（端到端验收场景 5 暴露）
   「先找到桌面上的合同然后再挪到 `C:\Windows\System32`」被解成 `dest="Desktop"`
   （因为整句里有"桌面"），于是计划**"成功"地**把文件挪到用户没要求的地方 ——
   静默做错事，比明确拒绝危险得多。
   修复：**显式路径优先**（盘符 / UNC / `~` / 空格后的绝对路径），原样交给安全层裁决 →
   命中白名单外路径 → 计划中止且理由对用户可读。
3. **`_extract_dest` 两轮扫描的第一轮会先命中错误别名**（测试暴露）
   「先找到**桌面**上的压缩包然后再挪到**文档**」里两个别名都存在，
   按别名表顺序先命中"桌面" → 文件被挪到桌面。
   修复：先看移动动词后的子句，再退回整句。
4. **`TemplatePlanner.can_plan` 未把自身工具表传给 `_recipe_usable`**
   不传会走"工具表未知 → 一律放行"的宽松兜底，于是缺工具的配方也被报成"可规划"，
   与 `plan()` 的结论自相矛盾。修复后 `can_plan`/`plan` 对同一输入结论一致。
5. **命令内联进 `pwsh` 双引号字符串时 `${...}` 被 PowerShell 展开**（工具链坑，非代码缺陷）
   验证占位符时 `${s1.paths}` 变成了空串，看起来像"占位符解析坏了"。
   教训：含 `${...}` 的 Python 代码一律写进脚本文件再跑。
6. **敏感判据的中文词不能加 `\b`**（自检暴露）
   Python 的 `\b` 以 `\w` 为界，而汉字本身就属于 `\w`，
   于是「我的密码是abc」里 `密码` 与 `是` 之间不存在词边界，加了 `\b` 反而**永不命中**。
   修复：ASCII 关键词用 `\b` 收窄（避免误伤 passwordless），中文词用裸子串匹配。

### 15.6 评审回传的 seam 改进建议（已记录，未在本轮实施）

子代理在交付时提了 5 条契约层面的建议，均**只报告未改**（避免动到已冻结的契约）：

| # | 建议 | 现状与影响 |
|---|------|-----------|
| 1 | `MemoryService` 缺 `get(item_id)` / `recent(limit)` / `items()` | `memory_forget` 的预览想说明"删的是哪条"、`memory_recall` 空查询要"列最近几条"，都只能靠 Provider 补充能力。已在 `HybridMemory` 上加了 `get`/`recent`，工具层用 `getattr` 探测 + 优雅回退（任何只实现 seam 的 MemoryService 都可用）。**下个版本建议把它们提为 seam 的可选方法** |
| 2 | `EmbedderService.dim` 无法表达"未知" | `SentenceTransformerEmbedder` 加载前只能申报 `fallback_dim`；若真实维度与之不符，vec0 索引会按错误宽度建。已兜住（跳过索引 / 重开时重建），但 seam 若加 `ensure_ready()` 或 `dim_hint` 会更干净 |
| 3 | `MemoryItem` 没有 embedding 字段 | store 必须自带嵌入器，或由调用方显式传向量。已两条都支持（`add` 的可选参数是**超集**，原签名仍成立） |
| 4 | `format_recall` 与 `hint_for` 的措辞不统一（"我记得你提过" vs "你之前说过"） | 二者用途不同：`recall_text` 是**念给用户听**，`hint_for` 是**喂给 LLM 的提示**。刻意保留两种措辞 |
| 5 | 敏感模式顺序使 `api_key = sk-…` 报"密钥"而非"API Key" | 通用模式在前。纯文案问题 |

### 15.7 未交付 / 未验证（转入后续）

- **真实 sentence-transformers 模型从未下载**（无网络使用）：只测了假模块的成功/失败/
  编码异常/维度探测/并发加载/close 重载路径，**真模型上的 encode 归一化未验证**
- **真实 LLM API 未跑通**（P1 遗留，同 §14.5 第 4 项）：planner 的 `llm` / `hybrid`
  两条路径只在假 LLM 上验证；规则路径有端到端证据
- **`hint_for` 在 2000 条上限时 ≈13ms**（每句话一次）：远低于 1.5s 感知延迟预算，
  但比规则路由的 0.07ms 大三个数量级 —— 若将来把 `max_items` 调大需重新评估
- **`_search_vector_python`**（仅 sqlite-vec 不可用时走）是纯 Python 全表余弦，
  2000 条 × 256 维约 2–4ms
- **P1 人工项**（5 项语音验证 / 真实 GUI 启动）与本轮无关，仍需真机与人
- **D3 多用户隔离**：框架支持 `ctx.fork()`，无产品需求前不启用

---

## 十六、P3 验收关卡

### 16.1 关卡结论

| 关卡 | 通过条件 | 实测 | 结论 |
|------|---------|------|------|
| P3-G0 | 语法检查通过 | 全部新增/修改模块 `py_compile` 通过 | ✅ |
| P3-G1 | 专项测试通过 | planner **218** + memory **303** = 521 passed / 0 failed | ✅ |
| P3-G2 | 全量回归零失败 | **2139 passed / 1 skipped / 0 failed / exit=0** | ✅ |
| P3-G3 | 覆盖率保持 100% | 内核+Agent+ack_cache **6602 语句 / 0 未覆盖 / 100%** | ✅ |
| P3-G4 | 端到端不回退 | `p3_acceptance_smoke` **43/43**；`p1_acceptance_smoke` **18/18**；`measure_acceptance_metrics` **8/8** | ✅ |
| P3-G5 | 文档回写 | 债务表 D5 标记偿还、本节、§15、tasks-p3.json、README、config.yaml | ✅ |

**P3 阶段判定：通过。**

### 16.2 端到端验收证据（`tools/p3_acceptance_smoke.py`，43/43 通过）

**A. 多步任务规划**

| # | 场景 | 结果 |
|---|------|------|
| 1 | 真实配置装配 | `tools=21`、`planner=HybridPlanner`、`memory=HybridMemory`、插件 12 条、记忆 `degraded=False` |
| 2 | 单步指令不受影响 | 有结果，且**未产出计划** |
| 3 | 多步：搜 → 移，确认后完成 | 链路 `file_search → file_move`，来源 `template`；确认问句「这是第 2 步，一共 2 步。前面已经找到 2 个文件。…」；批准后 `success 2/2` |
| 4 | 多步：确认后取消 | `cancelled`，且如实说明"前 N 步已做完、改不了" |
| 5 | 多步：某步被安全守卫拒绝 | `failed`，理由可读（「这个位置我不能动哦…」），后续步骤一步都没执行 |
| 6 | 计划含未注册工具 | 未启动计划，退回闲聊 |

**B. 长期记忆**

| # | 场景 | 结果 |
|---|------|------|
| 7 | 中文写入 → 中文检索 | 「浏览器」命中「我喜欢用 Chrome 浏览器」；换词形「Chrome」同样命中 |
| 8 | 敏感信息拒绝入库 | 密码 / API Key / 身份证号 三条全被拒，记忆条数不变 |
| 9 | 情景记忆 | 执行一次搜索后可按「找文件」召回「上次让你「找文件」，找到 2 个文件」 |
| 10 | 记忆工具经注册表可用 | 三个工具均已注册；remember/recall 正常；敏感内容被拒并说明原因 |
| 11 | 偏好提示注入路由上下文 | `memory_hint = '你之前说过：我喜欢用 Chrome 浏览器、常用目录是下载'` |
| 12 | 存储位置安全 | 记忆库落盘（4096 bytes），在沙箱内、不在用户白名单目录 |

**汇总统计**：`plans=3 plan_steps=4 plan_halted=2 plan_failed=1 plan_cancelled=1`；
记忆 `count=7 backend=sqlite_vec rejected_sensitive=3`。

### 16.3 D7 状态的更新

P3 期间新增全量运行样本（本轮共 **6 轮** `pytest tests/`，含 §15.4 的最终验证轮），
**全部 exit=0，未复现崩溃**。累计样本：修复后 **28 轮 0 次**（修复前 11 轮 2 次）。

按 2/11 基线，28 轮全绿纯属巧合的概率约 (9/11)^28 ≈ 0.0035。**判定 D7 已有效消除**，
但鉴于其历史表现（间歇性、参与线程多），仍建议保留一行登记直到有跨机器/跨版本样本。

### 16.4 `# pragma: no cover` 清单（累计 4 处，均为结构上不可达的防御分支）

| 位置 | 不可达原因 |
|------|-----------|
| `agent/tools/browser_tools.py` — `BLOCKED_SCHEMES` 二次校验 | 协议白名单已先拦截 |
| `agent/tools/productivity_tools.py` — `cn_to_number` 的"非数字字符"出口 | 调用方正则只喂数字串 |
| `agent/tracker.py` — 序数 token 为空出口 | 正则两条分支必然填充分组 |
| `agent/seams/planner.py` — `PlanStep.refs()` 的"取值非字符串"分支 | `_walk_values()` 契约保证只产出 `str` |
| `core/kernel/loader.py` — 环检测候选集合为空的兜底返回 | 调用方保证候选非空（详见源码注释） |

> **勘误（验收阶段，2026-09-10）**：上表原先写"累计 4 处"，实际清点有 **8 处** ——
> 说明这份清单**漏登记过**，而漏登记的屏蔽就是"隐性凑覆盖率"。验收时逐处核对后处置了三处：
>
> | 原位置 | 处置 |
> |--------|------|
> | `agent/tools/file_tools.py` — `_human_size` 末尾"逻辑上不可达"的 return | **重构成"循环处理 B/KB/MB + GB 兜底"**，每行都可达 → 不需要屏蔽（死代码删掉比屏蔽好） |
> | `agent/seams/planner.py` — `_Missing.__repr__`（理由写"仅调试可见"） | **本来就已被 `test_missing_sentinel_repr` 覆盖** → 屏蔽是多余的，删掉 |
> | `agent/providers/productivity/open_meteo.py` — `requests` 导入失败分支 | **由验收阶段新写的用例用 `sys.modules["requests"] = None` 逼出来**（本轮我自己加的屏蔽，已删） |
>
> 教训：**`# pragma: no cover` 清单必须与代码同步核对**，否则"覆盖率 100%"里会混进屏蔽换来的假绿。
> 现在总数 **5 处**，每一处都在上表里写了不可达理由。
>
> **二次勘误（同轮稍后）**：审计"到底有几处屏蔽"的 grep 一度数出 **6 处** —— 第 6 处是
> `agent/tools/file_tools.py` 里一句**说明文字**（"也就不需要 `# pragma: no cover` 去屏蔽它"）。
> 也就是说：**审计被自己的注释文字骗了**（更危险的是反过来 —— coverage 自己也按子串认这个指令，
> 写在语句行上就会真的生效）。处置：把那句说明改成不写出字面量的说法，并在注释里写明原因。
> 数字回到 5 处，且这次是**逐个打印文件:行号核对过的**，不是数出来的。
>
> 这一处与上面三条同源：**凡是"统计类"的结论，都要能逐条列出证据，而不是只报一个数**。

### 16.5 证据归档

| 文件 | 内容 |
|------|------|
| `docs/agent/evidence/p3/p3_acceptance_smoke.txt` | 12 场景 / 43 断言的完整输出（含每步确认问句与计划链路） |
| `docs/agent/evidence/p3/coverage.txt` | 覆盖率命令原始输出（`TOTAL 6602 0 100%`） |
| `docs/agent/evidence/p3/full_regression.txt` | 全量回归原始输出（`2139 passed, 1 skipped`） |

> P3 只新增 **1 处** `# pragma: no cover`，并且**没有**为了凑覆盖率而放宽容忍度：
> 唯一那处是 `refs()` 里"取值非字符串"的防御分支，其不可达性由 `_walk_values()`
> 的契约保证。过程中反而**因为新增代码带出一行死代码而重构掉了它**
> （`_verb_end` 原先分两步求"最早位置 + 该位置上的最长动词"，
> 第二步的兜底分支按构造成立不了 → 改为单趟遍历，见 §15.5 同类问题）。

---

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
| 测试用例 | 全量 `pytest tests` | **2668 passed / 1 skipped / 0 failed** | skipped 的那 1 项是环境相关，不是"忽略失败" |

**P5 登记项逐条终态**（四种终态，没有第五种；机器可读的账本在 `tasks-p5.json`）：

| 终态 | 项数 | 具体 |
|------|------|------|
| ✅ 已修 | 2 | P5-A2（D17 + 顺带 D18）、P5-AUDIT（G13 清单改自动发现） |
| ✅ 已完成 | 3 | P5-A1（账本刷新 + 完成度）、P5-A3（D14 终态登记）、P5-AUDIT-CLOSURE（收口审计） |
| ⬜ 明确不做 | 6 | P5-C3 D3 / C4 天气 IP / C5 D7 / C6 繁简边界 / C7 诱饵页 / C8 历史账本 |
| 🟡 待人工 | 2 | P5-C1 轮换 key、P5-C2 麦克风+GUI+人耳验收（各附最小操作路径与判据） |
| ⏳ 未到终态（**闭环缺口**） | 5 | P5-B1 D16 守护 / B2 voice_service 传参 / B3 ST+vec0 / B4 medium 长句 / B5 SERP fixture |

> **上表的数字是审计脚本数出来的，不是我数出来的**：`evidence/p5/closure_audit.txt`
> 由 `_tmp_closure_audit.py` 生成，逐条列终态、逐条核对证据文件是否存在。
> 它一跑就照出过一次账本与现实不一致（`AGENTS.md` 写着完成、`tasks-p5.json` 还是
> `pending`）—— 机器只读后者，这正是"文档说做完了"与"账本记着做完了"的区别。

**为什么 B1~B5 算不出百分比**：它们**是闭环缺口，不是"可选项"** —— 计划里它们是
"有代码可改 / 从未实测"的已登记项。**闭环尚未宣告**，因为还有 5 项没做完。
把它们写成"可选增强"等于**用措辞把欠的活儿抹掉**，已改回。
（这条更正本身也是本轮审计的产物：审计脚本读 `tasks-p5.json` 的 `state` 字段，
而措辞写在 `AGENTS.md` 里 —— **两个地方不一致时，机器只信前者**。）

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
