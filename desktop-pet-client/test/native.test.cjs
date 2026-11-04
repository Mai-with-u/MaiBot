'use strict';

const assert = require('node:assert/strict');
const { createHash } = require('node:crypto');
const test = require('node:test');
const { PNG } = require('pngjs');
const { createNativeCapabilities } = require('../src/native.cjs');

function fixture({ width = 320, height = 180, platform = 'win32', permission = 'granted' } = {}) {
  const state = { width, height, bitmap: Buffer.alloc(width * height * 4), requests: [], copies: [], itemCalls: 0 };
  for (let offset = 3; offset < state.bitmap.length; offset += 4) state.bitmap[offset] = 255;
  const image = {
    isEmpty: () => false,
    getSize: () => ({ width: state.width, height: state.height }),
    toBitmap: () => Buffer.from(state.bitmap),
  };
  const source = { display_id: '42', thumbnail: image };
  const apis = {
    desktopCapturer: { getSources: async options => {
      state.requests.push(options);
      return [{ display_id: 'other', thumbnail: { isEmpty: () => assert.fail('must select primary display') } }, source];
    } },
    screen: { getPrimaryDisplay: () => ({ id: 42, bounds: { width: 3840, height: 2160 }, scaleFactor: 1 }) },
    ClipboardItem: class {
      constructor(items) { state.itemCalls++; this.items = items; this.types = Object.keys(items); }
      async getType(type) { return this.items[type]; }
    },
    clipboard: {
      writeText: async text => { await new Promise(resolve => setImmediate(resolve)); state.copies.push({ text }); },
      write: async items => { await new Promise(resolve => setImmediate(resolve)); state.copies.push({ items }); },
    },
    systemPreferences: { getMediaAccessStatus: kind => {
      assert.equal(kind, 'screen');
      return permission;
    } },
  };
  const options = { allowScreenRead: true, platform };
  const capabilities = createNativeCapabilities(apis, options);
  return { state, apis, options, capabilities, source };
}

function fill(state, value) {
  for (let offset = 0; offset < state.bitmap.length; offset += 4) {
    state.bitmap[offset] = state.bitmap[offset + 1] = state.bitmap[offset + 2] = value;
  }
}

test('screen capture requires both local consent and macOS screen permission', async () => {
  const local = fixture();
  local.options.allowScreenRead = false;
  await assert.rejects(local.capabilities.captureScreen(), /未授权/);
  assert.equal(local.state.requests.length, 0);
  for (const permission of ['denied', 'restricted', 'unknown']) {
    const mac = fixture({ platform: 'darwin', permission });
    await assert.rejects(mac.capabilities.captureScreen(), /macOS.*未授权/);
    assert.equal(mac.state.requests.length, 0);
  }
});

test('first macOS capture can request consent but never returns an unapproved frame', async () => {
  const granted = fixture({ platform: 'darwin', permission: 'not-determined' });
  let permission = 'not-determined';
  granted.apis.systemPreferences.getMediaAccessStatus = () => permission;
  granted.apis.desktopCapturer.getSources = async () => { permission = 'granted'; return [granted.source]; };
  assert.ok((await granted.capabilities.captureScreen()).base64);
  const notGranted = fixture({ platform: 'darwin', permission: 'not-determined' });
  await assert.rejects(notGranted.capabilities.captureScreen(), /未授权/);
  assert.equal(notGranted.state.requests.length, 1);
});

test('primary capture returns bounded PNG, UTC timestamp, RGB colors and exact SHA256', async () => {
  const { state, capabilities } = fixture({ width: 2000, height: 2 });
  for (let offset = 0; offset < state.bitmap.length; offset += 4) state.bitmap[offset + 2] = 255;
  const capture = await capabilities.captureScreen();
  assert.deepEqual(state.requests[0], { types: ['screen'], thumbnailSize: { width: 1920, height: 1080 }, fetchWindowIcons: false });
  assert.equal(capture.width, 1920);
  assert.equal(capture.height, 2);
  assert.match(capture.captured_at, /Z$/);
  assert.ok(Number.isFinite(Date.parse(capture.captured_at)));
  const data = Buffer.from(capture.base64, 'base64');
  assert.equal(capture.digest, createHash('sha256').update(data).digest('hex'));
  const png = PNG.sync.read(data);
  assert.deepEqual([...png.data.subarray(0, 4)], [255, 0, 0, 255]);
  assert.equal(png.width, capture.width);
  assert.equal(png.height, capture.height);
});

test('capture rejects unknown display identity, empty source and malformed bitmap', async () => {
  const unknown = fixture();
  unknown.source.display_id = '';
  await assert.rejects(unknown.capabilities.captureScreen(), /无法确定主屏/);
  const empty = fixture();
  empty.source.thumbnail.isEmpty = () => true;
  await assert.rejects(empty.capabilities.captureScreen(), /无法读取主屏/);
  const malformed = fixture();
  malformed.source.thumbnail.toBitmap = () => Buffer.from([0]);
  await assert.rejects(malformed.capabilities.captureScreen(), /位图长度/);
});

test('sampling compares against last retained frame and reset forces a new image', async () => {
  const { state, capabilities } = fixture();
  assert.ok((await capabilities.sampleScreen({ threshold: 0.015 })).base64);
  fill(state, 19);
  assert.deepEqual(await capabilities.sampleScreen({ threshold: 0.015 }), { unchanged: true });
  // The ignored 19/255 frame must not become the baseline: accumulated change is now 35/255.
  fill(state, 35);
  assert.ok((await capabilities.sampleScreen({ threshold: 0.015 })).base64);
  fill(state, 54);
  assert.deepEqual(await capabilities.sampleScreen({ threshold: 0.015 }), { unchanged: true });
  assert.ok((await capabilities.sampleScreen({ threshold: 0.015, reset: true })).base64);
  for (const threshold of [0, NaN, Infinity, '0.015', true, 1.01]) {
    await assert.rejects(capabilities.sampleScreen({ threshold }), /阈值/);
  }
});

test('sampling applies the changed-pixel ratio instead of a whole-image mean', async () => {
  const { state, capabilities } = fixture();
  await capabilities.sampleScreen({ threshold: 0.002 });
  for (let i = 0; i < 100; i++) state.bitmap.fill(255, i * 4, i * 4 + 4);
  assert.deepEqual(await capabilities.sampleScreen({ threshold: 0.002 }), { unchanged: true });
  for (let i = 100; i < 200; i++) state.bitmap.fill(255, i * 4, i * 4 + 4);
  assert.ok((await capabilities.sampleScreen({ threshold: 0.002 })).base64);
});

test('capture cannot pile up and permission revoked during capture discards the image', async () => {
  const { apis, options, source } = fixture();
  let release;
  apis.desktopCapturer.getSources = () => new Promise(resolve => { release = resolve; });
  const capabilities = createNativeCapabilities(apis, options);
  const first = capabilities.captureScreen();
  await assert.rejects(capabilities.captureScreen(), /正在采集/);
  options.allowScreenRead = false;
  release([source]);
  await assert.rejects(first, /未授权/);
});

test('Windows application query uses fixed asynchronous command and preserves names/titles', async () => {
  const { apis } = fixture();
  const calls = [];
  const capabilities = createNativeCapabilities(apis, {
    platform: 'win32', execFile: (command, args, options, callback) => {
      calls.push({ command, args, options });
      queueMicrotask(() => callback(null, '\uFEFF' + JSON.stringify({ ProcessName: 'Code', MainWindowTitle: '编辑器窗口' })));
    },
  });
  assert.deepEqual(await capabilities.applications(), [{ process: 'Code', title: '编辑器窗口' }]);
  assert.equal(calls[0].command, 'powershell.exe');
  assert.deepEqual(calls[0].args.slice(0, 3), ['-NoProfile', '-NonInteractive', '-Command']);
  assert.match(calls[0].args[3], /Get-Process/);
  assert.equal(calls[0].options.windowsHide, true);
  assert.equal(calls[0].options.timeout, 10_000);
});

test('macOS/Linux process query preserves executable names and does not fabricate titles', async () => {
  for (const platform of ['darwin', 'linux']) {
    const { apis } = fixture();
    const capabilities = createNativeCapabilities(apis, {
      platform, execFile: (command, args, _options, callback) => {
        assert.equal(command, '/bin/ps');
        assert.deepEqual(args, ['-A', '-o', 'comm=']);
        queueMicrotask(() => callback(null, '/Applications/My App.app/Contents/MacOS/My App\n/usr/bin/code\n'));
      },
    });
    assert.deepEqual(await capabilities.applications(), [{ process: 'My App', title: '' }, { process: 'code', title: '' }]);
  }
});

test('failed application query is surfaced instead of returning an empty result', async () => {
  const { apis } = fixture();
  const capabilities = createNativeCapabilities(apis, {
    execFile: (_command, _args, _options, callback) => queueMicrotask(() => callback(new Error('permission denied'))),
  });
  await assert.rejects(capabilities.applications(), /permission denied/);
});

test('clipboard enforces text/image bounds and awaits Electron 45 ClipboardItem writes', async () => {
  const { state, capabilities } = fixture();
  await capabilities.copyPanelContent('text', '🙂'.repeat(16_000));
  assert.equal(state.copies[0].text.length, 32_000);
  await assert.rejects(capabilities.copyPanelContent('text', 'x'.repeat(16_001)), /16000/);
  await assert.rejects(capabilities.copyPanelContent('image', 'x'.repeat(1_000_001)), /内容过大/);
  await assert.rejects(capabilities.copyPanelContent('html', '<script>'), /不支持/);
  const png = PNG.sync.write({ width: 2, height: 1, data: Buffer.from([255, 0, 0, 255, 0, 255, 0, 128]) });
  await capabilities.copyPanelContent('image', png.toString('base64'));
  assert.equal(state.itemCalls, 1);
  const [item] = state.copies[1].items;
  assert.deepEqual(item.types, ['image/png']);
  const blob = await item.getType('image/png');
  assert.ok(blob instanceof Blob);
  assert.equal(blob.type, 'image/png');
  const decoded = PNG.sync.read(Buffer.from(await blob.arrayBuffer()));
  assert.deepEqual([...decoded.data], [255, 0, 0, 255, 0, 255, 0, 128]);
  assert.equal(decoded.width, 2);
  assert.equal(decoded.height, 1);
});

test('asynchronous OS clipboard errors are surfaced to the caller', async () => {
  const { apis, capabilities } = fixture();
  apis.clipboard.writeText = async () => { throw new Error('clipboard text denied'); };
  await assert.rejects(capabilities.copyPanelContent('text', 'example'), /clipboard text denied/);
  apis.clipboard.write = async () => { throw new Error('clipboard image denied'); };
  const png = PNG.sync.write({ width: 1, height: 1, data: Buffer.from([0, 0, 0, 255]) });
  await assert.rejects(capabilities.copyPanelContent('image', png.toString('base64')), /clipboard image denied/);
});

test('clipboard rejects invalid base64, forged dimensions, duplicate headers and CRC corruption before native decode', async () => {
  const { state, capabilities } = fixture();
  const png = PNG.sync.write({ width: 1, height: 1, data: Buffer.from([0, 0, 0, 255]) });
  const oversized = Buffer.from(png);
  oversized.writeUInt32BE(4_000_001, 16);
  const duplicate = Buffer.concat([png.subarray(0, 33), png.subarray(8, 33), png.subarray(33)]);
  const corrupted = Buffer.from(png);
  corrupted[corrupted.length - 1] ^= 255;
  for (const encoded of ['AAAA!', 'data:image/png;base64,' + png.toString('base64'),
    Buffer.from('not a png').toString('base64'), oversized.toString('base64'), duplicate.toString('base64'), corrupted.toString('base64')]) {
    await assert.rejects(capabilities.copyPanelContent('image', encoded));
  }
  assert.equal(state.itemCalls, 0);
  assert.equal(state.copies.length, 0);
});
