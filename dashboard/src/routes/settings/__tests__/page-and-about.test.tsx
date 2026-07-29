import type { ReactNode } from 'react'

import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { AboutTab } from '../AboutTab'
import { SettingsPage } from '../index'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))

vi.mock('@/components/ui/scroll-area', () => ({
  ScrollArea: ({
    children,
    viewportRef,
  }: {
    children: ReactNode
    viewportRef?: { current: HTMLDivElement | null }
  }) => (
    <div
      data-testid="settings-scroll-viewport"
      ref={(node) => {
        if (viewportRef) {
          viewportRef.current = node
        }
      }}
    >
      {children}
    </div>
  ),
}))

vi.mock('@/components/ui/tabs', async () => {
  const { createContext, useContext } = await import('react')

  type TabsContextValue = {
    value: string
    onValueChange: (value: string) => void
  }

  const TabsContext = createContext<TabsContextValue | null>(null)

  return {
    Tabs: ({
      children,
      value,
      onValueChange,
    }: {
      children: ReactNode
      value: string
      onValueChange: (value: string) => void
    }) => (
      <TabsContext.Provider value={{ value, onValueChange }}>
        <div data-testid="settings-tabs" data-value={value}>
          {children}
        </div>
      </TabsContext.Provider>
    ),
    TabsList: ({ children }: { children: ReactNode }) => <div>{children}</div>,
    TabsTrigger: ({ children, value }: { children: ReactNode; value: string }) => {
      const context = useContext(TabsContext)
      return (
        <button type="button" onClick={() => context?.onValueChange(value)}>
          {children}
        </button>
      )
    },
    TabsContent: ({ children, value }: { children: ReactNode; value: string }) => {
      const context = useContext(TabsContext)
      return context?.value === value ? <section>{children}</section> : null
    },
  }
})

vi.mock('../AppearanceTab', () => ({
  AppearanceTab: () => <div>外观页内容</div>,
}))

vi.mock('../SecurityTab', () => ({
  SecurityTab: () => <div>安全页内容</div>,
}))

vi.mock('../OtherTab', () => ({
  OtherTab: () => <div>其他页内容</div>,
}))

describe('设置页入口与关于页', () => {
  const scrollTo = vi.fn()

  beforeEach(() => {
    window.history.replaceState(null, '', '/settings')
    Object.defineProperty(HTMLDivElement.prototype, 'scrollTo', {
      configurable: true,
      value: scrollTo,
    })
    vi.stubGlobal(
      'requestAnimationFrame',
      vi.fn((callback: FrameRequestCallback) => {
        callback(0)
        return 1
      })
    )
    vi.stubGlobal('cancelAnimationFrame', vi.fn())
  })

  afterEach(() => {
    cleanup()
    scrollTo.mockReset()
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
  })

  it('优先读取查询参数，并在切换标签时同步 URL 和滚动位置', async () => {
    window.history.replaceState(null, '', '/settings?tab=security')
    const replaceState = vi.spyOn(window.history, 'replaceState')
    const user = userEvent.setup()

    render(<SettingsPage />)

    expect(screen.getByTestId('settings-tabs')).toHaveAttribute('data-value', 'security')
    expect(screen.getByText('安全页内容')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'settings.tabs.about' }))

    expect(screen.getByTestId('settings-tabs')).toHaveAttribute('data-value', 'about')
    expect(replaceState).toHaveBeenLastCalledWith(null, '', '/settings?tab=about')
    expect(scrollTo).toHaveBeenCalledWith({ top: 0 })
  })

  it('无效标签回退到外观页，并在滚动阈值两侧折叠和展开标题', () => {
    window.history.replaceState(null, '', '/settings?tab=unknown')
    render(<SettingsPage />)

    expect(screen.getByTestId('settings-tabs')).toHaveAttribute('data-value', 'appearance')
    expect(screen.getByText('外观页内容')).toBeInTheDocument()

    const viewport = screen.getByTestId('settings-scroll-viewport')
    const titleContainer = screen.getByRole('heading', { name: 'settings.title' }).parentElement
      ?.parentElement
    Object.defineProperty(viewport, 'clientHeight', { configurable: true, value: 500 })

    viewport.scrollTop = 65
    fireEvent.scroll(viewport)
    expect(titleContainer).toHaveClass('max-h-0', 'opacity-0')

    viewport.scrollTop = 4
    fireEvent.scroll(viewport)
    expect(titleContainer).toHaveClass('max-h-24', 'opacity-100')
  })

  it('关于页展示版本、技术栈、许可证和安全的外部链接属性', () => {
    render(<AboutTab />)

    expect(screen.getByText(/MaiBot Dashboard/)).toBeInTheDocument()
    expect(screen.getByText('React 19.2.0')).toBeInTheDocument()
    expect(screen.getByText('GPLv3')).toBeInTheDocument()
    expect(screen.getByText('TypeScript')).toBeInTheDocument()

    expect(screen.getByRole('link', { name: /settings.about.visitGitHub/ })).toHaveAttribute(
      'href',
      'https://github.com/Mai-with-u/MaiBot-Dashboard'
    )
    expect(screen.getByRole('link', { name: '@MotricSeven' })).toHaveAttribute(
      'rel',
      'noopener noreferrer'
    )
  })
})
