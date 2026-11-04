'use strict';

const { createHash, randomUUID } = require('node:crypto');
const fs = require('node:fs/promises');
const path = require('node:path');
const electron = require('electron');
const { app, BrowserWindow, dialog, ipcMain, Menu, nativeImage, protocol, screen, session, Tray } = electron;
const { discoverBots, normalizeServer, RemoteConnection } = require('./connection.cjs');
const { createNativeCapabilities } = require('./native.cjs');
const { installLocalPages } = require('./local-pages.cjs');
const { PetWindowController, RecordsWindowController } = require('./windows.cjs');

// 原生 Wayland 不提供桌宠所需的全局定位；Linux 使用 X11 / XWayland。
if (process.platform === 'linux') app.commandLine.appendSwitch('ozone-platform', 'x11');
if (app.commandLine.hasSwitch('user-data-dir')) app.setPath('userData', path.resolve(app.commandLine.getSwitchValue('user-data-dir')));
protocol.registerSchemesAsPrivileged([{ scheme: 'petapp', privileges: { standard: true, secure: true, supportFetchAPI: true } }]);
app.setName('MaiBot Desktop Pet');

const urls = { login: 'petapp://local/login/login.html', pet: 'petapp://local/pet/index.html', records: 'petapp://local/pet/exploration.html' };
const roles = new Map(), pendingCommands = new Map();
const uuid = () => randomUUID().replaceAll('-', '');
let loginWindow, petWindow, recordsWindow, petController, recordsController, connection, native, tray;
let settings, settingsPath, activeKey, persistence = Promise.resolve(), quitting = false, stopped = false, switching = false, connecting = false;

function report(error) {
  // 错误来自本地操作和经连接层截断的服务端说明，不记录请求/口令。
  console.error(error.message || String(error));
}

async function readSettings() {
  try {
    const value = JSON.parse(await fs.readFile(settingsPath, 'utf8'));
    if (!value || typeof value !== 'object' || !value.clients || typeof value.clients !== 'object') throw new Error('桌宠设置文件格式无效');
    return value;
  } catch (error) {
    if (error.code === 'ENOENT') return { server: '', botId: '', botName: '', clients: {} };
    throw error;
  }
}

function saveSettings() {
  const content = JSON.stringify(settings, null, 2);
  const operation = persistence.then(async () => {
    await fs.mkdir(path.dirname(settingsPath), { recursive: true });
    await fs.writeFile(settingsPath + '.tmp', content, { mode: 0o600 });
    await fs.rename(settingsPath + '.tmp', settingsPath);
  });
  // 下一次保存不因已显示的上一次磁盘错误而永久失效。
  persistence = operation.catch(report);
  return operation;
}

function trusted(event, role) {
  if (roles.get(event.sender.id) !== role || event.senderFrame !== event.sender.mainFrame || event.senderFrame.url !== urls[role]) {
    throw new Error('此窗口无权调用该接口');
  }
}

function createWindow(role, options) {
  const icon = process.platform === 'win32'
    ? (app.isPackaged ? path.join(process.resourcesPath, 'maimai.ico') : path.resolve(__dirname, '../../dashboard/public/maimai.ico')) : undefined;
  const window = new BrowserWindow({ ...options, show: false,
    icon,
    webPreferences: { preload: path.join(__dirname, 'preload.cjs'), partition: 'maibot-pet',
      sandbox: true, contextIsolation: true, nodeIntegration: false, webSecurity: true, spellcheck: false,
      backgroundThrottling: false, devTools: !app.isPackaged },
  });
  roles.set(window.webContents.id, role);
  const contentsId = window.webContents.id;
  window.setMenu(null);
  window.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  window.webContents.on('will-navigate', event => event.preventDefault());
  window.webContents.on('will-attach-webview', event => event.preventDefault());
  window.webContents.on('render-process-gone', (_event, details) => {
    report(new Error(`桌宠窗口已退出: ${details.reason}`));
    window.close();
  });
  window.on('closed', () => {
    roles.delete(contentsId);
    for (const [id, pending] of pendingCommands) {
      if (pending.sender === contentsId) { clearTimeout(pending.timer); pending.reject(new Error('桌宠窗口已关闭')); pendingCommands.delete(id); }
    }
  });
  window.loadURL(urls[role]).catch(error => { report(error); window.close(); });
  return window;
}

function toRenderer(window, command) {
  if (!window || window.isDestroyed()) return Promise.reject(new Error('桌宠窗口尚未就绪'));
  const id = uuid();
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => { pendingCommands.delete(id); reject(new Error('桌宠页面响应超时')); }, command.type === 'skin' ? 12000 : 15000);
    pendingCommands.set(id, { sender: window.webContents.id, resolve, reject, timer });
    window.webContents.send('pet:command', { id, command });
  });
}

ipcMain.on('pet:result', (event, payload) => {
  const role = roles.get(event.sender.id);
  if (role !== 'pet' && role !== 'records') return;
  try { trusted(event, role); } catch { return; }
  const pending = payload && pendingCommands.get(payload.id);
  if (!pending || pending.sender !== event.sender.id) return;
  clearTimeout(pending.timer); pendingCommands.delete(payload.id);
  if (typeof payload.error === 'string') pending.reject(new Error(payload.error.slice(0, 2000)));
  else pending.resolve(payload.result);
});

function userEvent(event) {
  if (!connection) throw new Error('桌宠未连接服务器');
  return connection.sendEvent(event);
}

async function executeCommand(command) {
  switch (command.type) {
    case 'capture_screen': return native.captureScreen();
    case 'sample_screen': return native.sampleScreen({ threshold: command.threshold, reset: command.reset });
    case 'applications': return { items: await native.applications() };
    case 'todo': throw new Error('远端待办由服务器管理');
    case 'show':
      petWindow.showInactive();
      return toRenderer(petWindow, { type: 'visibility', visible: true });
    case 'hide':
      await petController.dragEnd();
      petWindow.hide();
      return toRenderer(petWindow, { type: 'visibility', visible: false });
    case 'exploration_tasks':
      if (typeof command.request_id === 'string' && command.request_id.startsWith('records:')) {
        return recordsWindow ? toRenderer(recordsWindow, command) : {};
      }
      return toRenderer(petWindow, command);
    default: return toRenderer(petWindow, command);
  }
}

function openRecords() {
  if (recordsWindow) { recordsWindow.restore(); recordsWindow.show(); return; }
  const window = createWindow('records', { title: '探索记录 · MaiBot', width: 1040, height: 740,
    minWidth: 680, minHeight: 480, frame: false, resizable: true, backgroundColor: '#fff8ec' });
  recordsWindow = window;
  const controller = new RecordsWindowController(window, screen);
  recordsController = controller;
  window.once('ready-to-show', () => window.show());
  window.on('blur', () => controller.end());
  window.on('closed', () => { controller.end(); recordsWindow = null; recordsController = null; });
}

async function todo(action, payload = {}) {
  if (!connection) throw new Error('桌宠未连接服务器');
  return connection.request('POST', '/todos', { action, ...payload, ...(action === 'list' ? {} : { request_id: uuid() }) });
}

const petMethods = {
  ready() { connection.start(); return true; },
  async send(requestId, text, images) {
    if (!/^[0-9a-f]{32}$/.test(requestId)) throw new Error('无效请求 ID');
    const result = await connection.request('POST', `/chat/${requestId}`, { text, images });
    // HTTP 已提交成功；页面展示失败不能诱使用户重复发送。
    toRenderer(petWindow, { type: 'accepted', request_id: requestId }).catch(report);
    return result;
  },
  async todos(completed = false) {
    if (typeof completed !== 'boolean') throw new Error('无效待办筛选');
    const { items } = await todo('list', { include_completed: completed });
    return completed ? items.filter(item => item.status === 'completed').sort((a, b) => a.completed_at - b.completed_at) : items;
  },
  async todo_image(id) {
    if (typeof id !== 'string' || !/^[0-9a-f]{64}$/.test(id)) throw new Error('无效待办图片 ID');
    return (await connection.request('GET', `/todos/images/${id}`)).data_url;
  },
  async add_todo(text, options) { return (await todo('add', { text, options })).item; },
  async update_todo(todoId, text, options) { return (await todo('update', { todo_id: todoId, text, options })).item; },
  async done_todo(todoId) { return todo('complete', { todo_id: todoId }); },
  open_exploration_records: openRecords,
  exploration_tasks(taskId, requestId) { return userEvent({ type: 'exploration_tasks', task_id: taskId, request_id: requestId }); },
  explore(goal) { return userEvent({ type: 'explore', goal }); },
  fit(scale, height = 900) { return petController.fit(scale, height); },
  region(rects) { return petController.region(rects); },
  drag_begin() { return petController.dragBegin(); },
  drag_move() { return petController.dragMove(); },
  drag_end() { return petController.dragEnd(); },
  clicked() { return userEvent({ type: 'clicked' }); },
  screen_action(action) { return userEvent({ type: 'screen_action', action }); },
  refresh_buttons() { return userEvent({ type: 'buttons_refresh' }); },
  click_button(pluginId, buttonId, actionId = null) { return userEvent({ type: 'button_click', plugin_id: pluginId, button_id: buttonId, action_id: actionId }); },
  copy_panel_content(kind, content) { return native.copyPanelContent(kind, content); },
  exit_client() { app.quit(); },
};
const recordsMethods = {
  refresh(taskId, requestId) { return userEvent({ type: 'exploration_tasks', task_id: taskId, request_id: 'records:' + requestId }); },
  window_control(action) { return recordsController.control(action); },
  window_gesture(edge) { return recordsController.gesture(edge); },
  window_gesture_end() { return recordsController.end(); },
};

ipcMain.handle('pet:call', (event, method, args) => {
  const role = roles.get(event.sender.id);
  if (role !== 'pet' && role !== 'records') throw new Error('此窗口无权调用桌宠接口');
  trusted(event, role);
  const methods = role === 'pet' ? petMethods : recordsMethods;
  if (typeof method !== 'string' || !Object.hasOwn(methods, method) || !Array.isArray(args) || args.length > 5) throw new Error('未知桌宠接口');
  return methods[method](...args);
});

ipcMain.handle('login:profile', event => {
  trusted(event, 'login');
  return { server: settings.server, botId: settings.botId, botName: settings.botName };
});
ipcMain.handle('login:fit', (event, height) => {
  trusted(event, 'login');
  if (!Number.isFinite(height)) throw new Error('无效窗口高度');
  // 固定表单宽度，避免 Windows 高 DPI 下回读宽度的取整误差累积。
  loginWindow.setContentSize(400, Math.ceil(Math.max(300, Math.min(560, height))));
});
ipcMain.handle('page:open-external', (event, value) => {
  const role = roles.get(event.sender.id);
  if (role !== 'pet' && role !== 'records') throw new Error('此窗口无权打开链接');
  trusted(event, role);
  if (typeof value !== 'string' || value.length > 8192) throw new Error('无效链接');
  const url = new URL(value);
  if (!['https:', 'http:'].includes(url.protocol) || url.username || url.password) throw new Error('只允许 HTTP(S) 链接');
  return electron.shell.openExternal(url.href);
});
ipcMain.handle('login:discover', (event, server, password) => { trusted(event, 'login'); return discoverBots(server, password); });
ipcMain.handle('login:connect', async (event, input) => {
  trusted(event, 'login');
  if (connection || connecting || switching || quitting) throw new Error('桌宠正在切换连接');
  connecting = true;
  try { return await connectClient(input); }
  finally { connecting = false; }
});

async function connectClient(input) {
  if (!input || !/^[0-9a-f]{32}$/.test(input.botId) || typeof input.password !== 'string' || !input.password || input.password.length > 4096
    || typeof input.allowScreenRead !== 'boolean' || typeof input.botName !== 'string' || input.botName.length > 200) throw new Error('无效连接信息');
  const options = { server: normalizeServer(input.server), botId: input.botId, botName: input.botName, password: input.password, allowScreenRead: input.allowScreenRead };
  activeKey = createHash('sha256').update(`${options.server}\n${options.botId}`).digest('hex');
  const profile = settings.clients[activeKey] || { clientId: uuid() };
  settings.clients[activeKey] = profile;
  Object.assign(settings, { server: options.server, botId: options.botId, botName: options.botName });
  await saveSettings();
  native = createNativeCapabilities(electron, { allowScreenRead: options.allowScreenRead });
  connection = new RemoteConnection({ ...options, clientId: profile.clientId }, { executeCommand });
  const window = createWindow('pet', { title: `麦麦 · ${options.botName}`, width: 260, height: 900,
    transparent: true, frame: false, resizable: false, alwaysOnTop: true, skipTaskbar: true,
    hasShadow: false, backgroundColor: '#00000000' });
  petWindow = window;
  petController = new PetWindowController(window, screen, {
    anchor: profile.anchor, emit: userEvent,
    save: async anchor => { settings.clients[activeKey].anchor = anchor; await saveSettings(); },
  });
  window.setAlwaysOnTop(true, 'floating');
  if (process.platform === 'darwin') window.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
  window.once('ready-to-show', () => window.showInactive());
  window.on('closed', () => { petWindow = null; if (!quitting && !switching) app.quit(); });
  connection.on('state', state => { toRenderer(window, { type: 'connection', ...state }).catch(report); });
  connection.on('connected', () => { if (!window.isDestroyed()) { window.showInactive(); toRenderer(window, { type: 'visibility', visible: true }).catch(report); } });
  loginWindow.close(); loginWindow = null;
  return true;
}

function trayIcon() {
  // 本地生成的小图标不需要网络或额外素材依赖。
  const size = 32, bitmap = Buffer.alloc(size * size * 4);
  for (let y = 0; y < size; y++) for (let x = 0; x < size; x++) {
    if (Math.hypot(x - 15.5, y - 15.5) > 14) continue;
    const i = (y * size + x) * 4, eye = (x >= 9 && x <= 11 || x >= 20 && x <= 22) && y >= 12 && y <= 17;
    bitmap[i] = eye ? 35 : 120; bitmap[i + 1] = eye ? 40 : 207; bitmap[i + 2] = eye ? 40 : 168; bitmap[i + 3] = 255;
  }
  return nativeImage.createFromBitmap(bitmap, { width: size, height: size });
}

function showLogin() {
  if (loginWindow) { loginWindow.show(); return; }
  const window = createWindow('login', { title: '连接桌宠 · MaiBot', width: 400, height: 340,
    useContentSize: true, resizable: false, backgroundColor: '#fffaf2' });
  loginWindow = window;
  window.once('ready-to-show', () => window.show());
  window.on('closed', () => { loginWindow = null; if (!connection && !quitting) app.quit(); });
}

async function changeConnection() {
  if (quitting || switching || connecting) return;
  switching = true;
  // 先停止网络、取消页面请求，再关闭旧窗口；用户不需重启应用来修改地址或授权。
  try {
    if (connection) await connection.stop();
    connection = null;
    if (recordsWindow) recordsWindow.close();
    if (petWindow) petWindow.close();
    native = null;
    showLogin();
  } finally {
    switching = false;
  }
}

app.on('window-all-closed', () => { if (!connection && !quitting && !switching) app.quit(); });
app.on('activate', () => { if (petWindow) petWindow.show(); else showLogin(); });
app.on('before-quit', event => {
  if (stopped) return;
  event.preventDefault();
  if (quitting) return;
  quitting = true;
  Promise.allSettled([connection?.stop(), persistence]).then(() => { stopped = true; tray?.destroy(); app.quit(); });
});

app.whenReady().then(async () => {
  settingsPath = path.join(app.getPath('userData'), 'settings.json');
  settings = await readSettings();
  const webRoot = app.isPackaged ? path.join(process.resourcesPath, 'web') : path.resolve(__dirname, '../../src/desktop_pet/web');
  await installLocalPages(session.fromPartition('maibot-pet'), webRoot, path.join(__dirname, '../renderer'));
  Menu.setApplicationMenu(process.platform === 'darwin'
    ? Menu.buildFromTemplate([{ role: 'appMenu' }, { role: 'editMenu' }, { role: 'windowMenu' }]) : null);
  tray = new Tray(trayIcon()); tray.setToolTip('MaiBot 桌宠');
  tray.setContextMenu(Menu.buildFromTemplate([
    { label: '显示桌宠', click: () => { if (petWindow) { petWindow.show(); toRenderer(petWindow, { type: 'visibility', visible: true }).catch(report); } else showLogin(); } },
    { label: '更换连接 / 屏幕授权', click: () => changeConnection().catch(error => dialog.showErrorBox('切换连接失败', error.message)) },
    { type: 'separator' }, { label: '退出', click: () => app.quit() },
  ]));
  tray.on('double-click', () => { if (petWindow) petWindow.show(); else showLogin(); });
  showLogin();
}).catch(error => { dialog.showErrorBox('桌宠启动失败', error.message); app.quit(); });
