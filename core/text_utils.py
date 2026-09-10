"""
文本工具：中文分句
用于LLM回复按句切分，实现边生成边播报。
"""

import re

# 句末标点（中英文），标点保留在句尾
_SENTENCE_BOUNDARY = re.compile(r'(?<=[。！？!?.])')


def split_sentences(text: str, min_len: int = 2) -> list:
    """将文本按句切分，返回句子列表。

    规则：
    - 按句末标点（。！？!?）切分，标点保留在句尾
    - 换行视为分隔符
    - 无句末标点的碎片（len < min_len 且未结束）并入前一句
    - 空白/空文本返回 []

    Args:
        text: 原始文本
        min_len: 成句最小长度（字符数）

    Returns:
        句子列表
    """
    if not text or not text.strip():
        return []

    sentences = []
    for line in re.split(r'[\n\r]+', text):
        if not line.strip():
            continue
        parts = _SENTENCE_BOUNDARY.split(line)
        for p in parts:
            p = p.strip()
            if not p:
                continue
            sentences.append(p)

    # 合并未结束的短碎片到前一句（如 "好啊 我的意思"）
    # 但保留成句的短句（如 "喵。"）
    result = []
    for s in sentences:
        if len(s) < min_len and s[-1] not in "。！？!?":
            if result:
                result[-1] += s
            else:
                result.append(s)
        else:
            result.append(s)

    return result
