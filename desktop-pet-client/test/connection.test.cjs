'use strict';

const assert = require('node:assert/strict');
const { EventEmitter, once } = require('node:events');
const http = require('node:http');
const { setTimeout: sleep } = require('node:timers/promises');
const test = require('node:test');
const { WebSocketServer, WebSocket } = require('ws');
const { normalizeServer, discoverBots, RemoteConnection } = require('../src/connection.cjs');

const BOT = 'a'.repeat(32);
const REMOTE = `/api/host/bots/${BOT}/proxy/api/webui/desktop-pet/remote`;

function deferred() {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return { promise, resolve };
}

function event(emitter, name, predicate = () => true, timeout = 6000) {
  return new Promise((resolve, reject) => {
    const listener = value => {
      if (!predicate(value)) return;
      clearTimeout(timer);
      emitter.off(name, listener);
      resolve(value);
    };
    const timer = setTimeout(() => {
      emitter.off(name, listener);
      reject(new Error(`Timed out waiting for ${name}`));
    }, timeout);
    emitter.on(name, listener);
  });
}

function json(response, body, status = 200, headers = {}) {
  response.writeHead(status, { 'content-type': 'application/json', ...headers });
  response.end(JSON.stringify(body));
}

async function host(t, options = {}) {
  const fixture = {
    events: new EventEmitter(), logins: 0, logouts: 0, upgrades: 0, requests: [],
    hellos: [], messages: [], sockets: [], clients: [], sessions: new Set(),
  };
  const server = http.createServer(async (request, response) => {
    try {
      const chunks = [];
      for await (const chunk of request) chunks.push(chunk);
      const raw = Buffer.concat(chunks).toString();
      const body = raw ? JSON.parse(raw) : undefined;
      fixture.requests.push({ path: request.url, headers: request.headers, body });
      if (request.url === '/api/webui/auth/verify') {
        fixture.logins += 1;
        fixture.events.emit('login', body);
        if (options.loginGate) await options.loginGate.promise;
        if (body.token !== 'test-password') { json(response, { valid: false }); return; }
        const session = `session-${fixture.logins}`;
        fixture.sessions.add(session);
        json(response, { valid: true }, 200, { 'set-cookie': `maibot_host_session_test=${session}; HttpOnly; Path=/; SameSite=Lax` });
        return;
      }
      const cookie = request.headers.cookie || '';
      const authorized = [...fixture.sessions].some(value => cookie.includes(`maibot_host_session_test=${value}`));
      if (!authorized) { json(response, { detail: '请先登录' }, 401); return; }
      if (request.url === '/api/webui/auth/logout') {
        fixture.logouts += 1;
        for (const session of fixture.sessions) if (cookie.includes(`=${session}`)) fixture.sessions.delete(session);
        json(response, { success: true }, 200, { 'set-cookie': 'maibot_host_session_test=; Max-Age=0; Path=/' });
        return;
      }
      if (options.http && await options.http(request, response, body, fixture)) return;
      if (request.url === '/api/host/bots') { json(response, { bots: [{ bot_id: BOT, name: '麦麦' }] }); return; }
      if (request.url.startsWith(REMOTE + '/')) {
        fixture.events.emit('request', { request, body });
        json(response, { items: [], request_id: body?.request_id, accepted: true });
        return;
      }
      json(response, { detail: 'missing' }, 404);
    } catch (error) {
      if (!response.destroyed) json(response, { detail: error.message }, 500);
    }
  });
  const sockets = new WebSocketServer({ noServer: true });
  server.on('upgrade', (request, socket, head) => {
    fixture.upgrades += 1;
    sockets.handleUpgrade(request, socket, head, websocket => {
      fixture.sockets.push(websocket);
      const index = fixture.upgrades;
      if (options.expireHandshake && index === 1) { fixture.sessions.clear(); websocket.close(4001); return; }
      websocket.on('error', () => {});
      websocket.on('message', async raw => {
        const message = JSON.parse(raw.toString());
        fixture.messages.push({ index, message });
        fixture.events.emit('message', { index, message });
        if (message.type === 'hello') {
          fixture.hellos.push(message);
          if (options.rejectConnection) { websocket.send(JSON.stringify({ type: 'error', error: '请先启用 remote 模式' })); websocket.close(1008); return; }
          websocket.send(JSON.stringify({ type: 'hello', version: options.version ?? 1, connection_id: `connection-${index}`,
            heartbeat_interval: options.interval ?? 20, heartbeat_timeout: options.timeout ?? 60 }));
        } else if (message.type === 'ready') {
          fixture.events.emit('ready-request', index);
          if (options.readyGate) await options.readyGate.promise;
          if (websocket.readyState !== WebSocket.OPEN) return;
          websocket.send(JSON.stringify({ type: 'ready', version: 1 }));
        } else if (message.type === 'ping' && !options.ignorePong) {
          websocket.send(JSON.stringify({ type: 'pong' }));
        }
      });
    });
  });
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  fixture.url = `http://127.0.0.1:${server.address().port}`;
  fixture.connection = (executeCommand = async () => ({}), changes = {}) => {
    const connection = new RemoteConnection({ server: fixture.url, botId: BOT, password: 'test-password',
      clientId: 'test-device', allowScreenRead: true, ...changes }, { executeCommand });
    fixture.clients.push(connection);
    return connection;
  };
  t.after(async () => {
    options.readyGate?.resolve();
    options.loginGate?.resolve();
    await Promise.all(fixture.clients.map(connection => connection.stop()));
    for (const websocket of sockets.clients) websocket.terminate();
    await new Promise(resolve => sockets.close(resolve));
    server.closeAllConnections();
    await new Promise(resolve => server.close(resolve));
  });
  return fixture;
}

async function connect(connection) {
  const connected = event(connection, 'connected');
  connection.start();
  await connected;
}

test('normalizes reverse proxy prefixes and rejects credentials or alternate protocols', () => {
  assert.equal(normalizeServer(' https://example.com/maibot/// '), 'https://example.com/maibot');
  assert.equal(normalizeServer('http://127.0.0.1:7999/'), 'http://127.0.0.1:7999');
  for (const invalid of ['file:///tmp/a', 'ws://example.com', 'https://user:secret@example.com',
    'https://example.com?token=secret', 'https://example.com/#x', 'http:\\example.com', '', null]) {
    assert.throws(() => normalizeServer(invalid));
  }
});

test('discovery logs in with an in-memory cookie and revokes the temporary session', async t => {
  const server = await host(t);
  assert.deepEqual(await discoverBots(server.url, 'test-password'), [{ bot_id: BOT, name: '麦麦' }]);
  assert.equal(server.logins, 1);
  assert.equal(server.logouts, 1);
  assert.equal(server.sessions.size, 0);
  assert.deepEqual(server.requests[0].body, { token: 'test-password' });
  assert.equal(server.requests[0].headers.cookie, undefined);
  assert.match(server.requests[1].headers.cookie, /maibot_host_session_test=session-1/);
});

test('discovery rejects a streamed oversized response and still logs out', async t => {
  const server = await host(t, {
    http: async (request, response) => {
      if (request.url !== '/api/host/bots') return false;
      response.writeHead(200, { 'content-type': 'application/json' });
      response.write('{"bots":"');
      for (let index = 0; index < 32 && !response.destroyed; index += 1) {
        response.write('x'.repeat(64 * 1024));
        await sleep(1);
      }
      response.end('"}');
      return true;
    },
  });
  await assert.rejects(discoverBots(server.url, 'test-password'), /大小限制/);
  assert.equal(server.logouts, 1);
});

test('HTTP redirects never forward the cookie or credentials to another origin', async t => {
  const destination = await host(t);
  const source = await host(t, {
    http: async (request, response) => {
      if (request.url !== '/api/host/bots') return false;
      response.writeHead(302, { location: destination.url + '/api/host/bots' });
      response.end();
      return true;
    },
  });
  await assert.rejects(discoverBots(source.url, 'test-password'), /重定向/);
  assert.equal(destination.requests.length, 0);
  assert.equal(source.logouts, 1);
});

test('HTTP remains unavailable until the server confirms ready and binds the exact connection', async t => {
  const gate = deferred();
  const server = await host(t, { readyGate: gate });
  const connection = server.connection();
  const readyRequest = event(server.events, 'ready-request');
  const connected = event(connection, 'connected');
  connection.start();
  await readyRequest;
  assert.equal(connection.connected, false);
  await assert.rejects(connection.request('POST', '/todos', { action: 'list' }), /未连接/);
  assert.equal(server.requests.filter(request => request.path === REMOTE + '/todos').length, 0);
  gate.resolve();
  await connected;
  await connection.request('POST', '/todos', { action: 'list' });
  assert.equal(server.requests.at(-1).headers['x-desktop-pet-connection'], 'connection-1');
  assert.deepEqual(server.hellos[0], { type: 'hello', version: 1, client_id: 'test-device', capabilities: { screen_read: true } });
});

test('commands execute in order and receive result or error acknowledgements', async t => {
  const server = await host(t);
  const called = [];
  const gate = deferred();
  const connection = server.connection(async command => {
    called.push(command.type);
    if (command.type === 'skin') { await gate.promise; return { installed: true }; }
    throw new Error('native action failed');
  });
  await connect(connection);
  const firstAck = event(server.events, 'message', item => item.message.id === 'first');
  const secondAck = event(server.events, 'message', item => item.message.id === 'second');
  server.sockets[0].send(JSON.stringify({ type: 'skin', id: 'first' }));
  server.sockets[0].send(JSON.stringify({ type: 'show', id: 'second' }));
  await sleep(20);
  assert.deepEqual(called, ['skin']);
  gate.resolve();
  assert.deepEqual((await firstAck).message, { type: 'ack', id: 'first', result: { installed: true } });
  assert.deepEqual((await secondAck).message, { type: 'ack', id: 'second', error: 'native action failed' });
});

test('todo mutation IDs are preserved and events are never queued offline', async t => {
  const server = await host(t);
  const connection = server.connection();
  await assert.rejects(connection.sendEvent({ type: 'clicked' }), /未连接/);
  await connect(connection);
  const payload = { action: 'add', text: 'task', request_id: 'b'.repeat(32) };
  await connection.request('POST', '/todos', payload);
  await connection.request('POST', '/todos', payload);
  const submitted = server.requests.filter(request => request.path === REMOTE + '/todos');
  assert.deepEqual(submitted.map(request => request.body.request_id), [payload.request_id, payload.request_id]);
  await assert.rejects(connection.request('POST', '/todos', { action: 'add', text: 'task' }), /request_id/);
  await assert.rejects(connection.request('POST', '/../../api/webui/auth/verify', {}), /不支持/);
  const clicked = event(server.events, 'message', item => item.message.type === 'clicked');
  await connection.sendEvent({ type: 'clicked' });
  await clicked;
  assert.equal(server.messages.filter(item => item.message.type === 'clicked').length, 1);
  await connection.stop();
  await assert.rejects(connection.sendEvent({ type: 'clicked' }), /未连接/);
});

test('ordinary disconnect reconnects without a second login or replaying old acknowledgements', async t => {
  const server = await host(t);
  const gate = deferred();
  const entered = deferred();
  const connection = server.connection(async () => { entered.resolve(); await gate.promise; return {}; });
  await connect(connection);
  server.sockets[0].send(JSON.stringify({ type: 'show', id: 'old-command' }));
  await entered.promise;
  const reconnected = event(connection, 'connected');
  server.sockets[0].close(1001);
  await reconnected;
  gate.resolve();
  await sleep(30);
  assert.equal(server.logins, 1);
  assert.equal(server.upgrades, 2);
  assert.equal(server.messages.filter(item => item.message.id === 'old-command').length, 0);
  await connection.request('POST', '/todos', { action: 'list' });
  assert.equal(server.requests.at(-1).headers['x-desktop-pet-connection'], 'connection-2');
});

test('close 4001 during handshake obtains a fresh login before reconnecting', async t => {
  const server = await host(t, { expireHandshake: true });
  const connection = server.connection();
  await connect(connection);
  assert.equal(server.logins, 2);
  assert.equal(server.upgrades, 2);
});

test('close 4001 after ready reauthenticates and does not silently reuse an expired cookie', async t => {
  const server = await host(t);
  const connection = server.connection();
  await connect(connection);
  const reconnected = event(connection, 'connected');
  server.sessions.clear();
  server.sockets[0].close(4001);
  await reconnected;
  assert.equal(server.logins, 2);
  await connection.request('POST', '/todos', { action: 'list' });
  assert.match(server.requests.at(-1).headers.cookie, /session-2/);
});

test('invalid credentials stop automatic login attempts', async t => {
  const server = await host(t);
  const connection = server.connection(undefined, { password: 'wrong-password' });
  const failed = event(connection, 'state', state => state.text.includes('登录失败'));
  connection.start();
  await failed;
  await sleep(1100);
  assert.equal(server.logins, 1);
  assert.equal(server.upgrades, 0);
});

test('a missing pong disconnects the client independently of command traffic', async t => {
  const server = await host(t, { interval: 0.02, timeout: 0.06, ignorePong: true });
  const connection = server.connection();
  await connect(connection);
  const offline = event(connection, 'state', state => state.text.includes('心跳超时'));
  await offline;
  assert.equal(connection.connected, false);
  assert.ok(server.messages.some(item => item.message.type === 'ping'));
  await connection.stop();
});

test('stop aborts pending login and never starts a delayed reconnect', async t => {
  const gate = deferred();
  const server = await host(t, { loginGate: gate });
  const connection = server.connection();
  const login = event(server.events, 'login');
  connection.start();
  await login;
  await connection.stop();
  gate.resolve();
  await sleep(1100);
  assert.equal(server.upgrades, 0);
  assert.equal(server.logins, 1);
});

test('HTTP responses reject declared oversize and invalid JSON without replaying the request', async t => {
  let invalid = false;
  const server = await host(t, {
    http: async (request, response) => {
      if (!request.url.startsWith(REMOTE + '/todos/images/')) return false;
      if (invalid) {
        response.writeHead(200, { 'content-type': 'application/json' });
        response.end('{broken');
      } else {
        response.writeHead(200, { 'content-type': 'application/json', 'content-length': 49 * 1024 * 1024 });
        response.flushHeaders();
      }
      return true;
    },
  });
  const connection = server.connection();
  await connect(connection);
  const path = '/todos/images/' + 'c'.repeat(64);
  await assert.rejects(connection.request('GET', path), /大小限制/);
  invalid = true;
  await assert.rejects(connection.request('GET', path), /JSON/);
  assert.equal(server.requests.filter(request => request.path === REMOTE + path).length, 2);
});

test('control messages are bounded and binary server messages fail explicitly', async t => {
  const server = await host(t);
  const connection = server.connection();
  await connect(connection);
  await assert.rejects(connection.sendEvent({ type: 'explore', goal: 'x'.repeat(65 * 1024) }), /大小限制/);
  const failed = event(connection, 'state', state => state.text.includes('二进制'));
  server.sockets[0].send(Buffer.from('invalid'), { binary: true });
  await failed;
  assert.equal(connection.connected, false);
});

test('protocol mismatch and disabled mode are visible to the caller', async t => {
  const incompatible = await host(t, { version: 2 });
  const first = incompatible.connection();
  const versionFailure = event(first, 'state', state => state.text.includes('版本不兼容'));
  first.start();
  await versionFailure;
  const disabled = await host(t, { rejectConnection: true });
  const second = disabled.connection();
  const modeFailure = event(second, 'state', state => state.text.includes('remote 模式'));
  second.start();
  await modeFailure;
  await second.stop();
});
