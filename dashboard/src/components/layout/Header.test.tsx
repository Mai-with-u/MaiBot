import type {
  AnchorHTMLAttributes,
  HTMLAttributes,
  MouseEvent as ReactMouseEvent,
  ReactElement,
  ReactNode,
} from 'react'

import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { Header } from './Header'
import type { WebUIExtension } from '@/lib/plugin-webui'

const mocks = vi.hoisted(() => ({
  pathname: '/',
  electron: false,
  inheritedFrom: 'header',
  language: 'zh-CN',
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
      language: mocks.language,
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

  const MotionDiv = ({
    animate: _animate,
    initial: _initial,
    transition: _transition,
    onAnimationComplete: _onAnimationComplete,
    ...props
  }: HTMLAttributes<HTMLDivElement> & {
    onAnimationComplete?: unknown
    animate?: unknown
    initial?: unknown
    transition?: unknown
  }) => <div {...props} />

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
      div: MotionDiv,
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
  SearchDialog: ({
    open,
    onOpenChange,
  }: {
    open: boolean
    onOpenChange: (open: boolean) => void
  }) =>
    open ? (
      <>
        <div>搜索对话框已打开</div>
        <button type="button" onClick={() => onOpenChange(false)}>
          关闭搜索对话框
        </button>
      </>
    ) : null,
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
  const { cloneElement, forwardRef } = await import('react')
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
    }: HTMLAttributes<HTMLDivElement> & { asChild?: boolean; value?: string }) =>
      asChild ? (
        cloneElement(children as ReactElement<Record<string, unknown>>, {
          ...props,
          role: 'tab',
          'data-workspace-tab': value,
        })
      ) : (
        <div role="tab" data-workspace-tab={value} {...props}>{children}</div>
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

function makeProps(
  overrides: Partial<Parameters<typeof Header>[0]> = {}
): Parameters<typeof Header>[0] {
  return {
    sidebarOpen: true,
    searchOpen: false,
    actualTheme: 'dark',
    onSidebarToggle: vi.fn(),
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
  it('插件顶部标签保留原始标题并通过宿主工作区切换导航', () => {
    const extension: WebUIExtension = { schema_version: 2, plugin_id: 'test.plugin', workspace_title: '消息统计', pages: [{
      id: 'overview', mode: 'declarative', title: '概览', description: '', placement: 'workspace', icon: 'chart', queries: {}, actions: {}, content: [],
    }] }
    const props = makeProps({ extensions: [extension] })
    render(<Header {...props} />)
    fireEvent.click(screen.getByRole('tab', { name: '消息统计' }))
    expect(props.onWorkspaceNavigate).toHaveBeenCalledWith('/extensions/test.plugin/overview')
    expect(mocks.t).not.toHaveBeenCalledWith('消息统计')
  })

  it('最多展示两个插件工作区，当前工作区保持可见，其余进入更多菜单', () => {
    const extensions: WebUIExtension[] = ['first', 'second', 'third'].map((plugin_id) => ({ schema_version: 2, plugin_id, workspace_title: plugin_id, pages: [{
      id: 'overview', mode: 'declarative', title: 'Overview', description: '', placement: 'workspace', icon: 'puzzle', queries: {}, actions: {}, content: [],
    }] }))
    render(<Header {...makeProps({ extensions, workspaceMode: 'plugin:third' })} />)
    expect(screen.getByRole('tab', { name: 'first' })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: 'third' })).toBeInTheDocument()
    expect(screen.queryByRole('tab', { name: 'second' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'pluginWebUI.more' })).toBeInTheDocument()
  })
  beforeEach(() => {
    mocks.pathname = '/'
    mocks.electron = false
    mocks.inheritedFrom = 'header'
    mocks.language = 'zh-CN'
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
    fireEvent.click(screen.getByRole('tab', { name: 'workspace.logs' }))

    expect(props.onSidebarToggle).toHaveBeenCalledOnce()
    expect(props.onTopbarToggle).toHaveBeenCalledOnce()
    expect(props.onSearchOpenChange).toHaveBeenCalledWith(true)
    expect(window.open).toHaveBeenCalledWith('https://docs.mai-mai.org', '_blank')
    expect(mocks.toggleTheme).toHaveBeenCalledWith('light', props.onThemeChange, expect.anything())
    expect(mocks.changeLanguage).toHaveBeenCalledWith('en')
    expect(mocks.logout).toHaveBeenCalledOnce()
    expect(props.onWorkspaceNavigate).toHaveBeenCalledWith('/logs')
  })

  it('悬浮模式不显示顶栏侧栏按钮，并尊重页面背景继承', () => {
    mocks.inheritedFrom = 'page'
    const props = makeProps({ topbarCollapsed: true, sidebarOpen: false })
    const { container } = render(<Header {...props} />)

    expect(container.querySelector('[data-dashboard-header-collapsed="true"]')).toBeInTheDocument()
    expect(screen.queryByTestId('background-header')).not.toBeInTheDocument()
    // 顶栏搜索是常驻输入框，折叠态由整块顶栏内容容器隐藏
    expect(
      container.querySelector('[data-dashboard-header-collapsed="true"] > div.hidden')
    ).toContainElement(screen.getByRole('button', { name: 'header.searchPlaceholder' }))

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

  it('搜索面板打开时不抢占顶栏高亮，当前工作区保持选中', () => {
    mocks.pathname = '/settings'
    const { rerender } = render(<Header {...makeProps({ searchOpen: false })} />)

    const settingsTab = screen.getByRole('tab', { name: 'workspace.settings' })
    expect(document.querySelector('[data-header-action-highlighted="true"]')).toBeNull()
    expect(settingsTab).toHaveAttribute('data-workspace-highlighted', 'true')

    rerender(<Header {...makeProps({ searchOpen: true })} />)
    // 顶栏搜索已改为内联输入框，没有对应的顶栏操作按钮，选中胶囊应留在当前工作区标签上
    expect(document.querySelector('[data-header-action-highlighted="true"]')).toBeNull()
    expect(settingsTab).toHaveAttribute('data-workspace-highlighted', 'true')
    expect(settingsTab.querySelector('[data-layout-id="topbar-selection-pill"]')).toBeInTheDocument()
    expect(document.querySelectorAll('[data-layout-id="topbar-selection-pill"]')).toHaveLength(1)
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

  it('日志工作区隐藏侧栏切换，日志槽位可见；手机导航交给底部标签栏，顶栏不再有菜单按钮', () => {
    const props = makeProps({ workspaceMode: 'logs', sidebarOpen: true })
    render(<Header {...props} />)

    expect(screen.queryByRole('button', { name: 'a11y.closeMenu' })).not.toBeInTheDocument()
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

  it('搜索已打开时再次提交保持打开、由搜索对话框关闭，Electron 无后端名时回退未连接文案', async () => {
    mocks.electron = true
    mocks.getActiveBackend.mockResolvedValue(null)
    const props = makeProps({ searchOpen: true })
    render(<Header {...props} />)

    fireEvent.click(screen.getByRole('button', { name: 'header.searchPlaceholder' }))
    expect(props.onSearchOpenChange).toHaveBeenCalledTimes(1)
    expect(props.onSearchOpenChange).toHaveBeenLastCalledWith(true)
    fireEvent.click(await screen.findByRole('button', { name: '关闭搜索对话框' }))
    expect(props.onSearchOpenChange).toHaveBeenLastCalledWith(false)
    expect(await screen.findByText('header.notConnected')).toBeInTheDocument()
  })

  it('当前工作区标签的普通点击不导航，修饰键点击也不拦截切换', () => {
    const props = makeProps({ workspaceMode: 'settings' })
    render(<Header {...props} />)

    fireEvent.click(screen.getByRole('tab', { name: 'workspace.settings' }))
    fireEvent.click(screen.getByRole('tab', { name: 'workspace.logs' }), { metaKey: true })
    fireEvent.click(screen.getByRole('tab', { name: 'workspace.logs' }), { button: 1 })
    expect(props.onWorkspaceNavigate).not.toHaveBeenCalled()
  })

  it('悬停工作区会抢占当前标签高亮，离开延迟后恢复，锁定后不再跟随悬停', () => {
    vi.useFakeTimers()
    mocks.pathname = '/settings'
    const props = makeProps({ workspaceMode: 'settings' })
    const { rerender, unmount } = render(<Header {...props} />)

    const logsTab = screen.getByRole('tab', { name: 'workspace.logs' })
    const settingsTab = screen.getByRole('tab', { name: 'workspace.settings' })
    const settingsLink = settingsTab
    const tabs = document.querySelector('[data-dashboard-workspace-tabs="true"]') as HTMLElement

    expect(settingsLink.querySelector('[data-layout-id="topbar-selection-pill"]')).toBeInTheDocument()

    fireEvent.pointerEnter(logsTab)
    expect(logsTab.querySelector('[data-layout-id="topbar-selection-pill"]')).toBeInTheDocument()
    expect(settingsLink.querySelector('[data-layout-id="topbar-selection-pill"]')).not.toBeInTheDocument()

    fireEvent.pointerLeave(tabs)
    act(() => {
      vi.advanceTimersByTime(599)
    })
    expect(logsTab.querySelector('[data-layout-id="topbar-selection-pill"]')).toBeInTheDocument()
    act(() => {
      vi.advanceTimersByTime(1)
    })
    expect(settingsLink.querySelector('[data-layout-id="topbar-selection-pill"]')).toBeInTheDocument()

    fireEvent.pointerEnter(logsTab)
    fireEvent.click(logsTab)
    expect(props.onWorkspaceNavigate).toHaveBeenCalledWith('/logs')
    fireEvent.pointerEnter(settingsTab)
    expect(logsTab.querySelector('[data-layout-id="topbar-selection-pill"]')).toBeInTheDocument()
    expect(settingsTab.querySelector('[data-layout-id="topbar-selection-pill"]')).not.toBeInTheDocument()

    fireEvent.pointerLeave(tabs)
    act(() => {
      vi.advanceTimersByTime(600)
    })
    expect(logsTab.querySelector('[data-layout-id="topbar-selection-pill"]')).toBeInTheDocument()

    rerender(<Header {...makeProps({ workspaceMode: 'logs' })} />)
    fireEvent.pointerEnter(settingsTab)
    expect(settingsTab.querySelector('[data-layout-id="topbar-selection-pill"]')).toBeInTheDocument()

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

    const logsTab = screen.getByRole('tab', { name: 'workspace.logs' })
    const docsButton = screen.getByRole('button', { name: 'header.viewDocs' })

    fireEvent.pointerEnter(logsTab)
    fireEvent.pointerEnter(docsButton)
    expect(docsButton).toHaveAttribute('data-header-action-highlighted', 'true')
    expect(logsTab.querySelector('[data-layout-id="topbar-selection-pill"]')).not.toBeInTheDocument()

    fireEvent.pointerLeave(docsButton)
    act(() => {
      vi.advanceTimersByTime(600)
    })
    expect(docsButton).toHaveAttribute('data-header-action-highlighted', 'false')
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
    const logsTab = screen.getByRole('tab', { name: 'workspace.logs' }) as HTMLElement

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
    expect(logsTab).toHaveClass('px-1.5')
    expect(screen.getByRole('tab', { name: 'workspace.logs' }).querySelector('span.hidden')).not.toHaveClass(
      'sm:inline'
    )

    // 压缩态阈值放宽到 96px，50px 间隙仍保持压缩
    applyRects(400, 150, 200)
    act(() => {
      window.dispatchEvent(new Event('resize'))
      vi.advanceTimersByTime(0)
    })
    expect(logsTab).toHaveClass('px-1.5')

    applyRects(400, 80, 200)
    act(() => {
      window.dispatchEvent(new Event('resize'))
      vi.advanceTimersByTime(0)
    })
    expect(logsTab).toHaveClass('px-2')
    expect(screen.getByRole('tab', { name: 'workspace.logs' }).querySelector('span.hidden')).toHaveClass(
      'sm:inline'
    )

    switcher.style.display = 'none'
    applyRects(400, 300, 200)
    act(() => {
      window.dispatchEvent(new Event('resize'))
      vi.advanceTimersByTime(0)
    })
    expect(logsTab).toHaveClass('px-2')

    switcher.style.display = ''
    switcher.dataset.logViewerSwitcherCompact = 'false'
    act(() => {
      window.dispatchEvent(new Event('resize'))
      vi.advanceTimersByTime(0)
    })
    expect(logsTab).toHaveClass('px-2')

    rerender(<Header {...makeProps({ workspaceMode: 'settings' })} />)
    act(() => {
      vi.advanceTimersByTime(0)
    })
    expect(logsTab).toHaveClass('px-2')

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
    expect(screen.getByRole('tab', { name: 'workspace.logs' })).toHaveClass('px-2')
    expect(observers[0]?.observe).toHaveBeenCalled()

    unmount()
    expect(observers[0]?.disconnect).toHaveBeenCalled()
    globalThis.ResizeObserver = OriginalResizeObserver
  })

  it('工作区离开定时器可被再次离开/进入/点击打断，卸载时清掉未完成定时器', () => {
    vi.useFakeTimers()
    const props = makeProps({ workspaceMode: 'settings' })
    const { rerender, unmount } = render(<Header {...props} />)

    const logsTab = screen.getByRole('tab', { name: 'workspace.logs' })
    const settingsTab = screen.getByRole('tab', { name: 'workspace.settings' })
    const tabs = document.querySelector('[data-dashboard-workspace-tabs="true"]') as HTMLElement

    fireEvent.pointerEnter(logsTab)
    fireEvent.pointerLeave(tabs)
    fireEvent.pointerLeave(tabs)
    fireEvent.pointerEnter(settingsTab)
    expect(settingsTab.querySelector('[data-layout-id="topbar-selection-pill"]')).toBeInTheDocument()

    fireEvent.pointerLeave(tabs)
    fireEvent.click(logsTab)
    expect(props.onWorkspaceNavigate).toHaveBeenCalledWith('/logs')

    rerender(<Header {...makeProps({ workspaceMode: 'logs' })} />)
    fireEvent.pointerEnter(settingsTab)
    fireEvent.pointerLeave(tabs)
    unmount()
    act(() => {
      vi.advanceTimersByTime(600)
    })
  })

  it('顶栏按钮悬停定时器可被再次进入打断，并触发工作区/语言/文档等剩余回调', () => {
    vi.useFakeTimers()
    const props = makeProps()
    render(<Header {...props} />)

    const settingsAction = screen.getByTitle('sidebar.menu.settings')
    const docsButton = screen.getByRole('button', { name: 'header.viewDocs' })
    const languageButton = screen.getByRole('button', { name: 'header.switchLanguage' })
    const themeButton = screen.getAllByRole('button', { name: 'header.switchToLight' })[0]
    const logoutButton = screen.getByRole('button', { name: 'header.logout' })
    const settingsLink = screen.getByRole('tab', { name: 'workspace.settings' })

    fireEvent.pointerEnter(settingsAction)
    fireEvent.pointerLeave(settingsAction)
    fireEvent.pointerLeave(settingsAction)
    fireEvent.pointerEnter(settingsAction)
    // 再次进入会清掉未完成的离开定时器，高亮不会被延迟清除
    act(() => {
      vi.advanceTimersByTime(600)
    })
    expect(settingsAction).toHaveAttribute('data-header-action-highlighted', 'true')

    fireEvent.pointerEnter(docsButton)
    fireEvent.pointerLeave(docsButton)
    fireEvent.pointerEnter(languageButton)
    fireEvent.click(languageButton)
    fireEvent.pointerEnter(themeButton)
    fireEvent.pointerLeave(themeButton)
    fireEvent.pointerEnter(logoutButton)
    fireEvent.pointerLeave(logoutButton)
    fireEvent.click(settingsLink)
    fireEvent.click(screen.getAllByRole('button', { name: 'English' })[1])

    expect(settingsLink).toHaveAttribute('href', '/')
    expect(mocks.changeLanguage).toHaveBeenCalledWith('en')

    act(() => {
      vi.advanceTimersByTime(600)
    })
  })

  it('日志工作区在动画帧内缺少切换器时保持未压缩', () => {
    vi.useFakeTimers()
    vi.spyOn(window, 'requestAnimationFrame').mockImplementation((callback) => {
      return window.setTimeout(() => callback(0), 0)
    })
    vi.spyOn(window, 'cancelAnimationFrame').mockImplementation((id) => {
      window.clearTimeout(id)
    })

    render(<Header {...makeProps({ workspaceMode: 'logs' })} />)
    act(() => {
      vi.advanceTimersByTime(0)
    })
    expect(screen.getByRole('tab', { name: 'workspace.logs' })).toHaveClass('px-2')
  })

  it('折叠顶栏在非设置工作区隐藏侧栏按钮，深色更多菜单走对应样式', () => {
    const collapsedProps = makeProps({
      topbarCollapsed: true,
      sidebarOpen: true,
      workspaceMode: 'logs',
      actualTheme: 'dark',
    })
    const { rerender } = render(<Header {...collapsedProps} />)

    const strip = document.querySelector('[data-dashboard-header-strip="true"]')
    expect(
      strip?.querySelector('[data-dashboard-sidebar-mode-switch="true"]')
    ).toHaveClass('lg:hidden')

    const expandedProps = makeProps({
      topbarCollapsed: false,
      sidebarOpen: true,
      workspaceMode: 'logs',
      actualTheme: 'dark',
    })
    rerender(<Header {...expandedProps} />)

    fireEvent.click(screen.getAllByRole('button', { name: 'header.switchToLight' })[1])
    expect(mocks.toggleTheme).toHaveBeenCalledWith(
      'light',
      expandedProps.onThemeChange,
      expect.anything()
    )
  })

  it('语言代码为空时回退中文并勾选中文项', () => {
    mocks.language = ''
    render(<Header {...makeProps()} />)
    const zhItem = screen.getAllByRole('button', { name: /中文/ })[0]
    expect(zhItem.querySelector('svg')).toBeTruthy()
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
