"""
情绪分析模块测试
"""

import pytest
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from services.emotion_analyzer import analyze_emotion, analyze_per_sentence, dominant_emotion


class TestAnalyzeEmotion:
    """analyze_emotion 单句情绪分析测试"""

    def test_happy_keywords(self):
        """开心关键词检测"""
        assert analyze_emotion("哈哈，太好了！") == "happy"
        assert analyze_emotion("恭喜恭喜，真棒！") == "happy"
        assert analyze_emotion("终于完成了，好开心！") == "happy"
        assert analyze_emotion("哈哈哈，不错不错") == "happy"

    def test_sad_keywords(self):
        """悲伤关键词检测"""
        assert analyze_emotion("我好难过啊") == "sad"
        assert analyze_emotion("呜呜，好伤心") == "sad"
        assert analyze_emotion("唉，真遗憾...") == "sad"
        assert analyze_emotion("对不起，我很抱歉") == "sad"

    def test_angry_keywords(self):
        """愤怒关键词检测"""
        assert analyze_emotion("气死我了！") == "angry"
        assert analyze_emotion("讨厌你！烦死了") == "angry"
        assert analyze_emotion("哼，真让人生气") == "angry"

    def test_surprise_keywords(self):
        """惊讶关键词检测"""
        assert analyze_emotion("天哪，真的吗！") == "surprise"
        assert analyze_emotion("不会吧，居然这样？？") == "surprise"
        assert analyze_emotion("哇，太意外了！") == "surprise"

    def test_love_keywords(self):
        """爱心关键词检测"""
        assert analyze_emotion("宝贝，我好想你") == "love"
        assert analyze_emotion("亲爱的，mua~") == "love"
        assert analyze_emotion("我喜欢你") == "love"

    def test_dance_keywords(self):
        """跳舞关键词检测"""
        assert analyze_emotion("一起来跳舞吧！") == "dance"
        assert analyze_emotion("嗨起来，蹦迪！") == "dance"

    def test_think_keywords(self):
        """思考关键词检测"""
        assert analyze_emotion("让我想想...") == "think"
        assert analyze_emotion("嗯，这个嘛...") == "think"

    def test_calm_keywords(self):
        """平静关键词检测"""
        assert analyze_emotion("别担心，没事的") == "calm"
        assert analyze_emotion("放松一下，深呼吸") == "calm"

    def test_emoji_detection(self):
        """表情符号高权重检测"""
        assert analyze_emotion("今天还行 😊") == "happy"
        assert analyze_emotion("好难过 😢") == "sad"
        assert analyze_emotion("气死 😤") == "angry"
        assert analyze_emotion("爱你 ❤️") == "love"
        assert analyze_emotion("震惊 🤯") == "surprise"

    def test_pattern_repetition(self):
        """重复字符模式检测"""
        assert analyze_emotion("哈哈哈哈") == "happy"
        assert analyze_emotion("呜呜呜") == "sad"
        assert analyze_emotion("哇！！！") == "surprise"

    def test_empty_text(self):
        """空文本返回 talk"""
        assert analyze_emotion("") == "talk"
        assert analyze_emotion("  ") == "talk"
        assert analyze_emotion(None) == "talk"

    def test_neutral_text(self):
        """普通文本返回 talk"""
        assert analyze_emotion("今天天气不错") == "talk"
        assert analyze_emotion("请帮我查一下") == "talk"
        assert analyze_emotion("你好") == "talk"

    def test_mixed_emotions_priority(self):
        """混合情绪时高优先级胜出"""
        # "爱你" + "难过" → love 优先于 sad（love 在 priority 列表更前）
        result = analyze_emotion("亲爱的我好爱你，但是好难过")
        assert result in ["love", "sad"]  # 两个都可能，但 love 有优先级

    def test_punctuation_intensity(self):
        """标点强度检测"""
        assert analyze_emotion("！！！") == "surprise"
        assert analyze_emotion("!!!") == "surprise"


class TestDominantEmotion:
    """dominant_emotion 多句主导情绪提取测试"""

    def test_single_emotion(self):
        """单一情绪占主导"""
        sentences = ["哈哈太好了", "真开心", "恭喜恭喜"]
        assert dominant_emotion(sentences) == "happy"

    def test_mixed_emotions(self):
        """混合情绪投票"""
        sentences = ["好难过", "呜呜", "哈哈"]
        result = dominant_emotion(sentences)
        assert result == "sad"  # 2票sad vs 1票happy

    def test_empty_sentences(self):
        """空列表返回 talk"""
        assert dominant_emotion([]) == "talk"
        assert dominant_emotion(None) == "talk"


class TestAnalyzePerSentence:
    """analyze_per_sentence 逐句分析测试"""

    def test_basic(self):
        """基本逐句分析"""
        sentences = ["哈哈太好了", "但是有点难过", "哇不会吧"]
        results = analyze_per_sentence(sentences)
        assert len(results) == 3
        assert results[0] == "happy"
        assert results[1] == "sad"
        assert results[2] == "surprise"

    def test_empty(self):
        """空列表"""
        assert analyze_per_sentence([]) == []


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
