'use strict';

const { randomUUID } = require('node:crypto');

function contains(rect, point) {
  const [x, y, width, height, corner] = rect;
  if (point.x < x || point.x > x + width || point.y < y || point.y > y + height) return false;
  const radius = Math.min(corner, width / 2, height / 2);
  const dx = Math.max(x + radius - point.x, 0, point.x - (x + width - radius));
  const dy = Math.max(y + radius - point.y, 0, point.y - (y + height - radius));
  return dx * dx + dy * dy <= radius * radius;
}

function validateRects(rects) {
  if (!Array.isArray(rects) || rects.length > 256 || rects.some(rect =>
    !Array.isArray(rect) || rect.length !== 5 || rect.some(n => !Number.isFinite(n) || Math.abs(n) > 10000)
    || rect[2] < 0 || rect[3] < 0 || rect[4] < 0)) throw new Error('无效窗口区域');
  return rects;
}

function shapeRects(rects, width, height) {
  const shapes = [];
  for (const [x, y, w, h, corner] of rects) {
    const radius = Math.min(corner, w / 2, h / 2);
    let previous;
    for (let row = Math.max(0, Math.floor(y)); row < Math.min(height, Math.ceil(y + h)); row++) {
      const dy = Math.max(y + radius - (row + .5), 0, row + .5 - (y + h - radius));
      const inset = radius - Math.sqrt(Math.max(0, radius * radius - dy * dy));
      const left = Math.max(0, Math.floor(x + inset)), right = Math.min(width, Math.ceil(x + w - inset));
      if (right <= left) continue;
      if (previous && previous.x === left && previous.width === right - left && previous.y + previous.height === row) previous.height++;
      else { previous = { x: left, y: row, width: right - left, height: 1 }; shapes.push(previous); }
    }
  }
  // 空数组表示恢复整个窗口，使用窗口外的小区域表达“没有可点击内容”。
  return shapes.length ? shapes : [{ x: -1, y: -1, width: 1, height: 1 }];
}

/** Electron 的窗口、屏幕光标均采用 DIP，不能再次乘 devicePixelRatio。 */
class PetWindowController {
  constructor(window, screen, { anchor, emit, save, platform = process.platform }) {
    this.window = window;
    this.screen = screen;
    this.emit = emit;
    this.save = save;
    this.shaped = platform === 'win32' || platform === 'linux';
    this.drag = null;
    this.height = Math.min(900, screen.getPrimaryDisplay().workArea.height);
    this.anchor = anchor && screen.getAllDisplays().some(({ workArea: a }) =>
      anchor.x >= a.x && anchor.x <= a.x + a.width && anchor.y - 40 >= a.y && anchor.y - 40 <= a.y + a.height
    ) ? anchor : this.defaultAnchor();
    this.rects = [[47, this.height - 155, 166, 139, 42]];
    this.ignored = null;
    this.place();
    // Windows/X11 由原生区域精确处理穿透；macOS 没有 setShape，按全局光标切换穿透。
    this.timer = this.shaped ? null : setInterval(() => this.updatePointer(), 32);
    this.region(this.rects);
    this.displayChanged = () => this.keepOnScreen();
    screen.on('display-removed', this.displayChanged);
    screen.on('display-metrics-changed', this.displayChanged);
    window.on('closed', () => {
      clearInterval(this.timer);
      screen.removeListener('display-removed', this.displayChanged);
      screen.removeListener('display-metrics-changed', this.displayChanged);
    });
  }

  defaultAnchor() {
    const area = this.screen.getPrimaryDisplay().workArea;
    return { x: area.x + area.width - 138, y: area.y + area.height };
  }

  place() {
    this.window.setBounds({ x: Math.round(this.anchor.x - 130), y: Math.round(this.anchor.y - this.height), width: 260, height: this.height });
  }

  keepOnScreen() {
    if (this.window.isDestroyed()) return;
    const area = this.screen.getDisplayNearestPoint(this.anchor).workArea;
    this.anchor.x = Math.max(area.x + 130, Math.min(area.x + area.width - 130, this.anchor.x));
    this.anchor.y = Math.max(area.y + 180, Math.min(area.y + area.height, this.anchor.y));
    this.place();
  }

  fit(_scale, height) {
    if (!Number.isFinite(height)) throw new Error('无效窗口高度');
    this.height = Math.round(Math.max(460, Math.min(900, height)));
    this.place();
  }

  region(rects) {
    this.rects = validateRects(rects);
    if (this.shaped) this.window.setShape(shapeRects(this.rects, 260, this.height));
    else this.updatePointer();
  }

  updatePointer() {
    if (this.shaped || this.window.isDestroyed()) return;
    const cursor = this.screen.getCursorScreenPoint(), bounds = this.window.getBounds();
    const ignore = !this.drag && !this.rects.some(rect => contains(rect, { x: cursor.x - bounds.x, y: cursor.y - bounds.y }));
    if (ignore !== this.ignored) {
      this.window.setIgnoreMouseEvents(ignore, { forward: true });
      this.ignored = ignore;
    }
  }

  dragBegin() {
    this.drag = { cursor: this.screen.getCursorScreenPoint(), anchor: { ...this.anchor }, id: randomUUID().replaceAll('-', ''), moved: false };
    this.updatePointer();
  }

  async dragMove() {
    const drag = this.drag;
    if (!drag) return;
    const cursor = this.screen.getCursorScreenPoint();
    this.anchor = { x: drag.anchor.x + cursor.x - drag.cursor.x, y: drag.anchor.y + cursor.y - drag.cursor.y };
    this.place();
    if (!drag.moved) {
      drag.moved = true;
      const scale = this.screen.getDisplayNearestPoint(cursor).scaleFactor;
      await this.emit({ type: 'drag_started', gesture_id: drag.id,
        offset_x: (drag.anchor.x - drag.cursor.x) * scale, offset_y: (drag.anchor.y - drag.cursor.y) * scale, scale });
    }
  }

  async dragEnd() {
    const drag = this.drag;
    this.drag = null;
    this.updatePointer();
    if (drag?.moved) {
      await this.save(this.anchor);
      await this.emit({ type: 'drag_ended', gesture_id: drag.id });
    }
  }
}

class RecordsWindowController {
  constructor(window, screen) { this.window = window; this.screen = screen; this.timer = null; }

  control(action) {
    if (action === 'close') { this.end(); this.window.close(); return false; }
    if (action === 'minimize') this.window.minimize();
    else if (action === 'maximize') this.window.isMaximized() ? this.window.unmaximize() : this.window.maximize();
    else if (action !== 'state') throw new Error('未知窗口操作');
    return this.window.isMaximized();
  }

  gesture(edge) {
    this.end();
    if (edge === 'move') return; // 标题栏由 CSS app-region 交给操作系统拖动。
    if (!['n', 's', 'w', 'e', 'nw', 'ne', 'sw', 'se'].includes(edge)) throw new Error('未知缩放方向');
    if (this.window.isMaximized()) return;
    const start = this.screen.getCursorScreenPoint(), bounds = this.window.getBounds();
    this.timer = setInterval(() => {
      if (this.window.isDestroyed()) { this.end(); return; }
      const cursor = this.screen.getCursorScreenPoint();
      let { x, y, width, height } = bounds;
      if (edge.includes('e')) width = Math.max(680, bounds.width + cursor.x - start.x);
      if (edge.includes('s')) height = Math.max(480, bounds.height + cursor.y - start.y);
      if (edge.includes('w')) { width = Math.max(680, bounds.width - cursor.x + start.x); x = bounds.x + bounds.width - width; }
      if (edge.includes('n')) { height = Math.max(480, bounds.height - cursor.y + start.y); y = bounds.y + bounds.height - height; }
      this.window.setBounds({ x, y, width, height });
    }, 16);
  }

  end() { clearInterval(this.timer); this.timer = null; }
}

module.exports = { contains, validateRects, shapeRects, PetWindowController, RecordsWindowController };
