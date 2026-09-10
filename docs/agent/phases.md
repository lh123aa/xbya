# 欣雅 Agent 层 — 历史阶段实施记录（P0 / P1 / P2 / F2）

> 本文件是 `AGENTS.md` 的**历史归档**：P0 与 P1 的逐模块交付、P2 三项技术债偿还、
> F2（插件加载器接入）的全部细节都在这里。
>
> 之所以从 `AGENTS.md` 拆出来：`AGENTS.md` 会被当作工作区指令整体加载，有 65536 字节
> 上限；P3 写完后全文超限被截断，会把最新的约定切掉。历史记录有价值，但不该挤占
> 「当前约定与规范」的预算。**当前实施状态见 `AGENTS.md` §15 / §16。**
>
> 另有两份专门的报告：`docs/agent/acceptance-report-p1.md`（P1 系统性验收报告）、
> `docs/agent/evidence/`（P1 证据归档）。

---

## 十一、P0 实施进度

> 更新于 P0 完成时。验收关卡 G0~G3 全部通过。

### 11.1 已交付模块

| 模块 | 文件 | 测试 | 状态 |
|------|------|------|------|
| 插件内核 | `core/kernel/{events,service,registry,context,__init__}.py` | `tests/kernel/test_kernel.py`（80） | ✅ 覆盖率 99% |
| 消息协议 | `agent/message.py` | 并入下列测试 | ✅ |
| 工具框架 | `agent/tools/{base,registry}.py` | `tests/agent/test_file_tools.py` | ✅ |
| 意图路由 | `agent/seams/router.py` + `agent/providers/router/rule_router.py` | `tests/agent/test_router.py`（31） | ✅ 准确率 100%，P95 0.25ms |
| 安全守卫 | `agent/seams/safety.py` + `agent/providers/safety/basic_guard.py` | `tests/agent/test_safety.py`（59） | ✅ 覆盖率 90% |
| 执行器 | `agent/seams/executor.py` + `agent/providers/executor/thread_pool.py` | 并入 `test_pipeline.py` | ✅ |
| 6 个文件工具 | `agent/tools/file_tools.py` | `tests/agent/test_file_tools.py`（84） | ✅ |
| 确认语缓存 | `services/ack_cache.py` | 并入 `test_pipeline.py` | ✅ 命中 <10ms |
| 并行管线 | `agent/pipeline.py` | `tests/agent/test_pipeline.py` | ✅ ack∥submit 间隔 <50ms |
| 装配引导 | `agent/bootstrap.py` | `tests/agent/test_integration.py`（27） | ✅ |
| 前端集成 | `ui/pet_window.py`（`AgentEventBridge` + 事件槽） | 同上 | ✅ |
| 应用集成 | `core/app.py`（`_setup_agent_layer` / `_teardown_agent_layer`） | `tests/test_app.py` | ✅ |
| 配置 | `config.yaml` 新增 `agent` 段 | `test_integration.py` | ✅ |
| 会话收尾 | `conftest.py` | — | ✅ 偿还 D7/D8 |

### 11.2 实测指标（对照 7.1）

| 指标 | 目标 | 实测 | 结论 |
|------|------|------|------|
| 规则路由准确率 | >90% | **100%**（50/50） | ✅ 超标 |
| 规则路由延迟 | <10ms | **平均 0.066ms / P95 0.25ms** | ✅ 超标 40 倍 |
| 感知延迟 | <1.5s | 确认语缓存命中 ≈0ms | ✅ |
| 误操作率 | 0 | 白名单/穿越/UNC/软链/空字节 全拦截 | ✅ |
| 删除可恢复率 | 100% | 源码审查无永久删除调用 | ✅ |
| 内核覆盖率 | >90% | **99%** | ✅ |
| 现有功能回归 | 0 失败 | **669 passed, 0 failed** | ✅ |
| 插件可卸载 | 是 | `dispose()` 幂等 + 卸载后事件不再响应 | ✅ |
| 测试退出稳定性 | — | 4/4 run exit=0，无 stderr 噪音 | ✅ 偿还 D7 |

### 11.3 未交付（转入 P1）

- LLM 路由 / 混合路由（D10）
- 实体追踪 / 指代消解（D11）
- 配置驱动插件加载器（D12）
- 结果摘要器（模板 + LLM 润色）
- 系统工具集 / 浏览器工具集 / 生产力工具集
- 长期记忆

### 11.4 关键设计修正（实施中发现）

1. **安全守卫的校验范围**：裸文件名与目录别名（`Desktop`、`b.txt`）不应被当作相对路径校验——它们由工具在白名单目录内解析，天然安全。只有"看起来是路径"的取值（绝对路径/含分隔符/`~`）才需要白名单校验。此修正避免了"找一下桌面上的合同"被误拒。
2. **操作预览归属工具层**：删除操作的影响范围需要先扫描才知道，而扫描是工具的能力。为此在 `BaseTool` 增加 `preview(params)` 钩子，由管线在请求确认前调用。
3. **跨线程 UI 安全**：Agent 事件可能由执行线程发出，`AgentEventBridge` 统一转成 Qt 信号，由队列连接投递回 UI 线程。
4. **运行时关闭防御**：`_speak_sentences` / `app.synthesize` / edge_tts 插件三层加 `sys.is_finalizing()` 与 `cannot schedule new futures` 判断，避免退出阶段刷错误日志。

---

## 十二、P1 实施进度与最终验收

> 更新于 P1 完成时（最后一期验收）。关卡 G0~G2 通过，G3 有条件通过。
> **完整验收报告：`docs/agent/acceptance-report-p1.md`；证据归档：`docs/agent/evidence/`。**

### 12.1 P1 已交付模块

| 模块 | 文件 | 规格 |
|------|------|------|
| LLM 路由 | `agent/providers/router/llm_router.py` | 15 个工具 schema，3 种响应形态解析，`allowed_actions()` 由实例工具反推 |
| 混合路由 | `agent/providers/router/hybrid_router.py` | 规则优先，置信度 < 阈值才转 LLM，LLM 失败 → 规则 → 闲聊 |
| 实体追踪 | `agent/tracker.py` | 序数（`第N个`/`最后一个`/`首个`）+ 集合 + 单数指代；FIFO 容量 20 文件 / 10 动作 |
| 结果摘要 | `agent/providers/summarizer/{template_sum,llm_sum,hybrid_sum}.py` | 模板按 action 分派；失败/截断/>阈值/网络类动作转 LLM 润色 |
| 配置驱动加载 | `core/kernel/loader.py` | `parse_config` → `resolve_order`（Kahn + 环检测）→ `load`/`unload`；`mark`/`reclaim` 实现注册所有权移交 |
| 系统工具（5） | `agent/tools/system_tools.py` | `system_info`（中英指标别名）/`clipboard`/`open_app`/`screenshot`（真实 GDI 截屏）/`run_command` |
| 生产力工具（4） | `agent/tools/productivity_tools.py` | `calculate`（AST 白名单 + 中文数字/运算符）、`translate`、`reminder`、`weather` |
| 浏览器工具（3） | `agent/tools/browser_tools.py` | `web_open`/`web_search`/`web_read`；url 规范化 + SSRF 防护（拒内网/回环/链路本地） |
| 工具预览钩子 | `agent/tools/base.py` | `BaseTool.preview(params)`：确认前展示影响范围 |
| 最终验收脚本 | `tools/p1_acceptance_smoke.py` | 真实 config 装配 + 7 个端到端场景，18 项断言 |

规模：新增/修改 **18 个工具**，规则路由意图 **18 个**。

### 12.2 最终验收指标（对照 7.1）

| 指标 | 目标 | 实测 | 结论 |
|------|------|------|------|
| 规则路由准确率 | >90% | **100%**（73/73 条测试集） | ✅ 超标 |
| 规则路由延迟 | <10ms | 平均 0.066ms / P95 0.25ms（P0 实测，P1 未劣化） | ✅ 超标 40 倍 |
| 感知延迟（首次反馈） | <1.5s | 确认语缓存命中 ≈0ms | ✅ |
| 总延迟（结果播报） | <3s | 端到端 22ms（只读指令，含确认语） | ✅ 超标 130 倍 |
| 误操作率 | 0 | 白名单/穿越/UNC/软链/空字节 全拦截（含端到端场景 5） | ✅ |
| 删除可恢复率 | 100% | 强制 `send2trash`，源码审查无永久删除分支 | ✅ |
| 工具执行成功率 | >95% | 全量测试中工具调用零未捕获异常 | ✅ |
| 内核+Agent 覆盖率 | >90% | **100%**（4078 语句 / 0 未覆盖，含 `services/ack_cache`） | ✅ 超标 |
| 现有功能回归 | 0 失败 | **1464 passed, 1 skipped, 0 failed, exit=0** | ✅ |
| 插件可卸载 | 是 | `unload` 逆序执行 disposer，单个失败不中断 | ✅ |
| 测试退出稳定性 | — | ⚠️ **不达标**：10 次全量运行复现 1 次 `0xC0000005`（见 D7 / 报告 F1） | ❌ 未达标 |

**覆盖率汇总（`tests/kernel` + `tests/agent`）**

```bash
# 单文件模块必须用点号形式；写成 services/ack_cache 会被 coverage 静默忽略
pytest tests/kernel tests/agent \
  --cov=core.kernel --cov=agent --cov=services.ack_cache --cov-report=term-missing
```

```
TOTAL   4078   0   100%
```

剩余 `# pragma: no cover` 仅 3 处，均为**结构上不可达**的防御分支，已在源码注明理由：
- `agent/tools/browser_tools.py` — `BLOCKED_SCHEMES` 二次校验（协议白名单已先拦截）
- `agent/tools/productivity_tools.py` — `cn_to_number` 的"非数字字符"出口（调用方正则只喂数字串）
- `agent/tracker.py` — 序数 token 为空出口（正则两条分支必然填充分组）

### 12.3 端到端验收证据（`tools/p1_acceptance_smoke.py`，18/18 通过）

| # | 场景 | 结果 |
|---|------|------|
| 1 | 真实配置装配 | `tools=18 router=HybridRouter enabled=True` |
| 2 | 只读：搜索文件 | `找到「合同_2025.pdf」，5字节，9月10号`，总延迟 22ms |
| 3 | 只读：系统状态 | `CPU 用了 14.6%，内存用了 73.6%，电量 0%，磁盘用了 91.1%` |
| 4 | 危险：删除 → 确认 → 取消 | 发出 high 风险确认；确认前未执行；取消后文件完好 |
| 5 | 危险：删除 → 确认 → 回收站 | `已经把 1 个文件移到回收站啦`，文件确实消失 |
| 6 | 安全边界：白名单外路径 | `这个位置我不能动哦，只能操作：…`（审计记录 rejected） |
| 7 | 审计日志 | `audit.db` 已生成（16384 bytes） |

### 12.4 P1 实施中发现并修复的缺陷

1. **口语化系统状态被误判为读文件**（端到端验收暴露）
   `看看电脑状态` → `file_read`（因 `file_read` 的「看看」权重 1.5 而 `system_info` 无匹配）。
   修复：为 `system_info` 补充口语关键词（`电脑状态`/`电脑怎么样`/`电脑卡`/`多少电` 等），
   并新增 7 条正向用例 + 2 条反向保障用例（`看看电脑上的笔记` 仍须是 `file_read`）。
2. **延迟统计在非语音入口恒为 0**
   只有 `_on_speech` 记录 `_request_t0`，直接调用 `handle_text` 时 `elapsed_ms` 恒等于 0，
   导致 `stats()` 的 `result_p50/p95` 失真、验收指标无法度量。
   修复：`handle_text` 增加 `setdefault` 补记起始时刻；新增回归用例断言 `elapsed_ms > 0`。
3. **`_find_cycle` 从未真正回报环路径**（覆盖率收尾暴露）
   原实现的 `while` 条件在环上必然以 `node is None` 退出，`if node in seen` 分支不可达，
   环路径实际总是退回兜底值。修复：改为"进入节点时判重"，现返回真正闭合的环（如 `a→b→a`）。
4. **剪贴板 ctypes 原型截断**（前序修复，此处归档）
   ctypes 默认 `restype=c_int` 在 64 位下把 HANDLE 截断成 32 位，解引用即访问违例（0xC0000409）。
   修复：`ClipboardTool._win32()` 缓存并显式声明全部 `restype/argtypes`。

### 12.5 未交付（转入 P2/P3）

- 长期记忆 / 指代消解持久化（D1）
- 确认语 LRU 动态缓存（D2）
- 摘要器异步化（D4）
- 多步任务规划 planner seam（D5）
- 多用户隔离（D3）

### 12.6 验收关卡结论

> 关卡定义见 `docs/agent/acceptance.md` §1.1。完整报告见 **`docs/agent/acceptance-report-p1.md`**。

| 关卡 | 计划通过条件 | 实测 | 结论 |
|------|-------------|------|------|
| G0 | Phase A 完成：内核覆盖率 >90%、零回归、示例可运行 | 内核覆盖率 **100%**（465 语句 / 0 未覆盖）；133 passed | ✅ 通过（示例脚本缺失，见报告 F6） |
| G1 | Phase B+C 完成：管线事件可观测、路由准确率 >90% | 准确率 **100%**（73/73）；管线 49 passed | ✅ 通过 |
| G2 | Phase D+E+F 完成：安全边界 0 失败、删除 100% 可恢复 | 安全+边界+文件工具 155 passed / **0 failed**；无永久删除分支 | ✅ 通过 |
| G3 | Phase G+H 完成：端到端跑通、延迟达标、无回归 | 端到端 **18/18**；延迟 max 5.2ms；全量 1464 passed | ⚠️ **有条件通过**（间歇性崩溃，见 D7） |

**P1 阶段判定：有条件通过。**

10 项最终指标全部达标、8 个 Phase 测试全绿、覆盖率 100%、端到端 18/18；
但尚存 **1 项开放缺陷**（D7 间歇性崩溃）、**4 项未验证**（LLM 真实调用、浏览器联网、剪贴板/开应用/截屏真实执行、翻译/天气真实服务）、
**5 项人工项未执行**（语音搜索 / 语音删除确认 / 打断 / 降级路径 / 安全边界，另加真实 GUI 启动）、
**3 项未交付**（loader 接入、计划引用的示例与测试文件、tasks.json 状态更新）。

转为"通过"的条件（按优先级）：

1. 修复或规避 D7 间歇性崩溃，使全量回归稳定 `exit=0`
2. 执行 5 项人工语音验证并留存截图/录屏
3. 真实环境启动一次应用，确认 Agent 层在完整 GUI 内可用
4. 用真实 LLM API 跑通一次 LLM 路由与润色
5. 将 loader 接入 `build_agent_stack`，补齐 `agent/plugins/` 与配置项
6. 更新 `tasks.json` 状态与 `acceptance.md` 文件名偏差

---

## 十三、P2 实施进度

> 任务分解见 `docs/agent/tasks-p2.json`。逐项完成即更新本节。

### 13.1 P2 范围（3 项，均为技术债偿还）

| 任务 | 债务 | 目标 | 状态 |
|------|------|------|------|
| P2-1 | D2 | 确认语 LRU 动态缓存 | ✅ **已完成** |
| P2-2 | D4 | 摘要器异步化（先播模板，LLM 润色后补播） | ✅ **已完成** |
| P2-3 | D1 | 指代消解持久化（重启后仍可解析"那个文件"） | ✅ **已完成** |

**明确不在 P2 范围**：D3 多用户隔离（框架已支持 `ctx.fork()`，无产品需求）、D5 多步任务规划（P3 planner seam）、长期记忆 / 向量 DB（P3）、Computer Use 等 v3.0 产品愿景。
**P1 遗留缺陷 F1（间歇性崩溃）/ F2（loader 未接入）** 属 P1 收尾，不混入 P2。

### 13.2 P2-1 交付详情（确认语 LRU 动态缓存）

| 项 | 内容 |
|----|------|
| 新增 API | `AckCache.get_or_synthesize(text, synthesize=None)` — 命中直接返回，未命中合成后入栈 |
| 查找顺序 | ① 动态 LRU 命中并提升优先级 → ② 预热短语反查命中（提升进 LRU） → ③ 实时合成并写入 LRU |
| 淘汰策略 | `OrderedDict` 实现，超容量淘汰最久未用；命中时 `move_to_end` |
| 容量配置 | `agent.pipeline.ack_lru_size`（默认 64；0 = 关闭动态缓存，退化为直通） |
| 统计 | `AckCache.lru_stats()` → hits / misses / evictions / size / maxsize |
| 线程安全 | 写入与顺序升级均在锁内；并发压测 8 线程 × 50 次容量不越界 |
| 失败语义 | 合成返回 `None` / 空字节 / 抛异常 → 不写入缓存（避免负缓存污染），调用方降级 |
| 管线接入 | 确认问句（含文件预览的长尾文本）经 LRU 合成；`FEEDBACK_CONFIRM` 新增 `audio` 字段 |
| 统计透出 | `pipeline.stats()` 新增 `ack_lru_size` / `ack_lru_hits` / `ack_lru_misses` / `ack_lru_evictions` |
| 线程收尾 | 新增 `AckCache.close(timeout)`：取消预热并**有界 join** 预热线程；`bootstrap` 的 disposer 由 `cancel` 改为 `close` |
| 测试 | `tests/agent/test_ack_lru.py`（33 项） |

**实测**：同一确认问句第二次命中，`synthesize` 调用次数 1，命中延迟 <10ms；容量 4 塞 5 条淘汰最久未用者；8 线程并发后 `size == maxsize`。

### 13.3 P2-1 顺带修复的 P1 遗留问题

**F1 崩溃的参与者**：`g3_crash_0xC0000005_20260910.txt` 的线程转储显示，崩溃时存活线程的栈帧是
`services/ack_cache.py:128 in _do_warm_up` —— 多个确认语预热线程在解释器 teardown 阶段仍存活。
原 `cancel()` 只置标志位，若预热线程正阻塞在慢 TTS 调用里便不会被回收；
全量测试反复装配/释放 Agent 栈，这些线程不断堆积参与退出竞态。
已通过 `AckCache.close(timeout)` 做有界 join 修复。**D7 是否因此完全消除待累计更多轮全量运行验证**，暂不关闭。

### 13.4 覆盖率命令勘误（影响 P1 验收证据）

系统性验收时发现：`--cov=services/ack_cache`（斜杠形式）**不生效**，coverage 报
`Module services/ack_cache was never imported` 并静默跳过该模块 —— 而 P1 报告中的
`TOTAL 3897 0 100%` 正是用这条命令跑出来的，即**当时的覆盖率并未包含 `services/ack_cache.py`**。

修正后（点号形式 `--cov=services.ack_cache`）：

```
services\ack_cache.py    178    0   100%
TOTAL                   4078    0   100%
```

规则：**单文件模块必须用点号形式**（`services.ack_cache`），目录可用路径形式（`core/kernel`）。
已同步修正 `README.md`、`docs/agent/acceptance.md` 与验收报告。

### 13.5 P2-3 交付详情（指代消解持久化）

| 项 | 内容 |
|----|------|
| 新增模块 | `agent/tracker_store.py` — `TrackerStore`，实体栈的 JSON 持久化后端 |
| 新增 API | `EntityTracker.to_dict()` / `from_dict(data)`、`ActionRecord.to_dict()` / `from_dict()`；`ActionRecord` 增加 `timestamp` |
| 落盘字段 | 仅 `name` / `path` / `size` / `mtime` / `is_dir`（`PERSIST_FILE_KEYS`），**不写文件内容**；字符串截断至 512 字符 |
| 原子写 | 同目录 `mkstemp` → `json.dump` + `fsync` → `os.replace`；失败清理临时文件，且清理失败也不抛异常 |
| 节流写 | 默认 1s 内至多落盘一次（`save`）；`flush` 忽略节流强制写 |
| 时序 | `AgentStack.start()` → `load_into` 恢复；`dispose()` → disposer 链强制 `flush` |
| 容错 | 文件缺失/JSON 损坏/顶层非对象/版本过高/字段类型错/条目非法 → 一律退化为空栈 + warning，**不阻塞启动** |
| 安全边界 | 拒绝把状态写入用户桌面/文档/下载/图片（`_is_inside_forbidden_dir`），命中即关闭持久化并告警 |
| 配置 | `agent.pipeline.tracker_persist`（默认 true）、`agent.pipeline.tracker_store`（默认 `./data/agent_entities.json`） |
| 统计 | `pipeline.stats()` 新增 `tracker_restored`；`AgentStack.stats()` 新增 `tracker_store` 快照 |
| 不入库 | `.gitignore` 增加 `data/agent_entities.json*` 与 `data/agent_audit.db` |
| 测试 | `tests/agent/test_tracker_persist.py`（47 项） |

**实测闭环**：装配 → 写入 3 个实体 → `dispose()` 落盘 → 重新装配 → `start()` 恢复 3 条 →
`"第二个"` 解析为 `f2.png`、`"最后一个"` 为 `f3.png`、`"那些"` 返回 3 条；
损坏 JSON 与字段类型错均退化为空栈且不阻塞启动。

### 13.6 P2-3 顺带修复的缺陷：延迟统计用了粗粒度时钟

`AgentPipeline._elapsed_ms` 原用 `time.time()` 计算差值。`time.time()` 在 Windows 上
分辨率约 15.6ms，于是"亚毫秒即完成"的请求会算出 `elapsed_ms == 0.0` —— 这既让
`stats()` 的 `ack_p50/p95`、`result_p50/p95` 失去意义，也让针对该字段的断言不稳定。
改为 `time.perf_counter()`（单调、高精度），`_on_speech` 与 `handle_text` 两处记录点同步更换。

> 该缺陷由 P2-3 的回归用例 `test_handle_text_records_real_latency` 暴露：单独跑该文件时通过，
> 全量运行时因时序不同算出 0.0 而失败。修复后连续 3 轮全量运行全绿。

### 13.7 P2 验收关卡

| 关卡 | 通过条件 | 实测（P2-1 + P2-3 后） | 结论 |
|------|---------|----------------------|------|
| P2-G0 | 语法检查通过 | `py_compile` 全部通过 | ✅ |
| P2-G1 | 专项测试通过 | `test_ack_lru.py` 33 + `test_tracker_persist.py` 47 = 80 passed | ✅ |
| P2-G2 | 全量回归零失败 | **1545 passed / 1 skipped / 0 failed / exit=0**（连续 3 轮） | ✅ |
| P2-G3 | 覆盖率保持 100% | 内核+Agent+ack_cache **4261 语句 / 0 未覆盖 / 100%** | ✅ |
| P2-G4 | 端到端不回退 | `p1_acceptance_smoke.py` **18/18** | ✅ |
| P2-G5 | 文档回写 | 债务表 D1/D2 标记偿还、本节、tasks-p2.json、README、.gitignore | ✅ |

### 13.8 P2-2 交付详情（摘要器异步化）

| 项 | 内容 |
|----|------|
| 新事件 | `feedback.refine` — `{request_id, summary, previous, action}` |
| Seam 扩展 | `SummarizerService.fast_summary()`（廉价即时摘要，默认等价 summarize）+ `should_refine()`（默认 False）+ `refine()`（默认空串） |
| 分流语义 | 立即播报走 `fast_summary`（绝不碰 LLM）；`should_refine` 为真才另起线程做 `refine` |
| HybridSummarizer | `fast_summary` 只走模板；`refine` 强制走 LLM，并把"LLM 失败回退模板"识别为无改善（返回空串） |
| 线程模型 | 每个待润色请求一个 **daemon 线程**（复用工具执行池会被后续指令排队拖慢；daemon 保证退出不被 join —— D7 教训） |
| 取消语义 | 打断该 request_id → 结果作废不补播；`stop()` 置停止位并有界 join（`refine_timeout`，默认 10s） |
| 不发补播的情形 | 润色空、与模板文案相同、被打断、已停止、LLM 抛异常 |
| 配置 | `agent.summarizer.async_refine`（默认 true；false 时回退 P1 同步行为） |
| 统计 | `pipeline.stats()` 新增 `refined` / `refine_skipped` / `refine_pending` |
| UI 接线 | `AgentEventBridge.refineReceived` + `PetWindow._on_agent_refine`（正在播报时只更新气泡，不叠话） |
| 测试 | `tests/agent/test_async_refine.py`（26 项） |

**实测（慢 LLM 400ms 场景）**：

| 事件 | 时刻 | 文案 |
|------|------|------|
| `feedback.result` | **3ms** | 找到 6 个文件（f5.txt、f4.txt、f2.txt 等 6 个） |
| `feedback.refine` | 305ms | 润色后的自然语言播报 |

即"用户听到结果"的时刻从原先的 ~305ms（等 LLM）降到 **3ms**，提升约 100 倍。
简单结果仍直接跳过润色（`refine_skipped` 计数），不浪费 LLM 配额。

### 13.9 P2 验收关卡

| 关卡 | 通过条件 | 实测（P2 三项全部完成后） | 结论 |
|------|---------|--------------------------|------|
| P2-G0 | 语法检查通过 | `py_compile` 全部通过 | ✅ |
| P2-G1 | 专项测试通过 | `test_ack_lru` 33 + `test_tracker_persist` 47 + `test_async_refine` 26 = 106 passed | ✅ |
| P2-G2 | 全量回归零失败 | **1579 passed / 1 skipped / 0 failed / exit=0** | ✅（见 §13.10 关于 D7 的说明） |
| P2-G3 | 覆盖率保持 100% | 内核+Agent+ack_cache **4366 语句 / 0 未覆盖 / 100%** | ✅ |
| P2-G4 | 端到端不回退 | `p1_acceptance_smoke.py` **18/18**；`measure_acceptance_metrics.py` 8/8 | ✅ |
| P2-G5 | 文档回写 | 债务表 D1/D2/D4 标记偿还、本节、tasks-p2.json、README、.gitignore | ✅ |

### 13.10 D7 间歇性崩溃：定位到一处确定性成因并修复

P2 期间该崩溃又复现两次，两次故障帧完全一致：`tests/test_vrm_bridge.py:310 in test_real_timer_fires_by_event_loop`
（即 `QEventLoop.exec()`）。据此做了定向排查与实验：

**排查结论**：该文件的 `make_bridge` fixture **没有任何 teardown**。它用 `FakeView` + **真实 `QWebChannel`** 构造 `VrmBridge`，
而 `VrmBridge` 内部持有一个**正在运行的超时 `QTimer`**。整个文件约 30 个用例创建的桥只在测试结束后被解释器 GC
在任意时刻回收 —— 这些"已析构对象残留的定时器/队列事件"若在后续用例的嵌套事件循环里被投递，就是访问违例。

**处理**：给 `make_bridge` 加确定性收尾 —— 记录本用例创建的桥 → 停表 → `deleteLater()` → `processEvents()`。

**实验结果**（全量 `pytest tests/`，同一批命令）：

| 阶段 | 样本 | 崩溃 |
|------|------|------|
| 修复前（P1 系统性验收 + P2 抽样） | 11 轮 | **2 次**（≈1/5.5） |
| 修复后（含 P2-2 冻结后复测） | **22 轮** | **0 次** |

其中冻结代码后的最终采样为连续 **12/12** 全绿（每轮 1579 passed / 1 skipped / exit=0）。
按 2/11 的基线，22 轮全绿纯属巧合的概率约为 (9/11)^22 ≈ 0.014 —— **已具备统计学意义**，
因此判定该修复为 D7 的**有效缓解**；正式关闭仍建议在 P3 期间继续累计轮次。

**对 P2-G2 的影响**：修后 22 轮全绿使 §13.9 的 P2-G2 结论具备较强支撑。

---

## 十四、F2 偿还：插件加载器真正接入运行时

> 对应 P1 验收报告 §5.4 / 发现 F2。此项属 **P1 收尾**，不混入 P2 范围。

### 14.1 问题回顾

`core/kernel/loader.py` 在 P1 就写完了、43 项单测全绿、行为正确，但**除自身测试外无任何生产代码引用**：
`build_agent_stack` 仍是 `BasicGuard(...)`、`RuleRouter(...)`、`all_file_tools(...)` 一个个 `new` 出来。
于是设计原则 7「配置驱动组装」名不副实 —— 换 Provider 还得改 `bootstrap.py`。

### 14.2 交付内容

**新增 `agent/plugins/` 插件包（10 个插件）**

| 插件 id | 模块 | 产出 | 依赖 |
|---------|------|------|------|
| `kernel` | `kernel_plugin.py` | `tool_registry` | — |
| `safety` | `safety_plugin.py` | `safety`（guard） | kernel |
| `file_tools` | `file_tools_plugin.py` | 6 个文件工具 | safety |
| `system_tools` | `system_tools_plugin.py` | 5 个系统工具 | safety |
| `productivity_tools` | `productivity_tools_plugin.py` | 4 个生产力工具 | kernel |
| `browser_tools` | `browser_tools_plugin.py` | 3 个浏览器工具 | kernel |
| `router` | `router_plugin.py` | `router` | kernel |
| `summarizer` | `summarizer_plugin.py` | `summarizer` | kernel |
| `executor` | `executor_plugin.py` | `executor`（线程池） | 4 个工具插件 |
| `pipeline` | `pipeline_plugin.py` | `ack_cache` / `tracker` / `tracker_store` / `pipeline` | safety, router, summarizer, executor |

> 与 P0 计划的差异：原计划 6 个装配文件（kernel/router/safety/executor/file_tools/summarizer）。
> 实际按「一个关注点一个插件」拆到 10 个 —— 工具集按类别独立成插件，
> 这样"摘掉某组工具"是删清单一行的事（计划里只能靠配置开关）。

**依赖注入方式**（两种，各有明确用途）

- **环境服务** —— `build_agent_stack` 加载插件前预置：
  `agent.config` / `agent.bus` / `agent.tts` / `agent.llm_once` / `agent.llm_route_call` /
  `agent.translate` / `agent.weather` / `agent.reminder_cb`
- **插件间服务** —— `safety` / `tool_registry` / `router` / `executor` / `summarizer` / `ack_cache` /
  `tracker` / `tracker_store` / `pipeline`，顺序由清单 `depends_on` 经拓扑排序保证

**`build_agent_stack` 的职责收窄**：预置环境服务 → 跑加载器 → 取回服务 → 拼 `AgentStack`。
它**不再 new 任何 Provider**（`_build_router` / `_build_summarizer` 已移入对应插件）。

**清单落到真实配置**：`config.yaml` 的 `agent.plugins` 写全 10 条；删掉/注释整段则回退
`DEFAULT_MANIFEST`（向后兼容：既有配置与既有调用方行为不变）。

**可观测量与热插拔**：`AgentStack` 新增 `loader` / `ctx` 属性，`stats()` 新增 `plugins` 字段
（实际加载的插件 id 列表，按拓扑顺序）；`stack.loader.unload("system_tools")` 会让那 5 个工具
**真的从注册表消失**（插件返回的组合 disposer 逆序注销）。

**失败不留半成品**：缺关键服务 → `RuntimeError`（上层据此降级为纯对话模式），
并在抛错前完成插件卸载与上下文释放；插件入口抛异常 → 其注册被 `mark`/`reclaim` 回滚。

### 14.3 验证

| 验证项 | 结果 |
|--------|------|
| 默认清单等价性 | 18 工具 + HybridRouter + HybridSummarizer，与硬编码时代一致 |
| 拓扑顺序 | `kernel < safety < file_tools < executor < pipeline` |
| 换实现（改配置） | `router.provider=rule` → **RuleRouter**；`summarizer.provider=template` → **TemplateSummarizer** |
| 增减能力（改配置） | 关系统+浏览器工具 → 18→10；**从清单删 `system_tools` 条目** → 18→13 |
| 清单条目 `enabled: false` | 该插件不加载，工具数相应减少 |
| 热插拔 | `unload("system_tools")` → 工具数 -5 且 `system_info` 从注册表消失；`unload("safety")` → 服务消失 |
| 真实 config.yaml | 清单 10 条被正确解析并驱动装配（tools=18） |
| 失败路径 | 缺关键服务 → RuntimeError 且上下文已释放；坏模块被计入 failed；失败插件注册被回滚 |
| 新增测试 | `tests/agent/test_plugins.py`（38 项）+ 测试夹具 `tests/agent/boom_plugin.py` |

### 14.4 回归与覆盖率

> 下表是 **F2 收口时（P3 之前）的快照**；P3 后的最新数字见 §15.4。

| 指标 | 结果 |
|------|------|
| 全量回归 | **1617 passed / 1 skipped / 0 failed / exit=0**（连续 2 轮） |
| 覆盖率 | 内核+Agent+ack_cache **4553 语句 / 0 未覆盖 / 100%** |
| 端到端 | `p1_acceptance_smoke.py` **18/18** |
| 指标脚本 | `measure_acceptance_metrics.py` **8/8** |

**副作用（正面）**：插件路径现在是**默认装配路径**，因此全套 1617 个用例都在跑插件装配 ——
加载器不再只有它自己的 43 项测试，而是被整个测试套件持续验证。

### 14.5 P1 "转为通过" 条件的进度

| # | 条件 | 状态 |
|---|------|------|
| 1 | 修复 D7 间歇性崩溃 | 🟡 疑似已修复（22 轮 0 次），待继续累计 |
| 2 | 5 项人工语音验证 | ⬜ 需真机麦克风，无法自动化 |
| 3 | 真实 GUI 启动验证 | ⬜ 待执行（会弹窗口，需用户在场） |
| 4 | 真实 LLM API 跑通路由与润色 | ⬜ 需用户决定是否使用配额 |
| 5 | loader 接入 `build_agent_stack` | ✅ **本次完成** |
| 6 | 修正 `tasks.json` 状态与 `acceptance.md` 文件名漂移 | ✅ **本次完成**（见 §14.6） |

### 14.6 文档漂移修正（P1 报告 F6/F8）

- **`acceptance.md` 文件名漂移**：P0 计划引用 `tests/kernel/test_{events,context,registry}.py` 与
  `tests/agent/test_safety_boundary.py`、`docs/agent/examples/kernel_demo.py`，实际实现合并为
  `test_kernel.py` / `test_kernel_final.py` / `test_edge_cases.py` 且示例脚本不存在。已在 §2.x 标注实际路径。
- **`tasks.json` 状态从未更新**：123 项任务 `status` 全为 `None`。已改为在文件头记录
  `execution_status`（Phase A~H 全部完成、P1/P2 完成、逐任务状态以 AGENTS.md §11~§14 为准），
  不再伪造逐任务状态。

---
