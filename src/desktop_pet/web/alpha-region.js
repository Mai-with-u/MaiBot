/* 将 RGBA 像素中非透明的横向游程合并为互不重叠的矩形。 */
(() => {
  window.petAlphaRegion = function(data, width, height) {
    const rects = [];
    let previous = new Map();
    for (let y = 0; y < height; y++) {
      const current = new Map();
      let x = 0;
      while (x < width) {
        while (x < width && data[(y * width + x) * 4 + 3] === 0) x++;
        const left = x;
        while (x < width && data[(y * width + x) * 4 + 3] !== 0) x++;
        if (x === left) continue;
        const key = `${left}:${x}`;
        let rect = previous.get(key);
        if (rect) rect[3]++;
        else { rect = [left, y, x - left, 1, 0]; rects.push(rect); }
        current.set(key, rect);
      }
      previous = current;
    }
    return rects;
  };
})();
