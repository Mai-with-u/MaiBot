import {
  useCallback,
  useLayoutEffect,
  useRef,
  useState,
  type PointerEvent,
  type ReactNode,
} from 'react'
import { Hand, Maximize, Minus, MousePointer2, Plus, RotateCcw } from 'lucide-react'

import { Button } from '@/components/ui/button'

type Camera = { x: number; y: number; scale: number }
const MIN_SCALE = 0.01
const MAX_SCALE = 3

/** client 坐标属于屏幕空间；平移量属于 CSS 布局空间，需消除祖先缩放与边框。 */
function canvasPoint(area: HTMLDivElement, clientX: number, clientY: number) {
  const rect = area.getBoundingClientRect()
  return {
    x: ((clientX - rect.left) * area.offsetWidth) / rect.width - area.clientLeft,
    y: ((clientY - rect.top) * area.offsetHeight) / rect.height - area.clientTop,
  }
}

export function ComponentGalleryCanvas({
  children,
  toolbar,
  resetKey,
}: {
  children: ReactNode
  toolbar?: ReactNode
  resetKey?: string
}) {
  const viewport = useRef<HTMLDivElement>(null)
  const content = useRef<HTMLDivElement>(null)
  const camera = useRef<Camera>({ x: 0, y: 0, scale: 1 })
  const [view, setView] = useState<Camera>({ x: 0, y: 0, scale: 1 })
  const fitting = useRef(false)
  const drag = useRef<{ id: number; x: number; y: number; origin: Camera } | null>(null)
  const moved = useRef(false)
  const [handMode, setHandMode] = useState(false)
  const [dragging, setDragging] = useState(false)

  const applyCamera = useCallback((next: Camera) => {
    camera.current = next
    setView(next)
  }, [])

  // 筛选后仅复位视图，不重挂工具栏，保证搜索框连续输入时保留焦点。
  useLayoutEffect(() => {
    const frame = requestAnimationFrame(() => {
      fitting.current = false
      applyCamera({ x: 0, y: 0, scale: 1 })
    })
    return () => cancelAnimationFrame(frame)
  }, [resetKey, applyCamera])

  const fitAll = useCallback(() => {
    const area = viewport.current
    const board = content.current
    if (!area || !board || !board.offsetWidth || !board.offsetHeight) return
    const scale = Math.min(
      (area.clientWidth - 32) / board.offsetWidth,
      (area.clientHeight - 32) / board.offsetHeight,
      1
    )
    if (scale <= 0) return
    fitting.current = true
    applyCamera({
      x: (area.clientWidth - board.offsetWidth * scale) / 2,
      y: (area.clientHeight - board.offsetHeight * scale) / 2,
      scale,
    })
  }, [applyCamera])

  // 锚点始终对应同一个画布坐标，缩放时鼠标下方的组件不会漂移。
  const zoomAt = useCallback(
    (factor: number, x: number, y: number) => {
      const current = camera.current
      const scale = Math.min(MAX_SCALE, Math.max(MIN_SCALE, current.scale * factor))
      fitting.current = false
      applyCamera({
        x: x - ((x - current.x) * scale) / current.scale,
        y: y - ((y - current.y) * scale) / current.scale,
        scale,
      })
    },
    [applyCamera]
  )

  function zoomCenter(factor: number) {
    const area = viewport.current
    if (area) zoomAt(factor, area.clientWidth / 2, area.clientHeight / 2)
  }

  useLayoutEffect(() => {
    const area = viewport.current!
    const board = content.current!
    // 原生非 passive 监听器才能阻止滚轮滚动页面或触发浏览器缩放。
    const wheel = (event: WheelEvent) => {
      event.preventDefault()
      event.stopPropagation()
      if (drag.current) return
      const point = canvasPoint(area, event.clientX, event.clientY)
      const delta = event.deltaY || event.deltaX
      const pixels =
        delta * (event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? area.clientHeight : 1)
      zoomAt(Math.exp(-Math.max(-200, Math.min(200, pixels)) * 0.002), point.x, point.y)
    }
    area.addEventListener('wheel', wheel, { passive: false, capture: true })
    const observer = new ResizeObserver(() => {
      if (fitting.current) fitAll()
    })
    observer.observe(area)
    observer.observe(board)
    return () => {
      area.removeEventListener('wheel', wheel, { capture: true })
      observer.disconnect()
    }
  }, [fitAll, zoomAt])

  function startDrag(event: PointerEvent<HTMLDivElement>) {
    // Portal 中的弹层不属于画布，不拦截其事件。
    if (!(event.target instanceof Element) || !event.currentTarget.contains(event.target)) return
    if (event.button !== 0 && event.button !== 1) return
    moved.current = false
    if (!handMode && event.button !== 1 && event.target.closest('[data-gallery-item]')) return
    event.preventDefault()
    event.stopPropagation()
    event.currentTarget.focus({ preventScroll: true })
    event.currentTarget.setPointerCapture(event.pointerId)
    fitting.current = false
    const point = canvasPoint(event.currentTarget, event.clientX, event.clientY)
    drag.current = {
      id: event.pointerId,
      x: point.x,
      y: point.y,
      origin: camera.current,
    }
    setDragging(true)
  }

  function moveDrag(event: PointerEvent<HTMLDivElement>) {
    const start = drag.current
    if (!start || start.id !== event.pointerId) return
    const point = canvasPoint(event.currentTarget, event.clientX, event.clientY)
    const dx = point.x - start.x
    const dy = point.y - start.y
    if (Math.abs(dx) + Math.abs(dy) > 3) moved.current = true
    applyCamera({ ...start.origin, x: start.origin.x + dx, y: start.origin.y + dy })
  }

  function endDrag(event: PointerEvent<HTMLDivElement>) {
    if (drag.current?.id !== event.pointerId) return
    drag.current = null
    if (event.currentTarget.hasPointerCapture(event.pointerId))
      event.currentTarget.releasePointerCapture(event.pointerId)
    setDragging(false)
  }

  return (
    <div className="flex h-full min-h-0 flex-1 flex-col gap-2">
      <div className="flex shrink-0 flex-nowrap items-center gap-1.5 overflow-x-auto pb-1 whitespace-nowrap [&>*]:shrink-0">
        {toolbar}
        <Button
          variant={handMode ? 'outline' : 'default'}
          size="sm"
          aria-pressed={!handMode}
          onClick={() => setHandMode(false)}
        >
          <MousePointer2 className="mr-1 h-4 w-4" />
          交互
        </Button>
        <Button
          variant={handMode ? 'default' : 'outline'}
          size="sm"
          aria-pressed={handMode}
          onClick={() => setHandMode(true)}
        >
          <Hand className="mr-1 h-4 w-4" />
          平移
        </Button>
        <Button
          variant="outline"
          size="icon-sm"
          aria-label="缩小画布"
          onClick={() => zoomCenter(1 / 1.2)}
        >
          <Minus className="h-4 w-4" />
        </Button>
        <output aria-label="画布缩放比例" className="w-14 text-center text-xs tabular-nums">
          {Math.round(view.scale * 100)}%
        </output>
        <Button
          variant="outline"
          size="icon-sm"
          aria-label="放大画布"
          onClick={() => zoomCenter(1.2)}
        >
          <Plus className="h-4 w-4" />
        </Button>
        <Button variant="outline" size="sm" onClick={fitAll}>
          <Maximize className="mr-1 h-4 w-4" />
          显示全部
        </Button>
        <Button
          variant="outline"
          size="sm"
          onClick={() => {
            fitting.current = false
            applyCamera({ x: 0, y: 0, scale: 1 })
          }}
        >
          <RotateCcw className="mr-1 h-4 w-4" />
          重置视图
        </Button>
        <p id="gallery-canvas-help" className="sr-only">
          滚轮缩放 · 拖动空白处 / 鼠标中键平移 · 聚焦画布后方向键平移，+ / − 缩放，Home 显示全部
        </p>
      </div>
      {/* eslint-disable jsx-a11y/no-noninteractive-tabindex, jsx-a11y/no-noninteractive-element-interactions -- 画布保留 region 语义，提供键盘焦点、方向键和缩放快捷键。 */}
      <div
        ref={viewport}
        role="region"
        aria-label="公共组件画布"
        aria-describedby="gallery-canvas-help"
        tabIndex={0}
        className="bg-background border-border focus-visible:ring-ring relative min-h-0 w-full flex-1 overflow-clip border focus-visible:ring-2 focus-visible:outline-none"
        style={{
          cursor: dragging ? 'grabbing' : handMode ? 'grab' : 'auto',
          touchAction: handMode ? 'none' : 'auto',
          userSelect: dragging || handMode ? 'none' : undefined,
        }}
        onPointerDownCapture={startDrag}
        onPointerMove={moveDrag}
        onPointerUp={endDrag}
        onPointerCancel={endDrag}
        onLostPointerCapture={() => {
          drag.current = null
          setDragging(false)
        }}
        onKeyDown={(event) => {
          if (event.target !== event.currentTarget) return
          const directions: Record<string, [number, number]> = {
            ArrowLeft: [80, 0],
            ArrowRight: [-80, 0],
            ArrowUp: [0, 80],
            ArrowDown: [0, -80],
          }
          if (directions[event.key]) {
            event.preventDefault()
            fitting.current = false
            const [x, y] = directions[event.key]
            applyCamera({ ...camera.current, x: camera.current.x + x, y: camera.current.y + y })
          } else if (['+', '=', '-'].includes(event.key)) {
            event.preventDefault()
            zoomCenter(event.key === '-' ? 1 / 1.2 : 1.2)
          } else if (event.key === 'Home') {
            event.preventDefault()
            fitAll()
          }
        }}
        onClickCapture={(event) => {
          if (
            event.target instanceof Node &&
            event.currentTarget.contains(event.target) &&
            ((moved.current && event.detail !== 0) || handMode)
          ) {
            event.preventDefault()
            event.stopPropagation()
            moved.current = false
          }
        }}
        onAuxClick={(event) => {
          if (event.button === 1) event.preventDefault()
        }}
      >
        <div
          ref={content}
          data-gallery-content
          className="absolute top-0 left-0 flex w-max flex-col items-start gap-6 p-8"
          style={{
            transformOrigin: '0 0',
            transform: `translate(${view.x}px, ${view.y}px) scale(${view.scale})`,
          }}
        >
          {children}
        </div>
      </div>
      {/* eslint-enable jsx-a11y/no-noninteractive-tabindex, jsx-a11y/no-noninteractive-element-interactions */}
    </div>
  )
}
