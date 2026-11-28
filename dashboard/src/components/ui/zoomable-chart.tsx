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
 * - 动画由 motion/react 与统一动效运行时处理，不触发 React re-render
 * - Must NOT: 不在 handler 内使用 useState
 */

import { useEffect, useRef } from 'react'
import { usePinch } from '@use-gesture/react'
import { animate, motion, useMotionValue } from 'motion/react'

import { useMotion } from '@/lib/motion'
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
  const scaleValue = useMotionValue(1)
  const { enabled, transitions } = useMotion()

  useEffect(() => {
    return () => {
      scaleValue.stop()
    }
  }, [scaleValue])

  usePinch(
    ({ offset: [scale], first, last }) => {
      // Rubberband: 超出范围时有弹性阻力
      const clamped = Math.min(Math.max(scale, minScale * 0.85), maxScale * 1.15)
      const rubberband = clamped < minScale
        ? minScale + (clamped - minScale) * 0.3
        : clamped > maxScale
          ? maxScale + (clamped - maxScale) * 0.3
          : clamped

      // 松手后弹回范围内
      if (last && (scale < minScale || scale > maxScale)) {
        const boundedScale = Math.min(Math.max(scale, minScale), maxScale)
        if (!enabled) {
          scaleValue.jump(boundedScale)
        } else {
          animate(scaleValue, boundedScale, transitions.gentle)
        }
        return
      }

      if (first || !enabled) {
        scaleValue.jump(rubberband)
      } else {
        animate(scaleValue, rubberband, transitions.spring)
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
      <motion.div style={{ scale: scaleValue }} className="w-full h-full origin-center">
        {children}
      </motion.div>
    </div>
  )
}
