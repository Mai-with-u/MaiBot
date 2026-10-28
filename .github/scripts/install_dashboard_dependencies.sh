#!/usr/bin/env bash
# Run from dashboard/. Keep the install failure visible after collecting diagnostics.
set -euo pipefail

# GitHub runners use the official registry; local installs keep their mirror URLs.
# Only transport URLs change: package versions and integrity hashes stay locked.
if [[ "${GITHUB_ACTIONS:-}" == "true" ]]; then
  node <<'NODE'
const fs = require('node:fs');
const replacements = [
  ['https://mirrors.cloud.tencent.com/npm/', 'https://registry.npmjs.org/'],
  ['https://registry.npmmirror.com/', 'https://registry.npmjs.org/'],
];
for (const filename of ['bun.lock', 'bunfig.toml']) {
  const original = fs.readFileSync(filename, 'utf8');
  let updated = original;
  for (const [mirror, registry] of replacements) {
    updated = updated.replaceAll(mirror, registry);
  }
  fs.writeFileSync(filename, updated);
  console.log(`${filename}: using npm official registry in GitHub Actions`);
}
NODE
fi

if bun install --frozen-lockfile --verbose; then
  exit 0
else
  install_status=$?
fi

echo '::group::Dashboard dependency installation diagnostics'
bun --version || true
node --version || true
node <<'NODE' || true
const fs = require('node:fs');
const path = require('node:path');
const { createRequire } = require('node:module');

const packages = ['electron', '@electron-internal/extract-zip', '@electron/get'];
for (const name of packages) {
  const directory = path.resolve('node_modules', name);
  console.log(`\nPackage: ${name}`);
  try {
    const stat = fs.lstatSync(directory);
    console.log('Directory:', directory, 'Symlink:', stat.isSymbolicLink());
    console.log('Real path:', fs.realpathSync(directory));
    console.log('Files:', fs.readdirSync(directory));
    const manifest = JSON.parse(fs.readFileSync(path.join(directory, 'package.json'), 'utf8'));
    console.log('Metadata:', JSON.stringify({
      name: manifest.name, version: manifest.version, main: manifest.main,
      exports: manifest.exports, dependencies: manifest.dependencies,
    }, null, 2));
  } catch (error) {
    console.log('File inspection failed:', error.message);
  }
}

for (const context of ['package.json', 'node_modules/electron/install.js']) {
  const resolveFrom = createRequire(path.resolve(context));
  console.log(`\nResolution from ${context}:`);
  for (const name of packages) {
    try {
      console.log(name, '=>', resolveFrom.resolve(name));
    } catch (error) {
      console.log(name, '=>', error.code, error.message);
      console.log('Search paths:', resolveFrom.resolve.paths(name));
    }
  }
}
NODE
echo '::endgroup::'
exit "$install_status"
