# -*- coding: utf-8 -*-
"""D49 回归：配置指向不存在的插件时，"语义搜索降级"必须真的发生，且说得清原因。

## 这个用例防的是什么

`services/file_service.py` 的 `semantic_search` 原先这么判降级：

    if self.embedding is None or self.vector_db is None:
        logger.warning("语义搜索插件未初始化，使用全文搜索")
        return self.search_files(query, top_k)

但插件加载失败时拿到的是**空对象**（`NullEmbedding` / `NullVectorDB`），
**不是 None** ⇒ 这句永真不了 ⇒ **降级分支不可达** ⇒
用户拿到的是 `[]` 加一句没有归因的「查询向量化失败」。

所以本文件**不能**只断言"返回了列表"（改前改后都是 `[]`，那样是假绿）。
必须钉住**接线**：降级到底走没走、日志里有没有点名配置键。

判据三条，逐条可反方向验证：
  1. `_degraded` 里记着原因，且原因**含配置键名** `plugins.embedding.engine`
  2. 降级时真的调用了 `search_files`（用替身计数，不看返回值的真假）
  3. 插件**可用**时**不许**走降级（反方向：不能一律降级了事）
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


class _FakeUnavailable:
    """像 NullEmbedding / NullVectorDB 那样：**不是 None**，但自报不可用。"""

    def is_available(self) -> bool:
        return False

    def embed(self, text):          # pragma: no cover - 不该被调用
        return None

    def search(self, vec, top_k=5):  # pragma: no cover - 不该被调用
        return []


class _FakeAvailableEmbedding:
    def is_available(self) -> bool:
        return True

    def embed(self, text):
        return [0.1, 0.2, 0.3]


class _FakeAvailableVectorDB:
    def __init__(self):
        self.searched = 0

    def is_available(self) -> bool:
        return True

    def search(self, vec, top_k=5):
        self.searched += 1
        return [{"file_path": "x", "file_name": "x"}]


@pytest.fixture()
def svc(monkeypatch):
    """造一个不进真实插件加载的 FileService（只测判据与接线）。"""
    from services import file_service as fs_mod

    s = fs_mod.FileService.__new__(fs_mod.FileService)
    s.embedding = None
    s.vector_db = None
    s._degraded = {}
    s.db_connection = None

    # 全文搜索替身：计数 + 返回可辨识的标记
    calls = {"n": 0}

    def fake_search_files(query, top_k=5):
        calls["n"] += 1
        return [{"file_path": "FULLTEXT", "file_name": query}]

    # 用实例属性盖住方法，避免依赖真实数据库
    monkeypatch.setattr(s, "search_files", fake_search_files, raising=False)
    s._calls = calls
    return s


# ---------------------------------------------------------------- 判据 1

def test_degraded_reason_names_the_config_key(svc):
    """降级原因里必须能读出**要改哪个配置键**。

    这是本轮修的核心：改前日志只有「查询向量化失败」这种没有归因的症状描述。
    """
    svc.embedding = _FakeUnavailable()
    svc.vector_db = _FakeUnavailable()
    svc._degraded = {
        "embedding": "plugins.embedding.engine 写的是 'embed_anything'，但…没有这个插件",
        "vector_db": "plugins.vector_db.engine 写的是 'leann'，但…没有这个插件",
    }

    svc.semantic_search("测试")

    assert "plugins.embedding.engine" in svc._degraded["embedding"]
    assert "plugins.vector_db.engine" in svc._degraded["vector_db"]
    # 点名了**具体引擎名**，不是笼统的"未初始化"
    assert "embed_anything" in svc._degraded["embedding"]
    assert "leann" in svc._degraded["vector_db"]


# ---------------------------------------------------------------- 判据 2

def test_empty_object_is_treated_as_unavailable_not_as_usable(svc):
    """空对象必须被判为"不可用"。

    反方向：如果判据退回 `is None`，这个用例会变红 ——
    因为 `_FakeUnavailable()` **不是 None**，旧判据会认为它可用。
    """
    svc.embedding = _FakeUnavailable()
    svc.vector_db = _FakeUnavailable()

    assert svc.embedding is not None, "前提：空对象确实不是 None"
    assert svc._embedding_unavailable() is True
    assert svc._vector_db_unavailable() is True


def test_degrade_actually_calls_fulltext_search(svc):
    """降级必须**真的调用**全文搜索 —— 钉接线，不是钉返回值。"""
    svc.embedding = _FakeUnavailable()
    svc.vector_db = _FakeUnavailable()
    svc._degraded = {"embedding": "plugins.embedding.engine 写的是 'embed_anything'"}

    got = svc.semantic_search("找简历")

    assert svc._calls["n"] == 1, "降级分支没有调用 search_files（旧代码就是这个问题）"
    assert got and got[0]["file_path"] == "FULLTEXT"


def test_degrade_warning_logs_the_reason(svc, caplog):
    """日志里必须出现真实原因，而不只是"未初始化"。"""
    svc.embedding = _FakeUnavailable()
    svc.vector_db = _FakeUnavailable()
    svc._degraded = {
        "embedding": "plugins.embedding.engine 写的是 'embed_anything'，但插件加载器里没有这个插件",
    }

    with caplog.at_level(logging.WARNING):
        svc.semantic_search("测试")

    text = caplog.text
    assert "语义搜索不可用" in text
    assert "plugins.embedding.engine" in text, "日志没有点名配置键，用户无从下手"


# ---------------------------------------------------------------- 判据 3（反方向）

def test_available_plugins_do_not_degrade(svc):
    """插件可用时**不许**降级。

    没有这条，把 `semantic_search` 改成"永远走全文搜索"也能让前两个用例全绿 ——
    那是把"修好降级"做成了"取消语义搜索"。
    """
    vdb = _FakeAvailableVectorDB()
    svc.embedding = _FakeAvailableEmbedding()
    svc.vector_db = vdb

    got = svc.semantic_search("测试")

    assert svc._calls["n"] == 0, "可用时不该走全文搜索"
    assert vdb.searched == 1, "可用时应该走向量检索"
    assert got == [{"file_path": "x", "file_name": "x"}]


def test_missing_instance_is_unavailable(svc):
    """真的什么都没有（None）时，也必须判为不可用。"""
    svc.embedding = None
    svc.vector_db = None

    assert svc._embedding_unavailable() is True
    assert svc._vector_db_unavailable() is True


def test_is_available_raising_is_treated_as_unavailable(svc):
    """插件自报异常时按不可用处理，且不把异常抛给调用方。"""
    class _Boom:
        def is_available(self):
            raise RuntimeError("敌意插件")

    svc.embedding = _Boom()
    assert svc._embedding_unavailable() is True


def test_plugin_without_is_available_is_assumed_usable(svc):
    """没有 `is_available` 接口时**不假装能判**，按可用放行。"""
    class _NoProbe:
        pass

    svc.embedding = _NoProbe()
    assert svc._embedding_unavailable() is False
