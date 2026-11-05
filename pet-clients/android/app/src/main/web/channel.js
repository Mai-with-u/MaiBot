/* A MessagePort is transferred only to the verified top-level document; no JavaScript interface is exposed to frames. */
(() => {
  if (window.__androidChannel) return;
  let port, serial = 0;
  const waiting = new Map();
  window.addEventListener('message', event => {
    if (event.data !== 'maibot-native-channel' || !event.ports[0] || port) return;
    port = event.ports[0];
    port.onmessage = ({data}) => {
      const message = JSON.parse(data), pending = waiting.get(message.id);
      if (!pending) return;
      waiting.delete(message.id); clearTimeout(pending.timer);
      message.error ? pending.reject(new Error(message.error)) : pending.resolve(message.result);
    };
    for (const pending of waiting.values()) port.postMessage(pending.message);
  });
  const invoke = (command, payload = {}) => new Promise((resolve, reject) => {
    if (waiting.size >= 128) return reject(new Error('待处理请求过多'));
    const id = String(++serial), message = JSON.stringify({id, command, payload});
    const timer = setTimeout(() => { waiting.delete(id); reject(new Error('请求超时，请刷新确认结果')); }, 65000);
    waiting.set(id, {resolve, reject, timer, message});
    if (port) port.postMessage(message);
  });
  window.__androidChannel = {invoke};
  window.__TAURI_INTERNALS__ = {invoke};
})();
