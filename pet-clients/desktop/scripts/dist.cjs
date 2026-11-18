'use strict';
const { createHash } = require('node:crypto');
const { spawnSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '..');
const formats = process.argv[2];
if (!['nsis', 'dmg', 'deb,rpm,appimage'].includes(formats)) throw new Error('Unknown distribution format');
const extra = process.argv.slice(3);
const cli = path.join(path.dirname(require.resolve('@tauri-apps/cli')), 'tauri.js');
const built = spawnSync(process.execPath, [cli, 'build', '--bundles', formats, ...extra], { cwd: root, stdio: 'inherit' });
if (built.error) throw built.error;
if (built.status !== 0) process.exit(built.status ?? 1);

const targetIndex = extra.indexOf('--target');
const target = targetIndex >= 0 ? extra[targetIndex + 1] : extra.find(value => value.startsWith('--target='))?.slice(9) || '';
if (target && !/^[A-Za-z0-9_-]+$/.test(target)) throw new Error('Invalid Rust target');
const output = path.resolve(root, '../../dist/desktop-pet-client');
const bundle = path.join(root, 'src-tauri/target', target, extra.includes('--debug') ? 'debug' : 'release', 'bundle');
const version = require('../package.json').version;
const architecture = target ? target.split('-')[0].replace('x86_64', 'x64').replace('aarch64', 'arm64') : process.arch;
const platform = formats === 'nsis' ? 'win' : formats === 'dmg' ? 'mac' : 'linux';
const aliases = { x64: ['x64', 'x86_64', 'amd64'], arm64: ['arm64', 'aarch64'], ia32: ['x86', 'i686', 'i386'],
  armv7: ['armv7', 'armhf'], universal: ['universal'] }[architecture];
if (!aliases) throw new Error(`Unsupported output architecture: ${architecture}`);
const outputs = [];
fs.mkdirSync(output, { recursive: true });
for (const format of formats.split(',')) {
  const folder = path.join(bundle, format);
  const suffix = { nsis: '-setup.exe', dmg: '.dmg', deb: '.deb', rpm: '.rpm', appimage: '.AppImage' }[format];
  const files = fs.readdirSync(folder).filter(file => file.endsWith(suffix));
  // Only publish the artifact just built for this version and architecture.
  const candidates = files.filter(file => file.includes(version)
    && aliases.some(alias => new RegExp(`(?:^|[_\\-.])${alias}(?=[_\\-.]|$)`).test(file)));
  if (candidates.length !== 1) throw new Error(`Expected one ${format} artifact, found ${candidates.length}`);
  const name = `maibot-desktop-pet-${version}-${platform}-${architecture}${suffix}`;
  fs.copyFileSync(path.join(folder, candidates[0]), path.join(output, name));
  const hash = createHash('sha256').update(fs.readFileSync(path.join(output, name))).digest('hex');
  outputs.push(`${hash}  ${name}`);
  console.log(`${name}: ${(fs.statSync(path.join(output, name)).size / 1048576).toFixed(2)} MiB`);
}
const checksumFile = path.join(output, 'SHA256SUMS.txt');
const replaced = new Set(outputs.map(line => line.slice(66)));
const previous = fs.existsSync(checksumFile) ? fs.readFileSync(checksumFile, 'utf8').split(/\r?\n/).filter(line => {
  const match = /^([0-9a-f]{64})  ([^/\\]+)$/.exec(line);
  if (!match || replaced.has(match[2]) || !fs.existsSync(path.join(output, match[2]))) return false;
  return createHash('sha256').update(fs.readFileSync(path.join(output, match[2]))).digest('hex') === match[1];
}) : [];
fs.writeFileSync(checksumFile, [...previous, ...outputs].sort().join('\n') + '\n');
