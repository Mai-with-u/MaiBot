'use strict';

const { createHash } = require('node:crypto');
const fs = require('node:fs/promises');
const path = require('node:path');

/** 只服务明确列出的本地资源；服务器推送的是声明式数据，不是网页或脚本。 */
async function installLocalPages(session, webRoot, loginRoot) {
  const sources = [
    ...['index.html', 'exploration.html', 'host-bridge.js', 'skin.js', 'exploration-render.js', 'exploration-products.css', 'fonts/ArkPixel12-ZhHans.woff2']
      .map(name => [`/pet/${name}`, path.join(webRoot, name)]),
    ...['login.html', 'login.css', 'login.js'].map(name => [`/login/${name}`, path.join(loginRoot, name)]),
  ];
  const types = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8', '.woff2': 'font/woff2' };
  const files = new Map(await Promise.all(sources.map(async ([url, filename]) => {
    const bytes = await fs.readFile(filename);
    // 现有页面含内联脚本，按打包内容精确授权，禁止任意内联代码与 eval。
    const hashes = filename.endsWith('.html') ? [...bytes.toString().matchAll(/<script>([\s\S]*?)<\/script>/g)]
      .map(match => `'sha256-${createHash('sha256').update(match[1].replaceAll('\r\n', '\n')).digest('base64')}'`) : [];
    const policy = `default-src 'none'; script-src 'self' ${hashes.join(' ')}; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob: https: http:; media-src https: http:; font-src 'self'; connect-src 'none'; object-src 'none'; base-uri 'none'; frame-src 'none'; form-action 'none'`;
    return [url, { bytes, headers: { 'Content-Type': types[path.extname(filename)], 'Content-Security-Policy': policy, 'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'no-referrer' } }];
  })));
  session.protocol.handle('petapp', request => {
    const url = new URL(request.url);
    const file = url.hostname === 'local' && !url.search && !url.hash && files.get(url.pathname);
    if (request.method !== 'GET' || !file) return new Response('Not found', { status: 404 });
    return new Response(file.bytes, { headers: file.headers });
  });
  session.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
  session.setPermissionCheckHandler(() => false);
  session.on('will-download', event => event.preventDefault());
}

module.exports = { installLocalPages };
