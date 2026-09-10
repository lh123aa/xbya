"""
情绪分析模块
分析 LLM 回复文本的语义情绪，输出对应的动画状态名。
纯本地规则引擎，无外部依赖，延迟 < 0.1ms。

情绪映射：
  happy    — 开心、高兴、快乐、笑、庆祝
  sad      — 难过、伤心、哭、遗憾、抱歉
  angry    — 生气、愤怒、讨厌、烦
  surprise — 惊讶、震惊、哇、天哪、不会吧
  love     — 喜欢、爱、想你、宝贝、亲爱
  dance    — 跳舞、庆祝、耶、嗨起来
  think    — 思考、让我想想、嗯...
  calm     — 安慰、平静、放松、没事
  默认     — talk（普通说话）
"""

import re
from typing import Optional

# ── 情绪关键词库 ─────────────────────────────────────────
# 每个情绪有：正面词、负面词、语气词/标点模式、表情符号
_EMOTION_RULES = {
    "happy": {
        "keywords": [
            "开心", "高兴", "快乐", "太好了", "哈哈", "嘿嘿", "嘻嘻",
            "棒", "赞", "好棒", "耶", "好耶", "太棒",
            "恭喜", "祝贺", "庆祝", "好运", "幸运", "幸福", "甜蜜",
            "终于", "成功", "做到", "完成", "厉害", "牛", "666",
            "awesome", "great", "nice", "congrat", "happy", "yay",
        ],
        "patterns": [
            r"[哈哈]{2,}",       # 哈哈哈
            r"[嘿嘻嘻]{2,}",     # 嘿嘿嘿
            r"~+~+",             # 波浪线装饰
        ],
        "emoji": ["😊", "😄", "😁", "🥳", "🎉", "👍", "✨", "🌟", "💫", "🎊", "💪"],
        "weight": 1.0,
    },
    "sad": {
        "keywords": [
            "难过", "伤心", "悲伤", "遗憾", "抱歉", "对不起", "不好意思",
            "哭", "泪", "委屈", "可惜", "唉", "哎", "呜", "心疼",
            "不幸", "糟糕", "坏了", "坏了", "失望", "失落", "孤独",
            "寂寞", "想念", "思念", "离开", "再见", "离别", "分手",
            "sorry", "sad", "cry", "miss you",
        ],
        "patterns": [
            r"[呜唉哎]{2,}",
            r"[。。]{2,}",       # 省略号情绪
            r"\.{3,}",
            r"…{1,}",
            r"~+$",             # 结尾拖长波浪
        ],
        "emoji": ["😢", "😭", "💔", "😔", "🥺", "😞", "😿", "💤"],
        "weight": 1.0,
    },
    "angry": {
        "keywords": [
            "生气", "愤怒", "讨厌", "烦", "烦人", "气死", "恼火",
            "不满", "抗议", "抗议", "受不了", "受够", "忍无可忍",
            "混蛋", "笨蛋", "蠢", "白痴", "废物",
            "angry", "hate", "annoy",
        ],
        "patterns": [
            r"[哼]{2,}",
            r"切+",
        ],
        "emoji": ["😤", "😠", "💢", "🔥", "😡", "🤬", "💣"],
        "weight": 1.2,  # 愤怒情绪权重稍高（用户更容易感知到不爽）
    },
    "surprise": {
        "keywords": [
            "哇", "天哪", "天啊", "我的天", "不会吧", "真的吗", "假的",
            "居然", "竟然", "没想到", "意外", "惊喜", "惊人",
            "不敢相信", "难以置信", "不可思议", "what", "wow",
            "真的假的", "你猜", "告诉你", "好消息", "重磅",
        ],
        "patterns": [
            r"哇[！!]*",
            r"天[哪啊][！!]*",
            r"\?{2,}",          # 多个问号
            r"[？]{2,}",
            r"[！!]{3,}",       # 3个以上感叹号=震惊
        ],
        "emoji": ["😲", "🤯", "😱", "🫢", "😮", "❗", "⁉️"],
        "weight": 0.9,
    },
    "love": {
        "keywords": [
            "喜欢", "爱你", "爱", "想你", "想念", "挂念",
            "宝贝", "亲爱的", "亲爱", "甜", "甜蜜", "温暖",
            "拥抱", "抱抱", "亲", "亲亲", "么么", "mua",
            "心动", "浪漫", "缘分", "在一起", "永远",
            "love", "like", "miss", "darling", "sweet",
        ],
        "patterns": [
            r"mua[！!~]*",
            r"么么[！!~]*",
            r"[❤️💕💗💖💘💝]{1,}",
        ],
        "emoji": ["❤️", "💕", "💗", "💖", "💘", "💝", "🥰", "😘", "💋", "🫶", "💑"],
        "weight": 1.1,
    },
    "dance": {
        "keywords": [
            "跳舞", "舞蹈", "庆祝", "嗨起来", "蹦迪", "派对",
            "音乐", "节奏", "摇滚", "嗨", "狂欢", "放飞",
            "party", "dance", "celebrate",
        ],
        "patterns": [
            r"[！!]{2,}.*[！!]{2,}",  # 兴奋语气
        ],
        "emoji": ["🎶", "🎵", "🎤", "🎸", "🪩", "💃", "🕺", "🫰"],
        "weight": 0.8,
    },
    "think": {
        "keywords": [
            "让我想想", "嗯", "这个嘛", "思考", "分析", "考虑",
            "研究", "琢磨", "思索", "推理", "判断", "想想",
            "let me think", "hmm",
        ],
        "patterns": [
            r"嗯[,.，。…]*",
            r"这个[嘛啊]*",
            r"\.{3,}",
            r"…{2,}",
        ],
        "emoji": ["🤔", "💭", "🧐", "🔍"],
        "weight": 0.7,
    },
    "calm": {
        "keywords": [
            "安慰", "没关系", "不要紧", "放心", "别担心", "别怕",
            "平静", "放松", "深呼吸", "休息", "冷静", "安心",
            "没事", "有我在", "一切都会好", "会好的",
            "calm", "relax", "peace",
        ],
        "patterns": [
            r"没事[的]*",
            r"放心[吧]*",
        ],
        "emoji": ["😌", "🫧", "🌸", "🍃", "🕊️", "☁️"],
        "weight": 0.85,
    },
}

# 所有支持的动画状态（含非情绪状态）
ALL_EMOTION_STATES = list(_EMOTION_RULES.keys()) + ["talk"]


def analyze_emotion(text: str) -> str:
    """分析文本情绪，返回最佳匹配的动画状态名。

    算法：
    1. 扫描所有情绪的 关键词/正则/表情 符合项
    2. 每个符合项按规则权重累加得分
    3. 得分最高的情绪胜出；并列时优先级：love > angry > sad > happy > 其他
    4. 无任何命中时返回 "talk"（普通说话）

    性能：纯正则 + 字符串匹配，单次 < 0.1ms（1000字文本）

    Examples:
        >>> analyze_emotion("哈哈，太好了！恭喜你！")
        'happy'
        >>> analyze_emotion("呜呜，我好难过...")
        'sad'
        >>> analyze_emotion("哼，气死我了！！！")
        'angry'
        >>> analyze_emotion("天哪，不会吧？真的吗！")
        'surprise'
        >>> analyze_emotion("宝贝，我好想你~")
        'love'
        >>> analyze_emotion("今天天气不错")
        'talk'
    """
    if not text or not text.strip():
        return "talk"

    scores: dict[str, float] = {}

    for emotion, rules in _EMOTION_RULES.items():
        score = 0.0

        # 1. 关键词匹配
        for kw in rules["keywords"]:
            if kw in text:
                score += 2.0

        # 2. 正则模式匹配
        for pat in rules["patterns"]:
            matches = re.findall(pat, text)
            score += len(matches) * 1.5

        # 3. 表情符号匹配
        for em in rules["emoji"]:
            if em in text:
                score += 3.0  # 表情符号权重最高（用户意图明确）

        # 4. 应用情绪权重
        score *= rules.get("weight", 1.0)

        if score > 0:
            scores[emotion] = score

    if not scores:
        return "talk"

    # 并列时的优先级（越靠前越优先）
    priority = ["love", "angry", "sad", "happy", "surprise", "dance", "think", "calm"]

    # 找最高分
    max_score = max(scores.values())
    candidates = [e for e, s in scores.items() if s == max_score]

    if len(candidates) == 1:
        return candidates[0]

    # 并列时按优先级排序
    for p in priority:
        if p in candidates:
            return p

    return candidates[0]


def analyze_per_sentence(sentences: list[str]) -> list[str]:
    """逐句分析情绪，返回每句对应的状态名列表。

    用于播放时逐句切换表情（更细腻的交互体验）。
    """
    return [analyze_emotion(s) for s in sentences]


def dominant_emotion(sentences: list[str]) -> str:
    """从多句话中提取整体主导情绪。

    采用投票 + 加权：每句的情绪得 1 票，最终取票数最高的情绪。
    """
    if not sentences:
        return "talk"

    votes: dict[str, int] = {}
    for s in sentences:
        e = analyze_emotion(s)
        votes[e] = votes.get(e, 0) + 1

    return max(votes, key=votes.get)
