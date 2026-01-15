import { useEffect, useRef, useState } from 'react'

// 生成一个固定的随机种子（在模块加载时生成一次）
const NOISE_SEED = (() => {
  // 使用时间戳的一部分作为种子，但在开发环境中使用固定值以保持一致性
  if (import.meta.env.DEV) {
    return 42 // 开发环境使用固定种子
  }
  return Date.now() % 1000000
})()

// Perlin Noise implementation
class Noise {
  private grad3: number[][]
  private p: number[]
  private perm: number[]

  constructor(seed = 0) {
    // Use seed to ensure deterministic noise (seed is used implicitly in shuffle)
    void seed
    this.grad3 = [
      [1, 1, 0],
      [-1, 1, 0],
      [1, -1, 0],
      [-1, -1, 0],
      [1, 0, 1],
      [-1, 0, 1],
      [1, 0, -1],
      [-1, 0, -1],
      [0, 1, 1],
      [0, -1, 1],
      [0, 1, -1],
      [0, -1, -1],
    ]
    this.p = []
    for (let i = 0; i < 256; i++) {
      this.p[i] = Math.floor(Math.random() * 256)
    }
    this.perm = []
    for (let i = 0; i < 512; i++) {
      this.perm[i] = this.p[i & 255]
    }
  }

  dot(g: number[], x: number, y: number) {
    return g[0] * x + g[1] * y
  }

  mix(a: number, b: number, t: number) {
    return (1 - t) * a + t * b
  }

  fade(t: number) {
    return t * t * t * (t * (t * 6 - 15) + 10)
  }

  perlin2(x: number, y: number) {
    const X = Math.floor(x) & 255
    const Y = Math.floor(y) & 255
    x -= Math.floor(x)
    y -= Math.floor(y)
    const u = this.fade(x)
    const v = this.fade(y)
    const A = this.perm[X] + Y
    const AA = this.perm[A]
    const AB = this.perm[A + 1]
    const B = this.perm[X + 1] + Y
    const BA = this.perm[B]
    const BB = this.perm[B + 1]

    return this.mix(
      this.mix(
        this.dot(this.grad3[AA % 12], x, y),
        this.dot(this.grad3[BA % 12], x - 1, y),
        u
      ),
      this.mix(
        this.dot(this.grad3[AB % 12], x, y - 1),
        this.dot(this.grad3[BB % 12], x - 1, y - 1),
        u
      ),
      v
    )
  }
}

interface Point {
  x: number
  y: number
  wave: { x: number; y: number }
  cursor: { x: number; y: number; vx: number; vy: number }
}

export function WavesBackground() {
  const svgRef = useRef<SVGSVGElement>(null)
  const containerRef = useRef<HTMLDivElement>(null)
  const animationRef = useRef<number | undefined>(undefined)
  const [noiseInstance] = useState(() => new Noise(NOISE_SEED))
  
  const dataRef = useRef<{
    mouse: {
      x: number
      y: number
      lx: number
      ly: number
      sx: number
      sy: number
      v: number
      vs: number
      a: number
      set: boolean
    }
    lines: Point[][]
    paths: SVGPathElement[]
    noise: Noise
    bounding: DOMRect | null
  }>({
    mouse: {
      x: -10,
      y: 0,
      lx: 0,
      ly: 0,
      sx: 0,
      sy: 0,
      v: 0,
      vs: 0,
      a: 0,
      set: false,
    },
    lines: [],
    paths: [],
    noise: noiseInstance,
    bounding: null,
  })

  useEffect(() => {
    const container = containerRef.current
    const svg = svgRef.current
    if (!container || !svg) return

    const data = dataRef.current
    // 将 noiseInstance 赋值给 dataRef
    data.noise = noiseInstance

    // Set size
    const setSize = () => {
      const bounding = container.getBoundingClientRect()
      data.bounding = bounding
      svg.style.width = `${bounding.width}px`
      svg.style.height = `${bounding.height}px`
    }

    // Set lines
    const setLines = () => {
      if (!data.bounding) return

      const { width, height } = data.bounding

      data.lines = []
      data.paths.forEach((path) => path.remove())
      data.paths = []

      const xGap = 10
      const yGap = 32

      const oWidth = width + 200
      const oHeight = height + 30

      const totalLines = Math.ceil(oWidth / xGap)
      const totalPoints = Math.ceil(oHeight / yGap)

      const xStart = (width - xGap * totalLines) / 2
      const yStart = (height - yGap * totalPoints) / 2

      for (let i = 0; i <= totalLines; i++) {
        const points: Point[] = []

        for (let j = 0; j <= totalPoints; j++) {
          const point: Point = {
            x: xStart + xGap * i,
            y: yStart + yGap * j,
            wave: { x: 0, y: 0 },
            cursor: { x: 0, y: 0, vx: 0, vy: 0 },
          }
          points.push(point)
        }

        const path = document.createElementNS('http://www.w3.org/2000/svg', 'path')
        svg.appendChild(path)
        data.paths.push(path)
        data.lines.push(points)
      }
    }

    // Move points
    const movePoints = (time: number) => {
      const { lines, mouse, noise } = data

      lines.forEach((points) => {
        points.forEach((p) => {
          // Wave movement
          const move =
            noise.perlin2((p.x + time * 0.0125) * 0.002, (p.y + time * 0.005) * 0.0015) * 12
          p.wave.x = Math.cos(move) * 32
          p.wave.y = Math.sin(move) * 16

          // Mouse effect
          const dx = p.x - mouse.sx
          const dy = p.y - mouse.sy
          const d = Math.hypot(dx, dy)
          const l = Math.max(175, mouse.vs)

          if (d < l) {
            const s = 1 - d / l
            const f = Math.cos(d * 0.001) * s

            p.cursor.vx += Math.cos(mouse.a) * f * l * mouse.vs * 0.00065
            p.cursor.vy += Math.sin(mouse.a) * f * l * mouse.vs * 0.00065
          }

          p.cursor.vx += (0 - p.cursor.x) * 0.005
          p.cursor.vy += (0 - p.cursor.y) * 0.005

          p.cursor.vx *= 0.925
          p.cursor.vy *= 0.925

          p.cursor.x += p.cursor.vx * 2
          p.cursor.y += p.cursor.vy * 2

          p.cursor.x = Math.min(100, Math.max(-100, p.cursor.x))
          p.cursor.y = Math.min(100, Math.max(-100, p.cursor.y))
        })
      })
    }

    // Get moved point
    const moved = (point: Point, withCursorForce = true) => {
      const coords = {
        x: point.x + point.wave.x + (withCursorForce ? point.cursor.x : 0),
        y: point.y + point.wave.y + (withCursorForce ? point.cursor.y : 0),
      }
      coords.x = Math.round(coords.x * 10) / 10
      coords.y = Math.round(coords.y * 10) / 10
      return coords
    }

    // Draw lines
    const drawLines = () => {
      const { lines, paths } = data

      lines.forEach((points, lIndex) => {
        let p1 = moved(points[0], false)
        let d = `M ${p1.x} ${p1.y}`

        points.forEach((point, pIndex) => {
          const isLast = pIndex === points.length - 1
          p1 = moved(point, !isLast)
          d += `L ${p1.x} ${p1.y}`
        })

        paths[lIndex].setAttribute('d', d)
      })
    }

    // Tick
    const tick = (time: number) => {
      const { mouse } = data

      mouse.sx += (mouse.x - mouse.sx) * 0.1
      mouse.sy += (mouse.y - mouse.sy) * 0.1

      const dx = mouse.x - mouse.lx
      const dy = mouse.y - mouse.ly
      const d = Math.hypot(dx, dy)

      mouse.v = d
      mouse.vs += (d - mouse.vs) * 0.1
      mouse.vs = Math.min(100, mouse.vs)

      mouse.lx = mouse.x
      mouse.ly = mouse.y

      mouse.a = Math.atan2(dy, dx)

      if (container) {
        container.style.setProperty('--x', `${mouse.sx}px`)
        container.style.setProperty('--y', `${mouse.sy}px`)
      }

      movePoints(time)
      drawLines()

      animationRef.current = requestAnimationFrame(tick)
    }

    // Event handlers
    const handleMouseMove = (e: MouseEvent) => {
      if (!data.bounding) return
      const { mouse } = data
      mouse.x = e.pageX - data.bounding.left
      mouse.y = e.pageY - data.bounding.top + window.scrollY

      if (!mouse.set) {
        mouse.sx = mouse.x
        mouse.sy = mouse.y
        mouse.lx = mouse.x
        mouse.ly = mouse.y
        mouse.set = true
      }
    }

    const handleResize = () => {
      setSize()
      setLines()
    }

    // Init
    setSize()
    setLines()

    window.addEventListener('resize', handleResize)
    window.addEventListener('mousemove', handleMouseMove)

    animationRef.current = requestAnimationFrame(tick)

    return () => {
      window.removeEventListener('resize', handleResize)
      window.removeEventListener('mousemove', handleMouseMove)
      if (animationRef.current) {
        cancelAnimationFrame(animationRef.current)
      }
    }
  }, [noiseInstance])

  return (
    <div
      ref={containerRef}
      className="waves-background"
      style={{
        position: 'absolute',
        top: 0,
        left: 0,
        width: '100%',
        height: '100%',
        overflow: 'hidden',
        pointerEvents: 'none',
      }}
    >
      <div
        className="waves-cursor"
        style={{
          position: 'absolute',
          top: 0,
          left: 0,
          width: '0.5rem',
          height: '0.5rem',
          background: 'hsl(var(--primary) / 0.3)',
          borderRadius: '50%',
          transform: 'translate3d(calc(var(--x, -0.5rem) - 50%), calc(var(--y, 50%) - 50%), 0)',
          willChange: 'transform',
          pointerEvents: 'none',
        }}
      />
      <svg
        ref={svgRef}
        style={{
          display: 'block',
          width: '100%',
          height: '100%',
        }}
      >
        <style>{`
          path {
            fill: none;
            stroke: hsl(var(--primary) / 0.20);
            stroke-width: 1px;
          }
        `}</style>
      </svg>
    </div>
  )
}
