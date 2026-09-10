/*
 * 小忆桌宠 · Web 渲染层 (Task 1) — bridge.js
 *
 * 通过 QtWebEngine 的 QWebChannel 桥接 Python 与页面：
 *   - 服务端对象名：petBridge
 *   - Python -> JS：petBridge 信号 `command(payload: String)`，
 *     payload 为 JSON 字符串，type ∈ set_state / lip_sync / set_emotion /
 *     play_action / ping
 *   - JS -> Python：调用 petBridge.reportEvent(payload: String)
 *     （Java 的 QWebChannel 会原样转发给 Python 侧连接的信号）
 *
 * 纯浏览器（file:// 或 http://，无 qt.webChannelTransport）下不做任何
 * 崩溃操作，仅通过 URL 参数模拟指令（?state=happy&emotion=bored），
 * 便于本地验证。
 */
(function () {
  'use strict';

  const bridge = window.bridge = {
    connected: false,
    _obj: null,
    oncommand: null,
    _queue: [],
  };

  function dispatch(raw) {
    let cmd = raw;
    if (typeof raw === 'string') {
      try { cmd = JSON.parse(raw); } catch (e) {
        console.warn('[bridge] 非法指令 JSON:', raw);
        return;
      }
    }
    if (!cmd || typeof cmd !== 'object') return;
    if (typeof bridge.oncommand === 'function') {
      try { bridge.oncommand(cmd); } catch (e) {
        console.error('[bridge] 指令处理异常:', e);
      }
    } else {
      bridge._queue.push(cmd);
    }
  }

  bridge.reportEvent = function (payload) {
    if (!bridge.connected) return;
    try {
      bridge._obj.reportEvent(typeof payload === 'string' ? payload : JSON.stringify(payload));
    } catch (e) {
      console.warn('[bridge] reportEvent 失败:', e);
    }
  };

  bridge.setHandler = function (fn) {
    bridge.oncommand = typeof fn === 'function' ? fn : null;
    const queued = bridge._queue.splice(0);
    if (bridge.oncommand) {
      for (const cmd of queued) {
        try { bridge.oncommand(cmd); } catch (e) { console.error('[bridge]', e); }
      }
    }
  };

  function handleUrlParams() {
    const q = new URLSearchParams(location.search);
    if (q.get('state')) dispatch({ type: 'set_state', state: q.get('state') });
    if (q.get('emotion')) dispatch({ type: 'set_emotion', emotion: q.get('emotion') });
    if (q.get('lip_sync')) dispatch({ type: 'lip_sync', on: q.get('lip_sync') === '1' || q.get('lip_sync') === 'true' });
    if (q.get('action')) dispatch({ type: 'play_action', action: q.get('action') });
  }

  function startChannel() {
    if (typeof QWebChannel === 'undefined') {
      console.error('[bridge] QWebChannel 未加载');
      return;
    }
    try {
      new QWebChannel(qt.webChannelTransport, function (channel) {
        const server = channel.objects.petBridge;
        if (!server) {
          console.error('[bridge] WebChannel 服务端未暴露 petBridge 对象');
          return;
        }
        bridge._obj = server;
        bridge.connected = true;
        server.command.connect(function (raw) { dispatch(raw); });
        bridge.reportEvent({ type: 'bridge_connected' });
        console.log('[bridge] petBridge 已连接');
      });
    } catch (e) {
      console.error('[bridge] WebChannel 建立失败:', e);
    }
  }

  function setup() {
    const hasQt = (typeof qt !== 'undefined');
    const hasTransport = (typeof qt !== 'undefined' && qt.webChannelTransport);
    if (!hasQt || !hasTransport) {
      console.log('[bridge] 未检测到 QWebChannel（纯浏览器模式），使用 URL 参数指令');
      handleUrlParams();
      return;
    }
    if (typeof QWebChannel === 'undefined') {
      const s = document.createElement('script');
      s.src = 'qrc:///qtwebchannel/qwebchannel.js';
      s.onload = startChannel;
      s.onerror = function () {
        console.error('[bridge] qwebchannel.js 加载失败');
      };
      document.head.appendChild(s);
    } else {
      startChannel();
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', setup);
  } else {
    setup();
  }
})();
