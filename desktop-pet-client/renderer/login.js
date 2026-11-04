'use strict';
const login = window.desktopPetBridge.login;
const server = document.querySelector('#server'), password = document.querySelector('#password');
const bots = document.querySelector('#bot'), discover = document.querySelector('#discover');
const connect = document.querySelector('#connect'), status = document.querySelector('#status');
let savedBot = '', generation = 0;
function message(text, error = false) { status.textContent = text; status.classList.toggle('error', error); }
function resetBots() { generation++; bots.replaceChildren(new Option('先读取机器人列表', '')); bots.disabled = true; connect.disabled = true; }
server.addEventListener('input', resetBots);
password.addEventListener('input', resetBots);
login.profile().then(profile => {
  server.value = profile.server || 'http://127.0.0.1:8001';
  savedBot = profile.botId || '';
}).catch(error => message(error.message, true));
discover.addEventListener('click', async () => {
  if (!server.reportValidity() || !password.reportValidity()) return;
  resetBots();
  const current = generation;
  discover.disabled = true; message('正在读取机器人…');
  try {
    const items = await login.discover(server.value, password.value);
    if (current !== generation) return;
    bots.replaceChildren(...items.map(bot => new Option(`${bot.name} · ${bot.status}`, bot.bot_id)));
    if (!items.length) { resetBots(); message('宿主中还没有机器人。'); return; }
    if (items.some(bot => bot.bot_id === savedBot)) bots.value = savedBot;
    bots.disabled = false; connect.disabled = false;
    message('请确认该机器人已启动，并启用桌宠的远端模式。');
  } catch (error) { if (current === generation) message(error.message, true); }
  finally { discover.disabled = false; }
});
document.querySelector('#connection').addEventListener('submit', async event => {
  event.preventDefault(); connect.disabled = true; discover.disabled = true;
  try {
    await login.connect({ server: server.value, password: password.value, botId: bots.value,
      botName: bots.selectedOptions[0].textContent.split(' · ')[0],
      allowScreenRead: document.querySelector('#screen').checked });
    password.value = '';
  } catch (error) { message(error.message, true); connect.disabled = false; discover.disabled = false; }
});
