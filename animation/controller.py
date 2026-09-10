"""
动画控制器模块 v2.0
管理宠物动画状态与三层合成（基础层/表情层/特效层）
新增：平滑过渡、表情渐变、交互反馈
"""

import json
import os
import logging
import time
from typing import Optional
from PySide6.QtGui import QImage, QPainter, QColor
from animation.clip import AnimationClip
from animation.layer import AnimationLayer

logger = logging.getLogger(__name__)


class AnimationController:
    def __init__(self, resource_path: str = "resources/sprites"):
        self.resource_path = resource_path
        self.pet_name = ""
        self.width = 128
        self.height = 128

        # 三层合成
        self.base_layer = AnimationLayer("base")      # 身体动作
        self.expr_layer = AnimationLayer("expression")  # 表情
        self.overlay_layer = AnimationLayer("overlay")  # 特效（爱心/zozz）

        # 动画映射
        self.clips: dict[str, AnimationClip] = {}
        self.current_state = ""
        self.manifest = {}

        # 脏帧缓存：状态/动画时间不变时跳过重复合成
        self._cached_frame: Optional[QImage] = None  # 缓存的合成结果
        self._cache_key: Optional[tuple] = None  # (base_time, expr_time, overlay_time, state)

        # 平滑过渡系统
        self._prev_state = ""            # 上一个状态（用于过渡）
        self._transition_start = 0.0     # 过渡开始时间
        self._transition_duration = 0.3  # 过渡时长（秒）
        self._transition_alpha = 0.0     # 当前过渡透明度（0=旧状态, 1=新状态）
        self._transition_frame: Optional[QImage] = None  # 旧状态最后一帧（用于混合）

    def load_pet(self, pet_name: str) -> bool:
        """加载宠物资源"""
        self.pet_name = pet_name
        pet_path = os.path.join(self.resource_path, pet_name)

        # 读取manifest
        manifest_path = os.path.join(pet_path, "manifest.json")
        if os.path.exists(manifest_path):
            with open(manifest_path, 'r', encoding='utf-8') as f:
                self.manifest = json.load(f)
                self.width = self.manifest.get("size", [128, 128])[0]
                self.height = self.manifest.get("size", [128, 128])[1]

        # 加载各状态动画
        for anim_name, anim_info in self.manifest.get("animations", {}).items():
            anim_path = os.path.join(pet_path, anim_name)
            if os.path.isdir(anim_path):
                # 内置兜底：目录为空时程序化生成帧并持久化（仅生成一次）
                png_files = [f for f in os.listdir(anim_path) if f.endswith(".png")]
                if not png_files:
                    try:
                        from animation.builtin import generate_cat_frames
                        frames = generate_cat_frames(anim_name)
                        for idx, frame in enumerate(frames, 1):
                            frame.save(os.path.join(anim_path, f"frame_{idx:03d}.png"))
                        logger.info(f"内置绘制生成: {anim_name} {len(frames)}帧")
                    except Exception as e:
                        logger.warning(f"内置绘制失败: {anim_name} - {e}")

                clip = AnimationClip(
                    anim_path,
                    fps=anim_info.get("fps", 12),
                    loop=anim_info.get("loop", True)
                )
                self.clips[anim_name] = clip

        # 默认加载idle
        self.set_state("idle")
        self._invalidate_cache()
        logger.info(f"加载宠物: {pet_name}, {len(self.clips)}个动画")
        return len(self.clips) > 0

    def set_state(self, state: str):
        """设置状态（带平滑过渡）"""
        # 情绪名 → 动效状态名映射（emotion_analyzer 输出名 → controller 内部名）
        _EMOTION_ALIAS = {
            "calm": "calm_down",
            "think": "think",
        }
        state = _EMOTION_ALIAS.get(state, state)

        if state == self.current_state:
            return

        # 保存旧状态最后一帧用于过渡混合
        if self.current_state and self._cached_frame is not None:
            self._transition_frame = self._copy_image(self._cached_frame)
            self._prev_state = self.current_state
            self._transition_start = time.time()
        else:
            self._transition_frame = None

        self.current_state = state

        # 状态 → 图层映射（base=身体动作, expr=表情, overlay=特效）
        state_map = {
            "idle":       ("idle", None, None),
            "happy":      ("idle", "happy", "heart"),
            "sleep":      ("sleep", None, "zzz"),
            "talk":       ("idle", "talk", None),
            "sad":        ("idle", "sad", "tear"),
            "listen":     ("idle", None, "listen"),
            "think":      ("idle", None, "think"),
            "dance":      ("dance", None, None),
            "wander":     ("wander", None, None),
            "stare":      ("stare", None, None),
            # 新增情绪状态
            "angry":      ("idle", "angry", None),
            "surprise":   ("idle", "surprise", None),
            "love":       ("idle", "happy", "heart"),
            "calm_down":  ("idle", None, None),
            "comfort":    ("idle", None, None),
            "pat":        ("idle", "happy", "heart"),
            "poke":       ("idle", "surprise", None),
        }

        base, expr, overlay = state_map.get(state, ("idle", None, None))

        if base and base in self.clips:
            self.base_layer.set_clip(self.clips[base])

        if expr and expr in self.clips:
            self.expr_layer.set_clip(self.clips[expr])
        else:
            self.expr_layer.active = False

        if overlay and overlay in self.clips:
            self.overlay_layer.set_clip(self.clips[overlay])
        else:
            self.overlay_layer.active = False

        self._invalidate_cache()

    @staticmethod
    def _copy_image(img: QImage) -> QImage:
        """深拷贝QImage（避免引用问题）"""
        return img.copy() if img and not img.isNull() else None

    def update(self, delta_time: float):
        """更新动画"""
        self.base_layer.update(delta_time)
        self.expr_layer.update(delta_time)
        self.overlay_layer.update(delta_time)
        self._invalidate_cache()

    def _invalidate_cache(self):
        """使脏帧缓存失效"""
        self._cached_frame = None
        self._cache_key = None

    def composite(self) -> QImage:
        """合成最终画面（带脏帧缓存 + 平滑过渡）"""
        key = (self.base_layer.time, self.expr_layer.time,
               self.overlay_layer.time, self.current_state)
        if self._cached_frame is not None and key == self._cache_key:
            return self._cached_frame

        result = QImage(self.width, self.height, QImage.Format_ARGB32)
        result.fill(QColor(0, 0, 0, 0))  # 透明背景

        painter = QPainter(result)
        painter.setRenderHint(QPainter.Antialiasing)

        # 绘制顺序：base → expression → overlay
        self.base_layer.render(painter, 0, 0)
        self.expr_layer.render(painter, 0, 0)
        self.overlay_layer.render(painter, 0, 0)

        painter.end()

        # 平滑过渡：旧状态帧与新状态帧混合
        now = time.time()
        elapsed = now - self._transition_start
        if self._transition_frame is not None and elapsed < self._transition_duration:
            # 过渡中：计算混合比例（ease-in-out）
            t = elapsed / self._transition_duration
            alpha = t * t * (3.0 - 2.0 * t)  # smoothstep

            # 创建过渡帧：旧帧 * (1-alpha) + 新帧 * alpha
            blended = QImage(self.width, self.height, QImage.Format_ARGB32)
            blended.fill(QColor(0, 0, 0, 0))
            bp = QPainter(blended)
            bp.setRenderHint(QPainter.Antialiasing)
            bp.setOpacity(1.0 - alpha)
            bp.drawImage(0, 0, self._transition_frame)
            bp.setOpacity(alpha)
            bp.drawImage(0, 0, result)
            bp.end()
            result = blended
        elif self._transition_frame is not None:
            # 过渡结束：清除旧帧
            self._transition_frame = None

        self._cached_frame = result
        self._cache_key = key
        return self._cached_frame

    def get_size(self) -> tuple[int, int]:
        return self.width, self.height
