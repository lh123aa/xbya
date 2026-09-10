"""长期记忆存储与混合检索（Service Provider，核心）

**SQLite + FTS5 + float32 向量列，全部 stdlib**（`sqlite3` / `array` / `json` / `math` 级别的
零依赖，不引入任何新包）。可选的 `sqlite_vec` 扩展只影响"向量那一路用谁算"，
装不上也照样跑。

## 为什么是 SQLite

记忆要跨会话存活，而本项目的部署形态是"单机单用户的桌面宠物"：
自带 FTS5（全文检索）、单文件、零运维、零新依赖。向量部分自己存
`array('f').tobytes()` 的 BLOB，2000 条以内的暴力余弦是毫秒级，
换来的是"任何机器上都能跑"。

## 中文检索的坑（本模块存在的首要理由）

SQLite FTS5 默认的 `unicode61` 分词器把**连续汉字当成一个词元**：
「桌面上的合同」整串成为单一 token，于是检索「合同」**永远命中不了**
（实测：`MATCH '合同'` 返回 0 行，`MATCH '桌面上的合同'` 才返回 1 行）。

解法是**自己产出 token 串再存进 FTS5 列**（`tokenize()`）：
ASCII 词元小写保留，CJK 连续片段切成 单字 + 相邻双字。
检索时同样对 query 做 tokenize，再用 `OR` 连成 `MATCH` 表达式
（每个 token 加双引号并按 FTS5 规则转义内部引号）。

## 检索融合

两路候选（FTS5 bm25 / 向量余弦）各取 `max(limit*4, 20)` 条，用 **RRF**
（Reciprocal Rank Fusion，`score += 1/(k + rank)`，k=60）融合：
两路都命中的条目自然排前面。`kind` 过滤在**两路各自的 SQL 里**完成 ——
留到融合后过滤会让候选名额被别类条目挤占。
"""

import json
import logging
import os
import re
import sqlite3
import threading
import time
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, TypeVar

from agent.seams.embedder import (
    DEFAULT_DIM,
    EmbedderService,
    cosine,
    pack,
    unpack,
)
from agent.seams.memory import (
    DEFAULT_RECALL_LIMIT,
    MemoryItem,
    MemoryKind,
    coerce_kind,
    new_item_id,
    truncate_text,
)

logger = logging.getLogger(__name__)

#: 默认数据库位置（相对项目根目录；与 tracker_store 的约定一致）
DEFAULT_DB_PATH = os.path.join("data", "agent_memory.db")

#: 默认容量上限（超出后按热度淘汰）
DEFAULT_MAX_ITEMS = 2000

#: 向量检索的最低余弦相似度
#:
#: 不设门槛时"任何输入都能召回一堆无关记忆"：余弦总有正值，
#: 于是检索永远"有结果"，长期记忆反而变成噪声源。
DEFAULT_MIN_SIMILARITY = 0.25

#: RRF 平滑常数（原论文惯例值：越大越削弱头部名次的优势）
RRF_K = 60

#: 向量后端配置取值
DEFAULT_VECTOR_BACKEND = "auto"
_VECTOR_BACKENDS = ("auto", "python", "sqlite_vec")

#: 内存库标识（退化模式与测试都用它）
MEMORY_DB = ":memory:"

#: 向量索引表名（sqlite-vec 的 vec0 虚表）
VEC_TABLE = "memory_vec"

#: SQLite 等锁超时（秒）
SQLITE_TIMEOUT = 5.0

#: 候选池大小 = max(limit * 倍数, 下限)：RRF 需要"宽进严出"，候选太少融合无从发挥
_CANDIDATE_FACTOR = 4
_CANDIDATE_MIN = 20

#: 按类别做向量检索时的过取倍数 —— vec0 的 `k` 在 JOIN 条件之前生效，
#: 不过取就会让候选名额被别类条目挤占
_KIND_OVERFETCH = 4

#: 禁止写入的用户目录名（与 tracker_store / 安全白名单同源）
_FORBIDDEN_DIR_NAMES = ("desktop", "documents", "downloads", "pictures")

#: 主表 / FTS 表的结构校验清单（错结构无法靠 IF NOT EXISTS 修正，只能识别后降级）
_MEMORY_TABLE = "memory_items"
_FTS_TABLE = "memory_fts"
_MEMORY_COLUMNS = (
    "item_id", "kind", "text", "tokens", "metadata",
    "created_at", "last_used_at", "use_count", "embedding",
)
_FTS_COLUMNS = ("tokens", "item_id")

#: 读取条目时统一使用的列（顺序与 `_row_to_item` 一一对应）
_ITEM_COLUMNS = "item_id, kind, text, metadata, created_at, last_used_at, use_count"

_CREATE_MEMORY_SQL = f"""
CREATE TABLE IF NOT EXISTS {_MEMORY_TABLE} (
    rowid_key     INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id       TEXT UNIQUE NOT NULL,
    kind          TEXT NOT NULL,
    text          TEXT NOT NULL,
    tokens        TEXT NOT NULL,
    metadata      TEXT NOT NULL,
    created_at    REAL NOT NULL,
    last_used_at  REAL NOT NULL,
    use_count     INTEGER NOT NULL,
    embedding     BLOB
)
"""

_CREATE_FTS_SQL = (
    f"CREATE VIRTUAL TABLE IF NOT EXISTS {_FTS_TABLE} "
    f"USING fts5(tokens, item_id UNINDEXED)"
)

_INSERT_MEMORY_SQL = f"""
INSERT INTO {_MEMORY_TABLE}
    (item_id, kind, text, tokens, metadata, created_at, last_used_at, use_count, embedding)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

#: token 来源：ASCII 词元 或 CJK 连续片段（标点/空白/emoji 天然被排除）
_TOKEN_SOURCE_RE = re.compile(r"[A-Za-z0-9]+|[\u3400-\u4dbf\u4e00-\u9fff]+")

F = TypeVar("F", bound=Callable[..., Any])


# ══════════════════════════════════════════════════
#  共用工具
# ══════════════════════════════════════════════════


def never_raises(make_default: Callable[[], Any]) -> Callable[[F], F]:
    """把「永不抛异常」约定收敛成一处装饰器（本包内两个 Provider 共用）

    `seams/memory.py` 规定所有记忆方法不得抛异常。若每个公开方法各写一遍
    try/except，这条约定会被重复十几遍，失败路径也要逐个验证；
    集中到装饰器后，"失败时返回什么"变得可审计，且只有一条失败路径需要覆盖。

    Args:
        make_default: 失败时的返回值工厂；每次失败都新建，
            避免把同一个可变对象（如列表）返给多个调用方共享

    Returns:
        装饰器
    """
    def decorate(func: F) -> F:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return func(*args, **kwargs)
            except Exception as e:
                logger.warning("[memory] %s 调用失败（已兜底）: %s", func.__name__, e)
                return make_default()

        return wrapper  # type: ignore[return-value]

    return decorate


def tokenize(text: Any) -> str:
    """把文本切成 FTS5 用的 token 串（空格分隔）

    规则：
    - ASCII 词元小写保留（`Chrome` → `chrome`，`PDF` → `pdf`）
    - CJK 连续片段切成 **单字 + 相邻双字**（单字保召回，双字保精度）
    - 其余字符（标点、空白、emoji）由正则天然排除，无需额外过滤

    例：`"桌面上的合同.pdf"` → `"桌 面 上 的 合 同 桌面 面上 上的 的合 合同 pdf"`

    Args:
        text: 任意输入；None 视为空，非 str 用 `str()` 收敛

    Returns:
        空格分隔的 token 串；无可用 token（空串 / 纯标点）时返回空串
    """
    if text is None:
        return ""
    tokens: List[str] = []
    for match in _TOKEN_SOURCE_RE.finditer(str(text)):
        piece = match.group(0)
        if piece[0].isascii():
            tokens.append(piece.lower())
            continue
        tokens.extend(piece)
        tokens.extend(piece[i:i + 2] for i in range(len(piece) - 1))
    return " ".join(tokens)


def fts_match_expression(tokens: str) -> str:
    """token 串 → FTS5 的 MATCH 表达式

    每个 token 加双引号（避免 `AND`/`OR`/`*`/`-` 之类被当成语法），
    并用 `OR` 连接 —— 中文单字 token 很泛，靠 bm25 排序而不是靠 AND 收窄，
    否则「合同 备份」这种多词查询会因为"没有一条同时命中全部字"而全军覆没。

    FTS5 的 MATCH 语法里 `"` 是**短语引号**，token 内部若含 `"` 会让 SQL
    直接语法报错（实测：`MATCH '"a"b"'` → `unterminated string`），
    故按 FTS5 规则把 `"` 双写转义。本模块的 `tokenize` 不会产出引号，
    这层转义是防"调用方自己拼 token 串"的。

    Args:
        tokens: 空格分隔的 token 串

    Returns:
        MATCH 表达式；无 token 时返回空串（调用方据此跳过词法检索）
    """
    parts = [t for t in str(tokens or "").split(" ") if t]
    if not parts:
        return ""
    return " OR ".join('"' + p.replace('"', '""') + '"' for p in parts)


def rrf_fuse(
    rankings: Sequence[Sequence[str]],
    k: int = RRF_K,
) -> List[Tuple[str, float]]:
    """RRF（Reciprocal Rank Fusion）融合多路检索结果

    只用**名次**、不用原始分数：bm25 越小越好、余弦越大越好，量纲不可比，
    硬加权要么靠调参要么随语料漂移。RRF 把各路的"第几名"折成同一尺度
    （`1/(k + rank)`），于是"两路都命中"必然胜过"只有一路捧到第一"，
    这正是混合检索要的稳健性。

    Args:
        rankings: 每路的有序 item_id 列表（越靠前越相关）
        k: 平滑常数（默认 60）

    Returns:
        `(item_id, score)` 列表，按 score 降序；同分按 item_id 升序保证结果确定
    """
    scores: Dict[str, float] = {}
    for ranking in rankings:
        for rank, item_id in enumerate(ranking, start=1):
            scores[item_id] = scores.get(item_id, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))


def _is_inside_forbidden_dir(path: Path) -> bool:
    """路径是否落在用户桌面/文档/下载/图片下（与 tracker_store 同一判据）"""
    parts = [p.lower() for p in path.parts]
    return any(name in parts for name in _FORBIDDEN_DIR_NAMES)


def _import_sqlite_vec() -> Optional[Any]:
    """尝试导入 sqlite-vec；未安装返回 None（不抛异常）"""
    try:
        import sqlite_vec
    except Exception as e:                    # ImportError / 扩展二进制损坏
        logger.info("[memory] sqlite-vec 不可用（%s）", e)
        return None
    return sqlite_vec


# ══════════════════════════════════════════════════
#  RecallStore
# ══════════════════════════════════════════════════


class RecallStore:
    """SQLite 记忆存储 + FTS5/向量混合检索

    用法：
        store = RecallStore("data/agent_memory.db", embedder=HashingEmbedder())
        store.add(MemoryItem(text="喜欢用 Chrome"))
        store.search("浏览器偏好")

    线程模型：单连接 + `check_same_thread=False` + `RLock` 串行化。
    记忆读写在工具执行线程里发生，连接不能绑死线程；而 SQLite 连接本身
    不是线程安全的，因此**读写都在同一把锁内**（游标会被并发复用）。
    锁是可重入的：`add()` 内部会调用 `prune()`。

    **永不抛异常**：所有公开方法都被 `never_raises` 包住，
    失败时返回空值并记日志 —— 记忆是增强能力，坏了也不能拖垮主流程。
    """

    def __init__(
        self,
        path: Optional[str] = None,
        embedder: Optional[EmbedderService] = None,
        dim: int = DEFAULT_DIM,
        max_items: int = DEFAULT_MAX_ITEMS,
        vector_backend: str = DEFAULT_VECTOR_BACKEND,
        min_similarity: float = DEFAULT_MIN_SIMILARITY,
        rrf_k: int = RRF_K,
    ) -> None:
        """
        Args:
            path: 数据库路径；None 用 DEFAULT_DB_PATH。命中用户目录白名单
                或无法打开时退化为纯内存模式
            embedder: 嵌入器；None 表示不做向量检索（只走词法）
            dim: 向量维度（无嵌入器时使用；有嵌入器时以嵌入器为准）
            max_items: 容量上限，超出后按热度淘汰
            vector_backend: `auto` | `python` | `sqlite_vec`
            min_similarity: 向量检索的最低余弦相似度
            rrf_k: RRF 平滑常数
        """
        self._embedder = embedder
        self._dim = self._resolve_dim(embedder, dim)
        self._max_items = max(1, int(max_items))
        self._min_similarity = float(min_similarity)
        self._rrf_k = max(1, int(rrf_k))

        # 非白名单取值一律当 auto，避免一个错配置让记忆整体不可用
        want = str(vector_backend or "").strip().lower()
        self._vector_backend_config = want if want in _VECTOR_BACKENDS else DEFAULT_VECTOR_BACKEND

        self._con: Optional[sqlite3.Connection] = None
        self._lock = threading.RLock()
        self._ok = False
        self._vec_ready = False
        self._degraded = False
        self._degraded_reason = ""
        self._pruned = 0
        self._writes = 0

        # 安全边界：拒绝把记忆库写进用户桌面/文档/下载/图片（与 R1 白名单策略一致）
        raw = Path(path or DEFAULT_DB_PATH).expanduser()
        self._requested_path = str(raw)
        self._path = self._requested_path
        if _is_inside_forbidden_dir(raw):
            logger.warning("[memory] 拒绝把记忆库写入用户目录（%s），退化为纯内存模式", raw)
            self._degraded = True
            self._degraded_reason = f"路径落在受保护的用户目录内: {raw}"
            self._path = MEMORY_DB

        self._init_storage()

    # ══════════════════════════════════════════════
    #  初始化与降级
    # ══════════════════════════════════════════════

    @staticmethod
    def _resolve_dim(embedder: Optional[EmbedderService], fallback: int) -> int:
        """确定向量维度：嵌入器可用则用它的，否则用配置值

        维度必须在建索引表之前定死 —— `vec0` 的列宽是 `float[N]`，
        中途变维度会让索引与已存向量脱节。
        """
        if embedder is not None:
            try:
                return max(1, int(embedder.dim))
            except Exception as e:
                logger.warning(
                    "[memory] 嵌入器维度不可用（%s），改用配置维度 %s", e, fallback
                )
        return max(1, int(fallback))

    def _init_storage(self) -> None:
        """建立连接与表结构；任何失败都退化为纯内存模式（不阻塞启动）"""
        try:
            self._open_and_prepare(self._path)
        except (sqlite3.Error, OSError) as e:
            self._fallback_to_memory(f"{type(e).__name__}: {e}")

    def _open_and_prepare(self, db_path: str) -> None:
        """打开数据库、配 PRAGMA、载扩展、建表并校验结构

        本方法**允许抛异常**（连接失败 / 文件损坏 / 结构异常），由
        `_init_storage` 统一兜底成内存模式 —— 把"失败"集中在一处处理，
        比每个步骤各自容错更容易审计。

        Args:
            db_path: 数据库路径或 `:memory:`
        """
        if db_path != MEMORY_DB:
            # 父目录不存在就建：首次运行时 data/ 往往还不存在
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(
            db_path, check_same_thread=False, timeout=SQLITE_TIMEOUT
        )
        self._configure_pragmas()
        self._try_load_vector_extension()
        self._create_schema()
        self._verify_schema()
        self._ok = True

    def _configure_pragmas(self) -> None:
        """WAL + 同步级别 NORMAL

        WAL 让读写不互相阻塞（记忆检索可能在播报的同时发生）；
        NORMAL 在 WAL 下仍是崩溃安全的，且省掉每次提交的 fsync。
        在 `:memory:` 上 WAL 不生效，但 SQLite 只是把 journal_mode 报成
        `memory` 而不是报错，因此这里无需分支容错。
        """
        self._con.execute("PRAGMA journal_mode=WAL")
        self._con.execute("PRAGMA synchronous=NORMAL")

    def _try_load_vector_extension(self) -> None:
        """按配置尝试加载 sqlite-vec 扩展

        扩展加载失败只影响"向量那一路用谁算"，不影响存储本体，
        因此不抛异常、也不让整个存储降级。
        """
        self._vec_ready = False
        if self._vector_backend_config == "python":
            return

        module = _import_sqlite_vec()
        if module is None:
            self._note_backend_fallback("未安装 sqlite-vec")
            return

        try:
            self._con.enable_load_extension(True)
            module.load(self._con)
            self._con.enable_load_extension(False)
        except Exception as e:
            # 失败形态很多（缺 DLL / 编译时禁用了扩展加载 / 版本不匹配），
            # 故不限于 sqlite3.Error —— 任何异常都只意味着"这一路用不上"
            self._note_backend_fallback(f"扩展加载失败: {e}")
            return

        self._vec_ready = True
        logger.info("[memory] 向量后端就绪: sqlite_vec (dim=%d)", self._dim)

    def _note_backend_fallback(self, reason: str) -> None:
        """记录向量后端降级

        显式配置成 `sqlite_vec` 才算"没做到"，值得告警并计入 degraded；
        `auto` 下退回 python 本就是设计内的正常兜底，只记 info。
        """
        if self._vector_backend_config == "sqlite_vec":
            logger.warning("[memory] sqlite_vec 后端不可用（%s），已降级为 python", reason)
            self._degraded = True
            self._degraded_reason = self._degraded_reason or reason
            return
        logger.info("[memory] 向量检索使用 python 后端（%s）", reason)

    def _create_schema(self) -> None:
        """建表（幂等）：主表 + FTS5 表 + 可选的 vec0 索引表"""
        self._con.execute(_CREATE_MEMORY_SQL)
        self._con.execute(_CREATE_FTS_SQL)
        if self._vec_ready:
            self._ensure_vec_table()
        self._con.commit()

    def _vec_table_sql(self) -> str:
        """vec0 建表语句

        实测（sqlite-vec 0.1.9 / sqlite 3.45.3）：支持 `float[N]` 与
        `distance_metric=cosine`，`item_id TEXT PRIMARY KEY` 可作主键列。
        """
        return (
            f"CREATE VIRTUAL TABLE IF NOT EXISTS {VEC_TABLE} USING vec0("
            f"item_id TEXT PRIMARY KEY, embedding float[{self._dim}] "
            f"distance_metric=cosine)"
        )

    def _ensure_vec_table(self) -> None:
        """确保 vec0 索引表存在且维度正确

        维度不一致 = 换了嵌入器，旧向量对新维度没有意义，直接重建空索引
        （不清 `memory_items`：正文与元数据仍然有效，只是暂时没有向量）。
        """
        row = self._con.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
            (VEC_TABLE,),
        ).fetchone()
        if row is not None:
            if f"float[{self._dim}]" in (row[0] or ""):
                return
            logger.warning("[memory] 向量索引维度与当前维度（%d）不一致，重建索引", self._dim)
            self._con.execute(f"DROP TABLE IF EXISTS {VEC_TABLE}")
        self._con.execute(self._vec_table_sql())

    def _verify_schema(self) -> None:
        """校验表结构

        `CREATE TABLE IF NOT EXISTS` 不会修正**已存在的错误结构**
        （比如别的程序占用同名文件、或旧版本遗留的表），
        因此必须显式校验，否则后续每条 SQL 都会失败且原因难查。

        Raises:
            sqlite3.DatabaseError: 缺列（由 `_init_storage` 兜底为内存模式）
        """
        for table, required in (
            (_MEMORY_TABLE, _MEMORY_COLUMNS),
            (_FTS_TABLE, _FTS_COLUMNS),
        ):
            rows = self._con.execute(f"PRAGMA table_info({table})").fetchall()
            existing = {r[1] for r in rows}
            missing = [c for c in required if c not in existing]
            if missing:
                raise sqlite3.DatabaseError(
                    f"{table} 表结构异常，缺少列: {', '.join(missing)}"
                )

    def _fallback_to_memory(self, reason: str) -> None:
        """退化到纯内存模式：本次运行不落盘，但记忆功能仍然可用

        不阻塞启动是硬要求 —— 一个损坏的记忆库不该让桌宠起不来。
        """
        logger.warning("[memory] 记忆库不可用（%s），退化为纯内存模式", reason)
        self._degraded = True
        self._degraded_reason = reason
        self._close_connection()
        self._path = MEMORY_DB
        try:
            self._open_and_prepare(MEMORY_DB)
        except (sqlite3.Error, OSError) as e:
            logger.error("[memory] 纯内存模式也初始化失败，本次记忆功能不可用: %s", e)
            self._ok = False

    def _close_connection(self) -> None:
        """关闭并丢弃当前连接（可能不存在）"""
        if self._con is not None:
            try:
                self._con.close()
            except sqlite3.Error as e:
                logger.debug("[memory] 关闭连接失败（忽略）: %s", e)
            self._con = None

    def _available(self) -> bool:
        """存储是否可用（内存模式也算可用；彻底失败时为 False）"""
        return self._ok and self._con is not None

    # ══════════════════════════════════════════════
    #  写
    # ══════════════════════════════════════════════

    @never_raises(lambda: False)
    def add(self, item: MemoryItem, embedding: Optional[Sequence[float]] = None) -> bool:
        """写入一条记忆（同 item_id 视为覆盖更新）

        Args:
            item: 记忆条目；`item_id` 为空时自动生成并回填到该对象上
            embedding: 已算好的向量；None 则用本存储的嵌入器现算
                （`HybridMemory` 会先算好再传进来，避免同一条文本被嵌入两次）

        Returns:
            True=已写入；False=被拒绝（文本为空 / 类型不对 / 数据库不可用）
        """
        if not self._available() or not isinstance(item, MemoryItem):
            return False

        text = truncate_text(item.text)
        if not text:
            return False

        item_id = str(item.item_id or new_item_id())
        now = time.time()
        created_at = float(item.created_at or now)
        tokens = tokenize(text)
        # 显式传入的向量优先；嵌入器不可用则本条只参与词法检索
        vector = list(embedding) if embedding is not None else self._embed(text)
        blob = pack(vector) if vector else None

        with self._lock:
            with self._con:
                self._delete_items([item_id])           # 覆盖语义：先清旧记录
                self._con.execute(
                    _INSERT_MEMORY_SQL,
                    (
                        item_id,
                        coerce_kind(item.kind).value,
                        text,
                        tokens,
                        json.dumps(item.metadata or {}, ensure_ascii=False),
                        created_at,
                        float(item.last_used_at or created_at),
                        int(item.use_count or 0),
                        blob,
                    ),
                )
                self._con.execute(
                    f"INSERT INTO {_FTS_TABLE}(tokens, item_id) VALUES (?, ?)",
                    (tokens, item_id),
                )
                if self._vec_ready and blob is not None:
                    self._index_vector(item_id, vector, blob)

        item.item_id = item_id
        self._writes += 1
        self.prune()
        return True

    def _index_vector(
        self, item_id: str, vector: Sequence[float], blob: bytes
    ) -> None:
        """把向量写进 vec0 索引表

        维度不符时**只跳过索引**而不是让写入失败：换嵌入器之后旧维度向量
        仍应能存下正文（词法检索照常工作），只是暂时没有向量信号。
        """
        if len(vector) != self._dim:
            logger.warning(
                "[memory] 向量维度 %d 与索引维度 %d 不一致，本条不进向量索引",
                len(vector), self._dim,
            )
            return
        self._con.execute(
            f"INSERT INTO {VEC_TABLE}(item_id, embedding) VALUES (?, ?)",
            (item_id, blob),
        )

    def _embed(self, text: str) -> Optional[List[float]]:
        """用嵌入器把文本转成向量；不可用时返回 None"""
        if self._embedder is None or not self._embedder.ready():
            return None
        vector = self._embedder.embed_one(text)
        if not vector or not any(vector):
            return None
        return list(vector)

    @never_raises(lambda: False)
    def remove(self, item_id: str) -> bool:
        """删除一条记忆

        Args:
            item_id: 记忆 ID

        Returns:
            True=确实删掉了一条；False=不存在或删除失败
        """
        if not self._available():
            return False
        key = str(item_id or "")
        if not key:
            return False
        with self._lock:
            with self._con:
                deleted = self._delete_items([key])
        return deleted > 0

    @never_raises(lambda: 0)
    def remove_all(self, kind: Optional[Any] = None) -> int:
        """清空记忆（可按类别）

        Args:
            kind: 只清某一类；None 表示全清

        Returns:
            删除条数
        """
        if not self._available():
            return 0
        want = coerce_kind(kind) if kind is not None else None
        with self._lock:
            sql = f"SELECT item_id FROM {_MEMORY_TABLE}"
            params: List[Any] = []
            if want is not None:
                sql += " WHERE kind = ?"
                params.append(want.value)
            keys = [str(r[0]) for r in self._con.execute(sql, params).fetchall()]
            if not keys:
                return 0
            with self._con:
                self._delete_items(keys)
        return len(keys)

    def _delete_items(self, item_ids: Sequence[str]) -> int:
        """删除给定 item_id 在三张表里的记录（调用方需持锁）

        三张表必须一起删：主表删了而 FTS/索引还留着，检索会命中幽灵条目
        （JOIN 拿不到正文，融合结果里凭空少一条）。

        Args:
            item_ids: 待删除的 item_id 列表

        Returns:
            主表实际删除的行数
        """
        if not item_ids:
            return 0
        keys = [str(k) for k in item_ids]
        marks = ",".join("?" * len(keys))
        cursor = self._con.execute(
            f"DELETE FROM {_MEMORY_TABLE} WHERE item_id IN ({marks})", keys
        )
        deleted = cursor.rowcount
        self._con.execute(
            f"DELETE FROM {_FTS_TABLE} WHERE item_id IN ({marks})", keys
        )
        if self._vec_ready:
            self._con.execute(
                f"DELETE FROM {VEC_TABLE} WHERE item_id IN ({marks})", keys
            )
        return deleted

    @never_raises(lambda: 0)
    def touch(self, item_ids: Sequence[str]) -> int:
        """标记条目被检索命中：刷新 last_used_at 并累加 use_count

        Args:
            item_ids: 被命中的记忆 ID

        Returns:
            实际更新的条数（不存在的 id 不计入）
        """
        if not self._available():
            return 0
        keys = [str(i) for i in (item_ids or []) if i]
        if not keys:
            return 0

        now = time.time()
        updated = 0
        with self._lock:
            with self._con:
                for key in keys:
                    cursor = self._con.execute(
                        f"UPDATE {_MEMORY_TABLE} "
                        f"SET last_used_at = ?, use_count = use_count + 1 "
                        f"WHERE item_id = ?",
                        (now, key),
                    )
                    updated += cursor.rowcount
        return updated

    @never_raises(lambda: 0)
    def prune(self) -> int:
        """容量淘汰：超出 max_items 时删掉最不值得留的条目

        保留优先级（依次比较）：`use_count` 高 → `last_used_at` 新 → `created_at` 新。
        即"常用的留、最近用过的留、最近记下的留"，长期无人问津的旧记忆先走。

        Returns:
            本次淘汰的条数（未超限返回 0）
        """
        if not self._available():
            return 0
        with self._lock:
            overflow = self._count(None) - self._max_items
            if overflow <= 0:
                return 0
            victims = [
                str(r[0])
                for r in self._con.execute(
                    f"SELECT item_id FROM {_MEMORY_TABLE} "
                    f"ORDER BY use_count ASC, last_used_at ASC, created_at ASC "
                    f"LIMIT ?",
                    (overflow,),
                ).fetchall()
            ]
            if victims:
                with self._con:
                    self._delete_items(victims)
        self._pruned += len(victims)
        logger.info("[memory] 容量淘汰 %d 条（上限 %d）", len(victims), self._max_items)
        return len(victims)

    # ══════════════════════════════════════════════
    #  读
    # ══════════════════════════════════════════════

    @never_raises(lambda: None)
    def get(self, item_id: str) -> Optional[MemoryItem]:
        """按 ID 取一条记忆；不存在返回 None"""
        if not self._available():
            return None
        key = str(item_id or "")
        if not key:
            return None
        with self._lock:
            row = self._con.execute(
                f"SELECT {_ITEM_COLUMNS} FROM {_MEMORY_TABLE} WHERE item_id = ?",
                (key,),
            ).fetchone()
        return self._row_to_item(row) if row is not None else None

    @never_raises(list)
    def items(self, kind: Optional[Any] = None, limit: Optional[int] = None) -> List[MemoryItem]:
        """按插入顺序列出条目

        Args:
            kind: 只列某一类；None 表示不限
            limit: 最多返回条数（取**最早**写入的前 N 条）；None 表示不限

        Returns:
            条目列表（新的在后）
        """
        if not self._available():
            return []
        want = coerce_kind(kind) if kind is not None else None
        sql = f"SELECT {_ITEM_COLUMNS} FROM {_MEMORY_TABLE}"
        params: List[Any] = []
        if want is not None:
            sql += " WHERE kind = ?"
            params.append(want.value)
        sql += " ORDER BY rowid_key ASC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(max(0, int(limit)))
        with self._lock:
            rows = self._con.execute(sql, params).fetchall()
        return [self._row_to_item(r) for r in rows]

    @never_raises(list)
    def recent(self, limit: int = DEFAULT_RECALL_LIMIT, kind: Optional[Any] = None) -> List[MemoryItem]:
        """最近写入的条目（新的在前）

        与 `items()` 互补：`items()` 是"从头看"（按插入顺序、取前 N），
        本方法给"你还记得什么"这类无查询列举用（从尾部取 N）。

        Args:
            limit: 最多返回条数
            kind: 只列某一类；None 表示不限

        Returns:
            条目列表（新的在前）
        """
        if not self._available():
            return []
        want = coerce_kind(kind) if kind is not None else None
        sql = f"SELECT {_ITEM_COLUMNS} FROM {_MEMORY_TABLE}"
        params: List[Any] = []
        if want is not None:
            sql += " WHERE kind = ?"
            params.append(want.value)
        sql += " ORDER BY rowid_key DESC LIMIT ?"
        params.append(max(1, int(limit)))
        with self._lock:
            rows = self._con.execute(sql, params).fetchall()
        return [self._row_to_item(r) for r in rows]

    @never_raises(lambda: 0)
    def count(self, kind: Optional[Any] = None) -> int:
        """当前记忆条数（可按类别统计）"""
        if not self._available():
            return 0
        want = coerce_kind(kind) if kind is not None else None
        with self._lock:
            return self._count(want)

    def _count(self, kind: Optional[MemoryKind]) -> int:
        """条数统计（调用方需持锁或已确认连接可用）"""
        sql = f"SELECT COUNT(*) FROM {_MEMORY_TABLE}"
        params: List[Any] = []
        if kind is not None:
            sql += " WHERE kind = ?"
            params.append(kind.value)
        return int(self._con.execute(sql, params).fetchone()[0])

    @never_raises(dict)
    def by_kind(self) -> Dict[str, int]:
        """各类别的条数分布；查询失败返回空字典"""
        if not self._available():
            return {}
        with self._lock:
            rows = self._con.execute(
                f"SELECT kind, COUNT(*) FROM {_MEMORY_TABLE} GROUP BY kind"
            ).fetchall()
        return {coerce_kind(r[0]).value: int(r[1]) for r in rows}

    # ══════════════════════════════════════════════
    #  混合检索
    # ══════════════════════════════════════════════

    @never_raises(list)
    def search(
        self,
        query: str,
        kind: Optional[Any] = None,
        limit: int = DEFAULT_RECALL_LIMIT,
    ) -> List[MemoryItem]:
        """混合检索：FTS5 词法 + 向量余弦，用 RRF 融合

        Args:
            query: 查询文本（空串 / 纯标点 → 直接返回空列表）
            kind: 限定类别；None 表示不限。过滤在**两路各自的 SQL 里**生效
            limit: 最多返回条数

        Returns:
            按相关度降序的条目列表（`score` 为 RRF 融合分）；无命中返回空列表
        """
        if not self._available():
            return []

        text = truncate_text(query)
        # 没有任何可用 token 就无从检索：这既省一次全表向量比对，
        # 也避免"空查询把整库按相似度倒出来"这种荒唐结果
        if not tokenize(text):
            return []

        size = max(1, int(limit))
        candidates = max(size * _CANDIDATE_FACTOR, _CANDIDATE_MIN)
        want = coerce_kind(kind) if kind is not None else None

        lexical = self._run_route("词法", self._search_lexical, text, want, candidates)
        vector = self._run_route("向量", self._search_vector, text, want, candidates)

        fused = rrf_fuse([lexical, vector], k=self._rrf_k)[:size]
        if not fused:
            return []

        ids = [item_id for item_id, _ in fused]
        with self._lock:
            found = self._load_items(ids)

        results: List[MemoryItem] = []
        for item_id, score in fused:
            item = found.get(item_id)
            if item is None:
                continue               # 融合到一半被别的线程删了：跳过即可
            item.score = score
            results.append(item)
        return results

    def _run_route(
        self,
        route: str,
        func: Callable[..., List[str]],
        *args: Any,
    ) -> List[str]:
        """执行一路检索

        单路失败**只丢这一路**：换成在 `search` 外层吞异常的话，
        向量索引坏了会连带把词法结果一起丢掉 —— 那是两倍的损失。
        """
        try:
            return func(*args)
        except Exception as e:
            logger.warning("[memory] %s 检索失败，已跳过该路: %s", route, e)
            return []

    def _search_lexical(
        self, query: str, kind: Optional[MemoryKind], limit: int
    ) -> List[str]:
        """FTS5 词法检索（bm25 排序）

        bm25 越小越相关，故升序；JOIN 主表是为了在同一句 SQL 里完成
        kind 过滤（先合并再过滤会让候选被别类条目挤占）。
        """
        expression = fts_match_expression(tokenize(query))
        if not expression:
            return []

        sql = (
            f"SELECT {_FTS_TABLE}.item_id FROM {_FTS_TABLE} "
            f"JOIN {_MEMORY_TABLE} m ON m.item_id = {_FTS_TABLE}.item_id "
            f"WHERE {_FTS_TABLE} MATCH ?"
        )
        params: List[Any] = [expression]
        if kind is not None:
            sql += " AND m.kind = ?"
            params.append(kind.value)
        sql += f" ORDER BY bm25({_FTS_TABLE}) LIMIT ?"
        params.append(max(1, int(limit)))

        with self._lock:
            rows = self._con.execute(sql, params).fetchall()
        return [str(r[0]) for r in rows]

    def _search_vector(
        self, query: str, kind: Optional[MemoryKind], limit: int
    ) -> List[str]:
        """向量检索（按实际生效的后端分派）"""
        if self._embedder is None or not self._embedder.ready():
            return []
        vector = self._embedder.embed_one(query)
        # 零向量没有方向，余弦恒为 0：这一路等于没有信号
        if not vector or not any(vector):
            return []
        if self._vec_ready:
            return self._search_vector_sqlite_vec(vector, kind, limit)
        return self._search_vector_python(vector, kind, limit)

    def _search_vector_python(
        self, vector: Sequence[float], kind: Optional[MemoryKind], limit: int
    ) -> List[str]:
        """纯 Python 余弦比对（stdlib，不依赖 numpy）

        表规模上限只有 max_items（默认 2000），全表扫描是毫秒级；
        用 numpy 能快几倍，但它不是本项目的声明依赖，不能为了这点速度引入。
        """
        sql = f"SELECT item_id, embedding FROM {_MEMORY_TABLE} WHERE embedding IS NOT NULL"
        params: List[Any] = []
        if kind is not None:
            sql += " AND kind = ?"
            params.append(kind.value)

        with self._lock:
            rows = self._con.execute(sql, params).fetchall()
            dim = self._dim

        scored: List[Tuple[float, str]] = []
        for item_id, blob in rows:
            similarity = cosine(vector, unpack(blob, dim))
            if similarity >= self._min_similarity:
                scored.append((similarity, str(item_id)))
        scored.sort(key=lambda pair: (-pair[0], pair[1]))
        return [item_id for _, item_id in scored[: max(1, int(limit))]]

    def _search_vector_sqlite_vec(
        self, vector: Sequence[float], kind: Optional[MemoryKind], limit: int
    ) -> List[str]:
        """sqlite-vec 的 vec0 KNN 检索

        实测（sqlite-vec 0.1.9 / sqlite 3.45.3）结论：
        - 建表：`USING vec0(item_id TEXT PRIMARY KEY, embedding float[N] distance_metric=cosine)`
        - 查询：`WHERE embedding MATCH ? AND k = ? ORDER BY distance`（`k` 可绑定参数）
        - `distance_metric=cosine` 下 `distance == 1 - 余弦相似度`（正交为 1.0、同向为 0.0）
        - `k` 在 JOIN 出来的 kind 条件**之前**生效，故按类别检索时必须过取

        因此这里把 distance 换算回相似度，与 python 后端用**同一个门槛**，
        保证"换后端不改检索语义"。
        """
        size = max(1, int(limit))
        k = size * _KIND_OVERFETCH if kind is not None else size

        sql = (
            f"SELECT v.item_id, v.distance FROM {VEC_TABLE} v "
            f"JOIN {_MEMORY_TABLE} m ON m.item_id = v.item_id "
            f"WHERE v.embedding MATCH ? AND k = ?"
        )
        params: List[Any] = [pack(vector), k]
        if kind is not None:
            sql += " AND m.kind = ?"
            params.append(kind.value)
        sql += " ORDER BY v.distance"

        with self._lock:
            rows = self._con.execute(sql, params).fetchall()
        return [
            str(item_id)
            for item_id, distance in rows
            if (1.0 - float(distance)) >= self._min_similarity
        ]

    def _load_items(self, item_ids: Sequence[str]) -> Dict[str, MemoryItem]:
        """按 item_id 批量取回条目（调用方需持锁）"""
        if not item_ids:
            return {}
        marks = ",".join("?" * len(item_ids))
        rows = self._con.execute(
            f"SELECT {_ITEM_COLUMNS} FROM {_MEMORY_TABLE} "
            f"WHERE item_id IN ({marks})",
            [str(i) for i in item_ids],
        ).fetchall()
        return {str(r[0]): self._row_to_item(r) for r in rows}

    @staticmethod
    def _row_to_item(row: Sequence[Any]) -> MemoryItem:
        """数据库行 → MemoryItem（列顺序与 `_ITEM_COLUMNS` 一致）"""
        return MemoryItem(
            item_id=str(row[0]),
            kind=coerce_kind(row[1]),
            text=str(row[2] or ""),
            metadata=RecallStore._decode_metadata(row[3]),
            created_at=float(row[4] or 0.0),
            last_used_at=float(row[5] or 0.0),
            use_count=int(row[6] or 0),
        )

    @staticmethod
    def _decode_metadata(raw: Any) -> Dict[str, Any]:
        """metadata JSON → 字典

        单条坏数据不该毁掉整次检索：解析失败和非对象一律退回空字典。
        """
        if not raw:
            return {}
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            logger.debug("[memory] metadata JSON 损坏，退回空字典")
            return {}
        return data if isinstance(data, dict) else {}

    # ══════════════════════════════════════════════
    #  状态
    # ══════════════════════════════════════════════

    @property
    def vector_backend(self) -> str:
        """**实际生效**的向量后端（配置了 sqlite_vec 但加载失败时如实报 python）"""
        return "sqlite_vec" if self._vec_ready else "python"

    @property
    def dim(self) -> int:
        """向量维度"""
        return self._dim

    @property
    def path(self) -> str:
        """实际使用的数据库路径（退化模式为 `:memory:`）"""
        return self._path

    @property
    def embedder(self) -> Optional[EmbedderService]:
        """本存储使用的嵌入器（可能为 None）"""
        return self._embedder

    @property
    def ready(self) -> bool:
        """存储是否可用"""
        return self._available()

    @never_raises(dict)
    def stats(self) -> Dict[str, Any]:
        """状态快照（供 /status 与验收证据）"""
        return {
            "count": self.count(),
            "by_kind": self.by_kind(),
            "vector_backend": self.vector_backend,
            "vector_backend_config": self._vector_backend_config,
            "dim": self._dim,
            "path": self._path,
            "requested_path": self._requested_path,
            "pruned": self._pruned,
            "degraded": self._degraded,
            "degraded_reason": self._degraded_reason,
            "ready": self._ok,
            "max_items": self._max_items,
            "min_similarity": self._min_similarity,
            "rrf_k": self._rrf_k,
            "writes": self._writes,
            "embedder": self._embedder.describe() if self._embedder is not None else "",
        }

    @never_raises(lambda: None)
    def close(self) -> None:
        """关闭连接；可重复调用（已关闭后再调用是空操作）"""
        with self._lock:
            self._close_connection()
            self._ok = False

    def __repr__(self) -> str:
        return (
            f"<RecallStore path={self._path!r} count={self.count()} "
            f"backend={self.vector_backend} degraded={self._degraded}>"
        )
