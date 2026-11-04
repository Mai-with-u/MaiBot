'use strict';

// Real Electron windows against an ephemeral localhost host; no real Bot credentials or data.
const assert = require('node:assert/strict');
const { randomUUID } = require('node:crypto');
const { once } = require('node:events');
const fs = require('node:fs/promises');
const http = require('node:http');
const os = require('node:os');
const path = require('node:path');
const { setTimeout: delay } = require('node:timers/promises');
const { _electron: electron } = require('playwright-core');
const { PNG } = require('pngjs');
const { WebSocket, WebSocketServer } = require('ws');

const PROJECT = path.resolve(__dirname, '..');
const BOT_ID = '0123456789abcdef0123456789abcdef';
const PASSWORD = 'synthetic-electron-smoke-password';
const BOT_NAME = '冒烟测试麦麦';
const BASE = `/api/host/bots/${BOT_ID}/proxy/api/webui/desktop-pet/remote`;
const LOGIN_URL = 'petapp://local/login/login.html';
const PET_URL = 'petapp://local/pet/index.html';
const RECORDS_URL = 'petapp://local/pet/exploration.html';
const id = () => randomUUID().replaceAll('-', '');

async function until(check, label, timeout = 15_000) {
  const deadline = Date.now() + timeout;
  do {
    const result = await check();
    if (result) return result;
    await delay(25);
  } while (Date.now() < deadline);
  throw new Error(`Timed out: ${label}`);
}

class HostFixture {
  constructor() {
    this.sessions = new Set();
    this.sockets = new Set();
    this.acks = new Map();
    this.todos = [];
    this.chats = [];
    this.writes = [];
    this.failures = [];
    this.readyCount = 0;
    this.forbiddenRequests = 0;
    this.connection = null;
    this.server = http.createServer((request, response) => {
      this.handle(request, response).catch(error => {
        this.failures.push(error);
        if (!response.headersSent) this.json(response, { detail: error.message }, 500);
        else response.destroy();
      });
    });
    this.websocket = new WebSocketServer({ noServer: true });
    this.server.on('upgrade', (request, socket, head) => {
      try {
        assert.equal(request.url, BASE + '/ws');
        this.authenticate(request);
        this.websocket.handleUpgrade(request, socket, head, ws => this.attach(ws));
      } catch (error) {
        this.failures.push(error);
        socket.end('HTTP/1.1 401 Unauthorized\r\nConnection: close\r\n\r\n');
      }
    });
  }

  json(response, payload, status = 200) {
    response.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8' });
    response.end(JSON.stringify(payload));
  }

  authenticate(request) {
    const cookie = /(?:^|;\s*)pet-smoke-session=([^;]+)/.exec(request.headers.cookie || '')?.[1];
    assert.ok(cookie && this.sessions.has(cookie), 'host requests must carry an authenticated Cookie');
    return cookie;
  }

  async handle(request, response) {
    const chunks = [];
    for await (const chunk of request) chunks.push(chunk);
    const raw = Buffer.concat(chunks).toString('utf8');
    const body = raw ? JSON.parse(raw) : {};
    if (request.url === '/api/webui/auth/verify') {
      assert.equal(request.method, 'POST');
      assert.equal(body.token, PASSWORD);
      const session = id();
      this.sessions.add(session);
      response.setHeader('Set-Cookie', `pet-smoke-session=${session}; Path=/; HttpOnly; SameSite=Strict`);
      return this.json(response, { valid: true });
    }
    if (request.url.startsWith('/forbidden-')) {
      this.forbiddenRequests += 1;
      return this.json(response, { error: 'renderer navigation must be denied' });
    }
    const session = this.authenticate(request);
    if (request.url === '/api/webui/auth/logout') {
      this.sessions.delete(session);
      return this.json(response, { success: true });
    }
    if (request.url === '/api/host/bots') {
      return this.json(response, { bots: [{ bot_id: BOT_ID, name: BOT_NAME, status: 'running' }] });
    }
    assert.ok(request.url.startsWith(BASE + '/'), 'requests must pass through the selected Bot proxy');
    assert.equal(request.headers['x-desktop-pet-connection'], this.connection?.id,
      'HTTP operations must belong to the active WebSocket connection');
    if (request.url === BASE + '/todos') {
      assert.equal(request.method, 'POST');
      if (body.action === 'list') {
        return this.json(response, { items: this.todos.filter(todo => body.include_completed || todo.status !== 'completed') });
      }
      assert.match(body.request_id, /^[0-9a-f]{32}$/);
      assert.ok(!this.writes.some(write => write.request_id === body.request_id), 'do not replay mutations');
      this.writes.push(body);
      if (body.action === 'add') {
        const todo = { id: id(), text: body.text, kind: 'user', images: [], status: 'pending',
          condition: '', condition_type: 'natural', due_at: null, revision: id(), created_at: Date.now() / 1000,
          ...body.options };
        this.todos.push(todo);
        return this.json(response, { item: todo });
      }
      const todo = this.todos.find(item => item.id === body.todo_id);
      assert.ok(todo, 'update and complete must use a real returned todo ID');
      if (body.action === 'update') {
        Object.assign(todo, body.options, { text: body.text, revision: id(), updated_at: Date.now() / 1000 });
        return this.json(response, { item: todo });
      }
      assert.equal(body.action, 'complete');
      Object.assign(todo, { status: 'completed', completed_at: Date.now() / 1000 });
      return this.json(response, { completed: todo.id });
    }
    if (new RegExp(`^${BASE}/chat/[0-9a-f]{32}$`).test(request.url)) {
      assert.equal(request.method, 'POST');
      this.chats.push(body);
      this.json(response, { accepted: true });
      this.command('reply', { parts: [{ kind: 'text', text: '来自本机测试服务器的回复' }] })
        .catch(error => this.failures.push(error));
      return;
    }
    throw new Error(`Unexpected fixture endpoint: ${request.method} ${request.url}`);
  }

  attach(ws) {
    this.sockets.add(ws);
    const connection = { ws, id: id(), ready: false };
    ws.on('close', () => this.sockets.delete(ws));
    ws.on('error', error => this.failures.push(error));
    ws.on('message', raw => {
      try {
        const event = JSON.parse(raw.toString());
        if (event.type === 'hello') {
          assert.equal(event.version, 1);
          assert.equal(event.capabilities.screen_read, false, 'smoke never authorizes real desktop capture');
          this.connection = connection;
          ws.send(JSON.stringify({ type: 'hello', version: 1, connection_id: connection.id,
            heartbeat_interval: 1, heartbeat_timeout: 5 }));
        } else if (event.type === 'ready') {
          connection.ready = true;
          this.readyCount += 1;
          ws.send(JSON.stringify({ type: 'ready', version: 1 }));
        } else if (event.type === 'ping') {
          ws.send(JSON.stringify({ type: 'pong' }));
        } else if (event.type === 'ack') {
          this.acks.get(event.id)?.(event);
        } else if (event.type === 'exploration_tasks') {
          const task = { id: 'smoke-exploration', goal: 'Electron 冒烟探索记录', status: 'completed',
            created_at: Date.now() / 1000, updated_at: Date.now() / 1000, result_version: 2,
            result: '本机模拟服务器提供的记录', resources: [], experiences: [], events: [] };
          this.command('exploration_tasks', { request_id: event.request_id, items: [task], selected: task, error: '' })
            .catch(error => this.failures.push(error));
        } else if (event.type === 'buttons_refresh') {
          this.command('buttons', { buttons: [] }).catch(error => this.failures.push(error));
        } else {
          assert.ok(['clicked', 'drag_started', 'drag_ended'].includes(event.type), `Unexpected event: ${event.type}`);
        }
      } catch (error) { this.failures.push(error); }
    });
  }

  command(type, payload = {}) {
    assert.ok(this.connection?.ready && this.connection.ws.readyState === WebSocket.OPEN);
    const commandId = id();
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.acks.delete(commandId);
        reject(new Error(`Missing GUI command acknowledgement: ${type}`));
      }, 15_000);
      this.acks.set(commandId, event => { clearTimeout(timer); this.acks.delete(commandId); resolve(event); });
      this.connection.ws.send(JSON.stringify({ type, id: commandId, ...payload }));
    });
  }

  async start() {
    this.server.listen(0, '127.0.0.1');
    await once(this.server, 'listening');
    this.url = `http://127.0.0.1:${this.server.address().port}`;
  }

  async close() {
    for (const ws of this.sockets) ws.terminate();
    await new Promise(resolve => this.websocket.close(resolve));
    this.server.closeAllConnections();
    await new Promise(resolve => this.server.close(resolve));
  }
}

async function openPanel(page, button, mode) {
  const current = await page.locator('#stage').getAttribute('data-mode');
  if (current === mode) return;
  if (current !== 'menu') {
    if (current !== 'idle') await page.locator('#pet').click();
    await page.locator('#pet').click();
  }
  await page.locator(button).click();
  await page.waitForFunction(expected => document.querySelector('#stage').dataset.mode === expected, mode);
}

async function assertIsolation(app, page, fixture) {
  assert.deepEqual(await page.evaluate(() => ({ process: typeof window.process, require: typeof window.require })),
    { process: 'undefined', require: 'undefined' });
  const preferences = await app.evaluate(({ BrowserWindow }, url) => {
    const window = BrowserWindow.getAllWindows().find(item => item.webContents.getURL() === url);
    const prefs = window.webContents.getLastWebPreferences();
    return { nodeIntegration: prefs.nodeIntegration, contextIsolation: prefs.contextIsolation, sandbox: prefs.sandbox };
  }, page.url());
  assert.deepEqual(preferences, { nodeIntegration: false, contextIsolation: true, sandbox: true });
  const count = app.windows().length;
  assert.equal(await page.evaluate(url => window.open(url) === null, fixture.url + '/forbidden-popup'), true);
  const original = page.url();
  await page.evaluate(url => {
    window.location.assign(url);
    return new Promise(resolve => requestAnimationFrame(() => resolve()));
  }, fixture.url + '/forbidden-navigation');
  assert.equal(page.url(), original, 'renderer navigation must remain on the local application page');
  assert.equal(app.windows().length, count, 'renderer must not create new windows');
  assert.equal(fixture.forbiddenRequests, 0);
}

async function assertClipboardWorker(app, page) {
  // Materialize every original format before overwriting; nothing leaves the main process.
  await app.evaluate(async ({ clipboard, ClipboardItem }) => {
    globalThis.__petSmokeClipboard = await Promise.all((await clipboard.read()).map(async item =>
      new ClipboardItem(Object.fromEntries(await Promise.all(item.types.map(async type =>
        [type, await item.getType(type)]))))));
  });
  try {
    const text = 'Electron smoke temporary clipboard';
    await page.evaluate(value => window.desktopPetBridge.api.copy_panel_content('text', value), text);
    assert.equal(await app.evaluate(({ clipboard }) => clipboard.readText()), text);
    const png = new PNG({ width: 2, height: 2 });
    png.data.fill(255);
    const content = PNG.sync.write(png).toString('base64');
    await page.evaluate(value => window.desktopPetBridge.api.copy_panel_content('image', value), content);
    assert.deepEqual(await app.evaluate(async ({ clipboard, nativeImage }) => {
      const item = (await clipboard.read()).find(entry => entry.types.includes('image/png'));
      const png = await item.getType('image/png');
      return nativeImage.createFromBuffer(Buffer.from(await png.arrayBuffer())).getSize();
    }), { width: 2, height: 2 });
  } finally {
    await app.evaluate(async ({ clipboard }) => {
      if (globalThis.__petSmokeClipboard.length) await clipboard.write(globalThis.__petSmokeClipboard);
      else clipboard.clear();
      delete globalThis.__petSmokeClipboard;
    });
  }
}

async function run() {
  const temporary = await fs.mkdtemp(path.join(os.tmpdir(), 'maibot-electron-smoke-'));
  const keepArtifacts = process.env.PET_KEEP_ARTIFACTS === '1';
  const fixture = new HostFixture();
  let app;
  let passed = false;
  const pageErrors = [];
  try {
    await fixture.start();
    const env = { ...process.env };
    delete env.ELECTRON_RUN_AS_NODE;
    const packaged = process.env.PET_EXECUTABLE;
    app = await electron.launch({
      executablePath: packaged ? path.resolve(packaged) : require('electron'),
      args: [...(packaged ? [] : [PROJECT]), `--user-data-dir=${path.join(temporary, 'profile')}`],
      cwd: PROJECT, env, timeout: 30_000,
    });
    app.context().setDefaultTimeout(15_000);
    app.on('window', page => page.on('pageerror', error => pageErrors.push(error.message)));
    const login = await app.firstWindow();
    await login.waitForURL(LOGIN_URL);
    await login.waitForFunction(() => document.querySelector('#server').value.length > 0);
    await login.locator('#server').fill(fixture.url);
    await login.locator('#password').fill(PASSWORD);
    if (keepArtifacts) await login.screenshot({ path: path.join(temporary, 'login.png') });
    await login.locator('#connect').click();
    const pet = await until(() => app.windows().find(page => page.url() === PET_URL), 'pet window');
    await pet.locator('#remote-indicator[data-connected="true"]').waitFor();
    assert.equal(fixture.readyCount, 1);
    console.log('Real login and confirmed remote connection passed.');

    await openPanel(pet, '#orb-chat', 'chat');
    await pet.locator('#chat-input').fill('Electron 真实界面聊天验证');
    await pet.locator('#chat-input').press('Enter');
    await until(() => fixture.chats.length === 1, 'chat HTTP submission');
    assert.equal(fixture.chats[0].text, 'Electron 真实界面聊天验证');
    assert.deepEqual(fixture.chats[0].images, []);
    await pet.getByText('来自本机测试服务器的回复', { exact: true }).waitFor();
    await pet.waitForFunction(() => !document.querySelector('#chat-input').disabled && !document.querySelector('#chat-input').value);

    await openPanel(pet, '#orb-todo', 'todo');
    await pet.locator('#todo-input').fill('Electron 待办新增');
    await pet.locator('#todo-save').click();
    await pet.locator('#todos .todo-title').filter({ hasText: 'Electron 待办新增' }).waitFor();
    await pet.getByRole('button', { name: '编辑事项', exact: true }).click();
    await pet.locator('#todo-input').fill('Electron 待办修改');
    await pet.locator('#todo-save').click();
    await pet.getByRole('button', { name: '完成并归档：Electron 待办修改', exact: true }).click();
    await pet.waitForFunction(() => document.querySelector('#todos').children.length === 0);
    await pet.locator('#todo-history').click();
    await pet.locator('#todos .todo-title').filter({ hasText: 'Electron 待办修改' }).waitFor();
    assert.deepEqual(fixture.writes.map(write => write.action), ['add', 'update', 'complete']);
    console.log('Chat submission and todo lifecycle passed.');

    const notice = await fixture.command('notice', { parts: [{ kind: 'text', text: '服务器通知已显示' }] });
    assert.equal(notice.error, undefined);
    await pet.locator('#say').getByText('服务器通知已显示', { exact: true }).waitFor();
    const capture = await fixture.command('capture_screen');
    assert.match(capture.error, /授权|允许|permission/i);
    assert.equal(capture.result, undefined, 'a denied capture must never return an image');
    if (keepArtifacts) await pet.screenshot({ path: path.join(temporary, 'pet.png') });

    await pet.evaluate(() => window.desktopPetBridge.api.open_exploration_records());
    const records = await until(() => app.windows().find(page => page.url() === RECORDS_URL), 'exploration records window');
    await records.locator('#detail h2').getByText('Electron 冒烟探索记录', { exact: true }).waitFor();

    const previousId = fixture.connection.id;
    fixture.connection.ws.close(1012, 'smoke reconnect');
    await pet.locator('#remote-indicator[data-connected="false"]').waitFor({ state: 'attached' });
    await until(() => fixture.readyCount === 2, 'second confirmed connection');
    await pet.locator('#remote-indicator[data-connected="true"]').waitFor();
    assert.notEqual(fixture.connection.id, previousId);
    const restored = await pet.evaluate(() => window.desktopPetBridge.api.todos(true));
    assert.equal(restored.length, 1);
    assert.equal(restored[0].status, 'completed');
    assert.equal(fixture.chats.length, 1, 'reconnect must not resend chat');
    assert.equal(fixture.writes.length, 3, 'reconnect must not repeat todo writes');
    await assertClipboardWorker(app, pet);
    // A denied navigation remains pending in Playwright; run these after all locator actions.
    await assertIsolation(app, pet, fixture);
    await assertIsolation(app, records, fixture);
    assert.equal(fixture.forbiddenRequests, 0, 'blocked navigation must not issue delayed requests');
    assert.deepEqual(fixture.failures.map(error => error.message), []);
    assert.deepEqual(pageErrors, []);
    passed = true;
    console.log('Electron smoke passed: login, chat, todo lifecycle, notification, screen consent, PNG worker, records, isolation, reconnect.');
    if (keepArtifacts) console.log(`Smoke artifacts retained at: ${temporary}`);
  } catch (error) {
    if (app) {
      for (const [index, page] of app.windows().entries()) {
        await page.screenshot({ path: path.join(temporary, `failure-${index}.png`) }).catch(() => {});
      }
    }
    console.error(`Smoke artifacts retained at: ${temporary}`);
    if (fixture.failures.length) console.error(fixture.failures);
    throw error;
  } finally {
    if (app) await app.close().catch(() => app.process().kill());
    await fixture.close();
    // Only remove the unique directory created by this run, never a caller-supplied path.
    if (passed && !keepArtifacts && path.dirname(path.resolve(temporary)) === path.resolve(os.tmpdir())
        && path.basename(temporary).startsWith('maibot-electron-smoke-')) {
      await fs.rm(temporary, { recursive: true, force: true });
    }
  }
}

run().catch(error => { console.error(error); process.exitCode = 1; });
