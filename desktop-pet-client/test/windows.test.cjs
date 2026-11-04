'use strict';
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const test = require('node:test');
const { contains, validateRects, shapeRects, PetWindowController } = require('../src/windows.cjs');

test('透明圆角与空白区域允许穿透，边界和非法区域校验', () => {
  assert.equal(contains([10, 20, 100, 60, 20], { x: 10, y: 20 }), false);
  assert.equal(contains([10, 20, 100, 60, 20], { x: 30, y: 20 }), true);
  assert.equal(contains([10, 20, 100, 60, 20], { x: 1000, y: 40 }), false);
  assert.throws(() => validateRects([[0, 0, 10, -1, 0]]));
  assert.throws(() => validateRects([[NaN, 0, 10, 1, 0]]));
});

test('原生区域保留圆角、裁剪到窗口边界且不会意外恢复整窗', () => {
  const shapes = shapeRects([[10, 10, 60, 60, 20]], 100, 100);
  const hit = (x, y) => shapes.some(rect => x >= rect.x && x < rect.x + rect.width && y >= rect.y && y < rect.y + rect.height);
  assert.equal(hit(10, 10), false); assert.equal(hit(30, 10), true); assert.equal(hit(40, 40), true);
  assert.equal(hit(70, 70), false);
  for (const rect of shapeRects([[-20, -20, 200, 200, 40]], 100, 100)) {
    assert.ok(rect.x >= 0 && rect.y >= 0 && rect.x + rect.width <= 100 && rect.y + rect.height <= 100);
  }
  assert.equal(shapeRects([], 100, 100).length, 1);
});

test('高DPI窗口只使用一次缩放，拖动仍上报服务端要求的物理偏移', async () => {
  const window = new EventEmitter(), screen = new EventEmitter();
  let bounds, cursor = { x: 1800, y: 1000 }, ignored;
  Object.assign(window, {
    setBounds(value) { bounds = value; }, getBounds: () => bounds, isDestroyed: () => false,
    setIgnoreMouseEvents(value) { ignored = value; },
  });
  const display = { workArea: { x: 0, y: 0, width: 1920, height: 1080 }, scaleFactor: 2 };
  Object.assign(screen, { getPrimaryDisplay: () => display, getAllDisplays: () => [display],
    getDisplayNearestPoint: () => display, getCursorScreenPoint: () => cursor });
  const events = [], saved = [];
  const controller = new PetWindowController(window, screen, { platform: 'darwin', emit: async event => events.push(event), save: async value => saved.push(value) });
  try {
    controller.fit(2, 900);
    assert.equal(bounds.width, 260); assert.equal(bounds.height, 900);
    controller.region([[0, 0, 20, 20, 0]]); assert.equal(ignored, true);
    controller.dragBegin(); assert.equal(ignored, false);
    cursor = { x: 1840, y: 970 };
    await controller.dragMove(); await controller.dragEnd();
    assert.equal(saved[0].x, 1822); assert.equal(saved[0].y, 1050);
    assert.equal(events[0].type, 'drag_started'); assert.equal(events[0].offset_x, -36);
    assert.equal(events[0].scale, 2); assert.equal(events[1].type, 'drag_ended');
    await controller.dragEnd(); assert.equal(events.length, 2);
  } finally { window.emit('closed'); }
});
