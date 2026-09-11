"""P4-C3：sentence-transformers **真模型**验证（从未下载过，只测过假模块路径）

p4-plan.md 的验收条件：「真模型加载 + encode 归一化断言；不下载则如实记录未验证」。

要验证的是三件此前只用假模块测过的事：
  1. 懒加载真的能加载起来（`ready()` 由 False 变 True）
  2. `dim` 申报的是**模型真实维度**（而不是 fallback）
  3. `encode` 出来的向量**真的归一化**（L2 范数 ≈ 1）—— 这条最关键：
     假模块测不到真 encode，所以"归一化"一直是纸面承诺。

另外核对一件容易搞错的事：项目 config 写的是 `st_model: all-MiniLM-L6-v2`，
而本机 HF 缓存里有一个 `Qdrant/all-MiniLM-L6-v2-onnx`（ONNX 版）——
**两者不是同一个仓库**，所以这里要实测 ST 到底会去加载哪个、有没有真的下载。
"""

import math
import sys
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from agent.providers.memory.st_embedder import SentenceTransformerEmbedder

MODEL = sys.argv[1] if len(sys.argv) > 1 else "all-MiniLM-L6-v2"

print("=" * 74)
print(f"P4-C3：真模型加载与 encode 归一化（model={MODEL}）")
print("=" * 74)

emb = SentenceTransformerEmbedder(model_name=MODEL)

print(f"[构造后] ready={emb.ready()}  dim(申报)={emb.dim}  describe={emb.describe()}")
before_ready = emb.ready()

t0 = time.perf_counter()
vecs = emb.embed(["我喜欢用 Chrome 浏览器", "常用目录是下载"])
dt = (time.perf_counter() - t0) * 1000

print(f"[首次 embed] 耗时 {dt:.0f}ms（含模型加载）  ready={emb.ready()}"
      f"  dim(实测)={emb.dim}  返回 {len(vecs)} 条")
print(f"           每条维度={[len(v) for v in vecs]}")

checks = []
checks.append(("懒加载前 ready 必须是 False", before_ready is False))
checks.append(("加载后 ready 必须变 True", emb.ready() is True))
checks.append(("维度必须是非零实数", emb.dim > 0))
checks.append(("每条向量维度 == 申报维度", all(len(v) == emb.dim for v in vecs)))

norms = [math.sqrt(sum(x * x for x in v)) for v in vecs]
print()
for i, (txt, n) in enumerate(zip(["我喜欢用 Chrome 浏览器", "常用目录是下载"], norms)):
    print(f"  向量{i + 1} L2 范数 = {n:.6f}   （文本 {txt!r}）")
checks.append(("向量必须已归一化（|‖v‖-1| < 1e-3）", all(abs(n - 1.0) < 1e-3 for n in norms)))

# 语义自检：相关句的余弦应高于无关句（真模型该有的性质，假模块做不到）
vs = emb.embed(["我喜欢用 Chrome 浏览器", "我平时都用 Chrome 上网", "今天中午吃什么"])
def cos(a, b):
    return sum(x * y for x, y in zip(a, b))
sim_rel = cos(vs[0], vs[1])
sim_unrel = cos(vs[0], vs[2])
print(f"  余弦(同义句) = {sim_rel:.4f}   余弦(无关句) = {sim_unrel:.4f}")
checks.append(("同义句相似度 > 无关句（真语义能力）", sim_rel > sim_unrel))

# 可关闭与重载
emb.close()
checks.append(("close() 后 ready 回到 False", emb.ready() is False))
vecs2 = emb.embed(["重新加载"])
checks.append(("close() 后仍可再次懒加载", emb.ready() is True and len(vecs2) == 1))

print()
print("-" * 74)
bad = 0
for label, ok in checks:
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        bad += 1
print("-" * 74)
print(f"C3 结果：{len(checks) - bad}/{len(checks)} 通过")
print(f"模型真实维度 = {emb.dim}（config 的 memory.dim 是 256，"
      f"两者不同是正常的：256 是 hashing 嵌入器的维度）")
emb.close()
raise SystemExit(0 if bad == 0 else 1)
