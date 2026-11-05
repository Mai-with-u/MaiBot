/* 仅注入本地页面；远端 WebUI 不获得桌面能力或通用命令入口。 */
(() => {
  'use strict';
  const invoke = (command, payload) => window.__TAURI_INTERNALS__.invoke(command, payload);
  const methods = ['ready', 'send', 'todos', 'todo_image', 'add_todo', 'update_todo', 'done_todo',
    'open_exploration_records', 'exploration_tasks', 'explore', 'fit', 'region',
    'drag_begin', 'drag_move', 'drag_end', 'clicked', 'screen_action', 'refresh_buttons',
    'click_button', 'copy_panel_content', 'exit_client', 'refresh', 'window_control',
    'window_gesture', 'window_gesture_end'];
  let receiver;
  window.__desktopPetDispatch = message => {
    if (!receiver) throw new Error('桌宠页面尚未就绪');
    receiver(message);
  };
  window.desktopPetBridge = Object.freeze({
    api: Object.freeze(Object.fromEntries(methods.map(method => [method,
      (...args) => invoke('pet_call', { method, args })]))),
    login: Object.freeze(Object.fromEntries(['profile', 'fit', 'discover', 'connect', 'cancel'].map(method => [method,
      (...args) => invoke('login_call', { method, args })]))),
    onCommand(callback) { receiver = callback; return () => { receiver = null; }; },
    commandResult: (id, result, error) => invoke('page_result', { id, result: result ?? null, error: error ?? null }),
  });
  document.addEventListener('click', event => {
    if (!event.isTrusted || event.button !== 0) return;
    const anchor = event.target.closest('a[href]');
    if (!anchor || !/^https?:/.test(anchor.href)) return;
    event.preventDefault();
    invoke('page_open_external', { value: anchor.href }).catch(console.error);
  }, true);
})();
