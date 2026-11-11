/* Runs only in the isolated, same-origin authentication WebView. Credentials stay in its browser profile. */
(() => {
  'use strict';

  const config = window.__petTransportConfig;
  const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
  if (window.top !== window.self || !object(config) || typeof config.nonce !== 'string'
    || typeof config.server !== 'string' || !window.__TAURI_INTERNALS__?.invoke) return;
  let server;
  try {
    const url = new URL(config.server);
    if (!['https:', 'http:'].includes(url.protocol) || url.origin !== window.location.origin
      || url.username || url.password || url.search || url.hash) return;
    server = url.href.replace(/\/+$/, '');
    const root = url.pathname.replace(/\/+$/, '');
    if (root && window.location.pathname !== root && !window.location.pathname.startsWith(root + '/')) return;
  } catch { return; }
  // PageFinished may run more than once for one document. Never create duplicate sockets/pollers.
  if (window.__petTransport?.nonce === config.nonce) return;
  window.__petTransport?.stop();

  const invoke = window.__TAURI_INTERNALS__.invoke.bind(window.__TAURI_INTERNALS__);
  const nonce = config.nonce;
  const encoder = new TextEncoder();
  const VERSION = 1;
  const MAX_HTTP_BYTES = 48 * 1024 * 1024;
  const MAX_AUTH_BYTES = 1024 * 1024;
  const MAX_SERVER_MESSAGE = 64 * 1024 * 1024;
  const MAX_ACK_BYTES = 24 * 1024 * 1024;
  const MAX_EVENT_BYTES = 64 * 1024;
  const REQUEST_ID = /^[0-9a-f]{32}$/;
  const USER_EVENTS = new Set(['clicked', 'buttons_refresh', 'button_click', 'drag_started', 'drag_ended',
    'screen_action', 'exploration_tasks', 'explore']);
  const pendingCommands = new Map();
  const pendingRequests = new Set();
  let options, controller, runner, connection, generation = 0;
  let authTimer, authController, authProbe, authenticated = false, authNotice = false, stopped = false;

  class ProtocolError extends Error {}
  class AuthRequired extends Error {}
  class HTTPError extends Error {
    constructor(status, message) { super(message); this.status = status; }
  }
  const messageOf = error => String(error?.message || error).slice(0, 1000);
  const sizeOf = value => encoder.encode(value).byteLength;
  const notify = payload => Promise.resolve(invoke('transport_message', { payload, nonce }));
  function publish(payload) {
    // Navigation destroys the IPC target; the new document will publish its own ready state.
    notify(payload).catch(() => {});
  }
  const state = (connected, text) => publish({ type: 'state', connected, text });

  async function readJSON(response, limit) {
    const declared = response.headers.get('content-length');
    if (declared !== null && (!/^\d+$/.test(declared) || Number(declared) > limit)) {
      await response.body?.cancel();
      throw new ProtocolError('服务器响应超过大小限制');
    }
    const reader = response.body?.getReader(), chunks = [];
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
      } finally { reader.releaseLock(); }
    }
    if (!/^application\/(?:[\w.+-]+\+)?json(?:\s*;|\s*$)/i.test(response.headers.get('content-type') || '')) {
      throw new ProtocolError('服务器响应不是 JSON');
    }
    const bytes = new Uint8Array(size);
    let offset = 0;
    for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
    let data;
    try { data = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes)); }
    catch { throw new ProtocolError('服务器返回了无效的 JSON'); }
    if (!object(data)) throw new ProtocolError('服务器响应必须是 JSON 对象');
    return data;
  }

  async function jsonRequest(url, { method = 'GET', payload, signal, headers = {}, limit = MAX_HTTP_BYTES,
    timeout = 60_000 } = {}) {
    const body = payload === undefined ? undefined : JSON.stringify(payload);
    if (body !== undefined && sizeOf(body) > MAX_HTTP_BYTES) throw new RangeError('图文请求不能超过 48 MiB');
    const abort = new AbortController();
    const aborted = () => abort.abort(signal.reason);
    if (signal?.aborted) abort.abort(signal.reason);
    else signal?.addEventListener('abort', aborted, { once: true });
    const timer = setTimeout(() => abort.abort(new Error('请求超时，结果尚未确认；请刷新记录后再操作')), timeout);
    try {
      // Browser-owned HTTP auth and HttpOnly cookies apply to HTTP and WS on this origin.
      // Never follow redirects with credentials to another site, or inspect Set-Cookie/Authorization.
      const response = await fetch(url, { method, body, credentials: 'include', redirect: 'error',
        cache: 'no-store', signal: abort.signal,
        headers: { Accept: 'application/json', ...(body === undefined ? {} : { 'Content-Type': 'application/json' }), ...headers },
      });
      if (response.status === 401) {
        await response.body?.cancel();
        throw new AuthRequired('登录已失效，请在 WebUI 中重新登录');
      }
      if (response.redirected || response.type === 'opaqueredirect' || response.status >= 300 && response.status < 400) {
        await response.body?.cancel();
        throw new ProtocolError('服务器返回重定向，请使用最终的 WebUI 地址');
      }
      let result;
      try { result = await readJSON(response, limit); }
      catch (error) {
        if (!response.ok) throw new HTTPError(response.status, `服务器返回 HTTP ${response.status}：${messageOf(error)}`);
        throw error;
      }
      if (!response.ok) throw new HTTPError(response.status,
        typeof result.detail === 'string' ? result.detail.slice(0, 1000) : `服务器返回 HTTP ${response.status}`);
      return result;
    } finally {
      clearTimeout(timer);
      signal?.removeEventListener('abort', aborted);
    }
  }

  function authRequired(reason) {
    authenticated = false;
    if (!authNotice) { authNotice = true; publish({ type: 'auth_required', reason }); }
    state(false, reason);
  }

  async function probeAuth(signal) {
    const status = await jsonRequest(server + '/api/webui/auth/check', { signal, limit: MAX_AUTH_BYTES, timeout: 15_000 });
    if (typeof status.authenticated !== 'boolean') throw new ProtocolError('服务器返回了无效的认证状态');
    if (!status.authenticated || status.requires_custom_token === true) {
      throw new AuthRequired(status.requires_custom_token === true ? '请先在 WebUI 完成首次设置' : '请在 WebUI 中登录');
    }
    const result = await jsonRequest(server + '/api/host/bots', { signal, limit: MAX_AUTH_BYTES, timeout: 15_000 });
    if (!Array.isArray(result.bots) || result.bots.some(bot => !object(bot) || !REQUEST_ID.test(bot.bot_id)
      || typeof bot.name !== 'string' || bot.name.length > 200)) throw new ProtocolError('服务器返回的机器人列表无效');
    const bots = result.bots.map(bot => ({ bot_id: bot.bot_id, name: bot.name,
      status: typeof bot.status === 'string' ? bot.status.slice(0, 32) : '' }));
    if (signal.aborted) return;
    const changed = !authenticated;
    authenticated = true;
    authNotice = false;
    if (changed) publish({ type: 'authenticated', bots });
  }

  function scheduleAuth(delay = 2000) {
    clearTimeout(authTimer);
    if (!stopped) authTimer = setTimeout(pollAuth, delay);
  }

  async function pollAuth() {
    if (stopped || authProbe) return;
    authController = new AbortController();
    const signal = authController.signal;
    authProbe = probeAuth(signal);
    try { await authProbe; }
    catch (error) {
      if (signal.aborted) return;
      if (error instanceof AuthRequired) authRequired(messageOf(error));
      else state(false, `等待 WebUI：${messageOf(error)}`);
    } finally {
      authProbe = null;
      if (!authenticated && !signal.aborted) scheduleAuth();
    }
  }

  function send(socket, event) {
    if (!connection || connection.socket !== socket || socket.readyState !== WebSocket.OPEN) throw new Error('桌宠已离线');
    const raw = JSON.stringify(event), limit = event.type === 'ack' ? MAX_ACK_BYTES : MAX_EVENT_BYTES;
    if (sizeOf(raw) > limit || socket.bufferedAmount + sizeOf(raw) > MAX_ACK_BYTES * 2) {
      throw new RangeError('桌宠消息超过大小限制');
    }
    socket.send(raw);
  }

  function commandResult(id, result, error) {
    const pending = pendingCommands.get(id);
    if (!pending || pending.session !== connection) return;
    pendingCommands.delete(id);
    clearTimeout(pending.timer);
    if (error !== undefined && error !== null) pending.reject(new Error(String(error).slice(0, 1000)));
    else pending.resolve(result === undefined ? {} : result);
  }

  function execute(command, session) {
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        pendingCommands.delete(command.id);
        reject(new Error('桌宠指令执行超时'));
      }, 25_000);
      pendingCommands.set(command.id, { resolve, reject, timer, session });
      notify({ type: 'command', id: command.id, command }).catch(error => {
        if (pendingCommands.get(command.id)?.session === session) commandResult(command.id, null, messageOf(error));
      });
    });
  }

  function connect(signal, selected, ready) {
    return new Promise((_resolve, reject) => {
      const base = `${server}/api/host/bots/${selected.botId}/proxy/api/webui/desktop-pet/remote`;
      const url = new URL(base + '/ws');
      url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
      const socket = new WebSocket(url.href);
      const session = { socket, base, connected: false, id: '', controller: new AbortController(), fail: null };
      connection = session;
      let finished = false, stage = 'hello', timer, heartbeat, lastPong = Date.now(), interval, timeout;
      let queue = Promise.resolve(), queued = 0, queuedBytes = 0;
      const queuedIds = new Set();
      const active = () => !finished && connection === session && !signal.aborted;
      const fail = error => {
        if (finished) return;
        finished = true;
        clearTimeout(timer); clearInterval(heartbeat);
        signal.removeEventListener('abort', aborted);
        session.controller.abort(new Error('桌宠连接已断开，结果尚未确认；请刷新记录后再操作'));
        for (const [id, pending] of pendingCommands) {
          if (pending.session !== session) continue;
          clearTimeout(pending.timer); pendingCommands.delete(id); pending.reject(error);
        }
        if (connection === session) connection = null;
        if (socket.readyState < WebSocket.CLOSING) socket.close();
        reject(error);
      };
      session.fail = fail;
      const aborted = () => fail(signal.reason || new Error('桌宠连接已停止'));
      const deadline = () => { clearTimeout(timer); timer = setTimeout(() => fail(new Error('服务器未及时确认桌宠连接')), 15_000); };
      signal.addEventListener('abort', aborted, { once: true });
      if (signal.aborted) { aborted(); return; }
      deadline();
      socket.addEventListener('open', () => {
        if (!active()) return;
        try { send(socket, { type: 'hello', version: VERSION, client_id: selected.clientId,
          capabilities: { screen_read: selected.allowScreenRead } }); } catch (error) { fail(error); }
      });
      socket.addEventListener('error', () => fail(new Error('无法连接服务器，请检查网络和 WebUI 登录状态')));
      socket.addEventListener('close', event => fail(event.code === 4001
        ? new AuthRequired('登录已失效，请在 WebUI 中重新登录')
        : new Error(`服务器连接已关闭（${event.code}）${event.reason ? '：' + event.reason.slice(0, 200) : ''}`)));
      socket.addEventListener('message', event => {
        if (!active()) return;
        try {
          if (typeof event.data !== 'string') throw new ProtocolError('服务器发送了不支持的二进制消息');
          const bytes = sizeOf(event.data);
          if (bytes > MAX_SERVER_MESSAGE) throw new ProtocolError('服务器消息超过大小限制');
          let command;
          try { command = JSON.parse(event.data); } catch { throw new ProtocolError('服务器返回了无效的 JSON'); }
          if (!object(command) || typeof command.type !== 'string') throw new ProtocolError('服务器指令格式无效');
          if (command.type === 'error') throw new Error(typeof command.error === 'string' ? command.error.slice(0, 1000) : '服务器拒绝桌宠连接');
          if (stage === 'hello') {
            if (command.type !== 'hello' || command.version !== VERSION || typeof command.connection_id !== 'string'
              || !/^[A-Za-z0-9_-]{1,128}$/.test(command.connection_id)) throw new ProtocolError('桌宠协议版本不兼容，请同步更新客户端和服务器');
            if (!Number.isFinite(command.heartbeat_interval) || !Number.isFinite(command.heartbeat_timeout)
              || command.heartbeat_interval < 0.01 || command.heartbeat_interval > 60
              || command.heartbeat_timeout < command.heartbeat_interval * 2 || command.heartbeat_timeout > 300) {
              throw new ProtocolError('服务器心跳设置无效');
            }
            interval = command.heartbeat_interval * 1000; timeout = command.heartbeat_timeout * 1000;
            session.id = command.connection_id; stage = 'ready'; deadline(); send(socket, { type: 'ready' });
            return;
          }
          if (stage === 'ready') {
            if (command.type !== 'ready' || command.version !== VERSION) throw new ProtocolError('服务器未确认桌宠就绪');
            stage = 'connected'; session.connected = true; clearTimeout(timer); lastPong = Date.now();
            ready();
            heartbeat = setInterval(() => {
              try {
                if (Date.now() - lastPong > timeout) throw new Error('服务器心跳超时');
                send(socket, { type: 'ping' });
              } catch (error) { fail(error); }
            }, interval);
            state(true, `已连接 · ${selected.botName || '远端麦麦'}`);
            return;
          }
          if (command.type === 'pong') { lastPong = Date.now(); return; }
          if (typeof command.id !== 'string' || !command.id || command.id.length > 128 || queuedIds.has(command.id)) {
            throw new ProtocolError('服务器指令缺少有效或唯一的回执 ID');
          }
          queued++; queuedBytes += bytes; queuedIds.add(command.id);
          if (queued > 64 || queuedBytes > 96 * 1024 * 1024) throw new ProtocolError('服务器指令积压过多');
          // Preserve skin/button/animation ordering. No command or ACK may cross a reconnection.
          queue = queue.then(async () => {
            if (!active()) return;
            try {
              const result = await execute(command, session);
              if (active()) send(socket, { type: 'ack', id: command.id, result });
            } catch (error) {
              if (active()) send(socket, { type: 'ack', id: command.id, error: messageOf(error) });
            }
          }).catch(fail).finally(() => { queued--; queuedBytes -= bytes; queuedIds.delete(command.id); });
        } catch (error) { fail(error); }
      });
    });
  }

  function delay(ms, signal) {
    return new Promise(resolve => {
      const done = () => { clearTimeout(timer); signal.removeEventListener('abort', done); resolve(); };
      const timer = setTimeout(done, ms);
      signal.addEventListener('abort', done, { once: true });
      if (signal.aborted) done();
    });
  }

  async function run(signal, selected) {
    let retry = 1000;
    while (!signal.aborted) {
      let error;
      try {
        // Browser WebSocket hides handshake HTTP status. Probe on every reconnect to identify expired auth.
        await probeAuth(signal);
        if (signal.aborted) break;
        state(false, '正在连接桌宠…');
        await connect(signal, selected, () => { retry = 1000; });
      } catch (caught) { error = caught; }
      if (signal.aborted) return;
      if (error instanceof AuthRequired) { authRequired(messageOf(error)); scheduleAuth(); return; }
      if (error instanceof ProtocolError || error instanceof HTTPError && [403, 429].includes(error.status)) {
        state(false, messageOf(error)); return;
      }
      state(false, `已离线：${messageOf(error)}；${retry / 1000} 秒后重连`);
      await delay(retry, signal);
      retry = Math.min(retry * 2, 30_000);
    }
  }

  function start(selected) {
    try {
      if (!object(selected) || typeof selected.botId !== 'string' || !REQUEST_ID.test(selected.botId)
        || typeof selected.clientId !== 'string' || !/^[A-Za-z0-9_-]{1,64}$/.test(selected.clientId)
        || typeof selected.allowScreenRead !== 'boolean') throw new TypeError('桌宠连接设置无效');
      if (runner && JSON.stringify(options) === JSON.stringify(selected)) return;
      stopConnection(); stopped = false; clearTimeout(authTimer); authController?.abort();
      options = { ...selected };
      const activeController = new AbortController();
      controller = activeController;
      // A held Web Lock keeps Chromium's hidden transport page eligible to service its live connection.
      // Other WebViews can omit the API; their native window controls background throttling instead.
      if (window.navigator?.locks) {
        window.navigator.locks.request('maibot-desktop-pet:' + nonce, { signal: controller.signal }, () => new Promise(resolve => {
          if (activeController.signal.aborted) resolve();
          else activeController.signal.addEventListener('abort', () => resolve(), { once: true });
        })).catch(error => {
          if (!activeController.signal.aborted) state(Boolean(connection?.connected), `无法保持后台连接：${messageOf(error)}`);
        });
      }
      const ownGeneration = ++generation;
      runner = run(controller.signal, options).finally(() => {
        activeController.abort();
        if (generation === ownGeneration) runner = null;
      });
    } catch (error) { state(false, messageOf(error)); }
  }

  function stopConnection() {
    generation++; controller?.abort(new Error('桌宠连接已停止'));
    connection?.fail(new Error('桌宠连接已停止'));
    runner = null;
  }

  function stop() {
    stopped = true; clearTimeout(authTimer); authController?.abort(); stopConnection();
    state(false, '已断开连接');
    // Do not log out: this browser profile deliberately retains the WebUI session for the next launch.
  }

  async function request(id, method, path, payload) {
    if (typeof id !== 'string' || !REQUEST_ID.test(id)) {
      publish({ type: 'response', id: typeof id === 'string' ? id.slice(0, 128) : '', ok: false, error: '请求 ID 无效' }); return;
    }
    if (pendingRequests.has(id)) return; // One terminal response for an in-flight correlation ID.
    pendingRequests.add(id);
    const session = connection;
    try {
      if (pendingRequests.size > 16) throw new Error('桌宠请求过多，请稍后重试');
      if (!session?.connected) throw new Error('桌宠未连接服务器，请等待连接恢复');
      const allowed = method === 'POST' && (path === '/todos' || /^\/chat\/[0-9a-f]{32}$/.test(path))
        || method === 'GET' && /^\/todos\/images\/[0-9a-f]{64}$/.test(path);
      if (!allowed) throw new TypeError('不支持的桌宠接口');
      if (method === 'POST' && !object(payload)) throw new TypeError('请求必须是 JSON 对象');
      if (method === 'GET') {
        if (payload !== null && payload !== undefined) throw new TypeError('图片读取不接受请求体');
        payload = undefined;
      }
      if (path === '/todos' && payload.action !== 'list'
        && (typeof payload.request_id !== 'string' || !REQUEST_ID.test(payload.request_id))) {
        throw new TypeError('修改待办必须提供 32 位十六进制 request_id');
      }
      const data = await jsonRequest(session.base + path, { method, payload, signal: session.controller.signal,
        headers: { 'X-Desktop-Pet-Connection': session.id } });
      if (connection !== session || !session.connected) throw new Error('桌宠连接已变化，请刷新记录确认操作结果');
      publish({ type: 'response', id, ok: true, data });
    } catch (error) {
      if (error instanceof AuthRequired && !stopped && connection === session) {
        session?.fail(error); authRequired(messageOf(error)); scheduleAuth();
      }
      publish({ type: 'response', id, ok: false, error: messageOf(error), ...(error.status ? { status: error.status } : {}) });
    } finally { pendingRequests.delete(id); }
  }

  function sendEvent(event) {
    try {
      if (!connection?.connected) throw new Error('桌宠未连接服务器');
      if (!object(event) || !USER_EVENTS.has(event.type)) throw new TypeError('不支持的桌宠事件');
      send(connection.socket, event);
    } catch (error) { state(Boolean(connection?.connected), messageOf(error)); }
  }

  Object.defineProperty(window, '__petTransport', { configurable: true,
    value: Object.freeze({ nonce, start, stop, request, sendEvent, commandResult }) });
  window.addEventListener('pagehide', stop, { once: true });
  notify({ type: 'ready' }).then(() => scheduleAuth(0)).catch(stop);
})();
