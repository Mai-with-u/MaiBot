import { useContext, useMemo } from 'react'
import { MotionGlobalConfig, useReducedMotion } from 'motion/react'

import { AnimationContext } from '@/lib/animation-context'
import { ThemeProviderContext } from '@/lib/theme-context'
import { DEFAULT_DASHBOARD_STYLE, type DashboardStyle } from '@/lib/theme/tokens'
import { MotionContext, type MotionRuntime } from './context'
import {
  buildMotionTransitions,
  getMotionProfile,
  resolveMotionSpeedScale,
  scaleDuration,
} from './profiles'

export function createMotionRuntime(
  style: DashboardStyle,
  speedScale: number,
  enableAnimations: boolean,
  reduced: boolean
): MotionRuntime {
  const profile = getMotionProfile(style)
  const enabled = enableAnimations && !reduced && speedScale > 0
  const transitions = buildMotionTransitions(profile, speedScale, enabled)

  return {
    style,
    enabled,
    reduced,
    speedScale,
    profile,
    transitions,
    staggerDelay: (index: number) => {
      if (!enabled || index <= 0) return 0
      const clamped = Math.min(index, profile.distances.maxStaggerItems)
      return scaleDuration(clamped * profile.distances.stagger, speedScale, enabled)
    },
    duration: (seconds: number) => scaleDuration(seconds, speedScale, enabled),
  }
}

/**
 * 读取当前风格与用户偏好对应的统一动效运行时。
 * 在 `<MotionProvider>` 外（如独立组件单测）也可安全读取，不抛出异常。
 */
export function useMotion(): MotionRuntime {
  const provided = useContext(MotionContext)
  const animationContext = useContext(AnimationContext)
  const themeContext = useContext(ThemeProviderContext)
  const reducedMotion = useReducedMotion() === true || MotionGlobalConfig.skipAnimations === true

  const fallbackStyle = themeContext.themeConfig.dashboardStyle ?? DEFAULT_DASHBOARD_STYLE
  const durationToken =
    themeContext.themeConfig.styleTokenOverrides?.[fallbackStyle]?.animation?.['anim-duration-normal']
  const speedScale = resolveMotionSpeedScale(durationToken)
  const enableAnimations = animationContext?.enableAnimations ?? true

  const fallbackRuntime = useMemo(
    () => createMotionRuntime(fallbackStyle, speedScale, enableAnimations, reducedMotion),
    [enableAnimations, fallbackStyle, reducedMotion, speedScale]
  )

  return provided ?? fallbackRuntime
}
