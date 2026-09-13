# D49 —— 配置指向的 embedding / vector_db 引擎不存在，且降级判据写错

**发现轮次**：系统性功能完整性审计（2026-09-11）
**发现方式**：`tools/audit_plugin_params.py` 报"类不存在" → 追到 `tools/audit_dead_engines.py`
**定性**：✅ **已修（本轮）** —— 修的是"配置说谎"与"归因信息缺失"两部分

---

## 1. 现象（三层，逐层加重）

### 第 1 层：配置里的引擎名指向不存在的插件

```
config.yaml:
  plugins.embedding.engine   = embed_anything
  plugins.vector_db.engine   = leann
```

而 `plugins/embedding/embed_anything/` 与 `plugins/vector_db/leann/` 两个目录里
**只有 0 字节的 `__init__.py`**，**没有 `plugin.py`**：

```
plugins\embedding\embed_anything\__init__.py    0 bytes
plugins\vector_db\leann\__init__.py             0 bytes
```

`PluginLoader.scan()` 的判据是 `plugin_dir / "plugin.py"` 存在（`core/plugin_loader.py:108`），
所以这两个目录**被整个跳过**，连"发现插件"的日志都不会出现。

实测 `scan()` 共发现 **9 个**插件，两个坏引擎都不在其中：

```
发现 9 个: ['3d_speaker', 'edge_tts', 'faster_whisper', 'groq_whisper',
           'liveportrait', 'ollama', 'openai_api', 'openrouter', 'watchfiles']
embed_anything   被发现? 否 ← 配置里写的就是它
leann            被发现? 否 ← 配置里写的就是它
```

### 第 2 层：`is None` 判据接不住"空对象"

`services/file_service.py:406`：

```python
if self.embedding is None or self.vector_db is None:
    logger.warning("语义搜索插件未初始化，使用全文搜索")
    return self.search_files(query, top_k)
```

设计意图是"插件没加载就降级到全文搜索"。但加载失败时走的**不是** `None`，
而是 `load_by_interface()` 给的**空对象**（`core/plugin_loader.py:42`）：

```
svc.embedding = NullEmbedding
svc.vector_db = NullVectorDB
是 None 吗? embedding=False  vector_db=False

→ 第 406 行那句判断：**不成立**（不会降级，直接往下走）
```

### 第 3 层：真正触发的分支给不出归因

往下走到第 411~414 行：

```python
query_vector = self.embedding.embed(query)
if query_vector is None:
    logger.error("查询向量化失败")
    return []
```

`NullEmbedding.embed()` 返回 `None`（`interfaces/embedding.py:65`），
所以**这一段会走到**，日志里出现的是：

```
[ERROR] 查询向量化失败
```

**没有任何一处说"因为 `embed_anything` 这个插件不存在"。**
真实原因（配置指向不存在的引擎）在启动时只有一行 WARNING，
与用户看到的 ERROR 之间**没有关联线索**。

> ⚠️ **更正记录**：我第一版探针把第 412 行写成"不会走到"，判据是
> "`execute` 返回 `[]` 不是 `None`"。那是**读串了行** —— 我在探针 4 里
> 先调过一次 `embed()` 才打印，把 `vector_db.add()` 的 `AttributeError`
> 看成了 `embed()` 的返回值。逐行复核后更正为"**会走到**"。
> 结论（用户可见结果是 `[]`）不变，但**归属行变了** —— 归属错了就会修错地方。

---

## 2. 严重性：**低**，理由是可核对的

这个缺陷听起来严重（"配置说谎"），但实际影响面很小。三条判据：

| 判据 | 测量 | 命令 |
|------|------|------|
| `semantic_search()` 有生产调用者吗 | **0 个**（只有定义与本次探针） | `git grep -n 'semantic_search' -- '*.py'` |
| `get_file_service()` 有调用者吗 | **0 个**（只有定义） | `git grep -n 'get_file_service' -- '*.py'` |
| `core/app.py` 用 `FileService` 吗 | **不用**（`app.py` 里 0 处命中） | `Select-String -Path core\app.py -Pattern 'FileService'` |

即：**整条子系统不可达**。用户碰不到 `semantic_search`，
所以"恒返回 `[]`"目前**不会**影响任何真实交互。

**为什么还要修**：① 它是**已登记能力**（`config.yaml` 里写着，
`config_manager.DEFAULT_CONFIG` 里也写着），下一个读配置的人会以为它可用；
② 一旦有人按配置接线（这正是 D22/D23 的同族动作），
他就会拿到一个**永远返回空、且不提示为什么**的搜索。
按本项目"配置项看起来存在、实际不接线"的既有定性，这条必须登记。

---

## 3. 修法（本轮实际做的）

修的是**两件可验证的事**，不是"实现一个 embedding 后端"（那是另一件事）：

1. **配置不再说谎**：把两个引擎显式置为 `null`，并在 `config.yaml` 里写明原因。
   语义是"**本能力当前无后端**"，与"引擎名叫 X 但没实现"是两回事 ——
   前者是事实，后者是误导。
2. **归因信息补齐**：`FileService.initialize()` 在引擎加载失败时，
   把"配置里写的是谁、为什么失败"记进 `self._degraded: dict[str, str]`；
   `semantic_search()` 的降级判据由 `is None` 改为"**空对象也算降级**"，
   降级时打出的日志**点名真实原因**，而不是笼统的"未初始化"。

判据（可复跑）：

- `python tools/audit_plugin_params.py` → embedding / vector_db 不再报"配了但不生效"
- `python tools/audit_dead_engines.py` → 不再出现"配置里写的就是它"的 ❌
- `pytest tests/test_file_service_degrade.py` → 4 项全绿
- **反方向**：把 `is None` 改回去 → 用例变红

---

## 4. 可复用判据

> **判据 1**：**"没加载"不等于 `None`**。
> 本项目里凡是用空对象（`NullEmbedding` / `NullVectorDB` / `NullASR` …）
> 做降级的地方，`x is None` 这个判据**一律接不住**。
> 检查方法：`git grep -n 'is None'` 逐处核对它判的是不是空对象。
> 空对象有它自己的接口可以问（`is_available()` 返回 `False`），那才是判据。

> **判据 2**：**配置里的引擎名必须能在 `scan()` 的输出里找到**。
> 判据不是"目录存在"，而是"目录里有 `plugin.py`"。
> 0 字节的 `__init__.py` 会让目录看起来像个插件包，实际不是 ——
> **`git grep` 找目录名会命中，`scan()` 却看不见它**，两边不一致。

> **判据 3**：**报错要能追到"哪一行配置"**。
> "查询向量化失败"是一句**没有归因**的日志：它说的是症状，
> 不是原因。原因（`plugins.embedding.engine: embed_anything` 不存在）
> 只在启动时出现过一次，两者之间没有链接。
> 判据：**用户看到的那条错误里，能不能读出要改哪个配置键**。
