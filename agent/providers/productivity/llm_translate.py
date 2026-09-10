"""翻译能力：复用项目自己的 LLM 插件（`agent.productivity.translate`）

## 为什么是"复用 LLM"而不是接翻译 API

`translate` 工具从 P2 起就要求外部注入一个 `TranslateFunc`，而
`config.yaml` 里没有、也不需要第三个翻译服务商的密钥 ——
项目**已经有**一个可用的 LLM（`plugins/llm/openrouter`，含备用端点）。
用一句精心写过的提示词就能翻译，且换模型/换服务商只改 LLM 配置。

## 这个 Provider 的职责边界

- 只做"文本 → 译文"，不做语言检测（`target_lang` 由工具校验并给默认值）
- **失败一律返回 None**，由工具层给出友好文案（翻译是增强能力，
  不该因为网络抖动让用户看到异常）
- 输出清洗：模型爱加引号/前缀/"译文："标签，这些都要剥掉，
  否则用户会听到"译文：冒号 Hello"
"""

from __future__ import annotations

import logging
import re
from typing import Callable, Optional

logger = logging.getLogger(__name__)

#: LLM 单次补全签名（与 `agent.plugins.SVC_LLM_ONCE` 一致）
LLMOnceCall = Callable[[str], Optional[str]]

#: 目标语言代码 → 提示词里用的语言名
LANG_NAMES = {
    "zh": "中文", "en": "英文", "ja": "日文", "ko": "韩文",
    "fr": "法文", "de": "德文", "es": "西班牙文", "ru": "俄文",
}

#: 模型爱加的前缀标签："译文：""以下是翻译：""Translation:""Here is the translation:"
#:
#: 实测真实 LLM 会返回 `以下是翻译：Today the weather is nice...` ——
#: 原正则只认 `译文：`，这个前缀就漏到了用户耳朵里。
_LABEL_RE = re.compile(
    r"^\s*[^\n：:]{0,24}?(?:译文|翻译|translation|translated)[^\n：:]{0,24}?\s*[:：]\s*",
    re.IGNORECASE,
)

#: 没有冒号、独占一行的情况："以下是译文\nHello"
_LABEL_LINE_RE = re.compile(
    r"^\s*[^\n：:]{0,24}?(?:译文|翻译|translation)[^\n：:]{0,24}?\s*\n+",
    re.IGNORECASE,
)

#: 最多剥几轮标签（防病态输入下无限循环；正常 1 轮就收敛）
_MAX_LABEL_PASSES = 4

#: 提示词：把要求压到最短，同时明确"只输出译文"
_PROMPT = """把下面的文本翻译成{lang}。

要求：
1. 只输出译文本身，不要加引号、不要说"译文："、不要任何解释
2. 保持原文的语气与换行
3. 专有名词（人名/品牌/代码）保持原样

原文：
{text}"""


def clean_translation(raw: str) -> str:
    """清洗模型输出：剥掉包裹的引号与"译文："前缀

    这些是 LLM 的常见礼貌性装饰，念出来很难听，所以在这里统一去掉。
    """
    text = str(raw or "").strip()
    if not text:
        return ""

    # 反复剥：模型可能叠前缀（"译文：以下是翻译：\nHello"）——
    # 只剥一次的话第二个标签会漏给用户听，而这正是真实 LLM 干过的事。
    for _ in range(_MAX_LABEL_PASSES):
        before = text
        text = _LABEL_RE.sub("", text).strip()
        text = _LABEL_LINE_RE.sub("", text).strip()
        if text == before:
            break

    # 只有首尾成对时才剥引号（避免把译文里本来就有的引号弄坏）
    for left, right in (('"', '"'), ("'", "'"), ("“", "”"), ("「", "」")):
        if len(text) >= 2 and text.startswith(left) and text.endswith(right):
            text = text[1:-1].strip()
            break
    return text


def make_llm_translate(llm_once: Optional[LLMOnceCall]) -> Optional[Callable[[str, str], Optional[str]]]:
    """构造翻译函数；没有 LLM 时返回 None（工具会给出"还没接上翻译能力"）

    Args:
        llm_once: LLM 单次补全（通常是 `XiaoyiApp.chat_once`）

    Returns:
        `(text, target_lang) -> Optional[str]`，失败返回 None
    """
    if llm_once is None:
        return None

    def translate(text: str, target_lang: str) -> Optional[str]:
        target = str(target_lang or "en").lower()
        lang = LANG_NAMES.get(target, LANG_NAMES["en"])
        try:
            raw = llm_once(_PROMPT.format(lang=lang, text=text))
        except Exception as e:                      # LLM 抛异常不该让工具炸
            logger.warning("[translate] LLM 调用失败: %s", e)
            return None
        cleaned = clean_translation(raw or "")
        if not cleaned:
            logger.warning("[translate] LLM 未返回可用译文")
            return None
        return cleaned

    return translate
