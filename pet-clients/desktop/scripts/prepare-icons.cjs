'use strict';

// Preserve the existing MaiBot artwork: copy its largest embedded PNG verbatim,
// then use Tauri's official converter for the macOS container and resolutions.
const assert = require('node:assert/strict');
const { spawnSync } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const root = path.resolve(__dirname, '..');
const source = fs.readFileSync(path.resolve(root, '../../dashboard/public/maimai.ico'));
const signature = Buffer.from('89504e470d0a1a0a', 'hex');
assert.ok(source.length >= 6 && source.readUInt16LE(0) === 0 && source.readUInt16LE(2) === 1, 'Expected an ICO file');
const count = source.readUInt16LE(4);
assert.ok(count > 0 && 6 + count * 16 <= source.length, 'Invalid ICO directory');
const frames = [];
for (let index = 0; index < count; index++) {
  const entry = 6 + index * 16;
  const length = source.readUInt32LE(entry + 8), offset = source.readUInt32LE(entry + 12);
  assert.ok(offset >= 6 + count * 16 && offset + length <= source.length, 'Invalid ICO frame range');
  const png = source.subarray(offset, offset + length);
  if (png.length < 33 || !png.subarray(0, 8).equals(signature)) continue;
  assert.equal(png.toString('ascii', 12, 16), 'IHDR', 'Invalid embedded PNG');
  const width = png.readUInt32BE(16), height = png.readUInt32BE(20);
  if (width > 0 && width === height) frames.push({ png, width });
}
frames.sort((left, right) => right.width - left.width);
assert.ok(frames.length, 'The source ICO must contain a square PNG frame');
const { png, width } = frames[0];
const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'maibot-pet-icons-'));
try {
  const input = path.join(temporary, 'source.png');
  const generated = path.join(temporary, 'generated');
  fs.writeFileSync(input, png);
  const cli = path.join(path.dirname(require.resolve('@tauri-apps/cli')), 'tauri.js');
  const result = spawnSync(process.execPath, [cli, 'icon', input, '--output', generated], { cwd: root, stdio: 'inherit' });
  if (result.error) throw result.error;
  assert.equal(result.status, 0, 'Tauri icon generation failed');
  const icns = fs.readFileSync(path.join(generated, 'icon.icns'));
  assert.ok(icns.length > 8 && icns.toString('ascii', 0, 4) === 'icns'
    && icns.readUInt32BE(4) === icns.length, 'Invalid generated ICNS');
  const output = path.join(root, 'src-tauri', 'icons');
  fs.mkdirSync(output, { recursive: true });
  fs.writeFileSync(path.join(output, 'icon.png'), png);
  fs.writeFileSync(path.join(output, 'icon.icns'), icns);
  console.log(`Preserved ${width} × ${width} PNG from maimai.ico; generated icons/icon.icns with Tauri.`);
} finally {
  // Delete only this invocation's unique temporary directory, never a caller-provided path.
  const resolved = path.resolve(temporary);
  assert.ok(path.dirname(resolved) === path.resolve(os.tmpdir())
    && path.basename(resolved).startsWith('maibot-pet-icons-'), 'Unsafe temporary directory');
  fs.rmSync(resolved, { recursive: true, force: true });
}
