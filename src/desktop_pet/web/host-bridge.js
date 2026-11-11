/* 独立客户端与本体内嵌窗口共用页面；没有桌面桥时由 pywebview 正常初始化。 */
(() => {
  'use strict';
  const bridge = window.desktopPetBridge;
  if (!bridge) return;
  window.pywebview = { api: bridge.api };
  const received = new Set([
    'reply', 'notice', 'thinking_caption', 'thinking', 'idle', 'failed', 'accepted',
    'buttons', 'button_result', 'screen_state', 'exploration_state', 'exploration_result', 'exploration_tasks', 'connection',
  ]);
  bridge.onCommand(async ({ id, command }) => {
    try {
      let result = {};
      if (command.type === 'skin') {
        let timer;
        try {
          result = await Promise.race([
            window.petSkin.install(command.skin),
            new Promise((_, reject) => { timer = setTimeout(() => {
              window.petSkin.cancelInstall(); reject(new Error('皮肤资源解码超时'));
            }, 10000); }),
          ]);
        } finally { clearTimeout(timer); }
      } else if (command.type === 'animation') {
        result = window.petSkin.command(command.action, command.args, command.owner);
      } else if (command.type === 'visibility') {
        window.petSkin.visibility(command.visible);
      } else if (command.type === 'todo_refresh') {
        await window.pet.refreshTodos();
      } else if (command.type === 'exploration_tasks' && command.request_id.startsWith('records:')) {
        window.records.receive(command);
      } else if (received.has(command.type)) {
        window.pet.receive(command);
      } else {
        throw new Error(`未知桌宠页面指令: ${command.type}`);
      }
      bridge.commandResult(id, result);
    } catch (error) { bridge.commandResult(id, null, String(error.message || error)); }
  });
  window.addEventListener('DOMContentLoaded', () => {
    // 支持原生标题栏拖动的客户端可接管标题区域；按钮仍由现有页面处理。
    const title = document.getElementById('title-drag');
    if (title) title.style.setProperty('-webkit-app-region', 'drag');
    for (const edge of document.querySelectorAll('.resize-edge')) {
      edge.addEventListener('pointerdown', event => {
        if (event.button === 0) edge.setPointerCapture(event.pointerId);
      });
    }
    const endGesture = () => bridge.api.window_gesture_end().catch(() => {});
    if (title) {
      window.addEventListener('pointerup', endGesture);
      window.addEventListener('pointercancel', endGesture);
      window.addEventListener('blur', endGesture);
    }
    window.dispatchEvent(new Event('pywebviewready'));
  }, { once: true });
})();
