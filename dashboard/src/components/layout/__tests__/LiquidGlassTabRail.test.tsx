import { fireEvent, render, screen } from '@testing-library/react'
import { House, LayoutGrid, MessageCircle } from 'lucide-react'
import { MotionGlobalConfig } from 'motion/react'
import type { ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { MotionProvider } from '@/components/motion'
import { AnimationContext } from '@/lib/animation-context'
import { ThemeProviderContext, type ThemeProviderState } from '@/lib/theme-context'
import { DEFAULT_DASHBOARD_STYLE_CONFIG } from '@/lib/theme/tokens'
import { LiquidGlassTabRail } from '../LiquidGlassTabRail'

vi.mock('@tanstack/react-router', () => ({
  Link: ({
    to,
    children,
    ...rest
  }: {
    to: string
    children: ReactNode
    [key: string]: unknown
  }) => (
    <a href={to} {...rest}>
      {children}
    </a>
  ),
}))

const ITEMS = [
  { id: 'home', to: '/', label: '首页', icon: House },
  { id: 'chat', to: '/chat', label: '聊天', icon: MessageCircle },
  { id: 'manage', to: '/manage', label: '管理', icon: LayoutGrid },
]

const MODERN_THEME_STATE: ThemeProviderState = {
  theme: 'dark',
  resolvedTheme: 'dark',
  setTheme: () => {},
  themeConfig: {
    selectedPreset: 'dark',
    accentColor: '',
    styleTokenOverrides: {},
    styleCustomCSS: {},
    dashboardStyle: 'modern',
    styleBackgroundConfig: {},
    styleConfig: DEFAULT_DASHBOARD_STYLE_CONFIG,
  },
  updateThemeConfig: () => {},
  resetTheme: () => {},
}

function renderRail(props?: Partial<Parameters<typeof LiquidGlassTabRail>[0]>) {
  const onExpand = props?.onExpand ?? vi.fn()
  const onNavigate = props?.onNavigate ?? vi.fn()

  const result = render(
    <ThemeProviderContext.Provider value={MODERN_THEME_STATE}>
      <AnimationContext.Provider value={{ enableAnimations: true, setEnableAnimations: () => {} }}>
        <MotionProvider>
          <LiquidGlassTabRail
            items={ITEMS}
            activeId={props?.activeId ?? 'home'}
            collapsed={props?.collapsed ?? false}
            onExpand={onExpand}
            onNavigate={onNavigate}
          />
        </MotionProvider>
      </AnimationContext.Provider>
    </ThemeProviderContext.Provider>
  )

  return { ...result, onExpand, onNavigate }
}

describe('LiquidGlassTabRail', () => {
  const originalSkip = MotionGlobalConfig.skipAnimations

  beforeEach(() => {
    MotionGlobalConfig.skipAnimations = false
  })

  afterEach(() => {
    MotionGlobalConfig.skipAnimations = originalSkip
  })

  it('renders all tabs with FlClash liquid dock indicator and lifts lens during pointer drag', () => {
    const { container, onNavigate } = renderRail({ activeId: 'home' })
    const rail = container.querySelector<HTMLElement>('[data-liquid-dock="true"]')
    const indicator = container.querySelector<HTMLElement>('[data-mobile-tab-indicator="true"]')

    expect(rail).toBeInTheDocument()
    expect(indicator).toHaveAttribute('data-liquid-lens-lifted', 'false')

    vi.spyOn(rail!, 'getBoundingClientRect').mockReturnValue({
      x: 0,
      y: 0,
      left: 0,
      top: 0,
      right: 308,
      bottom: 62,
      width: 308,
      height: 62,
      toJSON: () => ({}),
    })

    // 按下开始拖拽：透镜抬升进入 m-glass-lens 态
    fireEvent.pointerDown(rail!, { pointerId: 1, button: 0, clientX: 50, clientY: 30 })
    expect(rail).toHaveAttribute('data-lifted', 'true')
    expect(indicator).toHaveAttribute('data-liquid-lens-lifted', 'true')

    // 滑向第三个标签「管理」并松手：触发路由跳转并落回静息态
    fireEvent.pointerMove(rail!, { pointerId: 1, clientX: 260, clientY: 30 })
    fireEvent.pointerUp(rail!, { pointerId: 1, clientX: 260, clientY: 30 })

    expect(onNavigate).toHaveBeenCalledWith('/manage')
    expect(rail).toHaveAttribute('data-lifted', 'false')
    expect(indicator).toHaveAttribute('data-liquid-lens-lifted', 'false')
  })

  it('collapses to active icon orb and calls onExpand when clicked', () => {
    const { container, onExpand } = renderRail({ activeId: 'manage', collapsed: true })
    const rail = container.querySelector<HTMLElement>('[data-liquid-dock="true"]')

    expect(screen.queryByText('首页')).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: '管理' })).toBeInTheDocument()

    fireEvent.click(rail!)
    expect(onExpand).toHaveBeenCalledTimes(1)
  })
})
