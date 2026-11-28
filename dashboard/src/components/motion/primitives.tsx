import { forwardRef, type ReactNode } from 'react'
import {
  AnimatePresence,
  motion,
  type AnimatePresenceProps,
  type HTMLMotionProps,
} from 'motion/react'

import { useMotion } from '@/lib/motion'

export interface MotionPageProps {
  routeKey: string
  className?: string
  children: ReactNode
}

/**
 * 路由级页面入场容器：随界面风格自动切换入场位移与阻尼曲线。
 * 动画结束后 `y: 0` 会让 Motion 还原 `transform: none`，不阻断内部 sticky/fixed 定位。
 */
export function MotionPage({ routeKey, className, children }: MotionPageProps) {
  const { enabled, profile, transitions } = useMotion()

  return (
    <motion.div
      key={routeKey}
      data-motion-page="true"
      className={className}
      initial={enabled ? { opacity: 0, y: profile.distances.pageY } : false}
      animate={{ opacity: 1, y: 0 }}
      transition={transitions.page}
    >
      {children}
    </motion.div>
  )
}

export type MotionFloatingVariant = 'popover' | 'menu' | 'tooltip' | 'select'

export interface MotionFloatingProps extends HTMLMotionProps<'div'> {
  variant?: MotionFloatingVariant
}

/**
 * 浮层表面动效原语：通过 Radix `asChild` 直接附着到浮层节点，
 * 入场由 `motion/react` 风格弹簧驱动，退场交由 Radix Presence + `--motion-*` 令牌衔接。
 */
export const MotionFloating = forwardRef<HTMLDivElement, MotionFloatingProps>(
  ({ variant = 'popover', initial, animate, transition, ...props }, ref) => {
    const { enabled, profile, transitions } = useMotion()
    const { overlayScale, overlayY } = profile.distances
    const offset = variant === 'tooltip' ? Math.max(2, Math.round(overlayY * 0.6)) : overlayY

    return (
      <motion.div
        ref={ref}
        data-motion-floating={variant}
        initial={
          initial ??
          (enabled
            ? {
                opacity: 0,
                scale: overlayScale,
                y: offset,
              }
            : false)
        }
        animate={animate ?? { opacity: 1, scale: 1, y: 0 }}
        transition={transition ?? transitions.overlay}
        {...props}
      />
    )
  }
)
MotionFloating.displayName = 'MotionFloating'

export const MotionOverlayBackdrop = forwardRef<HTMLDivElement, HTMLMotionProps<'div'>>(
  ({ initial, animate, transition, ...props }, ref) => {
    const { enabled, transitions } = useMotion()

    return (
      <motion.div
        ref={ref}
        data-motion-overlay="true"
        initial={initial ?? (enabled ? { opacity: 0 } : false)}
        animate={animate ?? { opacity: 1 }}
        transition={transition ?? transitions.fast}
        {...props}
      />
    )
  }
)
MotionOverlayBackdrop.displayName = 'MotionOverlayBackdrop'

export interface MotionDialogSurfaceProps extends HTMLMotionProps<'div'> {
  side?: 'center' | 'right'
  isMobileSheet?: boolean
}

/**
 * 弹窗表面动效原语：支持居中弹窗、右侧抽屉与手机端底部弹层三种几何形态，
 * 搭配 `asChild` 附着在 Radix Dialog / AlertDialog Content 上。
 */
export const MotionDialogSurface = forwardRef<HTMLDivElement, MotionDialogSurfaceProps>(
  ({ side = 'center', isMobileSheet = false, initial, animate, transition, ...props }, ref) => {
    const { enabled, profile, transitions } = useMotion()
    const { dialogScale, dialogY } = profile.distances

    const computedInitial = (() => {
      if (initial !== undefined) return initial
      if (!enabled) return false as const
      if (side === 'right') {
        return { opacity: 0, x: 28 }
      }
      if (isMobileSheet) {
        return { opacity: 0, y: 32 }
      }
      return { opacity: 0, scale: dialogScale, y: dialogY }
    })()

    const computedAnimate =
      animate ??
      (side === 'right'
        ? { opacity: 1, x: 0 }
        : isMobileSheet
          ? { opacity: 1, y: 0 }
          : { opacity: 1, scale: 1, y: 0 })

    return (
      <motion.div
        ref={ref}
        data-motion-dialog={side === 'right' ? 'right' : isMobileSheet ? 'sheet' : 'center'}
        initial={computedInitial}
        animate={computedAnimate}
        transition={transition ?? transitions.dialog}
        {...props}
      />
    )
  }
)
MotionDialogSurface.displayName = 'MotionDialogSurface'

export const MotionTabContent = forwardRef<HTMLDivElement, HTMLMotionProps<'div'>>(
  ({ initial, animate, transition, ...props }, ref) => {
    const { enabled, profile, transitions } = useMotion()
    const { tabX, tabY } = profile.distances

    return (
      <motion.div
        ref={ref}
        data-motion-tab-content="true"
        initial={initial ?? (enabled ? { opacity: 0, x: tabX, y: tabY } : false)}
        animate={animate ?? { opacity: 1, x: 0, y: 0 }}
        transition={transition ?? transitions.tab}
        {...props}
      />
    )
  }
)
MotionTabContent.displayName = 'MotionTabContent'

export interface MotionCollapseProps {
  open: boolean
  className?: string
  children: ReactNode
}

/**
 * 通用高度折叠/展开原语，用于条件渲染区块、筛选抽屉、工具参数展开等场景。
 */
export function MotionCollapse({ open, className, children }: MotionCollapseProps) {
  const { enabled, transitions } = useMotion()

  return (
    <AnimatePresence initial={false}>
      {open && (
        <motion.div
          data-motion-collapse="true"
          className={className}
          style={{ overflow: 'hidden' }}
          initial={enabled ? { height: 0, opacity: 0 } : false}
          animate={{ height: 'auto', opacity: 1 }}
          exit={enabled ? { height: 0, opacity: 0 } : undefined}
          transition={transitions.collapse}
        >
          {children}
        </motion.div>
      )}
    </AnimatePresence>
  )
}

export interface MotionFadeInProps extends HTMLMotionProps<'div'> {
  offsetY?: number
  delay?: number
}

export const MotionFadeIn = forwardRef<HTMLDivElement, MotionFadeInProps>(
  ({ offsetY, delay = 0, initial, animate, exit, transition, ...props }, ref) => {
    const { enabled, profile, transitions, duration } = useMotion()
    const y = offsetY ?? profile.distances.listItemY

    return (
      <motion.div
        ref={ref}
        initial={initial ?? (enabled ? { opacity: 0, y } : false)}
        animate={animate ?? { opacity: 1, y: 0 }}
        exit={exit ?? (enabled ? { opacity: 0, y: Math.round(y * 0.5) } : undefined)}
        transition={
          transition ?? {
            ...transitions.normal,
            delay: delay > 0 ? duration(delay) : 0,
          }
        }
        {...props}
      />
    )
  }
)
MotionFadeIn.displayName = 'MotionFadeIn'

export interface MotionStaggerItemProps extends HTMLMotionProps<'div'> {
  index?: number
}

/**
 * 列表/卡片交错入场项：交错延迟受当前风格上限约束，长列表不会产生累积拖影。
 */
export const MotionStaggerItem = forwardRef<HTMLDivElement, MotionStaggerItemProps>(
  ({ index = 0, initial, animate, transition, ...props }, ref) => {
    const { enabled, profile, transitions, staggerDelay } = useMotion()

    return (
      <motion.div
        ref={ref}
        initial={initial ?? (enabled ? { opacity: 0, y: profile.distances.listItemY } : false)}
        animate={animate ?? { opacity: 1, y: 0 }}
        transition={
          transition ?? {
            ...transitions.normal,
            delay: staggerDelay(index),
          }
        }
        {...props}
      />
    )
  }
)
MotionStaggerItem.displayName = 'MotionStaggerItem'

export type MotionPresenceProps = AnimatePresenceProps & {
  children?: ReactNode
}

export function MotionPresence({ children, ...props }: MotionPresenceProps) {
  return <AnimatePresence {...props}>{children}</AnimatePresence>
}
