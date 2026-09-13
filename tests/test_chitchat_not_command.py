# -*- coding: utf-8 -*-
"""闲聊不得被判成文件操作（D46）。

## 缺陷现场（用户报告）

> 她总是说"哎呀，刚才那个「Desktop」我没太听明白呢。我目前只能操..."
> 我也没让他操作桌面软件呢

日志证据（`logs/xbya.log`）：

    file_list({'dirs': ['Desktop']}) conf=0.93
    目录解析失败: 不认识这些目录：Desktop
    可用的目录是：Desktop、Documents、Downloads、Pictures
    -> "「Desktop」这个位置我没听懂哦，我只能操作：Desktop、..."

**用户从没提过任何目录**，却被派了一个 `file_list`，还带着
`dirs=['Desktop']`。这是三处缺陷叠加：

### 缺陷 1：`_extract_list_params` 凭空造出默认目录

    return {"dirs": dirs or ["Desktop"]}      # <- 没说目录就默认 Desktop

于是"没说目录"被伪装成"说了 Desktop"，**用户的意图被静默替换**。

### 缺陷 2：关键词表里是日常口语

`file_list` 的关键词含 `"有什么"(2.2)`、`"看一下"(1.2)`，而 `file_list`
的基础分 0.60 + 关键词 0.25 + 参数完整 0.15 ≈ 0.93。于是：

    今天有什么好吃的          -> file_list conf=0.93
    你觉得我这个人有什么缺点    -> file_list conf=0.93
    看一下我新买的鼠标         -> file_list conf=0.85

### 缺陷 3：低置信的"动作类"意图会被执行

HybridRouter 在 LLM 不可用时 `return rule_cmd`，
于是 0.33 置信度的猜测照样被当成指令。

## 本用例钉住

1. 闲聊（未提及任何目录/文件）必须回落 `chat`
2. 参数提取**不许凭空造默认值**
3. 反方向：真·文件指令必须仍然路由成功
"""

import pytest

from agent.providers.router.rule_router import RuleRouter


@pytest.fixture(scope='module')
def rule():
    return RuleRouter()


@pytest.fixture(scope='module')
def hybrid():
    from agent.providers.router.hybrid_router import HybridRouter
    # LLM 不可用 → 走"规则 + 兜底"分支，正是出问题的场景
    return HybridRouter(rule=RuleRouter(), threshold=0.5)


#: 纯闲聊：**没有提到任何目录或文件**。必须全都回落 chat。
CHITCHAT = [
    '今天有什么好吃的',
    '你觉得我这个人有什么缺点',
    '看一下我新买的鼠标',
    '我桌面上东西太多了',
    '你在干嘛呀',
    '有什么推荐的电影吗',
    '显示一下你的本事',
]


class TestChitchatIsNotACommand:
    @pytest.mark.parametrize('text', CHITCHAT)
    def test_chitchat_routes_to_chat(self, hybrid, text):
        cmd = hybrid.route(text)
        assert cmd.action == 'chat', (
            f'{text!r} 被判成了 {cmd.action}({cmd.params}) '
            f'conf={cmd.confidence:.2f} —— 用户在闲聊，不该产生文件操作'
        )


class TestNoInventedDefaults:
    """参数提取不许凭空造默认值。"""

    def test_list_params_has_no_default_dir(self, rule):
        """`file_list` 在没提到目录时，不许填 `dirs`。

        原实现 `dirs or ["Desktop"]` 把"没说"伪装成"说了 Desktop"，
        既制造了假意图，又让置信度因为"参数完整"而虚高。
        """
        norm = rule._normalize('有什么')
        params = rule._extract_list_params(norm, '有什么', {})
        assert not params.get('dirs'), (
            f'未提及目录却填了 dirs={params.get("dirs")} —— '
            f'默认值只能补"已确认的意图"，不能制造意图'
        )

    def test_list_params_keeps_explicit_dir(self, rule):
        """反方向：明确说了目录就要保留。"""
        norm = rule._normalize('列出桌面上的文件')
        params = rule._extract_list_params(norm, '列出桌面上的文件', {})
        assert params.get('dirs'), '明确说了桌面却丢了 dirs'


class TestConfidencePenalty:
    """缺目标的文件动作必须显著降分。"""

    def test_file_action_without_target_is_low_confidence(self, rule):
        cmd = rule.route('今天有什么好吃的')
        if cmd.action != 'chat':
            assert cmd.confidence < 0.5, (
                f'缺目标的 {cmd.action} 置信度 {cmd.confidence:.2f} '
                f'仍高于阈值 0.5 —— 会被当成指令执行'
            )

    def test_confidence_drops_without_target(self, rule):
        """有目标 vs 没目标，置信度必须拉开差距。"""
        with_target = rule.route('列出桌面上的文件')
        assert with_target.action == 'file_list'
        bare = rule.route('有什么')
        # 没有目标时要么直接 chat，要么置信度明显更低
        if bare.action == 'file_list':
            assert bare.confidence < with_target.confidence - 0.3, (
                f'缺目标只降了 {with_target.confidence - bare.confidence:.2f}'
            )


class TestRealCommandsStillWork:
    """反方向保护：真·指令不许被误降。"""

    @pytest.mark.parametrize('text,expected', [
        ('找一下桌面上的PDF文件', 'file_search'),
        ('删除桌面上的截图', 'file_delete'),
        ('列出下载目录里的文件', 'file_list'),
        ('打开桌面上的报告.txt', 'file_read'),
        ('看一下桌面', 'file_list'),
    ])
    def test_real_command_routes(self, hybrid, text, expected):
        cmd = hybrid.route(text)
        assert cmd.action == expected, (
            f'{text!r} 期望 {expected}，实际 {cmd.action} '
            f'(conf={cmd.confidence:.2f})'
        )


class TestHybridLowConfFallsBackToChat:
    """LLM 不可用时，低置信的**动作类**意图不能执行。"""

    def test_low_conf_action_becomes_chat(self):
        from agent.providers.router.hybrid_router import HybridRouter

        class _FixedRule:
            """固定返回一个低置信的 file_list。"""
            def route(self, text, context=None):
                from agent.message import AgentCommand
                return AgentCommand(action='file_list', params={'dirs': ['Desktop']},
                                    raw_text=text, confidence=0.33, source='test')

        h = HybridRouter(rule=_FixedRule(), threshold=0.5)
        cmd = h.route('随便说点什么')
        assert cmd.action == 'chat', (
            f'低置信(0.33)的 file_list 被执行了 —— '
            f'没把握时宁可当闲聊，也不能拿用户的文件赌一个猜测'
        )

    def test_high_conf_action_still_executes(self):
        """反方向：高置信动作照常执行。"""
        from agent.providers.router.hybrid_router import HybridRouter

        class _FixedRule:
            def route(self, text, context=None):
                from agent.message import AgentCommand
                return AgentCommand(action='file_list', params={'dirs': ['Desktop']},
                                    raw_text=text, confidence=0.95, source='test')

        h = HybridRouter(rule=_FixedRule(), threshold=0.5)
        assert h.route('列出桌面').action == 'file_list'
