"""P4-B5：短句确认/取消语的识别（含一个危险的假确认缺陷）

## 缺陷一：待确认时，一句招呼被当成「确定」（已修）

`RuleRouter._matches_any` 原是朴素子串匹配 `if p in text`，而
`CONFIRM_PHRASES` 里含**单字**「好」、`CANCEL_PHRASES` 里含**单字**「否」。
真实 `RuleRouter` 实测（`has_pending_confirm=True`）：

| 用户说 | 修前判成 | 后果 |
|--------|---------|------|
| 「你好呀」 | **confirm** | 一句招呼 → 放过待确认的 `file_delete` |
| 「我很好」 | **confirm** | 同上 |
| 「好奇怪」 | **confirm** | 同上 |
| 「好久不见」 | **confirm** | 同上 |
| 「你是否在听」 | **cancel** | 用户只是提问，待确认项却被撤掉 |

`confirm` 是**唯一会直接把用户推进副作用**的路由分支（它会放过删除），
对应 AGENTS.md 的 R1「误操作损坏文件」——最高优先级风险。

修法：短语按长度分两档 —— 多字子串命中（保住「嗯嗯，好的」），
**单字要求整句成立**（只允许夹语气助词）。

## 缺陷二：ASR 侧确实有改善空间（已实测，结论见证据）

`tools/measure_asr_stimulus.py --short` 的基线（TTS 替身，每句 4 次）：
「算了」**0/4**（4 次全被听成「散了」）、「不用了」2/4（另 2 次**整句被丢**）。
加解码偏置 `initial_prompt` 后 6 句全部 **4/4**、空输出 0、听错 0。

> ⚠️ 这只证明**偏置有效**，不证明"真人说「算了」也会被听成「散了」" ——
> 测的是 TTS 替身这一条链路。真人那一条在 `docs/agent/manual-acceptance.md`。
"""

import pytest

from agent.providers.router.rule_router import (
    CANCEL_PHRASES,
    CONFIRM_PHRASES,
    SINGLE_CHAR_FILLERS,
    RuleRouter,
)


@pytest.fixture
def router():
    return RuleRouter()


def _act(router, text):
    return router.route(text, {"has_pending_confirm": True, "source": "voice"}).action


# ══════════════════════════════════════════════════
#  一、日常话绝不能被当成确认/取消（本轮修掉的危险缺陷）
# ══════════════════════════════════════════════════

class TestEverydaySpeechIsNotConfirmation:
    """这些句子在待确认时**必须**走正常路由，绝不能 confirm/cancel"""

    @pytest.mark.parametrize("text", [
        "你好呀",        # 含单字「好」
        "我很好",
        "好奇怪",
        "好久不见",
        "好多人啊",
        "这不太好吧",
    ])
    def test_containing_hao_is_not_confirm(self, router, text):
        assert _act(router, text) != "confirm", (
            f"{text!r} 被判成 confirm —— 一句招呼会执行待确认的删除"
        )

    @pytest.mark.parametrize("text", ["你是否在听", "是否继续呢", "天气如何"])
    def test_containing_fou_is_not_cancel(self, router, text):
        assert _act(router, text) != "cancel"

    def test_whatsapp_like_greeting(self, router):
        """最常见的招呼组合"""
        for text in ("你好", "您好", "嗨", "在吗"):
            assert _act(router, text) not in ("confirm", "cancel")


# ══════════════════════════════════════════════════
#  二、真正的确认/取消必须照旧有效（防"修窄到不能干活"）
# ══════════════════════════════════════════════════

class TestRealConfirmCancelStillWorks:
    @pytest.mark.parametrize("text", [
        "确定", "确认", "是的", "对的", "没错", "可以", "行吧",
        "好的", "删吧", "做吧", "执行吧", "同意", "批准",
        "好的，删吧", "嗯嗯，好的", "那好吧", "可以的",
    ])
    def test_confirm(self, router, text):
        assert _act(router, text) == "confirm", f"{text!r} 应判 confirm"

    @pytest.mark.parametrize("text", [
        "算了", "不用了", "不用", "取消", "不要了", "不要",
        "别删", "不删", "不做了", "不执行", "停下", "停止", "不对",
        "算了算了", "那算了",
    ])
    def test_cancel(self, router, text):
        assert _act(router, text) == "cancel", f"{text!r} 应判 cancel"

    @pytest.mark.parametrize("text", ["好", "嗯", "嗯嗯", "好呀", "嗯好"])
    def test_single_char_with_fillers_still_confirms(self, router, text):
        """单字允许夹语气助词 —— 否则会把「嗯嗯」「好呀」这类真实应答弄丢"""
        assert _act(router, text) == "confirm", f"{text!r} 应判 confirm"

    def test_fou_alone_is_cancel(self, router):
        assert _act(router, "否") == "cancel"

    def test_cancel_wins_over_confirm(self, router):
        """同时含确认与取消词时，取消优先（保守方向）"""
        assert _act(router, "算了，不确定") == "cancel"


# ══════════════════════════════════════════════════
#  三、判据本身（含无单字短语的形态）
# ══════════════════════════════════════════════════

class TestMatchesAnyContract:
    """`_matches_any` 是通用工具：两种短语表形态都要成立"""

    def test_multi_char_substring(self):
        assert RuleRouter._matches_any("嗯嗯，好的", CONFIRM_PHRASES) is True

    def test_multi_only_list_has_no_single_branch(self):
        """**只有多字短语**的表：不应走进单字分支（这条钉住那条早退）"""
        assert RuleRouter._matches_any("确定", ["确定", "确认"]) is True
        assert RuleRouter._matches_any("你好呀", ["确定", "确认"]) is False

    def test_no_match_at_all(self):
        assert RuleRouter._matches_any("帮我找文件", CONFIRM_PHRASES) is False

    def test_single_char_requires_whole_utterance(self):
        assert RuleRouter._matches_any("好", CONFIRM_PHRASES) is True
        assert RuleRouter._matches_any("你好", CONFIRM_PHRASES) is False

    def test_punctuation_is_stripped(self):
        """「好！」「嗯。」要算 —— 标点不该毁掉一次确认"""
        assert RuleRouter._matches_any("好！", CONFIRM_PHRASES) is True
        assert RuleRouter._matches_any("嗯。", CONFIRM_PHRASES) is True

    def test_filler_set_excludes_meaningful_chars(self):
        """语气助词表里**不能**混进实义字

        「是 / 对 / 行」看起来像应答，但它们出现在「你是否在听」这种问句里，
        放进允许集会立刻把这个缺陷重新引回来。
        """
        for ch in "是对行不没要":
            assert ch not in SINGLE_CHAR_FILLERS, f"{ch!r} 不该被当成语气助词"

    def test_phrase_tables_have_the_single_chars_we_think(self):
        """把"哪几个是单字"钉住 —— 缺陷正是由它们引起的"""
        assert {p for p in CONFIRM_PHRASES if len(p) == 1} == {"好", "嗯"}
        assert {p for p in CANCEL_PHRASES if len(p) == 1} == {"否"}


# ══════════════════════════════════════════════════
#  四、没有待确认项时不参与（回归保护）
# ══════════════════════════════════════════════════

class TestNoPendingConfirmUnchanged:
    def test_greeting_without_pending(self, router):
        assert _act(router, "你好呀") == "chat"

    def test_confirm_word_without_pending_is_not_confirm(self, router):
        """没有待确认项时，「确定」不该凭空变成 confirm"""
        assert router.route("确定", {"has_pending_confirm": False}).action != "confirm"
