"""实体追踪器测试

覆盖 agent/tracker.py：
- 文件栈/操作栈记录与容量
- 序数指代（第一个/第2个/最后）
- 集合指代（那些/这些/全部）
- 单数指代（它/那个/这个）
- 越界与空上下文
"""

import sys
from pathlib import Path

import pytest

project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from agent.tracker import (
    MAX_ACTIONS,
    MAX_FILES,
    ActionRecord,
    EntityTracker,
)


def files(n: int) -> list:
    """造 n 个文件实体"""
    return [{"name": f"file{i}.txt", "path": rf"C:\Desktop\file{i}.txt"} for i in range(1, n + 1)]


@pytest.fixture
def tracker():
    return EntityTracker()


# ══════════════════════════════════════════════════
#  记录
# ══════════════════════════════════════════════════

class TestPushFiles:
    """push_files"""

    def test_push_dict_list(self, tracker):
        """记录字典列表"""
        assert tracker.push_files(files(3)) == 3
        assert tracker.file_count() == 3

    def test_push_single_dict(self, tracker):
        """单个字典也接受"""
        assert tracker.push_files({"name": "a.txt", "path": "/a.txt"}) == 1

    def test_push_string_list(self, tracker):
        """字符串列表被规范化为实体"""
        assert tracker.push_files([r"C:\Desktop\a.txt"]) == 1
        assert tracker.files()[0]["name"] == "a.txt"

    def test_push_replaces_previous(self, tracker):
        """新结果替换旧结果（"那些"指最近一次）"""
        tracker.push_files(files(3))
        tracker.push_files(files(2))
        assert tracker.file_count() == 2
        assert tracker.files()[0]["name"] == "file1.txt"

    def test_push_empty_noop(self, tracker):
        """空输入不改动状态"""
        tracker.push_files(files(2))
        assert tracker.push_files([]) == 0
        assert tracker.push_files(None) == 0
        assert tracker.file_count() == 2

    def test_push_invalid_type(self, tracker):
        """不可识别类型返回 0"""
        assert tracker.push_files(42) == 0
        assert tracker.push_files([1, 2, 3]) == 0

    def test_capacity_limit(self, tracker):
        """超出容量被截断"""
        tracker.push_files(files(MAX_FILES + 10))
        assert tracker.file_count() == MAX_FILES

    def test_custom_capacity(self):
        """自定义容量"""
        t = EntityTracker(max_files=2)
        t.push_files(files(5))
        assert t.file_count() == 2


class TestPushAction:
    """push_action"""

    def test_push_and_read(self, tracker):
        """记录操作"""
        tracker.push_action("file_search", {"pattern": "*"}, 3)
        last = tracker.last_action()
        assert isinstance(last, ActionRecord)
        assert last.action == "file_search"
        assert last.params == {"pattern": "*"}
        assert last.result_count == 3

    def test_empty_action_ignored(self, tracker):
        """空操作名被忽略"""
        tracker.push_action("")
        assert tracker.last_action() is None

    def test_capacity_fifo(self, tracker):
        """超出容量丢弃最旧"""
        for i in range(MAX_ACTIONS + 5):
            tracker.push_action(f"a{i}")
        assert len(tracker.actions()) == MAX_ACTIONS
        assert tracker.actions()[0].action == "a5"

    def test_last_action_none_when_empty(self, tracker):
        """无记录时返回 None"""
        assert tracker.last_action() is None


# ══════════════════════════════════════════════════
#  序数指代
# ══════════════════════════════════════════════════

class TestResolveOrdinal:
    """序数指代解析"""

    @pytest.mark.parametrize("text,index", [
        ("第一个", 0), ("第1个", 0), ("第一", 0),
        ("第二个", 1), ("第2个", 1),
        ("第三个", 2), ("第 3 个", 2),
        ("第十个", 9),
    ])
    def test_ordinal_mapping(self, tracker, text, index):
        """序数词正确映射到索引"""
        tracker.push_files(files(10))
        result = tracker.resolve(text)
        assert len(result) == 1
        assert result[0]["name"] == f"file{index + 1}.txt"

    def test_last(self, tracker):
        """最后一个"""
        tracker.push_files(files(3))
        assert tracker.resolve("最后一个")[0]["name"] == "file3.txt"

    def test_first_chinese(self, tracker):
        """首 → 第一个"""
        tracker.push_files(files(3))
        assert tracker.resolve("首个")[0]["name"] == "file1.txt"

    def test_no_ordinal_returns_none(self, tracker):
        """无序号时 resolve_ordinal 返回 None"""
        tracker.push_files(files(3))
        assert tracker.resolve_ordinal("那些文件") is None

    def test_out_of_range(self, tracker):
        """越界返回空列表"""
        tracker.push_files(files(2))
        assert tracker.resolve("第五个") == []

    def test_empty_context(self, tracker):
        """无候选文件时返回空"""
        assert tracker.resolve("第一个") == []

    def test_zero_ordinal(self, tracker):
        """第0个 → 空"""
        tracker.push_files(files(3))
        assert tracker.resolve("第0个") == []

    def test_unknown_chinese_numeral(self, tracker):
        """不识别的中文数字 → 空"""
        tracker.push_files(files(3))
        assert tracker.resolve("第廿个") == []


# ══════════════════════════════════════════════════
#  集合 / 单数指代
# ══════════════════════════════════════════════════

class TestResolveReferences:
    """集合与单数指代"""

    @pytest.mark.parametrize("word", ["那些", "这些", "全部", "所有", "它们", "都"])
    def test_plural_returns_all(self, tracker, word):
        """集合指代返回全部候选"""
        tracker.push_files(files(4))
        assert len(tracker.resolve(f"把{word}删了")) == 4

    @pytest.mark.parametrize("word", ["它", "那个", "这个", "刚才那个", "上面那个"])
    def test_singular_returns_first(self, tracker, word):
        """单数指代返回最近单个"""
        tracker.push_files(files(3))
        result = tracker.resolve(f"打开{word}")
        assert len(result) == 1
        assert result[0]["name"] == "file1.txt"

    def test_unresolvable(self, tracker):
        """无法解析返回空"""
        tracker.push_files(files(3))
        assert tracker.resolve("随便什么") == []

    def test_none_and_empty(self, tracker):
        """None 与空串"""
        tracker.push_files(files(3))
        assert tracker.resolve(None) == []
        assert tracker.resolve("") == []
        assert tracker.resolve("   ") == []

    def test_ordinal_priority_over_plural(self, tracker):
        """序数优先于集合（"第一个" 不应返回全部）"""
        tracker.push_files(files(5))
        assert len(tracker.resolve("第一个文件")) == 1


class TestIsReference:
    """is_reference"""

    @pytest.mark.parametrize("text", [
        "第一个", "第2个", "那些", "这些", "它", "那个", "刚才那个", "全部",
    ])
    def test_reference_detected(self, tracker, text):
        """指代表达被识别"""
        assert tracker.is_reference(text) is True

    @pytest.mark.parametrize("text", ["报告.docx", "找一下合同", "", "你好"])
    def test_non_reference(self, tracker, text):
        """非指代表达"""
        assert tracker.is_reference(text) is False

    def test_none(self, tracker):
        """None"""
        assert tracker.is_reference(None) is False


# ══════════════════════════════════════════════════
#  清理与状态
# ══════════════════════════════════════════════════

class TestCleanup:
    """清理与查询"""

    def test_clear(self, tracker):
        """清空全部"""
        tracker.push_files(files(3))
        tracker.push_action("a")
        tracker.clear()
        assert tracker.file_count() == 0
        assert tracker.actions() == []
        assert tracker.has_context() is False

    def test_clear_files_only(self, tracker):
        """只清文件栈，保留操作栈"""
        tracker.push_files(files(3))
        tracker.push_action("a")
        tracker.clear_files()
        assert tracker.file_count() == 0
        assert len(tracker.actions()) == 1

    def test_has_context(self, tracker):
        """上下文判断"""
        assert tracker.has_context() is False
        tracker.push_files(files(1))
        assert tracker.has_context() is True

    def test_files_is_snapshot(self, tracker):
        """files 返回快照"""
        tracker.push_files(files(3))
        snap = tracker.files()
        snap.append({"name": "x"})
        assert tracker.file_count() == 3

    def test_repr(self, tracker):
        """repr 含计数"""
        tracker.push_files(files(2))
        assert "files=2" in repr(tracker)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
