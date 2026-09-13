# -*- coding: utf-8 -*-
"""
animation.vrm_state_map — 宠物状态 → VRM 动画/表情映射（Task 3）

纯 Python 模块（无 Qt 依赖），对接：
- Python 侧状态机（ui.state_machine.py）中的 7 个状态
- JS 侧 State/表情（assets/vrm/js/app.js 的 petX.setState / ExpressionPresets，
  见 Task 1 的 STATE_DEFS 七状态及任务说明）

注意：本模块仅做纯映射，不调用 JS。
- STATE_TO_VRM 的值为 JS petX.setState 接收的小写状态名：
  app.js 以 STATE_DEFS（app.js:29-37）小写键严格校验，非小写将被
  warn + 拒绝（app.js:597），故值域必须与之一致。
- 表情名以 JS 侧 EXPR_PRESETS（neutral/happy/sad/relaxed）为准：
  talk→"neutral"（talk 的 happy 表情由 JS lipSync 通道派生，
  见 STATE_DEFS.talk.expr='happy'，app.js:31,475，本函数不重复设置）。
"""

# 所有标准状态（含新增情绪/交互状态）
# JS 侧 STATE_DEFS 对未映射状态静默回退 idle
STANDARD_STATES: tuple[str, ...] = (
    "idle", "talk", "think", "listen", "happy", "sad",
    "angry", "surprise", "love", "dance", "stare",
    "calm_down", "comfort", "pat", "poke",
)

# 状态名 → VRM 侧动画名（值为 JS petX.setState 接收的小写状态名）
# 无对应VRM动画的状态回退到 idle
STATE_TO_VRM: dict[str, str] = {
    "idle":       "idle",
    "talk":       "talk",
    "think":      "think",
    "listen":     "listen",
    "happy":      "happy",
    "sad":        "sad",
    "angry":      "idle",      # VRM无angry动画，回退idle+表情
    "surprise":   "idle",      # VRM无surprise动画，回退idle+表情
    "love":       "happy",     # love复用happy
    "dance":      "idle",      # VRM无dance动画，回退idle
    "stare":      "idle",      # VRM无stare动画，回退idle
    "calm_down":  "idle",
    "comfort":    "idle",
    "pat":        "happy",     # pat复用happy
    "poke":       "idle",
}

# 状态 → morph 表情名映射
# VRM 支持的表情: neutral, happy, sad, relaxed, angry, surprised
_STATE_EXPRESSIONS: dict[str, str] = {
    "idle":       "neutral",
    "talk":       "neutral",   # talk的happy由JS lipSync派生
    "think":      "neutral",
    "listen":     "neutral",
    "happy":      "happy",
    "sad":        "sad",
    "angry":      "angry",
    "surprise":   "surprised",
    "love":       "happy",
    "dance":      "happy",
    "stare":      "neutral",
    "calm_down":  "relaxed",
    "comfort":    "relaxed",
    "pat":        "happy",
    "poke":       "surprised",
}


def expression_for(state: str) -> str:
    """状态 → morph 表情名

    规则：优先查 _STATE_EXPRESSIONS 映射表；未命中回退 "neutral"。
    """
    return _STATE_EXPRESSIONS.get(state, "neutral")


def validate() -> list[str]:
    """校验 7 个标准状态均有非空映射，返回缺失/空值状态名列表

    Returns:
        list[str]: 为空表示映射完整
    """
    missing: list[str] = []
    for state in STANDARD_STATES:
        if not STATE_TO_VRM.get(state):
            missing.append(state)
    return missing
