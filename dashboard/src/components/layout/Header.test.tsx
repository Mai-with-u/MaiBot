import type {
  AnchorHTMLAttributes,
  HTMLAttributes,
  MouseEvent as ReactMouseEvent,
  ReactNode,
} from 'react'

import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { Header } from './Header'

const mocks = vi.hoisted(() => ({
  pathname: '/',
  electron: false,
  inheritedFrom: 'header',
  focusCompanion: true,
  t: vi.fn((key: string) => key),
  changeLanguage: vi.fn(),
  getActiveBackend: vi.fn(),
  logout: vi.fn(),
  toggleTheme: vi.fn(),
}))

vi.mock('@tanstack/react-router', () => ({
  Link: ({
    to,
    children,
    onClick,
    ...props
  }: { to: string; children: ReactNode } & AnchorHTMLAttributes<HTMLAnchorElement>) => (
    <a
      href={to}
      {...props}
      onClick={(event) => {
        event.preventDefault()
        onClick?.(event)
      }}
    >
      {children}
    </a>
  ),
  useRouterState: ({
    select,
  }: {
    select: (state: { location: { pathname: string } }) => unknown
  }) => select({ location: { pathname: mocks.pathname } }),
}))

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: mocks.t,
    i18n: {
      language: 'zh-CN',
      changeLanguage: mocks.changeLanguage,
    },
  }),
}))

vi.mock('motion/react', async () => {
  const { forwardRef } = await import('react')

  const MotionHeader = forwardRef<
    HTMLElement,
    HTMLAttributes<HTMLElement> & {
      animate?: unknown
      initial?: unknown
      transition?: unknown
    }
  >(({ animate, initial, transition, ...props }, ref) => (
    <header
      ref={ref}
      data-motion={animate || initial || transition ? 'true' : undefined}
      {...props}
    />
  ))
  MotionHeader.displayName = 'MotionHeader'

  const MotionSpan = ({
    children,
    layoutId,
    transition,
    ...props
  }: HTMLAttributes<HTMLSpanElement> & {
    layoutId?: string
    transition?: unknown
  }) => (
    <span data-layout-id={layoutId} data-transition={transition ? 'true' : undefined} {...props}>
      {children}
    </span>
  )

  return {
    LayoutGroup: ({ children }: { children: ReactNode }) => <>{children}</>,
    motion: {
      header: MotionHeader,
      span: MotionSpan,
    },
  }
})

vi.mock('@/components/background-layer', () => ({
  BackgroundLayer: ({ layerId }: { layerId: string }) => (
    <div data-testid={`background-${layerId}`} />
  ),
}))

vi.mock('@/components/electron/BackendManager', () => ({
  BackendManager: ({ open }: { open: boolean }) => (open ? <div>后端管理器已打开</div> : null),
}))

vi.mock('@/components/search-dialog', () => ({
  SearchDialog: ({ open }: { open: boolean }) => (open ? <div>搜索对话框已打开</div> : null),
}))

vi.mock('@/components/ui/dropdown-menu', () => ({
  DropdownMenu: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  DropdownMenuTrigger: ({ children }: { children: ReactNode }) => <>{children}</>,
  DropdownMenuContent: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  DropdownMenuItem: ({
    children,
    onClick,
  }: {
    children: ReactNode
    onClick?: (event: ReactMouseEvent<HTMLButtonElement>) => void
  }) => (
    <button type="button" onClick={onClick}>
      {children}
    </button>
  ),
  DropdownMenuSeparator: () => <hr />,
  DropdownMenuSub: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  DropdownMenuSubContent: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  DropdownMenuSubTrigger: ({ children }: { children: ReactNode }) => <div>{children}</div>,
}))

vi.mock('@/components/ui/tabs', async () => {
  const { forwardRef } = await import('react')
  const TabsList = forwardRef<HTMLDivElement, HTMLAttributes<HTMLDivElement>>((props, ref) => (
    <div ref={ref} {...props} />
  ))
  TabsList.displayName = 'TabsList'

  return {
    Tabs: ({ children }: { children: ReactNode }) => <div>{children}</div>,
    TabsList,
    TabsTrigger: ({ children }: { children: ReactNode }) => <>{children}</>,
  }
})

vi.mock('@/components/use-theme', () => ({
  toggleThemeWithTransition: mocks.toggleTheme,
}))

vi.mock('@/hooks/use-background', () => ({
  useBackground: () => ({
    config: { type: 'color', color: '#123456' },
    inheritedFrom: mocks.inheritedFrom,
  }),
}))

vi.mock('@/lib/auth', () => ({
  logout: mocks.logout,
}))

vi.mock('@/lib/runtime', () => ({
  isElectron: () => mocks.electron,
}))

vi.mock('@/lib/settings-manager', () => ({
  DEFAULT_SETTINGS: { enableFocusCompanion: false },
  getSetting: () => mocks.focusCompanion,
}))

function makeProps(
  overrides: Partial<Parameters<typeof Header>[0]> = {}
): Parameters<typeof Header>[0] {
  return {
    sidebarOpen: true,
    mobileMenuOpen: false,
    searchOpen: false,
    actualTheme: 'dark',
    onSidebarToggle: vi.fn(),
    onMobileMenuToggle: vi.fn(),
    onSearchOpenChange: vi.fn(),
    onThemeChange: vi.fn(),
    onTopbarToggle: vi.fn(),
    onWorkspaceNavigate: vi.fn(),
    topbarCollapsed: false,
    workspaceMode: 'settings',
    ...overrides,
  }
}

describe('Header', () => {
  beforeEach(() => {
    mocks.pathname = '/'
    mocks.electron = false
    mocks.inheritedFrom = 'header'
    mocks.focusCompanion = true
    mocks.getActiveBackend.mockResolvedValue({ name: '本地后端' })
    mocks.logout.mockResolvedValue(undefined)
    vi.spyOn(window, 'open').mockImplementation(() => null)
    Object.defineProperty(window, 'electronAPI', {
      configurable: true,
      value: { getActiveBackend: mocks.getActiveBackend },
    })
  })

  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
    vi.restoreAllMocks()
  })

  it('触发顶栏主要操作、工作区切换、语言和主题变更', async () => {
    const props = makeProps()
    render(<Header {...props} />)

    expect(screen.getByTestId('background-header')).toBeInTheDocument()
    expect(screen.getAllByRole('link', { name: 'sidebar.menu.focusCompanion' })[0]).toHaveAttribute(
      'href',
      '/focus'
    )

    fireEvent.click(screen.getByRole('button', { name: 'a11y.closeMenu' }))
    const sidebarModeButton = screen.getByRole('button', {
      name: 'header.switchSidebarToHover',
    })
    expect(sidebarModeButton.querySelector('svg')).toHaveClass('lucide-chevron-left', 'h-5', 'w-5')
    fireEvent.click(sidebarModeButton)
    fireEvent.click(screen.getByRole('button', { name: 'header.collapseTopbar' }))
    fireEvent.click(screen.getByRole('button', { name: 'header.searchPlaceholder' }))
    fireEvent.click(screen.getByRole('button', { name: 'header.viewDocs' }))
    fireEvent.click(screen.getAllByRole('button', { name: 'header.switchToLight' })[0])
    fireEvent.click(screen.getAllByRole('button', { name: 'English' })[0])
    fireEvent.click(screen.getByRole('button', { name: 'header.logout' }))
    fireEvent.click(screen.getByRole('link', { name: 'workspace.chat' }))

    expect(props.onMobileMenuToggle).toHaveBeenCalledOnce()
    expect(props.onSidebarToggle).toHaveBeenCalledOnce()
    expect(props.onTopbarToggle).toHaveBeenCalledOnce()
    expect(props.onSearchOpenChange).toHaveBeenCalledWith(true)
    expect(window.open).toHaveBeenCalledWith('https://docs.mai-mai.org', '_blank')
    expect(mocks.toggleTheme).toHaveBeenCalledWith('light', props.onThemeChange, expect.anything())
    expect(mocks.changeLanguage).toHaveBeenCalledWith('en')
    expect(mocks.logout).toHaveBeenCalledOnce()
    expect(props.onWorkspaceNavigate).toHaveBeenCalledWith('/chat')
  })

  it('响应专注陪伴设置事件，并在重置事件后恢复默认隐藏', () => {
    mocks.focusCompanion = false
    render(<Header {...makeProps()} />)

    expect(screen.queryAllByRole('link', { name: 'sidebar.menu.focusCompanion' })).toHaveLength(0)

    act(() => {
      window.dispatchEvent(
        new CustomEvent('maibot-settings-change', {
          detail: { key: 'enableFocusCompanion', value: true },
        })
      )
    })
    expect(screen.getAllByRole('link', { name: 'sidebar.menu.focusCompanion' })).not.toHaveLength(0)

    act(() => {
      window.dispatchEvent(new Event('maibot-settings-reset'))
    })
    expect(screen.queryAllByRole('link', { name: 'sidebar.menu.focusCompanion' })).toHaveLength(0)
  })

  it('悬浮模式不显示顶栏侧栏按钮，并尊重页面背景继承', () => {
    mocks.inheritedFrom = 'page'
    const props = makeProps({ topbarCollapsed: true, sidebarOpen: false })
    const { container } = render(<Header {...props} />)

    expect(container.querySelector('[data-dashboard-header-collapsed="true"]')).toBeInTheDocument()
    expect(screen.queryByTestId('background-header')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'header.searchPlaceholder' })).toHaveClass('hidden')

    expect(screen.queryByRole('button', { name: 'header.expandSidebar' })).not.toBeInTheDocument()
    fireEvent.click(screen.getAllByRole('button', { name: 'header.expandTopbar' })[0])
    expect(props.onSidebarToggle).not.toHaveBeenCalled()
    expect(props.onTopbarToggle).toHaveBeenCalledOnce()
  })

  it('Electron 环境读取活动后端，并能打开后端管理器', async () => {
    mocks.electron = true
    render(<Header {...makeProps()} />)

    expect(await screen.findByText('本地后端')).toBeInTheDocument()
    expect(mocks.getActiveBackend).toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: /本地后端/ }))
    expect(screen.getByText('后端管理器已打开')).toBeInTheDocument()
  })

  it('搜索状态打开时加载懒加载对话框', async () => {
    render(<Header {...makeProps({ searchOpen: true })} />)
    await waitFor(() => expect(screen.getByText('搜索对话框已打开')).toBeInTheDocument())
  })
})
