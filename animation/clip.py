"""
动画片段模块
加载PNG序列帧，基于时间获取当前帧
"""

import os
from PySide6.QtGui import QImage
import logging

logger = logging.getLogger(__name__)


class AnimationClip:
    def __init__(self, path: str, fps: int = 12, loop: bool = True):
        self.path = path
        self.fps = fps
        self.loop = loop
        self.frames: list[QImage] = []
        self.frame_interval = 1.0 / fps
        self._loaded = False

    def load(self) -> bool:
        """加载PNG序列帧"""
        if self._loaded:
            return True

        try:
            if not os.path.isdir(self.path):
                return False

            files = sorted([f for f in os.listdir(self.path)
                          if f.endswith('.png')])

            for f in files:
                img = QImage(os.path.join(self.path, f))
                if not img.isNull():
                    self.frames.append(img)

            self._loaded = len(self.frames) > 0
            logger.info(f"加载动画: {self.path}, {len(self.frames)}帧")
            return self._loaded

        except OSError as e:
            logger.error(f"加载动画失败: {e}")
            return False

    def get_frame(self, time: float) -> QImage:
        """获取指定时间的帧"""
        if not self.frames:
            return QImage()

        if time < 0:
            logger.warning(f"get_frame called with negative time {time}, clamping to 0")
            time = 0.0

        frame_idx = int(time / self.frame_interval) % len(self.frames)

        if not self.loop and time >= self.get_duration():
            frame_idx = len(self.frames) - 1

        return self.frames[frame_idx]

    def get_duration(self) -> float:
        """总时长(秒)"""
        return len(self.frames) * self.frame_interval

    def is_finished(self, time: float) -> bool:
        """是否播放结束"""
        if self.loop:
            return False
        return time >= self.get_duration()

    def reset(self):
        """重置(外部时间控制，此处为空操作)"""
        pass
