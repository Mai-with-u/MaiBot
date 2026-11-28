import {
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type ReactNode,
  type RefObject,
} from 'react'
import { createPortal } from 'react-dom'
import { AnimatePresence, motion, useMotionValue, useSpring } from 'motion/react'

import { useMotion } from '@/lib/motion'
import { cn } from '@/lib/utils'

interface IndicatorRect {
  x: number
  y: number
  width: number
  height: number
}

function measureRelativeRect(container: HTMLElement, target: HTMLElement): IndicatorRect {
  const containerRect = container.getBoundingClientRect()
  const targetRect = target.getBoundingClientRect()
  return {
    x: targetRect.left - containerRect.left + container.scrollLeft,
    y: targetRect.top - containerRect.top + container.scrollTop,
    width: targetRect.width,
    height: targetRect.height,
  }
}

export interface ModernTabIndicatorProps {
  listRef: RefObject<HTMLElement | null>
  /** 下划线模式（用于设置页底边栏）或药丸模式（用于常规 TabsList） */
  variant?: 'auto' | 'pill' | 'underline'
}

interface TabIndicatorState {
  rect: IndicatorRect | null
  mode: 'pill' | 'underline'
  hasMoved: boolean
}

/**
 * 现代风格专属的弹簧滑行 Tab 指示器（灵感来自 DesignBookmark Tab pill / Smooth tab）：
 * 监听 Radix TabsList 内当前激活的 `[data-state='active']` 触发器并平滑跟随；
 * 首次挂载直接落位不闪烁（initial={false}），千禧/未来复古风格下不渲染。
 */
export function ModernTabIndicator({ listRef, variant = 'auto' }: ModernTabIndicatorProps) {
  const { style, enabled, transitions } = useMotion()
  const active = style === 'modern' && enabled
  const [indicator, setIndicator] = useState<TabIndicatorState>({
    rect: null,
    mode: 'pill',
    hasMoved: false,
  })
  const hasMeasuredRef = useRef(false)

  useEffect(() => {
    const list = listRef.current
    if (!list || !active) {
      list?.removeAttribute('data-modern-tab-glide')
      hasMeasuredRef.current = false
      return
    }

    const syncRect = () => {
      // 文件夹标签拥有独立折角造型，不由药丸指示器接管。
      if (list.closest('[data-dashboard-folder-tabs="true"]')) {
        list.removeAttribute('data-modern-tab-glide')
        setIndicator((prev) => (prev.rect === null ? prev : { rect: null, mode: prev.mode, hasMoved: false }))
        return
      }

      const activeTrigger = list.querySelector<HTMLElement>(
        '[data-dashboard-tabs-trigger="true"][data-state="active"]'
      )
      if (!activeTrigger) {
        list.removeAttribute('data-modern-tab-glide')
        setIndicator((prev) => (prev.rect === null ? prev : { rect: null, mode: prev.mode, hasMoved: false }))
        return
      }

      const isUnderline =
        variant === 'underline' ||
        (variant === 'auto' &&
          (activeTrigger.hasAttribute('data-dashboard-settings-tabs') ||
            Boolean(list.closest('[data-dashboard-settings-tabs="true"]'))))
      const nextMode: 'pill' | 'underline' = isUnderline ? 'underline' : 'pill'
      const nextRect = measureRelativeRect(list, activeTrigger)

      if (nextRect.width <= 0 || nextRect.height <= 0) {
        list.removeAttribute('data-modern-tab-glide')
        setIndicator((prev) => (prev.rect === null ? prev : { rect: null, mode: nextMode, hasMoved: false }))
        return
      }

      list.dataset.modernTabGlide = nextMode
      const moved = hasMeasuredRef.current
      hasMeasuredRef.current = true
      setIndicator({ rect: nextRect, mode: nextMode, hasMoved: moved })
    }

    const frameId = window.requestAnimationFrame(syncRect)

    const mutationObserver = new MutationObserver(syncRect)
    mutationObserver.observe(list, {
      attributes: true,
      attributeFilter: ['data-state'],
      subtree: true,
      childList: true,
    })

    const resizeObserver = new ResizeObserver(syncRect)
    resizeObserver.observe(list)

    return () => {
      window.cancelAnimationFrame(frameId)
      mutationObserver.disconnect()
      resizeObserver.disconnect()
      list.removeAttribute('data-modern-tab-glide')
    }
  }, [active, listRef, variant])

  if (!active || !indicator.rect) return null

  const { rect, mode, hasMoved } = indicator

  return (
    <motion.span
      aria-hidden="true"
      data-modern-tab-indicator={mode}
      initial={false}
      animate={
        mode === 'underline'
          ? { x: rect.x, width: rect.width, opacity: 1 }
          : { x: rect.x, y: rect.y, width: rect.width, height: rect.height, opacity: 1 }
      }
      transition={hasMoved ? transitions.snappy : { duration: 0 }}
      className={cn(
        'pointer-events-none absolute top-0 left-0 z-0',
        mode === 'underline'
          ? 'top-auto bottom-0 h-[2px] rounded-full bg-primary shadow-[0_0_8px_hsl(var(--primary)/0.45)]'
          : 'rounded-md bg-background shadow-[0_1px_3px_hsl(var(--foreground)/0.12),0_0_0_1px_hsl(var(--input))]'
      )}
    />
  )
}

export interface ModernGlideHighlightProps {
  containerRef: RefObject<HTMLElement | null>
  /** 要跟随高亮的目标元素选择器 */
  itemSelector: string
  /** 可选：用于追踪键盘高亮/选中态的属性选择器 */
  highlightedSelector?: string
  className?: string
  inset?: number
}

interface GlideHighlightState {
  rect: IndicatorRect | null
  visible: boolean
  animatePosition: boolean
}

/**
 * 现代风格列表/菜单液体跟随高亮背板（灵感来自 DesignBookmark Spring dropdown / Command palette）：
 * 当指针划过或键盘切换选项时，磨砂高光块沿选项位置弹簧滑行，离开容器后柔和淡出。
 */
export function ModernGlideHighlight({
  containerRef,
  itemSelector,
  highlightedSelector,
  className,
  inset = 0,
}: ModernGlideHighlightProps) {
  const { style, enabled, transitions } = useMotion()
  const active = style === 'modern' && enabled
  const [highlight, setHighlight] = useState<GlideHighlightState>({
    rect: null,
    visible: false,
    animatePosition: false,
  })

  useEffect(() => {
    const container = containerRef.current
    if (!container || !active) {
      return
    }

    container.dataset.modernGlideContainer = 'true'

    const updateTarget = (target: HTMLElement | null) => {
      if (
        !target ||
        target.hasAttribute('data-disabled') ||
        target.getAttribute('aria-disabled') === 'true'
      ) {
        return
      }
      const next = measureRelativeRect(container, target)
      if (next.width <= 0 || next.height <= 0) return
      const nextRect: IndicatorRect = {
        x: next.x + inset,
        y: next.y + inset,
        width: Math.max(0, next.width - inset * 2),
        height: Math.max(0, next.height - inset * 2),
      }
      setHighlight((prev) => ({
        rect: nextRect,
        visible: true,
        animatePosition: prev.rect !== null,
      }))
    }

    const resolveItem = (node: EventTarget | null) => {
      if (!(node instanceof Element)) return null
      const matched = node.closest<HTMLElement>(itemSelector)
      return matched && container.contains(matched) ? matched : null
    }

    const handlePointerMove = (event: PointerEvent) => {
      const item = resolveItem(event.target)
      if (item) {
        updateTarget(item)
      }
    }

    const handleFocusIn = (event: FocusEvent) => {
      const item = resolveItem(event.target)
      if (item) {
        updateTarget(item)
      }
    }

    const handlePointerLeave = () => {
      setHighlight((prev) => (prev.visible ? { ...prev, visible: false } : prev))
    }

    const handleFocusOut = (event: FocusEvent) => {
      const next = event.relatedTarget
      if (!(next instanceof Node) || !container.contains(next)) {
        setHighlight((prev) => (prev.visible ? { ...prev, visible: false } : prev))
      }
    }

    container.addEventListener('pointermove', handlePointerMove)
    container.addEventListener('focusin', handleFocusIn)
    container.addEventListener('pointerleave', handlePointerLeave)
    container.addEventListener('focusout', handleFocusOut)

    let mutationObserver: MutationObserver | null = null
    let frameId: number | null = null
    if (highlightedSelector) {
      const syncHighlighted = () => {
        const highlighted = container.querySelector<HTMLElement>(highlightedSelector)
        if (highlighted) {
          updateTarget(highlighted)
        }
      }
      frameId = window.requestAnimationFrame(syncHighlighted)
      mutationObserver = new MutationObserver(syncHighlighted)
      mutationObserver.observe(container, {
        attributes: true,
        attributeFilter: ['data-highlighted', 'data-selected', 'aria-selected'],
        subtree: true,
      })
    }

    return () => {
      if (frameId !== null) window.cancelAnimationFrame(frameId)
      container.removeEventListener('pointermove', handlePointerMove)
      container.removeEventListener('focusin', handleFocusIn)
      container.removeEventListener('pointerleave', handlePointerLeave)
      container.removeEventListener('focusout', handleFocusOut)
      mutationObserver?.disconnect()
      delete container.dataset.modernGlideContainer
    }
  }, [active, containerRef, highlightedSelector, inset, itemSelector])

  if (!active || !highlight.rect) return null

  const { rect, visible, animatePosition } = highlight
  const positionTransition = animatePosition ? transitions.snappy : { duration: 0 }

  return (
    <motion.div
      aria-hidden="true"
      data-modern-glide-highlight="true"
      initial={false}
      animate={{
        x: rect.x,
        y: rect.y,
        width: rect.width,
        height: rect.height,
        opacity: visible ? 1 : 0,
      }}
      transition={{
        x: positionTransition,
        y: positionTransition,
        width: positionTransition,
        height: positionTransition,
        opacity: transitions.instant,
      }}
      className={cn(
        'pointer-events-none absolute top-0 left-0 z-0 rounded-md bg-foreground/[0.07] dark:bg-foreground/[0.11]',
        className
      )}
    />
  )
}

export interface MotionIconSwapProps {
  iconKey: string
  children: ReactNode
  className?: string
}

/**
 * 上下文图标变形切换原语（scale 0.25->1, opacity 0->1, blur 4px->0px, spring bounce 0）：
 * 用于主题切换（Sun/Moon）、侧栏展开收起、复制成功等图标状态切换，首次挂载不触发入场。
 */
export function MotionIconSwap({ iconKey, children, className }: MotionIconSwapProps) {
  const { enabled, duration } = useMotion()

  if (!enabled) {
    return <span className={cn('inline-flex items-center justify-center', className)}>{children}</span>
  }

  return (
    <span className={cn('relative inline-flex items-center justify-center', className)}>
      <AnimatePresence mode="popLayout" initial={false}>
        <motion.span
          key={iconKey}
          className="inline-flex items-center justify-center"
          initial={{ opacity: 0, scale: 0.25, filter: 'blur(4px)' }}
          animate={{ opacity: 1, scale: 1, filter: 'blur(0px)' }}
          exit={{ opacity: 0, scale: 0.25, filter: 'blur(4px)' }}
          transition={{ type: 'spring', duration: duration(0.3), bounce: 0 }}
        >
          {children}
        </motion.span>
      </AnimatePresence>
    </span>
  )
}

export interface MotionNumberProps {
  value: number
  format?: (value: number) => string
  className?: string
}

/**
 * 现代风格统计数字弹簧滚动读数（灵感来自 DesignBookmark Stat card）：
 * 仅在 `modern` 风格且动效开启时对数值变化做平滑插值，非现代风格直接静态输出。
 */
export function MotionNumber({ value, format, className }: MotionNumberProps) {
  const { style, enabled } = useMotion()
  const animateNumber = style === 'modern' && enabled && Number.isFinite(value)
  const motionVal = useMotionValue(value)
  const springVal = useSpring(motionVal, { stiffness: 260, damping: 28, mass: 0.7 })
  const staticDisplay = format ? format(value) : String(value)
  const [animatedDisplay, setAnimatedDisplay] = useState(staticDisplay)

  useEffect(() => {
    if (!animateNumber) {
      motionVal.jump(value)
      return
    }
    motionVal.set(value)
  }, [animateNumber, motionVal, value])

  useEffect(() => {
    if (!animateNumber) return
    const isInteger = Number.isInteger(value)
    return springVal.on('change', (latest) => {
      const rounded = isInteger ? Math.round(latest) : Number(latest.toFixed(2))
      setAnimatedDisplay(format ? format(rounded) : String(rounded))
    })
  }, [animateNumber, format, springVal, value])

  return <span className={cn('tabular-nums', className)}>{animateNumber ? animatedDisplay : staticDisplay}</span>
}

export interface MorphTooltipTarget {
  id: string
  label: string
  anchor: HTMLElement
}

export interface ModernMorphTooltipProps {
  target: MorphTooltipTarget | null
  side?: 'bottom' | 'right'
  sideOffset?: number
  className?: string
}

interface MorphTooltipGeometry {
  pillX: number
  pillY: number
  pillWidth: number
  pillHeight: number
  tailX: number
  tailY: number
  direction: 1 | -1
}

/**
 * 现代风格变形跟随提示气泡（灵感来自 DesignBookmark Morph tooltip）：
 * 在一组相邻图标键之间移动指针时，复用同一颗浮层连续平滑过渡位置、宽度、尖角与文字，
 * 避免多个独立 Tooltip 反复挂载/卸载造成的闪烁。
 */
export function ModernMorphTooltip({
  target,
  side = 'bottom',
  sideOffset = 8,
  className,
}: ModernMorphTooltipProps) {
  const { style, enabled, transitions } = useMotion()
  const active = style === 'modern' && enabled
  const measureRef = useRef<HTMLSpanElement | null>(null)
  const previousAnchorCenterRef = useRef<number | null>(null)
  const [geometry, setGeometry] = useState<MorphTooltipGeometry | null>(null)
  const [hasMoved, setHasMoved] = useState(false)

  useLayoutEffect(() => {
    if (!active || !target || !target.anchor.isConnected) {
      previousAnchorCenterRef.current = null
      return
    }

    const compute = () => {
      const anchorRect = target.anchor.getBoundingClientRect()
      const measured = measureRef.current?.getBoundingClientRect()
      const pillWidth = Math.max(44, Math.ceil(measured?.width ?? target.label.length * 12 + 24))
      const pillHeight = Math.max(28, Math.ceil(measured?.height ?? 30))
      const margin = 8

      const anchorCenterX = anchorRect.left + anchorRect.width / 2
      const anchorCenterY = anchorRect.top + anchorRect.height / 2
      const axisPos = side === 'bottom' ? anchorCenterX : anchorCenterY
      const prevAxisPos = previousAnchorCenterRef.current
      const direction: 1 | -1 = prevAxisPos !== null && axisPos < prevAxisPos ? -1 : 1
      const moved = prevAxisPos !== null && Math.abs(axisPos - prevAxisPos) > 1
      previousAnchorCenterRef.current = axisPos

      if (side === 'right') {
        const rawY = anchorCenterY - pillHeight / 2
        const pillY = Math.min(Math.max(margin, rawY), window.innerHeight - pillHeight - margin)
        const pillX = Math.min(anchorRect.right + sideOffset, window.innerWidth - pillWidth - margin)
        const tailY = Math.min(Math.max(8, anchorCenterY - pillY - 4), pillHeight - 16)
        setHasMoved(moved)
        setGeometry({ pillX, pillY, pillWidth, pillHeight, tailX: -4, tailY, direction })
        return
      }

      const rawX = anchorCenterX - pillWidth / 2
      const pillX = Math.min(Math.max(margin, rawX), window.innerWidth - pillWidth - margin)
      const pillY = Math.min(anchorRect.bottom + sideOffset, window.innerHeight - pillHeight - margin)
      const tailX = Math.min(Math.max(10, anchorCenterX - pillX - 4), pillWidth - 18)
      setHasMoved(moved)
      setGeometry({ pillX, pillY, pillWidth, pillHeight, tailX, tailY: -4, direction })
    }

    const frameId = window.requestAnimationFrame(compute)
    return () => window.cancelAnimationFrame(frameId)
  }, [active, side, sideOffset, target])

  if (!active || typeof document === 'undefined') return null

  const open = Boolean(target && geometry)
  const label = target?.label ?? ''

  return createPortal(
    <>
      {/* 隐藏测量节点，精准计算目标文字渲染宽度，驱动气泡宽度平滑变形 */}
      <span
        ref={measureRef}
        aria-hidden="true"
        className="pointer-events-none fixed top-[-9999px] left-[-9999px] px-3 py-1.5 text-xs font-medium whitespace-nowrap opacity-0"
      >
        {label}
      </span>
      <AnimatePresence>
        {open && target && geometry && (
          <motion.div
            key="modern-morph-tooltip"
            role="tooltip"
            data-modern-morph-tooltip="true"
            data-dashboard-floating-content="true"
            initial={{
              opacity: 0,
              scale: 0.94,
              x: geometry.pillX,
              y: geometry.pillY + (side === 'bottom' ? 4 : 0),
              width: geometry.pillWidth,
            }}
            animate={{
              opacity: 1,
              scale: 1,
              x: geometry.pillX,
              y: geometry.pillY,
              width: geometry.pillWidth,
            }}
            exit={{
              opacity: 0,
              scale: 0.95,
              transition: { duration: 0.1, ease: [0.4, 0, 1, 1] },
            }}
            transition={{
              x: hasMoved ? transitions.snappy : { duration: 0 },
              y: hasMoved ? transitions.snappy : transitions.fast,
              width: hasMoved ? transitions.snappy : { duration: 0 },
              opacity: transitions.instant,
              scale: transitions.fast,
            }}
            className={cn(
              'pointer-events-none fixed top-0 left-0 z-[70] flex items-center justify-center rounded-md border bg-popover/95 px-3 py-1.5 text-xs font-medium text-popover-foreground shadow-lg backdrop-blur-md',
              className
            )}
          >
            <motion.span
              aria-hidden="true"
              initial={false}
              animate={{ x: geometry.tailX, y: geometry.tailY }}
              transition={hasMoved ? transitions.snappy : { duration: 0 }}
              className={cn(
                'pointer-events-none absolute top-0 left-0 h-2 w-2 rotate-45 border bg-popover/95',
                side === 'bottom'
                  ? 'border-r-0 border-b-0'
                  : 'border-t-0 border-r-0'
              )}
            />
            <span className="relative flex w-full items-center justify-center overflow-hidden whitespace-nowrap">
              <AnimatePresence mode="popLayout" initial={false} custom={geometry.direction}>
                <motion.span
                  key={target.id}
                  custom={geometry.direction}
                  variants={{
                    enter: (dir: 1 | -1) => ({ opacity: 0, x: dir * 10, filter: 'blur(3px)' }),
                    center: { opacity: 1, x: 0, filter: 'blur(0px)' },
                    leave: (dir: 1 | -1) => ({ opacity: 0, x: -dir * 10, filter: 'blur(3px)' }),
                  }}
                  initial="enter"
                  animate="center"
                  exit="leave"
                  transition={transitions.fast}
                  className="inline-block whitespace-nowrap"
                >
                  {target.label}
                </motion.span>
              </AnimatePresence>
            </span>
          </motion.div>
        )}
      </AnimatePresence>
    </>,
    document.body
  )
}

export interface MotionRollTextProps {
  textKey: string
  children: ReactNode
  className?: string
}

/**
 * 垂直滚动切换文案原语（灵感来自 DesignBookmark Copy button / Loading to success）：
 * 文案切换时沿 Y 轴轻柔滚动交叉淡入，首次挂载不触发入场。
 */
export function MotionRollText({ textKey, children, className }: MotionRollTextProps) {
  const { style, enabled, transitions } = useMotion()

  if (style !== 'modern' || !enabled) {
    return <span className={className}>{children}</span>
  }

  return (
    <span className={cn('relative inline-flex items-center overflow-hidden align-middle', className)}>
      <AnimatePresence mode="popLayout" initial={false}>
        <motion.span
          key={textKey}
          initial={{ opacity: 0, y: 8, filter: 'blur(2px)' }}
          animate={{ opacity: 1, y: 0, filter: 'blur(0px)' }}
          exit={{ opacity: 0, y: -8, filter: 'blur(2px)' }}
          transition={transitions.fast}
          className="inline-flex items-center whitespace-nowrap"
        >
          {children}
        </motion.span>
      </AnimatePresence>
    </span>
  )
}

export interface MotionThinkingTextProps {
  text: string
  active?: boolean
  className?: string
}

/**
 * 现代风格 AI 思考逐字流光波纹（灵感来自 Kokonut UI Thinking text）：
 * 仅在 `modern` 风格且活跃状态时启用克制的单色明暗波浪，保持后台沉静可读。
 */
export function MotionThinkingText({ text, active = true, className }: MotionThinkingTextProps) {
  const { style, enabled } = useMotion()

  if (!active || style !== 'modern' || !enabled) {
    return <span className={className}>{text}</span>
  }

  const chars = Array.from(text)
  return (
    <span data-modern-thinking-text="true" className={cn('inline-flex whitespace-pre', className)} aria-label={text}>
      {chars.map((char, index) => (
        <span
          key={`${index}-${char}`}
          aria-hidden="true"
          data-modern-thinking-char="true"
          style={{ animationDelay: `${index * 60}ms` }}
        >
          {char}
        </span>
      ))}
    </span>
  )
}

export interface MotionHoldButtonProps {
  children: ReactNode
  holdingLabel?: ReactNode
  holdDurationMs?: number
  onComplete: () => void
  disabled?: boolean
  variant?: 'default' | 'destructive'
  className?: string
}

/**
 * 长按填充确认按钮（灵感来自 DesignBookmark Hold to publish / Kokonut UI Hold button）：
 * 按住期间双层裁剪进度条平滑推进，提前松手弹性回退，满格后触发确认回调。
 */
export function MotionHoldButton({
  children,
  holdingLabel,
  holdDurationMs = 900,
  onComplete,
  disabled = false,
  variant = 'default',
  className,
}: MotionHoldButtonProps) {
  const { enabled } = useMotion()
  const [holding, setHolding] = useState(false)
  const [completed, setCompleted] = useState(false)
  const timerRef = useRef<number | null>(null)

  const cancelHold = () => {
    if (timerRef.current !== null) {
      window.clearTimeout(timerRef.current)
      timerRef.current = null
    }
    setHolding(false)
  }

  useEffect(() => cancelHold, [])

  const startHold = () => {
    if (disabled) return
    setCompleted(false)
    setHolding(true)
    timerRef.current = window.setTimeout(() => {
      timerRef.current = null
      setHolding(false)
      setCompleted(true)
      onComplete()
    }, holdDurationMs)
  }

  const fillClass = variant === 'destructive' ? 'bg-destructive' : 'bg-primary'
  const textFillClass = variant === 'destructive' ? 'text-destructive-foreground' : 'text-primary-foreground'

  return (
    <button
      type="button"
      disabled={disabled}
      data-modern-hold-button="true"
      data-holding={holding ? 'true' : 'false'}
      data-completed={completed ? 'true' : 'false'}
      onPointerDown={startHold}
      onPointerUp={cancelHold}
      onPointerLeave={cancelHold}
      onPointerCancel={cancelHold}
      onKeyDown={(event) => {
        if ((event.key === ' ' || event.key === 'Enter') && !event.repeat) {
          startHold()
        }
      }}
      onKeyUp={(event) => {
        if (event.key === ' ' || event.key === 'Enter') {
          cancelHold()
        }
      }}
      className={cn(
        'dashboard-button relative inline-flex h-[var(--control-height)] cursor-pointer items-center justify-center overflow-hidden rounded-md border px-4 text-sm font-medium select-none disabled:pointer-events-none disabled:opacity-50',
        variant === 'destructive'
          ? 'border-destructive/40 bg-destructive/10 text-destructive'
          : 'border-input bg-background text-foreground',
        className
      )}
    >
      <span className="relative z-10 inline-flex items-center gap-2">
        {holding && holdingLabel ? holdingLabel : children}
      </span>
      <motion.span
        aria-hidden="true"
        initial={false}
        animate={{ width: holding ? '100%' : '0%' }}
        transition={
          holding
            ? { duration: holdDurationMs / 1000, ease: 'linear' }
            : enabled
              ? { type: 'spring', stiffness: 460, damping: 32 }
              : { duration: 0 }
        }
        className={cn('pointer-events-none absolute inset-y-0 left-0 z-20 overflow-hidden', fillClass)}
      >
        <span
          className={cn(
            'flex h-full w-max items-center justify-center gap-2 px-4 whitespace-nowrap',
            textFillClass
          )}
        >
          {holding && holdingLabel ? holdingLabel : children}
        </span>
      </motion.span>
    </button>
  )
}

