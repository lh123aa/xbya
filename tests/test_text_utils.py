"""
文本工具测试：中文分句
"""

import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from core.text_utils import split_sentences


class TestSplitSentences:
    def test_standard_sentences(self):
        """标准中文标点切句：。！？；"""
        text = "今天天气真好。我们出去玩吧！你准备好了吗？"
        sentences = split_sentences(text)
        assert sentences == ["今天天气真好。", "我们出去玩吧！", "你准备好了吗？"]

    def test_english_punctuation(self):
        """英文标点也支持：.!?（分号;不切分）"""
        text = "Hello world. How are you? Fine; thanks!"
        sentences = split_sentences(text)
        assert sentences == ["Hello world.", "How are you?", "Fine; thanks!"]

    def test_no_punctuation(self):
        """无标点长文本：整体作为一句"""
        text = "今天天气不错我们出去走走吧我的意思是不想在家待着"
        sentences = split_sentences(text)
        assert sentences == [text]

    def test_short_segment_merge(self):
        """短段落前后无标点时并入相邻句"""
        text = "喵。小忆真棒"
        sentences = split_sentences(text)
        assert sentences == ["喵。", "小忆真棒"]

    def test_mixed_with_newlines(self):
        """换行也切分，空行忽略"""
        text = "第一句\n\n第二句！第三句"
        sentences = split_sentences(text)
        assert sentences == ["第一句", "第二句！", "第三句"]

    def test_whitespace_only(self):
        """纯空白返回空列表"""
        assert split_sentences("   \n  ") == []

    def test_empty(self):
        """空字符串返回空列表"""
        assert split_sentences("") == []
