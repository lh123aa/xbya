"""P3 长期记忆测试（一）：嵌入器 + 存储 + 混合记忆

覆盖 `agent/providers/memory/` 全部模块：

- `hashing_embedder` —— 确定性、归一化、中文词形相似度质量门槛
- `st_embedder`       —— 懒加载、加载成功/失败两条路径、降级为词法检索
- `recall_store`      —— 中文分词、FTS5/向量/混合检索、容错、降级、并发
- `hybrid_memory`     —— 敏感判据、情景记忆、路由提示、"永不抛异常"

约定：DB 一律落在 `tmp_path`，**绝不写项目 data/**（`data/` 是运行期目录）。
"""

from __future__ import annotations

import os
import sqlite3
import sys
import threading
import time
from pathlib import Path

import pytest

project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from agent.providers.memory.hashing_embedder import (
    NGRAM_SIZES,
    TOKEN_WEIGHT,
    HashingEmbedder,
)
from agent.providers.memory.hybrid_memory import (
    DEFAULT_HINT_MIN_SCORE,
    HybridMemory,
)
from agent.providers.memory.recall_store import (
    DEFAULT_MIN_SIMILARITY,
    MEMORY_DB,
    RRF_K,
    VEC_TABLE,
    RecallStore,
    fts_match_expression,
    never_raises,
    rrf_fuse,
    tokenize,
)
from agent.providers.memory.st_embedder import SentenceTransformerEmbedder
from agent.seams.embedder import DEFAULT_DIM, EmbedderService, cosine
from agent.seams.memory import MemoryItem, MemoryKind, sensitive_reason


# ══════════════════════════════════════════════════
#  测试替身
# ══════════════════════════════════════════════════


class FakeEmbedder(EmbedderService):
    """可控嵌入器：按预置映射给出向量，便于精确断言检索语义"""

    capability_name = "embedder"

    def __init__(
        self,
        mapping=None,
        dim: int = 4,
        is_ready: bool = True,
        forced=None,
        raise_on_embed: bool = False,
        raise_on_dim: bool = False,
    ) -> None:
        self._mapping = mapping or {}
        self._dim = dim
        self._ready = is_ready
        self._forced = forced
        self._raise_on_embed = raise_on_embed
        self._raise_on_dim = raise_on_dim
        self.calls = []

    @property
    def dim(self):
        if self._raise_on_dim:
            raise RuntimeError("维度不可用")
        return self._dim

    def ready(self) -> bool:
        return self._ready

    def embed(self, texts):
        return [self.embed_one(t) for t in texts]

    def embed_one(self, text):
        self.calls.append(text)
        if self._raise_on_embed:
            raise RuntimeError("嵌入失败")
        if self._forced is not None:
            return list(self._forced)
        return list(self._mapping.get(text, [0.0] * self._dim))


class _NoDimEmbedder:
    """完全没有 dim 属性的嵌入器（第三方实现可能长这样）"""

    def ready(self) -> bool:
        return True

    def embed_one(self, text):
        return [1.0]

    def embed(self, texts):
        return [[1.0] for _ in texts]


class _BoomConnection:
    """close() 会失败的假连接（覆盖"连关都关不掉"的兜底分支）"""

    def close(self) -> None:
        raise sqlite3.OperationalError("关不掉")


class _FakeModel:
    """假句向量模型（不联网、不下载）"""

    def __init__(self, dim=8, fail_encode=False, dim_getter="ok") -> None:
        self.dim = dim
        self.fail_encode = fail_encode
        self.dim_getter = dim_getter

    def get_sentence_embedding_dimension(self):
        if self.dim_getter == "raise":
            raise RuntimeError("探不到维度")
        if self.dim_getter == "bad":
            return "not-a-number"
        if self.dim_getter == "none":
            return None
        return self.dim

    def encode(self, texts):
        if self.fail_encode:
            raise RuntimeError("编码失败")
        return [[float(i + 1)] * self.dim for i, _ in enumerate(texts)]


class _NoDimModel:
    """没有 get_sentence_embedding_dimension 的模型"""

    def __init__(self, dim=6) -> None:
        self.dim = dim

    def encode(self, texts):
        return [[1.0] * self.dim]


class _FakeModule:
    """假 sentence_transformers 模块"""

    def __init__(self, model=None, raise_on_build=False) -> None:
        self.calls = []
        self._model = model
        self._raise = raise_on_build

    def SentenceTransformer(self, name):          # noqa: N802 - 模拟真实类名
        self.calls.append(name)
        if self._raise:
            raise OSError("模型没下载")
        return self._model


def _item(text: str, kind=MemoryKind.FACT, item_id: str = "", **meta) -> MemoryItem:
    """构造测试用记忆条目"""
    return MemoryItem(text=text, kind=kind, item_id=item_id, metadata=dict(meta))


def _raiser(*args, **kwargs):
    """总是抛异常的桩（用于验证失败被隔离/被兜底）"""
    raise sqlite3.OperationalError("这一路坏了")


@pytest.fixture
def db_path(tmp_path) -> str:
    """落在临时目录里的库路径（父目录故意不存在，用于验证自动创建）"""
    return str(tmp_path / "sub" / "agent_memory.db")


@pytest.fixture
def store(db_path):
    """默认真实后端（`auto` → 装了 sqlite-vec 就是 sqlite_vec）"""
    s = RecallStore(db_path, embedder=HashingEmbedder())
    yield s
    s.close()


# ══════════════════════════════════════════════════
#  tokenize / MATCH 表达式 / RRF
# ══════════════════════════════════════════════════


class TestTokenize:
    """中文分词：自产 token 串（FTS5 自带分词器切不开中文）"""

    def test_chinese_with_ascii_suffix(self):
        """规格里给出的样例必须逐字命中"""
        assert tokenize("桌面上的合同.pdf") == (
            "桌 面 上 的 合 同 桌面 面上 上的 的合 合同 pdf"
        )

    def test_ascii_is_lowercased(self):
        assert tokenize("Chrome PDF") == "chrome pdf"

    def test_digits_kept(self):
        assert tokenize("2025年") == "2025 年"

    def test_punctuation_and_space_dropped(self):
        assert tokenize("你好！！ 世界") == "你 好 你好 世 界 世界"

    def test_single_cjk_char(self):
        """单字片段没有 bigram，只产出单字 token"""
        assert tokenize("好") == "好"

    def test_extension_a_cjk(self):
        """扩展 A 区汉字也算 CJK（避免生僻字被整段丢掉）"""
        assert tokenize("\u3400\u3401") == "\u3400 \u3401 \u3400\u3401"

    def test_empty_and_none(self):
        assert tokenize("") == ""
        assert tokenize(None) == ""
        assert tokenize("   ") == ""

    def test_punctuation_only(self):
        assert tokenize("！！？。，") == ""

    def test_non_str_is_coerced(self):
        assert tokenize(123) == "123"


class TestMatchExpression:
    """FTS5 MATCH 表达式构造（含引号转义）"""

    def test_or_joined_and_quoted(self):
        assert fts_match_expression("合 同 合同") == '"合" OR "同" OR "合同"'

    def test_empty(self):
        assert fts_match_expression("") == ""
        assert fts_match_expression(None) == ""
        assert fts_match_expression("   ") == ""

    def test_inner_quote_is_escaped(self):
        """FTS5 里 `"` 是短语引号：不双写转义会让 SQL 直接语法报错"""
        assert fts_match_expression('a"b') == '"a""b"'

    def test_escaped_expression_is_accepted_by_sqlite(self):
        """未转义的写法真的会炸，转义后的必须能被 FTS5 接受（回归防线）"""
        con = sqlite3.connect(":memory:")
        con.execute("CREATE VIRTUAL TABLE t USING fts5(tokens, item_id UNINDEXED)")
        con.execute("INSERT INTO t(tokens, item_id) VALUES (?, ?)", ("a b", "x"))

        with pytest.raises(sqlite3.OperationalError):
            con.execute('SELECT item_id FROM t WHERE t MATCH ?', ('"a"b"',)).fetchall()

        # 转义后不再语法报错（FTS5 会把它当成短语 "a b"，命中与否不重要）
        rows = con.execute(
            "SELECT item_id FROM t WHERE t MATCH ?", (fts_match_expression('a"b'),)
        ).fetchall()
        assert rows == [("x",)]
        con.close()


class TestRrfFuse:
    """RRF 融合"""

    def test_two_route_hit_wins(self):
        fused = rrf_fuse([["a", "b"], ["a", "c"]], k=60)
        assert fused[0][0] == "a"
        assert fused[0][1] == pytest.approx(2 / 61)

    def test_single_route(self):
        assert rrf_fuse([["x"]], k=60) == [("x", pytest.approx(1 / 61))]

    def test_tie_broken_by_item_id(self):
        """同分时按 item_id 升序 —— 检索结果必须可复现"""
        assert [i for i, _ in rrf_fuse([["b"], ["a"]], k=60)] == ["a", "b"]

    def test_empty(self):
        assert rrf_fuse([[], []]) == []
        assert rrf_fuse([]) == []


class TestNeverRaises:
    """"永不抛异常"装饰器"""

    def test_returns_configured_default(self):
        @never_raises(list)
        def explode():
            raise ValueError("炸了")

        assert explode() == []

    def test_factory_gives_fresh_object(self):
        @never_raises(list)
        def explode():
            raise ValueError("炸了")

        first = explode()
        first.append(1)
        assert explode() == []

    def test_passes_through_normal_return(self):
        @never_raises(list)
        def ok():
            return [1, 2]

        assert ok() == [1, 2]


# ══════════════════════════════════════════════════
#  HashingEmbedder
# ══════════════════════════════════════════════════


class TestHashingEmbedder:

    def test_default_dim(self):
        assert HashingEmbedder().dim == DEFAULT_DIM == 256

    def test_custom_dim_and_clamp(self):
        assert HashingEmbedder(dim=64).dim == 64
        assert HashingEmbedder(dim=0).dim == 1, "非法维度收敛为 1，避免取模除零"
        assert HashingEmbedder(dim=-5).dim == 1

    def test_describe_and_repr(self):
        emb = HashingEmbedder()
        assert emb.describe() == "HashingEmbedder(dim=256, ngram=2,3)"
        assert "256" in repr(emb)
        assert NGRAM_SIZES == (2, 3)
        assert TOKEN_WEIGHT == 1.0

    def test_empty_batch(self):
        assert HashingEmbedder().embed([]) == []

    def test_batch_length_matches_input(self):
        vectors = HashingEmbedder(dim=32).embed(["a", "b", "c"])
        assert len(vectors) == 3
        assert all(len(v) == 32 for v in vectors)

    def test_vectors_are_l2_normalized(self):
        vector = HashingEmbedder().embed_one("桌面上的合同备份")
        norm = sum(v * v for v in vector) ** 0.5
        assert norm == pytest.approx(1.0, abs=1e-6)

    def test_blank_inputs_give_zero_vectors(self):
        emb = HashingEmbedder(dim=16)
        for blank in ("", "   ", "\t\n", None):
            assert emb.embed_one(blank) == [0.0] * 16, f"{blank!r} 应为零向量"

    def test_non_str_is_coerced(self):
        emb = HashingEmbedder(dim=16)
        assert emb.embed_one(123) == emb.embed_one("123")
        assert any(v != 0.0 for v in emb.embed_one(123))

    def test_deterministic_across_instances(self):
        """跨实例稳定 = 跨进程稳定（不能用带随机盐的内置 hash()）"""
        a = HashingEmbedder(dim=64).embed_one("我喜欢用 Chrome")
        b = HashingEmbedder(dim=64).embed_one("我喜欢用 Chrome")
        assert a == b
        assert any(v != 0.0 for v in a)

    def test_pinned_vector_is_process_independent(self):
        """钉住一组具体数值：换成内置 hash() 会因随机盐立刻打破它

        这条断言的价值在**跨进程**：内置 `hash()` 的盐随 `PYTHONHASHSEED` 变化，
        于是落盘的向量与重启后查询的向量不在同一空间，向量检索会静默失效。
        钉住数值后，任何"图省事改回 hash()"的改动都会在这里失败。
        """
        vector = HashingEmbedder(dim=8).embed_one("桌面上的合同 backup")
        assert vector == pytest.approx(
            [-0.698535, -0.46569, -0.46569, -0.232845, 0.0, 0.0, 0.15523, 0.0],
            abs=1e-6,
        )

    def test_identical_text_is_maximally_similar(self):
        emb = HashingEmbedder()
        same = cosine(emb.embed_one("备份合同"), emb.embed_one("备份合同"))
        assert same == pytest.approx(1.0)

    def test_one_char_change_beats_unrelated(self):
        """质量门槛：改一个字的相似度必须明显高于完全无关的句子"""
        emb = HashingEmbedder()
        base = "我喜欢用Chrome浏览器看视频"
        changed = "我喜欢用Chrome浏览器看电影"
        unrelated = "今天天气不错适合出门散步"

        sim_changed = cosine(emb.embed_one(base), emb.embed_one(changed))
        sim_unrelated = cosine(emb.embed_one(base), emb.embed_one(unrelated))

        assert sim_changed > 0.5, f"改一字后相似度偏低: {sim_changed}"
        assert sim_unrelated < 0.3, f"无关句相似度偏高: {sim_unrelated}"
        assert sim_changed > sim_unrelated + 0.3

    def test_chinese_word_overlap(self):
        """共享字/词越多越相似（中文没有空格，靠字符 n-gram）"""
        emb = HashingEmbedder()
        sim_close = cosine(emb.embed_one("桌面上的合同文件"), emb.embed_one("桌面上的合同备份"))
        sim_far = cosine(emb.embed_one("桌面上的合同文件"), emb.embed_one("明天的天气预报"))
        assert sim_close > sim_far

    def test_feature_weights(self):
        """词元权重高于 n-gram，bigram 高于 trigram"""
        features = dict(HashingEmbedder._features("chrome 浏览器"))
        assert features["wchrome"] == TOKEN_WEIGHT == 1.0
        assert features["n2:浏览"] == 0.6
        assert features["n3:浏览器"] == 0.3
        assert features["wchrome"] > features["n2:浏览"] > features["n3:浏览器"]

    def test_features_have_no_punctuation(self):
        assert HashingEmbedder._features("！？。，") == []

    def test_bucket_range_and_signed_hashing(self):
        """带符号哈希：低位定符号、其余位定桶"""
        emb = HashingEmbedder(dim=16)
        signs = set()
        for feature in ("wchrome", "n2:合同", "n3:浏览器"):
            bucket, sign = emb._bucket(feature)
            assert 0 <= bucket < 16
            assert sign in (1.0, -1.0)
            signs.add(sign)
        assert len(signs) >= 1


# ══════════════════════════════════════════════════
#  SentenceTransformerEmbedder
# ══════════════════════════════════════════════════


class TestSentenceTransformerEmbedder:

    def test_construction_is_lazy(self):
        emb = SentenceTransformerEmbedder()
        assert emb.ready() is False, "构造阶段不得加载模型"
        assert emb.dim == 384
        assert "lazy" in emb.describe()
        assert "ready=False" in repr(emb)

    def test_defaults_for_blank_args(self):
        emb = SentenceTransformerEmbedder(model_name="", fallback_dim=0)
        assert "all-MiniLM-L6-v2" in emb.describe()
        assert emb.dim == 1

    def test_empty_batch_needs_no_model(self):
        emb = SentenceTransformerEmbedder()
        assert emb.embed([]) == []
        assert emb.ready() is False, "空批次不应触发加载"

    def test_load_success_with_fake_module(self, monkeypatch):
        """真实走一遍 `import sentence_transformers` 与构造（不下载模型）"""
        module = _FakeModule(_FakeModel(dim=8))
        monkeypatch.setitem(sys.modules, "sentence_transformers", module)

        emb = SentenceTransformerEmbedder(model_name="fake-model", fallback_dim=8)
        vectors = emb.embed(["你好", "世界"])

        assert module.calls == ["fake-model"]
        assert emb.ready() is True
        assert emb.dim == 8, "加载后应改用模型真实维度"
        assert "ready" in emb.describe()
        assert len(vectors) == 2
        for vector in vectors:
            assert len(vector) == 8
            assert sum(v * v for v in vector) == pytest.approx(1.0, abs=1e-6)

    def test_load_failure_when_module_missing(self, monkeypatch):
        """依赖没装：记 warning、标记不可用、返回零向量、不抛异常"""
        monkeypatch.setitem(sys.modules, "sentence_transformers", None)

        emb = SentenceTransformerEmbedder(fallback_dim=4)
        assert emb.embed(["你好"]) == [[0.0] * 4]
        assert emb.ready() is False
        assert emb.dim == 4

    def test_load_failure_is_not_retried(self, monkeypatch):
        """失败只尝试一次：每次重试都要 import torch，代价高"""
        module = _FakeModule(raise_on_build=True)
        monkeypatch.setitem(sys.modules, "sentence_transformers", module)

        emb = SentenceTransformerEmbedder(fallback_dim=4)
        assert emb.embed(["一"]) == [[0.0] * 4]
        assert emb.embed(["二"]) == [[0.0] * 4]
        assert len(module.calls) == 1

    def test_encode_failure_returns_zero_vectors(self, monkeypatch):
        module = _FakeModule(_FakeModel(dim=4, fail_encode=True))
        monkeypatch.setitem(sys.modules, "sentence_transformers", module)

        emb = SentenceTransformerEmbedder(fallback_dim=4)
        assert emb.embed(["你好"]) == [[0.0] * 4]
        assert emb.ready() is True, "模型本身加载成功，只是这次编码失败"

    def test_dim_getter_missing(self, monkeypatch):
        """模型没有维度接口时退回 fallback_dim"""
        monkeypatch.setattr(
            SentenceTransformerEmbedder, "_load_model", lambda self: _NoDimModel(dim=6)
        )
        emb = SentenceTransformerEmbedder(fallback_dim=6)
        emb.embed(["你好"])
        assert emb.dim == 6

    def test_dim_getter_raises(self, monkeypatch):
        monkeypatch.setattr(
            SentenceTransformerEmbedder,
            "_load_model",
            lambda self: _FakeModel(dim=6, dim_getter="raise"),
        )
        emb = SentenceTransformerEmbedder(fallback_dim=9)
        emb.embed(["你好"])
        assert emb.dim == 9

    def test_dim_getter_returns_garbage(self, monkeypatch):
        monkeypatch.setattr(
            SentenceTransformerEmbedder,
            "_load_model",
            lambda self: _FakeModel(dim=6, dim_getter="bad"),
        )
        emb = SentenceTransformerEmbedder(fallback_dim=9)
        emb.embed(["你好"])
        assert emb.dim == 9

    def test_dim_getter_returns_none(self, monkeypatch):
        monkeypatch.setattr(
            SentenceTransformerEmbedder,
            "_load_model",
            lambda self: _FakeModel(dim=6, dim_getter="none"),
        )
        emb = SentenceTransformerEmbedder(fallback_dim=9)
        emb.embed(["你好"])
        assert emb.dim == 9

    def test_to_vector_accepts_plain_list(self, monkeypatch):
        """模型输出没有 tolist() 时也要能收敛（list 分支）"""
        model = _FakeModel(dim=3)
        model.encode = lambda texts: [[3.0, 4.0, 0.0]]
        monkeypatch.setattr(SentenceTransformerEmbedder, "_load_model", lambda self: model)

        emb = SentenceTransformerEmbedder(fallback_dim=3)
        assert emb.embed(["你好"])[0] == pytest.approx([0.6, 0.8, 0.0])

    def test_concurrent_first_call_loads_once(self, monkeypatch):
        """加载进行中的并发调用：阻塞在锁上，模型只加载一次（锁内双检）"""
        entered = threading.Event()
        started = threading.Event()
        release = threading.Event()
        calls = []
        second = []

        def slow_load(self):
            calls.append(1)
            entered.set()
            release.wait(5.0)
            return _FakeModel(dim=2)

        monkeypatch.setattr(SentenceTransformerEmbedder, "_load_model", slow_load)
        emb = SentenceTransformerEmbedder(fallback_dim=2)

        worker = threading.Thread(target=lambda: emb.embed(["甲"]))
        worker.start()
        assert entered.wait(5.0), "加载线程未进入加载函数"

        def second_call():
            started.set()
            second.append(emb.embed(["乙"]))

        helper = threading.Thread(target=second_call)
        helper.start()
        assert started.wait(5.0)
        time.sleep(0.1)          # 让 helper 走到锁上（worker 此刻仍持锁）
        release.set()

        worker.join(5.0)
        helper.join(5.0)

        assert len(calls) == 1, "模型只应加载一次"
        assert len(second[0][0]) == 2

    def test_already_loaded_model_skips_lock(self, monkeypatch):
        """模型已就绪时走无锁快路径"""
        monkeypatch.setattr(
            SentenceTransformerEmbedder, "_load_model", lambda self: _FakeModel(dim=2)
        )
        emb = SentenceTransformerEmbedder(fallback_dim=2)
        emb.embed(["甲"])
        assert emb.ready() is True
        assert len(emb.embed(["乙"])[0]) == 2

    def test_close_is_idempotent_and_allows_reload(self, monkeypatch):
        module = _FakeModule(_FakeModel(dim=4))
        monkeypatch.setitem(sys.modules, "sentence_transformers", module)

        emb = SentenceTransformerEmbedder(fallback_dim=4)
        emb.embed(["你好"])
        assert emb.ready() is True

        emb.close()
        emb.close()                                  # 可重复调用
        assert emb.ready() is False
        assert emb.dim == 4

        emb.embed(["再来一次"])
        assert emb.ready() is True
        assert len(module.calls) == 2, "close 之后应能重新加载"


# ══════════════════════════════════════════════════
#  RecallStore：基础 CRUD
# ══════════════════════════════════════════════════


class TestStoreCrud:

    def test_add_get_round_trip(self, store):
        item = _item("喜欢用 Chrome", item_id="f1", source="test")
        assert store.add(item) is True
        assert item.item_id == "f1"

        got = store.get("f1")
        assert got is not None
        assert got.text == "喜欢用 Chrome"
        assert got.kind is MemoryKind.FACT
        assert got.metadata == {"source": "test"}
        assert got.created_at > 0
        assert got.use_count == 0

    def test_add_generates_item_id(self, store):
        item = _item("自动编号")
        assert store.add(item) is True
        assert item.item_id.startswith("m")
        assert store.get(item.item_id) is not None

    def test_add_empty_text_rejected(self, store):
        assert store.add(_item("   ")) is False
        assert store.count() == 0

    def test_add_non_item_rejected(self, store):
        assert store.add("不是条目") is False

    def test_add_truncates_long_text(self, store):
        assert store.add(_item("长" * 800)) is True
        assert len(store.recent(1)[0].text) == 512

    def test_add_same_id_replaces(self, store):
        store.add(_item("第一版", item_id="dup"))
        store.add(_item("第二版", item_id="dup"))

        assert store.count() == 1, "同 item_id 应覆盖而不是新增"
        assert store.get("dup").text == "第二版"
        assert store._con.execute(
            "SELECT COUNT(*) FROM memory_fts WHERE item_id = 'dup'"
        ).fetchone()[0] == 1, "FTS 表也不应留下重复行"

    def test_add_with_explicit_embedding(self, store):
        item = _item("显式向量", item_id="e1")
        assert store.add(item, embedding=[1.0, 0.0, 0.0, 0.0]) is True
        assert store.get("e1") is not None

    def test_get_unknown_and_blank(self, store):
        assert store.get("nope") is None
        assert store.get("") is None

    def test_remove(self, store):
        store.add(_item("待删", item_id="r1"))
        assert store.remove("r1") is True
        assert store.get("r1") is None
        assert store.remove("r1") is False, "重复删除返回 False"
        assert store.remove("") is False

    def test_remove_all_by_kind(self, store):
        store.add(_item("事实一", kind=MemoryKind.FACT))
        store.add(_item("事实二", kind=MemoryKind.FACT))
        store.add(_item("经历一", kind=MemoryKind.EPISODE))

        assert store.remove_all(MemoryKind.FACT) == 2
        assert store.count() == 1
        assert store.remove_all() == 1, "不带 kind 时全清"
        assert store.remove_all() == 0, "空库返回 0"

    def test_items_in_insertion_order(self, store):
        for i in range(3):
            store.add(_item(f"第{i}条", item_id=f"i{i}"))

        assert [it.text for it in store.items()] == ["第0条", "第1条", "第2条"]
        assert [it.text for it in store.items(limit=2)] == ["第0条", "第1条"]
        assert store.items(limit=0) == []

    def test_items_by_kind(self, store):
        store.add(_item("事实", kind=MemoryKind.FACT))
        store.add(_item("实体", kind=MemoryKind.ENTITY))
        assert [it.text for it in store.items(kind="entity")] == ["实体"]
        assert [it.text for it in store.items(kind=MemoryKind.ENTITY)] == ["实体"]

    def test_recent_is_newest_first(self, store):
        for i in range(4):
            store.add(_item(f"第{i}条"))
        assert [it.text for it in store.recent(2)] == ["第3条", "第2条"]
        assert [it.text for it in store.recent(2, kind="fact")] == ["第3条", "第2条"]
        assert store.recent(0) != [], "limit 至少为 1"

    def test_count_by_kind(self, store):
        store.add(_item("a", kind=MemoryKind.FACT))
        store.add(_item("b", kind=MemoryKind.EPISODE))
        assert store.count() == 2
        assert store.count(MemoryKind.EPISODE) == 1
        assert store.count("episode") == 1
        # 类别不认识时由 coerce_kind 收敛成 fact（宁可归类错也不丢条目）
        assert store.count("天书") == 1

    def test_by_kind(self, store):
        store.add(_item("a", kind=MemoryKind.FACT))
        store.add(_item("b", kind=MemoryKind.EPISODE))
        assert store.by_kind() == {"fact": 1, "episode": 1}

    def test_touch_updates_heat(self, store):
        store.add(_item("热的", item_id="h1"))
        store.add(_item("冷的", item_id="h2"))

        assert store.touch(["h1", "h1", "不存在"]) == 2
        assert store.get("h1").use_count == 2
        assert store.get("h2").use_count == 0
        assert store.touch([]) == 0

    def test_corrupt_metadata_falls_back_to_empty_dict(self, store):
        store.add(_item("坏元数据", item_id="bad"))
        store._con.execute(
            "UPDATE memory_items SET metadata = ? WHERE item_id = ?", ("{不是 JSON", "bad")
        )
        store._con.commit()
        assert store.get("bad").metadata == {}

    def test_metadata_non_object_falls_back(self, store):
        store.add(_item("数组元数据", item_id="arr"))
        store._con.execute(
            "UPDATE memory_items SET metadata = ? WHERE item_id = ?", ("[1,2]", "arr")
        )
        store._con.commit()
        assert store.get("arr").metadata == {}

    def test_metadata_empty_string(self, store):
        store.add(_item("空元数据", item_id="none"))
        store._con.execute(
            "UPDATE memory_items SET metadata = ? WHERE item_id = ?", ("", "none")
        )
        store._con.commit()
        assert store.get("none").metadata == {}

    def test_metadata_unicode_preserved(self, store):
        store.add(_item("中文元数据", item_id="u1", note="档案"))
        assert store.get("u1").metadata == {"note": "档案"}

    def test_stats_shape(self, store):
        store.add(_item("一条", kind=MemoryKind.FACT))
        stats = store.stats()

        for key in ("count", "by_kind", "vector_backend", "dim", "path", "pruned", "degraded"):
            assert key in stats, f"stats 缺少 {key}"
        assert stats["count"] == 1
        assert stats["by_kind"] == {"fact": 1}
        assert stats["pruned"] == 0
        assert stats["degraded"] is False
        assert stats["ready"] is True
        assert stats["writes"] == 1
        assert stats["rrf_k"] == RRF_K
        assert "HashingEmbedder" in stats["embedder"]
        assert stats["path"].endswith("agent_memory.db")
        assert stats["requested_path"] == stats["path"]

    def test_properties(self, store, db_path):
        assert store.dim == DEFAULT_DIM
        assert store.path == db_path
        assert store.embedder is not None
        assert store.ready is True
        assert store.vector_backend in ("python", "sqlite_vec")
        assert "RecallStore" in repr(store)

    def test_parent_directory_created(self, db_path):
        assert not Path(db_path).parent.exists()
        store = RecallStore(db_path)
        try:
            assert Path(db_path).parent.is_dir()
        finally:
            store.close()

    def test_wal_pragma(self, store):
        mode = store._con.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode.lower() == "wal"


class TestStorePrune:

    def test_prune_keeps_hot_items(self, db_path):
        """每次 add 都会自动淘汰 —— 被 touch 过的条目必须活下来"""
        store = RecallStore(db_path, embedder=HashingEmbedder(), max_items=3)
        try:
            for i in range(3):
                store.add(_item(f"第{i}条", item_id=f"p{i}"))
            store.touch(["p0", "p1"])          # 让前两条变"热"
            assert store.prune() == 0, "未超限时不应淘汰"

            store.add(_item("第四条", item_id="p3"))
            store.add(_item("第五条", item_id="p4"))

            remaining = {it.item_id for it in store.items()}
            assert store.count() == 3
            assert {"p0", "p1"} <= remaining, f"热门条目被误删: {remaining}"
            assert store.stats()["pruned"] == 2
        finally:
            store.close()

    def test_explicit_prune_when_over_limit(self, db_path):
        """上限调小后，显式 prune() 负责把超出部分清掉"""
        store = RecallStore(db_path, embedder=HashingEmbedder(), max_items=10)
        try:
            for i in range(6):
                store.add(_item(f"第{i}条", item_id=f"q{i}"))
            assert store.count() == 6

            store._max_items = 3               # 模拟配置从 10 调成 3
            assert store.prune() == 3
            assert store.count() == 3
            assert store.prune() == 0
        finally:
            store.close()

    def test_add_triggers_prune(self, db_path):
        store = RecallStore(db_path, embedder=HashingEmbedder(), max_items=2)
        try:
            for i in range(4):
                store.add(_item(f"第{i}条"))
            assert store.count() == 2
        finally:
            store.close()

    def test_prune_without_overflow(self, store):
        store.add(_item("唯一一条"))
        assert store.prune() == 0

    def test_prune_updates_fts_and_vec_index(self, db_path):
        store = RecallStore(db_path, embedder=HashingEmbedder(), max_items=1)
        try:
            store.add(_item("合同备份", item_id="old"))
            store.add(_item("天气预报", item_id="new"))

            assert store.get("old") is None
            assert store._con.execute(
                "SELECT COUNT(*) FROM memory_fts WHERE item_id = 'old'"
            ).fetchone()[0] == 0
            if store.vector_backend == "sqlite_vec":
                assert store._con.execute(
                    f"SELECT COUNT(*) FROM {VEC_TABLE} WHERE item_id = 'old'"
                ).fetchone()[0] == 0
        finally:
            store.close()


# ══════════════════════════════════════════════════
#  RecallStore：混合检索
# ══════════════════════════════════════════════════


class TestSearchChinese:
    """中文检索命中 —— 本能力存在的首要理由"""

    def test_keyword_finds_chinese_sentence(self, store):
        store.add(_item("桌面上的合同.pdf 是上周的"))
        store.add(_item("会议纪要放在文档目录里"))
        store.add(_item("喜欢用 Chrome 浏览器"))

        hits = store.search("合同")
        assert hits, "中文关键词必须能命中文档（FTS5 默认分词器做不到）"
        assert "合同" in hits[0].text
        assert hits[0].score > 0
        assert len(hits) == 1, "不该把无关条目也召回"

    def test_shared_chars_hit(self, store):
        store.add(_item("把预算表整理一下"))
        store.add(_item("明天要下雨记得带伞"))
        hits = store.search("预算")
        assert hits and "预算表" in hits[0].text

    def test_kind_filter_applies(self, store):
        store.add(_item("合同的备份", kind=MemoryKind.FACT))
        store.add(_item("合同已经发出去了", kind=MemoryKind.EPISODE))

        assert len(store.search("合同")) == 2
        facts = store.search("合同", kind=MemoryKind.FACT)
        assert [it.kind for it in facts] == [MemoryKind.FACT]
        episodes = store.search("合同", kind="episode")
        assert [it.kind for it in episodes] == [MemoryKind.EPISODE]

    def test_limit_respected(self, store):
        for i in range(8):
            store.add(_item(f"合同备份第{i}号文件"))
        assert len(store.search("合同", limit=3)) <= 3
        assert len(store.search("合同", limit=1)) <= 1

    def test_empty_and_punctuation_query(self, store):
        store.add(_item("合同备份"))
        assert store.search("") == []
        assert store.search("   ") == []
        assert store.search("！！？。") == []
        assert store.search(None) == []

    def test_no_hits(self, store):
        store.add(_item("喜欢用 Chrome"))
        assert store.search("宇航员登陆火星") == []

    def test_english_query(self, store):
        store.add(_item("喜欢用 Chrome 浏览器"))
        store.add(_item("常用编辑器是 VSCode"))
        hits = store.search("chrome")
        assert hits and "Chrome" in hits[0].text


class TestSearchFusion:
    """两路融合：RRF 让"两路都命中"排前面"""

    @pytest.fixture
    def fused_store(self, tmp_path):
        # 查询「合同」→ [1,0,0,0]
        fake = FakeEmbedder(
            mapping={
                "合同": [1.0, 0.0, 0.0, 0.0],
                "桌面上的合同": [1.0, 0.0, 0.0, 0.0],   # 两路都命中
                "合同的备份": [0.0, 1.0, 0.0, 0.0],     # 仅词法（余弦 0）
                "备份文件": [1.0, 0.0, 0.0, 0.0],       # 仅向量（词法无 token）
                "无关内容": [0.0, 0.0, 1.0, 0.0],       # 两路都不该命中
            }
        )
        store = RecallStore(str(tmp_path / "fusion.db"), embedder=fake)
        store.add(_item("桌面上的合同", item_id="both"))
        store.add(_item("合同的备份", item_id="lexical"))
        store.add(_item("备份文件", item_id="vector"))
        store.add(_item("无关内容", item_id="none"))
        yield store
        store.close()

    def test_both_route_hit_ranks_first(self, fused_store):
        hits = fused_store.search("合同")
        assert hits[0].item_id == "both", "两路都命中的条目必须排第一"
        ids = {it.item_id for it in hits}
        assert "lexical" in ids and "vector" in ids
        assert "none" not in ids, "两路都没命中的条目不该被召回"

    def test_scores_are_rrf(self, fused_store):
        hits = fused_store.search("合同")
        assert hits[0].score > 1 / (RRF_K + 1), "双路命中必须高于单路第一名"
        assert hits[0].score > hits[1].score

    def test_min_similarity_filters_low_cosine(self, fused_store):
        """低于门槛的向量候选不进融合（否则任何输入都能召回一堆）"""
        assert fused_store._min_similarity == DEFAULT_MIN_SIMILARITY
        vector_hits = fused_store._search_vector_python([1.0, 0.0, 0.0, 0.0], None, 10)
        assert "lexical" not in vector_hits, "余弦 0 的条目不该出现在向量候选里"
        assert "both" in vector_hits

    def test_kind_filter_in_vector_route(self, fused_store):
        """kind 过滤必须在两路都生效（融合后再过滤会被别类挤占候选）"""
        fused_store.add(_item("备份文件", kind=MemoryKind.EPISODE, item_id="vec_ep"))
        hits = fused_store.search("合同", kind=MemoryKind.EPISODE)
        assert [it.item_id for it in hits] == ["vec_ep"]

    def test_vector_route_used_when_lexical_misses(self, tmp_path):
        fake = FakeEmbedder(
            mapping={"查询词": [1.0, 0.0, 0.0, 0.0], "备份文件": [1.0, 0.0, 0.0, 0.0]}
        )
        store = RecallStore(str(tmp_path / "vec.db"), embedder=fake, vector_backend="python")
        try:
            store.add(_item("备份文件", item_id="v1"))
            assert [it.item_id for it in store.search("查询词")] == ["v1"]
        finally:
            store.close()

    def test_search_does_not_touch_heat(self, fused_store):
        """store 层不更新热度（那是 HybridMemory.recall 的职责）"""
        fused_store.search("合同")
        assert fused_store.get("both").use_count == 0


class TestVectorBackends:
    """向量后端：python / sqlite_vec / 自动降级"""

    @pytest.mark.parametrize("backend", ["python", "sqlite_vec"])
    def test_same_results_on_both_backends(self, tmp_path, backend):
        fake = FakeEmbedder(
            mapping={"合同": [1.0, 0.0, 0.0, 0.0], "桌面上的合同": [1.0, 0.0, 0.0, 0.0]}
        )
        store = RecallStore(
            str(tmp_path / f"{backend}.db"), embedder=fake, vector_backend=backend
        )
        try:
            if backend == "sqlite_vec" and store.vector_backend != "sqlite_vec":
                pytest.skip("本机没有可用的 sqlite-vec 扩展")
            store.add(_item("桌面上的合同", item_id="a"))
            assert store.vector_backend == backend
            assert [it.item_id for it in store.search("合同")] == ["a"]
        finally:
            store.close()

    @pytest.mark.parametrize("backend", ["python", "sqlite_vec"])
    def test_kind_filter_on_each_backend(self, tmp_path, backend):
        fake = FakeEmbedder(
            mapping={
                "查询词": [1.0, 0.0, 0.0, 0.0],
                "甲类条目": [1.0, 0.0, 0.0, 0.0],
                "乙类条目": [1.0, 0.0, 0.0, 0.0],
            }
        )
        store = RecallStore(
            str(tmp_path / f"kind_{backend}.db"), embedder=fake, vector_backend=backend
        )
        try:
            if backend == "sqlite_vec" and store.vector_backend != "sqlite_vec":
                pytest.skip("本机没有可用的 sqlite-vec 扩展")
            store.add(_item("甲类条目", kind=MemoryKind.FACT, item_id="f"))
            store.add(_item("乙类条目", kind=MemoryKind.EPISODE, item_id="e"))

            assert [it.item_id for it in store.search("查询词", kind=MemoryKind.EPISODE)] == ["e"]
            assert [it.item_id for it in store.search("查询词", kind=MemoryKind.FACT)] == ["f"]
        finally:
            store.close()

    def test_rows_without_embedding_are_skipped(self, tmp_path):
        """embedding 为 NULL 的条目（只走词法）不该进向量候选"""
        store = RecallStore(
            str(tmp_path / "nullemb.db"),
            embedder=FakeEmbedder(mapping={"查询词": [1.0, 0, 0, 0]}),
            vector_backend="python",
        )
        try:
            store.add(_item("备份文件", item_id="n1"))
            store._con.execute(
                "UPDATE memory_items SET embedding = NULL WHERE item_id = ?", ("n1",)
            )
            store._con.commit()
            assert store._search_vector_python([1.0, 0, 0, 0], None, 5) == []
        finally:
            store.close()

    def test_auto_falls_back_to_python_without_module(self, tmp_path, monkeypatch):
        """未安装 sqlite-vec：auto 下退回 python，且**不算降级**"""
        import agent.providers.memory.recall_store as module

        monkeypatch.setattr(module, "_import_sqlite_vec", lambda: None)
        store = RecallStore(str(tmp_path / "auto.db"), vector_backend="auto")
        try:
            assert store.vector_backend == "python"
            assert store.stats()["degraded"] is False, "auto 兜底属正常路径"
        finally:
            store.close()

    def test_explicit_sqlite_vec_degrades_with_warning(self, tmp_path, monkeypatch):
        """显式要求 sqlite_vec 却拿不到：告警 + degraded，但功能仍可用"""
        import agent.providers.memory.recall_store as module

        monkeypatch.setattr(module, "_import_sqlite_vec", lambda: None)
        store = RecallStore(str(tmp_path / "want.db"), vector_backend="sqlite_vec")
        try:
            assert store.vector_backend == "python"
            assert store.stats()["degraded"] is True
            assert "sqlite" in store.stats()["degraded_reason"]
            assert store.add(_item("仍然能写")) is True
        finally:
            store.close()

    def test_extension_load_failure_falls_back(self, tmp_path, monkeypatch):
        """扩展导入成功但 load() 失败（缺 DLL / 版本不匹配）"""
        import agent.providers.memory.recall_store as module

        class _BrokenModule:
            @staticmethod
            def load(con):
                raise sqlite3.OperationalError("扩展加载失败")

        monkeypatch.setattr(module, "_import_sqlite_vec", lambda: _BrokenModule())
        store = RecallStore(str(tmp_path / "broken.db"), vector_backend="sqlite_vec")
        try:
            assert store.vector_backend == "python"
            assert store.stats()["degraded"] is True
        finally:
            store.close()

    def test_import_sqlite_vec_failure_path(self, monkeypatch):
        """真的把模块屏蔽掉，走一遍 import 失败的 except"""
        import agent.providers.memory.recall_store as module

        monkeypatch.setitem(sys.modules, "sqlite_vec", None)
        assert module._import_sqlite_vec() is None

    def test_vec_index_dimension_mismatch_rebuilds(self, tmp_path):
        """换维度（换嵌入器）后索引表被重建，检索不至于全废"""
        path = str(tmp_path / "dim.db")
        first = RecallStore(path, embedder=FakeEmbedder(dim=3), vector_backend="sqlite_vec")
        try:
            if first.vector_backend != "sqlite_vec":
                pytest.skip("本机没有可用的 sqlite-vec 扩展")
            first.add(_item("老维度", item_id="old"))
        finally:
            first.close()

        second = RecallStore(path, embedder=FakeEmbedder(dim=5), vector_backend="sqlite_vec")
        try:
            ddl = second._con.execute(
                "SELECT sql FROM sqlite_master WHERE name = ?", (VEC_TABLE,)
            ).fetchone()[0]
            assert "float[5]" in ddl
            assert second.add(_item("新维度", item_id="new")) is True
        finally:
            second.close()

    def test_vec_dimension_mismatch_on_add_skips_index_only(self, tmp_path):
        """显式传入维度不符的向量：只跳过索引，不让写入失败"""
        store = RecallStore(
            str(tmp_path / "skip.db"),
            embedder=FakeEmbedder(dim=4),
            vector_backend="sqlite_vec",
        )
        try:
            if store.vector_backend != "sqlite_vec":
                pytest.skip("本机没有可用的 sqlite-vec 扩展")
            assert store.add(_item("维度不符", item_id="odd"), embedding=[1.0, 0.0]) is True
            assert store.get("odd") is not None
            assert store._con.execute(
                f"SELECT COUNT(*) FROM {VEC_TABLE} WHERE item_id = 'odd'"
            ).fetchone()[0] == 0
        finally:
            store.close()

    def test_unknown_backend_config_falls_back_to_auto(self, tmp_path):
        store = RecallStore(str(tmp_path / "unknown.db"), vector_backend="魔法")
        try:
            assert store.stats()["vector_backend_config"] == "auto"
        finally:
            store.close()

    def test_reopen_with_same_dim_keeps_existing_index(self, tmp_path):
        """同维度重开：索引表已存在且维度一致 → 不重建（数据仍在）"""
        path = str(tmp_path / "reopen.db")
        first = RecallStore(path, embedder=FakeEmbedder(dim=4), vector_backend="sqlite_vec")
        try:
            if first.vector_backend != "sqlite_vec":
                pytest.skip("本机没有可用的 sqlite-vec 扩展")
            first.add(_item("用甲", item_id="a"), embedding=[1.0, 0, 0, 0])
            assert first.count() == 1
        finally:
            first.close()

        second = RecallStore(path, embedder=FakeEmbedder(dim=4), vector_backend="sqlite_vec")
        try:
            assert second.count() == 1, "重开不该丢数据（说明索引表没有被重建）"
            assert second._con.execute(
                f"SELECT COUNT(*) FROM {VEC_TABLE}"
            ).fetchone()[0] == 1
        finally:
            second.close()

    def test_empty_delete_and_fetch_are_noops(self, store):
        """批量删除/取回的空入参早退（公共 API 已在更外层挡掉，这里直测防御分支）"""
        assert store._delete_items([]) == 0
        assert store._load_items([]) == {}

    def test_vec_query_dimension_mismatch_is_guarded(self, tmp_path):
        """查询向量维度与索引不符 → 向量那一路失败，但**不拖垮整次检索**"""
        fake = FakeEmbedder(dim=4, forced=[1.0, 0.0, 0.0, 0.0])
        store = RecallStore(
            str(tmp_path / "guard.db"), embedder=fake, vector_backend="sqlite_vec"
        )
        try:
            if store.vector_backend != "sqlite_vec":
                pytest.skip("本机没有可用的 sqlite-vec 扩展")
            store.add(_item("合同备份"))
            fake._forced = [1.0, 0.0]              # 模拟换嵌入器后维度突变
            assert store.search("合同"), "词法那一路仍应给出结果"
        finally:
            store.close()


class TestSearchRouteIsolation:

    def test_lexical_failure_keeps_vector_route(self, tmp_path):
        """FTS 表坏掉时词法路作废，但向量路仍能召回"""
        fake = FakeEmbedder(
            mapping={"查询词": [1.0, 0.0, 0.0, 0.0], "备份文件": [1.0, 0.0, 0.0, 0.0]}
        )
        store = RecallStore(
            str(tmp_path / "iso.db"), embedder=fake, vector_backend="python"
        )
        try:
            store.add(_item("备份文件", item_id="v1"))
            store._con.execute("DROP TABLE memory_fts")
            store._con.commit()

            assert [it.item_id for it in store.search("查询词")] == ["v1"]
        finally:
            store.close()

    def test_both_routes_broken_returns_empty(self, tmp_path, monkeypatch):
        store = RecallStore(str(tmp_path / "dead.db"), embedder=FakeEmbedder())
        try:
            store.add(_item("合同备份"))
            monkeypatch.setattr(store, "_search_lexical", _raiser)
            monkeypatch.setattr(store, "_search_vector", _raiser)
            assert store.search("合同") == []
        finally:
            store.close()

    def test_lexical_with_empty_tokens_is_empty(self, store):
        """直接调私有检索函数时也要能处理空 token 串（防御早退）"""
        assert store._search_lexical("！！", None, 5) == []

    def test_item_vanishing_before_fetch_is_skipped(self, tmp_path, monkeypatch):
        """融合之后条目被删（并发）→ 跳过而不是抛 KeyError"""
        store = RecallStore(str(tmp_path / "gone.db"), embedder=HashingEmbedder())
        try:
            store.add(_item("合同备份", item_id="gone"))
            monkeypatch.setattr(store, "_load_items", lambda ids: {})
            assert store.search("合同") == []
        finally:
            store.close()


# ══════════════════════════════════════════════════
#  容错与降级
# ══════════════════════════════════════════════════


class TestDegradation:

    def test_forbidden_user_dir_degrades_to_memory(self):
        desktop = Path(os.path.expanduser("~")) / "Desktop" / "agent_memory.db"
        store = RecallStore(str(desktop))
        try:
            assert store.path == MEMORY_DB
            assert store.stats()["degraded"] is True
            assert "受保护" in store.stats()["degraded_reason"]
            assert store.add(_item("内存模式也能记")) is True
            assert store.count() == 1
        finally:
            store.close()

    def test_forbidden_dir_match_is_case_insensitive(self, tmp_path):
        store = RecallStore(str(tmp_path / "Downloads" / "m.db"))
        try:
            assert store.path == MEMORY_DB
            assert store.stats()["degraded"] is True
        finally:
            store.close()

    def test_corrupt_db_file_degrades_to_memory(self, tmp_path):
        path = tmp_path / "corrupt.db"
        path.write_bytes(b"\x00\x01 not a database at all " * 8)

        store = RecallStore(str(path))
        try:
            assert store.stats()["degraded"] is True
            assert store.path == MEMORY_DB
            assert store.add(_item("损坏库之后仍可用")) is True
            assert store.search("损坏") != []
        finally:
            store.close()

    def test_wrong_schema_degrades_to_memory(self, tmp_path):
        """表结构异常（旧版本遗留 / 别的程序占用同名文件）"""
        path = tmp_path / "wrong.db"
        con = sqlite3.connect(str(path))
        con.execute("CREATE TABLE memory_items (item_id TEXT)")
        con.execute("CREATE VIRTUAL TABLE memory_fts USING fts5(tokens)")
        con.commit()
        con.close()

        store = RecallStore(str(path))
        try:
            assert store.stats()["degraded"] is True
            assert "表结构异常" in store.stats()["degraded_reason"]
            assert store.count() == 0
        finally:
            store.close()

    def test_parent_path_is_file_degrades(self, tmp_path):
        """父"目录"其实是文件 → mkdir 失败 → 内存模式（不阻塞启动）"""
        blocker = tmp_path / "blocker"
        blocker.write_text("x", encoding="utf-8")

        store = RecallStore(str(blocker / "sub" / "m.db"))
        try:
            assert store.stats()["degraded"] is True
        finally:
            store.close()

    def test_connect_failure_degrades(self, tmp_path, monkeypatch):
        """连 sqlite3.connect 都失败（路径非法）"""
        monkeypatch.setattr(sqlite3, "connect", _raiser)
        store = RecallStore(str(tmp_path / "x.db"))
        # monkeypatch 也拦住了内存库，故此时连内存模式都建不起来
        assert store.stats()["degraded"] is True
        assert store.stats()["ready"] is False
        assert store.count() == 0

    def test_memory_fallback_failure_marks_unavailable(self, tmp_path, monkeypatch):
        """连内存库都建不起来 → 全部方法安全降级为"什么都没有" """
        monkeypatch.setattr(RecallStore, "_create_schema", _raiser)
        store = RecallStore(str(tmp_path / "dead.db"))

        assert store.stats()["ready"] is False
        assert store.add(_item("写不进去")) is False
        assert store.get("x") is None
        assert store.remove("x") is False
        assert store.remove_all() == 0
        assert store.items() == []
        assert store.recent() == []
        assert store.count() == 0
        assert store.by_kind() == {}
        assert store.touch(["x"]) == 0
        assert store.prune() == 0
        assert store.search("合同") == []
        assert store.stats()["degraded"] is True

    def test_close_is_idempotent(self, store):
        store.add(_item("一条"))
        store.close()
        store.close()
        assert store.ready is False
        assert store.stats()["ready"] is False

    def test_close_swallows_connection_error(self, store):
        """连接自身 close() 失败也要吞掉（不能把关闭变成异常源）"""
        store._con = _BoomConnection()
        store.close()
        assert store.ready is False

    def test_broken_connection_never_raises(self, store):
        """连接被外部关掉后，所有公开方法仍不得抛异常"""
        store.add(_item("合同备份", item_id="x"))
        store._con.close()                      # 绕过 store.close()，模拟外部破坏

        assert store.add(_item("再写")) is False
        assert store.get("x") is None
        assert store.remove("x") is False
        assert store.remove_all() == 0
        assert store.items() == []
        assert store.recent() == []
        assert store.count() == 0
        assert store.by_kind() == {}
        assert store.touch(["x"]) == 0
        assert store.prune() == 0
        assert store.search("合同") == []
        assert isinstance(store.stats(), dict)

    def test_embedder_dim_failure_uses_configured_dim(self, tmp_path):
        store = RecallStore(
            str(tmp_path / "dimless.db"),
            embedder=FakeEmbedder(raise_on_dim=True),
            dim=7,
        )
        try:
            assert store.dim == 7
        finally:
            store.close()

    def test_embedder_without_dim_attribute(self, tmp_path):
        store = RecallStore(str(tmp_path / "nodim.db"), embedder=_NoDimEmbedder(), dim=9)
        try:
            assert store.dim == 9
        finally:
            store.close()


class TestLexicalOnlyMode:
    """没有嵌入器时的纯词法模式"""

    def test_search_works_without_embedder(self, tmp_path):
        store = RecallStore(str(tmp_path / "lex.db"))
        try:
            store.add(_item("桌面上的合同备份", item_id="a"))
            assert store.embedder is None
            assert store.dim == DEFAULT_DIM
            hits = store.search("合同")
            assert [it.item_id for it in hits] == ["a"]
            assert hits[0].score == pytest.approx(1 / (RRF_K + 1))
        finally:
            store.close()

    def test_unready_embedder_is_ignored(self, tmp_path):
        store = RecallStore(
            str(tmp_path / "unready.db"), embedder=FakeEmbedder(is_ready=False)
        )
        try:
            store.add(_item("合同备份", item_id="a"))
            assert store.search("合同")[0].item_id == "a"
        finally:
            store.close()

    def test_zero_vector_result_is_ignored(self, tmp_path):
        store = RecallStore(
            str(tmp_path / "zero.db"),
            embedder=FakeEmbedder(dim=4, forced=[0.0, 0.0, 0.0, 0.0]),
        )
        try:
            store.add(_item("合同备份", item_id="a"))
            assert store._search_vector("合同", None, 5) == []
            assert store.search("合同")[0].item_id == "a"
        finally:
            store.close()

    def test_embedder_raising_does_not_crash_add(self, tmp_path):
        """嵌入器违约抛异常时 add 兜底为 False（不抛异常）"""
        store = RecallStore(
            str(tmp_path / "boom.db"), embedder=FakeEmbedder(raise_on_embed=True)
        )
        try:
            assert store.add(_item("嵌入炸了", item_id="a")) is False
            assert store.get("a") is None
        finally:
            store.close()


class TestConcurrency:

    def test_parallel_writes_and_reads(self, tmp_path):
        """多线程访问：连接开着 check_same_thread=False，靠锁串行化"""
        store = RecallStore(
            str(tmp_path / "concurrent.db"), embedder=HashingEmbedder(dim=64)
        )
        errors = []
        barrier = threading.Barrier(6)

        def writer(worker: int) -> None:
            try:
                barrier.wait(timeout=5)
                for i in range(20):
                    store.add(_item(f"工人{worker}的第{i}份合同备份"))
            except Exception as e:
                errors.append(e)

        def reader() -> None:
            try:
                barrier.wait(timeout=5)
                for _ in range(20):
                    store.search("合同")
                    store.count()
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer, args=(w,)) for w in range(4)]
        threads += [threading.Thread(target=reader) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)

        try:
            assert errors == [], f"并发访问出错: {errors}"
            assert store.count() == 80
            assert store.search("合同", limit=5)
        finally:
            store.close()


# ══════════════════════════════════════════════════
#  HybridMemory
# ══════════════════════════════════════════════════


@pytest.fixture
def memory(tmp_path):
    store = RecallStore(str(tmp_path / "hybrid.db"), embedder=HashingEmbedder())
    mem = HybridMemory(store)
    yield mem
    mem.close()


class TestHybridRemember:

    def test_remember_and_recall(self, memory):
        item_id = memory.remember("喜欢用 Chrome 浏览器")
        assert item_id and item_id.startswith("m")
        assert memory.count() == 1

        hits = memory.recall("浏览器")
        assert [it.item_id for it in hits] == [item_id]
        assert memory.get(item_id).use_count == 1, "命中应刷新热度"

    def test_remember_rejects_sensitive_content(self, memory, caplog):
        """敏感信息：直接拒绝，且日志里绝不出现原文"""
        secret = "我的密码是 hunter2xxxx"
        assert sensitive_reason(secret) == "密码"

        with caplog.at_level("WARNING"):
            assert memory.remember(secret) is None

        assert memory.count() == 0
        assert "密码" in caplog.text
        assert "hunter2xxxx" not in caplog.text, "原文不得进日志"
        assert memory.stats()["rejected_sensitive"] == 1

    def test_remember_rejects_empty(self, memory):
        assert memory.remember("") is None
        assert memory.remember("   ") is None
        assert memory.remember(None) is None
        assert memory.count() == 0

    def test_remember_truncates(self, memory):
        memory.remember("长" * 900)
        assert len(memory.recent(1)[0].text) == 512

    def test_sensitive_scan_happens_before_truncation(self, memory):
        """先判敏感再截断：藏在 512 字之后的密码也不能漏"""
        assert memory.remember("啊" * 600 + "密码: abc123") is None
        assert memory.count() == 0

    def test_remember_kind_and_metadata(self, memory):
        item_id = memory.remember(
            "上次找过合同", kind="episode", metadata={"action": "file_search"}
        )
        item = memory.get(item_id)
        assert item.kind is MemoryKind.EPISODE
        assert item.metadata == {"action": "file_search"}

    def test_remember_returns_none_when_store_fails(self, memory, monkeypatch):
        monkeypatch.setattr(memory.store, "add", lambda *a, **k: False)
        assert memory.remember("写不进去") is None
        assert memory.stats()["remembered"] == 0

    def test_remember_without_embedder(self, tmp_path):
        store = RecallStore(str(tmp_path / "noembed.db"))
        mem = HybridMemory(store)
        try:
            assert mem.remember("没有嵌入器也能记") is not None
            assert mem.recall("嵌入器") != []
        finally:
            mem.close()

    def test_remember_with_unready_embedder(self, tmp_path):
        store = RecallStore(
            str(tmp_path / "unready.db"), embedder=FakeEmbedder(is_ready=False)
        )
        mem = HybridMemory(store)
        try:
            assert mem.remember("记下来") is not None
        finally:
            mem.close()

    def test_remember_with_zero_vector_embedder(self, tmp_path):
        store = RecallStore(
            str(tmp_path / "zerovec.db"), embedder=FakeEmbedder(dim=4, forced=[0.0] * 4)
        )
        mem = HybridMemory(store)
        try:
            assert mem.remember("零向量") is not None
        finally:
            mem.close()

    def test_store_embedder_is_reused(self, tmp_path):
        """装配层只建一个嵌入器：HybridMemory 不传 embedder 时复用 store 的"""
        emb = HashingEmbedder(dim=32)
        store = RecallStore(str(tmp_path / "reuse.db"), embedder=emb)
        mem = HybridMemory(store)
        try:
            assert mem._embedder is emb
        finally:
            mem.close()


class TestHybridRecall:

    def test_recall_miss_counts(self, memory):
        memory.remember("喜欢用 Chrome")
        assert memory.recall("航天飞机发射") == []
        assert memory.stats()["recall_misses"] == 1
        assert memory.stats()["recall_hits"] == 0

    def test_recall_text(self, memory):
        memory.remember("喜欢用 Chrome 浏览器")
        memory.remember("常用目录是 D 盘")
        text = memory.recall_text("浏览器")
        assert text.startswith("我记得你提过：")
        assert "Chrome" in text

    def test_recall_text_empty(self, memory):
        assert memory.recall_text("什么都没有") == ""

    def test_recall_limit(self, memory):
        for i in range(5):
            memory.remember(f"合同备份第{i}号")
        assert len(memory.recall("合同", limit=2)) <= 2

    def test_recall_kind_filter(self, memory):
        memory.remember("合同的偏好", kind=MemoryKind.FACT)
        memory.remember("合同的经历", kind=MemoryKind.EPISODE)
        hits = memory.recall("合同", kind=MemoryKind.FACT)
        assert [it.kind for it in hits] == [MemoryKind.FACT]

    def test_recent_and_count(self, memory):
        memory.remember("第一条")
        memory.remember("第二条")
        assert memory.recent(1)[0].text == "第二条"
        assert memory.count() == 2
        assert memory.count(MemoryKind.FACT) == 2

    def test_forget(self, memory):
        item_id = memory.remember("会被忘掉")
        assert memory.forget(item_id) is True
        assert memory.forget(item_id) is False
        assert memory.count() == 0

    def test_forget_all(self, memory):
        memory.remember("一")
        memory.remember("二", kind=MemoryKind.EPISODE)
        assert memory.forget_all(MemoryKind.EPISODE) == 1
        assert memory.forget_all() == 1
        assert memory.forget_all() == 0

    def test_get_unknown(self, memory):
        assert memory.get("不存在") is None


class TestHybridEpisode:

    def test_record_episode(self, memory):
        item_id = memory.record_episode("找合同", "找到 3 个文件", {"pattern": "*合同*"})
        item = memory.get(item_id)
        assert item.text == "上次让你「找合同」，找到 3 个文件"
        assert item.kind is MemoryKind.EPISODE
        assert item.metadata["action"] == "找合同"
        assert item.metadata["success"] is True
        assert item.metadata["params"] == {"pattern": "*合同*"}
        assert memory.stats()["episodes"] == 1

    def test_record_episode_skips_failures(self, memory):
        assert memory.record_episode("找合同", "没找到", success=False) is None
        assert memory.count() == 0

    def test_record_episode_disabled(self, memory):
        memory._episode_enabled = False
        assert memory.record_episode("找合同", "找到 3 个文件") is None

    def test_record_episode_requires_text(self, memory):
        assert memory.record_episode("", "找到 3 个文件") is None
        assert memory.record_episode("找合同", "") is None

    def test_record_episode_params_must_be_dict(self, memory):
        item_id = memory.record_episode("找合同", "找到 3 个文件", params="不是字典")
        assert memory.get(item_id).metadata["params"] == {}

    def test_record_episode_truncates_action(self, memory):
        item_id = memory.record_episode("动" * 200, "结果")
        assert len(memory.get(item_id).metadata["action"]) == 64


class TestHybridHint:

    def test_hint_from_fact(self, memory):
        memory.remember("喜欢用 Chrome 浏览器", kind=MemoryKind.FACT)
        hint = memory.hint_for("帮我打开浏览器")
        assert hint.startswith("你之前说过：")
        assert "Chrome" in hint
        assert memory.stats()["hint_hits"] == 1

    def test_hint_ignores_episode(self, memory):
        memory.remember("上次找过合同", kind=MemoryKind.EPISODE)
        assert memory.hint_for("找合同") == ""

    def test_default_threshold_means_top_two(self):
        """门槛落在 RRF 尺度上：某一路排进前 2 名才够，第 3 名不够"""
        assert DEFAULT_HINT_MIN_SCORE == pytest.approx(1 / (RRF_K + 2))
        assert 1 / (RRF_K + 1) >= DEFAULT_HINT_MIN_SCORE
        assert 1 / (RRF_K + 2) >= DEFAULT_HINT_MIN_SCORE
        assert 1 / (RRF_K + 3) < DEFAULT_HINT_MIN_SCORE

    def test_hint_threshold_filters_hits(self, tmp_path):
        """有命中但分数不过门槛 → 不提示（宁可不给，也不能带错路由）"""
        store = RecallStore(str(tmp_path / "strict.db"), embedder=HashingEmbedder())
        mem = HybridMemory(store, hint_min_score=0.5)
        try:
            mem.remember("喜欢用 Chrome 浏览器")
            assert mem.hint_for("浏览器") == ""
            assert mem.stats()["hint_hits"] == 0
        finally:
            mem.close()

    def test_hint_no_match(self, memory):
        memory.remember("喜欢用 Chrome 浏览器")
        assert memory.hint_for("的") == ""

    def test_hint_disabled(self, memory):
        memory.remember("喜欢用 Chrome")
        memory._hint_enabled = False
        assert memory.hint_for("浏览器") == ""

    def test_hint_empty_text(self, memory):
        memory.remember("喜欢用 Chrome")
        assert memory.hint_for("") == ""
        assert memory.hint_for(None) == ""

    def test_hint_no_memory(self, memory):
        assert memory.hint_for("浏览器") == ""


class TestHybridRobustness:

    def test_stats_merges_store_and_own_counters(self, memory):
        memory.remember("一条")
        stats = memory.stats()
        for key in (
            "count", "by_kind", "vector_backend", "dim", "path", "pruned", "degraded",
            "provider", "remembered", "rejected_sensitive", "recall_hits",
            "recall_misses", "episodes", "hint_hits",
        ):
            assert key in stats, f"stats 缺少 {key}"
        assert stats["provider"] == "hybrid"
        assert stats["count"] == 1

    def test_stats_with_broken_store(self, tmp_path, monkeypatch):
        store = RecallStore(str(tmp_path / "bs.db"))
        mem = HybridMemory(store)
        monkeypatch.setattr(store, "stats", _raiser)
        try:
            assert mem.stats() == {}
        finally:
            mem.close()

    def test_all_methods_never_raise_when_store_explodes(self, tmp_path, monkeypatch):
        """store 全面失灵时，seam 的每个方法都必须给出空值"""
        store = RecallStore(str(tmp_path / "boom2.db"))
        mem = HybridMemory(store)
        for name in ("add", "search", "touch", "remove", "remove_all", "recent", "get", "count"):
            monkeypatch.setattr(store, name, _raiser)

        assert mem.remember("写不进去") is None
        assert mem.recall("查询") == []
        assert mem.recall_text("查询") == ""
        assert mem.recent() == []
        assert mem.get("x") is None
        assert mem.forget("x") is False
        assert mem.forget_all() == 0
        assert mem.count() == 0
        assert mem.record_episode("动作", "摘要") is None
        assert mem.hint_for("查询") == ""
        mem.close()

    def test_close_is_idempotent_and_keeps_embedder(self, tmp_path):
        emb = HashingEmbedder()
        store = RecallStore(str(tmp_path / "close.db"), embedder=emb)
        mem = HybridMemory(store)
        mem.remember("一条")
        mem.close()
        mem.close()
        assert store.ready is False
        assert emb.ready() is True, "嵌入器由装配层持有，记忆关闭不该顺手卸掉它"

    def test_repr(self, memory):
        assert "HybridMemory" in repr(memory)


# ══════════════════════════════════════════════════
#  与 seam 契约的一致性
# ══════════════════════════════════════════════════


class TestSeamContract:

    def test_hybrid_memory_implements_memory_service(self, memory):
        from agent.seams.memory import MemoryService

        assert isinstance(memory, MemoryService)
        assert memory.capability_name == "memory"

    def test_embedders_implement_embedder_service(self):
        assert isinstance(HashingEmbedder(), EmbedderService)
        assert isinstance(SentenceTransformerEmbedder(), EmbedderService)

    def test_recall_only_returns_positive_scores(self, memory):
        memory.remember("喜欢用 Chrome")
        hits = memory.recall("Chrome")
        assert hits
        for item in hits:
            assert item.score > 0

    def test_recall_is_descending(self, memory):
        for i in range(5):
            memory.remember(f"合同备份第{i}号文件")
        scores = [it.score for it in memory.recall("合同", limit=5)]
        assert scores == sorted(scores, reverse=True)

    def test_construction_is_cheap(self):
        """seam 硬约定：构造不得加载模型/建索引"""
        started = time.perf_counter()
        emb = SentenceTransformerEmbedder()
        elapsed = time.perf_counter() - started
        assert emb.ready() is False
        assert elapsed < 0.5, f"构造耗时 {elapsed:.3f}s，不符合廉价约定"
