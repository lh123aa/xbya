"""人设加载回归用例（D34）。

用户报告：「后台设置中，人设设定好像没有真正的加载到虚拟人设角色中」

根因：`UniversalLLM.__init__` 按 reply_style **二选一**挑 prompt，

    if reply_style == "detailed" and system_prompt_detailed: ...
    elif reply_style == "concise" and system_prompt_concise: ...
    else: system_prompt

而设置界面编辑的正是 `system_prompt`。只要 reply_style 有值
（出厂即 concise），用户写的人设就被整段绕过去 —— 实测设置里 1935 字，
模型只收到内置的 388 字。
"""
import pytest


def compose(**kw):
    """★ 必须走**构造函数**，不能直接调 `_compose_system_prompt`。

    踩过的坑：第一版测试直接调那个静态helper，结果反向验证时
    把 `__init__` 里的选择逻辑退回旧实现，测试**照样全绿** ——
    因为它测的是 helper，而 bug 在"谁被调用"这一层接线。
    用例必须钉住**最终对象持有的 system_prompt**，才拦得住接线错误。
    """
    from plugins.llm.openrouter.plugin import UniversalLLM
    # 传最小可用参数，避免真连网
    kw.setdefault("api_key", "test-key")
    kw.setdefault("base_url", "http://127.0.0.1:1/v1")
    return UniversalLLM(**kw).system_prompt


USER = "你是一只叫小黑的猫，说话简短傲娇。"


# ---------- 主缺陷：用户人设必须生效 ----------

def test_user_persona_used_when_concise_style():
    """★ 主缺陷：reply_style=concise 时，用户人设不得被内置 concise 段顶掉。"""
    got = compose(system_prompt=USER, reply_style="concise",
                  system_prompt_concise="请简短回答。")
    assert USER in got, f"用户人设必须出现在最终 prompt 里，实际={got[:80]!r}"


def test_user_persona_used_when_detailed_style():
    """★ 同上，detailed 分支也不能顶掉用户人设。"""
    got = compose(system_prompt=USER, reply_style="detailed",
                  system_prompt_detailed="请详细回答。")
    assert USER in got


def test_user_persona_used_when_style_missing():
    """reply_style 为空/未知时同样要用用户人设。"""
    got = compose(system_prompt=USER, reply_style="")
    assert USER in got


# ---------- 详略段是"附加"，不是"替换" ----------

def test_style_block_appended_not_replacing():
    """详略段应与人设**并存**（人设管身份、详略段管本次输出要求）。"""
    got = compose(system_prompt=USER, reply_style="concise",
                  system_prompt_concise="请简短回答。")
    assert USER in got and "请简短回答。" in got, f"两者都要有，实际={got!r}"
    assert got.index(USER) < got.index("请简短回答。"), \
        "人设应在前面（先定身份，再给本次输出要求）"


def test_detailed_block_used_when_detailed():
    """detailed 时用的是 detailed 段，不是 concise 段。"""
    got = compose(system_prompt=USER, reply_style="detailed",
                  system_prompt_concise="短的。",
                  system_prompt_detailed="长的。")
    assert "长的。" in got and "短的。" not in got


# ---------- 没写人设时保持原行为 ----------

def test_no_user_persona_falls_back_to_style_block():
    """用户没写人设 → 退回详略段（出厂行为不变）。"""
    got = compose(system_prompt="", reply_style="concise",
                  system_prompt_concise="内置 concise 人设")
    assert got == "内置 concise 人设"


def test_no_user_persona_no_style_block_uses_default():
    """都没有 → 内置默认人设，且不得为空。"""
    got = compose(system_prompt=None, reply_style="concise",
                  system_prompt_concise=None)
    assert got.strip(), "最终 prompt 不能为空"


def test_whitespace_only_persona_treated_as_missing():
    """只有空白的人设视同没写（别把一堆空格发给模型）。"""
    got = compose(system_prompt="   \n  ", reply_style="concise",
                  system_prompt_concise="内置")
    assert got == "内置"


def test_identical_persona_and_style_not_duplicated():
    """人设与详略段内容相同时不重复拼接（避免无谓翻倍）。"""
    got = compose(system_prompt=USER, reply_style="concise",
                  system_prompt_concise=USER)
    assert got.count(USER) == 1, f"不该重复，实际={got!r}"


# ---------- 真实配置端到端 ----------

def test_real_config_persona_reaches_model():
    """用真实 config.yaml 构造插件，确认设置里的人设真的进了 system_prompt。"""
    import io
    import yaml
    cfg = yaml.safe_load(io.open("config.yaml", encoding="utf-8"))
    params = dict(cfg["plugins"]["llm"]["params"])

    user = (params.get("system_prompt") or "").strip()
    if not user:
        pytest.skip("本机 config 没写人设，跳过")

    from plugins.llm.openrouter.plugin import UniversalLLM
    inst = UniversalLLM(**params)
    assert user in inst.system_prompt, \
        "config 里写的人设没进最终 system message —— 就是用户报的那个问题"
