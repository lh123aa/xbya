# -*- coding: utf-8 -*-
"""
ui.vrm_bridge — Python 侧桥接器（Task 2）

与 assets/vrm/js/bridge.js（Task 1）精确对齐的 QWebChannel 双向协议：

- 服务端对象名: ``petBridge``（经 QWebChannel.registerObject 注册）
- Python -> JS: 信号 ``command(payload: String)``，payload 为 JSON 字符串，
  ``{type: set_state|lip_sync|set_emotion|play_action|ping, ...}``
- JS -> Python: 槽 ``reportEvent(payload: String)``，事件 type ∈
  bridge_connected/app_ready/model_ready/state_changed/emotion_changed/
  lip_sync/action/body_part/pong（本桥解析 body_part -> bodyPartClicked、
  bridge_connected -> 就绪标记；其余事件透传日志，供 Task 4 使用）

防护（Task 1 遗留边界：加载失败后 JS 回调永不触发）：
- 未就绪缓存：bridge_connected 到达前 send 的指令进入 FIFO deque（上限 16，
  溢出丢弃最旧），就绪后按序重放（先重放、后发 bridgeReady）
- 就绪超时：构造后 30s 未就绪 -> bridgeError("bridge timeout") 并清空缓存；
  超时状态经公开属性 timed_out 暴露（迟到 ready 可恢复就绪，timed_out 保持 True，
  供 Task 4 区分"正常就绪"与"超时后恢复"）
"""

import json
import logging
from collections import deque
from typing import TYPE_CHECKING, Optional

from PySide6.QtCore import QObject, QTimer, Signal, Slot
from PySide6.QtWebChannel import QWebChannel

if TYPE_CHECKING:
    from PySide6.QtWebEngineWidgets import QWebEngineView

logger = logging.getLogger(__name__)

CACHE_MAX = 16
READY_TIMEOUT_MS = 30000

EVENT_BRIDGE_CONNECTED = "bridge_connected"
EVENT_BODY_PART = "body_part"
EVENT_DRAG_DELTA = "drag_delta"
EVENT_PONG = "pong"


class VrmBridge(QObject):
    """QWebChannel 双向桥接器：Python <-> 页面(window.bridge)"""

    # JS -> Python 事件
    bodyPartClicked = Signal(str)
    dragDelta = Signal(float, float)
    bridgeReady = Signal()
    bridgeError = Signal(str)

    # Python -> JS 指令（与 bridge.js `server.command.connect(...)` 对齐）
    command = Signal(str)

    def __init__(self, view: "QWebEngineView", timeout_ms: int = READY_TIMEOUT_MS):
        super().__init__()
        self._view = view
        self._ready = False
        self.timed_out = False
        self._timeout_ms = timeout_ms
        self._cache: deque = deque(maxlen=CACHE_MAX)

        self._channel = QWebChannel(self)
        self._channel.registerObject("petBridge", self)
        view.page().setWebChannel(self._channel)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(timeout_ms)
        self._timer.timeout.connect(self._on_timeout)
        self._timer.start()

    # ------------------------------------------------------------------ #
    # Python -> JS
    # ------------------------------------------------------------------ #

    def send(self, command: str, **payload) -> None:
        """发送指令；未就绪时缓存（FIFO 上限 16），就绪后按序重放"""
        if not command:
            raise ValueError("command 不能为空")
        msg = json.dumps({"type": command, **payload}, ensure_ascii=False)
        if self._ready:
            self.command.emit(msg)
            return
        if len(self._cache) >= self._cache.maxlen:
            logger.warning(
                "[vrm_bridge] 指令缓存已满(%d)，丢弃最旧指令: %s",
                CACHE_MAX, self._cache[0])
        self._cache.append(msg)

    def set_state(self, state: str) -> None:
        """7 状态: idle/talk/think/listen/sleep/happy/sad"""
        self.send("set_state", state=state)

    def set_emotion(self, name: str) -> None:
        """情绪: happy/normal/bored/lonely"""
        self.send("set_emotion", emotion=name)

    def lip_sync(self, on: bool) -> None:
        """口型同步开关"""
        self.send("lip_sync", on=bool(on))

    def play_action(self, action: str) -> None:
        """程序化动作: hello/pet/feed/hit_tail"""
        self.send("play_action", action=action)

    def ping(self) -> None:
        """心跳：JS 侧回 pong（就绪校验/联调排查）"""
        self.send("ping")

    # ------------------------------------------------------------------ #
    # JS -> Python
    # ------------------------------------------------------------------ #

    @Slot(str)
    def reportEvent(self, payload: str) -> None:
        """bridge.js 调用的服务端方法：解析 JSON 事件并分发"""
        if not isinstance(payload, str):
            logger.warning("[vrm_bridge] reportEvent 载荷非字符串: %r", payload)
            return
        try:
            data = json.loads(payload)
        except (ValueError, TypeError):
            logger.warning("[vrm_bridge] reportEvent 非法 JSON: %r", payload)
            return
        if not isinstance(data, dict):
            logger.warning("[vrm_bridge] reportEvent 载荷应为对象: %r", payload)
            return
        op = data.pop("type", None)
        self._on_channel_message(op, data)

    def _on_channel_message(self, op: Optional[str], data: dict) -> None:
        """分发 JS 上报事件

        Args:
            op: 事件 type（如 body_part/bridge_connected）
            data: 其余载荷（不含 type）
        """
        if op == EVENT_BRIDGE_CONNECTED:
            self._mark_ready()
        elif op == EVENT_BODY_PART:
            part = data.get("part") if isinstance(data, dict) else None
            if part:
                self.bodyPartClicked.emit(str(part))
        elif op == EVENT_DRAG_DELTA:
            try:
                dx = float(data.get("dx", 0.0))
                dy = float(data.get("dy", 0.0))
            except (TypeError, ValueError):
                logger.warning("[vrm_bridge] drag_delta 载荷非法: %r", data)
            else:
                self.dragDelta.emit(dx, dy)
        elif op == EVENT_PONG:
            logger.debug("[vrm_bridge] pong: %s", data)
        elif op is None:
            logger.warning("[vrm_bridge] 事件缺少 type 字段: %r", data)
        else:
            # app_ready/model_ready/state_changed/emotion_changed/lip_sync/action
            # 均为状态回执，Task 4 状态同步时再消费
            logger.debug("[vrm_bridge] 未处理事件 type=%s: %s", op, data)

    # ------------------------------------------------------------------ #
    # 内部
    # ------------------------------------------------------------------ #

    def _mark_ready(self) -> None:
        if self._ready:
            return
        self._timer.stop()
        self._ready = True
        # 先重放缓存，再发 bridgeReady：槽内（如 Task 4）调 send() 时
        # ready 已为 True 会立即发射，保证 JS 收到顺序 = 缓存重放在前、
        # 槽内指令在后，不插队
        self._flush_cache()
        if self.timed_out:
            logger.info("[vrm_bridge] 超时后收到 bridge_connected，恢复就绪"
                        "（recovered: timed_out=True）")
        self.bridgeReady.emit()

    def _flush_cache(self) -> None:
        while self._cache:
            self.command.emit(self._cache.popleft())

    def _on_timeout(self) -> None:
        """构造后 30s 未收到 bridge_connected -> 超时防护"""
        if self._ready:
            return
        self._timer.stop()
        self.timed_out = True
        self._cache.clear()
        logger.error(
            "[vrm_bridge] bridge 未在 %d ms 内就绪，超时（cache 已清空）", self._timeout_ms)
        self.bridgeError.emit("bridge timeout")

    # ------------------------------------------------------------------ #
    @property
    def is_ready(self) -> bool:
        return self._ready

    @property
    def channel(self) -> QWebChannel:
        return self._channel
