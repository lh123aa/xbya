# -*- coding: utf-8 -*-
"""
animation.vrm_state_map 单元测试（纯 Python，无 Qt 依赖）

覆盖：
- STATE_TO_VRM 全状态覆盖（17 个状态 → JS 侧 7 状态契约）
- validate() 缺失检测
- expression_for() 表情映射（含新增情绪状态）
"""

import sys
from pathlib import Path

import pytest

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from animation.vrm_state_map import (
    STATE_TO_VRM,
    STANDARD_STATES,
    expression_for,
    validate,
)

#: JS 侧真正支持的 7 个状态（映射值域硬边界）
JS_STATES = {"idle", "talk", "think", "listen", "happy", "sad"}

#: 基础状态 → 直连映射
EXPECTED_VRM = {
    "idle": "idle",
    "talk": "talk",
    "think": "think",
    "listen": "listen",
    "happy": "happy",
    "sad": "sad",
}

#: 新增状态 → JS 状态（无对应动画的回退 idle，语义相近的复用）
EXPECTED_FALLBACKS = {
    "angry": "idle",
    "surprise": "idle",
    "love": "happy",
    "dance": "idle",
    "stare": "idle",
    "calm_down": "idle",
    "comfort": "idle",
    "pat": "happy",
    "poke": "idle",
}

#: 状态 → morph 表情（VRM 支持 neutral/happy/sad/relaxed/angry/surprised）
EXPECTED_EXPRESSIONS = {
    "idle": "neutral",
    "talk": "neutral",
    "think": "neutral",
    "listen": "neutral",
    "happy": "happy",
    "sad": "sad",
    "angry": "angry",
    "surprise": "surprised",
    "love": "happy",
    "dance": "happy",
    "stare": "neutral",
    "calm_down": "relaxed",
    "comfort": "relaxed",
    "pat": "happy",
    "poke": "surprised",
}


class TestStateToVrm:
    """STATE_TO_VRM 映射表"""

    def test_standard_states_constant(self):
        """标准状态集包含基础状态与新增情绪/交互状态"""
        expected = set(EXPECTED_VRM) | set(EXPECTED_FALLBACKS)
        assert set(STANDARD_STATES) == expected
        assert len(STANDARD_STATES) == 15

    def test_base_states_present(self):
        """基础状态必须在标准状态集中（sleep 已移除）"""
        base_without_sleep = JS_STATES - {"sleep"}
        assert base_without_sleep <= set(STANDARD_STATES)

    def test_covers_all_standard_states(self):
        """映射表覆盖全部标准状态"""
        assert set(STATE_TO_VRM) == set(STANDARD_STATES)

    def test_expected_vrm_animation_names(self):
        """基础 7 状态直连映射"""
        for state, vrm in EXPECTED_VRM.items():
            assert STATE_TO_VRM[state] == vrm

    def test_new_states_fallback_mapping(self):
        """新增状态按设计回退/复用"""
        for state, vrm in EXPECTED_FALLBACKS.items():
            assert STATE_TO_VRM[state] == vrm, f"{state} 映射不符"

    def test_all_values_within_js_contract(self):
        """所有映射值必须落在 JS 侧 7 状态内（否则 JS 会拒绝）"""
        invalid = {k: v for k, v in STATE_TO_VRM.items() if v not in JS_STATES}
        assert not invalid, f"映射到 JS 契约外状态: {invalid}"

    def test_values_non_empty(self):
        for state, vrm in STATE_TO_VRM.items():
            assert vrm, f"{state} 映射值不能为空"

    def test_values_lowercase(self):
        """映射值必须小写（app.js 严格校验小写键）"""
        for state, vrm in STATE_TO_VRM.items():
            assert vrm == vrm.lower(), f"{state} 映射值 {vrm!r} 不是小写"


class TestValidate:
    """validate() 缺失检测"""

    def test_complete_map_returns_empty(self, monkeypatch):
        assert validate() == []

    def test_missing_state_reported(self, monkeypatch):
        broken = dict(STATE_TO_VRM)
        del broken["happy"]
        monkeypatch.setattr("animation.vrm_state_map.STATE_TO_VRM", broken)
        assert validate() == ["happy"]

    def test_missing_new_state_reported(self, monkeypatch):
        """新增状态缺失也能被检测到"""
        broken = dict(STATE_TO_VRM)
        del broken["angry"]
        monkeypatch.setattr("animation.vrm_state_map.STATE_TO_VRM", broken)
        assert validate() == ["angry"]

    def test_empty_value_reported(self, monkeypatch):
        broken = dict(STATE_TO_VRM)
        broken["happy"] = ""
        monkeypatch.setattr("animation.vrm_state_map.STATE_TO_VRM", broken)
        assert validate() == ["happy"]

    def test_dry_run_does_not_modify(self):
        before = dict(STATE_TO_VRM)
        validate()
        assert STATE_TO_VRM == before


class TestExpressionFor:
    """expression_for() 表情映射"""

    @pytest.mark.parametrize("morph", ["sad", "happy"])
    def test_positive_morphs(self, morph):
        assert expression_for(morph) == morph

    @pytest.mark.parametrize("state", ["idle", "talk", "think", "listen"])
    def test_neutral_states(self, state):
        assert expression_for(state) == "neutral"

    @pytest.mark.parametrize("state,expected", sorted(EXPECTED_EXPRESSIONS.items()))
    def test_all_standard_states_have_explicit_expression(self, state, expected):
        """每个标准状态都有显式表情映射"""
        assert expression_for(state) == expected

    @pytest.mark.parametrize(
        "state", ["", "nonexistent", "relaxed", "unknown_state", "blink"])
    def test_unknown_state_defaults_neutral(self, state):
        """契约外状态回退 neutral"""
        assert expression_for(state) == "neutral"

    def test_new_emotions_map_to_vrm_morphs(self):
        """新增情绪映射到 VRM 支持的表情"""
        assert expression_for("angry") == "angry"
        assert expression_for("surprise") == "surprised"
        assert expression_for("love") == "happy"
        assert expression_for("dance") == "happy"

    def test_returned_values_are_valid_morphs(self):
        """返回值必须是 VRM 支持的表情名"""
        valid = {"neutral", "happy", "sad", "relaxed", "angry", "surprised"}
        for state in STANDARD_STATES:
            expr = expression_for(state)
            assert expr in valid, f"{state} → {expr} 不是有效 morph"
