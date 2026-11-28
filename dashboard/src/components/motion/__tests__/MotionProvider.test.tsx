import { render, screen } from '@testing-library/react'
import { MotionGlobalConfig } from 'motion/react'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { AnimationContext } from '@/lib/animation-context'
import { useMotion } from '@/lib/motion'
import { ThemeProviderContext, type ThemeProviderState } from '@/lib/theme-context'
import {
  DEFAULT_DASHBOARD_STYLE,
  DEFAULT_DASHBOARD_STYLE_CONFIG,
  type DashboardStyle,
} from '@/lib/theme/tokens'
import { MotionProvider } from '../MotionProvider'

function MotionProbe() {
  const motion = useMotion()
  return (
    <div
      data-testid="probe"
      data-style={motion.style}
      data-enabled={String(motion.enabled)}
      data-speed={String(motion.speedScale)}
      data-stagger-2={String(motion.staggerDelay(2))}
      data-stagger-99={String(motion.staggerDelay(99))}
    />
  )
}

function makeThemeState(
  dashboardStyle: DashboardStyle,
  durationNormal?: string
): ThemeProviderState {
  return {
    theme: 'light',
    resolvedTheme: 'light',
    setTheme: () => {},
    themeConfig: {
      selectedPreset: 'light',
      accentColor: '',
      styleTokenOverrides: durationNormal
        ? { [dashboardStyle]: { animation: { 'anim-duration-normal': durationNormal } } }
        : {},
      styleCustomCSS: {},
      dashboardStyle,
      styleBackgroundConfig: {},
      styleConfig: DEFAULT_DASHBOARD_STYLE_CONFIG,
    },
    updateThemeConfig: () => {},
    resetTheme: () => {},
  }
}

describe('MotionProvider and useMotion', () => {
  let previousSkipAnimations: boolean | undefined

  beforeEach(() => {
    previousSkipAnimations = MotionGlobalConfig.skipAnimations
    MotionGlobalConfig.skipAnimations = false
  })

  afterEach(() => {
    MotionGlobalConfig.skipAnimations = previousSkipAnimations
  })

  it('safely works outside MotionProvider without throwing', () => {
    render(<MotionProbe />)
    const probe = screen.getByTestId('probe')
    expect(probe).toHaveAttribute('data-style', DEFAULT_DASHBOARD_STYLE)
    expect(probe).toHaveAttribute('data-enabled', 'true')
    expect(probe).toHaveAttribute('data-speed', '1')
  })

  it('syncs style profile, CSS variables, and speed overrides onto documentElement', () => {
    render(
      <ThemeProviderContext.Provider value={makeThemeState('millennium', '100ms')}>
        <AnimationContext.Provider value={{ enableAnimations: true, setEnableAnimations: () => {} }}>
          <MotionProvider>
            <MotionProbe />
          </MotionProvider>
        </AnimationContext.Provider>
      </ThemeProviderContext.Provider>
    )

    const probe = screen.getByTestId('probe')
    expect(probe).toHaveAttribute('data-style', 'millennium')
    expect(probe).toHaveAttribute('data-enabled', 'true')
    expect(probe).toHaveAttribute('data-speed', '0.65')
    expect(document.documentElement.dataset.motionStyle).toBe('millennium')
    expect(document.documentElement.dataset.motionEnabled).toBe('true')
    expect(document.documentElement.style.getPropertyValue('--motion-tap-scale')).toBe('1')
    expect(document.documentElement.style.getPropertyValue('--motion-tap-y')).toBe('2px')
    // 超过上限的交错序号应封顶，防止长列表延迟失控
    expect(Number(probe.getAttribute('data-stagger-99'))).toBeLessThanOrEqual(0.1)
    expect(Number(probe.getAttribute('data-stagger-99'))).toBeGreaterThan(0)
  })

  it('disables motion globally when AnimationProvider turns animations off', () => {
    render(
      <ThemeProviderContext.Provider value={makeThemeState('future-retro')}>
        <AnimationContext.Provider value={{ enableAnimations: false, setEnableAnimations: () => {} }}>
          <MotionProvider>
            <MotionProbe />
          </MotionProvider>
        </AnimationContext.Provider>
      </ThemeProviderContext.Provider>
    )

    const probe = screen.getByTestId('probe')
    expect(probe).toHaveAttribute('data-style', 'future-retro')
    expect(probe).toHaveAttribute('data-enabled', 'false')
    expect(document.documentElement.dataset.motionEnabled).toBe('false')
    expect(document.documentElement.style.getPropertyValue('--motion-duration-fast')).toBe('0ms')
  })
})
