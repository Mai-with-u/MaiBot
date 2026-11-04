'use strict';

const { randomUUID } = require('node:crypto');
const { EventEmitter } = require('node:events');
const { setTimeout: sleep } = require('node:timers/promises');
const { CookieJar } = require('tough-cookie');
const WebSocket = require('ws');

const VERSION = 1;
const MAX_HTTP_BYTES = 48 * 1024 * 1024;
const MAX_AUTH_BYTES = 1024 * 1024;
const MAX_SERVER_MESSAGE = 64 * 1024 * 1024;
const MAX_ACK_BYTES = 24 * 1024 * 1024;
const MAX_EVENT_BYTES = 64 * 1024;
const HTTP_TIMEOUT = 60_000;
const HANDSHAKE_TIMEOUT = 15_000;
const REQUEST_ID = /^[0-9a-f]{32}$/;
const USER_EVENTS = new Set([
  'clicked', 'buttons_refresh', 'button_click', 'drag_started', 'drag_ended',
  'screen_action', 'exploration_tasks', 'explore',
]);

class AuthenticationError extends Error {}
class SessionExpiredError extends Error {}
class ProtocolError extends Error {}
class HTTPError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

function object(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function messageOf(error) {
  return (error instanceof Error ? error.message : String(error)).slice(0, 1000);
}

function normalizeServer(value) {
  if (typeof value !== 'string' || !value.trim() || value.includes('\\')) {
    throw new TypeError('服务器地址必须以 http:// 或 https:// 开头');
  }
  let url;
  try { url = new URL(value.trim()); } catch { throw new TypeError('服务器地址无效'); }
  if (!['http:', 'https:'].includes(url.protocol) || !url.hostname) {
    throw new TypeError('服务器地址必须以 http:// 或 https:// 开头');
  }
  if (url.username || url.password || url.search || url.hash) {
    throw new TypeError('服务器地址不能包含用户名、密码、查询参数或锚点');
  }
  return url.href.replace(/\/+$/, '');
}

function passwordValue(password) {
  if (typeof password !== 'string' || !password || password.length > 4096) {
    throw new TypeError('请填写有效的 WebUI 密码');
  }
  return password;
}

async function readJSON(response, limit) {
  const declared = response.headers.get('content-length');
  if (declared !== null && (!/^\d+$/.test(declared) || Number(declared) > limit)) {
    await response.body?.cancel();
    throw new ProtocolError('服务器响应超过大小限制');
  }
  const reader = response.body?.getReader();
  const chunks = [];
  let size = 0;
  if (reader) {
    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        size += value.byteLength;
        if (size > limit) {
          await reader.cancel();
          throw new ProtocolError('服务器响应超过大小限制');
        }
        chunks.push(value);
      }
    } finally {
      reader.releaseLock();
    }
  }
  if (!/^application\/(?:[\w.+-]+\+)?json(?:\s*;|\s*$)/i.test(response.headers.get('content-type') || '')) {
    throw new ProtocolError('服务器响应不是 JSON');
  }
  let data;
  try {
    const raw = new TextDecoder('utf-8', { fatal: true }).decode(Buffer.concat(chunks, size));
    data = JSON.parse(raw);
  } catch {
    throw new ProtocolError('服务器返回了无效的 JSON');
  }
  if (!object(data)) throw new ProtocolError('服务器响应必须是 JSON 对象');
  return data;
}

async function jsonRequest(jar, url, { method = 'GET', payload, signal, timeout = HTTP_TIMEOUT,
  headers = {}, limit = MAX_HTTP_BYTES } = {}) {
  const body = payload === undefined ? undefined : JSON.stringify(payload);
  if (body !== undefined && Buffer.byteLength(body) > MAX_HTTP_BYTES) {
    throw new RangeError('图文请求不能超过 48 MiB');
  }
  const cookie = await jar.getCookieString(url);
  const signals = [AbortSignal.timeout(timeout)];
  if (signal) signals.push(signal);
  const response = await fetch(url, {
    method, body, redirect: 'manual', signal: AbortSignal.any(signals),
    headers: { Accept: 'application/json', ...(body === undefined ? {} : { 'Content-Type': 'application/json' }),
      ...(cookie ? { Cookie: cookie } : {}), ...headers },
  });
  if (response.status >= 300 && response.status < 400) {
    await response.body?.cancel();
    throw new ProtocolError('服务器返回重定向，请填写最终的宿主地址');
  }
  for (const cookieHeader of response.headers.getSetCookie()) await jar.setCookie(cookieHeader, url);
  let result;
  try {
    result = await readJSON(response, limit);
  } catch (error) {
    if (response.status >= 400) throw new HTTPError(response.status, `服务器返回 HTTP ${response.status}：${messageOf(error)}`);
    throw error;
  }
  if (response.status >= 400) {
    throw new HTTPError(response.status, typeof result.detail === 'string'
      ? result.detail.slice(0, 1000) : `服务器返回 HTTP ${response.status}`);
  }
  return result;
}

async function authenticate(jar, server, password, signal) {
  let result;
  try {
    result = await jsonRequest(jar, server + '/api/webui/auth/verify', {
      method: 'POST', payload: { token: password }, signal, timeout: HANDSHAKE_TIMEOUT, limit: MAX_AUTH_BYTES,
    });
  } catch (error) {
    if (error instanceof HTTPError && [401, 403, 429].includes(error.status)) {
      throw new AuthenticationError('登录失败或尝试过多，请检查 WebUI 密码后重新连接');
    }
    throw error;
  }
  if (result.valid !== true) throw new AuthenticationError('登录失败，请检查 WebUI 密码后重新连接');
}

async function logout(jar, server) {
  try {
    if (await jar.getCookieString(server + '/api/webui/auth/logout')) {
      await jsonRequest(jar, server + '/api/webui/auth/logout', {
        method: 'POST', timeout: 3000, limit: MAX_AUTH_BYTES,
      });
    }
  } finally {
    await jar.removeAllCookies();
  }
}

async function discoverBots(server, password) {
  const base = normalizeServer(server);
  passwordValue(password);
  const jar = new CookieJar();
  await authenticate(jar, base, password);
  try {
    const result = await jsonRequest(jar, base + '/api/host/bots', { timeout: HANDSHAKE_TIMEOUT, limit: MAX_AUTH_BYTES });
    if (!Array.isArray(result.bots) || result.bots.some(bot => !object(bot) || typeof bot.bot_id !== 'string')) {
      throw new ProtocolError('服务器返回的机器人列表无效');
    }
    return result.bots;
  } finally {
    // 发现操作不保留临时登录；注销失败也清空内存 Cookie，不覆盖原来的读取结果。
    await logout(jar, base).catch(() => {});
  }
}

class RemoteConnection extends EventEmitter {
  constructor(options, { executeCommand } = {}) {
    super();
    if (!object(options)) throw new TypeError('缺少连接设置');
    if (typeof executeCommand !== 'function') throw new TypeError('必须提供 executeCommand');
    const server = normalizeServer(options.server);
    if (typeof options.botId !== 'string' || !REQUEST_ID.test(options.botId)) throw new TypeError('Bot 实例 ID 无效');
    const clientId = options.clientId === undefined ? randomUUID().replaceAll('-', '') : options.clientId;
    if (typeof clientId !== 'string' || !/^[A-Za-z0-9_-]{1,64}$/.test(clientId)) throw new TypeError('客户端 ID 无效');
    if (options.allowScreenRead !== undefined && typeof options.allowScreenRead !== 'boolean') {
      throw new TypeError('allowScreenRead 必须是布尔值');
    }
    this.options = { ...options, server, password: passwordValue(options.password), clientId,
      allowScreenRead: options.allowScreenRead === true };
    this.base = `${server}/api/host/bots/${options.botId}/proxy/api/webui/desktop-pet/remote`;
    this.executeCommand = executeCommand;
    this.connected = false;
    this._jar = new CookieJar();
    this._runner = null;
    this._stopping = null;
    this._controller = null;
    this._socket = null;
    this._connectionId = '';
    this._endConnection = null;
    this._sessionController = null;
  }

  start() {
    if (this._runner || this._stopping) return;
    this._controller = new AbortController();
    this._runner = this._run(this._controller.signal).finally(() => { this._runner = null; });
  }

  async stop() {
    if (this._stopping) return this._stopping;
    this._stopping = this._stop();
    try { await this._stopping; } finally { this._stopping = null; }
  }

  async _stop() {
    this._controller?.abort(new Error('桌宠连接已停止'));
    this._endConnection?.(new Error('桌宠连接已停止'));
    this._disconnect();
    if (this._runner) await this._runner;
    await logout(this._jar, this.options.server).catch(() => {});
    this.emit('state', { connected: false, text: '已断开连接' });
  }

  _disconnect() {
    const connected = this.connected;
    this.connected = false;
    this._connectionId = '';
    if (connected) this.emit('disconnected');
  }

  async _run(signal) {
    let authenticated = false;
    let retry = 1000;
    while (!signal.aborted) {
      let error;
      try {
        if (!authenticated) {
          this.emit('state', { connected: false, text: '正在登录服务器…' });
          await authenticate(this._jar, this.options.server, this.options.password, signal);
          authenticated = true;
        }
        await this._connect(signal);
      } catch (caught) {
        error = caught;
      } finally {
        if (this.connected) retry = 1000;
        this._disconnect();
      }
      if (signal.aborted) break;
      if (error instanceof AuthenticationError || error instanceof ProtocolError) {
        this.emit('state', { connected: false, text: messageOf(error) });
        return;
      }
      if (error instanceof SessionExpiredError) {
        authenticated = false;
        await this._jar.removeAllCookies();
      }
      this.emit('state', { connected: false,
        text: `已离线：${messageOf(error)}；${retry / 1000} 秒后重连` });
      try { await sleep(retry, undefined, { signal }); } catch { break; }
      retry = Math.min(retry * 2, 30_000);
    }
  }

  async request(method, path, payload) {
    if (!this.connected || !this._sessionController) throw new Error('桌宠未连接服务器，请等待连接恢复');
    const allowed = (method === 'POST' && (path === '/todos' || /^\/chat\/[0-9a-f]{32}$/.test(path)))
      || (method === 'GET' && /^\/todos\/images\/[0-9a-f]{64}$/.test(path));
    if (!allowed) throw new TypeError('不支持的桌宠接口');
    if (method === 'POST' && !object(payload)) throw new TypeError('请求必须是 JSON 对象');
    if (path === '/todos' && payload.action !== 'list' && !REQUEST_ID.test(payload.request_id || '')) {
      throw new TypeError('修改待办必须提供 32 位十六进制 request_id');
    }
    const connectionId = this._connectionId;
    try {
      const result = await jsonRequest(this._jar, this.base + path, {
        method, payload, signal: this._sessionController.signal,
        headers: { 'X-Desktop-Pet-Connection': connectionId },
      });
      if (!this.connected || this._connectionId !== connectionId) throw new Error('桌宠连接已变化，请刷新记录确认操作结果');
      return result;
    } catch (error) {
      if (error instanceof HTTPError && error.status === 401) {
        this._endConnection?.(new SessionExpiredError('宿主登录会话已失效'));
      }
      if (error.name === 'TimeoutError' || error.name === 'AbortError') {
        throw new Error('请求超时或连接已断开，结果尚未确认；请刷新记录后再操作');
      }
      throw error;
    }
  }

  async sendEvent(event) {
    if (!this.connected || !this._socket) throw new Error('桌宠未连接服务器');
    if (!object(event) || !USER_EVENTS.has(event.type)) throw new TypeError('不支持的桌宠事件');
    await this._send(this._socket, event);
  }

  _send(socket, event) {
    if (socket !== this._socket || socket.readyState !== WebSocket.OPEN) return Promise.reject(new Error('桌宠已离线'));
    let raw;
    try {
      raw = JSON.stringify(event);
      if (Buffer.byteLength(raw) > (event.type === 'ack' ? MAX_ACK_BYTES : MAX_EVENT_BYTES)) {
        throw new RangeError('桌宠消息超过大小限制');
      }
    } catch (error) { return Promise.reject(error); }
    return new Promise((resolve, reject) => {
      socket.send(raw, error => error ? reject(error) : resolve());
    });
  }

  async _connect(signal) {
    const cookie = await this._jar.getCookieString(this.base + '/ws');
    signal.throwIfAborted();
    const url = new URL(this.base + '/ws');
    url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
    await new Promise((resolve, reject) => {
      const socket = new WebSocket(url, {
        headers: cookie ? { Cookie: cookie } : {}, followRedirects: false, rejectUnauthorized: true,
        handshakeTimeout: HANDSHAKE_TIMEOUT, maxPayload: MAX_SERVER_MESSAGE, perMessageDeflate: false,
      });
      this._socket = socket;
      this._sessionController = new AbortController();
      let stage = 'hello';
      let finished = false;
      let timer;
      let heartbeat;
      let interval = 20_000;
      let timeout = 60_000;
      let lastPong = Date.now();
      let queue = Promise.resolve();
      let queued = 0;
      let queuedBytes = 0;
      const active = () => !finished && socket === this._socket && !signal.aborted;
      const fail = error => {
        if (finished) return;
        finished = true;
        clearTimeout(timer);
        clearInterval(heartbeat);
        signal.removeEventListener('abort', aborted);
        this._sessionController.abort();
        this._endConnection = null;
        this._socket = null;
        if (socket.readyState !== WebSocket.CLOSED) socket.terminate();
        reject(error);
      };
      const aborted = () => fail(signal.reason);
      const deadline = () => {
        clearTimeout(timer);
        timer = setTimeout(() => fail(new Error('服务器未及时确认桌宠连接')), HANDSHAKE_TIMEOUT);
      };
      this._endConnection = fail;
      signal.addEventListener('abort', aborted, { once: true });
      deadline();
      socket.on('open', () => {
        this._send(socket, { type: 'hello', version: VERSION, client_id: this.options.clientId,
          capabilities: { screen_read: this.options.allowScreenRead } }).catch(fail);
      });
      socket.on('unexpected-response', (request, response) => {
        const status = response.statusCode;
        response.destroy();
        request.destroy();
        if (status === 401) fail(new SessionExpiredError('宿主登录会话已失效'));
        else if ([403, 429].includes(status)) fail(new AuthenticationError(`服务器拒绝连接：HTTP ${status}`));
        else if (status >= 300 && status < 400) fail(new ProtocolError('服务器返回重定向，请填写最终的宿主地址'));
        else fail(new Error(`服务器拒绝连接：HTTP ${status}`));
      });
      socket.on('error', fail);
      socket.on('close', code => fail(code === 4001
        ? new SessionExpiredError('宿主登录会话已失效') : new Error(`服务器连接已关闭（${code}）`)));
      socket.on('message', (raw, binary) => {
        if (!active()) return;
        let command;
        try {
          if (binary) throw new ProtocolError('服务器发送了不支持的二进制消息');
          try { command = JSON.parse(raw.toString('utf8')); } catch { throw new ProtocolError('服务器返回了无效的 JSON'); }
          if (!object(command) || typeof command.type !== 'string') throw new ProtocolError('服务器指令格式无效');
          if (command.type === 'error') throw new Error(typeof command.error === 'string' ? command.error.slice(0, 1000) : '服务器拒绝桌宠连接');
          if (stage === 'hello') {
            if (command.type !== 'hello' || command.version !== VERSION
              || typeof command.connection_id !== 'string' || !/^[A-Za-z0-9_-]{1,128}$/.test(command.connection_id)) {
              throw new ProtocolError('桌宠协议版本不兼容，请同步更新客户端和服务器');
            }
            if (!Number.isFinite(command.heartbeat_interval) || !Number.isFinite(command.heartbeat_timeout)
              || command.heartbeat_interval < 0.01 || command.heartbeat_interval > 60
              || command.heartbeat_timeout < command.heartbeat_interval * 2 || command.heartbeat_timeout > 300) {
              throw new ProtocolError('服务器心跳设置无效');
            }
            interval = command.heartbeat_interval * 1000;
            timeout = command.heartbeat_timeout * 1000;
            this._connectionId = command.connection_id;
            stage = 'ready';
            deadline();
            this._send(socket, { type: 'ready' }).catch(fail);
            return;
          }
          if (stage === 'ready') {
            if (command.type !== 'ready' || command.version !== VERSION) throw new ProtocolError('服务器未确认桌宠就绪');
            stage = 'connected';
            clearTimeout(timer);
            lastPong = Date.now();
            heartbeat = setInterval(() => {
              if (Date.now() - lastPong > timeout) { fail(new Error('服务器心跳超时')); return; }
              this._send(socket, { type: 'ping' }).catch(fail);
            }, interval);
            heartbeat.unref();
            this.connected = true;
            this.emit('state', { connected: true, text: `已连接 · ${this.options.botName || '远端麦麦'}` });
            this.emit('connected');
            return;
          }
          if (command.type === 'pong') { lastPong = Date.now(); return; }
          if (typeof command.id !== 'string' || !command.id || command.id.length > 128) {
            throw new ProtocolError('服务器指令缺少有效的回执 ID');
          }
          queued += 1;
          queuedBytes += raw.byteLength;
          if (queued > 64 || queuedBytes > 96 * 1024 * 1024) throw new ProtocolError('服务器指令积压过多');
          // 原生窗口指令按到达顺序执行；断线后不执行积压指令，也不把旧回执发给新连接。
          queue = queue.then(async () => {
            if (!active()) return;
            try {
              const result = await this.executeCommand(command);
              if (active()) await this._send(socket, { type: 'ack', id: command.id, result });
            } catch (error) {
              if (active()) await this._send(socket, { type: 'ack', id: command.id, error: messageOf(error) });
            }
          }).catch(fail).finally(() => { queued -= 1; queuedBytes -= raw.byteLength; });
        } catch (error) { fail(error); }
      });
    });
  }
}

module.exports = { normalizeServer, discoverBots, RemoteConnection };
