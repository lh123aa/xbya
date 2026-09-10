# 小忆桌面宠物 v3.0 重构计划

> **目标：** 将现有pygame桌面宠物重构为PySide6 + 序列帧动画的专业级桌面宠物

**现状问题：**
1. pygame透明窗口效果差，不支持真正的点击穿透
2. 程序化绘制（QPainter/pygame.draw）效果粗糙
3. 状态只有6个，动画单调
4. pet_window.py 27KB过于臃肿

**重构方案：**
- UI框架：pygame → PySide6（原生透明窗口+鼠标穿透）
- 动画系统：程序化绘制 → PNG序列帧 + 分层合成
- 状态机：6状态 → 11状态 + 情绪系统
- 架构：单文件 → 模块化（animation/ai/ui分离）

**保留部分：**
- core/（app, config_manager, event_bus, plugin_loader）
- plugins/（ollama, faster_whisper, edge_tts等）
- services/（voice_service等）
- interfaces/（所有接口定义）

---

## 阶段一：基础框架（Day 1）

### Task 1: 安装PySide6 + 验证透明窗口

**文件：**
- Create: `ui/pyside_window.py`
- Modify: `requirements.txt`

**步骤：**

- [ ] **Step 1: 安装PySide6**

```bash
pip install PySide6
```

- [ ] **Step 2: 创建最小透明窗口测试**

```python
# ui/pyside_window.py
import sys
from PySide6.QtWidgets import QApplication, QWidget
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPainter, QColor

class TestWindow(QWidget):
    def __init__(self):
        super().__init__()
        # 无边框 + 透明 + 置顶
        self.setWindowFlags(
            Qt.FramelessWindowHint |
            Qt.WindowStaysOnTopHint |
            Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(300, 350)
        
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        # 画个半透明圆测试
        painter.setBrush(QColor(255, 180, 120, 200))
        painter.drawEllipse(50, 50, 200, 200)
        painter.end()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    win = TestWindow()
    win.show()
    sys.exit(app.exec())
```

- [ ] **Step 3: 运行验证透明效果**

```bash
python ui/pyside_window.py
```
预期：屏幕出现透明背景的橘色圆，可穿透点击

- [ ] **Step 4: 验证鼠标穿透**

在paintEvent中添加：
```python
def paintEvent(self, event):
    # 只在有内容的区域响应鼠标
    pass
```

- [ ] **Step 5: Commit**

```bash
git add ui/pyside_window.py requirements.txt
git commit -m "feat: add PySide6 transparent window test"
```

---

### Task 2: 创建动画素材目录结构

**文件：**
- Create: `resources/sprites/cat/idle/` (12帧)
- Create: `resources/sprites/cat/happy/` (12帧)
- Create: `resources/sprites/cat/sleep/` (10帧)
- Create: `resources/sprites/cat/talk/` (10帧)

**步骤：**

- [ ] **Step 1: 创建目录结构**

```bash
mkdir -p resources/sprites/cat/{idle,happy,sleep,talk,sad,listen,think}
```

- [ ] **Step 2: 准备素材来源**

方案A（推荐）：从免费素材站下载
- https://kenney.nl/assets/series/Tiny
- https://opengameart.org/art-search-advanced?field_art_type_tid%5B%5D=9
- itch.io 搜索 "cat sprite sheet"

方案B：用AI生成
- 使用Stable Diffusion生成猫咪各状态PNG序列

方案C：手动绘制
- Aseprite/Piskel绘制像素风猫咪

- [ ] **Step 3: 素材规格要求**

```
每帧尺寸：128x128 或 256x256
格式：PNG（透明背景）
命名：frame_001.png, frame_002.png, ...
帧率：idle 12fps, happy 15fps, sleep 8fps
```

- [ ] **Step 4: 创建素材清单文件**

```python
# resources/sprites/cat/manifest.json
{
    "name": "默认猫咪",
    "size": [128, 128],
    "animations": {
        "idle": {"frames": 12, "fps": 12, "loop": true},
        "happy": {"frames": 12, "fps": 15, "loop": true},
        "sleep": {"frames": 10, "fps": 8, "loop": true},
        "talk": {"frames": 10, "fps": 12, "loop": false},
        "sad": {"frames": 10, "fps": 10, "loop": true},
        "listen": {"frames": 8, "fps": 10, "loop": true},
        "think": {"frames": 8, "fps": 10, "loop": true}
    }
}
```

- [ ] **Step 5: Commit**

```bash
git add resources/
git commit -m "feat: add sprite directory structure and manifest"
```

---

## 阶段二：动画系统（Day 2-3）

### Task 3: 实现AnimationClip（动画片段）

**文件：**
- Create: `animation/clip.py`
- Test: `tests/test_animation.py`

**接口：**
```python
class AnimationClip:
    def __init__(self, path: str, fps: int = 12, loop: bool = True)
    def load(self) -> bool  # 加载PNG序列帧
    def get_frame(self, time: float) -> QImage  # 获取当前帧
    def get_duration(self) -> float  # 总时长(秒)
    def is_finished(self) -> bool
    def reset(self)
```

**步骤：**

- [ ] **Step 1: 编写测试**

```python
# tests/test_animation.py
import pytest
from animation.clip import AnimationClip

def test_load_animation():
    clip = AnimationClip("resources/sprites/cat/idle", fps=12, loop=True)
    assert clip.load() == True
    assert clip.get_duration() > 0

def test_get_frame():
    clip = AnimationClip("resources/sprites/cat/idle", fps=12, loop=True)
    clip.load()
    frame = clip.get_frame(0.0)
    assert frame is not None
    assert frame.width() > 0
```

- [ ] **Step 2: 实现AnimationClip**

```python
# animation/clip.py
import os
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtCore import Qt
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
            files = sorted([f for f in os.listdir(self.path) 
                          if f.endswith('.png')])
            
            for f in files:
                img = QImage(os.path.join(self.path, f))
                if not img.isNull():
                    self.frames.append(img)
            
            self._loaded = len(self.frames) > 0
            logger.info(f"加载动画: {self.path}, {len(self.frames)}帧")
            return self._loaded
            
        except Exception as e:
            logger.error(f"加载动画失败: {e}")
            return False
    
    def get_frame(self, time: float) -> QImage:
        """获取指定时间的帧"""
        if not self.frames:
            return QImage()
        
        frame_idx = int(time / self.frame_interval) % len(self.frames)
        
        if not self.loop and time >= self.get_duration():
            frame_idx = len(self.frames) - 1
        
        return self.frames[frame_idx]
    
    def get_duration(self) -> float:
        return len(self.frames) * self.frame_interval
    
    def is_finished(self, time: float) -> bool:
        if self.loop:
            return False
        return time >= self.get_duration()
    
    def reset(self):
        pass  # 重置由外部时间控制
```

- [ ] **Step 3: 运行测试**

```bash
pytest tests/test_animation.py -v
```

- [ ] **Step 4: Commit**

```bash
git add animation/clip.py tests/test_animation.py
git commit -m "feat: implement AnimationClip for PNG sequence loading"
```

---

### Task 4: 实现AnimationLayer（动画图层）

**文件：**
- Create: `animation/layer.py`

**接口：**
```python
class AnimationLayer:
    def __init__(self, name: str)
    def set_clip(self, clip: AnimationClip)
    def update(self, delta_time: float)
    def render(self, painter: QPainter, x: int, y: int)
    def is_active(self) -> bool
```

**步骤：**

- [ ] **Step 1: 实现AnimationLayer**

```python
# animation/layer.py
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
        self.alpha = 1.0  # 透明度 0-1
    
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
        
        # 设置透明度
        painter.setOpacity(self.alpha)
        painter.drawImage(x + self.offset_x, y + self.offset_y, frame)
        painter.setOpacity(1.0)
    
    def is_active(self) -> bool:
        return self.active
```

- [ ] **Step 2: Commit**

```bash
git add animation/layer.py
git commit -m "feat: implement AnimationLayer for compositing"
```

---

### Task 5: 实现AnimationController（动画控制器）

**文件：**
- Create: `animation/controller.py`
- Create: `animation/builtin.py`

**接口：**
```python
class AnimationController:
    def __init__(self, resource_path: str)
    def load_pet(self, pet_name: str) -> bool
    def set_state(self, state: str)  # idle/happy/sleep/talk...
    def update(self, delta_time: float)
    def composite(self) -> QImage  # 合成最终画面
    def get_size(self) -> tuple[int, int]
```

**步骤：**

- [ ] **Step 1: 实现AnimationController**

```python
# animation/controller.py
from PySide6.QtGui import QImage, QPainter, QColor
from PySide6.QtCore import Qt
from animation.clip import AnimationClip
from animation.layer import AnimationLayer
import json
import os
import logging

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
        self.current_state = "idle"
        self.manifest = {}
    
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
                clip = AnimationClip(
                    anim_path, 
                    fps=anim_info.get("fps", 12),
                    loop=anim_info.get("loop", True)
                )
                self.clips[anim_name] = clip
        
        # 默认加载idle
        self.set_state("idle")
        logger.info(f"加载宠物: {pet_name}, {len(self.clips)}个动画")
        return len(self.clips) > 0
    
    def set_state(self, state: str):
        """设置状态"""
        if state == self.current_state:
            return
        
        self.current_state = state
        
        # 状态 → 图层映射
        state_map = {
            "idle":     ("idle", None, None),
            "happy":    ("idle", "happy", "heart"),
            "sleep":    ("sleep", None, "zzz"),
            "talk":     ("idle", "talk", None),
            "sad":      ("idle", "sad", "tear"),
            "listen":   ("idle", None, "listen"),
            "think":    ("idle", None, "think"),
            "dance":    ("dance", None, None),
            "wander":   ("idle", None, None),
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
    
    def update(self, delta_time: float):
        """更新动画"""
        self.base_layer.update(delta_time)
        self.expr_layer.update(delta_time)
        self.overlay_layer.update(delta_time)
    
    def composite(self) -> QImage:
        """合成最终画面"""
        result = QImage(self.width, self.height, QImage.Format_ARGB32)
        result.fill(QColor(0, 0, 0, 0))  # 透明背景
        
        painter = QPainter(result)
        painter.setRenderHint(QPainter.Antialiasing)
        
        # 绘制顺序：base → expression → overlay
        self.base_layer.render(painter, 0, 0)
        self.expr_layer.render(painter, 0, 0)
        self.overlay_layer.render(painter, 0, 0)
        
        painter.end()
        return result
    
    def get_size(self) -> tuple[int, int]:
        return self.width, self.height
```

- [ ] **Step 2: 创建内置后备动画（builtin.py）**

```python
# animation/builtin.py
from PySide6.QtGui import QImage, QPainter, QColor, QBrush, QPen
from PySide6.QtCore import Qt, QPointF
import math

def generate_cat_idle_frames(width: int = 128, height: int = 128, num_frames: int = 12) -> list[QImage]:
    """生成猫咪待机动画帧（程序化绘制）"""
    frames = []
    
    for i in range(num_frames):
        img = QImage(width, height, QImage.Format_ARGB32)
        img.fill(QColor(0, 0, 0, 0))
        
        painter = QPainter(img)
        painter.setRenderHint(QPainter.Antialiasing)
        
        t = i / num_frames * 2 * math.pi
        breath = math.sin(t) * 2  # 呼吸偏移
        
        # 身体
        cx, cy = width // 2, height // 2 + 10 + breath
        painter.setBrush(QColor(255, 190, 120))
        painter.drawEllipse(cx - 25, cy - 20, 50, 45)
        
        # 头
        head_y = cy - 35
        painter.drawEllipse(cx - 22, head_y - 20, 44, 40)
        
        # 耳朵
        painter.drawPolygon([
            QPointF(cx - 18, head_y - 15),
            QPointF(cx - 25, head_y - 35),
            QPointF(cx - 8, head_y - 18)
        ])
        painter.drawPolygon([
            QPointF(cx + 18, head_y - 15),
            QPointF(cx + 25, head_y - 35),
            QPointF(cx + 8, head_y - 18)
        ])
        
        # 眼睛
        blink = (i == 5)  # 第5帧眨眼
        if blink:
            painter.setPen(QPen(QColor(80, 60, 50), 2))
            painter.drawLine(cx - 12, head_y, cx - 5, head_y)
            painter.drawLine(cx + 5, head_y, cx + 12, head_y)
        else:
            painter.setBrush(QColor(255, 255, 255))
            painter.drawEllipse(cx - 14, head_y - 5, 10, 10)
            painter.drawEllipse(cx + 4, head_y - 5, 10, 10)
            painter.setBrush(QColor(50, 50, 50))
            painter.drawEllipse(cx - 11, head_y - 2, 5, 5)
            painter.drawEllipse(cx + 7, head_y - 2, 5, 5)
        
        # 鼻子
        painter.setBrush(QColor(240, 140, 140))
        painter.drawEllipse(cx - 3, head_y + 8, 6, 4)
        
        painter.end()
        frames.append(img)
    
    return frames
```

- [ ] **Step 3: Commit**

```bash
git add animation/controller.py animation/builtin.py
git commit -m "feat: implement AnimationController with layer compositing"
```

---

## 阶段三：窗口系统（Day 4-5）

### Task 6: 实现PetWindow（主窗口）

**文件：**
- Create: `ui/pet_window.py` (新文件，覆盖旧的)
- Create: `ui/state_machine.py`

**接口：**
```python
class PetWindow(QWidget):
    def __init__(self)
    def set_app(self, app: XiaoyiApp)
    def show_bubble(self, text: str, duration: int = 3000)
    def set_state(self, state: str)
```

**步骤：**

- [ ] **Step 1: 实现状态机**

```python
# ui/state_machine.py
import random
import time

class PetStateMachine:
    STATES = {
        "idle":     {"next": ["wander", "sleep", "happy", "stare", "dance"], "weight": [15, 8, 8, 8, 10]},
        "wander":   {"next": ["idle"], "weight": [100]},
        "sleep":    {"next": ["idle"], "weight": [100]},
        "happy":    {"next": ["idle"], "weight": [100]},
        "stare":    {"next": ["idle"], "weight": [100]},
        "dance":    {"next": ["idle"], "weight": [100]},
        "talk":     {"next": ["idle"], "weight": [100]},
        "sad":      {"next": ["idle"], "weight": [100]},
        "listen":   {"next": ["idle"], "weight": [100]},
        "think":    {"next": ["idle"], "weight": [100]},
    }
    
    def __init__(self):
        self.current_state = "idle"
        self.state_time = 0.0
        self.state_duration = 10.0
        self.emotion = "neutral"  # neutral/happy/sad
    
    def update(self, delta: float) -> str | None:
        """更新状态，返回新状态名或None"""
        self.state_time += delta
        
        if self.state_time < self.state_duration:
            return None
        
        # 切换到下一个状态
        info = self.STATES.get(self.current_state, self.STATES["idle"])
        next_states = info["next"]
        weights = info["weight"]
        
        # 情绪影响权重
        if self.emotion == "happy":
            if "happy" in next_states:
                idx = next_states.index("happy")
                weights[idx] *= 3
        elif self.emotion == "sad":
            if "sad" in next_states:
                idx = next_states.index("sad")
                weights[idx] *= 3
        
        new_state = random.choices(next_states, weights=weights, k=1)[0]
        
        self.current_state = new_state
        self.state_time = 0.0
        self.state_duration = random.uniform(5, 15)
        
        return new_state
    
    def set_emotion(self, emotion: str):
        self.emotion = emotion
    
    def trigger_state(self, state: str):
        """强制触发状态"""
        self.current_state = state
        self.state_time = 0.0
        self.state_duration = 3.0
```

- [ ] **Step 2: 实现PetWindow**

```python
# ui/pet_window.py
from PySide6.QtWidgets import QWidget, QLabel
from PySide6.QtCore import Qt, QTimer, QPoint
from PySide6.QtGui import QPainter, QPixmap, QColor
from animation.controller import AnimationController
from ui.state_machine import PetStateMachine
import logging

logger = logging.getLogger(__name__)

class PetWindow(QWidget):
    def __init__(self):
        super().__init__()
        
        # 窗口属性
        self.setWindowFlags(
            Qt.FramelessWindowHint |
            Qt.WindowStaysOnTopHint |
            Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(300, 350)
        
        # 动画控制器
        self.anim_controller = AnimationController()
        
        # 状态机
        self.state_machine = PetStateMachine()
        
        # 位置
        self.pet_x = 100
        self.pet_y = 100
        
        # 拖拽
        self.dragging = False
        self.drag_offset = QPoint()
        
        # 气泡
        self.bubble_text = ""
        self.bubble_timer = 0
        
        # 主循环 (30fps)
        self.timer = QTimer()
        self.timer.timeout.connect(self.tick)
        self.timer.start(33)
        
        # 时间
        self.last_time = time.time()
        
        # 应用引用
        self.app = None
        
        logger.info("PetWindow初始化完成")
    
    def set_app(self, app):
        self.app = app
    
    def load_pet(self, pet_name: str):
        """加载宠物"""
        self.anim_controller.load_pet(pet_name)
        size = self.anim_controller.get_size()
        self.setFixedSize(size[0] + 100, size[1] + 100)  # 留边距给阴影和气泡
    
    def tick(self):
        """主循环"""
        now = time.time()
        delta = now - self.last_time
        self.last_time = now
        
        # 更新状态机
        new_state = self.state_machine.update(delta)
        if new_state:
            self.anim_controller.set_state(new_state)
            logger.debug(f"状态切换: {new_state}")
        
        # 更新动画
        self.anim_controller.update(delta)
        
        # 更新气泡
        if self.bubble_timer > 0:
            self.bubble_timer -= delta * 1000
            if self.bubble_timer <= 0:
                self.bubble_text = ""
        
        # 重绘
        self.update()
    
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        
        # 绘制阴影
        painter.setBrush(QColor(0, 0, 0, 40))
        painter.drawEllipse(
            self.pet_x + 20, 
            self.pet_y + self.anim_controller.get_size()[1] - 10,
            self.anim_controller.get_size()[0] - 40, 
            20
        )
        
        # 绘制宠物
        frame = self.anim_controller.composite()
        painter.drawImage(self.pet_x, self.pet_y, frame)
        
        # 绘制气泡
        if self.bubble_text:
            self._draw_bubble(painter)
        
        painter.end()
    
    def _draw_bubble(self, painter: QPainter):
        """绘制对话气泡"""
        # 简单气泡实现
        x = self.pet_x + 50
        y = self.pet_y - 40
        
        painter.setBrush(QColor(255, 255, 255, 220))
        painter.setPen(QColor(200, 200, 200))
        painter.drawRoundedRect(x, y, 150, 30, 10, 10)
        
        painter.setPen(QColor(50, 50, 50))
        painter.drawText(x + 10, y + 20, self.bubble_text[:20])
    
    def show_bubble(self, text: str, duration: int = 3000):
        """显示气泡"""
        self.bubble_text = text
        self.bubble_timer = duration
    
    def set_state(self, state: str):
        """外部设置状态"""
        self.state_machine.trigger_state(state)
    
    # ========== 鼠标事件 ==========
    
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.dragging = True
            self.drag_offset = event.pos()
            event.accept()
    
    def mouseMoveEvent(self, event):
        if self.dragging:
            new_pos = event.globalPosition().toPoint() - self.drag_offset
            self.move(new_pos)
            event.accept()
    
    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.dragging = False
            event.accept()
    
    def mouseDoubleClickEvent(self, event):
        """双击打开聊天"""
        if self.app:
            # TODO: 打开聊天窗口
            self.show_bubble("你好呀！", 2000)
    
    def contextMenuEvent(self, event):
        """右键菜单"""
        from PySide6.QtWidgets import QMenu
        menu = QMenu(self)
        menu.addAction("聊天", self._on_chat)
        menu.addAction("喂食", self._on_feed)
        menu.addSeparator()
        menu.addAction("退出", self.close)
        menu.exec(event.globalPos())
    
    def _on_chat(self):
        self.show_bubble("喵~", 1000)
    
    def _on_feed(self):
        self.show_bubble("好香！", 1000)
        self.set_state("happy")
```

- [ ] **Step 3: Commit**

```bash
git add ui/pet_window.py ui/state_machine.py
git commit -m "feat: implement PySide6 PetWindow with state machine"
```

---

### Task 7: 集成到现有App

**文件：**
- Modify: `run.py`
- Modify: `core/app.py`

**步骤：**

- [ ] **Step 1: 修改run.py**

```python
# run.py - 替换pygame启动为PySide6
import sys
from PySide6.QtWidgets import QApplication
from ui.pet_window import PetWindow
from core.app import XiaoyiApp

def main():
    app = QApplication(sys.argv)
    
    # 初始化后端
    xiaoyi = XiaoyiApp()
    xiaoyi.initialize()
    
    # 创建宠物窗口
    pet_window = PetWindow()
    pet_window.set_app(xiaoyi)
    pet_window.load_pet("cat")
    pet_window.show()
    
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 测试完整启动**

```bash
python run.py
```
预期：PySide6透明窗口显示猫咪，可拖拽，自动切换状态

- [ ] **Step 3: Commit**

```bash
git add run.py
git commit -m "feat: integrate PySide6 window with existing app"
```

---

## 阶段四：功能完善（Day 6-7）

### Task 8: 语音交互集成

**文件：**
- Modify: `ui/pet_window.py`
- Create: `ui/chat_bubble.py`

**步骤：**

- [ ] **Step 1: 实现语音按钮**

在PetWindow中添加：
```python
def mousePressEvent(self, event):
    if event.button() == Qt.LeftButton:
        # 单击 = 语音识别
        if not self.dragging:
            self._start_voice()
```

- [ ] **Step 2: 语音流程**

```python
def _start_voice(self):
    """开始语音识别"""
    self.set_state("listen")
    self.show_bubble("听我说...", 5000)
    
    # 调用ASR插件
    if self.app:
        threading.Thread(target=self._voice_thread, daemon=True).start()

def _voice_thread(self):
    """语音识别线程"""
    # 录音 + ASR
    text = self.app.transcribe_audio()
    
    if text:
        # 显示识别结果
        self.show_bubble(f"你说: {text}", 2000)
        
        # 调用LLM
        self.set_state("think")
        response = self.app.chat(text)
        
        # 显示回复
        self.set_state("talk")
        self.show_bubble(response, 3000)
        
        # TTS播放
        self.app.speak(response)
        
        # 回到idle
        self.set_state("idle")
```

- [ ] **Step 3: Commit**

```bash
git add ui/pet_window.py ui/chat_bubble.py
git commit -m "feat: integrate voice interaction"
```

---

### Task 9: 素材准备（并行任务）

**这是需要你手动完成的部分：**

**方案A：下载免费素材**
```
推荐来源：
1. https://kenney.nl/assets/series/Tiny - 像素风角色
2. https://opengameart.org - 开源游戏素材
3. itch.io "cat sprite" - 大量免费猫咪素材

下载后放入：resources/sprites/cat/
```

**方案B：AI生成**
```
用Stable Diffusion生成：
Prompt: "cute cat character sprite sheet, idle animation, 
        12 frames, pixel art, transparent background, 
        game asset, kawaii style"

生成后手动裁剪为单帧PNG
```

**方案C：用内置后备**
```
先用builtin.py的程序化绘制，后续替换为真实素材
```

---

## 阶段五：测试与优化（Day 8）

### Task 10: 性能优化

**检查项：**
- [ ] 帧率稳定30fps
- [ ] 内存占用 < 100MB
- [ ] CPU占用 < 5%
- [ ] 启动时间 < 3秒

**优化手段：**
- 懒加载：只预加载idle帧
- 脏帧缓存：状态不变时不重新合成
- 图片缓存：QPixmap缓存已加载的帧

---

### Task 11: 打包分发

**文件：**
- Create: `build.spec` (PyInstaller)

```bash
pip install pyinstaller
pyinstaller build.spec
```

---

## 文件结构（重构后）

```
xiaoyi-desktop-pet/
├── run.py                    # 启动入口
├── config.yaml               # 配置文件
├── requirements.txt          # 依赖
│
├── core/                     # 核心模块（保留）
│   ├── app.py
│   ├── config_manager.py
│   ├── event_bus.py
│   └── plugin_loader.py
│
├── interfaces/               # 接口定义（保留）
│   ├── asr.py
│   ├── llm.py
│   ├── tts.py
│   └── ...
│
├── plugins/                  # 插件实现（保留）
│   ├── asr/faster_whisper/
│   ├── llm/ollama/
│   └── tts/edge_tts/
│
├── services/                 # 服务层（保留）
│   ├── voice_service.py
│   └── ...
│
├── ui/                       # UI层（重写）
│   ├── pet_window.py         # 主窗口
│   ├── state_machine.py      # 状态机
│   ├── chat_bubble.py        # 气泡对话
│   └── tray.py               # 系统托盘
│
├── animation/                # 动画系统（新建）
│   ├── clip.py               # 动画片段
│   ├── layer.py              # 动画图层
│   ├── controller.py         # 动画控制器
│   └── builtin.py            # 内置后备动画
│
├── resources/                # 资源文件（新建）
│   └── sprites/
│       └── cat/
│           ├── manifest.json
│           ├── idle/         # 12帧
│           ├── happy/        # 12帧
│           ├── sleep/        # 10帧
│           └── talk/         # 10帧
│
└── tests/                    # 测试（保留）
    └── test_animation.py
```

---

## 依赖变化

```txt
# requirements.txt
# 保留
pyyaml
psutil
requests
faster-whisper
edge-tts
pyaudio
watchfiles

# 移除
pygame
PyOpenGL

# 新增
PySide6>=6.5.0
```

---

## 风险与应对

| 风险 | 应对 |
|------|------|
| PySide6学习曲线 | 参考DesktopPet-项目代码 |
| 素材制作耗时 | 先用builtin后备，后续替换 |
| 透明窗口兼容性 | PySide6原生支持，比pygame稳定 |
| 性能问题 | 懒加载+脏帧缓存 |

---

## 里程碑

| 里程碑 | 时间 | 交付物 |
|--------|------|--------|
| M1 | Day 1 | PySide6透明窗口可运行 |
| M2 | Day 3 | 动画系统完成，可播放序列帧 |
| M3 | Day 5 | 宠物窗口+状态机+拖拽 |
| M4 | Day 7 | 语音交互集成 |
| M5 | Day 8 | 测试通过+打包 |

---

## 执行方式

**推荐：Subagent-Driven**
- 每个Task独立执行
- Task间有明确依赖
- 可并行的Task（如素材准备）单独处理

**备选：Inline Execution**
- 按顺序执行所有Task
- 每完成一个Task提交审查
