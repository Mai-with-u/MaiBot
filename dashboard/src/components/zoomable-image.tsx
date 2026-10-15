import { useCallback, useEffect, useRef, useState } from 'react'

import { cn } from '@/lib/utils'

const MIN_SCALE = 1
const MAX_SCALE = 8
/** 双击放大到的倍数 */
const DOUBLE_CLICK_SCALE = 2.5
/** 滚轮每滚动一个像素对应的缩放指数，越大缩放越快 */
const WHEEL_ZOOM_SPEED = 0.0015

interface Transform {
  scale: number
  x: number
  y: number
}

interface Point {
  x: number
  y: number
}

const IDENTITY: Transform = { scale: 1, x: 0, y: 0 }

interface ZoomableImageProps {
  src: string
  alt: string
  /** 作用于图片本身，用来限定未缩放时的显示尺寸 */
  className?: string
  /** 作用于裁剪容器 */
  containerClassName?: string
}

/**
 * 可缩放、可平移的图片：滚轮或双指缩放（以指针位置为中心），放大后拖动平移，双击在原始大小与放大之间切换。
 * 变换以容器中心为原点，位移先于缩放应用。
 */
export function ZoomableImage({ src, alt, className, containerClassName }: ZoomableImageProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const imageRef = useRef<HTMLImageElement>(null)
  const [transform, setTransform] = useState<Transform>(IDENTITY)
  const [dragging, setDragging] = useState(false)
  /** 当前按下的指针位置（相对视口），一个指针平移、两个指针缩放 */
  const pointersRef = useRef(new Map<number, Point>())

  /** 限制位移：放大后的图片边缘不能离开容器边缘，没放大到超出容器的方向保持居中。 */
  const clamp = useCallback((next: Transform): Transform => {
    const container = containerRef.current
    const image = imageRef.current
    if (!container || !image || next.scale <= MIN_SCALE) return IDENTITY
    const maxX = Math.max(0, (image.offsetWidth * next.scale - container.clientWidth) / 2)
    const maxY = Math.max(0, (image.offsetHeight * next.scale - container.clientHeight) / 2)
    return {
      scale: next.scale,
      x: Math.min(maxX, Math.max(-maxX, next.x)),
      y: Math.min(maxY, Math.max(-maxY, next.y)),
    }
  }, [])

  /** 缩放到指定倍数，并保持视口坐标 point 下的图片内容不动。 */
  const zoomAt = useCallback(
    (point: Point, resolveScale: (current: number) => number) => {
      const container = containerRef.current
      if (!container) return
      const rect = container.getBoundingClientRect()
      // 指针相对容器中心的偏移
      const offsetX = point.x - (rect.left + rect.width / 2)
      const offsetY = point.y - (rect.top + rect.height / 2)
      setTransform((current) => {
        const scale = Math.min(MAX_SCALE, Math.max(MIN_SCALE, resolveScale(current.scale)))
        const ratio = scale / current.scale
        return clamp({
          scale,
          x: offsetX - ratio * (offsetX - current.x),
          y: offsetY - ratio * (offsetY - current.y),
        })
      })
    },
    [clamp]
  )

  // React 的 onWheel 是被动监听，无法阻止默认滚动，这里手动注册。
  useEffect(() => {
    const container = containerRef.current
    if (!container) return
    const handleWheel = (event: WheelEvent) => {
      event.preventDefault()
      // 按行滚动的设备 deltaY 以行为单位，折算成像素
      const delta = event.deltaMode === WheelEvent.DOM_DELTA_LINE ? event.deltaY * 16 : event.deltaY
      zoomAt({ x: event.clientX, y: event.clientY }, (scale) =>
        scale * Math.exp(-delta * WHEEL_ZOOM_SPEED)
      )
    }
    container.addEventListener('wheel', handleWheel, { passive: false })
    return () => container.removeEventListener('wheel', handleWheel)
  }, [zoomAt])

  const handlePointerDown = (event: React.PointerEvent<HTMLDivElement>) => {
    if (event.pointerType === 'mouse' && event.button !== 0) return
    event.currentTarget.setPointerCapture(event.pointerId)
    pointersRef.current.set(event.pointerId, { x: event.clientX, y: event.clientY })
    setDragging(true)
  }

  const handlePointerMove = (event: React.PointerEvent<HTMLDivElement>) => {
    const pointers = pointersRef.current
    const previous = pointers.get(event.pointerId)
    if (!previous) return
    const current = { x: event.clientX, y: event.clientY }

    if (pointers.size === 2) {
      // 双指：按两指间距的变化缩放，中心取两指中点
      const other = Array.from(pointers.entries()).find(([id]) => id !== event.pointerId)![1]
      const previousDistance = Math.hypot(previous.x - other.x, previous.y - other.y)
      const currentDistance = Math.hypot(current.x - other.x, current.y - other.y)
      if (previousDistance > 0) {
        const ratio = currentDistance / previousDistance
        zoomAt({ x: (current.x + other.x) / 2, y: (current.y + other.y) / 2 }, (scale) => scale * ratio)
      }
    } else if (pointers.size === 1) {
      setTransform((value) =>
        clamp({ ...value, x: value.x + current.x - previous.x, y: value.y + current.y - previous.y })
      )
    }
    pointers.set(event.pointerId, current)
  }

  const handlePointerEnd = (event: React.PointerEvent<HTMLDivElement>) => {
    pointersRef.current.delete(event.pointerId)
    if (pointersRef.current.size === 0) setDragging(false)
  }

  const zoomed = transform.scale > MIN_SCALE

  return (
    <div
      ref={containerRef}
      className={cn(
        'relative flex touch-none items-center justify-center overflow-hidden select-none',
        zoomed ? (dragging ? 'cursor-grabbing' : 'cursor-grab') : 'cursor-zoom-in',
        containerClassName
      )}
      onPointerDown={handlePointerDown}
      onPointerMove={handlePointerMove}
      onPointerUp={handlePointerEnd}
      onPointerCancel={handlePointerEnd}
      onDoubleClick={(event) =>
        zoomAt({ x: event.clientX, y: event.clientY }, (scale) =>
          scale > MIN_SCALE ? MIN_SCALE : DOUBLE_CLICK_SCALE
        )
      }
    >
      <img
        ref={imageRef}
        src={src}
        alt={alt}
        draggable={false}
        className={cn('will-change-transform', !dragging && 'transition-transform duration-75', className)}
        style={{
          transform: `translate(${transform.x}px, ${transform.y}px) scale(${transform.scale})`,
        }}
      />
      {zoomed && (
        <span className="pointer-events-none absolute right-2 bottom-2 rounded bg-black/60 px-1.5 py-0.5 text-xs text-white tabular-nums">
          {Math.round(transform.scale * 100)}%
        </span>
      )}
    </div>
  )
}
