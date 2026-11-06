'use strict';
// Owned emulator only: synthetic proxy/WebUI credentials, no user's server or phone data.
const { execFile } = require('node:child_process');
const { promisify } = require('node:util');
const fs = require('node:fs/promises');
const path = require('node:path');
const os = require('node:os');
const net = require('node:net');
const assert = require('node:assert/strict');
const { setTimeout: delay } = require('node:timers/promises');
const { chromium } = require('../../desktop/node_modules/playwright-core');
const { HostFixture } = require('../../desktop/scripts/smoke.cjs');
const exec = promisify(execFile);
const serial = process.env.ANDROID_SERIAL || 'emulator-5580';
if (!/^emulator-\d+$/.test(serial)) throw new Error('This test grants permissions on an owned emulator only');
const sdk = process.env.ANDROID_HOME || path.join(process.env.LOCALAPPDATA, 'Android', 'Sdk');
const adbPath = path.join(sdk, 'platform-tools', process.platform === 'win32' ? 'adb.exe' : 'adb');
const pkg = 'org.maibot.androidpet';
async function adb(...args) { return (await exec(adbPath, ['-s', serial, ...args], { encoding: 'utf8', maxBuffer: 4e6 })).stdout.trim(); }
async function until(check, label, timeout = 30000) {
  const end = Date.now() + timeout;
  while (Date.now() < end) { const result = await check(); if (result) return result; await delay(200); }
  throw new Error('Timed out: ' + label);
}
async function nodes() {
  await adb('shell', 'uiautomator', 'dump', '/sdcard/maibot-smoke.xml');
  const xml = await adb('shell', 'cat', '/sdcard/maibot-smoke.xml');
  return [...xml.matchAll(/<node\s+([^>]+)>/g)].map(match => Object.fromEntries([...match[1].matchAll(/([\w-]+)="([^"]*)"/g)].map(m => [m[1], m[2]])));
}
async function tap(node) {
  const [x1, y1, x2, y2] = node.bounds.match(/\d+/g).map(Number);
  await adb('shell', 'input', 'tap', String(Math.round((x1 + x2) / 2)), String(Math.round((y1 + y2) / 2)));
}
async function clickText(text) {
  const node = await until(async () => (await nodes()).find(node => node.text.toLowerCase() === text.toLowerCase() && node.enabled === 'true'), text);
  await tap(node);
}
async function proxyLogin() {
  const visible = await nodes();
  if (!visible.some(node => node.text.startsWith('代理认证'))) return false;
  const fields = visible.filter(node => node.class === 'android.widget.EditText');
  assert.equal(fields.length, 2);
  await tap(fields[0]); await adb('shell', 'input', 'text', 'synthetic-proxy-user');
  // The IME moves the dialog; reobserve before targeting its password field.
  await tap((await nodes()).find(node => node.class === 'android.widget.EditText' && node.password === 'true'));
  await adb('shell', 'input', 'text', 'synthetic-proxy-password');
  await clickText('登录'); return true;
}
async function port() {
  const server = net.createServer(); await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const value = server.address().port; await new Promise(resolve => server.close(resolve)); return value;
}
async function overlayBounds() {
  const dump = await adb('shell', 'dumpsys', 'window', 'windows');
  const window = dump.split(/(?=Window #\d+)/).find(block => block.includes(pkg) && /ty=(APPLICATION_OVERLAY|2038)/.test(block));
  const match = window && /(?:mFrame|\bframe)=\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]/.exec(window);
  return match ? match.slice(1).map(Number) : null;
}
async function run() {
  const fixture = new HostFixture(), artifacts = await fs.mkdtemp(path.join(os.tmpdir(), 'maibot-android-smoke-'));
  let browser, forwarded;
  try {
    await fixture.start();
    const fixturePort = new URL(fixture.url).port;
    await adb('reverse', 'tcp:' + fixturePort, 'tcp:' + fixturePort);
    const apk = process.env.PET_ANDROID_APK || path.resolve(__dirname, '../app/build/outputs/apk/debug/app-debug.apk');
    await adb('install', '-r', apk);
    await adb('shell', 'am', 'force-stop', pkg);
    await adb('shell', 'appops', 'set', pkg, 'SYSTEM_ALERT_WINDOW', 'allow');
    await adb('shell', 'pm', 'grant', pkg, 'android.permission.POST_NOTIFICATIONS');
    await adb('shell', 'input', 'keyevent', 'KEYCODE_HOME');
    await adb('shell', 'am', 'start', '-n', pkg + '/.MainActivity');
    const address = await until(async () => (await nodes()).find(node => node.class === 'android.widget.EditText'), 'address');
    await tap(address); await adb('shell', 'input', 'keyevent', 'KEYCODE_MOVE_END');
    await adb('shell', 'input', 'keycombination', '113', '29');
    await adb('shell', 'input', 'text', fixture.url);
    await clickText('通过 WebUI 登录');
    await until(proxyLogin, 'native HTTP Basic challenge');
    const pid = await adb('shell', 'pidof', pkg); forwarded = await port();
    await adb('forward', 'tcp:' + forwarded, 'localabstract:webview_devtools_remote_' + pid);
    browser = await chromium.connectOverCDP('http://127.0.0.1:' + forwarded, {noDefaults:true});
    const pages = () => browser.contexts().flatMap(context => context.pages());
    const auth = await until(() => pages().find(page => page.url().startsWith(fixture.url)), 'authentication WebView');
    await auth.locator('#webui-password').fill('synthetic-tauri-smoke-password');
    await auth.locator('#webui-login button').click();
    await clickText('启动悬浮桌宠');
    const pet = await until(() => pages().find(page => page.url().endsWith('/pet/index.html')), 'overlay WebView');
    pet.setDefaultTimeout(15000);
    await pet.locator('#remote-indicator[data-connected="true"]').waitFor();
    assert.equal(fixture.verifyCount, 1); assert.ok(fixture.basicHTTP > 0 && fixture.basicWS > 0);
    assert.equal(await pet.locator('#skin-canvas').evaluate(el => getComputedStyle(el).display), 'none');
    assert.ok(await pet.locator('#builtin-pet').evaluate(el => el.getBoundingClientRect().bottom <= innerHeight));
    // Non-focusable overlays are omitted from uiautomator's active-window tree.
    const beforeDrag = await until(overlayBounds, 'native pet bounds');
    const [left, top, right, bottom] = beforeDrag;
    const middleX = Math.round((left + right) / 2), middleY = Math.round(bottom - 77 * await pet.evaluate(() => devicePixelRatio));
    await adb('shell', 'input', 'swipe', String(middleX), String(middleY), String(middleX - 60), String(middleY - 180), '600');
    await until(async () => JSON.stringify(await overlayBounds()) !== JSON.stringify(beforeDrag), 'native touch drag');
    const screen = await exec(adbPath, ['-s', serial, 'exec-out', 'screencap', '-p'], { encoding: 'buffer', maxBuffer: 16e6 });
    await fs.writeFile(path.join(artifacts, 'overlay.png'), screen.stdout);
    async function petAnchor() {
      const bounds = await overlayBounds();
      const rect = await pet.locator('#pet').evaluate(el => {
        const r = el.getBoundingClientRect();
        return { x: r.x, y: r.y, height: innerHeight, dpr: devicePixelRatio };
      });
      return { x: bounds[0] + rect.x * rect.dpr, y: bounds[3] + (rect.y - rect.height) * rect.dpr };
    }
    async function nativeTap(selector) {
      const rect = await pet.locator(selector).evaluate(el => {
        const r = el.getBoundingClientRect();
        return { x: r.x + r.width / 2, y: r.y + r.height / 2, height: innerHeight, dpr: devicePixelRatio };
      });
      const bounds = await overlayBounds();
      await adb('shell', 'input', 'tap', String(Math.round(bounds[0] + rect.x * rect.dpr)),
        String(Math.round(bounds[3] + (rect.y - rect.height) * rect.dpr)));
    }
    const anchor = await petAnchor();
    const idleBounds = await overlayBounds();
    const apiLevel = Number(await adb('shell', 'getprop', 'ro.build.version.sdk'));
    await nativeTap('#pet');
    await delay(700);
    assert.equal(await pet.locator('#stage').getAttribute('data-mode'), 'menu');
    assert.deepEqual(await petAnchor(), anchor, 'opening the menu must preserve the pet position');
    await nativeTap('#orb-chat');
    await delay(700);
    assert.equal(await pet.locator('#stage').getAttribute('data-mode'), 'chat');
    assert.deepEqual(await petAnchor(), anchor, 'opening chat must preserve the pet position');
    if (apiLevel >= 33) assert.deepEqual(await overlayBounds(), idleBounds, 'menu changes must not resize or move the native window');
    const input = await pet.locator('#chat-input').evaluate(el => { const r=el.getBoundingClientRect(); return {x:r.x+r.width/2,y:r.y+r.height/2,height:innerHeight,dpr:devicePixelRatio}; });
    const bounds = await overlayBounds();
    await adb('shell', 'input', 'tap', String(Math.round(bounds[0]+input.x*input.dpr)), String(Math.round(bounds[3]+(input.y-input.height)*input.dpr)));
    await until(async () => /mInputShown=true|mIsInputViewShown=true/.test(await adb('shell', 'dumpsys', 'input_method')), 'soft keyboard');
    await until(async () => { const moved = await overlayBounds(); return moved && moved[3] < bounds[3]; }, 'overlay moving above the keyboard');
    await delay(300);
    await fs.writeFile(path.join(artifacts, 'keyboard.png'), (await exec(adbPath, ['-s', serial, 'exec-out', 'screencap', '-p'], {encoding:'buffer', maxBuffer:16e6})).stdout);
    await pet.locator('#chat-input').fill('Android 测试聊天'); await pet.locator('#chat-input').press('Enter');
    await until(() => fixture.chats.length === 1, 'chat submission');
    await pet.getByText('来自本机测试服务器的回复', {exact:true}).waitFor();
    await adb('shell', 'input', 'keyevent', 'KEYCODE_BACK');
    const todos = await pet.evaluate(async () => {
      const api = window.desktopPetBridge.api;
      const added = await api.add_todo('Android 待办', {});
      await api.update_todo(added.id, 'Android 修改待办', {});
      await api.done_todo(added.id);
      return api.todos(true);
    });
    assert.equal(todos[0].text, 'Android 修改待办');
    const denied = await fixture.command('capture_screen'); assert.match(denied.error, /未启用/);
    const deniedIPC = await auth.evaluate(async () => {
      try { await window.__androidChannel.invoke('pet_call', {method:'exit_client', args:[]}); return ''; } catch (error) { return error.message; }
    });
    assert.match(deniedIPC, /无权/);
    await pet.evaluate(() => window.desktopPetBridge.api.open_exploration_records());
    const records = await until(() => pages().find(page => page.url().endsWith('/pet/exploration.html')), 'records');
    await records.getByText('Tauri 冒烟探索记录', {exact:true}).first().waitFor();
    const previousReady = fixture.readyCount;
    fixture.connection.ws.close(1012, 'Android reconnect test');
    await until(() => fixture.readyCount > previousReady, 'WebSocket reconnect');
    assert.equal(fixture.chats.length, 1); assert.equal(fixture.writes.length, 3);
    await adb('shell', 'input', 'tap', '20', '500'); // Outside touch releases the editor's window focus.
    // Clock has a different UID and permits overlays. Its menu is under our transparent canvas.
    await adb('shell', 'am', 'start', '-n', 'com.android.deskclock/.DeskClock');
    const menu = await until(async () => (await nodes()).find(node => node['content-desc'] === 'More options'), 'Clock menu');
    const row = menu.bounds.match(/\d+/g).map(Number);
    const overlay = await overlayBounds();
    assert.ok(overlay, 'pet overlay must still exist above Clock');
    const menuX = Math.min(overlay[2], row[2]) - 10;
    assert.ok(menuX > overlay[0] && menuX > row[0], 'menu tap must be inside both the overlay and Clock button');
    await adb('shell', 'input', 'tap', String(menuX), String(Math.round((row[1] + row[3]) / 2)));
    await until(async () => (await nodes()).some(node => node.text === 'Settings'), 'transparent overlay touch pass-through');
    await browser.close(); browser = null;
    await adb('forward', '--remove', 'tcp:' + forwarded); forwarded = null;
    await adb('shell', 'am', 'force-stop', pkg);
    await adb('shell', 'input', 'keyevent', 'KEYCODE_HOME');
    await adb('shell', 'am', 'start', '-n', pkg + '/.MainActivity');
    await clickText('通过 WebUI 登录');
    await until(proxyLogin, 'proxy authentication after process restart');
    await clickText('启动悬浮桌宠');
    await until(() => fixture.readyCount > previousReady + 1, 'persisted WebUI cookie');
    assert.equal(fixture.verifyCount, 1, 'restarting must reuse the WebUI cookie without re-entering its password');
    assert.deepEqual(fixture.failures.map(error => error.message), []);
    console.log('Android smoke passed: proxy challenge, WebUI login, native overlay/touch drag/keyboard, stable menu position, transparent touch pass-through, default pet, chat, todos, records, denied capture and remote IPC, reconnect, cross-process cookie reuse.');
    console.log('Artifacts: ' + artifacts);
  } catch (error) {
    await fs.writeFile(path.join(artifacts, 'logcat.txt'), await adb('logcat', '-d', '-t', '1500'));
    if (browser) for (const page of browser.contexts().flatMap(context => context.pages())) console.error(await page.evaluate(() => ({url:location.href, text:document.body.innerText.slice(0,300)})).catch(() => null));
    console.error({basicHTTP:fixture.basicHTTP, verifyCount:fixture.verifyCount, failures:fixture.failures.map(error=>error.message)});
    console.error('Artifacts: ' + artifacts); throw error;
  } finally {
    if (browser) await browser.close();
    await adb('shell', 'am', 'force-stop', pkg);
    if (forwarded) await adb('forward', '--remove', 'tcp:' + forwarded);
    await adb('reverse', '--remove', 'tcp:' + new URL(fixture.url).port);
    await fixture.close();
  }
}
run().catch(error => { console.error(error); process.exitCode = 1; });
