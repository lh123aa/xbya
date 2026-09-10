# 欣雅 Agent 层 P1 — 系统性验收报告

| 项目 | 内容 |
|------|------|
| 报告编号 | XIAOYI-AGENT-P1-ACC-001 |
| 报告日期 | 2026-09-10 |
| 验收对象 | 欣雅 Agent 执行层 P1（Phase A~H） |
| 验收依据 | `AGENTS.md` §7、`docs/agent/acceptance.md` v1.0、`docs/agent/spec.md`、`docs/agent/tasks.json` |
| 验收方式 | 自动化关卡执行 + 指标量化实测 + 源码审查 + 人工项清单核对 |
| 验收结论 | **有条件通过**（4 项未验证、3 项未交付、1 项开放缺陷，见第五章） |
| 证据归档 | `docs/agent/evidence/`（18 个文件） |

---

## 勘误（Errata）

| 编号 | 日期 | 内容 |
|------|------|------|
| **E1** | 2026-09-10（P2-1 期间发现） | **M8 覆盖率的测量命令失效，报告数字低估了覆盖面同时也漏测了一个模块。**<br>原命令 `--cov=services/ack_cache` 用的是**斜杠形式**，coverage 会报 `Module services/ack_cache was never imported` 并**静默跳过**该模块——即报告中的 `TOTAL 3897 0 100%` 实际只覆盖 `core/kernel` + `agent`，**未包含 `services/ack_cache.py`**。<br>改用点号形式 `--cov=services.ack_cache` 后，ack_cache 首次被纳入统计，并暴露出 7 行未覆盖（预热取消/同步 wait_ready 等），已补测至 100%。<br>**修正后（含 P2-1 新增代码）**：`services\ack_cache.py 178 0 100%`、`TOTAL 4078 0 100%`。<br>**结论影响**：M8「覆盖率 100%」的**结论仍成立**（内核与 Agent 层确实 100%），但报告当时**未能证明** `ack_cache` 也达到 100%；现已补齐证明。已在 `AGENTS.md` §13.4 记录规则：单文件模块必须用点号形式。 |

---

## 一、验收方法与执行说明

本次验收不采信开发期自述，全部指标由脚本或 pytest 现场取值：

| 手段 | 脚本/命令 | 说明 |
|------|-----------|------|
| 关卡执行 | 见第二章 | 按 `acceptance.md` 各 Phase 验收命令逐条执行，输出重定向归档 |
| 指标量化 | `tools/measure_acceptance_metrics.py` | 直接测被测系统，不依赖测试内部断言 |
| 端到端 | `tools/p1_acceptance_smoke.py` | 真实配置装配 + 7 场景 18 断言 |
| 源码审查 | 脚本内静态扫描 | 删除不可逆性、风险登记完整性 |
| 人工项 | 清单核对 | 麦克风类场景本环境无法执行，标注为未验证 |

**度量边界声明（重要）**：本报告严格区分三种证据强度——

- **真实执行**：真的读写文件、真的调用系统 API、真的跑子进程
- **mock 验证**：外部依赖被打桩，验证的是代码路径而非真实服务
- **未验证**：从未以任何方式执行过

凡属后两类，均在表格中显式标注，**不并入"通过"计数**。

---

## 二、验收关卡结论（G0~G3）

| 关卡 | 计划通过条件 | 实测 | 结论 |
|------|-------------|------|------|
| **G0** | 内核覆盖率 >90%，现有测试零回归，示例可运行 | 内核覆盖率 **100%**（465 语句 / 0 未覆盖）；`--cov-fail-under=90` 达成；133 passed | **通过**（示例脚本缺失，见 F6） |
| **G1** | 并行管线事件可观测，规则路由准确率 >90% | 路由准确率 **100%**（73/73）；管线 49 passed，ack 与 result 事件均可观测 | **通过** |
| **G2** | 安全边界测试 0 失败，删除 100% 可恢复 | 安全+边界+文件工具 155 passed / 1 skipped / **0 failed**；删除无永久删除分支 | **通过** |
| **G3** | 端到端跑通，延迟达标，现有功能无回归 | 端到端 **18/18**；延迟 P95 0.8ms / 总延迟 max 5.2ms；全量 1464 passed | **有条件通过**（存在间歇性崩溃，见 F1） |

### 关卡证据文件

| 关卡 | 证据文件 | 关键行 |
|------|---------|--------|
| G0 | `evidence/g0_coverage.txt` | `TOTAL 465 0 100%`；`Required test coverage of 90% reached` |
| G1 | `evidence/g1_router_accuracy.txt`、`evidence/g1_pipeline.txt` | 35 passed / 49 passed |
| G2 | `evidence/g2_security_report.txt` | 142 passed, 1 skipped |
| G3 | `evidence/g3_regression.txt`、`evidence/g3_e2e/smoke_output.txt` | 1464 passed / 1 skipped；18/18 |

---

## 三、最终验收指标实测（10 项）

| # | 指标 | 目标值 | 实测值 | 结论 |
|---|------|--------|--------|------|
| M1 | 规则路由准确率 | >90% | **100.0%**（73/73，action 错 0 / 参数错 0） | ✅ 超标 |
| M2 | 规则路由延迟 | P95 <10ms | **P50 0.112ms / P95 0.804ms / P99 1.703ms**（n=365） | ✅ 超标 12 倍 |
| M3 | 感知延迟（首次反馈） | <1.5s | **P50 1.0ms / max 1.2ms**（n=4） | ✅ 超标 1250 倍 |
| M4 | 总延迟（结果播报） | <3s | **P50 3.1ms / max 5.2ms**（n=4） | ✅ 超标 577 倍 |
| M5 | 误操作率 | 0 | 非法路径拒绝 **8/8**；白名单内正确放行 | ✅ |
| M6 | 删除可恢复率 | 100% | 永久删除调用 **0 处**；`send2trash` 实调用 1 次 | ✅ |
| M7 | 工具执行成功率 | >95% | **100%**（10/10 真实执行子集） | ✅ |
| M8 | 内核测试覆盖率 | >90% | 内核 **100%**（465 语句）；内核+Agent **100%**（3897 语句）<br>⚠️ 当时命令未含 `ack_cache`，见 **勘误 E1** | ✅ 超标 |
| M9 | 现有功能回归 | 0 失败 | **1464 passed / 1 skipped / 0 failed** | ✅（存在间歇性崩溃，见 F1） |
| M10 | 插件可卸载 | 是 | load→服务可用→unload→**服务不可用** | ✅（但未接入运行时，见 F2） |

**M1 参数核对细节**：73 条指令集不仅校验 action，还校验关键参数子集（如 `pattern`、`dirs`、`time_range`、`metric`、`minutes`、`command`），参数级全部命中。

**M5 边界探测集（8 条全部拒绝）**：白名单外系统目录（System32）、白名单外程序目录（Program Files）、`..` 上跳穿越、多级穿越、UNC 路径、空字节注入、系统关键目录、其他盘符。同时验证白名单内路径**不误拒**。

**M2/M3/M4 波动说明**：计时指标受机器负载影响，两次独立运行 M2 P95 分别为 0.062ms 与 0.804ms、M4 max 分别为 1.0ms 与 5.2ms，均远低于阈值，结论不受影响。归档值取最后一次运行。

---

## 四、Phase 逐阶段验收

| Phase | 范围 | 测试目标 | 结果 | 计划通过标准 | 判定 |
|-------|------|---------|------|-------------|------|
| A | 插件内核 | `tests/kernel` | 133 passed | 覆盖率 ≥90%、零回归 | ✅ |
| B | 并行管线 | `test_pipeline.py` | 49 passed | 事件可观测、ack∥submit | ✅ |
| C | 意图路由 | `test_router.py` + `test_llm_router.py` | 78 passed | 准确率 >90%、P95 <10ms、降级链正确 | ✅ |
| D | 安全守卫 | `test_safety.py` + `test_edge_cases.py` | 155 passed / 1 skipped | 边界 0 失败、删除不可逆、源码审查 | ✅ |
| E | 执行器与摘要器 | `test_summarizer.py` | 92 passed | 模板/LLM 分流正确 | ✅ |
| F | 文件工具 | `test_file_tools.py` | 84 passed | 六工具通过、覆盖率 ≥75% | ✅ |
| G | 实体追踪 | `test_tracker.py` | 59 passed | 序数/集合/单数指代 | ✅ |
| H | 集成与端到端 | 集成+系统+生产力+浏览器工具 | 254 passed | 端到端跑通、无回归 | ✅ |

**Phase 附加抽检**（脚本直接调用，非测试代劳）：

| 能力 | 抽检输入 | 实测输出 |
|------|---------|---------|
| 指代消解 | 栈内 `[a.png, b.png, c.png]` | "第二个"→`b.png`；"最后一个"→`c.png`；"那些"→3 项 |
| 混合路由降级 | LLM 抛 `TimeoutError` | 降级为 `chat`，LLM 被调用 1 次后放弃，进程未崩 |
| 模板摘要 | `file_search` 结果 1 项 | `找到「报告.docx」，1K`（非原始 summary 直出） |

---

## 五、未通过 / 未验证 / 未交付项

> 本章是本报告的核心：第四章的"全部 ✅"只覆盖**已执行**的部分。以下为**未覆盖**的部分。

### 5.1 开放缺陷（1 项）

#### F1 — 全量测试间歇性访问违例（0xC0000005）【中等，建议修复】

| 项 | 内容 |
|----|------|
| 现象 | 全量 `pytest tests/` 偶发以 `0xC0000005`（access violation）终止 |
| 频率 | **约 1/10**（本次验收 10 次全量运行中崩溃 1 次；其后连续 9 次干净） |
| 故障帧 | `tests/test_vrm_bridge.py:310 in test_real_timer_fires_via_event_loop` — 即 `loop.exec()` 处 |
| 崩溃栈特征 | 主线程在 `QEventLoop.exec()`；进程内同时存在十余个 `concurrent.futures.thread._worker` 空闲线程与一个 `asyncio/windows_events._poll` 帧 |
| 危害 | 全部用例实际已跑完，但进程在打印汇总前死亡 → CI 看到虚假失败，且无测试报告 |
| 隔离复现 | `test_vrm_bridge.py` 单跑 **20/20 干净** |
| 定向复现 | PetWindow/Agent 测试 + VRM bridge 组合跑 **15/15 干净** |
| 归因 | 依赖**全量累积状态**（残留执行器线程 + 事件队列 + 嵌套事件循环），非 Agent 层代码、非 P1 新增测试引入（新增测试文件单跑 5/5 干净） |
| 证据 | `evidence/known_issues/g3_crash_0xC0000005_20260910.txt`（含完整 faulthandler 线程转储） |
| 建议 | ① 在 `conftest.py` 的 `pytest_sessionfinish` 前增加"排空 Qt 事件队列 + shutdown 全部残留 ThreadPoolExecutor"步骤；② 或将该 VRM 测试标记为需串行执行并前置事件队列清理；③ 修复前 CI 应对该退出码做白名单重试 |

> **技术债 D7 应重新打开**。原判"已偿还（4/4 干净）"的样本量不足以支撑结论；本次 10 次运行即复现 1 次。

### 5.2 未验证项（4 项）

| 编号 | 项 | 未验证原因 | 已做到的验证强度 |
|------|----|-----------|-----------------|
| F3 | **LLM 路由 / LLM 润色 真实调用** | 需真实 LLM API 配额；验收环境刻意断开 | 仅 mock（`test_llm_router.py` 43 项 + 指标脚本降级链抽检）。**从未以真实 API 跑通过** |
| F4 | 浏览器三工具联网行为 | 会真实联网、可能污染搜索结果 | 仅 mock（本报告已修正 mock 契约：`web_read` 需 `resp.raw.read()`），mock 3/3 |
| F5 | `clipboard` / `open_app` / `screenshot` 真实执行 | 会弹窗、会写图片、剪贴板真实调用曾触发崩溃 | 仅 mock。`screenshot` 在 P0 阶段验证过真实 GDI 捕获，本轮未复验 |
| F7 | `translate` / `weather` 真实服务 | 依赖项目 LLM/天气插件，需外网 | 依赖注入桩，返回固定值 |

### 5.3 人工验证项（5 项，本环境无法执行）

按 `acceptance.md` §7.3 手动清单核对，**5 项全部未执行**——均需真实麦克风与真人交互：

| 场景 | 计划要求 | 状态 |
|------|---------|------|
| 1. 语音搜索文件 | 听确认语、看气泡、看 think 动效、听结果播报、截图留证 | ⬜ 未执行 |
| 2. 语音删除确认 | 听预览、说"确定"、开回收站验证、验证可恢复、截图留证 | ⬜ 未执行（脚本级已达 18/18，缺语音链路） |
| 3. 打断 | 播报中按 Ctrl+Alt+D，观察停止与动效切换 | ⬜ 未执行 |
| 4. 降级路径（R6） | 说"讲个笑话"，验证走原 LLM 闲聊 | ⬜ 未执行 |
| 5. 安全边界（R1） | 说"打开 C 盘 Windows 文件夹"，验证拒绝 + 审计留痕 | ⬜ 未执行（脚本级已拦截，缺语音链路） |

**附加未执行项**：真实 GUI 启动验证。本轮仅通过 `tests/test_app.py` 与脚本级装配验证 Agent 层；**未真正 `python run.py` 起窗口确认完整应用内可用**。

### 5.4 未交付项（3 项）

| 编号 | 项 | 计划出处 | 现状 |
|------|----|---------|------|
| F2 | **配置驱动插件装配未接入** | `AGENTS.md` §1.3 原则 7、§9；`tasks.json` Phase H（H-01~H-06） | `core/kernel/loader.py` 已实现、单测 43 项、行为正确（M10 通过），但**除自身测试外无任何生产代码引用**；`agent/bootstrap.py` L266~L426 仍逐个 `new` 各 Provider。D12 只偿还了一半 |
| F6 | 计划引用的产物缺失 | `acceptance.md` §2.5 / §5.5 | `docs/agent/examples/kernel_demo.py`、`tests/agent/test_safety_boundary.py`、`tests/kernel/test_{events,context,registry}.py` 均不存在（实际实现合并为 `test_kernel.py` / `test_kernel_loader.py` / `test_edge_cases.py`） |
| F8 | `tasks.json` 进度从未更新 | `tasks.json` 自身 | 123 项任务 `status` 全为 `None`；仅 Phase A 展开明细，Phase B~H 仍为"待细化"摘要，而 B~H 实际已实现大半。该文件与代码已脱节 |

### 5.5 规划内未做项（P2/P3，非本次范围）

| 债务 | 内容 | 影响 |
|------|------|------|
| D1 | 指代消解无持久化 | 重启后"那个文件"失效 |
| D2 | 确认语缓存静态 | 长尾短语需实时合成 |
| D3 | 单机单用户假设 | 不支持多用户 |
| D4 | 摘要器同步调 LLM | 复杂结果播报延迟 +1s |
| D5 | 无多步任务规划 | "整理并归档"类指令不支持 |
| — | 长期记忆（`agent/seams/memory.py`） | 文件不存在（计划标为 P3） |

---

## 六、交付物清单

### 6.1 代码

| 类别 | 文件 | 规模 |
|------|------|------|
| 插件内核 | `core/kernel/{events,service,registry,context,loader}.py` | loader 329 行 |
| 消息与管线 | `agent/{message,pipeline,tracker,bootstrap}.py` | tracker 213 行 |
| Seam 定义 | `agent/seams/{router,safety,executor,summarizer}.py` | — |
| Provider | router（rule/llm/hybrid）、safety、executor、summarizer（template/llm/hybrid） | hybrid_router 130 行 |
| 工具（18 个） | `agent/tools/{file,system,productivity,browser}_tools.py` + `base.py` + `registry.py` | system 563 / productivity 380 / browser 337 行 |
| 前端/应用集成 | `ui/pet_window.py`（`AgentEventBridge`）、`core/app.py` | — |
| 配置 | `config.yaml` 新增 `agent` 段 | — |

### 6.2 工具清单（18 个，含风险等级）

| 类别 | 工具 | 风险 |
|------|------|------|
| 文件 | `file_search` `file_list` `file_read` | low |
| 文件 | `file_rename` `file_move` | medium |
| 文件 | `file_delete` | high（强制回收站） |
| 系统 | `system_info` `clipboard` | low |
| 系统 | `open_app` `screenshot` | medium |
| 系统 | `run_command` | high |
| 生产力 | `calculate` `translate` `reminder` `weather` | low |
| 浏览器 | `web_open` `web_search` `web_read` | low |

### 6.3 测试

| 指标 | 数值 |
|------|------|
| 全量用例 | 1464 passed / 1 skipped / 0 failed |
| Agent 层用例 | 1124 passed |
| 内核+Agent 覆盖率 | **100%**（3897 语句 / 0 未覆盖）<br>⚠️ 未含 `ack_cache`，见勘误 E1 |
| 内核单独覆盖率 | **100%**（465 语句） |
| 不可达分支 | 3 处 `# pragma: no cover`，均已在源码注明理由 |

### 6.4 验收脚本与文档

| 产物 | 用途 |
|------|------|
| `tools/measure_acceptance_metrics.py` | 10 项指标量化（本报告第三章数据来源） |
| `tools/p1_acceptance_smoke.py` | 端到端 7 场景 18 断言 |
| `docs/agent/evidence/` | 18 个归档证据文件 |
| `AGENTS.md` §11/§12 | P0/P1 实施进度与验收 |
| `README.md` | 架构、工具清单、覆盖率和验收命令 |

---

## 七、计划与实现的偏差汇总

| # | 偏差 | 性质 | 处置建议 |
|---|------|------|---------|
| 1 | 计划 4 个内核测试文件 → 实际合并为 2 个 + 1 个边界文件 | 命名漂移 | 更新 `acceptance.md` §2.x 的文件名 |
| 2 | 计划 `test_safety_boundary.py` → 实际并入 `test_edge_cases.py` | 命名漂移 | 同上 |
| 3 | 计划 `docs/agent/examples/kernel_demo.py` → 不存在 | 产物缺失 | 补写示例脚本或从计划中移除 |
| 4 | 计划路由测试集 50 条 → 实际 73 条 | 计划弱于实现 | 已更新 `acceptance.md` 引用 |
| 5 | 计划 `agent/plugins/*.py`（6 个装配文件）→ 不存在 | 产物缺失 | 见 F2 |
| 6 | `config.yaml` 无 `agent.kernel.plugin_config` | 产物缺失 | 见 F2 |
| 7 | `tasks.json` 状态字段全空 | 文档脱节 | 见 F8 |
| 8 | 确认语缓存：计划"LRU 动态缓存"未做，改为固定短语预热 | 范围调整 | 已在债务表 D2 登记 |

---

## 八、证据索引

| 文件 | 内容 |
|------|------|
| `evidence/g0_coverage.txt` | 内核覆盖率（--cov-fail-under=90） |
| `evidence/g1_router_accuracy.txt` | 路由测试 |
| `evidence/g1_pipeline.txt` | 并行管线测试 |
| `evidence/g2_security_report.txt` | 安全 + 文件工具测试 |
| `evidence/g3_regression.txt` | 全量回归（干净运行） |
| `evidence/g3_e2e/smoke_output.txt` | 端到端 18/18 完整输出 |
| `evidence/g3_e2e/latency_metrics.txt` | 延迟指标副本 |
| `evidence/metrics.txt` / `.json` | 10 项指标完整实测记录 |
| `evidence/phase_A_kernel.txt` ~ `phase_H_integration.txt` | 8 个 Phase 的独立测试输出 |
| `evidence/known_issues/g3_crash_0xC0000005_20260910.txt` | **F1 崩溃现场**（faulthandler 全线程转储，26.5KB） |

复现命令：

```bash
# 指标量化
python tools/measure_acceptance_metrics.py

# 端到端
python tools/p1_acceptance_smoke.py

# 关卡
pytest tests/kernel/ --cov=core/kernel --cov-report=term-missing --cov-fail-under=90 -q
pytest tests/agent/test_router.py tests/agent/test_pipeline.py -q
pytest tests/agent/test_safety.py tests/agent/test_edge_cases.py -q
pytest tests/ -q
```

---

## 九、验收结论

### 9.1 判定

**有条件通过。**

- 10 项最终指标**全部达标**，其中 6 项大幅超标（路由延迟超标 12 倍、感知延迟超标 1250 倍、总延迟超标 577 倍）。
- 8 个 Phase 测试全绿，覆盖率 100%，端到端 18/18。
- 但存在 **1 项开放缺陷（F1 间歇性崩溃）**、**4 项未验证（F3~F5、F7）**、**5 项人工项未执行**、**3 项未交付（F2、F6、F8）**。

### 9.2 通过条件（建议）

| # | 条件 | 优先级 |
|---|------|--------|
| 1 | 修复或规避 F1 间歇性崩溃，使全量回归稳定 `exit=0` | 高 |
| 2 | 执行 5 项人工语音验证并留存截图/录屏 | 高 |
| 3 | 真实环境启动一次应用，确认 Agent 层在完整 GUI 内可用 | 高 |
| 4 | 用真实 LLM API 跑通一次 LLM 路由与润色 | 中 |
| 5 | 将 loader 接入 `build_agent_stack`，补齐 `agent/plugins/` 与配置项 | 中 |
| 6 | 更新 `tasks.json` 状态与 `acceptance.md` 文件名偏差 | 低 |

### 9.3 签署

| 角色 | 结论 | 说明 |
|------|------|------|
| 自动化验收 | 通过 | 关卡 G0~G3 + 10 指标 + 8 Phase |
| 人工验收 | **待执行** | 5 项语音场景 + 1 项 GUI 启动 |
| 综合判定 | **有条件通过** | 待 9.2 第 1~3 项完成后方可转为"通过" |

---

**报告版本：** v1.0
**生成方式：** 脚本实测（`tools/measure_acceptance_metrics.py` + `tools/p1_acceptance_smoke.py` + pytest）
**下次复核：** F1 修复后 / 人工验证完成后
