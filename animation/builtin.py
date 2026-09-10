"""
内置后备动画模块 v2.0
程序化绘制猫咪动画帧 — 自然眨眼 + 平滑表情过渡 + 微动作
"""

import math
import random
from PySide6.QtGui import QImage, QPainter, QColor, QPen, QPainterPath
from PySide6.QtCore import QPointF, QRectF, Qt

# ── 配色 ──────────────────────────────────────────────
BODY_COLOR      = QColor(255, 190, 120)
BODY_SHADOW     = QColor(230, 165, 100)
OUTLINE_COLOR   = QColor(80, 60, 50)
EYE_WHITE_COLOR = QColor(255, 255, 255)
PUPIL_COLOR     = QColor(50, 50, 50)
NOSE_COLOR      = QColor(240, 140, 140)
MOUTH_OPEN_CLR  = QColor(200, 110, 110)
INNER_EAR_CLR   = QColor(255, 180, 160, 180)
CHEEK_CLR       = QColor(255, 160, 140, 100)


# ── 工具函数 ──────────────────────────────────────────
def _make_frame(w: int, h: int):
    img = QImage(w, h, QImage.Format_ARGB32)
    img.fill(QColor(0, 0, 0, 0))
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    return img, p


def _lerp(a: float, b: float, t: float) -> float:
    """线性插值"""
    return a + (b - a) * max(0.0, min(1.0, t))


def _ease_in_out(t: float) -> float:
    """缓入缓出"""
    return t * t * (3.0 - 2.0 * t)


def _draw_curve(painter: QPainter, x1: float, x2: float, y: float,
                ctrl_y: float, width: float = 2.0):
    """通用贝塞尔曲线（用于嘴巴、眼睛弧线等）"""
    path = QPainterPath()
    path.moveTo(x1, y)
    path.quadTo((x1 + x2) / 2, ctrl_y, x2, y)
    painter.setPen(QPen(OUTLINE_COLOR, width))
    painter.drawPath(path)


# ── 眼睛绘制（支持半闭状态）──────────────────────────
def _draw_eye_open(painter, cx: float, cy: float, scale: float = 1.0,
                   pupil_dy: float = 0.0):
    """睁开的眼睛（scale 控制大小，1.0=正常，1.3=大眼）"""
    r = 5.5 * scale
    painter.setBrush(EYE_WHITE_COLOR)
    painter.setPen(QPen(OUTLINE_COLOR, 1.2))
    painter.drawEllipse(QRectF(cx - r, cy - r, r * 2, r * 2))
    # 瞳孔
    pr = 2.5 * scale
    painter.setBrush(PUPIL_COLOR)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawEllipse(QRectF(cx - pr, cy - pr + pupil_dy, pr * 2, pr * 2))
    # 高光
    hr = 1.0 * scale
    painter.setBrush(QColor(255, 255, 255, 220))
    painter.drawEllipse(QRectF(cx - pr + 1, cy - pr - 0.5 + pupil_dy, hr, hr))


def _draw_eye_half(painter, cx: float, cy: float, close_t: float):
    """
    半闭眼睛：close_t ∈ [0,1]
    0 = 完全睁开，1 = 完全闭合（弧线）
    眼睛高度从 11px 线性缩小到 0，同时出现弧线
    """
    eye_h = 11.0 * (1.0 - close_t)  # 眼白高度
    if eye_h > 1.0:
        # 还有眼白
        painter.setBrush(EYE_WHITE_COLOR)
        painter.setPen(QPen(OUTLINE_COLOR, 1.0))
        painter.drawEllipse(QRectF(cx - 5.5, cy - eye_h / 2, 11.0, eye_h))
        # 瞳孔（只在眼白足够大时显示）
        if eye_h > 4.0:
            pr = 2.5 * (eye_h / 11.0)
            painter.setBrush(PUPIL_COLOR)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(QRectF(cx - pr, cy - pr, pr * 2, pr * 2))
    else:
        # 完全闭合 → 弧线
        _draw_curve(painter, cx - 5.5, cx + 5.5, cy, cy + 3.0, 1.5)


def _draw_eye_closed(painter, cx: float, cy: float):
    """闭眼弧线"""
    _draw_curve(painter, cx - 5.5, cx + 5.5, cy, cy + 3.0, 1.5)


def _draw_eye_happy(painter, cx: float, cy: float):
    """开心眼 ^^ 弧线"""
    _draw_curve(painter, cx - 6, cx + 6, cy, cy - 5.0, 1.8)


# ── 完整猫咪绘制 ──────────────────────────────────────
def _draw_cat(painter: QPainter, cx: float, cy: float, head_y: float,
              *, ear_droop: float = 0.0,
              eye_openness: float = 1.0,
              pupil_dy: float = 0.0,
              mouth: str = "none",
              mouth_open: float = 0.0,
              blink: bool = False,
              happy_eye: bool = False,
              wide_eye: bool = False):
    """
    绘制完整猫咪

    :param eye_openness: 0.0=全闭 0.5=半闭 1.0=全开（用于平滑眨眼）
    :param blink: 直接画闭眼弧线（覆盖 eye_openness）
    :param happy_eye: 开心^^眼
    :param wide_eye: 大眼模式
    """
    # ── 身体 ──
    painter.setBrush(BODY_COLOR)
    painter.setPen(QPen(OUTLINE_COLOR, 1.0))
    painter.drawEllipse(QRectF(cx - 25, cy - 20, 50, 45))
    # 肚皮高光
    painter.setBrush(QColor(255, 220, 190, 120))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawEllipse(QRectF(cx - 15, cy - 10, 30, 30))

    # ── 头 ──
    painter.setBrush(BODY_COLOR)
    painter.setPen(QPen(OUTLINE_COLOR, 1.0))
    painter.drawEllipse(QRectF(cx - 22, head_y - 20, 44, 40))

    # ── 耳朵 ──
    for side in (-1, 1):
        tip_x = cx + side * 25
        tip_y = head_y - 35 + ear_droop * side  # 用 side 使左右对称偏移
        painter.setBrush(BODY_COLOR)
        painter.setPen(QPen(OUTLINE_COLOR, 1.0))
        painter.drawPolygon([
            QPointF(cx + side * 18, head_y - 15),
            QPointF(tip_x, tip_y),
            QPointF(cx + side * 8, head_y - 18),
        ])
        # 内耳
        painter.setBrush(INNER_EAR_CLR)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawPolygon([
            QPointF(cx + side * 15, head_y - 16),
            QPointF(tip_x + side * (-2), tip_y + 5),
            QPointF(cx + side * 10, head_y - 18),
        ])

    # ── 眼睛 ──
    eye_y = head_y
    if blink:
        _draw_eye_closed(painter, cx - 10, eye_y)
        _draw_eye_closed(painter, cx + 10, eye_y)
    elif happy_eye:
        _draw_eye_happy(painter, cx - 10, eye_y)
        _draw_eye_happy(painter, cx + 10, eye_y)
    elif wide_eye:
        _draw_eye_open(painter, cx - 10, eye_y, scale=1.3, pupil_dy=pupil_dy)
        _draw_eye_open(painter, cx + 10, eye_y, scale=1.3, pupil_dy=pupil_dy)
    else:
        # 平滑眨眼：根据 eye_openness 绘制
        if eye_openness >= 0.95:
            _draw_eye_open(painter, cx - 10, eye_y, scale=1.0, pupil_dy=pupil_dy)
            _draw_eye_open(painter, cx + 10, eye_y, scale=1.0, pupil_dy=pupil_dy)
        elif eye_openness <= 0.05:
            _draw_eye_closed(painter, cx - 10, eye_y)
            _draw_eye_closed(painter, cx + 10, eye_y)
        else:
            _draw_eye_half(painter, cx - 10, eye_y, 1.0 - eye_openness)
            _draw_eye_half(painter, cx + 10, eye_y, 1.0 - eye_openness)

    # ── 鼻子 ──
    painter.setBrush(NOSE_COLOR)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawEllipse(QRectF(cx - 3, head_y + 8, 6, 4))

    # ── 嘴巴 ──
    if mouth == "smile":
        _draw_curve(painter, cx - 6, cx + 6, head_y + 13, head_y + 17, 1.5)
    elif mouth == "frown":
        _draw_curve(painter, cx - 6, cx + 6, head_y + 13, head_y + 9, 1.5)
    elif mouth == "line":
        painter.setPen(QPen(OUTLINE_COLOR, 1.5))
        painter.drawLine(QPointF(cx - 4, head_y + 13), QPointF(cx + 4, head_y + 13))
    elif mouth == "open":
        painter.setBrush(MOUTH_OPEN_CLR)
        painter.setPen(QPen(OUTLINE_COLOR, 1.0))
        w = mouth_open * 8
        h = mouth_open * 5
        painter.drawEllipse(QRectF(cx - w / 2, head_y + 10, w, h))

    # ── 腮红 ──
    painter.setBrush(CHEEK_CLR)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawEllipse(QRectF(cx - 24, head_y + 4, 8, 5))
    painter.drawEllipse(QRectF(cx + 16, head_y + 4, 8, 5))


# ── 思考气泡 ──────────────────────────────────────────
def _draw_thought_bubble(painter, cx, head_y, phase):
    bx = cx + 36 + 1.5 * math.sin(phase)
    by = head_y - 29 - 1.0 * math.sin(phase * 1.3)
    painter.setBrush(QColor(255, 255, 255, 235))
    painter.setPen(QPen(OUTLINE_COLOR, 1))
    painter.drawEllipse(QRectF(bx - 7, by - 7, 14, 14))
    painter.drawEllipse(QRectF(cx + 26, head_y - 20, 5, 5))
    painter.setBrush(PUPIL_COLOR)
    painter.setPen(Qt.PenStyle.NoPen)
    for dx, dy in ((-3.5, -1.0), (0.0, -2.0), (3.5, -1.0)):
        painter.drawEllipse(QRectF(bx + dx - 1.2, by + dy - 1.2, 2.4, 2.4))
    painter.setBrush(BODY_COLOR)
    painter.setPen(QPen(OUTLINE_COLOR, 1))


# ══════════════════════════════════════════════════════
#  眨眼调度器：模拟真实眨眼节奏
# ══════════════════════════════════════════════════════

class BlinkScheduler:
    """
    自然眨眼调度器
    - 正常间隔 2~5 秒随机
    - 偶尔双眨（间隔 0.15s）
    - 眨眼动作本身 0.15~0.2s（3~4帧过渡）
    """

    def __init__(self, fps: int = 12):
        self.fps = fps
        self._frame = 0
        self._next_blink = self._random_interval()
        self._blink_duration = 0  # 当前眨眼中剩余帧数
        self._blink_total = 0     # 本次眨眼总帧数
        self._double_blink = False

    def _random_interval(self) -> int:
        """下次眨眼的等待帧数（2~5秒）"""
        return random.randint(int(self.fps * 2), int(self.fps * 5))

    def _random_blink_frames(self) -> int:
        """眨眼动作帧数（0.15~0.25秒，3~4帧@12fps）"""
        return random.randint(3, 4)

    def update(self) -> float:
        """
        每帧调用，返回 eye_openness ∈ [0.0, 1.0]
        0.0 = 完全闭合，1.0 = 完全睁开
        """
        self._frame += 1

        # 非眨眼期：等待
        if self._blink_duration == 0:
            if self._frame >= self._next_blink:
                # 开始眨眼
                self._blink_total = self._random_blink_frames()
                self._blink_duration = self._blink_total
                self._double_blink = random.random() < 0.15  # 15%概率双眨
            return 1.0

        # 眨眼中：计算 openness
        progress = 1.0 - (self._blink_duration / self._blink_total)
        self._blink_duration -= 1

        # 闭合阶段 (0→0.5) 和睁开阶段 (0.5→1.0) 用 ease
        if progress < 0.5:
            close_t = _ease_in_out(progress * 2)
        else:
            close_t = _ease_in_out(2.0 - progress * 2)

        # 眨眼结束
        if self._blink_duration <= 0:
            if self._double_blink:
                # 双眨：快速再眨一次
                self._double_blink = False
                self._blink_total = 2
                self._blink_duration = 2
                self._next_blink = self._frame + int(self.fps * 0.5)
            else:
                self._next_blink = self._frame + self._random_interval()

        return 1.0 - close_t


# ══════════════════════════════════════════════════════
#  帧生成器（各状态）
# ══════════════════════════════════════════════════════

def generate_cat_idle_frames(width=128, height=128, num_frames=36) -> list[QImage]:
    """
    待机：36帧循环（3秒@12fps），带自然眨眼 + 缓慢呼吸 + 微摇摆
    """
    blink = BlinkScheduler(fps=12)
    frames = []

    for i in range(num_frames):
        img, p = _make_frame(width, height)
        t = i / num_frames * 2 * math.pi

        # 呼吸（慢）
        breath = math.sin(t) * 1.5
        # 微摇摆（左右）
        sway = math.sin(t * 0.7) * 1.2

        cx = width // 2 + sway
        cy = height // 2 + 10 + breath
        head_y = cy - 35

        openness = blink.update()

        _draw_cat(p, cx, cy, head_y, eye_openness=openness)
        p.end()
        frames.append(img)

    return frames


def generate_cat_happy_frames(width=128, height=128, num_frames=24) -> list[QImage]:
    """
    开心：24帧（2秒），^^眼 + 笑口 + 轻弹跳 + 偶尔眨眼
    """
    blink = BlinkScheduler(fps=12)
    frames = []

    for i in range(num_frames):
        img, p = _make_frame(width, height)
        t = i / num_frames * 2 * math.pi

        bounce = math.sin(t * 2) * 2.5
        cx = width // 2
        cy = height // 2 + 10 + bounce
        head_y = cy - 35

        # 偶尔眨眼（开心时眨眼少）
        openness = blink.update()
        is_blink = openness < 0.3

        _draw_cat(p, cx, cy, head_y,
                  happy_eye=not is_blink,
                  mouth="smile")
        p.end()
        frames.append(img)

    return frames


def generate_cat_sleep_frames(width=128, height=128, num_frames=36) -> list[QImage]:
    """
    睡眠：36帧（3秒），闭眼 + 缓慢呼吸 + 偶尔微动
    """
    frames = []
    for i in range(num_frames):
        img, p = _make_frame(width, height)
        t = i / num_frames * 2 * math.pi

        breath = math.sin(t) * 2.0
        # 偶尔小翻身
        sway = math.sin(t * 0.3) * 0.8

        cx = width // 2 + sway
        cy = height // 2 + 10 + breath
        head_y = cy - 35

        _draw_cat(p, cx, cy, head_y, eye_openness=0.0)
        p.end()
        frames.append(img)

    return frames


def generate_cat_talk_frames(width=128, height=128, num_frames=20) -> list[QImage]:
    """
    说话：20帧，嘴巴自然开合节奏 + 眨眼
    """
    blink = BlinkScheduler(fps=12)
    frames = []

    for i in range(num_frames):
        img, p = _make_frame(width, height)
        t = i / num_frames * 2 * math.pi

        breath = math.sin(t) * 1.5
        cx = width // 2
        cy = height // 2 + 10 + breath
        head_y = cy - 35

        openness = blink.update()

        # 说话时嘴巴节奏：不是简单交替，有大小变化
        mouth_pattern = [1.0, 0.6, 0.9, 0.3, 0.8, 0.5, 1.0, 0.4, 0.7, 0.2]
        m_open = mouth_pattern[i % len(mouth_pattern)]

        if m_open > 0.5:
            _draw_cat(p, cx, cy, head_y, eye_openness=openness,
                      mouth="open", mouth_open=m_open)
        else:
            _draw_cat(p, cx, cy, head_y, eye_openness=openness, mouth="line")

        p.end()
        frames.append(img)

    return frames


def generate_cat_sad_frames(width=128, height=128, num_frames=30) -> list[QImage]:
    """
    伤心：30帧（2.5秒），耳朵下垂 + 嘴角下弯 + 缓慢眨眼
    """
    blink = BlinkScheduler(fps=12)
    # 伤心时眨眼更慢（5~8秒间隔）
    blink._next_blink = blink._random_interval() + blink.fps * 3
    frames = []

    for i in range(num_frames):
        img, p = _make_frame(width, height)
        t = i / num_frames * 2 * math.pi

        breath = math.sin(t) * 1.0  # 呼吸更弱
        cx = width // 2
        cy = height // 2 + 12 + breath
        head_y = cy - 35

        openness = blink.update()

        _draw_cat(p, cx, cy, head_y,
                  ear_droop=7.0,
                  eye_openness=openness,
                  mouth="frown")
        p.end()
        frames.append(img)

    return frames


def generate_cat_listen_frames(width=128, height=128, num_frames=24) -> list[QImage]:
    """
    聆听：24帧（2秒），耳朵竖起 + 眼睛睁大 + 瞳孔微动
    """
    blink = BlinkScheduler(fps=12)
    frames = []

    for i in range(num_frames):
        img, p = _make_frame(width, height)
        t = i / num_frames * 2 * math.pi

        breath = math.sin(t) * 1.5
        cx = width // 2
        cy = height // 2 + 10 + breath
        head_y = cy - 35

        openness = blink.update()
        # 瞳孔跟随微动
        pupil_dy = math.sin(t * 1.5) * 1.5

        is_wide = openness > 0.9
        _draw_cat(p, cx, cy, head_y,
                  ear_droop=-5.0,
                  wide_eye=is_wide,
                  pupil_dy=pupil_dy if not is_wide else 0)
        p.end()
        frames.append(img)

    return frames


def generate_cat_think_frames(width=128, height=128, num_frames=36) -> list[QImage]:
    """
    思考：36帧（3秒），眼睛向上看 + 思考气泡 + 缓慢眨眼
    """
    blink = BlinkScheduler(fps=12)
    blink._next_blink = blink._random_interval() + blink.fps * 2
    frames = []

    for i in range(num_frames):
        img, p = _make_frame(width, height)
        t = i / num_frames * 2 * math.pi

        breath = math.sin(t) * 1.5
        cx = width // 2
        cy = height // 2 + 10 + breath
        head_y = cy - 35

        openness = blink.update()
        # 眼球向上看
        pupil_dy = -2.5

        _draw_cat(p, cx, cy, head_y,
                  eye_openness=openness,
                  pupil_dy=pupil_dy)
        _draw_thought_bubble(p, cx, head_y, t)
        p.end()
        frames.append(img)

    return frames


# ══════════════════════════════════════════════════════
#  新增：情绪 & 交互动效生成器
# ══════════════════════════════════════════════════════

# ── 辅助：爱心绘制 ──────────────────────────────────────
def _draw_heart(painter, cx: float, cy: float, size: float, alpha: int = 220):
    """绘制一颗爱心（参数化曲线）"""
    from PySide6.QtGui import QColor, QPen
    from PySide6.QtCore import Qt
    import math as _m

    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(255, 100, 120, alpha))
    path = QPainterPath()
    # 心形参数方程
    points = []
    for i in range(50):
        t = i / 49.0 * 2 * _m.pi
        x = size * 16 * _m.sin(t) ** 3
        y = -size * (13 * _m.cos(t) - 5 * _m.cos(2*t) - 2 * _m.cos(3*t) - _m.cos(4*t))
        points.append(QPointF(cx + x / 16, cy + y / 16))
    if points:
        path.moveTo(points[0])
        for pt in points[1:]:
            path.lineTo(pt)
        path.closeSubpath()
    painter.drawPath(path)


# ── 辅助：问号/叹号绘制 ─────────────────────────────────
def _draw_exclamation(painter, cx: float, cy: float, size: float = 1.0):
    """绘制感叹号"""
    from PySide6.QtGui import QColor, QPen, QFont
    from PySide6.QtCore import Qt
    painter.setPen(QPen(QColor(255, 80, 60), 2.5 * size))
    painter.setFont(QFont("Arial", int(16 * size), QFont.Weight.Bold))
    painter.drawText(QRectF(cx - 5, cy - 12, 10, 14), Qt.AlignmentFlag.AlignCenter, "!")


def _draw_question(painter, cx: float, cy: float, size: float = 1.0):
    """绘制问号"""
    from PySide6.QtGui import QColor, QPen, QFont
    from PySide6.QtCore import Qt
    painter.setPen(QPen(QColor(100, 150, 255), 2.5 * size))
    painter.setFont(QFont("Arial", int(16 * size), QFont.Weight.Bold))
    painter.drawText(QRectF(cx - 5, cy - 12, 10, 14), Qt.AlignmentFlag.AlignCenter, "?")


# ── 生气 ─────────────────────────────────────────────
def generate_cat_angry_frames(width=128, height=128, num_frames=24) -> list[QImage]:
    """
    生气：24帧（2秒），皱眉 + 耳朵后压 + 嘴巴抿紧 + 身体微颤 + 红脸
    """
    blink = BlinkScheduler(fps=12)
    frames = []

    for i in range(num_frames):
        img, p = _make_frame(width, height)
        t = i / num_frames * 2 * math.pi

        # 生气颤抖（高频微振）
        shake_x = math.sin(t * 6) * 1.5
        shake_y = math.sin(t * 8) * 0.8
        cx = width // 2 + shake_x
        cy = height // 2 + 10 + shake_y
        head_y = cy - 35

        openness = blink.update()

        # 画猫（耳朵后压、嘴巴抿紧）
        _draw_cat(p, cx, cy, head_y,
                  ear_droop=10.0,
                  eye_openness=openness,
                  mouth="frown")

        # 生气红晕（脸颊两侧）
        p.setBrush(QColor(255, 100, 80, 80))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QRectF(cx - 26, head_y + 2, 10, 6))
        p.drawEllipse(QRectF(cx + 16, head_y + 2, 10, 6))

        # 头顶怒气符号（十字）
        anger_x = cx + 18 + math.sin(t * 3) * 2
        anger_y = head_y - 8
        p.setPen(QPen(QColor(255, 60, 40), 2.0))
        p.drawLine(QPointF(anger_x - 4, anger_y), QPointF(anger_x + 4, anger_y))
        p.drawLine(QPointF(anger_x, anger_y - 4), QPointF(anger_x, anger_y + 4))

        p.end()
        frames.append(img)

    return frames


# ── 惊讶 ─────────────────────────────────────────────
def generate_cat_surprise_frames(width=128, height=128, num_frames=24) -> list[QImage]:
    """
    惊讶：24帧（2秒），大眼圆睁 + 耳朵竖起 + 身体弹起 + 感叹号
    """
    blink = BlinkScheduler(fps=12)
    frames = []

    for i in range(num_frames):
        img, p = _make_frame(width, height)
        t = i / num_frames * 2 * math.pi

        # 弹起效果（前半段跳起，后半段落下）
        if t < math.pi:
            bounce = math.sin(t) * 6
        else:
            bounce = math.sin(t) * 2
        cx = width // 2
        cy = height // 2 + 10 - bounce
        head_y = cy - 35

        openness = blink.update()

        # 画猫（大眼、耳朵竖起）
        _draw_cat(p, cx, cy, head_y,
                  ear_droop=-6.0,
                  wide_eye=True,
                  eye_openness=max(openness, 0.9),
                  mouth="open",
                  mouth_open=0.8)

        # 感叹号特效（弹起时显示）
        if bounce > 2:
            _draw_exclamation(p, cx + 22, head_y - 5, 1.0)

        p.end()
        frames.append(img)

    return frames


# ── 爱心 ─────────────────────────────────────────────
def generate_cat_love_frames(width=128, height=128, num_frames=30) -> list[QImage]:
    """
    爱心：30帧（2.5秒），开心眼 + 笑口 + 爱心飘出 + 身体摇摆
    """
    blink = BlinkScheduler(fps=12)
    frames = []

    for i in range(num_frames):
        img, p = _make_frame(width, height)
        t = i / num_frames * 2 * math.pi

        # 摇摆
        sway = math.sin(t * 1.5) * 3
        bounce = math.sin(t * 2) * 2
        cx = width // 2 + sway
        cy = height // 2 + 10 + bounce
        head_y = cy - 35

        openness = blink.update()

        # 画猫（开心眼 + 笑口）
        _draw_cat(p, cx, cy, head_y,
                  happy_eye=openness > 0.3,
                  mouth="smile")

        # 爱心特效（逐个飘出，大小递减）
        for j in range(3):
            phase = (t + j * 2.1) % (2 * math.pi)
            if phase < math.pi:
                hx = cx - 15 + j * 15 + math.sin(phase * 2) * 5
                hy = head_y - 10 - phase / math.pi * 30
                h_size = 2.5 - j * 0.5
                h_alpha = int(220 - phase / math.pi * 100)
                _draw_heart(p, hx, hy, h_size, h_alpha)

        p.end()
        frames.append(img)

    return frames


# ── 跳舞 ─────────────────────────────────────────────
def generate_cat_dance_frames(width=128, height=128, num_frames=36) -> list[QImage]:
    """
    跳舞：36帧（3秒），左右摇摆 + 弹跳 + 开心眼 + 节奏感
    """
    blink = BlinkScheduler(fps=12)
    frames = []

    for i in range(num_frames):
        img, p = _make_frame(width, height)
        t = i / num_frames * 2 * math.pi

        # 跳舞节奏：左右摇摆 + 弹跳
        sway = math.sin(t * 2) * 5
        bounce = abs(math.sin(t * 3)) * 4
        tilt = math.sin(t * 2) * 0.1  # 身体倾斜

        cx = width // 2 + sway
        cy = height // 2 + 10 - bounce
        head_y = cy - 35

        openness = blink.update()

        # 画猫（开心眼 + 笑口 + 身体微倾）
        _draw_cat(p, cx, cy, head_y,
                  happy_eye=openness > 0.3,
                  mouth="smile",
                  ear_droop=tilt * 20)

        # 音符特效
        for j in range(2):
            phase = (t + j * 3.14) % (2 * math.pi)
            if phase < math.pi * 1.5:
                nx = cx + 20 + j * 10 + math.sin(phase * 3) * 4
                ny = head_y - 5 - phase / math.pi * 25
                n_alpha = int(200 - phase / math.pi * 120)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(255, 200, 50, n_alpha))
                # 音符形状（小圆+尾巴）
                p.drawEllipse(QRectF(nx - 2, ny - 2, 4, 4))
                p.setPen(QPen(QColor(255, 200, 50, n_alpha), 1.2))
                p.drawLine(QPointF(nx + 2, ny), QPointF(nx + 2, ny - 8))

        p.end()
        frames.append(img)

    return frames


# ── 闲逛 ─────────────────────────────────────────────
def generate_cat_wander_frames(width=128, height=128, num_frames=24) -> list[QImage]:
    """
    闲逛：24帧（2秒），左右走动 + 东张西望 + 尾巴摇
    """
    blink = BlinkScheduler(fps=12)
    frames = []

    for i in range(num_frames):
        img, p = _make_frame(width, height)
        t = i / num_frames * 2 * math.pi

        # 走路：左右平移 + 上下颠簸
        walk_x = math.sin(t) * 12
        walk_bounce = abs(math.sin(t * 2)) * 2
        cx = width // 2 + walk_x
        cy = height // 2 + 10 + walk_bounce
        head_y = cy - 35

        openness = blink.update()

        # 画猫（正常表情 + 微摇摆）
        _draw_cat(p, cx, cy, head_y, eye_openness=openness)

        # 尾巴摇摆
        tail_x = cx - 18
        tail_y = cy + 5
        tail_angle = math.sin(t * 3) * 0.5
        p.setPen(QPen(OUTLINE_COLOR, 2.0))
        p.setBrush(BODY_COLOR)
        from PySide6.QtCore import QPointF
        tail_end_x = tail_x - 12 + math.sin(tail_angle) * 8
        tail_end_y = tail_y - 10 + math.cos(tail_angle) * 5
        path = QPainterPath()
        path.moveTo(tail_x, tail_y)
        path.quadTo(tail_x - 8, tail_y - 5, tail_end_x, tail_end_y)
        p.drawPath(path)

        p.end()
        frames.append(img)

    return frames


# ── 发呆 ─────────────────────────────────────────────
def generate_cat_stare_frames(width=128, height=128, num_frames=30) -> list[QImage]:
    """
    发呆：30帧（2.5秒），眼睛放空 + 微晃 + 偶尔眨眼
    """
    blink = BlinkScheduler(fps=12)
    blink._next_blink = blink._random_interval() + blink.fps * 2
    frames = []

    for i in range(num_frames):
        img, p = _make_frame(width, height)
        t = i / num_frames * 2 * math.pi

        # 微晃
        sway = math.sin(t * 0.5) * 1.0
        cx = width // 2 + sway
        cy = height // 2 + 10
        head_y = cy - 35

        openness = blink.update()

        # 画猫（放空眼：瞳孔偏大、位置居中）
        _draw_cat(p, cx, cy, head_y, eye_openness=openness)

        # 眼睛放空效果：瞳孔微微扩散
        if openness > 0.5:
            pr = 3.5  # 放大瞳孔
            p.setBrush(PUPIL_COLOR)
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(QRectF(cx - 12 - pr, cy - 2 - pr, pr * 2, pr * 2))
            p.drawEllipse(QRectF(cx + 12 - pr, cy - 2 - pr, pr * 2, pr * 2))

        p.end()
        frames.append(img)

    return frames


# ── 安慰 ─────────────────────────────────────────────
def generate_cat_comfort_frames(width=128, height=128, num_frames=24) -> list[QImage]:
    """
    安慰：24帧（2秒），温柔表情 + 靠近 + 轻轻摇
    """
    blink = BlinkScheduler(fps=12)
    frames = []

    for i in range(num_frames):
        img, p = _make_frame(width, height)
        t = i / num_frames * 2 * math.pi

        # 轻柔摇摆
        sway = math.sin(t) * 2
        cx = width // 2 + sway
        cy = height // 2 + 12
        head_y = cy - 35

        openness = blink.update()

        # 画猫（温柔：半闭眼 + 微笑）
        _draw_cat(p, cx, cy, head_y,
                  eye_openness=openness * 0.7,
                  mouth="smile",
                  ear_droop=2.0)

        # 温柔光晕
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 220, 180, 40))
        p.drawEllipse(QRectF(cx - 30, cy - 30, 60, 60))

        p.end()
        frames.append(img)

    return frames


# ── 冷静 ─────────────────────────────────────────────
def generate_cat_calm_down_frames(width=128, height=128, num_frames=24) -> list[QImage]:
    """
    冷静：24帧（2秒），深呼吸 + 放松 + 闭眼
    """
    frames = []

    for i in range(num_frames):
        img, p = _make_frame(width, height)
        t = i / num_frames * 2 * math.pi

        # 深呼吸（大起大伏）
        breath = math.sin(t) * 3.0
        cx = width // 2
        cy = height // 2 + 10 + breath
        head_y = cy - 35

        # 眼睛：前半闭，后半开
        openness = 0.3 + 0.7 * max(0, math.sin(t))

        _draw_cat(p, cx, cy, head_y,
                  eye_openness=openness,
                  mouth="line",
                  ear_droop=1.0)

        # "呼气"效果
        if math.sin(t) < -0.3:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(200, 220, 255, 30))
            p.drawEllipse(QRectF(cx - 20, head_y - 5, 40, 15))

        p.end()
        frames.append(img)

    return frames


# ── 被摸头 ─────────────────────────────────────────────
def generate_cat_pat_frames(width=128, height=128, num_frames=24) -> list[QImage]:
    """
    被摸头：24帧（2秒），享受表情 + 耳朵抖动 + 爱心
    """
    blink = BlinkScheduler(fps=12)
    frames = []

    for i in range(num_frames):
        img, p = _make_frame(width, height)
        t = i / num_frames * 2 * math.pi

        # 微微下沉（被摸的感觉）
        squish = math.sin(t * 2) * 1.5
        cx = width // 2
        cy = height // 2 + 10 + squish
        head_y = cy - 35

        openness = blink.update()

        # 画猫（享受：半闭眼 + 微笑 + 耳朵抖动）
        ear_twitch = math.sin(t * 4) * 3  # 耳朵快速抖动
        _draw_cat(p, cx, cy, head_y,
                  eye_openness=openness * 0.6,
                  mouth="smile",
                  ear_droop=-3 + ear_twitch)

        # 小爱心飘出
        for j in range(2):
            phase = (t + j * 1.57) % (2 * math.pi)
            if phase < math.pi:
                hx = cx - 10 + j * 20 + math.sin(phase * 2) * 3
                hy = head_y - 8 - phase / math.pi * 20
                _draw_heart(p, hx, hy, 1.8, int(200 - phase / math.pi * 100))

        p.end()
        frames.append(img)

    return frames


# ── 被戳 ─────────────────────────────────────────────
def generate_cat_poke_frames(width=128, height=128, num_frames=18) -> list[QImage]:
    """
    被戳：18帧（1.5秒），弹跳 + 惊讶 → 生气
    """
    blink = BlinkScheduler(fps=12)
    frames = []

    for i in range(num_frames):
        img, p = _make_frame(width, height)
        t = i / num_frames * 2 * math.pi

        # 弹跳（前半弹起，后半落下）
        if t < math.pi:
            bounce = math.sin(t) * 5
            mouth_state = "open"
            mouth_open = 0.9
        else:
            bounce = math.sin(t) * 2
            mouth_state = "frown"
            mouth_open = 0

        cx = width // 2
        cy = height // 2 + 10 - bounce
        head_y = cy - 35

        openness = blink.update()

        # 画猫（弹起时大眼，落下时生气）
        _draw_cat(p, cx, cy, head_y,
                  wide_eye=bounce > 2,
                  eye_openness=max(openness, 0.8 if bounce > 2 else 0.5),
                  mouth=mouth_state,
                  mouth_open=mouth_open,
                  ear_droop=0 if bounce > 2 else 5)

        # 弹起时问号
        if bounce > 3:
            _draw_question(p, cx + 20, head_y - 5, 0.9)

        p.end()
        frames.append(img)

    return frames


# ── 状态映射 ──────────────────────────────────────────
_STATE_GENERATORS = {
    "idle":       generate_cat_idle_frames,
    "happy":      generate_cat_happy_frames,
    "sleep":      generate_cat_sleep_frames,
    "talk":       generate_cat_talk_frames,
    "sad":        generate_cat_sad_frames,
    "listen":     generate_cat_listen_frames,
    "think":      generate_cat_think_frames,
    "angry":      generate_cat_angry_frames,
    "surprise":   generate_cat_surprise_frames,
    "love":       generate_cat_love_frames,
    "dance":      generate_cat_dance_frames,
    "wander":     generate_cat_wander_frames,
    "stare":      generate_cat_stare_frames,
    "comfort":    generate_cat_comfort_frames,
    "calm_down":  generate_cat_calm_down_frames,
    "pat":        generate_cat_pat_frames,
    "poke":       generate_cat_poke_frames,
}


def generate_cat_frames(state: str, width=128, height=128) -> list[QImage]:
    """生成指定状态的猫咪动画帧"""
    gen = _STATE_GENERATORS.get(state, generate_cat_idle_frames)
    return gen(width, height)
