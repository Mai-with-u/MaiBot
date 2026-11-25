'use strict';

// Real Windows Tauri/WebView2 windows and a synthetic BasicAuth-protected host; no real credentials or desktop capture.
const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const { createHash, randomUUID } = require('node:crypto');
const { once } = require('node:events');
const { readFileSync } = require('node:fs');
const fs = require('node:fs/promises');
const http = require('node:http');
const os = require('node:os');
const path = require('node:path');
const { setTimeout: delay } = require('node:timers/promises');
const { chromium } = require('playwright-core');
const { WebSocket, WebSocketServer } = require('ws');

const PROJECT = path.resolve(__dirname, '..');
const BOT_ID = '0123456789abcdef0123456789abcdef';
const PASSWORD = 'synthetic-tauri-smoke-password';
const BASIC_USER = 'synthetic-proxy-user', BASIC_PASSWORD = 'synthetic-proxy-password';
const BASIC = 'Basic ' + Buffer.from(`${BASIC_USER}:${BASIC_PASSWORD}`).toString('base64');
const SAVED_LOGIN = process.env.PET_SMOKE_BROWSER_LOGIN !== '1';
const LEGACY_SKIN = process.env.PET_SMOKE_LEGACY_SKIN === '1';
const BOT_NAME = '冒烟测试麦麦';
const BASE = `/api/host/bots/${BOT_ID}/proxy/api/webui/desktop-pet/remote`;
const IMAGE_ID = 'a'.repeat(64);
const PNG_URL = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGN4NcntPwAG7gLCsCLTnQAAAABJRU5ErkJggg==';
const SKIN_IMAGE = readFileSync(path.resolve(PROJECT, '../../src/desktop_pet/presets/orange_sprout/idle-1.webp'));
const SKIN_DIGEST = createHash('sha256').update(SKIN_IMAGE).digest('hex');
const EXTRA_SKIN_IMAGE = readFileSync(path.resolve(PROJECT, '../../src/desktop_pet/presets/orange_sprout/thinking-1.webp'));
const EXTRA_SKIN_DIGEST = createHash('sha256').update(EXTRA_SKIN_IMAGE).digest('hex');
const id = () => randomUUID().replaceAll('-', '');

async function until(check, label, timeout = 15_000) {
  const deadline = Date.now() + timeout;
  do { const result = await check(); if (result) return result; await delay(25); } while (Date.now() < deadline);
  throw new Error(`Timed out: ${label}`);
}

class HostFixture {
  constructor() {
    this.sessions = new Set(); this.sockets = new Set(); this.acks = new Map();
    this.todos = []; this.chats = []; this.writes = []; this.failures = [];
    this.readyCount = 0; this.verifyCount = 0; this.logoutCount = 0; this.forbiddenRequests = 0;
    this.basicChallenges = 0; this.basicHTTP = 0; this.basicWS = 0; this.connection = null;
    this.skinDownloads = 0;
    this.skinPrefetches = 0;
    this.conversationId = '';
    this.server = http.createServer((request, response) => this.handle(request, response).catch(error => {
      this.failures.push(error);
      if (!response.headersSent) this.json(response, { detail: error.message }, 500); else response.destroy();
    }));
    this.websocket = new WebSocketServer({ noServer: true });
    this.server.on('upgrade', (request, socket, head) => {
      if (request.headers.authorization !== BASIC) {
        this.basicChallenges++;
        socket.end('HTTP/1.1 401 Unauthorized\r\nWWW-Authenticate: Basic realm="MaiBot smoke"\r\nConnection: close\r\n\r\n'); return;
      }
      try {
        this.basicWS++; assert.equal(request.url, BASE + '/ws'); this.authenticate(request);
        this.websocket.handleUpgrade(request, socket, head, ws => this.attach(ws));
      } catch (error) { this.failures.push(error); socket.end('HTTP/1.1 401 Unauthorized\r\nConnection: close\r\n\r\n'); }
    });
  }
  json(response, payload, status = 200) {
    response.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store' });
    response.end(JSON.stringify(payload));
  }
  cookie(request) { return /(?:^|;\s*)pet-smoke-session=([^;]+)/.exec(request.headers.cookie || '')?.[1]; }
  authenticate(request) {
    const cookie = this.cookie(request);
    assert.ok(cookie && this.sessions.has(cookie), 'host business requests require a browser-owned HttpOnly cookie'); return cookie;
  }
  async handle(request, response) {
    // Models Nginx auth_basic outside the application: static/API requests and WS handshakes all require it.
    if (request.headers.authorization !== BASIC) {
      this.basicChallenges++; response.writeHead(401, { 'WWW-Authenticate': 'Basic realm="MaiBot smoke"', 'Content-Type': 'text/html' });
      response.end('<h1>Proxy authentication required</h1>'); return;
    }
    this.basicHTTP++;
    const pathname = new URL(request.url, this.url).pathname;
    if (pathname === '/auth' || pathname === '/') {
      response.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store' });
      response.end(`<!doctype html><meta charset="utf-8"><title>测试 WebUI 登录</title>
        <h1>WebUI 登录</h1><form id="webui-login"><label>WebUI 密码<input id="webui-password" type="password" autocomplete="current-password"></label><button>登录 WebUI</button></form><p id="auth-result"></p>
        <script>document.querySelector('#webui-login').onsubmit=async event=>{event.preventDefault();
        const response=await fetch('/api/webui/auth/verify',{method:'POST',credentials:'include',headers:{'Content-Type':'application/json'},body:JSON.stringify({token:document.querySelector('#webui-password').value})});
        const result=await response.json();if(result.valid)location.assign('/');else document.querySelector('#auth-result').textContent='登录失败';};</script>`);
      return;
    }
    if (pathname === '/favicon.ico') { response.writeHead(204); response.end(); return; }
    if (pathname === '/api/host/desktop-pet/session') {
      this.authenticate(request);
      const host = new URL(this.url).host;
      response.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store',
        'Content-Security-Policy': `default-src 'none'; connect-src 'self' ipc: http://ipc.localhost ws://${host} wss://${host}; base-uri 'none'; frame-ancestors 'none'; form-action 'none'` });
      response.end('<!doctype html><meta charset="utf-8"><title>桌宠连接</title><p>桌宠已通过 WebUI 认证</p>'); return;
    }
    if (pathname.startsWith('/forbidden-')) { this.forbiddenRequests++; return this.json(response, { error: 'must not navigate' }); }
    const chunks = []; for await (const chunk of request) chunks.push(chunk);
    const raw = Buffer.concat(chunks).toString('utf8'), body = raw ? JSON.parse(raw) : {};
    if (pathname === '/api/webui/auth/check') return this.json(response, { authenticated: this.sessions.has(this.cookie(request)), requires_custom_token: false });
    if (pathname === '/api/webui/auth/verify') {
      assert.equal(request.method, 'POST'); assert.equal(body.token, PASSWORD); this.verifyCount++;
      const session = id(); this.sessions.add(session);
      response.setHeader('Set-Cookie', `pet-smoke-session=${session}; Path=/; HttpOnly; SameSite=Lax; Max-Age=3600`);
      return this.json(response, { valid: true, is_first_setup: false, requires_custom_token: false });
    }
    const session = this.authenticate(request);
    if (pathname === '/api/webui/auth/logout') { this.logoutCount++; this.sessions.delete(session); return this.json(response, { success: true }); }
    if (pathname === '/api/host/bots') return this.json(response, { bots: [{ bot_id: BOT_ID, name: BOT_NAME, status: 'running' }] });
    assert.ok(pathname.startsWith(BASE + '/'), 'business requests must use the selected Bot proxy');
    assert.equal(request.headers['x-desktop-pet-connection'], this.connection?.id, 'HTTP must bind to the current WS');
    if (pathname === BASE + '/skins/assets/' + SKIN_DIGEST) {
      assert.equal(request.method, 'GET'); assert.equal(raw, '');
      this.skinDownloads++;
      await delay(700);
      response.writeHead(200, {'Content-Type': 'image/webp', 'Cache-Control': 'no-store'});
      response.end(SKIN_IMAGE);
      return;
    }
    if (pathname === BASE + '/skins/assets/' + EXTRA_SKIN_DIGEST) {
      this.skinPrefetches++;
      response.writeHead(200, {'Content-Type': 'image/webp', 'Cache-Control': 'no-store'});
      response.end(EXTRA_SKIN_IMAGE);
      return;
    }
    if (pathname === BASE + '/todos/images/' + IMAGE_ID) {
      assert.equal(request.method, 'GET'); assert.equal(raw, ''); return this.json(response, { data_url: PNG_URL });
    }
    if (pathname === BASE + '/todos') {
      assert.equal(request.method, 'POST');
      if (body.action === 'list') return this.json(response, { items: this.todos.filter(todo => body.include_completed || todo.status !== 'completed') });
      assert.match(body.request_id, /^[0-9a-f]{32}$/);
      assert.ok(!this.writes.some(write => write.request_id === body.request_id), 'mutations must not be replayed'); this.writes.push(body);
      if (body.action === 'add') {
        const todo = { id: id(), text: body.text, kind: 'user', images: [], status: 'pending', condition: '',
          condition_type: 'natural', due_at: null, revision: id(), created_at: Date.now() / 1000, ...body.options };
        this.todos.push(todo); return this.json(response, { item: todo });
      }
      const todo = this.todos.find(item => item.id === body.todo_id); assert.ok(todo);
      if (body.action === 'update') {
        Object.assign(todo, body.options, { text: body.text, revision: id(), updated_at: Date.now() / 1000 }); return this.json(response, { item: todo });
      }
      assert.equal(body.action, 'complete'); Object.assign(todo, { status: 'completed', completed_at: Date.now() / 1000 });
      return this.json(response, { completed: todo.id });
    }
    if (new RegExp(`^${BASE}/chat/[0-9a-f]{32}$`).test(pathname)) {
      assert.equal(request.method, 'POST'); this.chats.push(body);
      assert.equal(body.conversation_id, this.conversationId);
      if (this.conversationId) {
        return this.json(response, { accepted: true, request_id: pathname.split('/').at(-1), message_id: 'sent-bound-user' });
      }
      this.json(response, { accepted: true, request_id: pathname.split('/').at(-1), message_id: 'sent-user' });
      this.command('reply', { message_id: 'live-reply', sound: 'wood', parts: [{ kind: 'text', text: '来自本机测试服务器的回复' }] }).catch(error => this.failures.push(error)); return;
    }
    throw new Error(`Unexpected fixture endpoint: ${request.method} ${pathname}`);
  }
  attach(ws) {
    this.sockets.add(ws); const connection = { ws, id: id(), ready: false };
    ws.on('close', () => this.sockets.delete(ws)); ws.on('error', error => this.failures.push(error));
    ws.on('message', raw => {
      try {
        const event = JSON.parse(raw.toString());
        if (event.type === 'hello') {
          assert.equal(event.version, 1); assert.equal(event.capabilities.screen_read, false, 'smoke must not authorize desktop capture');
          assert.equal(event.capabilities.chat_binding, true);
          this.connection = connection;
          ws.send(JSON.stringify({ type: 'hello', version: 1, connection_id: connection.id, heartbeat_interval: 1, heartbeat_timeout: 5 }));
        } else if (event.type === 'ready') {
          connection.ready = true; this.readyCount++; ws.send(JSON.stringify({ type: 'ready', version: 1 }));
          if (this.readyCount === 1) this.command('skin', {skin: {id: 'builtin', name: '内置麦麦', animations: {},
            canvas: {width:150, height:107, anchor:[75,107], bubble_anchor:[75,0], hitbox:[0,0,150,107]}}})
            .then(ack => assert.equal(ack.error, undefined))
            .catch(error => this.failures.push(error));
        } else if (event.type === 'ping') ws.send(JSON.stringify({ type: 'pong' }));
        else if (event.type === 'ack') this.acks.get(event.id)?.(event);
        else if (event.type === 'history') {
          if (this.conversationId) {
            this.command('history', {request_id: event.request_id, conversation_id: this.conversationId,
              items: [{id: 'bound-history', who: 'peer', sender: '群成员', parts: [{kind: 'text', text: '所选会话历史'}]}], cursors: {}, has_more: false})
              .catch(error => this.failures.push(error));
            return;
          }
          const older = Object.keys(event.cursors).length > 0;
          const items = older ? [{id: 'oldest', who: 'mai', parts: [{kind: 'text', text: '更早的历史回复'}]}]
            : Array.from({length: 30}, (_, index) => ({id: `history-${index}`, who: 'mai', parts: [{kind: 'text', text: `历史回复 ${index}`}]}));
          this.command('history', {request_id: event.request_id, items, cursors: older ? {} : {owner: 10}, has_more: !older})
            .catch(error => this.failures.push(error));
        } else if (event.type === 'exploration_tasks') {
          const task = { id: 'smoke-exploration', goal: 'Tauri 冒烟探索记录', status: 'completed',
            created_at: Date.now() / 1000, updated_at: Date.now() / 1000, result_version: 2,
            result: '本机模拟服务器提供的记录', resources: [], experiences: [], events: [] };
          this.command('exploration_tasks', { request_id: event.request_id, items: [task], selected: task, error: '' }).catch(error => this.failures.push(error));
        } else if (event.type === 'buttons_refresh') this.command('buttons', { buttons: [] }).catch(error => this.failures.push(error));
        else assert.ok(['clicked', 'drag_started', 'drag_ended'].includes(event.type), `Unexpected event: ${event.type}`);
      } catch (error) { this.failures.push(error); }
    });
  }
  command(type, payload = {}) {
    assert.ok(this.connection?.ready && this.connection.ws.readyState === WebSocket.OPEN);
    const commandId = id();
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => { this.acks.delete(commandId); reject(new Error(`Missing GUI ACK: ${type}`)); }, 15_000);
      this.acks.set(commandId, event => { clearTimeout(timer); this.acks.delete(commandId); resolve(event); });
      this.connection.ws.send(JSON.stringify({ type, id: commandId, ...payload }));
    });
  }
  async start() {
    this.server.listen(0, '127.0.0.1'); await once(this.server, 'listening'); this.url = `http://127.0.0.1:${this.server.address().port}`;
  }
  async close() {
    for (const ws of this.sockets) ws.terminate();
    await new Promise(resolve => this.websocket.close(resolve)); this.server.closeAllConnections();
    await new Promise(resolve => this.server.close(resolve));
  }
}

async function freePort() {
  const server = http.createServer(); server.listen(0, '127.0.0.1'); await once(server, 'listening');
  const port = server.address().port; await new Promise(resolve => server.close(resolve)); return port;
}
async function debugEndpoints(directory, depth = 0) {
  if (depth > 6) return [];
  let entries;
  try { entries = await fs.readdir(directory, { withFileTypes: true }); }
  catch (error) { if (error.code === 'ENOENT') return []; throw error; }
  const endpoints = [];
  for (const entry of entries) {
    const filename = path.join(directory, entry.name);
    if (entry.isDirectory()) endpoints.push(...await debugEndpoints(filename, depth + 1));
    else if (entry.isFile() && entry.name === 'DevToolsActivePort') {
      // Only read Chromium's debugger port marker. Never inspect cookie/credential databases.
      const content = await fs.readFile(filename, 'utf8');
      const port = Number(content.split(/\r?\n/)[0]);
      if (Number.isInteger(port) && port > 0 && port <= 65535) endpoints.push({ endpoint: `http://127.0.0.1:${port}`, filename });
    }
  }
  return endpoints;
}
async function executable() {
  if (process.env.PET_EXECUTABLE) return path.resolve(process.env.PET_EXECUTABLE);
  for (const configuration of ['debug', 'release']) {
    const candidate = path.join(PROJECT, 'src-tauri', 'target', configuration, 'maibot-desktop-pet.exe');
    try { await fs.access(candidate); return candidate; } catch {}
  }
  throw new Error('Build Tauri first or set PET_EXECUTABLE to the built application');
}
async function launch(temporary, pageErrors) {
  const shared = process.env.PET_SMOKE_SHARED_PROFILE === '1';
  const port = shared ? await freePort() : 0;
  const directory = path.join(temporary, 'settings');
  const env = { ...process.env, MAIBOT_PET_DATA_DIR: directory,
    WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS: `--remote-debugging-port=${port}` };
  // Production mode deliberately preserves Tauri's local-webview and per-server web-sessions directories.
  // A single-profile diagnostic mode is available only to troubleshoot CDP itself.
  delete env.WEBVIEW2_USER_DATA_FOLDER;
  if (shared) env.WEBVIEW2_USER_DATA_FOLDER = path.join(temporary, 'webview2');
  const child = spawn(await executable(), [], { cwd: PROJECT, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'], env });
  let output = '', launchError, closing = false, poll, scanning;
  const browsers = new Map(), backgroundErrors = [];
  const pages = () => [...browsers.values()].flatMap(browser => browser.contexts().flatMap(context => context.pages()));
  child.on('error', error => { launchError = error; });
  for (const pipe of [child.stdout, child.stderr]) pipe.on('data', data => { output = (output + data.toString()).slice(-65536); });
  async function scan() {
    if (closing || scanning) return scanning;
    scanning = (async () => {
      const endpoints = shared ? [{ endpoint: `http://127.0.0.1:${port}`, filename: '' }] : await debugEndpoints(directory);
      for (const { endpoint } of endpoints) {
        if (closing || browsers.has(endpoint)) continue;
        try { if (!(await fetch(endpoint + '/json/version', { signal: AbortSignal.timeout(500) })).ok) continue; } catch { continue; }
        try {
          const browser = await chromium.connectOverCDP(endpoint, {timeout: 2000});
          if (closing) { await browser.close(); continue; }
          const watch = page => page.on('pageerror', error => pageErrors.push(error.message));
          for (const context of browser.contexts()) { context.setDefaultTimeout(15_000); context.on('page', watch); context.pages().forEach(watch); }
          browsers.set(endpoint, browser);
          browser.once('disconnected', () => browsers.delete(endpoint));
        } catch (error) {
          // A rejected native login can close a WebView while CDP is attaching.
          if (!/closed|ECONNREFUSED|timeout|socket hang up/i.test(error.message)) throw error;
        }
      }
    })().finally(() => { scanning = null; });
    return scanning;
  }
  try {
    await until(async () => {
      if (launchError) throw launchError;
      if (child.exitCode !== null) throw new Error(`Tauri exited (${child.exitCode}): ${output}`);
      await scan(); return browsers.size > 0;
    }, 'WebView2 remote debugging endpoint', 30_000);
    poll = setInterval(() => scan().catch(error => { if (!closing) backgroundErrors.push(error); }), 100);
    return { child, backgroundErrors, pages, output: () => output, profileCount: () => browsers.size,
      async close() {
        closing = true; clearInterval(poll);
        const pet = pages().find(page => { try { return new URL(page.url()).pathname === '/pet/index.html'; } catch { return false; } });
        if (pet) await pet.evaluate(() => window.desktopPetBridge.api.exit_client()).catch(() => {});
        if (child.exitCode === null) await Promise.race([once(child, 'exit'), delay(5000)]);
        if (child.exitCode === null) child.kill();
        for (const browser of browsers.values()) await browser.close().catch(() => {});
        await scanning?.catch(() => {});
        if (child.exitCode === null) await Promise.race([once(child, 'exit'), delay(3000)]);
      },
    };
  } catch (error) {
    closing = true; clearInterval(poll); child.kill();
    for (const browser of browsers.values()) await browser.close().catch(() => {});
    throw new Error(`${error.message}\n${output}`);
  }
}
const localPage = (app, pathname) => app.pages().find(page => {
  try { const url = new URL(page.url()); return url.hostname === 'tauri.localhost' && url.pathname === pathname; } catch { return false; }
});
async function connectClient(app, fixture, firstLogin, expectSaveWarning = false) {
  const login = await until(() => localPage(app, '/login/login.html'), 'local login window');
  await login.waitForFunction(() => window.desktopPetBridge && document.querySelector('#server')?.value);
  const credentialStatus = login.locator('#credential-status');
  if (!firstLogin) {
    await credentialStatus.filter({hasText: SAVED_LOGIN ? /^已加密保存：Nginx、WebUI$/ : /^已加密保存：Nginx$/}).waitFor({timeout: 5000});
    assert.equal(await login.locator('#basic-password').inputValue(), '');
    assert.equal(await login.locator('#webui-token').inputValue(), '');
    await login.locator('#server').fill('http://127.0.0.1:1');
    await credentialStatus.waitFor({state: 'hidden'});
  }
  await login.locator('#server').fill(fixture.url);
  if (!firstLogin) {
    await credentialStatus.waitFor({state: 'visible'});
    if (process.env.PET_KEEP_ARTIFACTS === '1') await login.locator('main').screenshot({ path: path.join(fixture.artifactDirectory, 'saved-credentials.png') });
  }
  if (firstLogin) {
    await login.locator('#connect').click();
    // The first HTTP 401 must return to the app-owned form without CDP or a native modal.
    await login.getByText('服务器要求 Nginx 登录', {exact: false}).waitFor({timeout: 8000});
    await login.locator('#basic-user').fill(BASIC_USER);
    await login.locator('#basic-password').fill('synthetic-invalid-password');
    await login.locator('#connect').click();
    await login.getByText('Nginx 未接受', {exact: false}).waitFor({timeout: 8000});
    assert.equal(await login.locator('#basic-password').inputValue(), '', 'clear a rejected password');
    await login.locator('#basic-password').fill(BASIC_PASSWORD);
    if (SAVED_LOGIN) await login.locator('#webui-token').fill(PASSWORD);
  }
  await login.locator('#connect').click();
  if (!SAVED_LOGIN && firstLogin) {
    const auth = await until(() => app.pages().find(page => page.url() === fixture.url + '/auth'), 'remote WebUI login');
    await auth.locator('#webui-password').fill(PASSWORD);
    await auth.locator('#webui-login button').click();
  }
  if (expectSaveWarning) {
    await login.getByText('登录成功，但未能加密保存 WebUI 凭据。').waitFor({timeout: 8000});
    await credentialStatus.filter({hasText: /^已加密保存：Nginx$/}).waitFor();
    assert.equal(await login.locator('#bot').inputValue(), BOT_ID);
    await login.getByRole('button', {name: '启动桌宠'}).click();
  }
  const pet = await until(() => localPage(app, '/pet/index.html'), 'pet window', 25_000);
  await pet.locator('#remote-indicator[data-connected="true"]').waitFor();
  const auth = await until(() => app.pages().find(page => page.url() === fixture.url + '/api/host/desktop-pet/session'), 'authenticated transport window');
  assert.equal(new URL(auth.url()).pathname, '/api/host/desktop-pet/session', 'authenticated transport must use the lightweight session page');
  if (process.env.PET_SMOKE_SHARED_PROFILE !== '1') assert.ok(app.profileCount() >= 2, 'local and remote must use separate WebView2 profiles');
  return { pet, auth };
}
async function openPanel(page, button, mode) {
  const current = await page.locator('#stage').getAttribute('data-mode'); if (current === mode) return;
  if (current !== 'menu') { if (current !== 'idle') await page.locator('#pet').click(); await page.locator('#pet').click(); }
  await page.locator(button).click(); await page.waitForFunction(expected => document.querySelector('#stage').dataset.mode === expected, mode);
}
async function isolation(app, auth, pet, fixture) {
  assert.deepEqual(await pet.evaluate(() => ({ process: typeof window.process, require: typeof window.require })), { process: 'undefined', require: 'undefined' });
  assert.equal(await auth.evaluate(() => typeof window.desktopPetBridge), 'undefined');
  const denied = await auth.evaluate(async () => {
    try { await window.__TAURI_INTERNALS__.invoke('pet_call', { method: 'todos', args: [false] }); return ''; } catch (error) { return String(error); }
  });
  assert.match(denied, /not allowed|denied|无权|不允许|invalid/i, 'remote WebUI must not invoke pet IPC');
  const nonceDenied = await auth.evaluate(async () => {
    try { await window.__TAURI_INTERNALS__.invoke('transport_message', { nonce: 'invalid', payload: { type: 'ready' } }); return ''; } catch (error) { return String(error); }
  });
  assert.match(nonceDenied, /不匹配|not allowed|denied/i);
  assert.equal(await auth.evaluate(() => document.cookie.includes('pet-smoke-session')), false, 'session must remain HttpOnly');
  const original = pet.url();
  await pet.evaluate(url => { window.location.assign(url); }, fixture.url + '/forbidden-navigation').catch(() => {});
  await delay(150); assert.equal(pet.url(), original, 'native navigation denial must prevent remote document commit');
  // WebView2 can issue the initial network request before denying the navigation. The URL is
  // intentionally credential-free; this check promises document/IPC isolation, not network isolation.
}
function skin() {
  return { schema_version: 1, id: 'smoke.skin', name: '测试声明式皮肤', version: '1',
    canvas: { width: 220, height: 240, anchor: [110, 240], bubble_anchor: [110, 22], hitbox: [36, 32, 148, 204] },
    resources: { 'idle.webp': SKIN_DIGEST, 'thinking.webp': EXTRA_SKIN_DIGEST }, states: { idle: 'idle', thinking: 'idle', talking: 'idle', dragging: 'idle' },
    events: {}, animations: { idle: { duration_ms: 1000, motion: 'bob', sequence: [{ file: 'idle.webp', rect: [0, 0, 330, 360], duration_ms: 1000 }] } } };
}
function skinPayload(manifest = skin()) {
  if (!LEGACY_SKIN) return {skin: manifest, skin_assets: true};
  return {skin: {...manifest, resources: {
    'idle.webp': 'data:image/webp;base64,' + SKIN_IMAGE.toString('base64'),
    'thinking.webp': 'data:image/webp;base64,' + EXTRA_SKIN_IMAGE.toString('base64'),
  }}};
}
async function run() {
  if (process.platform !== 'win32') throw new Error('This smoke validates Windows WebView2; run it on Windows');
  const temporary = await fs.mkdtemp(path.join(os.tmpdir(), 'maibot-tauri-smoke-'));
  const keep = process.env.PET_KEEP_ARTIFACTS === '1', fixture = new HostFixture(), pageErrors = [];
  fixture.artifactDirectory = temporary;
  let app, passed = false;
  try {
    await fixture.start(); app = await launch(temporary, pageErrors);
    let { pet, auth } = await connectClient(app, fixture, true);
    assert.equal(fixture.verifyCount, 1);
    assert.ok(fixture.basicChallenges > 0, 'reverse proxy must actually challenge the client');
    console.log('Tauri WebUI login passed through HTTP Basic authentication.');
    await pet.locator('#builtin-pet').waitFor({state: 'visible'});
    // Tauri adds style hashes to CSP; without style-src-attr, existing inline styles are ignored.
    // A visible empty canvas then pushes the built-in SVG below the window before the server sends a skin.
    assert.equal(await pet.locator('#skin-canvas').evaluate(el => getComputedStyle(el).display), 'none');
    assert.ok(await pet.locator('#builtin-pet').evaluate(el => {
      const box = el.getBoundingClientRect(); return box.width > 0 && box.top >= 0 && box.bottom <= innerHeight;
    }), 'built-in pet must remain inside the viewport before any skin command');
    if (process.env.PET_INSPECT_WINDOW === '1') {
      // CDP mouse events do not move the OS pointer or exercise native transparency/hit testing.
      if (!process.stdin.isTTY) throw new Error('PET_INSPECT_WINDOW requires an interactive terminal');
      console.log('Inspect the visible pet with the real mouse (enter/leave, menu, drag), then press Enter here to continue.');
      process.stdin.resume();
      await once(process.stdin, 'data');
      process.stdin.pause();
    }
    const installing = fixture.command('skin', skinPayload());
    if (!LEGACY_SKIN) {
      await pet.locator('#skin-loading').waitFor({state: 'visible', timeout: 500});
      assert.equal(await pet.locator('#builtin-pet').isVisible(), true, 'keep the previous pet while loading');
      assert.equal(await pet.locator('#skin-loading-ring').evaluate(el => getComputedStyle(el).animationName), 'skin-loading-spin');
      if (keep) await pet.screenshot({ path: path.join(temporary, 'skin-loading.png'), omitBackground: true });
    }
    const installed = await installing;
    await pet.locator('#skin-loading').waitFor({state: 'hidden'});
    assert.equal(installed.error, undefined); assert.equal(installed.result.id, 'smoke.skin');
    const firstFrame = await pet.locator('#skin-canvas').evaluate(canvas => {
      const box = canvas.getBoundingClientRect();
      const pixel = canvas.getContext('2d').getImageData(Math.floor(canvas.width / 2), Math.floor(canvas.height / 2), 1, 1).data;
      return {box: box.toJSON(), width: canvas.width, viewport: [innerWidth, innerHeight], devicePixelRatio, pixel: [...pixel]};
    });
    assert.ok(firstFrame.box.left >= 0 && firstFrame.box.right <= firstFrame.viewport[0]
      && firstFrame.box.top >= 0 && firstFrame.box.bottom <= firstFrame.viewport[1], JSON.stringify(firstFrame));
    assert.equal(firstFrame.width, Math.round(firstFrame.box.width * Math.min(4, firstFrame.devicePixelRatio)));
    assert.ok(firstFrame.pixel[3] !== 0, 'downloaded frame must not be blank');
    if (keep) await pet.screenshot({ path: path.join(temporary, 'skin-preview.png'), omitBackground: true });
    assert.equal(fixture.skinDownloads, LEGACY_SKIN ? 0 : 1);
    const cached = await fixture.command('skin', skinPayload({...skin(), id: 'smoke.cached'}));
    assert.equal(cached.result?.id, 'smoke.cached');
    assert.equal(fixture.skinDownloads, LEGACY_SKIN ? 0 : 1, 'switching to an already downloaded image must use the local cache');
    if (!LEGACY_SKIN) await until(() => fixture.skinPrefetches > 0, 'background skin asset prefetch');
    const info = await fixture.command('animation', { action: 'get_skin', args: {}, owner: 'smoke' }); assert.equal(info.result.id, 'smoke.cached');
    const geometry = await pet.evaluate(() => ({ width: innerWidth, height: innerHeight, outerWidth, outerHeight, devicePixelRatio,
      visualScale: visualViewport.scale, screen: { width: screen.width, height: screen.height, availWidth: screen.availWidth, availHeight: screen.availHeight } }));
    console.log('Window geometry:', JSON.stringify(geometry));
    assert.ok(geometry.width >= 259 && geometry.width <= 261, 'desktop pet must retain its 260 CSS-pixel width at Windows text scaling');
    assert.ok(geometry.height >= 460 && geometry.height <= 901);
    await openPanel(pet, '#orb-chat', 'chat');
    await pet.getByText('历史回复 29', { exact: true }).waitFor();
    await pet.locator('#log').evaluate(log => { log.scrollTop = 0; log.dispatchEvent(new WheelEvent('wheel', {deltaY: -120, bubbles: true})); });
    await pet.getByText('更早的历史回复', { exact: true }).waitFor();
    await pet.locator('#log').evaluate(log => { log.scrollTop = log.scrollHeight; });
    await pet.evaluate(() => {
      window.__petAudioStarted = false;
      document.addEventListener('playing', () => { window.__petAudioStarted = true; }, true);
      const NativeAudio = window.Audio;
      window.Audio = function (...args) {
        const audio = new NativeAudio(...args);
        audio.addEventListener('playing', () => { window.__petAudioStarted = true; });
        return audio;
      };
    });
    await pet.locator('#chat-input').fill('Tauri 真实界面聊天验证');
    await pet.locator('#chat-input').press('Enter'); await until(() => fixture.chats.length === 1, 'chat HTTP submission');
    assert.equal(fixture.chats[0].text, 'Tauri 真实界面聊天验证'); assert.deepEqual(fixture.chats[0].images, []);
    await pet.getByText('来自本机测试服务器的回复', { exact: true }).waitFor();
    await pet.waitForFunction(() => window.__petAudioStarted);
    await pet.waitForFunction(() => document.querySelector('#unread-indicator').hidden);
    await pet.waitForFunction(() => !document.querySelector('#chat-input').disabled && !document.querySelector('#chat-input').value);
    await openPanel(pet, '#orb-todo', 'todo'); await pet.locator('#todo-input').fill('Tauri 待办新增'); await pet.locator('#todo-save').click();
    await pet.locator('#todos .todo-title').filter({ hasText: 'Tauri 待办新增' }).waitFor();
    await pet.getByRole('button', { name: '编辑事项', exact: true }).click(); await pet.locator('#todo-input').fill('Tauri 待办修改'); await pet.locator('#todo-save').click();
    await pet.getByRole('button', { name: '完成并归档：Tauri 待办修改', exact: true }).click();
    await pet.waitForFunction(() => document.querySelector('#todos').children.length === 0); await pet.locator('#todo-history').click();
    await pet.locator('#todos .todo-title').filter({ hasText: 'Tauri 待办修改' }).waitFor();
    assert.deepEqual(fixture.writes.map(write => write.action), ['add', 'update', 'complete']);
    assert.equal(await pet.evaluate(image => window.desktopPetBridge.api.todo_image(image), IMAGE_ID), PNG_URL);
    console.log('Declarative skin, chat, todo lifecycle and image HTTP passed.');
    for (const type of ['capture_screen', 'sample_screen']) {
      const denied = await fixture.command(type, { threshold: 0.01, reset: true });
      assert.match(denied.error, /授权|允许|permission/i); assert.equal(denied.result, undefined);
    }
    await pet.evaluate(() => window.desktopPetBridge.api.open_exploration_records());
    const records = await until(() => localPage(app, '/pet/exploration.html'), 'records window');
    await records.locator('#detail h2').getByText('Tauri 冒烟探索记录', { exact: true }).waitFor();
    assert.equal(typeof await records.evaluate(() => window.desktopPetBridge.api.window_control('state')), 'boolean');
    const previousReady = fixture.readyCount, previousConnection = fixture.connection.id;
    fixture.connection.ws.close(1012, 'smoke reconnect'); await until(() => fixture.readyCount > previousReady, 'reconnection');
    await pet.locator('#remote-indicator[data-connected="true"]').waitFor(); assert.notEqual(fixture.connection.id, previousConnection);
    assert.equal((await pet.evaluate(() => window.desktopPetBridge.api.todos(true))).length, 1);
    assert.equal(fixture.chats.length, 1); assert.equal(fixture.writes.length, 3);
    fixture.conversationId = 'qq-group-session';
    const bound = await fixture.command('conversation', {conversation_id: fixture.conversationId,
      name: '测试群', platform: 'qq', target_id: '123456', chat_type: 'group'});
    assert.equal(bound.error, undefined);
    await openPanel(pet, '#orb-chat', 'chat');
    await pet.getByText('所选会话历史', {exact: false}).waitFor();
    assert.match(await pet.locator('#chat-conversation').textContent(), /测试群.*123456/);
    assert.equal(await pet.getByText('历史回复 29', {exact: true}).count(), 0);
    await fixture.command('conversation_message', {conversation_id: 'other-session', message_id: 'wrong',
      sender: '其他群', parts: [{kind: 'text', text: '不应显示的消息'}]});
    assert.equal(await pet.getByText('不应显示的消息').count(), 0);
    await fixture.command('conversation_message', {conversation_id: fixture.conversationId, message_id: 'incoming-qq',
      sender: '新群成员', parts: [{kind: 'text', text: '实时群聊消息'}]});
    await pet.getByText('新群成员', {exact: true}).waitFor();
    await pet.locator('#chat-input').fill('投递到所选会话');
    await pet.locator('#chat-input').press('Enter');
    await until(() => fixture.chats.length === 2, 'bound chat submission');
    assert.equal(fixture.chats[1].conversation_id, fixture.conversationId);
    fixture.conversationId = '';
    await fixture.command('conversation', {conversation_id: '', name: '主人私聊'});
    await pet.getByText('历史回复 29', {exact: true}).waitFor();
    assert.equal(await pet.getByText('新群成员', {exact: true}).count(), 0);
    if (keep) { await pet.screenshot({ path: path.join(temporary, 'pet.png') }); await records.screenshot({ path: path.join(temporary, 'records.png') }); }
    await isolation(app, auth, pet, fixture); assert.deepEqual(app.backgroundErrors.map(error => error.message), []);
    await app.close(); app = null; await delay(1000);
    if (SAVED_LOGIN) fixture.sessions.clear(); // Force a new WebUI login, not just cookie reuse.
    // Relaunch the same synthetic profile; the WebUI password is not supplied a second time.
    app = await launch(temporary, pageErrors); ({ pet, auth } = await connectClient(app, fixture, false));
    // No skin command has been sent on this connection. The existing skin must be visible already.
    await pet.locator('#skin-canvas').waitFor({state: 'visible', timeout: 2500});
    const before = await pet.evaluate(() => window.petSkin.command('get_skin').id);
    assert.equal(before, 'smoke.cached');
    const restored = await fixture.command('skin', skinPayload());
    assert.equal(restored.result?.id, 'smoke.skin');
    assert.equal(fixture.skinDownloads, LEGACY_SKIN ? 0 : 1, 'restarting the process must not download the same skin again');
    const prefetched = await pet.evaluate(digest => window.desktopPetBridge.api.skin_asset(digest), EXTRA_SKIN_DIGEST);
    assert.ok(prefetched.startsWith('data:image/webp;base64,'));
    assert.equal(fixture.skinPrefetches, LEGACY_SKIN ? 0 : 1, 'prefetched actions must also be reusable after restart');
    assert.equal(fixture.verifyCount, SAVED_LOGIN ? 2 : 1, 'restart must authenticate without asking for credentials');
    assert.equal(fixture.logoutCount, 0);
    assert.equal((await pet.evaluate(() => window.desktopPetBridge.api.todos(true))).length, 1);
    assert.ok(fixture.basicHTTP > 0 && fixture.basicWS >= fixture.readyCount);
    const saved = await fs.readFile(path.join(temporary, 'settings', 'settings.json'), 'utf8');
    for (const secret of [PASSWORD, BASIC_PASSWORD, BASIC, ...fixture.sessions]) assert.ok(!saved.includes(secret), 'settings must not persist browser secrets');
    if (SAVED_LOGIN) {
      const profile = createHash('sha256').update(fixture.url).digest('hex');
      for (const kind of ['nginx', 'webui']) {
        const encrypted = await fs.readFile(path.join(temporary, 'settings', 'saved-logins', profile, kind));
        for (const secret of [BASIC_USER, BASIC_PASSWORD, PASSWORD, BASIC]) {
          assert.ok(!encrypted.includes(secret), 'credential files must not contain plaintext');
        }
      }
    }
    await isolation(app, auth, pet, fixture); assert.deepEqual(fixture.failures.map(error => error.message), []); assert.deepEqual(pageErrors, []);
    if (SAVED_LOGIN) {
      await app.close(); app = null;
      const blocked = path.join(temporary, 'credential-write-failure');
      const profile = createHash('sha256').update(fixture.url).digest('hex');
      // The destination is a directory, so saving WebUI must fail after Nginx was saved.
      await fs.mkdir(path.join(blocked, 'settings', 'saved-logins', profile, 'webui'), {recursive: true});
      app = await launch(blocked, pageErrors);
      await connectClient(app, fixture, true, true);
      assert.deepEqual(fixture.failures.map(error => error.message), []); assert.deepEqual(pageErrors, []);
      console.log('A WebUI credential write failure was reported without blocking the connection.');
    }
    passed = true;
    console.log('Tauri smoke passed: BasicAuth HTTP/WS, restart login, skin assets, chat, todos, records, consent, IPC isolation, reconnect.');
  } catch (error) {
    if (app) {
      for (const [index, page] of app.pages().entries()) await page.screenshot({ path: path.join(temporary, `failure-${index}.png`) }).catch(() => {});
      await fs.writeFile(path.join(temporary, 'process.log'), app.output());
    }
    console.error(`Smoke artifacts retained at: ${temporary}`); if (fixture.failures.length) console.error(fixture.failures.map(error => error.message)); throw error;
  } finally {
    if (app) await app.close().catch(error => console.error(error.message)); await fixture.close();
    // Only delete the unique, verified temp child created here, never a supplied profile.
    if (passed && !keep && path.dirname(path.resolve(temporary)) === path.resolve(os.tmpdir())
      && path.basename(temporary).startsWith('maibot-tauri-smoke-')) {
      try { await fs.rm(temporary, { recursive: true, force: true, maxRetries: 10, retryDelay: 200 }); }
      catch (error) {
        if (!['EBUSY', 'EPERM'].includes(error.code)) throw error;
        console.log(`WebView2 still holds its temporary profile; retained at: ${temporary}`);
      }
    }
    else if (keep) console.log(`Smoke artifacts retained at: ${temporary}`);
  }
}
if (require.main === module) run().catch(error => { console.error(error); process.exitCode = 1; });
module.exports = { HostFixture };
