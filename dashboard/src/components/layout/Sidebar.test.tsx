import type { ReactNode } from 'react'

import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { LogoArea } from './LogoArea'
import { Sidebar } from './Sidebar'

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
  ScrollArea: ({ children }: { children: ReactNode }) => <div>{children}</div>,
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
  })

  it('展开 Logo 时展示完整标题和版本，折叠时增加桌面简写', () => {
    const { rerender } = render(<LogoArea sidebarOpen />)

    expect(screen.getAllByText('MaiBot WebUI')).toHaveLength(2)
    expect(screen.queryByText('M')).not.toBeInTheDocument()

    rerender(<LogoArea sidebarOpen={false} />)
    expect(screen.getByText('M')).toHaveClass('lg:block')
    expect(screen.getAllByText('MaiBot WebUI')[0].parentElement).toHaveClass('lg:hidden')
  })

  it('展开侧栏时渲染独立背景、菜单分组和展开态导航项', () => {
    const onMobileMenuClose = vi.fn()
    const { container } = render(
      <Sidebar sidebarOpen mobileMenuOpen onMobileMenuClose={onMobileMenuClose} />
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
      <Sidebar sidebarOpen={false} mobileMenuOpen={false} onMobileMenuClose={vi.fn()} />
    )

    const aside = container.querySelector('[data-dashboard-sidebar="true"]')
    expect(aside).toHaveClass('bg-transparent', '-translate-x-full')
    expect(screen.queryByTestId('background-sidebar')).not.toBeInTheDocument()
    expect(screen.getByText('M')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '表情包' })).toHaveAttribute(
      'data-sidebar-open',
      'false'
    )
    expect(container.querySelector('.border-t')).toBeInTheDocument()
  })
})
