"""
状态机模块 v2.0
管理宠物的行为状态切换（情绪影响权重，自动随机切换）
新增：anger/love/surprise 情绪，交互事件驱动状态，表情过渡
"""

import random
import time


class PetStateMachine:
    """宠物行为状态机 v2.0"""

    # 基础状态转移图（idle 为枢纽，可随机到多种状态）
    STATES = {
        "idle":     {"next": ["wander", "sleep", "happy", "stare", "dance", "surprise", "angry"], "weight": [12, 6, 8, 8, 6, 3, 2]},
        "wander":   {"next": ["idle", "stare", "happy"], "weight": [70, 15, 15]},
        "sleep":    {"next": ["idle"], "weight": [100]},
        "happy":    {"next": ["idle", "dance", "love"], "weight": [50, 25, 25]},
        "stare":    {"next": ["idle", "think", "surprise"], "weight": [60, 25, 15]},
        "dance":    {"next": ["idle", "happy"], "weight": [60, 40]},
        "talk":     {"next": ["idle"], "weight": [100]},
        "sad":      {"next": ["idle", "comfort"], "weight": [70, 30]},
        "listen":   {"next": ["idle", "think", "happy"], "weight": [50, 30, 20]},
        "think":    {"next": ["idle", "happy", "sad"], "weight": [50, 25, 25]},
        # 新增情绪状态
        "angry":    {"next": ["idle", "calm_down"], "weight": [40, 60]},
        "surprise": {"next": ["idle", "happy", "think"], "weight": [40, 30, 30]},
        "love":     {"next": ["idle", "happy"], "weight": [60, 40]},
        "calm_down": {"next": ["idle", "happy"], "weight": [70, 30]},
        "comfort":  {"next": ["idle", "happy"], "weight": [50, 50]},
        "pat":      {"next": ["idle", "happy", "love"], "weight": [30, 30, 40]},
        "poke":     {"next": ["idle", "angry", "surprise"], "weight": [30, 40, 30]},
    }

    # 情绪 → 权重加成映射
    EMOTION_WEIGHTS = {
        "happy":    {"happy": 4, "love": 3, "dance": 2},
        "sad":      {"sad": 4, "comfort": 3},
        "angry":    {"angry": 4, "calm_down": 3},
        "excited":  {"happy": 3, "dance": 3, "surprise": 2},
        "calm":     {"sleep": 3, "idle": 2, "think": 2},
        "neutral":  {},
    }

    def __init__(self):
        self.current_state = "idle"
        self.state_time = 0.0
        self.state_duration = random.uniform(8, 15)
        self.emotion = "neutral"  # neutral/happy/sad/angry/excited/calm
        self._last_transition = time.time()
        self._transition_cooldown = 0.3  # 状态切换最小间隔（秒）
        self._interaction_count = 0       # 连续互动计数（影响情绪衰减）
        self._emotion_decay_timer = 0.0   # 情绪衰减计时器

    def update(self, delta: float) -> str | None:
        """更新状态，返回新状态名或None"""
        self.state_time += delta
        self._emotion_decay_timer += delta

        # 情绪自然衰减：持续15秒后逐步回归neutral
        if self._emotion_decay_timer > 15.0 and self.emotion != "neutral":
            if random.random() < 0.02:  # ~2%概率每帧衰减
                old_emotion = self.emotion
                self.emotion = "neutral"
                self._interaction_count = 0
                self._emotion_decay_timer = 0.0

        if self.state_time < self.state_duration:
            return None

        # 防止状态切换过于频繁
        now = time.time()
        if now - self._last_transition < self._transition_cooldown:
            return None

        # 切换到下一个状态
        info = self.STATES.get(self.current_state, self.STATES["idle"])
        next_states = info["next"]
        weights = list(info["weight"])

        # 情绪影响权重
        emotion_mods = self.EMOTION_WEIGHTS.get(self.emotion, {})
        for i, state in enumerate(next_states):
            if state in emotion_mods:
                weights[i] *= emotion_mods[state]

        # 连续互动越多，越容易回归平静
        if self._interaction_count > 5:
            if "idle" in next_states:
                idx = next_states.index("idle")
                weights[idx] *= 2

        new_state = random.choices(next_states, weights=weights, k=1)[0]

        self.current_state = new_state
        self.state_time = 0.0
        self.state_duration = self._get_state_duration(new_state)
        self._last_transition = now

        return new_state

    def _get_state_duration(self, state: str) -> float:
        """各状态的持续时长范围（秒）"""
        durations = {
            "idle":      (8, 15),
            "wander":    (3, 6),
            "sleep":     (15, 30),
            "happy":     (3, 6),
            "stare":     (4, 8),
            "dance":     (4, 7),
            "talk":      (2, 5),
            "sad":       (5, 10),
            "listen":    (2, 4),
            "think":     (3, 6),
            "angry":     (3, 5),
            "surprise":  (2, 4),
            "love":      (4, 7),
            "calm_down": (3, 5),
            "comfort":   (3, 5),
            "pat":       (2, 4),
            "poke":      (2, 3),
        }
        lo, hi = durations.get(state, (5, 10))
        return random.uniform(lo, hi)

    def set_emotion(self, emotion: str):
        """设置情绪（neutral/happy/sad/angry/excited/calm）"""
        if self.emotion != emotion:
            self.emotion = emotion
            self._emotion_decay_timer = 0.0
            self._interaction_count = 0

    def trigger_state(self, state: str, duration: float = None):
        """强制触发状态（用于交互事件）"""
        self.current_state = state
        self.state_time = 0.0
        self.state_duration = duration or self._get_state_duration(state)
        self._last_transition = time.time()
        self._interaction_count += 1

    def on_click(self) -> str:
        """点击交互 → 随机触发 pat/poke/happy"""
        choices = ["pat", "poke", "happy", "surprise"]
        weights = [35, 25, 25, 15]
        state = random.choices(choices, weights=weights, k=1)[0]
        self.trigger_state(state)
        return state

    def on_double_click(self) -> str:
        """双击交互 → 触发 love"""
        self.trigger_state("love", duration=4.0)
        self.set_emotion("happy")
        return "love"

    def on_drag(self) -> str:
        """拖拽交互 → 触发 dizzy/surprise"""
        state = random.choice(["surprise", "angry", "stare"])
        self.trigger_state(state, duration=2.0)
        return state

    def on_interrupt(self) -> str:
        """被打断 → 触发 angry/surprise"""
        state = random.choice(["angry", "surprise", "stare"])
        self.trigger_state(state, duration=3.0)
        return state

    def on_pat(self) -> str:
        """摸头交互 → 触发 happy/love"""
        state = random.choice(["happy", "love", "pat"])
        self.trigger_state(state, duration=4.0)
        self.set_emotion("happy")
        return state

    def on_scare(self) -> str:
        """吓一跳 → 触发 surprise → angry"""
        self.trigger_state("surprise", duration=2.0)
        self.set_emotion("excited")
        return "surprise"
