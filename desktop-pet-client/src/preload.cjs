'use strict';

const { contextBridge, ipcRenderer } = require('electron');

// 仅系统产生的链接点击才能打开浏览器；网页脚本不能调用通用 shell API。
document.addEventListener('click', event => {
  if (!event.isTrusted || event.button !== 0) return;
  const anchor = event.target.closest('a[href]');
  if (!anchor) return;
  const url = new URL(anchor.href);
  if (!['https:', 'http:'].includes(url.protocol)) return;
  event.preventDefault();
  ipcRenderer.invoke('page:open-external', url.href).catch(error => console.error(error.message));
}, true);

// 不向页面暴露 ipcRenderer 或 Node；主进程还会按窗口身份检查每个调用。
const methods = [
  'ready', 'send', 'todos', 'todo_image', 'add_todo', 'update_todo', 'done_todo',
  'open_exploration_records', 'exploration_tasks', 'explore', 'fit', 'region',
  'drag_begin', 'drag_move', 'drag_end', 'clicked', 'screen_action',
  'refresh_buttons', 'click_button', 'copy_panel_content', 'exit_client',
  'refresh', 'window_control', 'window_gesture', 'window_gesture_end',
];
const api = Object.fromEntries(methods.map(method => [method,
  (...args) => ipcRenderer.invoke('pet:call', method, args)]));
contextBridge.exposeInMainWorld('desktopPetBridge', {
  api,
  login: {
    profile: () => ipcRenderer.invoke('login:profile'),
    fit: height => ipcRenderer.invoke('login:fit', height),
    discover: (server, password) => ipcRenderer.invoke('login:discover', server, password),
    connect: options => ipcRenderer.invoke('login:connect', options),
  },
  onCommand: callback => {
    const listener = (_event, message) => callback(message);
    ipcRenderer.on('pet:command', listener);
    return () => ipcRenderer.removeListener('pet:command', listener);
  },
  commandResult: (id, result, error) => ipcRenderer.send('pet:result', { id, result, error }),
});
