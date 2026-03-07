/**
 * ZoomableChart — 支持 pinch-to-zoom 的图表容器（Task 8）
 *
 * 用法：
 *   <ZoomableChart aria-label="每小时请求量趋势">
 *     <ChartContainer ...>
 *       <LineChart ...>...</LineChart>
 *     </ChartContainer>
 *   </ZoomableChart>
 *
 * 特性：
 * - 支持 macOS 触控板双指缩放（wheel + ctrlKey）
 * - 支持移动端/触屏双指 pinch-to-zoom
 * - 缩放范围 0.5x – 4x，带 rubberband 效果
 * - 动画由 @react-spring/web 处理，不触发 React re-render
 * - Must NOT: 不在 handler 内使用 useState
 */

import { useRef } from 'react'
import { animated, useSpring } from '@react-spring/web'

const AnimatedDiv = animated('div')
import { usePinch } from '@use-gesture/react'

import { cn } from '@/lib/utils'

interface ZoomableChartProps {
  children: React.ReactNode
  className?: string
  'aria-label': string
  minScale?: number
  maxScale?: number
}

export function ZoomableChart({
  children,
  className,
  'aria-label': ariaLabel,
  minScale = 0.5,
  maxScale = 4,
}: ZoomableChartProps) {
  const containerRef = useRef<HTMLDivElement>(null)

  const [style, api] = useSpring(() => ({
    scale: 1,
    config: { tension: 300, friction: 40 },
  }))

  usePinch(
    ({ offset: [scale], first, last }) => {
      // Rubberband: 超出范围时有弹性阻力
      const clamped = Math.min(Math.max(scale, minScale * 0.85), maxScale * 1.15)
      const rubberband = clamped < minScale
        ? minScale + (clamped - minScale) * 0.3
        : clamped > maxScale
          ? maxScale + (clamped - maxScale) * 0.3
          : clamped

      api.start({ scale: rubberband, immediate: first })

      // 松手后弹回范围内
      if (last && (scale < minScale || scale > maxScale)) {
        api.start({
          scale: Math.min(Math.max(scale, minScale), maxScale),
          config: { tension: 200, friction: 30 },
        })
      }
    },
    {
      target: containerRef,
      scaleBounds: { min: minScale * 0.85, max: maxScale * 1.15 },
      rubberband: true,
      // 阻止浏览器默认的页面缩放
      preventDefault: true,
      eventOptions: { passive: false },
    }
  )

  return (
    <div
      ref={containerRef}
      role="img"
      aria-label={ariaLabel}
      className={cn('overflow-hidden touch-none select-none', className)}
      style={{ touchAction: 'none' }}
    >
      <AnimatedDiv style={style} className="w-full h-full origin-center">
        {children}
      </AnimatedDiv>
    </div>
  )
}
