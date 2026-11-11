'use strict';
const fs = require('node:fs/promises');
const path = require('node:path');

// 使用同一份桌宠页面，不生成另一套界面源码；构建目录只包含明确列出的资源。
async function prepare() {
  const root = path.resolve(__dirname, '..');
  const output = path.join(root, 'web-dist');
  const files = [
    ...['index.html', 'exploration.html', 'host-bridge.js', 'skin.js', 'exploration-render.js',
      'exploration-products.css', 'fonts/ArkPixel12-ZhHans.woff2']
      .map(name => [path.resolve(root, '../../src/desktop_pet/web', name), path.join('pet', name)]),
    ...['login.html', 'login.css', 'login.js'].map(name => [path.join(root, 'renderer', name), path.join('login', name)]),
  ];
  for (const [source, name] of files) {
    const target = path.join(output, name);
    await fs.mkdir(path.dirname(target), { recursive: true });
    await fs.copyFile(source, target);
  }
}
prepare().catch(error => { console.error(error); process.exitCode = 1; });
