import { useContext, useLayoutEffect, useMemo, type ReactNode } from 'react'
import { MotionConfig, MotionGlobalConfig, useReducedMotion } from 'motion/react'

import { AnimationContext } from '@/lib/animation-context'
import {
  MotionContext,
  buildMotionCssVariables,
  createMotionRuntime,
  resolveMotionSpeedScale,
} from '@/lib/motion'
import { ThemeProviderContext } from '@/lib/theme-context'
import { DEFAULT_DASHBOARD_STYLE } from '@/lib/theme/tokens'

export interface MotionProviderProps {
  children: ReactNode
}

/**
 * 全局动效底座提供者：
 * 1. 汇总当前界面风格（modern / future-retro / millennium）、全局动效开关、外观速度设置与系统 reduced-motion；
 * 2. 在应用根部挂载 `<MotionConfig>`，统一接管所有 `motion/react` 组件；
 * 3. 将对应的 `--motion-*` CSS 令牌写入 `:root`，使 CSS 过渡与 Radix 退场保持同源性格。
 */
export function MotionProvider({ children }: MotionProviderProps) {
  const themeContext = useContext(ThemeProviderContext)
  const animationContext = useContext(AnimationContext)
  const reducedMotion = useReducedMotion() === true || MotionGlobalConfig.skipAnimations === true

  const style = themeContext.themeConfig.dashboardStyle ?? DEFAULT_DASHBOARD_STYLE
  const durationToken =
    themeContext.themeConfig.styleTokenOverrides?.[style]?.animation?.['anim-duration-normal']
  const speedScale = resolveMotionSpeedScale(durationToken)
  const enableAnimations = animationContext?.enableAnimations ?? true

  const runtime = useMemo(
    () => createMotionRuntime(style, speedScale, enableAnimations, reducedMotion),
    [enableAnimations, reducedMotion, speedScale, style]
  )

  useLayoutEffect(() => {
    const root = document.documentElement
    root.dataset.motionEnabled = runtime.enabled ? 'true' : 'false'
    root.dataset.motionStyle = runtime.style

    const vars = buildMotionCssVariables(runtime.profile, runtime.speedScale, runtime.enabled)
    for (const [key, value] of Object.entries(vars)) {
      root.style.setProperty(key, value)
    }
  }, [runtime])

  return (
    <MotionContext.Provider value={runtime}>
      <MotionConfig
        reducedMotion={runtime.enabled ? 'user' : 'always'}
        transition={runtime.transitions.spring}
      >
        {children}
      </MotionConfig>
    </MotionContext.Provider>
  )
}
