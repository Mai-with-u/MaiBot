import { Link } from '@tanstack/react-router'
import { AnimatePresence, motion, useMotionValue, useSpring, useTransform } from 'motion/react'
import { useEffect, useRef, useState } from 'react'
import type { LucideIcon } from 'lucide-react'
import type { MouseEvent as ReactMouseEvent, PointerEvent as ReactPointerEvent } from 'react'

import { useMotion } from '@/lib/motion'
import { cn } from '@/lib/utils'
import { MOBILE_GLASS_CLASS } from './mobile-glass'

export interface LiquidGlassTabItem {
  id: string
  to: string
  label: string
  icon: LucideIcon
}

export interface LiquidGlassTabRailProps {
  items: LiquidGlassTabItem[]
  activeId: string
  collapsed: boolean
  onExpand: () => void
  onNavigate: (to: string) => void
}

const RAIL_PADDING = 4
const DRAG_ACTIVATION_PX = 4
const OVERDRAG_LIMIT = 0.24
const MAX_JELLY_STRETCH = 0.18
const JELLY_ASPECT = 1.35

/** FlClash prism_ui 同款橡皮筋阻尼：拖过两端时产生温和的回弹阻力 */
function rubberBand(overshoot: number, limit: number): number {
  const pull = 1 - 1 / ((Math.abs(overshoot) * 0.55) / limit + 1)
  return limit * pull * Math.sign(overshoot)
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max)
}

interface DragSession {
  pointerId: number
  startX: number
  lastX: number
  lastTime: number
  moved: boolean
  innerLeft: number
  innerWidth: number
}

/**
 * 现代主题专属的液态玻璃底部标签栏（参考 FlClash prism_ui NavigationDock）：
 * - 静息态：柔和底板贴合玻璃胶囊内部；
 * - 悬停态：指针轻滑时出现柔和的磁吸高光；
 * - 按压 / 横向滑动（Slide-to-Pick）：指示器抬升膨胀为清澈的放大液态玻璃透镜，
 *   带速度驱动的果冻拉伸、边界橡皮筋阻力与内部按压柔光，松手后弹簧回落并切换页面。
 */
export function LiquidGlassTabRail({
  items,
  activeId,
  collapsed,
  onExpand,
  onNavigate,
}: LiquidGlassTabRailProps) {
  const { enabled: motionEnabled, transitions } = useMotion()
  const dockTransition = transitions.snappy
  const count = Math.max(items.length, 1)
  const rawActiveIndex = items.findIndex((item) => item.id === activeId)
  const activeIndex = rawActiveIndex >= 0 ? rawActiveIndex : 0
  const hasActive = rawActiveIndex >= 0

  const railRef = useRef<HTMLDivElement>(null)
  const dragRef = useRef<DragSession | null>(null)
  const suppressClickRef = useRef(false)
  const jellyResetTimerRef = useRef<number | null>(null)

  const [lifted, setLifted] = useState(false)
  const [previewIndex, setPreviewIndex] = useState<number | null>(null)
  const [hoverIndex, setHoverIndex] = useState<number | null>(null)
  const [glow, setGlow] = useState<{ x: number; visible: boolean }>({ x: 50, visible: false })

  const targetPosition = useMotionValue(activeIndex)
  const springPosition = useSpring(targetPosition, {
    stiffness: motionEnabled ? 460 : 4000,
    damping: motionEnabled ? 34 : 200,
    mass: 0.75,
  })
  const rawJelly = useMotionValue(0)
  const springJelly = useSpring(rawJelly, {
    stiffness: motionEnabled ? 320 : 4000,
    damping: motionEnabled ? 28 : 200,
    mass: 0.85,
  })

  const lensXPercent = useTransform(springPosition, (position) => `${position * 100}%`)
  const lensScaleX = useTransform(springJelly, (stretch) => 1 + stretch)
  const lensScaleY = useTransform(springJelly, (stretch) => 1 - stretch * JELLY_ASPECT)

  useEffect(() => {
    if (!dragRef.current) {
      targetPosition.set(activeIndex)
    }
  }, [activeIndex, targetPosition])

  useEffect(() => {
    return () => {
      if (jellyResetTimerRef.current !== null) {
        window.clearTimeout(jellyResetTimerRef.current)
      }
    }
  }, [])

  const scheduleJellyRest = () => {
    if (jellyResetTimerRef.current !== null) {
      window.clearTimeout(jellyResetTimerRef.current)
    }
    jellyResetTimerRef.current = window.setTimeout(() => {
      rawJelly.set(0)
      jellyResetTimerRef.current = null
    }, 65)
  }

  const updateGlowFromClientX = (clientX: number, rect: DOMRect) => {
    if (rect.width <= 0) return
    const percent = clamp(((clientX - rect.left) / rect.width) * 100, 0, 100)
    setGlow({ x: percent, visible: true })
  }

  const positionFromClientX = (clientX: number, innerLeft: number, innerWidth: number) => {
    const slotWidth = innerWidth / count
    if (slotWidth <= 0) return { banded: activeIndex, snapped: activeIndex }
    const raw = (clientX - innerLeft) / slotWidth - 0.5
    const maxIndex = count - 1
    let banded = raw
    if (raw < 0) {
      banded = rubberBand(raw, OVERDRAG_LIMIT)
    } else if (raw > maxIndex) {
      banded = maxIndex + rubberBand(raw - maxIndex, OVERDRAG_LIMIT)
    }
    const snapped = clamp(Math.round(raw), 0, maxIndex)
    return { banded, snapped }
  }

  const handlePointerDown = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (collapsed || event.button !== 0) return
    const rail = railRef.current
    if (!rail) return
    const rect = rail.getBoundingClientRect()
    const innerLeft = rect.left + RAIL_PADDING
    const innerWidth = Math.max(rect.width - RAIL_PADDING * 2, 1)
    const { banded, snapped } = positionFromClientX(event.clientX, innerLeft, innerWidth)

    dragRef.current = {
      pointerId: event.pointerId,
      startX: event.clientX,
      lastX: event.clientX,
      lastTime: performance.now(),
      moved: false,
      innerLeft,
      innerWidth,
    }
    try {
      event.currentTarget.setPointerCapture(event.pointerId)
    } catch {
      // 某些合成事件环境不支持 pointer capture，退回正常冒泡处理。
    }

    setLifted(true)
    setHoverIndex(null)
    setPreviewIndex(snapped)
    targetPosition.set(snapped !== activeIndex ? snapped : banded)
    updateGlowFromClientX(event.clientX, rect)
  }

  const handlePointerMove = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (collapsed) return
    const rail = railRef.current
    if (!rail) return

    const drag = dragRef.current
    if (drag && drag.pointerId === event.pointerId) {
      const now = performance.now()
      const dx = event.clientX - drag.lastX
      const dt = Math.max(8, now - drag.lastTime)
      if (Math.abs(event.clientX - drag.startX) >= DRAG_ACTIVATION_PX) {
        drag.moved = true
      }
      drag.lastX = event.clientX
      drag.lastTime = now

      const { banded, snapped } = positionFromClientX(event.clientX, drag.innerLeft, drag.innerWidth)
      targetPosition.set(banded)
      setPreviewIndex(snapped)

      if (motionEnabled) {
        const velocity = Math.abs(dx / dt)
        const stretch = clamp(velocity * 0.085, 0, MAX_JELLY_STRETCH)
        rawJelly.set(stretch)
        scheduleJellyRest()
      }

      updateGlowFromClientX(event.clientX, rail.getBoundingClientRect())
      return
    }

    if (event.pointerType === 'mouse') {
      const rect = rail.getBoundingClientRect()
      const innerLeft = rect.left + RAIL_PADDING
      const innerWidth = Math.max(rect.width - RAIL_PADDING * 2, 1)
      const { snapped } = positionFromClientX(event.clientX, innerLeft, innerWidth)
      setHoverIndex(snapped)
    }
  }

  const finishDrag = (event: ReactPointerEvent<HTMLDivElement>, cancelled: boolean) => {
    const drag = dragRef.current
    if (!drag || drag.pointerId !== event.pointerId) return
    dragRef.current = null

    if (event.currentTarget.hasPointerCapture?.(event.pointerId)) {
      event.currentTarget.releasePointerCapture?.(event.pointerId)
    }

    const { snapped } = positionFromClientX(event.clientX, drag.innerLeft, drag.innerWidth)
    const targetIndex = cancelled ? activeIndex : snapped
    const targetItem = items[targetIndex]

    setLifted(false)
    setPreviewIndex(null)
    setGlow((current) => ({ ...current, visible: false }))
    rawJelly.set(0)
    targetPosition.set(targetIndex)

    if (!cancelled && targetItem) {
      if (targetItem.id !== activeId || drag.moved) {
        suppressClickRef.current = true
        window.setTimeout(() => {
          suppressClickRef.current = false
        }, 0)
      }
      if (targetItem.id !== activeId) {
        onNavigate(targetItem.to)
      }
    }
  }

  const handleLinkClick = (event: ReactMouseEvent<HTMLAnchorElement>) => {
    if (collapsed || suppressClickRef.current) {
      event.preventDefault()
    }
  }

  const effectiveIndex = lifted && previewIndex !== null ? previewIndex : activeIndex
  const showHoverPill = !collapsed && !lifted && hoverIndex !== null && hoverIndex !== activeIndex

  return (
    <motion.div
      ref={railRef}
      layout
      layoutDependency={collapsed}
      transition={dockTransition}
      data-mobile-tabbar-tabs="true"
      data-liquid-dock="true"
      data-lifted={lifted ? 'true' : 'false'}
      onClick={collapsed ? onExpand : undefined}
      onPointerDown={handlePointerDown}
      onPointerMove={handlePointerMove}
      onPointerUp={(event) => finishDrag(event, false)}
      onPointerCancel={(event) => finishDrag(event, true)}
      onPointerLeave={() => {
        if (!dragRef.current) setHoverIndex(null)
      }}
      className={cn(
        'relative flex touch-none rounded-full p-1 select-none',
        collapsed ? 'h-[52px] w-[52px] flex-none' : 'h-[62px] flex-1 overflow-visible',
        MOBILE_GLASS_CLASS
      )}
    >
      {/* 内部按压柔光（LiquidGlassGlow）：裁切在胶囊轮廓内 */}
      <span
        aria-hidden="true"
        data-liquid-dock-glow="true"
        className="pointer-events-none absolute inset-0 z-0 overflow-hidden rounded-full"
      >
        <span
          className="block h-full w-full transition-opacity duration-300"
          style={{
            opacity: glow.visible && !collapsed ? 1 : 0,
            background: `radial-gradient(128px circle at ${glow.x}% 50%, var(--m-glass-press-glow), transparent 72%)`,
          }}
        />
      </span>

      {/* 指针悬停磁吸高光（_HoverHighlight） */}
      {showHoverPill && (
        <span className="pointer-events-none absolute inset-1 z-[1]" aria-hidden="true">
          <motion.span
            data-liquid-dock-hover="true"
            initial={false}
            animate={{ x: `${hoverIndex * 100}%`, opacity: 1 }}
            transition={motionEnabled ? transitions.snappy : { duration: 0 }}
            className="bg-foreground/[0.06] dark:bg-white/[0.08] block h-full rounded-full"
            style={{ width: `${100 / count}%` }}
          />
        </span>
      )}

      {/* 静息底板 / 按下拖拽时的悬浮放大液态玻璃透镜（_Lens + _GlassLens） */}
      {hasActive && (
        <span
          className="pointer-events-none absolute inset-1 z-[3] overflow-visible"
          aria-hidden="true"
        >
          <motion.span
            data-mobile-tab-indicator="true"
            data-liquid-lens-lifted={lifted && !collapsed ? 'true' : 'false'}
            className="relative block h-full overflow-visible"
            style={{
              width: collapsed ? '100%' : `${100 / count}%`,
              x: collapsed ? '0%' : lensXPercent,
              scaleX: collapsed ? 1 : lensScaleX,
              scaleY: collapsed ? 1 : lensScaleY,
            }}
          >
            <motion.span
              initial={false}
              animate={
                lifted && !collapsed
                  ? { top: -5, bottom: -5, left: -6, right: -6 }
                  : { top: 0, bottom: 0, left: 0, right: 0 }
              }
              transition={motionEnabled ? transitions.snappy : { duration: 0 }}
              className={cn(
                'absolute rounded-full transition-[background,box-shadow] duration-200',
                lifted && !collapsed ? 'm-glass-lens' : 'm-dock-platter'
              )}
            />
          </motion.span>
        </span>
      )}

      <AnimatePresence initial={false} mode="popLayout">
        {items
          .filter((item) => !collapsed || item.id === activeId)
          .map((item) => {
            const index = items.findIndex((candidate) => candidate.id === item.id)
            const active = item.id === activeId
            const highlighted = !collapsed ? effectiveIndex === index : active
            const magnified = lifted && !collapsed && effectiveIndex === index
            const Icon = item.icon

            return (
              <motion.div
                key={item.id}
                layout
                layoutDependency={collapsed}
                transition={dockTransition}
                initial={motionEnabled ? { opacity: 0, scale: 0.8 } : false}
                animate={{ opacity: 1, scale: 1 }}
                exit={motionEnabled ? { opacity: 0, scale: 0.8 } : undefined}
                className="relative z-10 flex min-w-0 flex-1"
              >
                <Link
                  to={item.to}
                  data-mobile-tab="true"
                  data-active={highlighted ? 'true' : 'false'}
                  aria-current={active ? 'page' : undefined}
                  aria-label={collapsed ? item.label : undefined}
                  onClick={handleLinkClick}
                  draggable={false}
                  className={cn(
                    'relative flex min-w-0 flex-1 flex-col items-center justify-center gap-0.5 rounded-full text-[10px] font-semibold transition-[color,transform] [transition-duration:var(--motion-duration-fast)]',
                    highlighted ? 'text-primary' : 'text-foreground'
                  )}
                  style={
                    motionEnabled
                      ? { transform: magnified ? 'scale(1.08)' : 'scale(1)' }
                      : undefined
                  }
                >
                  <span className="relative flex">
                    <Icon
                      className="h-[22px] w-[22px]"
                      strokeWidth={highlighted ? 2 : 1.7}
                      aria-hidden="true"
                    />
                  </span>
                  {!collapsed && <span className="relative truncate">{item.label}</span>}
                </Link>
              </motion.div>
            )
          })}
      </AnimatePresence>
    </motion.div>
  )
}
