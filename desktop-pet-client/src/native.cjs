'use strict';

const { execFile: nodeExecFile } = require('node:child_process');
const { createHash } = require('node:crypto');
const { basename } = require('node:path').posix;
const { Worker, isMainThread, parentPort, workerData } = require('node:worker_threads');

const MAX_EDGE = 1920;
const MAX_CAPTURE_PIXELS = 16_000_000;
const MAX_CLIPBOARD_PIXELS = 4_000_000;
const SAMPLE_WIDTH = 320;
const SAMPLE_HEIGHT = 180;
const PNG_SIGNATURE = Buffer.from('89504e470d0a1a0a', 'hex');
const WINDOWS_PROCESSES =
  "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; $ErrorActionPreference='Stop'; " +
  '@(Get-Process | Where-Object { $_.Id -ne $PID } | Select-Object ProcessName,MainWindowTitle) | ConvertTo-Json -Compress';

/** CPU-heavy pixel work and PNG codecs run outside Electron's main/network loop. */
function imageWorker(payload) {
  return new Promise((resolve, reject) => {
    const worker = new Worker(__filename, {
      workerData: { desktopPetNativeWorker: 1, ...payload },
      resourceLimits: { maxOldGenerationSizeMb: 128 },
    });
    let settled = false;
    const finish = (error, value) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      void worker.terminate();
      if (error) reject(error);
      else resolve(value);
    };
    const timer = setTimeout(() => finish(new Error('图片处理超时')), 15_000);
    worker.once('message', message => finish(message.ok ? null : new Error(message.error), message.value));
    worker.once('error', error => finish(error));
    worker.once('exit', code => {
      if (!settled) finish(new Error(`图片处理进程提前退出（${code}）`));
    });
  });
}

function dimensions(width, height, limit) {
  if (!Number.isInteger(width) || !Number.isInteger(height) || width < 1 || height < 1 || width * height > limit) {
    throw new Error('图片尺寸无效或超过限制');
  }
}

function scaledSize(width, height, edge = MAX_EDGE) {
  const ratio = Math.min(1, edge / Math.max(width, height));
  return { width: Math.max(1, Math.round(width * ratio)), height: Math.max(1, Math.round(height * ratio)) };
}

/** Bilinear sampling; input/output use RGB, independent of native bitmap ordering. */
function resizeRgb(source, width, height, outputWidth, outputHeight) {
  const result = Buffer.allocUnsafe(outputWidth * outputHeight * 3);
  for (let y = 0; y < outputHeight; y++) {
    const sy = Math.max(0, Math.min(height - 1, (y + 0.5) * height / outputHeight - 0.5));
    const top = Math.floor(sy), bottom = Math.min(height - 1, top + 1), fy = sy - top;
    for (let x = 0; x < outputWidth; x++) {
      const sx = Math.max(0, Math.min(width - 1, (x + 0.5) * width / outputWidth - 0.5));
      const left = Math.floor(sx), right = Math.min(width - 1, left + 1), fx = sx - left;
      const a = (top * width + left) * 3, b = (top * width + right) * 3;
      const c = (bottom * width + left) * 3, d = (bottom * width + right) * 3;
      for (let channel = 0; channel < 3; channel++) {
        result[(y * outputWidth + x) * 3 + channel] = Math.round(
          (source[a + channel] * (1 - fx) + source[b + channel] * fx) * (1 - fy) +
          (source[c + channel] * (1 - fx) + source[d + channel] * fx) * fy,
        );
      }
    }
  }
  return result;
}

function processCapture(payload) {
  const { PNG } = require('pngjs');
  const input = Buffer.from(payload.bitmap);
  dimensions(payload.width, payload.height, MAX_CAPTURE_PIXELS);
  if (input.length !== payload.width * payload.height * 4) throw new Error('屏幕位图长度与尺寸不一致');
  const rgb = Buffer.allocUnsafe(payload.width * payload.height * 3);
  // Electron NativeImage uses Skia's premultiplied BGRA bitmap on supported desktop platforms.
  for (let i = 0, j = 0; i < input.length; i += 4, j += 3) {
    const alpha = input[i + 3];
    rgb[j] = alpha ? Math.min(255, Math.round(input[i + 2] * 255 / alpha)) : 0;
    rgb[j + 1] = alpha ? Math.min(255, Math.round(input[i + 1] * 255 / alpha)) : 0;
    rgb[j + 2] = alpha ? Math.min(255, Math.round(input[i] * 255 / alpha)) : 0;
  }
  const size = scaledSize(payload.width, payload.height);
  const pixels = size.width === payload.width && size.height === payload.height
    ? rgb : resizeRgb(rgb, payload.width, payload.height, size.width, size.height);
  const small = resizeRgb(pixels, size.width, size.height, SAMPLE_WIDTH, SAMPLE_HEIGHT);
  if (payload.previous && payload.previous.width === size.width && payload.previous.height === size.height) {
    const previous = payload.previous.pixels;
    if (previous.length !== small.length) throw new Error('屏幕采样状态损坏');
    let changed = 0;
    for (let i = 0; i < small.length; i += 3) {
      // Match the Bot's RGB-difference -> luminance rule, ignoring differences below 20/255.
      const difference = Math.round(0.299 * Math.abs(small[i] - previous[i]) +
        0.587 * Math.abs(small[i + 1] - previous[i + 1]) + 0.114 * Math.abs(small[i + 2] - previous[i + 2]));
      if (difference >= 20) changed++;
    }
    if (changed / (SAMPLE_WIDTH * SAMPLE_HEIGHT) < payload.threshold) return { result: { unchanged: true } };
  }
  const rgba = Buffer.allocUnsafe(size.width * size.height * 4);
  for (let i = 0, j = 0; i < pixels.length; i += 3, j += 4) {
    rgba[j] = pixels[i]; rgba[j + 1] = pixels[i + 1]; rgba[j + 2] = pixels[i + 2]; rgba[j + 3] = 255;
  }
  const png = PNG.sync.write({ ...size, data: rgba }, { colorType: 2, inputColorType: 6, inputHasAlpha: true });
  return {
    result: { base64: png.toString('base64'), captured_at: payload.capturedAt,
      ...size, digest: createHash('sha256').update(png).digest('hex') },
    sample: { ...size, pixels: small },
  };
}

function decodeClipboardImage(content) {
  const { PNG } = require('pngjs');
  if (typeof content !== 'string' || !content || content.length > 1_000_000 || content.length % 4 !== 0 ||
      !/^[A-Za-z0-9+/]*={0,2}$/.test(content)) throw new Error('无效的 PNG 图片或内容过大');
  const png = Buffer.from(content, 'base64');
  if (png.toString('base64') !== content || png.length < 33 || !png.subarray(0, 8).equals(PNG_SIGNATURE) ||
      png.toString('ascii', 12, 16) !== 'IHDR' || png.readUInt32BE(8) !== 13) {
    throw new Error('面板只支持有效的 PNG 图片');
  }
  const width = png.readUInt32BE(16), height = png.readUInt32BE(20);
  dimensions(width, height, MAX_CLIPBOARD_PIXELS);
  // Reject duplicate headers and truncated chunks before a decoder can allocate from another size.
  let offset = 8, ended = false, hasData = false;
  while (offset + 12 <= png.length) {
    const length = png.readUInt32BE(offset), type = png.toString('ascii', offset + 4, offset + 8);
    if (offset + length + 12 > png.length || (type === 'IHDR' && offset !== 8)) throw new Error('PNG 数据结构无效');
    if (type === 'IDAT') hasData = true;
    offset += length + 12;
    if (type === 'IEND') {
      ended = length === 0 && offset === png.length;
      break;
    }
  }
  if (!ended || !hasData) throw new Error('PNG 图片不完整');
  const decoded = PNG.sync.read(png, { checkCRC: true });
  if (decoded.width !== width || decoded.height !== height) throw new Error('PNG 图片尺寸不一致');
  const bitmap = Buffer.allocUnsafe(width * height * 4);
  for (let i = 0; i < bitmap.length; i += 4) {
    const alpha = decoded.data[i + 3];
    bitmap[i] = Math.round(decoded.data[i + 2] * alpha / 255);
    bitmap[i + 1] = Math.round(decoded.data[i + 1] * alpha / 255);
    bitmap[i + 2] = Math.round(decoded.data[i] * alpha / 255);
    bitmap[i + 3] = alpha;
  }
  return { bitmap, width, height };
}

/**
 * Electron dependencies are supplied by main; this module never imports Electron.
 * options.platform/execFile are test seams. macOS/Linux expose process names only,
 * because obtaining arbitrary window titles requires additional desktop-specific APIs.
 */
function createNativeCapabilities({ desktopCapturer, screen, nativeImage, clipboard, systemPreferences }, options = {}) {
  const platform = options.platform === undefined ? process.platform : options.platform;
  const execFile = options.execFile === undefined ? nodeExecFile : options.execFile;
  let previous = null;
  let captureBusy = false;

  function requireScreenPermission() {
    if (options.allowScreenRead !== true) throw new Error('客户端未授权读取桌面，请在连接设置中开启');
    if (platform === 'darwin') {
      const permission = systemPreferences.getMediaAccessStatus('screen');
      if (permission !== 'granted') {
        throw new Error(`macOS 屏幕录制权限未授权（${permission}），请在系统设置的隐私与安全性中允许本应用，然后重新启动`);
      }
    }
  }

  async function capture(sampleOptions) {
    requireScreenPermission();
    if (captureBusy) throw new Error('正在采集桌面，请稍后重试');
    captureBusy = true;
    try {
      if (sampleOptions && sampleOptions.reset) previous = null;
      const display = screen.getPrimaryDisplay();
      const size = scaledSize(Math.round(display.bounds.width * display.scaleFactor), Math.round(display.bounds.height * display.scaleFactor));
      const sources = await desktopCapturer.getSources({ types: ['screen'], thumbnailSize: size, fetchWindowIcons: false });
      requireScreenPermission();
      // Do not silently capture another display when PipeWire or a compositor omits display IDs.
      const source = sources.find(item => item.display_id === String(display.id));
      if (!source) throw new Error('无法确定主屏截图来源；当前桌面环境可能未提供显示器标识');
      if (source.thumbnail.isEmpty()) throw new Error('无法读取主屏画面，请检查系统屏幕录制权限');
      const dimensions = source.thumbnail.getSize(1);
      if (!Number.isInteger(dimensions.width) || !Number.isInteger(dimensions.height) ||
          dimensions.width < 1 || dimensions.height < 1 || dimensions.width * dimensions.height > MAX_CAPTURE_PIXELS) {
        throw new Error('主屏截图尺寸无效或过大');
      }
      const processed = await imageWorker({
        operation: 'capture', bitmap: source.thumbnail.toBitmap({ scaleFactor: 1 }), ...dimensions,
        capturedAt: new Date().toISOString(), previous: sampleOptions ? previous : null,
        threshold: sampleOptions ? sampleOptions.threshold : null,
      });
      requireScreenPermission();
      if (sampleOptions && processed.sample) previous = processed.sample;
      return processed.result;
    } finally {
      captureBusy = false;
    }
  }

  async function sampleScreen({ threshold, reset = false } = {}) {
    if (typeof threshold !== 'number' || !Number.isFinite(threshold) || threshold < 0.001 || threshold > 1 || typeof reset !== 'boolean') {
      throw new Error('屏幕变化阈值必须在 0.001～1 之间，reset 必须为布尔值');
    }
    return capture({ threshold, reset });
  }

  async function applications() {
    let command, args;
    if (platform === 'win32') {
      command = 'powershell.exe';
      args = ['-NoProfile', '-NonInteractive', '-Command', WINDOWS_PROCESSES];
    } else if (platform === 'darwin' || platform === 'linux') {
      command = '/bin/ps';
      args = ['-A', '-o', 'comm='];
    } else {
      throw new Error('当前平台不支持应用检测');
    }
    const stdout = await new Promise((resolve, reject) => {
      execFile(command, args, { encoding: 'utf8', timeout: 10_000, maxBuffer: 2 * 1024 * 1024, windowsHide: true },
        (error, output) => error ? reject(new Error(`读取本机应用失败：${error.message}`)) : resolve(output));
    });
    if (platform === 'win32') {
      const data = JSON.parse(stdout.replace(/^\uFEFF/, ''));
      const entries = Array.isArray(data) ? data : [data];
      return entries.map(entry => {
        if (!entry || typeof entry.ProcessName !== 'string' || typeof entry.MainWindowTitle !== 'string') {
          throw new Error('应用查询返回了无效的进程信息');
        }
        return { process: entry.ProcessName, title: entry.MainWindowTitle };
      });
    }
    return stdout.split(/\r?\n/).map(line => line.trim()).filter(Boolean)
      .map(name => ({ process: basename(name), title: '' }));
  }

  async function copyPanelContent(kind, content) {
    if (typeof content !== 'string') throw new Error('无效的复制内容');
    if (kind === 'text') {
      if (content.length > 32_000 || Array.from(content).length > 16_000) throw new Error('复制文本不能超过 16000 字');
      clipboard.writeText(content);
      return;
    }
    if (kind !== 'image' || content.length > 1_000_000) throw new Error('不支持的复制类型或内容过大');
    const decoded = await imageWorker({ operation: 'clipboard', content });
    // Decode/validate in the worker; only bounded native bitmap and clipboard copies run here.
    const image = nativeImage.createFromBitmap(Buffer.from(decoded.bitmap), { width: decoded.width, height: decoded.height, scaleFactor: 1 });
    if (image.isEmpty()) throw new Error('无法创建剪贴板图片');
    clipboard.writeImage(image);
  }

  return { captureScreen: () => capture(null), sampleScreen, applications, copyPanelContent };
}

if (!isMainThread && workerData && workerData.desktopPetNativeWorker === 1) {
  try {
    const value = workerData.operation === 'capture' ? processCapture(workerData) : decodeClipboardImage(workerData.content);
    parentPort.postMessage({ ok: true, value });
  } catch (error) {
    parentPort.postMessage({ ok: false, error: error.message });
  }
}

module.exports = { createNativeCapabilities };
