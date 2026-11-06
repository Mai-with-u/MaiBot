'use strict';
const login = window.desktopPetBridge.login;
const server = document.querySelector('#server');
const bots = document.querySelector('#bot'), botField = document.querySelector('#bot-field');
const connect = document.querySelector('#connect'), status = document.querySelector('#status');
const screenAccess = document.querySelector('#screen');
let savedBot = '', choices = null, busy = false;

// 机器人选择与报错按需出现，窗口随内容收放，不为隐藏字段预留空白。
const content = document.querySelector('main');
new ResizeObserver(() => login.fit(content.getBoundingClientRect().height, devicePixelRatio).catch(console.error)).observe(content);

function message(text = '', error = false) {
  status.textContent = text;
  status.hidden = !text;
  status.classList.toggle('error', error);
}

function showError(error) {
  // 控制台保留完整错误；界面只展示简明原因。
  console.error(error);
  const detail = String(error.message || error)
    .replace(/^Error invoking remote method '[^']+':\s*/, '')
    .replace(/^(?:Error|TypeError):\s*/, '');
  if (/fetch failed|ECONNREFUSED|ENOTFOUND|EHOSTUNREACH/i.test(detail)) {
    message('无法连接，请检查地址和服务是否已启动。', true);
  } else if (/timeout|timed out|aborted/i.test(detail)) {
    message('连接超时，请检查网络后重试。', true);
  } else {
    message(detail, true);
  }
}

function setBusy(value) {
  busy = value;
  server.disabled = screenAccess.disabled = connect.disabled = value;
  bots.disabled = value || !choices;
  connect.textContent = value ? '等待 WebUI 登录…' : choices ? '启动桌宠' : '通过 WebUI 连接';
}

function resetBots() {
  choices = null;
  botField.hidden = true;
  bots.disabled = true;
  bots.replaceChildren(new Option('请选择机器人', ''));
  connect.textContent = '通过 WebUI 连接';
  message();
}

server.addEventListener('input', resetBots);
login.profile().then(profile => {
  server.value = profile.server || 'http://127.0.0.1:8001';
  savedBot = profile.botId || '';
}).catch(showError);

document.querySelector('#connection').addEventListener('submit', async event => {
  event.preventDefault();
  if (busy) return;
  setBusy(true);
  message();
  try {
    if (!choices) {
      const items = await login.discover(server.value);
      if (!items.length) { message('还没有机器人，请先在宿主中创建。'); return; }
      choices = items;
      bots.replaceChildren(new Option('请选择机器人', ''), ...items.map(bot => new Option(bot.name, bot.bot_id)));
      if (items.some(bot => bot.bot_id === savedBot)) bots.value = savedBot;
      // 只有多个机器人时才增加选择步骤；一个机器人直接连接。
      if (items.length > 1) { botField.hidden = false; return; }
      bots.value = items[0].bot_id;
    }
    const bot = choices.find(item => item.bot_id === bots.value);
    if (!bot) { bots.focus(); return; }
    await login.connect({ server: server.value, botId: bot.bot_id,
      botName: bot.name, allowScreenRead: screenAccess.checked });
  } catch (error) {
    // 失败后重新读取机器人，避免保留已过期的选择或登录状态。
    resetBots();
    showError(error);
  } finally { setBusy(false); }
});
