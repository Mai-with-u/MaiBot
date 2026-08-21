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
  DropdownMenu: ({
    children,
    open,
    onOpenChange,
  }: {
    children: ReactNode
    open?: boolean
    onOpenChange?: (open: boolean) => void
  }) => (
    <div data-dropdown-open={open === undefined ? 'uncontrolled' : String(Boolean(open))}>
      {onOpenChange ? (
        <button type="button" onClick={() => onOpenChange(!open)}>
          打开语言菜单
        </button>
      ) : null}
      {children}
    </div>
  ),
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
    TabsTrigger: ({
      asChild,
      children,
      value,
      ...props
    }: HTMLAttributes<HTMLDivElement> & { asChild?: boolean; value?: string }) => (
      <div data-workspace-tab={value} {...props}>
        {children}
      </div>
    ),
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
    document.querySelectorAll('[data-log-viewer-switcher="true"]').forEach((node) => node.remove())
    cleanup()
    vi.clearAllMocks()
    vi.restoreAllMocks()
    vi.useRealTimers()
  })

  it('触发顶栏主要操作、工作区切换、语言和主题变更', async () => {
    const props = makeProps()
    render(<Header {...props} />)

    expect(screen.getByTestId('background-header')).toBeInTheDocument()
    expect(document.querySelector('[data-dashboard-header="true"]')).toHaveClass('bg-background')
    expect(document.querySelector('[data-dashboard-header="true"]')).not.toHaveClass('bg-card/80')
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

  it('忽略无关设置事件，并在关闭专注陪伴后隐藏入口', () => {
    render(<Header {...makeProps()} />)
    expect(screen.getAllByRole('link', { name: 'sidebar.menu.focusCompanion' }).length).toBeGreaterThan(0)

    act(() => {
      window.dispatchEvent(
        new CustomEvent('maibot-settings-change', {
          detail: { key: 'theme', value: 'dark' },
        })
      )
    })
    expect(screen.getAllByRole('link', { name: 'sidebar.menu.focusCompanion' }).length).toBeGreaterThan(0)

    act(() => {
      window.dispatchEvent(new CustomEvent('maibot-settings-change'))
    })
    expect(screen.getAllByRole('link', { name: 'sidebar.menu.focusCompanion' }).length).toBeGreaterThan(0)

    act(() => {
      window.dispatchEvent(
        new CustomEvent('maibot-settings-change', {
          detail: { key: 'enableFocusCompanion', value: false },
        })
      )
    })
    expect(screen.queryAllByRole('link', { name: 'sidebar.menu.focusCompanion' })).toHaveLength(0)
  })

  it('当前页为设置且搜索打开时高亮对应顶栏按钮', () => {
    mocks.pathname = '/settings'
    const { rerender } = render(<Header {...makeProps({ searchOpen: false })} />)

    expect(document.querySelector('[data-header-action-highlighted="true"]')).toHaveAttribute(
      'aria-label',
      'sidebar.menu.settings'
    )

    rerender(<Header {...makeProps({ searchOpen: true })} />)
    expect(document.querySelector('[data-header-action-highlighted="true"]')).toHaveAttribute(
      'aria-label',
      'header.searchPlaceholder'
    )
  })

  it('语言菜单打开时高亮语言按钮，并支持日韩切换', () => {
    render(<Header {...makeProps()} />)

    fireEvent.click(screen.getByRole('button', { name: '打开语言菜单' }))
    expect(document.querySelector('[data-header-action-highlighted="true"]')).toHaveAttribute(
      'aria-label',
      'header.switchLanguage'
    )

    fireEvent.click(screen.getAllByRole('button', { name: '日本語' })[0])
    fireEvent.click(screen.getAllByRole('button', { name: '한국어' })[0])
    expect(mocks.changeLanguage).toHaveBeenCalledWith('ja')
    expect(mocks.changeLanguage).toHaveBeenCalledWith('ko')
  })

  it('亮色主题走夜间切换，移动端更多菜单也能改主题并登出', () => {
    const props = makeProps({ actualTheme: 'light' })
    render(<Header {...props} />)

    fireEvent.click(screen.getAllByRole('button', { name: 'header.switchToDark' })[0])
    fireEvent.click(screen.getAllByRole('button', { name: 'header.switchToDark' })[1])
    fireEvent.click(screen.getByRole('button', { name: 'header.logoutLabel' }))

    expect(mocks.toggleTheme).toHaveBeenCalledTimes(2)
    expect(mocks.toggleTheme).toHaveBeenNthCalledWith(1, 'dark', props.onThemeChange, expect.anything())
    expect(mocks.logout).toHaveBeenCalledOnce()
    expect(screen.getByRole('button', { name: 'header.moreActions' })).toBeInTheDocument()
  })

  it('非设置工作区隐藏移动菜单与侧栏切换，日志槽位可见', () => {
    const props = makeProps({ workspaceMode: 'logs', sidebarOpen: true })
    render(<Header {...props} />)

    expect(screen.getByRole('button', { name: 'a11y.closeMenu' })).toHaveClass('hidden')
    expect(screen.getByRole('button', { name: 'header.switchSidebarToHover' })).toHaveClass(
      'lg:hidden'
    )
    expect(document.getElementById('log-viewer-topbar-tabs')).toHaveClass('sm:flex')
  })

  it('折叠顶栏且侧栏固定时仍可切回悬浮，并保留顶栏背景层', () => {
    const props = makeProps({ topbarCollapsed: true, sidebarOpen: true })
    render(<Header {...props} />)

    expect(screen.getByTestId('background-header')).toBeInTheDocument()
    const strip = document.querySelector('[data-dashboard-header-strip="true"]')
    expect(strip).toBeInTheDocument()
    fireEvent.click(
      strip?.querySelector('[data-dashboard-sidebar-mode-switch="true"]') as HTMLButtonElement
    )
    expect(props.onSidebarToggle).toHaveBeenCalledOnce()
  })

  it('搜索已打开时再次点击会关闭，Electron 无后端名时回退未连接文案', async () => {
    mocks.electron = true
    mocks.getActiveBackend.mockResolvedValue(null)
    const props = makeProps({ searchOpen: true })
    render(<Header {...props} />)

    fireEvent.click(screen.getByRole('button', { name: 'header.searchPlaceholder' }))
    expect(props.onSearchOpenChange).toHaveBeenCalledWith(false)
    expect(await screen.findByText('header.notConnected')).toBeInTheDocument()
  })

  it('当前工作区标签的普通点击不导航，修饰键点击也不拦截切换', () => {
    const props = makeProps({ workspaceMode: 'settings' })
    render(<Header {...props} />)

    fireEvent.click(screen.getByRole('link', { name: 'workspace.settings' }))
    fireEvent.click(screen.getByRole('link', { name: 'workspace.chat' }), { metaKey: true })
    fireEvent.click(screen.getByRole('link', { name: 'workspace.logs' }), { button: 1 })
    expect(props.onWorkspaceNavigate).not.toHaveBeenCalled()
  })

  it('悬停工作区会抢占设置高亮，离开延迟后恢复，锁定后不再跟随悬停', () => {
    vi.useFakeTimers()
    mocks.pathname = '/settings'
    const props = makeProps({ workspaceMode: 'settings' })
    const { rerender, unmount } = render(<Header {...props} />)

    const chatLink = screen.getByRole('link', { name: 'workspace.chat' })
    const logsLink = screen.getByRole('link', { name: 'workspace.logs' })
    const settingsLink = screen.getByRole('link', { name: 'sidebar.menu.settings' })
    const tabs = document.querySelector('[data-dashboard-workspace-tabs="true"]') as HTMLElement

    expect(settingsLink.querySelector('[data-layout-id="topbar-selection-pill"]')).toBeInTheDocument()

    fireEvent.pointerEnter(chatLink)
    expect(chatLink.querySelector('[data-layout-id="topbar-selection-pill"]')).toBeInTheDocument()
    expect(settingsLink.querySelector('[data-layout-id="topbar-selection-pill"]')).not.toBeInTheDocument()

    fireEvent.pointerLeave(tabs)
    act(() => {
      vi.advanceTimersByTime(599)
    })
    expect(chatLink.querySelector('[data-layout-id="topbar-selection-pill"]')).toBeInTheDocument()
    act(() => {
      vi.advanceTimersByTime(1)
    })
    expect(settingsLink.querySelector('[data-layout-id="topbar-selection-pill"]')).toBeInTheDocument()

    fireEvent.pointerEnter(chatLink)
    fireEvent.click(chatLink)
    expect(props.onWorkspaceNavigate).toHaveBeenCalledWith('/chat')
    fireEvent.pointerEnter(logsLink)
    expect(chatLink.querySelector('[data-layout-id="topbar-selection-pill"]')).toBeInTheDocument()
    expect(logsLink.querySelector('[data-layout-id="topbar-selection-pill"]')).not.toBeInTheDocument()

    fireEvent.pointerLeave(tabs)
    act(() => {
      vi.advanceTimersByTime(600)
    })
    expect(chatLink.querySelector('[data-layout-id="topbar-selection-pill"]')).toBeInTheDocument()

    rerender(<Header {...makeProps({ workspaceMode: 'chat' })} />)
    fireEvent.pointerEnter(logsLink)
    expect(logsLink.querySelector('[data-layout-id="topbar-selection-pill"]')).toBeInTheDocument()

    fireEvent.pointerEnter(settingsLink)
    fireEvent.pointerLeave(settingsLink)
    unmount()
    act(() => {
      vi.advanceTimersByTime(600)
    })
  })

  it('顶栏操作悬停会清掉工作区高亮，离开后延迟消失', () => {
    vi.useFakeTimers()
    const props = makeProps()
    render(<Header {...props} />)

    const chatLink = screen.getByRole('link', { name: 'workspace.chat' })
    const searchButton = screen.getByRole('button', { name: 'header.searchPlaceholder' })

    fireEvent.pointerEnter(chatLink)
    fireEvent.pointerEnter(searchButton)
    expect(searchButton).toHaveAttribute('data-header-action-highlighted', 'true')
    expect(chatLink.querySelector('[data-layout-id="topbar-selection-pill"]')).not.toBeInTheDocument()

    fireEvent.pointerLeave(searchButton)
    act(() => {
      vi.advanceTimersByTime(600)
    })
    expect(searchButton).toHaveAttribute('data-header-action-highlighted', 'false')
  })

  it('日志工作区根据切换器间距压缩标签，并在间隙足够后恢复', () => {
    vi.useFakeTimers()
    vi.spyOn(window, 'requestAnimationFrame').mockImplementation((callback) => {
      return window.setTimeout(() => callback(0), 0)
    })
    vi.spyOn(window, 'cancelAnimationFrame').mockImplementation((id) => {
      window.clearTimeout(id)
    })

    const switcher = document.createElement('div')
    switcher.setAttribute('data-log-viewer-switcher', 'true')
    switcher.dataset.logViewerSwitcherCompact = 'true'
    document.body.appendChild(switcher)

    const { rerender, unmount } = render(<Header {...makeProps({ workspaceMode: 'logs' })} />)
    const tabs = document.querySelector('[data-dashboard-workspace-tabs="true"]') as HTMLElement
    const measure = document.querySelector(
      '[data-dashboard-workspace-tabs-measure="true"]'
    ) as HTMLElement
    const chatTab = document.querySelector('[data-workspace-tab="chat"]') as HTMLElement

    const applyRects = (tabsRight: number, measureWidth: number, switcherRight: number) => {
      vi.spyOn(tabs, 'getBoundingClientRect').mockReturnValue(
        makeDomRect({ right: tabsRight })
      )
      vi.spyOn(measure, 'getBoundingClientRect').mockReturnValue(
        makeDomRect({ width: measureWidth })
      )
      vi.spyOn(switcher, 'getBoundingClientRect').mockReturnValue(
        makeDomRect({ right: switcherRight })
      )
    }

    applyRects(400, 300, 200)
    act(() => {
      window.dispatchEvent(new Event('resize'))
      vi.advanceTimersByTime(0)
    })
    expect(chatTab).toHaveClass('px-2')
    expect(screen.getByRole('link', { name: 'workspace.chat' }).querySelector('span.hidden')).not.toHaveClass(
      'sm:inline'
    )

    // 压缩态阈值放宽到 96px，50px 间隙仍保持压缩
    applyRects(400, 150, 200)
    act(() => {
      window.dispatchEvent(new Event('resize'))
      vi.advanceTimersByTime(0)
    })
    expect(chatTab).toHaveClass('px-2')

    applyRects(400, 80, 200)
    act(() => {
      window.dispatchEvent(new Event('resize'))
      vi.advanceTimersByTime(0)
    })
    expect(chatTab).toHaveClass('px-2.5')
    expect(screen.getByRole('link', { name: 'workspace.chat' }).querySelector('span.hidden')).toHaveClass(
      'sm:inline'
    )

    switcher.style.display = 'none'
    applyRects(400, 300, 200)
    act(() => {
      window.dispatchEvent(new Event('resize'))
      vi.advanceTimersByTime(0)
    })
    expect(chatTab).toHaveClass('px-2.5')

    switcher.style.display = ''
    switcher.dataset.logViewerSwitcherCompact = 'false'
    act(() => {
      window.dispatchEvent(new Event('resize'))
      vi.advanceTimersByTime(0)
    })
    expect(chatTab).toHaveClass('px-2.5')

    rerender(<Header {...makeProps({ workspaceMode: 'settings' })} />)
    act(() => {
      vi.advanceTimersByTime(0)
    })
    expect(chatTab).toHaveClass('px-2.5')

    unmount()
    switcher.remove()
  })

  it('日志工作区缺少切换器时不压缩，并在卸载时断开尺寸观察', () => {
    vi.useFakeTimers()
    const observers: Array<{ observe: ReturnType<typeof vi.fn>; disconnect: ReturnType<typeof vi.fn> }> =
      []
    const OriginalResizeObserver = globalThis.ResizeObserver
    globalThis.ResizeObserver = class ResizeObserver {
      observe = vi.fn()
      unobserve = vi.fn()
      disconnect = vi.fn()
      constructor() {
        observers.push(this)
      }
    } as unknown as typeof ResizeObserver

    const { unmount } = render(<Header {...makeProps({ workspaceMode: 'logs' })} />)
    act(() => {
      vi.advanceTimersByTime(0)
    })
    expect(document.querySelector('[data-workspace-tab="chat"]')).toHaveClass('px-2.5')
    expect(observers[0]?.observe).toHaveBeenCalled()

    unmount()
    expect(observers[0]?.disconnect).toHaveBeenCalled()
    globalThis.ResizeObserver = OriginalResizeObserver
  })
})

function makeDomRect(overrides: Partial<DOMRect>): DOMRect {
  return {
    x: 0,
    y: 0,
    top: 0,
    left: 0,
    bottom: 0,
    width: 0,
    height: 0,
    right: 0,
    toJSON: () => ({}),
    ...overrides,
  } as DOMRect
}
