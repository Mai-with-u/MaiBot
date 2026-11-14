export interface RegionRect {
  x: number
  y: number
  width: number
  height: number
}

/** 合并相交矩形的边界，只绘制容器外轮廓，不留下内部拼接线。 */
export function regionOutline(rects: RegionRect[]): string {
  const xs = [...new Set(rects.flatMap((r) => [r.x, r.x + r.width]))].sort((a, b) => a - b)
  const ys = [...new Set(rects.flatMap((r) => [r.y, r.y + r.height]))].sort((a, b) => a - b)
  const filled = new Set<string>()
  for (let x = 0; x < xs.length - 1; x++)
    for (let y = 0; y < ys.length - 1; y++) {
      if (
        rects.some(
          (r) =>
            xs[x] >= r.x &&
            xs[x + 1] <= r.x + r.width &&
            ys[y] >= r.y &&
            ys[y + 1] <= r.y + r.height
        )
      )
        filled.add(`${x},${y}`)
    }
  const segments = new Map<string, string>()
  for (const cell of filled) {
    const [x, y] = cell.split(',').map(Number)
    if (!filled.has(`${x},${y - 1}`)) segments.set(`${xs[x]},${ys[y]}`, `${xs[x + 1]},${ys[y]}`)
    if (!filled.has(`${x + 1},${y}`))
      segments.set(`${xs[x + 1]},${ys[y]}`, `${xs[x + 1]},${ys[y + 1]}`)
    if (!filled.has(`${x},${y + 1}`))
      segments.set(`${xs[x + 1]},${ys[y + 1]}`, `${xs[x]},${ys[y + 1]}`)
    if (!filled.has(`${x - 1},${y}`)) segments.set(`${xs[x]},${ys[y + 1]}`, `${xs[x]},${ys[y]}`)
  }
  const paths: string[] = []
  while (segments.size) {
    const start = segments.keys().next().value!
    let current = start
    const points = [start]
    do {
      const next = segments.get(current)!
      segments.delete(current)
      points.push(next)
      current = next
    } while (current !== start)
    paths.push(`M ${points.join(' L ')} Z`)
  }
  return paths.join(' ')
}
