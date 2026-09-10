# 欣雅 Agent 层 — 验收标准

> 配套文档：`AGENTS.md` / `spec.md` / `tasks.json`
> 
> **规则：** 每个任务的验收标准必须可执行。逻辑类功能必须转化为 pytest 用例。

---

## 一、验收总览

### 1.1 验收关卡

| 关卡 | 触发时机 | 通过条件 |
|------|---------|---------|
| **G0** | Phase A 完成 | 内核覆盖率 >90%，现有测试零回归，示例可运行 |
| **G1** | Phase B+C 完成 | 并行管线事件可观测，规则路由准确率 >90% |
| **G2** | Phase D+E+F 完成 | 安全边界测试 0 失败，删除 100% 可恢复 |
| **G3** | Phase G+H 完成 | 端到端跑通，延迟达标，现有功能无回归 |

### 1.2 最终验收指标

| # | 指标 | 目标值 | 测量方式 |
|---|------|--------|---------|
| 1 | 规则路由准确率 | >90% | 50 条测试指令集 |
| 2 | 规则路由延迟 | <10ms | pytest 计时 |
| 3 | 感知延迟（首次反馈） | <1.5s | 端到端计时 |
| 4 | 总延迟（结果播报） | <3s | 端到端计时 |
| 5 | 误操作率 | 0 | 白名单测试 + 审计日志 |
| 6 | 删除可恢复率 | 100% | 回收站验证 |
| 7 | 工具执行成功率 | >95% | 执行统计 |
| 8 | 内核测试覆盖率 | >90% | pytest --cov |
| 9 | 现有功能回归 | 0 失败 | 全量 pytest |
| 10 | 插件可卸载 | 是 | 卸载后服务不可用测试 |

---

## 二、Phase A — 插件内核验收

### 2.1 测试文件：`tests/kernel/test_kernel.py`

> **实现说明（P1 收尾勘误）**：本文档原先为 Phase A 规划了
> `test_events.py` / `test_context.py` / `test_registry.py` 三个文件，
> 实际实现合并为 `tests/kernel/test_kernel.py`（事件总线 / Context / Registry）
> 与 `tests/kernel/test_kernel_final.py`（回收边界与环检测）。
> 下面的用例内容仍然有效，只是文件归属不同。

```python
"""事件总线测试"""

def test_event_dataclass():
    """A-01: Event 可实例化，默认值正确"""
    e = Event(type="test.event", data={"k": "v"})
    assert e.type == "test.event"
    assert e.data == {"k": "v"}
    assert e.timestamp is not None      # 自动填充
    assert e.source == ""               # 默认空串（原写 None，与内核实现不符）

def test_on_off():
    """A-02: 订阅与取消订阅"""
    bus = EventBus()
    calls = []
    dispose = bus.on("test", lambda e: calls.append(e))
    bus.emit("test", v=1)
    assert len(calls) == 1
    dispose()
    bus.emit("test", v=2)
    assert len(calls) == 1              # 已取消，不再触发

def test_off_returns_bool():
    """A-02: off 精确移除，重复移除返回 False"""
    bus = EventBus()
    h = lambda e: None
    bus.on("test", h)
    assert bus.off("test", h) is True
    assert bus.off("test", h) is False  # 重复移除不抛异常

def test_emit_isolation():
    """A-03: 单 handler 异常不影响其他 handler（R3 防护）"""
    bus = EventBus()
    calls = []
    bus.on("test", lambda e: (_ for _ in ()).throw(RuntimeError("boom")))
    bus.on("test", lambda e: calls.append(1))
    bus.emit("test")                     # 不应抛出
    assert len(calls) == 1               # 第二个 handler 仍执行

def test_once_and_any():
    """A-04: 一次性订阅与通配订阅"""
    bus = EventBus()
    calls = []
    bus.once("test", lambda e: calls.append("once"))
    bus.emit("test")
    bus.emit("test")
    assert calls == ["once"]
    
    all_calls = []
    bus.on_any(lambda e: all_calls.append(e.type))
    bus.emit("a")
    bus.emit("b")
    assert all_calls == ["a", "b"]
```

### 2.2 测试文件：`tests/kernel/test_kernel.py`（Context 部分）

```python
"""Context 测试"""

def test_provide_and_use():
    """A-08/A-09: 注册与获取"""
    ctx = Context()
    ctx.provide("tts", "fake_tts")
    assert ctx.use("tts") == "fake_tts"

def test_use_not_found():
    """A-09: 未找到服务抛 ServiceNotFound"""
    ctx = Context()
    with pytest.raises(ServiceNotFound) as exc:
        ctx.use("nonexistent")
    assert "nonexistent" in str(exc.value)

def test_use_parent_lookup():
    """A-09: 父级查找"""
    parent = Context()
    parent.provide("tts", "parent_tts")
    child = parent.fork()
    assert child.use("tts") == "parent_tts"

def test_fork_isolation():
    """A-10: 子 context 注册不影响父"""
    parent = Context()
    parent.provide("a", 1)
    child = parent.fork()
    child.provide("b", 2)
    assert child.use("a") == 1          # 继承父
    assert child.use("b") == 2          # 子独有
    with pytest.raises(ServiceNotFound):
        parent.use("b")                  # 父不可见

def test_dispose_reverse_order():
    """A-11: 逆序清理"""
    ctx = Context()
    order = []
    ctx.provide("a", 1)
    ctx._disposers[0] = lambda: order.append("a")
    ctx.provide("b", 2)
    ctx._disposers[1] = lambda: order.append("b")
    ctx.dispose()
    assert order == ["b", "a"]           # 逆序

def test_dispose_isolation():
    """A-11: 单个 disposer 异常不中断清理"""
    ctx = Context()
    cleaned = []
    def bad(): raise RuntimeError("boom")
    def good(): cleaned.append("ok")
    ctx._disposers = [bad, good]
    ctx.dispose()                        # 不应抛出
    assert cleaned == ["ok"]

def test_dispose_idempotent():
    """A-11: dispose 幂等"""
    ctx = Context()
    ctx.provide("a", 1)
    ctx.dispose()
    ctx.dispose()                        # 第二次不应抛异常

def test_context_events_cleanup():
    """A-12: dispose 清理所有订阅"""
    ctx = Context()
    calls = []
    ctx.on("test", lambda e: calls.append(1))
    ctx.dispose()
    ctx.emit("test")
    # 订阅已被清理（emit 无 handler 或已注销）
```

### 2.3 测试文件：`tests/kernel/test_kernel.py`（Registry 部分）

```python
"""Registry 测试"""

def test_register_and_get():
    """A-14: 注册与获取"""
    reg = Registry()
    reg.register("item1", name="a")
    assert reg.get("a") == "item1"
    assert reg.has("a") is True

def test_register_from_attr():
    """A-14: 从对象 name 属性推导"""
    class Tool:
        name = "mytool"
    reg = Registry()
    reg.register(Tool())                 # 自动用 name 属性
    assert reg.has("mytool")

def test_register_no_name_raises():
    """A-14: 无法推导 name 时抛异常"""
    reg = Registry()
    with pytest.raises(ValueError):
        reg.register(object())           # 无 name 属性

def test_disposer():
    """A-14: 返回的 disposer 可注销"""
    reg = Registry()
    dispose = reg.register("item", name="a")
    dispose()
    assert reg.has("a") is False

def test_snapshot_semantics():
    """A-15: names/items 返回快照"""
    reg = Registry()
    reg.register("a", name="a")
    names = reg.names()
    reg.register("b", name="b")
    assert names == ["a"]                # 快照不受后续修改影响

def test_filter():
    """A-15: 谓词过滤"""
    reg = Registry()
    reg.register(1, name="one")
    reg.register(2, name="two")
    reg.register(3, name="three")
    assert reg.filter(lambda x: x > 1) == [2, 3]
```

### 2.4 测试文件：`tests/kernel/test_loader.py`

```python
"""插件加载器测试"""

def test_parse_config():
    """A-17: 解析配置"""
    cfg = {"plugins": [
        {"id": "a", "module": "m1", "entry": "setup"},
        {"id": "b", "module": "m2", "entry": "setup", "enabled": False},
    ]}
    specs = PluginLoader.parse_config(cfg)
    assert len(specs) == 1               # enabled=false 被过滤
    assert specs[0].id == "a"

def test_parse_config_missing_field():
    """A-17: 缺必填字段报错"""
    cfg = {"plugins": [{"id": "a"}]}     # 缺 module/entry
    with pytest.raises(ConfigError):
        PluginLoader.parse_config(cfg)

def test_topology_order():
    """A-18: 依赖拓扑排序"""
    specs = [
        PluginSpec(id="c", depends_on=["b"]),
        PluginSpec(id="b", depends_on=["a"]),
        PluginSpec(id="a"),
    ]
    ordered = PluginLoader.resolve_order(specs)
    ids = [s.id for s in ordered]
    assert ids.index("a") < ids.index("b") < ids.index("c")

def test_topology_circular():
    """A-18: 循环依赖检测"""
    specs = [
        PluginSpec(id="a", depends_on=["b"]),
        PluginSpec(id="b", depends_on=["a"]),
    ]
    with pytest.raises(CircularDependencyError) as exc:
        PluginLoader.resolve_order(specs)
    assert "a" in str(exc.value) and "b" in str(exc.value)

def test_topology_missing_dep():
    """A-18: 缺失依赖检测"""
    specs = [PluginSpec(id="a", depends_on=["nonexistent"])]
    with pytest.raises(MissingDependencyError):
        PluginLoader.resolve_order(specs)

def test_load_and_unload(tmp_path):
    """A-19/A-20: 加载与卸载"""
    # 创建临时插件模块
    mod = tmp_path / "test_plugin.py"
    mod.write_text('''
def setup(ctx, **cfg):
    ctx.provide("test_service", "hello")
''')
    # 加载 → 服务可用
    # 卸载 → 服务不可用
```

### 2.5 Phase A 验收命令

```bash
# 覆盖率检查
pytest tests/kernel/ --cov=core/kernel --cov-report=term-missing --cov-fail-under=90

# 零回归
pytest tests/ -q

# 示例可运行
# 勘误：计划中的 docs/agent/examples/kernel_demo.py 在 P1 收尾时并不存在（见下方勘误说明），
#       2026-09-10 已补齐 —— 自包含断言脚本，5 节演示 / 113 条断言，退出码 0，
#       原始输出归档在 docs/agent/evidence/g0/kernel_demo.txt
python docs/agent/examples/kernel_demo.py

# 端到端脚本
python tools/p1_acceptance_smoke.py
```

**通过标准：**
- ✅ 覆盖率 ≥90%
- ✅ 现有测试 0 失败
- ✅ 示例输出符合预期

> **勘误说明（P1 收尾 → 2026-09-10 补齐）**：G0 关卡原定的三项证据中，
> 「示例可运行」一项曾因 `docs/agent/examples/kernel_demo.py` 从未创建而不成立，
> 当时已改以端到端脚本替代。**该脚本现已补齐**：`python docs/agent/examples/kernel_demo.py`
> 退出码 0（5 节演示 / 113 条自断言，覆盖事件总线、Context、Service 生命周期、
> 注册表、插件加载器），原始输出见 `docs/agent/evidence/g0/kernel_demo.txt`，本项**恢复成立**。
> 缺口本身仍记录在 `docs/agent/acceptance-report-p1.md`（发现 F6）与 `AGENTS.md` §14.6。

---

## 三、Phase B — 并行管线验收

### 3.1 关键测试

```python
def test_parallel_dispatch():
    """B-07: ack 与 execute 必须同时发射（不互相等待）"""
    pipeline = Pipeline(...)
    timestamps = {}
    
    def on_ack(e): timestamps["ack"] = time.time()
    def on_submit(e): timestamps["submit"] = time.time()
    
    pipeline.bus.on("feedback.ack", on_ack)
    pipeline.bus.on("task.submitted", on_submit)
    
    pipeline.bus.emit("intent.resolved", command=cmd, request_id="r1")
    
    # 两者时间差必须 <50ms（证明是并行而非串行）
    assert abs(timestamps["ack"] - timestamps["submit"]) < 0.05

def test_total_latency_budget():
    """B-16: 感知延迟 <1.5s"""
    t0 = time.time()
    on_ack_time = None
    def on_ack(e):
        nonlocal on_ack_time
        on_ack_time = time.time() - t0
    
    # 模拟完整流程
    pipeline.simulate("找一下桌面上的PDF")
    assert on_ack_time < 1.5

def test_timeout_degradation():
    """B-09: 工具超时后正确降级"""
    slow_tool = MockTool(sleep=70)       # 超过 60s 超时
    result = executor.run(slow_tool, {}, timeout=1)
    assert result.success is False
    assert "超时" in result.summary or "停下" in result.summary

def test_interrupt_clears_queue():
    """B-10: 打断清空待播队列"""
    pipeline.bus.emit("task.submitted", task_id="t1")
    pipeline.bus.emit("speech.interrupted", request_id="r1")
    assert pipeline.pending_audio_queue.empty()

def test_ack_cache_hit():
    """B-13/B-14: 缓存命中零延迟"""
    cache = AckCache()
    cache.warm_up(fake_tts)
    t0 = time.time()
    audio = cache.get("search")
    elapsed = time.time() - t0
    assert audio is not None
    assert elapsed < 0.01               # 缓存命中 <10ms
```

### 3.2 Phase B 验收命令

```bash
pytest tests/agent/test_pipeline.py -v
pytest tests/agent/test_ack_lru.py -v      # 勘误：计划写的是 test_ack_cache.py，从未创建
                                           # （确认语缓存的专项测试在 P2-1 落地时命名为 test_ack_lru.py）
```

**通过标准：**
- ✅ ack 与 submit 时间差 <50ms
- ✅ 模拟感知延迟 <1.5s
- ✅ 缓存命中 <10ms
- ✅ 打断能清空队列

---

## 四、Phase C — 意图路由验收

### 4.1 50 条测试指令集

```python
# tests/agent/router_testset.py
ROUTER_TESTSET = [
    # ── file_search（10 条）──
    ("找一下桌面上的合同文件", "file_search", {"pattern": "*合同*"}),
    ("帮我搜一下下载文件夹里的PDF", "file_search", {"pattern": "*.pdf"}),
    ("桌面上的图片在哪", "file_search", {"pattern": "*.png"}),
    ("上周的报表文件找一下", "file_search", {"time_range": "last_week"}),
    ("有没有关于预算的文档", "file_search", {"pattern": "*预算*"}),
    ("找找我的简历", "file_search", {"pattern": "*简历*"}),
    ("下载目录里的安装包", "file_search", {"dir": ["Downloads"]}),
    ("文档文件夹里的Excel", "file_search", {"pattern": "*.xlsx"}),
    ("找一下截图", "file_search", {"pattern": "*截图*"}),
    ("前两天那个视频文件", "file_search", {"time_range": "last_2_days"}),
    
    # ── file_list（5 条）──
    ("桌面上有什么", "file_list", {"dir": ["Desktop"]}),
    ("列出下载文件夹", "file_list", {"dir": ["Downloads"]}),
    ("看看文档目录", "file_list", {"dir": ["Documents"]}),
    ("图片文件夹里有啥", "file_list", {"dir": ["Pictures"]}),
    ("显示桌面文件", "file_list", {"dir": ["Desktop"]}),
    
    # ── file_read（5 条）──
    ("打开报告.docx", "file_read", {"target": "报告.docx"}),
    ("看看这个文件", "file_read", {}),
    ("读一下简历", "file_read", {"target": "*简历*"}),
    ("打开第一个", "file_read", {"target": "第一个"}),  # 需上下文
    ("看看合同的内容", "file_read", {"target": "*合同*"}),
    
    # ── file_rename（5 条）──
    ("把报告改成年终总结", "file_rename", {"target": "年终总结"}),
    ("重命名这个文件", "file_rename", {}),
    ("把a.txt改成b.txt", "file_rename", {"source": "a.txt", "target": "b.txt"}),
    ("改名成新的", "file_rename", {"target": "新的"}),
    ("把这个文件名字换一下", "file_rename", {}),
    
    # ── file_move（5 条）──
    ("把报告移到文档文件夹", "file_move", {"dest": "Documents"}),
    ("挪到下载目录", "file_move", {"dest": "Downloads"}),
    ("移动到桌面", "file_move", {"dest": "Desktop"}),
    ("把这个移到图片文件夹", "file_move", {"dest": "Pictures"}),
    ("文件挪个位置", "file_move", {}),
    
    # ── file_delete（5 条）──
    ("删除桌面上的截图", "file_delete", {}),
    ("把那些都删了", "file_delete", {}),
    ("清理一下下载文件夹", "file_delete", {}),
    ("删掉这个文件", "file_delete", {}),
    ("不要这些了，删掉", "file_delete", {}),
    
    # ── 其他工具（10 条）──
    ("翻译这段话", "translate", {}),
    ("算一下 25 乘 4", "calculate", {"expression": "25*4"}),
    ("内存用了多少", "system_info", {"metric": "memory"}),
    ("复制这段文字", "clipboard", {"action": "set"}),
    ("帮我查一下天气", "chat", {}),          # 未实现 → chat 兜底
    
    # ── 闲聊兜底（5 条）──
    ("你好呀", "chat", {}),
    ("今天心情不错", "chat", {}),
    ("给我讲个笑话", "chat", {}),
    ("你觉得我怎么样", "chat", {}),
    ("陪我聊聊天", "chat", {}),
]
```

### 4.2 准确率测试

```python
def test_router_accuracy():
    """C-09: 规则路由准确率 >90%"""
    router = RuleRouter()
    correct = 0
    failures = []
    
    for text, expected_action, expected_params in ROUTER_TESTSET:
        cmd = router.route(text, {})
        if cmd.action == expected_action:
            correct += 1
        else:
            failures.append((text, expected_action, cmd.action))
    
    accuracy = correct / len(ROUTER_TESTSET)
    assert accuracy > 0.90, f"准确率 {accuracy:.1%}，失败: {failures}"

def test_router_latency():
    """C-09: 规则路由延迟 <10ms"""
    router = RuleRouter()
    times = []
    for text, _, _ in ROUTER_TESTSET:
        t0 = time.perf_counter()
        router.route(text, {})
        times.append((time.perf_counter() - t0) * 1000)
    
    p95 = sorted(times)[int(len(times) * 0.95)]
    assert p95 < 10.0, f"P95 延迟 {p95:.2f}ms"

def test_param_extraction():
    """C-05: 参数提取正确性"""
    router = RuleRouter()
    cmd = router.route("把报告.docx改成年终总结.docx", {})
    assert cmd.action == "file_rename"
    assert "报告" in cmd.params.get("source", "")
    assert "年终总结" in cmd.params.get("target", "")

def test_confidence_threshold():
    """C-13: 低置信度触发 LLM 兜底"""
    rule = MockRuleRouter(returns=AgentCommand(action="chat", confidence=0.3))
    llm = MockLLMRouter(returns=AgentCommand(action="file_search", confidence=0.8))
    hybrid = HybridRouter(rule=rule, llm=llm, threshold=0.5)
    
    cmd = hybrid.route("那个东西", {})
    assert cmd.action == "file_search"   # 走了 LLM 路径
    assert llm.call_count == 1

def test_llm_failure_fallback():
    """C-14: LLM 失败降级到 chat"""
    rule = MockRuleRouter(returns=AgentCommand(action="chat", confidence=0.3))
    llm = MockLLMRouter(raises=TimeoutError())
    hybrid = HybridRouter(rule=rule, llm=llm, threshold=0.5)
    
    cmd = hybrid.route("那个东西", {})
    assert cmd.action == "chat"          # 降级成功，不崩溃
```

### 4.3 Phase C 验收命令

```bash
pytest tests/agent/test_router.py -v
```

**通过标准：**
- ✅ 准确率 >90%（至少 45/50）
- ✅ P95 延迟 <10ms
- ✅ 降级链正确工作

---

## 五、Phase D — 安全守卫验收（R1 重点）

### 5.1 安全边界测试（必须 100% 通过）

```python
"""安全边界测试 — R1 核心防护验证"""

import pytest
from agent.providers.safety.basic_guard import validate_path, PathNotAllowed

class TestPathWhitelist:
    """路径白名单测试"""
    
    def test_valid_desktop_path(self):
        p = validate_path("~/Desktop/test.txt")
        assert str(p).endswith("test.txt")
    
    def test_directory_traversal_blocked(self):
        """目录穿越攻击必须被拒绝"""
        with pytest.raises(PathNotAllowed):
            validate_path("~/Desktop/../../../Windows/System32/x.dll")
    
    def test_absolute_system_path_blocked(self):
        """系统绝对路径必须被拒绝"""
        with pytest.raises(PathNotAllowed):
            validate_path("C:/Windows/System32/cmd.exe")
    
    def test_symlink_escape_blocked(self, tmp_path):
        """符号链接逃逸必须被拒绝"""
        # 在桌面创建指向 C:\Windows 的软链
        link = Path.home() / "Desktop" / "test_link"
        try:
            link.symlink_to("C:/Windows")
        except OSError:
            pytest.skip("需要管理员权限创建软链")
        
        with pytest.raises(PathNotAllowed):
            validate_path(str(link / "System32" / "cmd.exe"))
    
    def test_unc_path_blocked(self):
        """UNC 路径必须被拒绝"""
        with pytest.raises(PathNotAllowed):
            validate_path("//server/share/file.txt")
    
    def test_case_insensitive_blocked(self):
        """大小写绕过必须被拒绝（Windows 大小写不敏感）"""
        with pytest.raises(PathNotAllowed):
            validate_path("C:/WINDOWS/system32/cmd.exe")
    
    def test_short_name_blocked(self):
        """8.3 短名绕过必须被拒绝"""
        with pytest.raises(PathNotAllowed):
            validate_path("C:/PROGRA~1/test.txt")
    
    def test_home_dir_itself_blocked(self):
        """用户根目录本身不在白名单（只允许四个子目录）"""
        with pytest.raises(PathNotAllowed):
            validate_path("~/")
```

### 5.2 删除不可逆性测试

```python
class TestDeleteSafety:
    """删除安全测试 — 必须 100% 可恢复"""
    
    def test_permanent_delete_rejected(self):
        """永久删除请求必须被拒绝"""
        with pytest.raises(SecurityError):
            file_delete(["~/Desktop/test.txt"], permanent=True)
    
    def test_delete_goes_to_trash(self, tmp_path, monkeypatch):
        """删除必须走回收站"""
        called = []
        monkeypatch.setattr("send2trash.send2trash", lambda p: called.append(p))
        
        f = tmp_path / "test.txt"
        f.write_text("x")
        file_delete([str(f)])
        assert len(called) == 1          # send2trash 被调用
    
    def test_no_permanent_delete_in_source(self):
        """源码审查：file_delete 中不得有永久删除调用"""
        src = Path("agent/tools/file_tools.py").read_text()
        delete_func = extract_function(src, "file_delete")
        
        forbidden = ["os.remove", "shutil.rmtree", ".unlink(", "os.rmdir"]
        for f in forbidden:
            assert f not in delete_func, f"file_delete 中禁止出现 {f}"
    
    def test_delete_count_limit(self):
        """单次删除数量上限 200"""
        targets = [f"~/Desktop/f{i}.txt" for i in range(300)]
        result = file_delete(targets)
        assert result.success is False
        assert "200" in result.summary
```

### 5.3 风险确认测试

```python
class TestRiskConfirmation:
    def test_low_risk_auto_execute(self):
        """low 风险自动执行"""
        v = guard.check("file_search", {"pattern": "*"})
        assert v.allowed is True
        assert v.confirm_needed is False
    
    def test_medium_risk_needs_confirm(self):
        """medium 风险需要确认"""
        v = guard.check("file_rename", {"source": "a", "target": "b"})
        assert v.confirm_needed is True
        assert v.require_double is False
    
    def test_high_risk_double_confirm(self):
        """high 风险双重确认"""
        v = guard.check("file_delete", {"targets": ["a"]})
        assert v.confirm_needed is True
        assert v.require_double is True
    
    def test_critical_risk_rejected(self):
        """critical 风险直接拒绝"""
        v = guard.check("format_disk", {})
        assert v.allowed is False
        assert v.reason != ""
    
    def test_confirm_timeout(self):
        """确认超时 30s 自动取消"""
        guard.request_confirm("r1", "file_delete", {})
        time.sleep(0.1)
        guard._pending["r1"].created_at -= 31   # 模拟超时
        guard.expire_check()
        assert "r1" not in guard._pending
    
    def test_confirm_remembered(self):
        """确认选择被记住（15 分钟内不重复问）"""
        guard.confirm("r1", approved=True)
        v = guard.check("file_delete", {"targets": ["~/Desktop/a.txt"]})
        assert v.confirm_needed is False     # 已记住
```

### 5.4 审计日志测试

```python
class TestAuditLog:
    def test_audit_records_write_ops(self, tmp_path):
        """写操作必须记录审计日志"""
        guard = BasicGuard(audit_db=tmp_path / "audit.db")
        guard.audit("file_rename", {"source": "a", "target": "b"}, success=True)
        
        rows = guard.query_audit()
        assert len(rows) == 1
        assert rows[0]["action"] == "file_rename"
    
    def test_audit_records_read_ops_optional(self, tmp_path):
        """读操作审计可配置"""
        # 默认不记录读操作（避免日志膨胀）
    
    def test_audit_retention(self, tmp_path):
        """审计日志保留 30 天"""
        # 插入 31 天前的记录 → cleanup 后应被删除
```

### 5.5 Phase D 验收命令

```bash
pytest tests/agent/test_safety.py -v
pytest tests/agent/test_edge_cases.py -v      # 边界用例（原计划的 test_safety_boundary.py）
```

> **勘误（P1 收尾）**：计划中的 `tests/agent/test_safety_boundary.py` 实际合并进
> `tests/agent/test_edge_cases.py`；安全边界用例（穿越 / UNC / 软链 / 空字节 / 系统目录）
> 也分散在 `test_safety.py` 与 `test_file_tools.py` 中。
>
> **复核（2026-09-10）**：全文检索确认 `test_safety_boundary.py` 在本文档中只剩本节这两处引用，
> 且都标着「原计划」，无未标注的悬空引用；上面那句归属已逐词核实 ——
> 目录穿越、UNC、symlink、空字节、`System32` 在 `test_safety.py` 与
> `test_file_tools.py` 中均有用例，故本处无需再改。

**通过标准：**
- ✅ 路径白名单测试 7/7 通过
- ✅ 删除安全测试 4/4 通过
- ✅ 确认机制测试 6/6 通过
- ✅ **源码审查通过**（file_delete 无永久删除调用）

---

## 六、Phase F — 文件工具验收

### 6.1 六工具测试矩阵

| 工具 | 正常路径 | 边界 | 错误 |
|------|---------|------|------|
| file_search | 找到文件 | 结果 >50 截断；无结果 | 目录不存在 |
| file_list | 列出文件 | 空目录；>100 截断 | 目录不存在 |
| file_read | 读文本 | >10000 字符截断；>10MB 拒绝 | 非文本文件 |
| file_rename | 重命名成功 | 中文名；特殊字符 | 目标已存在；源不存在 |
| file_move | 移动成功 | 跨盘移动；同名冲突 | 目标目录不存在 |
| file_delete | 进回收站 | 200 上限；混合文件/目录 | 白名单外路径 |

### 6.2 关键测试

```python
def test_file_search_truncation(tmp_path, monkeypatch):
    """F-06: 结果超 50 个时截断并标记"""
    # 创建 60 个文件
    for i in range(60):
        (tmp_path / f"test_{i}.txt").write_text("x")
    
    tool = FileSearchTool()
    result = tool.execute({"pattern": "*.txt", "dir": [str(tmp_path)]})
    assert len(result.data) == 50
    assert result.truncated is True
    assert "更多" in result.summary

def test_file_read_size_limit(tmp_path):
    """F-10: 超过 10MB 拒绝"""
    big = tmp_path / "big.txt"
    big.write_bytes(b"x" * (11 * 1024 * 1024))
    
    result = FileReadTool().execute({"target": str(big)})
    assert result.success is False
    assert "太大" in result.summary

def test_file_rename_no_overwrite(tmp_path):
    """F-12: 目标已存在时不覆盖"""
    (tmp_path / "a.txt").write_text("a")
    (tmp_path / "b.txt").write_text("b")
    
    result = FileRenameTool().execute({
        "source": str(tmp_path / "a.txt"),
        "target": str(tmp_path / "b.txt"),
    })
    assert result.success is False
    assert (tmp_path / "b.txt").read_text() == "b"   # 未被覆盖

def test_file_delete_recoverable(tmp_path):
    """F-16/F-17: 删除后可从回收站恢复"""
    f = tmp_path / "to_delete.txt"
    f.write_text("content")
    
    result = FileDeleteTool().execute({"targets": [str(f)]})
    assert result.success is True
    assert not f.exists()
    # 人工验证：回收站中能找到该文件
```

### 6.3 Phase F 验收命令

```bash
pytest tests/agent/test_file_tools.py -v --cov=agent.tools.file_tools --cov-fail-under=75
```

**通过标准：**
- ✅ 6 个工具全部测试通过
- ✅ 覆盖率 ≥75%
- ✅ 手动验证：删除的文件能从回收站恢复

---

## 七、Phase H — 端到端验收

### 7.1 端到端场景测试

```python
"""端到端集成测试"""

def test_e2e_search_flow(qapp, monkeypatch):
    """H-14: 完整搜索流程"""
    app = build_test_app()
    
    # 模拟语音输入
    app.bus.emit("speech.recognized", text="找一下桌面上的PDF", request_id="r1")
    
    # 等待结果
    result = wait_for(app.bus, "feedback.result", timeout=5)
    
    assert result is not None
    assert "找到" in result["summary"] or "没找到" in result["summary"]
    assert result["emotion"] in ("happy", "sad", "think", "talk")

def test_e2e_delete_with_confirm(qapp):
    """H-15: 删除确认流程"""
    app = build_test_app()
    
    # 1. 发起删除
    app.bus.emit("speech.recognized", text="删除桌面上的截图", request_id="r1")
    
    # 2. 应收到确认请求
    confirm = wait_for(app.bus, "feedback.confirm", timeout=5)
    assert confirm is not None
    assert "确定" in confirm["question"] or "确认" in confirm["question"]
    
    # 3. 用户确认
    app.bus.emit("speech.recognized", text="确定", request_id="r2")
    
    # 4. 应收到执行结果
    result = wait_for(app.bus, "feedback.result", timeout=10)
    assert "回收站" in result["summary"] or "删除" in result["summary"]

def test_e2e_latency():
    """H-16: 端到端延迟测量"""
    app = build_test_app()
    marks = {}
    
    app.bus.on("speech.recognized", lambda e: marks.update(t0=time.time()))
    app.bus.on("feedback.ack", lambda e: marks.update(ack=time.time()))
    app.bus.on("feedback.result", lambda e: marks.update(result=time.time()))
    
    app.bus.emit("speech.recognized", text="桌面上有什么", request_id="r1")
    wait_for(app.bus, "feedback.result", timeout=10)
    
    perceived = marks["ack"] - marks["t0"]
    total = marks["result"] - marks["t0"]
    
    assert perceived < 1.5, f"感知延迟 {perceived:.2f}s 超标"
    assert total < 3.0, f"总延迟 {total:.2f}s 超标"

def test_e2e_fallback_to_chat():
    """H-13: 未命中意图时降级到闲聊"""
    app = build_test_app()
    app.bus.emit("speech.recognized", text="给我讲个笑话", request_id="r1")
    
    result = wait_for(app.bus, "feedback.result", timeout=10)
    # 应走 LLM 闲聊路径，而非工具执行
    assert "chat" in str(result).lower() or result["summary"] != ""

def test_e2e_interrupt():
    """H-15: 打断能中止任务"""
    app = build_test_app()
    app.bus.emit("speech.recognized", text="找一下所有文件", request_id="r1")
    time.sleep(0.1)
    app.bus.emit("speech.interrupted", request_id="r1")
    
    # 不应再收到结果（或收到取消提示）
    result = wait_for(app.bus, "feedback.result", timeout=2, allow_none=True)
    assert result is None or "停" in result.get("summary", "")
```

### 7.2 零回归验证

```bash
# 全量测试（含现有 + 新增）
pytest tests/ -v

# 现有功能专项验证
pytest tests/test_pet_window.py -v
pytest tests/test_voice_pipeline.py -v
pytest tests/test_hotkey_manager.py -v
pytest tests/test_animation.py -v
pytest tests/test_emotion_analyzer.py -v
```

**通过标准：**
- ✅ 所有现有测试仍然通过（当前基线：125 passed, 1 pre-existing failure）
- ✅ 新增测试全部通过
- ✅ 无新增失败

### 7.3 手动验证清单（P0 必须）

```markdown
## 手动验收清单

### 场景 1：搜索文件（P0）
- [ ] 启动应用，等待就绪
- [ ] 说"找一下桌面上的 PDF 文件"
- [ ] 观察：1.5 秒内听到确认语（"好的，我找找看~"）
- [ ] 观察：气泡显示确认语
- [ ] 观察：动效切换到 think
- [ ] 观察：3 秒内听到结果播报
- [ ] 观察：气泡显示文件列表
- [ ] 验证：结果与桌面实际文件一致
- [ ] 截图留证

### 场景 2：删除确认流程（P0）
- [ ] 说"删除桌面上的截图"
- [ ] 观察：气泡显示要删除的文件预览
- [ ] 观察：听到确认询问（"找到 N 张截图，确定要删除吗？"）
- [ ] 说"确定"
- [ ] 观察：听到执行结果
- [ ] 验证：文件已移入回收站（打开回收站确认）
- [ ] 验证：可从回收站恢复
- [ ] 截图留证

### 场景 3：打断（P0）
- [ ] 说"找一下所有文件"（耗时较长）
- [ ] 在播报确认语后立即按 Ctrl+Alt+D
- [ ] 观察：立即停止播报
- [ ] 观察：动效切换到 surprise/angry
- [ ] 验证：不再播报结果
- [ ] 截图留证

### 场景 4：降级路径（P0，R6）
- [ ] 说"给我讲个笑话"（非工具指令）
- [ ] 观察：走原 LLM 闲聊路径，正常回复
- [ ] 说"你好呀"
- [ ] 观察：正常闲聊
- [ ] 验证：现有语音功能未受影响

### 场景 5：安全边界（P0，R1）
- [ ] 说"打开 C 盘 Windows 文件夹"
- [ ] 观察：被拒绝并友好提示
- [ ] 验证：审计日志记录了这次拒绝
- [ ] 截图留证
```

### 7.3.1 自动回环验证结果（2026-09-10 补做，**不等于**上表人工验收）

上表的 5 项一直挂着"需真机麦克风 + 人在场"。本轮把**"人对着麦克风说话"这一段**
换成 **TTS 合成语音回灌 ASR**，其余（ASR → 事件总线 → 真实 Agent 管线 → 事件/审计/回收站）
走的是**真实代码路径**，从而把它变成可复跑的脚本：
`python tools/voice_scenarios_loopback.py --model small`。

> **证据强度声明（必须与结论一起读）**
>
> | | 覆盖 | 不覆盖 |
> |---|---|---|
> | 环节 | 合成语音 → ASR 转写 → 事件总线 → 真实 Agent 管线 → 事件 / 审计 / 回收站 | **①麦克风硬件采集**（声卡 / 环境噪声 / 回声消除）**②GUI 气泡与动效**（不起窗口，只断言其依赖的事件）**③人耳听感**（不播放、不听）**④`medium` 档 ASR 质量**（本机 HF 缓存只有 tiny/base/small，用 `small`） |
> | 测激 | 默认吃**固定测激**（`docs/agent/evidence/voice/fixtures/*.wav`），所以同一份代码每轮结论可比 | **不覆盖"任意一版合成语音都能识别"**——替身本身会抖（下详），要用 `python tools/measure_asr_stimulus.py --synth 6` 单独测 |
>
> 结论：**本回环是人工验收的前置自动过滤，不是替代**。上表要求的"观察动效/听确认语/截图留证"
> 仍必须由人完成。

| 场景 | §7.3 要求 | 回环实测 | 判定 |
|------|----------|---------|------|
| 1 搜索文件 | 「找一下桌面上的 PDF 文件」→ ≤1.5s 确认语、≤3s 结果 | 转写正确；ack **2.0ms**、result **4.7ms**；`file_search` 结果与磁盘实际文件一致 | ✅ |
| 2 删除确认 | 「删除桌面上的截图」→ 预览 → 确认 → 入回收站 | 转写为繁体「删除桌面上的**截圖**。」→ **繁简归一化后 `pattern='*截图*'`** → 预览就是 `截图_01.png`/`截图_02.png` → 确认 → 进回收站且**回收站中可恢复** | ✅（本轮修好归一化后） |
| 3 打断 | 「找一下所有文件」→ 立即 Ctrl+Alt+D → 停播报、不再播结果 | 管线侧 `interrupts 0→1`、无后续 `feedback.result`、无挂起计划；真实入口 `app.interrupt_speech()` 也能被管线感知 | ✅（本轮修好接线后） |
| 4 降级路径 | 「给我讲个笑话」/「你好呀」→ 走原 LLM 闲聊 | 两句均走 `pipeline.chat`，未误判工具；`enabled=false` 时全部转闲聊 | ✅ |
| 5 安全边界 | 「打开 C 盘 Windows 文件夹」→ 被拒绝 + 审计留痕 | 口语盘符现已被识别为路径 → **硬拒绝**（「这个位置我不能动哦，只能操作：…」）+ 审计留痕；显式路径探针 `C:\Windows\System32\cmd.exe` 同样硬拒绝 | ✅ |

**终轮成绩：`50/50 通过（失败 0、降级 0、退出码 0）`**（`docs/agent/evidence/acceptance/G12.txt`）。
对比归档的上一轮 `45/50`：那时场景 2 的 4 个失败 + 1 个降级**同根于一个缺陷**（下详），
该缺陷本轮已修。

> **不要把 `50/50` 读成"语音验收完成了"**：它是**固定测激**下的结论。
> 同一句话现场重合成 6 次，`edge_tts` 给出的波形并不完全相同
> （字节数一致、样点均值浮动），归档那次 `山豬桌面上集圖` 就是**某一版音频**
> 被整句听错——而同一份音频重复解码 6 次结果完全一致（`stimulus_variance.txt`）。
> 所以"替身抖动"是**与产品无关的随机源**，回归默认用固定测激把它掐掉；
> 想测量它，显式跑 `--fresh-stimulus` 或 `measure_asr_stimulus.py`。

**已修复的阻塞缺陷（曾是场景 2 的 4 个 FAIL 之根）**：
ASR 对中文的**繁体输出**（「截圖」）与规则路由的**简体关键词表**不匹配，
于是"删除"意图匹配上了、但文件类型关键词**静默丢失**：
`{dirs: ['Desktop']}` 里没有 `pattern`，而**置信度仍是 0.95，没被阈值拦下**，
管线便用实体栈兜底补目标 —— 于是"删除截图"变成了"删除上一次搜索到的文件"。
用户唯一能察觉的机会就是那句确认语。

**修法**：新增 `agent/text_norm.py`（整词表 + 无歧义字级兜底），
在规则路由的唯一入口 `_normalize()` 上接入；并加管线护栏 ——
用户已明说位置时 `file_delete` **不再用实体栈猜目标**。
覆盖范围写成合同：`tests/agent/test_text_norm.py::TestRouterVocabularyCoverage`
把"路由词表的繁体写法必须归一化回简体关键词"逐条钉住（59 条），
往词表加关键词若忘了补写法就会红。

**附带发现（非阻塞）**：短句确认/取消语在 ASR 下不稳（各 4 次实测）——
「算了」**2/4**（被识别成"散了"/"三郎"）、「不用了」3/4、「停止」3/4；
「确定」「好的」「取消」4/4。用户说「算了」时宠物会**没反应**（待确认项一直挂着）。
本轮又量到同一现象的另一面：`measure_asr_stimulus.py` 里
「确定」有 1/6 版被整句听成「蜥蜴」——**短句既是 ASR 的弱项，也是测激抖动的放大器**。

---

## 八、验收证据归档

| 关卡 | 证据类型 | 存放位置 |
|------|---------|---------|
| G0 | pytest 覆盖率报告 | `docs/agent/evidence/g0_coverage.txt` |
| G1 | 路由准确率报告 | `docs/agent/evidence/g1_router_accuracy.txt` |
| G2 | 安全测试报告 + 回收站截图 | `docs/agent/evidence/g2_security/` |
| G3 | 端到端录屏 + 延迟日志 | `docs/agent/evidence/g3_e2e/` |

---

**文档版本：** v1.0  
**覆盖范围：** P0（Phase A~H）
