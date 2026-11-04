'use strict';

const { contextBridge, ipcRenderer } = require('electron');

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
