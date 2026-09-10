"""
动画图层模块
管理单个动画片段的播放状态与渲染
"""

from PySide6.QtGui import QPainter
from animation.clip import AnimationClip


class AnimationLayer:
    def __init__(self, name: str):
        self.name = name
        self.clip: AnimationClip | None = None
        self.time = 0.0
        self.active = False
        self.offset_x = 0
        self.offset_y = 0
        self.alpha = 1.0

    def set_clip(self, clip: AnimationClip):
        self.clip = clip
        self.time = 0.0
        self.active = True
        if clip:
            clip.load()

    def update(self, delta_time: float):
        if not self.active or not self.clip:
            return

        self.time += delta_time

        if self.clip.is_finished(self.time):
            self.active = False

    def render(self, painter: QPainter, x: int, y: int):
        if not self.active or not self.clip:
            return

        frame = self.clip.get_frame(self.time)
        if frame.isNull():
            return

        painter.setOpacity(self.alpha)
        painter.drawImage(x + self.offset_x, y + self.offset_y, frame)
        painter.setOpacity(1.0)

    def is_active(self) -> bool:
        return self.active
