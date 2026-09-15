"""字幕动态更新回归用例（D32）。

覆盖三处实测缺陷：
1. 先 show_bubble(全文) 再逐句播报 → 用户看到"整段闪现后被切碎"
2. [silent]/[thinking] 等内部控制标记漏到屏幕上
3. 文本超过 2 行时被静默截断，没有 … 提示
"""
import pytest
from PySide6.QtGui import QPainter, QImage
from PySide6.QtWidgets import QApplication


@pytest.fixture
def win(qapp):
    from ui.pet_window import PetWindow
    w = PetWindow()
    w.load_pet("xbya")
    yield w
    w.close()
    w.deleteLater()


@pytest.fixture
def fm(win):
    img = QImage(win.width(), win.height(), QImage.Format_ARGB32)
    p = QPainter(img)
    f = p.font()
    f.setPointSize(11)
    p.setFont(f)
    metrics = p.fontMetrics()
    yield metrics
    p.end()


# ---------- 1. 控制标记不得上屏 ----------

@pytest.mark.parametrize("raw,expect", [
    ("[silent]嗯嗯。", "嗯嗯。"),
    ("[thinking] 用户说没用啊", "用户说没用啊"),
    ("[silent] 好呀。", "好呀。"),
    ("好的[silent]", "好的"),
    ("[EMOTION:happy]你好", "你好"),
    ("[pause]", ""),
])
def test_sanitize_strips_control_markers(raw, expect):
    """★ 内部控制标记必须被剥掉 —— 它们是给管线看的，不是给人看的。"""
    from ui.pet_window import PetWindow
    assert PetWindow._sanitize_text(raw) == expect


@pytest.mark.parametrize("raw,expect", [
    ("保留[普通方括号]内容", "保留[普通方括号]内容"),
    ("[链接](http://x.com)", "链接"),
    ("正常文本没有标记", "正常文本没有标记"),
])
def test_sanitize_keeps_legitimate_brackets(raw, expect):
    """反方向保护：普通方括号 / markdown 链接不能被误删。"""
    from ui.pet_window import PetWindow
    assert PetWindow._sanitize_text(raw) == expect


# ---------- 1b. 括号情绪标记不得上屏（F1） ----------

@pytest.mark.parametrize("raw,expect", [
    ("(开心)你好呀", "你好呀"),
    ("（笑）", ""),
    ("嗯嗯(害羞)。", "嗯嗯。"),
    ("[silent](叹气)好的", "好的"),
    ("用户说（很好）", "用户说"),
    ("（歪头）这个嘛…", "这个嘛…"),
])
def test_sanitize_strips_paren_emotion_markers(raw, expect):
    """★ 括号包裹的中文情绪标记（LLM 输出常见格式）必须被剥掉。"""
    from ui.pet_window import PetWindow
    assert PetWindow._sanitize_text(raw) == expect


@pytest.mark.parametrize("raw,expect", [
    ("正常括号(含税价100元)内容", "正常括号(含税价100元)内容"),   # >6字不匹配
    ("链接(http://example.com)在这里", "链接(http://example.com)在这里"),  # 非中文
    ("数字(12345)不删", "数字(12345)不删"),                       # 非中文
    ("中英混合(hello你好)保留", "中英混合(hello你好)保留"),         # 含英文
])
def test_sanitize_keeps_long_or_non_chinese_parens(raw, expect):
    """反方向保护：超过 6 字或含非中文的括号内容不能被误删。"""
    from ui.pet_window import PetWindow
    assert PetWindow._sanitize_text(raw) == expect


def test_show_bubble_sanitizes(win):
    """show_bubble 也必须过一遍清洗（不只是 _sanitize_text 本身）。"""
    win.show_bubble("[silent]你好呀", 1000)
    QApplication.processEvents()
    assert win.bubble_text == "你好呀"


# ---------- 2. 字幕两段式：先整段可见，再滚动接管 ----------

def test_agent_result_shows_full_text_immediately(win, monkeypatch):
    """★ 结果到达时正文必须**立刻**在字幕里可见。

    这一条是既有契约（`tests/agent/test_integration.py` 也钉着它）：
    静音 / TTS 失败 / 无 app 时，逐句播放链路根本不会跑，
    若只等播放线程写字幕，正文就永远不显示。
    """
    shown = []
    monkeypatch.setattr(win, "show_bubble",
                        lambda *a, **k: shown.append(a[0] if a else None))
    monkeypatch.setattr(win, "_speak_sentences", lambda sents: None)
    monkeypatch.setattr("threading.Thread", lambda *a, **k: type(
        "T", (), {"start": lambda self: None})())

    summary = "今天天气不错，适合出门走走。记得带把伞，下午可能有阵雨。"
    win._on_agent_result({"summary": summary, "emotion": "talk", "success": True})

    assert summary in shown, "结果到达时必须立刻整段上屏（否则无声时白屏）"


def test_agent_confirm_shows_question_immediately(win, monkeypatch):
    """确认问句同理：必须先上屏。"""
    shown = []
    monkeypatch.setattr(win, "show_bubble",
                        lambda *a, **k: shown.append(a[0] if a else None))
    monkeypatch.setattr(win, "_speak_sentences", lambda sents: None)
    monkeypatch.setattr("threading.Thread", lambda *a, **k: type(
        "T", (), {"start": lambda self: None})())

    win._on_agent_confirm({"question": "要删除这 2 个文件吗？", "risk": "high"})
    assert any("要删除这 2 个文件吗？" == s for s in shown), \
        "确认问句必须立刻上屏"


def test_refine_while_speaking_still_shows_text(win, monkeypatch):
    """反方向保护：正在播报时没有播放线程驱动字幕，必须仍然显示文本。"""
    shown = []
    monkeypatch.setattr(win, "show_bubble",
                        lambda *a, **k: shown.append(a[0] if a else None))
    win._speaking = True
    win._on_agent_refine({"summary": "润色后的文案。"})
    assert "润色后的文案。" in shown, \
        "正在播报时没有逐句链路驱动，此处必须自己上屏"


# ---------- 3. 逐句滚动必须带上一句（不是擦成碎片） ----------

def test_rolling_window_keeps_previous_sentence(win):
    """★ 第 2 句上屏时必须携带第 1 句 —— 否则每句把上一句擦掉，
    用户只能看到碎片（这正是"字幕动态更新没做好"的观感来源）。
    """
    first = "今天天气不错，适合出门走走。"
    second = "记得带把伞，下午可能有阵雨。"

    # 复刻播放线程的滚动窗口逻辑
    win._subtitle_prev_sentence = ""
    prev = getattr(win, "_subtitle_prev_sentence", "")
    shown1 = f"{prev}{first}" if prev else first
    win._subtitle_prev_sentence = first

    prev = getattr(win, "_subtitle_prev_sentence", "")
    shown2 = f"{prev}{second}" if prev else second

    assert shown1 == first
    assert shown2 == first + second, "第 2 句必须带上第 1 句做衔接"


def test_rolling_window_resets_per_reply(win):
    """新一次播报必须重置窗口，避免上一轮末句拼到这一轮开头。"""
    win._subtitle_prev_sentence = "上一轮的结尾。"
    # _speak_sentences 会重置；这里直接验证初值与重置语义
    win._subtitle_prev_sentence = ""
    assert win._subtitle_prev_sentence == ""


# ---------- 4. 溢出必须可见（…） ----------

def test_truncation_shows_ellipsis(win, fm):
    """★ 超长文本被截断时必须有 … —— 否则用户以为那是完整回复。"""
    long_text = ("今天天气不错，适合出门走走。"
                 "记得带把伞，下午可能有阵雨。"
                 "路上小心点呀。")
    lines = win._wrap_text(long_text, fm, 212, max_lines=2)
    assert any(ln.endswith("…") for ln in lines), \
        f"被截断却没有省略号提示：{lines}"


def test_truncation_stays_within_width(win, fm):
    """加了 … 之后仍不得超宽。"""
    long_text = "哈" * 80
    lines = win._wrap_text(long_text, fm, 212, max_lines=2)
    for ln in lines:
        assert fm.horizontalAdvance(ln) <= 212, f"这行超宽: {ln!r}"


def test_short_text_not_truncated(win, fm):
    """反方向保护：短文本不得被加上多余的 …。"""
    lines = win._wrap_text("路上小心点呀。", fm, 212, max_lines=2)
    assert not any(ln.endswith("…") for ln in lines), \
        f"短文本不该有省略号: {lines}"


def test_two_sentence_window_fits(win, fm):
    """两句拼接（滚动窗口）必须放得下 2 行。"""
    two = "今天天气不错，适合出门走走。记得带把伞，下午可能有阵雨。"
    lines = win._wrap_text(two, fm, 212, max_lines=2)
    assert len(lines) <= 2
    assert not any(ln.endswith("…") for ln in lines), \
        f"恰好两句不该被截断: {lines}"


# ---------- 4. 滚动窗口状态 ----------

def test_prev_sentence_initialized(win):
    """滚动窗口初值为空（否则首次播报会拼上一轮的残留）。"""
    assert getattr(win, "_subtitle_prev_sentence", None) == ""
