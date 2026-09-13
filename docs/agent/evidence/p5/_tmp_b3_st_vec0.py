# -*- coding: utf-8 -*-
"""P5-B3 证据：真 sentence-transformers 嵌入器 + sqlite-vec(vec0) 端到端

## 这条路径此前**从未被真模型走过**

P3 交付时 `st_embedder` 只有**假模块**覆盖（成功/失败/编码异常/维度探测/并发加载/
close 重载），P4-C3 补了"真模型能 encode"（8/8、维度 384、L2 范数 1.000000），
但**真模型 + 真 vec0 索引**这条组合没有任何证据。本脚本把它跑通并留原始输出。

## 要钉住的三件事（P5 计划 §四 B3 的验收条件）

1. 真 ST 嵌入器（384 维）**写入**一条记忆 → 能**检索**回来；
2. 检索走的是 **sqlite-vec 的 vec0 虚表**（不是退化成纯 Python 全表余弦），
   且 `stats()` 如实报告**实际生效**的后端；
3. **维度变化时索引被重建**，并且如实报告（不静默按错误宽度建表 —— 那会让
   向量检索悄悄失效，而所有词法检索看起来都正常）。

## 为什么这个证据必须自己造一个 store

`RecallStore(dim=256)` 的默认维度是 hashing 嵌入器的 256；真 ST 是 384。
"维度变化"这件事**正是要测的对象**，所以脚本刻意分两段：
先用 256（hashing）建库，再用 384（真 ST）打开同一个库文件 —— 看它怎么处理。

用法：python docs/agent/evidence/p5/_tmp_b3_st_vec0.py
"""
import json
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))

from agent.providers.memory.hashing_embedder import HashingEmbedder   # noqa: E402
from agent.providers.memory.recall_store import RecallStore           # noqa: E402
from agent.providers.memory.st_embedder import SentenceTransformerEmbedder  # noqa: E402
from agent.seams.memory import MemoryItem                             # noqa: E402

OUT = ROOT / "docs" / "agent" / "evidence" / "p5" / "st_vec0_e2e.txt"

lines = []
w = lines.append
w("# P5-B3 证据：真 sentence-transformers 嵌入器 + sqlite-vec(vec0) 端到端")
w("")
w(f"生成时间：{datetime.now():%Y-%m-%d %H:%M:%S}")
w("")
w("> 可复跑：`python docs/agent/evidence/p5/_tmp_b3_st_vec0.py`")
w("")


def section(title):
    w("")
    w(f"## {title}")
    w("")


def show(obj):
    if isinstance(obj, (dict, list)):
        w("```json")
        w(json.dumps(obj, ensure_ascii=False, indent=2, default=str))
        w("```")
    else:
        w("```")
        w(str(obj))
        w("```")


tmp = Path(tempfile.mkdtemp(prefix="xbya_b3_"))
db = tmp / "mem.db"

# ── 一、真 ST 嵌入器本身 ──
#
# ⚠️ 第一版探针在这里踩了自己的坑：它用 `ready()` 当"能不能用"的判据，
# 而 `ready()` 的契约（源码 docstring）是 **"未尝试加载时返回 False"** ——
# 它是**懒加载状态查询**，不是"探测并加载"。于是探针报「模型不可用」，
# 而实际上模型好得很（同一台机器上 `embed_one()` 直接返回 384 维）。
#
# 这个坑值得留档，因为它与"缺依赖就静默降级"是**相反**方向的误读：
# 我的探针把"还没加载"读成了"加载不了"。正确判据是**真的用一次**。
section("一、真 ST 嵌入器（懒加载 + 编码 + 归一化）")
st = SentenceTransformerEmbedder()
st_ready_before = st.ready()
w(f"- `ready()`（**未使用前**）→ **{st_ready_before}** —— 契约是「未尝试加载时返回 False」，")
w("  它是懒加载状态查询，**不是**「能不能用」的判据。下面真的用一次：")
t0 = time.time()
probe_vec = st.embed_one("探测一下")
load_s = time.time() - t0
w(f"- 首次 `embed_one()` 触发懒加载，用时 **{load_s:.2f}s**，返回长度 **{len(probe_vec)}**")
w(f"- 用完之后 `ready()` → **{st.ready()}**（这才是「已加载」）")
w(f"- `dim` → **{st.dim}**")
w(f"- `describe()` → `{st.describe()}`")
if not probe_vec or len(probe_vec) == 0 or not st.ready():
    w("")
    w("**⚠️ 真模型确实不可用 —— 后面几节无法验证，如实记录为未验证。**")
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("模型不可用，已如实留档")
    raise SystemExit(0)

vec = st.embed_one("桌面上的合同文件")
norm = sum(float(x) * float(x) for x in vec) ** 0.5
w(f"- 向量长度 = **{len(vec)}**，L2 范数 = **{norm:.6f}**（应为 1，即已归一化）")

sim_same = sum(a * b for a, b in zip(vec, st.embed_one("桌面上的合同文件")))
sim_diff = sum(a * b for a, b in zip(vec, st.embed_one("今天天气真好")))
w(f"- 同句自相似 = **{sim_same:.6f}**（确定性：应为 1.0）")
w(f"- 异句相似 = **{sim_diff:.6f}**（明显小于 1 才说明它真的在区分语义）")

# ── 二、真 ST + vec0 端到端（写入 → 检索） ──
section("二、真 ST + vec0 端到端：写入 3 条 → 语义检索")
store = RecallStore(path=str(db), embedder=st, dim=st.dim, vector_backend="auto")
w(f"- `stats()` 初始：")
show(store.stats())

ITEMS = [
    ("我喜欢用 Chrome 浏览器", "fact"),
    ("每周三下午开组会", "episode"),
    ("下载目录是我的常用目录", "fact"),
]
for text, kind in ITEMS:
    ok = store.add(MemoryItem(text=text, kind=kind))
    w(f"- add(MemoryItem(text={text!r}, kind={kind!r})) → {ok}")

w("")
w(f"- 写入后条数 = **{store.count()}**")
w(f"- 向量后端（实际生效）= **{store.vector_backend}**")
w(f"- `stats()`：")
show(store.stats())

w("")
w("### 语义检索：问「浏览器」应当命中「我喜欢用 Chrome 浏览器」")
hits = store.search("浏览器", limit=3)
for h in hits:
    w(f"- score={getattr(h, 'score', '?'):.4f}  kind={getattr(h, 'kind', '?')}  "
      f"text={getattr(h, 'text', '?')!r}")

w("")
w("### 换词形：问「Chrome」也应命中同一条（词法检索做不到这一点）")
hits2 = store.search("Chrome", limit=3)
for h in hits2:
    w(f"- score={getattr(h, 'score', '?'):.4f}  text={getattr(h, 'text', '?')!r}")

w("")
w("### 无关查询：问「外星人」应当检索不出（或分数极低）")
hits3 = store.search("外星人", limit=3)
w(f"- 命中 {len(hits3)} 条")
for h in hits3:
    w(f"- score={getattr(h, 'score', '?'):.4f}  text={getattr(h, 'text', '?')!r}")

w("")
w("### get / recent 也要在真 ST 下可用")
first_id = None
try:
    items = store.items() if hasattr(store, "items") else []
    if items:
        first_id = getattr(items[0], "item_id", None) or getattr(items[0], "id", None)
    w(f"- items() 取到 {len(items)} 条；首条 id = {first_id!r}")
    if first_id:
        got = store.get(first_id)
        w(f"- get({first_id!r}) → {getattr(got, 'text', got)!r}")
except Exception as e:                                                    # noqa: BLE001
    w(f"- items()/get() 出错：{type(e).__name__}: {e}")
w(f"- recent(2) → {[getattr(i, 'text', i) for i in store.recent(2)]}")

store.close()

# ── 三、维度变化：256（hashing）→ 384（真 ST）同一个库文件 ──
section("三、维度变化：同一个库文件先用 256（hashing）建，再用 384（真 ST）打开")

db2 = tmp / "dim_change.db"
h = HashingEmbedder(dim=256)
s256 = RecallStore(path=str(db2), embedder=h, dim=256, vector_backend="auto")
s256.add(MemoryItem(text="先用 hashing 写进去的一条记忆", kind="fact"))
st256 = s256.stats()
w("- 256 维度阶段：")
show(st256)
s256.close()

st384 = RecallStore(path=str(db2), embedder=st, dim=384, vector_backend="auto")
info = st384.stats()
w("")
w("- 换成 384 维度（真 ST）后重新打开**同一个库文件**：")
show(info)

w("")
w("### 判定")
w("")
w("看三件事是否同时成立：")
w("1. 打开时**没有抛异常**（维度不匹配不能让记忆功能整个失效）；")
w("2. `dim` 如实报 **384**（不是含糊地沿用 256）；")
w("3. 索引**被重建** —— 旧 256 维的向量不能再被当作 384 维用；")
w("   重建的代价是旧的**向量索引**失效，但**文本条目必须还在**（词法检索仍可用）。")
w("")
old_count = st384.count()
w(f"- 重新打开后条数 = **{old_count}**（旧的**文本**条目应当仍在）")
st384.add(MemoryItem(text="384 维度下新写入的一条", kind="fact"))
w(f"- 384 下 add 一条后条数 = **{st384.count()}**")
w(f"- 检索「记忆」→ {[getattr(i, 'text', i) for i in st384.search('记忆', limit=3)]}")

# ── 直接查库：vec0 虚表**声明的宽度**是否真的变成了 384 ──
#
# 这一步是"索引被重建"的**硬证据**：光看日志有一行"重建索引"不算，
# 要看库里那张虚表的建表语句里写的是多少维。旧表若还留着 256 维的定义，
# 第一次写入就会报维度错 —— 而那时候用户看到的是"记忆写不进去"。
import sqlite3                                                        # noqa: E402

w("")
w("### 硬证据：直接读 vec0 虚表的建表 SQL")
try:
    conn = sqlite3.connect(str(db2))
    rows = conn.execute(
        "SELECT name, sql FROM sqlite_master WHERE sql LIKE '%vec0%'"
    ).fetchall()
    for name, sql in rows:
        w(f"- 表 `{name}`：`{sql}`")
    if not rows:
        w("- **库里没有 vec0 虚表** —— 说明向量索引没有生效（只有词法检索）")
    else:
        declares = [n for n, s in rows if s and "float[384]" in s]
        w("")
        w(f"- 结论：**{'✅ 虚表宽度已是 float[384]' if declares else '❌ 虚表宽度不是 384'}**"
          f"（找到 {len(declares)} 张声明 384 的虚表）")
    conn.close()
except Exception as e:                                                    # noqa: BLE001
    w(f"- 读取 schema 出错：{type(e).__name__}: {e}")

w(f"- 最终 stats()：")
show(st384.stats())
st384.close()

# ── 四、强制纯 python 后端也走一遍（对照） ──
section("四、对照：强制 vector_backend='python'（不依赖 sqlite-vec）")
s_py = RecallStore(path=str(tmp / "py.db"), embedder=st, dim=384, vector_backend="python")
s_py.add(MemoryItem(text="纯 python 后端也要能写入与检索", kind="fact"))
w(f"- 实际后端 = **{s_py.vector_backend}**")
w(f"- 检索 → {[getattr(i, 'text', i) for i in s_py.search('写入', limit=3)]}")
s_py.close()

w("")
w("### 一个顺手核实过的相邻事实：`coerce_kind` 的宽松是有意的")
w("")
w("`MemoryKind` 只有三个值（`fact` / `episode` / `entity`），而 `coerce_kind()`")
w("**把不认识的一律收敛成 `fact`**（源码 docstring 明说）。所以若往 `kind` 里塞")
w("`\"preference\"`，它不会报错，会安静地变成 `fact`——本脚本第一版就是随手写了个")
w("不存在的 `\"preference\"`，于是 `by_kind` 显示 `{\"episode\": 1, \"fact\": 2}`")
w("而不是预期的两类。**这是脚本的错，不是产品的**：`memory_tools` 的 `_KIND_ENUM`")
w("与 `MemoryKind` 三个值**逐字一致**，LLM 能选的枚举里不存在 `preference`。")
w("")

w("---")
w("")
w("**边界声明**：本脚本验证的是「真 ST 模型 + 真 vec0 索引」这条组合在**本机**上的行为，")
w("包括维度变化时的重建。它**不**验证：跨机器的一致性、超大库（>1000 条）下的检索质量、")
w("以及换模型（比如换个维度的 ST 模型）后的语义质量差异 —— 这些需要另行测量。")

OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
print(f"写入 {OUT}（{OUT.stat().st_size} bytes）")
print("模型 dim =", st.dim, " ready =", st.ready())
