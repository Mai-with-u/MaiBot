const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const context = {window:{}};
vm.runInNewContext(fs.readFileSync('src/desktop_pet/web/alpha-region.js', 'utf8'), context);

test('alpha regions preserve transparent margins, gaps and holes', () => {
  const rows = ['......', '..##..', '.#..#.', '.#..#.', '..##..', '......'];
  const data = new Uint8ClampedArray(6 * 6 * 4);
  rows.forEach((row,y) => [...row].forEach((pixel,x) => {data[(y*6+x)*4+3] = pixel === '#' ? 1 : 0}));
  const rects = context.window.petAlphaRegion(data, 6, 6);
  for (let y=0; y<6; y++) for (let x=0; x<6; x++) {
    const hit = rects.some(([left,top,width,height]) => x>=left && x<left+width && y>=top && y<top+height);
    assert.equal(hit, rows[y][x] === '#', `pixel ${x},${y}`);
  }
  assert.equal(rects.length, 4);
});

test('fully transparent frame creates no hit regions', () => {
  assert.equal(context.window.petAlphaRegion(new Uint8ClampedArray(16), 2, 2).length, 0);
});
