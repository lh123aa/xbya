# P3 阶段实施记录（原 AGENTS.md §15 / §16）

> **来源**：本文件是从 `AGENTS.md` **整段搬出来**的 §15（P3 实施进度）与
> §16（P3 验收关卡），**原文一字未改，只换了位置**。
>
> **为什么搬**：`AGENTS.md` 是工作区指令文档，有 **65536 字节**硬预算，
> 超过会被**静默截断**（实测已发生过一次：P5-B4 加完 D19 后文件到 66697 字节，
> 被截掉 1161 字节）。处置方式与本项目前两次一致：
> §11~§14 → `phases.md`、§17 → `f-closure.md`。
>
> **为什么选这两节**：它们体量最大，且**不是当前阶段的约定**（当前是 P5）。
> 债务表、P5 闭环段、完成度段必须留在 `AGENTS.md` —— G13 与收口审计脚本
> 都按行匹配它们，搬走会让自动检查失明。
>
> **编号不变**：搬出来以后仍叫 §15 / §16，全项目的交叉引用（含源码注释）
> 因此不用改 —— 与 `phases.md` 沿用 §11~§14 的做法一致。

---

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

