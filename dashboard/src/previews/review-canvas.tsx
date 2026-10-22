import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import type { PointerEvent, ReactNode } from 'react'

type Camera = { x: number; y: number; scale: number }
type CanvasCommand = { action: 'fit' | 'in' | 'out' | 'reset'; revision: number }

/** 独立预览画布：相机移动不改变控件尺寸，鼠标所在的画布坐标在缩放时保持不动。 */
export function ReviewCanvas({ children, view, panning, onPanningChange, command, onZoomChange }: {
  children: ReactNode
  view: string
  panning: boolean
  onPanningChange: (panning: boolean) => void
  command: CanvasCommand
  onZoomChange: (zoom: number) => void
}) {
  const viewport = useRef<HTMLDivElement>(null)
  const content = useRef<HTMLDivElement>(null)
  const cameraRef = useRef<Camera>({ x: 24, y: 24, scale: 1 })
  const [camera, setCamera] = useState(cameraRef.current)
  const [space, setSpace] = useState(false)
  const [dragging, setDragging] = useState(false)
  const drag = useRef<{ pointer: number; x: number; y: number; camera: Camera } | null>(null)
  const hand = panning || space

  const moveCamera = useCallback((next: Camera) => {
    cameraRef.current = next
    setCamera(next)
    onZoomChange(Math.round(next.scale * 100))
  }, [onZoomChange])

  const zoomAt = useCallback((scale: number, x: number, y: number) => {
    const previous = cameraRef.current
    const nextScale = Math.max(0.15, Math.min(3, scale))
    const ratio = nextScale / previous.scale
    moveCamera({ x: x - (x - previous.x) * ratio, y: y - (y - previous.y) * ratio, scale: nextScale })
  }, [moveCamera])

  const fit = useCallback(() => {
    if (!viewport.current || !content.current) return
    const width = viewport.current.clientWidth
    const height = viewport.current.clientHeight
    const scale = Math.max(0.15, Math.min(1, (width - 48) / content.current.offsetWidth, (height - 64) / content.current.offsetHeight))
    moveCamera({ x: (width - content.current.offsetWidth * scale) / 2, y: 24, scale })
  }, [moveCamera])

  useLayoutEffect(() => {
    // 仅切换浏览内容时适应画布；主题字体或工具栏高度变化不能重置用户的相机。
    const frame = requestAnimationFrame(fit)
    return () => cancelAnimationFrame(frame)
  }, [view, fit])

  useEffect(() => {
    if (!viewport.current) return
    if (command.action === 'fit') { fit(); return }
    const scale = command.action === 'reset' ? 1 : cameraRef.current.scale * (command.action === 'in' ? 1.2 : 1 / 1.2)
    zoomAt(scale, viewport.current.clientWidth / 2, viewport.current.clientHeight / 2)
  }, [command, fit, zoomAt])

  useEffect(() => {
    const element = viewport.current
    if (!element) return
    const wheel = (event: WheelEvent) => {
      // 操作模式把普通滚轮交给列表和输入控件；Ctrl / 触控板捏合仍用于缩放画布。
      if (!hand && !event.ctrlKey && !event.metaKey) return
      event.preventDefault()
      const rect = element.getBoundingClientRect()
      const delta = event.deltaY * (event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? element.clientHeight : 1)
      zoomAt(cameraRef.current.scale * Math.exp(-Math.max(-100, Math.min(100, delta)) * 0.002), event.clientX - rect.left, event.clientY - rect.top)
    }
    element.addEventListener('wheel', wheel, { passive: false })
    return () => element.removeEventListener('wheel', wheel)
  }, [hand, zoomAt])

  useEffect(() => {
    const keyDown = (event: KeyboardEvent) => {
      if (event.target instanceof Element && event.target.closest('input, textarea, select, [contenteditable="true"], [role="slider"], [role="dialog"]')) return
      if (event.ctrlKey || event.metaKey || event.altKey) return
      if (event.code === 'Space') {
        // 保留聚焦按钮/开关的空格激活；空白画布上的空格才临时切换为抓手。
        if (event.target instanceof Element && event.target.closest('button, a, [role="switch"], [role="checkbox"], [role="radio"], [role="tab"]')) return
        event.preventDefault()
        setSpace(true)
      }
      if (event.key.toLowerCase() === 'h') onPanningChange(true)
      if (event.key.toLowerCase() === 'v') onPanningChange(false)
      if (event.key === '0') fit()
    }
    const keyUp = (event: KeyboardEvent) => { if (event.code === 'Space') setSpace(false) }
    const blur = () => { setSpace(false); drag.current = null; setDragging(false) }
    window.addEventListener('keydown', keyDown)
    window.addEventListener('keyup', keyUp)
    window.addEventListener('blur', blur)
    return () => {
      window.removeEventListener('keydown', keyDown)
      window.removeEventListener('keyup', keyUp)
      window.removeEventListener('blur', blur)
    }
  }, [fit, onPanningChange])

  const startDrag = (event: PointerEvent<HTMLDivElement>) => {
    if (event.button !== 1 && !(event.button === 0 && (hand || event.target === event.currentTarget))) return
    event.preventDefault()
    drag.current = { pointer: event.pointerId, x: event.clientX, y: event.clientY, camera: cameraRef.current }
    setDragging(true)
    event.currentTarget.setPointerCapture(event.pointerId)
  }
  const stopDrag = (event: PointerEvent<HTMLDivElement>) => {
    if (drag.current?.pointer !== event.pointerId) return
    drag.current = null
    setDragging(false)
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId)
  }

  return (
    <div ref={viewport} className="review-canvas" data-hand={hand} data-dragging={dragging}
      aria-label="预览画布" onPointerDown={startDrag} onPointerUp={stopDrag} onPointerCancel={stopDrag} onLostPointerCapture={stopDrag}
      onDoubleClick={event => { if (hand || event.target === event.currentTarget) fit() }}
      onPointerMove={event => {
        const current = drag.current
        if (current && current.pointer === event.pointerId) moveCamera({ ...current.camera, x: current.camera.x + event.clientX - current.x, y: current.camera.y + event.clientY - current.y })
      }}>
      <div ref={content} className={`review-artboard${view === 'all' ? ' review-artboard-all' : ''}`} inert={hand}
        style={{ transform: `translate(${camera.x}px, ${camera.y}px) scale(${camera.scale})` }}>
        {children}
      </div>
    </div>
  )
}
