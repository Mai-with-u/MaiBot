import type { ReactNode } from 'react'

import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { LogoArea } from './LogoArea'
import { Sidebar } from './Sidebar'

const SIDEBAR_HOVER_EXPAND_DELAY_MS = 180

const mocks = vi.hoisted(() => ({
  inheritedFrom: 'sidebar',
}))

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))

vi.mock('@/hooks/use-background', () => ({
  useBackground: () => ({
    config: { type: 'color', color: '#123456' },
    inheritedFrom: mocks.inheritedFrom,
  }),
}))

vi.mock('@/components/background-layer', () => ({
  BackgroundLayer: ({ layerId }: { layerId: string }) => (
    <div data-testid={`background-${layerId}`} />
  ),
}))

vi.mock('@/components/ui/scroll-area', () => ({
  ScrollArea: ({
    children,
    className,
  }: {
    children: ReactNode
    className?: string
  }) => (
    <div className={className}>{children}</div>
  ),
}))

vi.mock('./NavItem', () => ({
  NavItem: ({
    item,
    sidebarOpen,
    onMobileMenuClose,
  }: {
    item: { path: string; label: string }
    sidebarOpen: boolean
    onMobileMenuClose: () => void
  }) => (
    <button
      type="button"
      data-path={item.path}
      data-sidebar-open={String(sidebarOpen)}
      onClick={onMobileMenuClose}
    >
      {item.label}
    </button>
  ),
}))

vi.mock('./use-menu-sections', () => ({
  useMenuSections: () => [
    {
      title: 'sidebar.groups.overview',
      items: [{ path: '/', label: '首页' }],
    },
    {
      title: 'sidebar.groups.resources',
      items: [
        { path: '/memory', label: '记忆' },
        { path: '/emoji', label: '表情包' },
      ],
    },
  ],
}))

describe('LogoArea 与 Sidebar', () => {
  beforeEach(() => {
    mocks.inheritedFrom = 'sidebar'
  })

  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
    vi.useRealTimers()
  })

  it('展开 Logo 时展示品牌标题和光谱彩条，折叠时展示光谱彩块', () => {
    const { rerender } = render(<LogoArea sidebarOpen />)

    expect(screen.getByText('MAIBOT')).toBeInTheDocument()
    expect(document.querySelector('[data-dashboard-logo-spectrum="true"]')).toBeInTheDocument()
    expect(
      document.querySelector('[data-dashboard-logo-spectrum-block="true"]')
    ).not.toBeInTheDocument()

    rerender(<LogoArea sidebarOpen={false} />)
    expect(screen.getByText('MAIBOT').parentElement).toHaveClass('lg:opacity-0')
    expect(
      document.querySelector('[data-dashboard-logo-spectrum-block="true"]')
    ).toBeInTheDocument()
  })

  it('展开侧栏时渲染独立背景、菜单分组和展开态导航项', () => {
    const onMobileMenuClose = vi.fn()
    const { container } = render(
      <Sidebar
        sidebarOpen
        mobileMenuOpen
        onMobileMenuClose={onMobileMenuClose}
        onSidebarFix={vi.fn()}
      />
    )

    const aside = container.querySelector('[data-dashboard-sidebar="true"]')
    expect(aside).toHaveClass('bg-card', 'translate-x-0')
    expect(screen.getByTestId('background-sidebar')).toBeInTheDocument()
    expect(screen.getByRole('navigation')).toHaveAttribute('aria-label', 'a11y.sidebarNav')
    expect(screen.getByText('sidebar.groups.overview').parentElement).toHaveClass('hidden')
    expect(screen.getByText('sidebar.groups.resources')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '首页' })).toHaveAttribute(
      'data-sidebar-open',
      'true'
    )

    fireEvent.click(screen.getByRole('button', { name: '记忆' }))
    expect(onMobileMenuClose).toHaveBeenCalledOnce()
  })

  it('继承页面背景时保持透明且不重复渲染背景层', () => {
    mocks.inheritedFrom = 'page'
    const { container } = render(
      <Sidebar
        sidebarOpen={false}
        mobileMenuOpen={false}
        onMobileMenuClose={vi.fn()}
        onSidebarFix={vi.fn()}
      />
    )

    const aside = container.querySelector('[data-dashboard-sidebar="true"]')
    expect(aside).toHaveClass('bg-transparent', '-translate-x-full')
    expect(screen.queryByTestId('background-sidebar')).not.toBeInTheDocument()
    expect(
      container.querySelector('[data-dashboard-logo-spectrum-block="true"]')
    ).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '表情包' })).toHaveAttribute(
      'data-sidebar-open',
      'false'
    )
    expect(container.querySelector('.border-t')).toBeInTheDocument()
  })

  it('悬浮模式展开后才显示右折线，并在固定交接期间保持展开', () => {
    vi.useFakeTimers()
    const onSidebarFix = vi.fn()
    const { container, rerender } = render(
      <Sidebar
        sidebarOpen={false}
        mobileMenuOpen={false}
        onMobileMenuClose={vi.fn()}
        onSidebarFix={onSidebarFix}
      />
    )

    const aside = container.querySelector('[data-dashboard-sidebar="true"]')
    expect(aside).toHaveAttribute('data-dashboard-sidebar-mode', 'hover')
    expect(aside).toHaveAttribute('data-dashboard-sidebar-visually-open', 'false')
    expect(aside).toHaveClass(
      'lg:w-[var(--layout-sidebar-width)]',
      'lg:transition-[clip-path]',
      'lg:[clip-path:inset(0_calc(var(--layout-sidebar-width)-var(--layout-sidebar-collapsed-width))_0_0)]'
    )
    expect(
      container.querySelector('[data-dashboard-sidebar-collapsed-divider="true"]')
    ).toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: 'header.switchSidebarToFixed' })
    ).not.toBeInTheDocument()

    fireEvent.pointerEnter(aside as HTMLElement, { pointerType: 'mouse' })
    act(() => vi.advanceTimersByTime(SIDEBAR_HOVER_EXPAND_DELAY_MS))
    expect(aside).toHaveAttribute('data-dashboard-sidebar-hover-expanded', 'true')
    expect(aside).toHaveAttribute('data-dashboard-sidebar-visually-open', 'true')
    expect(aside).toHaveClass('lg:[clip-path:inset(0_0_0_0)]')
    expect(
      container.querySelector('[data-dashboard-sidebar-collapsed-divider="true"]')
    ).not.toBeInTheDocument()
    const hoverNavigation = screen.getByRole('navigation')
    const hoverNavigationClassName = hoverNavigation.className
    expect(hoverNavigation).toHaveClass(
      'px-[var(--layout-sidebar-nav-padding-collapsed)]',
      'py-[var(--layout-sidebar-nav-padding)]'
    )
    expect(screen.getByText('sidebar.groups.resources').parentElement).not.toHaveClass(
      'lg:mb-[var(--layout-sidebar-section-title-margin-bottom-collapsed)]'
    )
    expect(container.querySelector('.border-t')).toHaveClass('lg:opacity-0')
    expect(screen.getByRole('button', { name: '首页' })).toHaveAttribute(
      'data-sidebar-open',
      'true'
    )

    const fixButton = screen.getByRole('button', { name: 'header.switchSidebarToFixed' })
    expect(fixButton).toHaveClass(
      'right-4',
      'bottom-3',
      'border-0',
      'bg-transparent',
      'text-muted-foreground/55',
      'shadow-none'
    )
    expect(fixButton.querySelector('svg')).toHaveClass('lucide-chevron-right', 'h-5', 'w-5')

    fireEvent.click(fixButton)
    fireEvent.pointerLeave(aside as HTMLElement)
    expect(onSidebarFix).toHaveBeenCalledOnce()
    expect(aside).toHaveAttribute('data-dashboard-sidebar-fix-transition', 'true')
    expect(aside).toHaveAttribute('data-dashboard-sidebar-visually-open', 'true')

    rerender(
      <Sidebar
        sidebarOpen
        mobileMenuOpen={false}
        onMobileMenuClose={vi.fn()}
        onSidebarFix={onSidebarFix}
      />
    )
    expect(aside).toHaveAttribute('data-dashboard-sidebar-mode', 'fixed')
    expect(aside).toHaveAttribute('data-dashboard-sidebar-visually-open', 'true')
    expect(aside).not.toHaveAttribute('data-dashboard-sidebar-fix-transition')
    expect(screen.getByRole('navigation').className).toBe(hoverNavigationClassName)
    expect(
      screen.queryByRole('button', { name: 'header.switchSidebarToFixed' })
    ).not.toBeInTheDocument()
  })

  it('悬浮侧栏收起动画结束后再切换折叠内容与分割线', () => {
    vi.useFakeTimers()
    const { container } = render(
      <Sidebar
        sidebarOpen={false}
        mobileMenuOpen={false}
        onMobileMenuClose={vi.fn()}
        onSidebarFix={vi.fn()}
      />
    )
    const aside = container.querySelector('[data-dashboard-sidebar="true"]') as HTMLElement

    fireEvent.pointerEnter(aside, { pointerType: 'mouse' })
    act(() => vi.advanceTimersByTime(SIDEBAR_HOVER_EXPAND_DELAY_MS))
    fireEvent.pointerLeave(aside)

    expect(aside).toHaveAttribute('data-dashboard-sidebar-visually-open', 'true')
    expect(aside).toHaveClass(
      'lg:[clip-path:inset(0_calc(var(--layout-sidebar-width)-var(--layout-sidebar-collapsed-width))_0_0)]'
    )
    expect(
      container.querySelector('[data-dashboard-sidebar-collapsed-divider="true"]')
    ).not.toBeInTheDocument()

    act(() => vi.advanceTimersByTime(220))
    expect(aside).toHaveAttribute('data-dashboard-sidebar-visually-open', 'false')
    expect(
      container.querySelector('[data-dashboard-sidebar-collapsed-divider="true"]')
    ).toBeInTheDocument()
  })
})
