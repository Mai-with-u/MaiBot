import { fireEvent, render, screen } from '@testing-library/react'
import { MotionGlobalConfig } from 'motion/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { AnimationContext } from '@/lib/animation-context'
import { ThemeProviderContext, type ThemeProviderState } from '@/lib/theme-context'
import { DEFAULT_DASHBOARD_STYLE_CONFIG, type DashboardStyle } from '@/lib/theme/tokens'
import { Checkbox } from '@/components/ui/checkbox'
import { SegmentedControl } from '@/components/ui/management'
import { Progress } from '@/components/ui/progress'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import {
  MotionHoldButton,
  MotionIconSwap,
  MotionNumber,
  MotionProvider,
  MotionRollText,
  MotionThinkingText,
} from '../index'

function makeThemeState(dashboardStyle: DashboardStyle): ThemeProviderState {
  return {
    theme: 'light',
    resolvedTheme: 'light',
    setTheme: () => {},
    themeConfig: {
      selectedPreset: 'light',
      accentColor: '',
      styleTokenOverrides: {},
      styleCustomCSS: {},
      dashboardStyle,
      styleBackgroundConfig: {},
      styleConfig: DEFAULT_DASHBOARD_STYLE_CONFIG,
    },
    updateThemeConfig: () => {},
    resetTheme: () => {},
  }
}

function renderWithStyle(style: DashboardStyle, ui: React.ReactNode, enableAnimations = true) {
  return render(
    <ThemeProviderContext.Provider value={makeThemeState(style)}>
      <AnimationContext.Provider value={{ enableAnimations, setEnableAnimations: () => {} }}>
        <MotionProvider>{ui}</MotionProvider>
      </AnimationContext.Provider>
    </ThemeProviderContext.Provider>
  )
}

describe('modern theme motion UI components', () => {
  let previousSkipAnimations: boolean | undefined

  beforeEach(() => {
    previousSkipAnimations = MotionGlobalConfig.skipAnimations
    MotionGlobalConfig.skipAnimations = false
  })

  afterEach(() => {
    MotionGlobalConfig.skipAnimations = previousSkipAnimations
  })

  it('activates modern segmented pill exclusively on modern style', () => {
    const { container: modernContainer } = renderWithStyle(
      'modern',
      <SegmentedControl
        aria-label="模式"
        value="a"
        onValueChange={() => {}}
        options={[
          { value: 'a', label: '选项 A' },
          { value: 'b', label: '选项 B' },
        ]}
      />
    )

    expect(
      modernContainer.querySelector('[data-dashboard-segmented-control="true"]')
    ).toHaveAttribute('data-modern-segmented-glide', 'true')

    const { container: millenniumContainer } = renderWithStyle(
      'millennium',
      <SegmentedControl
        aria-label="模式2"
        value="a"
        onValueChange={() => {}}
        options={[
          { value: 'a', label: '选项 A' },
          { value: 'b', label: '选项 B' },
        ]}
      />
    )

    expect(
      millenniumContainer.querySelector('[data-dashboard-segmented-control="true"]')
    ).not.toHaveAttribute('data-modern-segmented-glide')
  })

  it('renders MotionIconSwap and MotionNumber cleanly across styles', () => {
    const { rerender } = renderWithStyle(
      'modern',
      <>
        <MotionIconSwap iconKey="sun">
          <span data-testid="icon">sun</span>
        </MotionIconSwap>
        <MotionNumber value={42} />
      </>
    )

    expect(screen.getByTestId('icon')).toHaveTextContent('sun')
    expect(screen.getByText('42')).toBeInTheDocument()

    rerender(
      <ThemeProviderContext.Provider value={makeThemeState('future-retro')}>
        <AnimationContext.Provider value={{ enableAnimations: true, setEnableAnimations: () => {} }}>
          <MotionProvider>
            <MotionIconSwap iconKey="moon">
              <span data-testid="icon">moon</span>
            </MotionIconSwap>
            <MotionNumber value={99} />
          </MotionProvider>
        </AnimationContext.Provider>
      </ThemeProviderContext.Provider>
    )

    expect(screen.getByText('moon')).toBeInTheDocument()
    expect(screen.getByText('99')).toBeInTheDocument()
  })

  it('preserves Tabs accessibility and data-state attributes with ModernTabIndicator', () => {
    renderWithStyle(
      'modern',
      <Tabs defaultValue="one">
        <TabsList>
          <TabsTrigger value="one">一</TabsTrigger>
          <TabsTrigger value="two">二</TabsTrigger>
        </TabsList>
        <TabsContent value="one">内容一</TabsContent>
        <TabsContent value="two">内容二</TabsContent>
      </Tabs>
    )

    expect(screen.getByRole('tab', { name: '一' })).toHaveAttribute('data-state', 'active')
    expect(screen.getByText('内容一')).toBeInTheDocument()
  })

  it('renders modern ThinkingText wave, RollText, animated Checkbox, and Progress cleanly', () => {
    const { container } = renderWithStyle(
      'modern',
      <>
        <MotionThinkingText text="思考中" active />
        <MotionRollText textKey="a">复制</MotionRollText>
        <Checkbox checked aria-label="勾选框" />
        <Progress value={60} aria-label="进度" />
      </>
    )

    expect(container.querySelectorAll('[data-modern-thinking-char="true"]')).toHaveLength(3)
    expect(screen.getByText('复制')).toBeInTheDocument()
    expect(screen.getByRole('checkbox', { name: '勾选框' })).toHaveAttribute('data-state', 'checked')
    expect(screen.getByRole('progressbar', { name: '进度' })).toBeInTheDocument()
  })

  it('triggers MotionHoldButton callback only after holding for the full duration', () => {
    vi.useFakeTimers()
    const onComplete = vi.fn()

    try {
      renderWithStyle(
        'modern',
        <MotionHoldButton holdDurationMs={500} onComplete={onComplete}>
          长按确认
        </MotionHoldButton>
      )

      const button = screen.getByRole('button', { name: '长按确认' })
      fireEvent.pointerDown(button)
      vi.advanceTimersByTime(300)
      fireEvent.pointerUp(button)
      expect(onComplete).not.toHaveBeenCalled()

      fireEvent.pointerDown(button)
      vi.advanceTimersByTime(500)
      expect(onComplete).toHaveBeenCalledTimes(1)
    } finally {
      vi.useRealTimers()
    }
  })
})
