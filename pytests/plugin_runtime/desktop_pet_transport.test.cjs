'use strict';

const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { resolve } = require('node:path');
const { test } = require('node:test');
const { setImmediate: flush } = require('node:timers/promises');
const vm = require('node:vm');

const renderer = resolve(__dirname, '../../pet-clients/desktop/renderer');
const source = readFileSync(resolve(renderer, 'transport.js'), 'utf8');
const server = 'https://pet.test';
const botId = 'a'.repeat(32);
const assetPath = '/skins/assets/' + 'b'.repeat(64);
const requestId = 'c'.repeat(32);

function setup({ fetch, forceToken = false, authStatus = () => ({ authenticated: true }) }) {
  const messages = [], timers = new Map();
  const location = new URL(server + '/auth');
  let socket;
  class WebSocket extends EventTarget {
    static OPEN = 1;
    readyState = 1;
    bufferedAmount = 0;
    constructor() { super(); socket = this; }
    send() {}
    close() {}
  }
  class FileReader {
    async readAsDataURL(blob) {
      this.result = `data:${blob.type};base64,${Buffer.from(await blob.arrayBuffer()).toString('base64')}`;
      this.onload();
    }
  }
  const window = new EventTarget();
  Object.assign(window, {
    top: window, self: window, location,
    __petTransportConfig: { server, nonce: 'test', tryToken: forceToken, forceToken },
    __TAURI_INTERNALS__: { async invoke(_command, { payload }) { messages.push(payload); } },
  });
  vm.runInNewContext(source, {
    window, location, URL, TextEncoder, TextDecoder, AbortController, AbortSignal: {}, Blob, FileReader, WebSocket,
    fetch(url, options) {
      const path = new URL(url).pathname;
      if (path === '/api/webui/auth/check') return Response.json(authStatus());
      if (path === '/api/host/bots') return Response.json({ bots: [] });
      return fetch(path, options);
    },
    setTimeout(callback, ms) { const id = Symbol(); timers.set(id, { callback, ms }); return id; },
    clearTimeout(id) { timers.delete(id); },
    setInterval() {}, clearInterval() {},
  });
  const api = window.__petTransport;
  function fireTimer(ms) {
    const entry = [...timers].find(([, timer]) => timer.ms === ms);
    if (!entry) return false;
    const [id, { callback }] = entry;
    timers.delete(id);
    callback();
    return true;
  }
  async function connect() {
    await flush();
    api.start({ botId, clientId: 'test', allowScreenRead: false });
    await flush();
    for (const message of [
      { type: 'hello', version: 1, connection_id: 'test', heartbeat_interval: 1, heartbeat_timeout: 2 },
      { type: 'ready', version: 1 },
    ]) socket.dispatchEvent(new MessageEvent('message', { data: JSON.stringify(message) }));
  }
  return { api, location, messages, timers, fireTimer, connect };
}

test('skin fetch works without AbortSignal statics and clears its timer after reading', async () => {
  const ctx = setup({ fetch: (_path, { signal }) => {
    assert.equal(signal.aborted, false);
    return new Response('image', { headers: { 'Content-Type': 'image/webp' } });
  } });
  await ctx.connect();
  await ctx.api.request(requestId, 'GET', assetPath);
  const response = ctx.messages.find(message => message.id === requestId);
  assert.equal(response.ok, true, response.error);
  assert.equal(response.data.data_url, 'data:image/webp;base64,aW1hZ2U=');
  assert.equal(ctx.fireTimer(20_000), false);
  ctx.api.stop();
});

for (const timedOut of [true, false]) {
  test(`skin fetch aborts while reading the body on ${timedOut ? 'timeout' : 'disconnect'}`, async () => {
    let signal;
    const ctx = setup({ fetch: (_path, options) => {
      signal = options.signal;
      return new Response(new ReadableStream({ start(stream) {
        signal.addEventListener('abort', () => stream.error(signal.reason), { once: true });
      } }), { headers: { 'Content-Type': 'image/webp' } });
    } });
    await ctx.connect();
    const pending = ctx.api.request(requestId, 'GET', assetPath);
    await flush();
    if (timedOut) assert.equal(ctx.fireTimer(20_000), true);
    else ctx.api.stop();
    assert.ok(signal?.aborted);
    await pending;
    assert.equal(ctx.messages.find(message => message.id === requestId).ok, false);
    assert.equal(ctx.fireTimer(20_000), false);
    ctx.api.stop();
  });
}

for (const [name, fetch] of [
  ['rejection', () => Response.json({ valid: false })],
  ['network error', () => { throw new TypeError('Failed to fetch'); }],
  ['rate limit', () => Response.json({ detail: 'Try again later' }, { status: 429 })],
  ['invalid reply', () => Response.json({})],
]) {
  test(`explicit WebUI password ${name} offers retry instead of using an existing session`, async () => {
    const ctx = setup({ forceToken: true, fetch });
    await flush();
    assert.equal(ctx.fireTimer(0), true);
    await flush();
    assert.ok(ctx.messages.some(({ type }) => type === 'token_required'));
    await ctx.api.verifyToken('test-password');
    ctx.fireTimer(0);
    await flush();
    assert.ok(ctx.messages.some(({ type }) => type === 'token_invalid'));
    assert.ok(!ctx.messages.some(({ type }) => type === 'authenticated'));
    assert.equal(ctx.timers.size, 0);
  });
}

test('explicit WebUI password is verified before the connection accepts the session', async () => {
  const ctx = setup({ forceToken: true, fetch: () => Response.json({ valid: true }) });
  await flush();
  ctx.fireTimer(0);
  await flush();
  assert.ok(!ctx.messages.some(({ type }) => type === 'authenticated'));
  await ctx.api.verifyToken('test-password');
  ctx.fireTimer(0);
  await flush();
  assert.deepEqual(ctx.messages.filter(({ type }) => type.startsWith('token_') || type === 'authenticated')
    .map(({ type }) => type), ['token_required', 'token_verified', 'authenticated']);
  ctx.api.stop();
});

test('first setup notifies native verification but waits for a custom password', async () => {
  let setupPending = true;
  const ctx = setup({
    forceToken: true,
    fetch: () => Response.json({ valid: true, requires_custom_token: true }),
    authStatus: () => ({ authenticated: true, requires_custom_token: setupPending }),
  });
  await flush();
  assert.equal(ctx.fireTimer(0), true);
  await flush();
  await ctx.api.verifyToken('test-temporary-password');
  const verified = ctx.messages.find(({ type }) => type === 'token_verified');
  assert.equal(verified?.requires_custom_token, true);
  assert.equal(ctx.fireTimer(0), true);
  await flush();
  assert.ok(ctx.messages.some(({ type }) => type === 'auth_required'));
  assert.ok(!ctx.messages.some(({ type }) => type === 'authenticated'));

  setupPending = false;
  assert.equal(ctx.fireTimer(2000), true);
  await flush();
  assert.ok(ctx.messages.some(({ type }) => type === 'authenticated'));
  ctx.api.stop();
});

test('the WebUI route changing during explicit sign-in does not resume cookie discovery', async () => {
  const ctx = setup({ forceToken: true });
  await flush();
  ctx.fireTimer(0);
  await flush();
  assert.ok(ctx.messages.some(({ type }) => type === 'token_required'));
  ctx.location.pathname = '/';
  ctx.fireTimer(2000);
  await flush();
  assert.ok(ctx.messages.some(({ type }) => type === 'token_invalid'));
  assert.ok(!ctx.messages.some(({ type }) => type === 'authenticated'));
});

test('WebUI verification failure clears the password and re-enables the connection form for retry', async () => {
  const elements = new Map();
  let focused;
  function querySelector(id) {
    if (!elements.has(id)) {
      elements.set(id, {
        value: '', hidden: false, validity: { valid: true }, classList: { toggle() {} },
        addEventListener(name, handler) { this[name] = handler; },
        replaceChildren() {}, focus() { focused = id; },
      });
    }
    return elements.get(id);
  }
  const login = {
    async profile() { return { server, rememberCredentials: true }; },
    async credential_status() { return {}; },
    async discover() { throw new Error('WebUI 密码无效或已更改，请在连接页重新填写'); },
  };
  vm.runInNewContext(readFileSync(resolve(renderer, 'login.js'), 'utf8'), {
    window: { desktopPetBridge: { login } }, document: { querySelector },
    ResizeObserver: class { observe() {} }, Option: class {},
  });
  await flush();
  const input = querySelector('#webui-token');
  input.value = 'test-password';
  await querySelector('#connection').submit({ preventDefault() {} });
  assert.equal(input.value, '');
  assert.equal(focused, '#webui-token');
  assert.equal(querySelector('#credentials').open, true);
  assert.equal(querySelector('#connect').disabled, false);
  assert.match(querySelector('#status').textContent, /重新输入/);
});
