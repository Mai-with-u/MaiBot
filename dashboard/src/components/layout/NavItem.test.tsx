import type { AnchorHTMLAttributes, ReactNode } from 'react'

import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { TooltipProvider } from '@/components/ui/tooltip'

import { NavItem } from './NavItem'

import type { MenuItem } from './types'

// useMatchRoute 返回的匹配函数，通过 hoisted mock 在各用例中控制激活态
const matchRouteMock = vi.hoisted(() => vi.fn())

vi.mock('@tanstack/react-router', () => ({
  useMatchRoute: () => matchRouteMock,
  // Link 简化为普通锚点，保留 to -> href 与其余属性透传（含 Radix asChild 注入的 ref）
  Link: ({
    to,
    children,
    onClick,
    ...props
  }: { to: string; children?: ReactNode } & AnchorHTMLAttributes<HTMLAnchorElement>) => (
    <a
      href={to}
      {...props}
      onClick={(event) => {
        // 阻止 jsdom 真实跳转，仅保留组件挂载的点击回调
        event.preventDefault()
        onClick?.(event)
      }}
    >
      {children}
    </a>
  ),
}))

vi.mock('react-i18next', () => ({
  // t 直接回显 key，组件文案断言使用 i18n key 本身
  useTranslation: () => ({ t: (key: string) => key }),
}))

/** 测试用图标桩：透传 className 以便断言激活态样式 */
function TestIcon({ className }: { className?: string }) {
  return <svg data-testid="nav-icon" className={className} />
}

const baseItem: MenuItem = {
  icon: TestIcon,
  label: 'sidebar.menu.modelManagement',
  path: '/config/model',
  tourId: 'sidebar-model-management',
}

function renderNavItem(overrides?: {
  item?: MenuItem
  sidebarOpen?: boolean
  onMobileMenuClose?: () => void
}) {
  const onMobileMenuClose = overrides?.onMobileMenuClose ?? vi.fn()
  const utils = render(
    <TooltipProvider>
      <ul>
        <NavItem
          item={overrides?.item ?? baseItem}
          sidebarOpen={overrides?.sidebarOpen ?? true}
          onMobileMenuClose={onMobileMenuClose}
        />
      </ul>
    </TooltipProvider>
  )
  return { ...utils, onMobileMenuClose }
}

describe('NavItem', () => {
  it('渲染指向 item.path 的链接并显示翻译后的标签', () => {
    matchRouteMock.mockReturnValue(false)
    renderNavItem()

    const link = screen.getByRole('link', { name: 'sidebar.menu.modelManagement' })
    expect(link).toHaveAttribute('href', '/config/model')
    expect(link).toHaveAttribute('data-tour', 'sidebar-model-management')
    expect(link).toHaveAttribute('data-dashboard-nav-item', 'true')
    // 匹配函数应以 item.path 作为目标路由调用
    expect(matchRouteMock).toHaveBeenCalledWith({ to: '/config/model' })
  })

  it('未激活时 data-active=false 且图标不带高亮色', () => {
    matchRouteMock.mockReturnValue(false)
    renderNavItem()

    expect(screen.getByRole('link')).toHaveAttribute('data-active', 'false')
    expect(screen.getByTestId('nav-icon')).not.toHaveClass('text-primary')
  })

  it('路由匹配时 data-active=true 且图标高亮', () => {
    // useMatchRoute 命中时返回路由参数对象（truthy）
    matchRouteMock.mockReturnValue({})
    renderNavItem()

    const link = screen.getByRole('link')
    expect(link).toHaveAttribute('data-active', 'true')
    expect(link).toHaveClass('bg-accent')
    expect(screen.getByTestId('nav-icon')).toHaveClass('text-primary')
  })

  it('点击链接时触发 onMobileMenuClose 回调', () => {
    matchRouteMock.mockReturnValue(false)
    const { onMobileMenuClose } = renderNavItem()

    fireEvent.click(screen.getByRole('link'))
    expect(onMobileMenuClose).toHaveBeenCalledTimes(1)
  })

  it('侧边栏展开时标签可见且不包裹 Tooltip', () => {
    matchRouteMock.mockReturnValue(false)
    const { container } = renderNavItem({ sidebarOpen: true })

    const label = container.querySelector('[data-dashboard-nav-label="true"]')
    expect(label).toHaveTextContent('sidebar.menu.modelManagement')
    expect(label).toHaveClass('opacity-100')
    // 展开态直接渲染链接，不经过 Radix TooltipTrigger（无 data-state）
    expect(screen.getByRole('link')).not.toHaveAttribute('data-state')
  })

  it('侧边栏收起时不再包裹 Tooltip 且标签在桌面端隐藏', () => {
    matchRouteMock.mockReturnValue(false)
    const { container } = renderNavItem({ sidebarOpen: false })

    const link = screen.getByRole('link')
    expect(link).not.toHaveAttribute('data-state')
    const label = container.querySelector('[data-dashboard-nav-label="true"]')
    expect(label).toHaveClass('lg:opacity-0')
  })

  it('未提供 tourId 时不渲染 data-tour 属性', () => {
    matchRouteMock.mockReturnValue(false)
    const itemWithoutTour: MenuItem = { icon: TestIcon, label: baseItem.label, path: baseItem.path }
    renderNavItem({ item: itemWithoutTour })

    expect(screen.getByRole('link')).not.toHaveAttribute('data-tour')
  })

  it('外部页面使用新窗口原生链接且不参与路由激活匹配', () => {
    matchRouteMock.mockClear()
    renderNavItem({
      item: {
        external: true,
        icon: TestIcon,
        label: 'sidebar.menu.statistics',
        path: '/maibot_statistics.html',
      },
    })

    const link = screen.getByRole('link', { name: 'sidebar.menu.statistics' })
    expect(link).toHaveAttribute('href', '/maibot_statistics.html')
    expect(link).toHaveAttribute('target', '_blank')
    expect(link).toHaveAttribute('rel', 'noopener noreferrer')
    expect(link).toHaveAttribute('data-active', 'false')
    expect(matchRouteMock).not.toHaveBeenCalled()
  })
})
