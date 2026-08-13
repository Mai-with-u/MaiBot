import { forwardRef, type HTMLAttributes, type ReactNode } from 'react'

import { act, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { Layout } from './Layout'

const routerMocks = vi.hoisted(() => ({
  navigate: vi.fn(() => Promise.resolve()),
  pathname: '/',
  status: 'idle' as 'idle' | 'pending',
  subscribe: vi.fn((_event?: string, _callback?: () => void) => () => {}),
}))

const layoutMocks = vi.hoisted(() => {
  const t = (key: string, options?: { page?: string }) =>
    options?.page ? `${key}:${options.page}` : key

  return {
    t,
    announce: vi.fn(),
    checking: false,
    electron: false,
    theme: 'light' as 'light' | 'dark' | 'system',
    pageBgType: 'none' as string,
    matchesShortcut: vi.fn(() => false),
    menuSections: [] as Array<{
      title: string
      items: Array<{ path: string; label: string }>
    }>,
  }
})

vi.mock('@tanstack/react-router', () => ({
  useRouter: () => ({
    navigate: routerMocks.navigate,
    get state() {
      return { location: { pathname: routerMocks.pathname } }
    },
    subscribe: routerMocks.subscribe,
  }),
  useRouterState: ({
    select,
  }: {
    select: (state: {
      location: { pathname: string }
      status: 'idle' | 'pending'
    }) => unknown
  }) =>
    select({
      location: { pathname: routerMocks.pathname },
      status: routerMocks.status,
    }),
}))
vi.mock('motion/react', () => {
  const MotionDiv = forwardRef<
    HTMLDivElement,
    HTMLAttributes<HTMLDivElement> & {
      animate?: unknown
      initial?: unknown
      layout?: boolean | 'position' | 'size'
      transition?: unknown
      variants?: unknown
    }
  >(({ animate, initial, layout, transition, variants, ...props }, ref) => (
    <div
      ref={ref}
      data-motion-layout={layout === false ? 'false' : layout}
      data-motion-configured={
        [animate, initial, layout, transition, variants].some(Boolean) ? 'true' : undefined
      }
      {...props}
    />
  ))
  MotionDiv.displayName = 'MotionDiv'

  return {
    AnimatePresence: ({ children }: { children: ReactNode }) => children,
    motion: { div: MotionDiv },
  }
})
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: layoutMocks.t }),
}))

vi.mock('@/components/background-layer', () => ({
  BackgroundLayer: () => null,
}))
vi.mock('@/components/back-to-top', () => ({
  BackToTop: () => <div data-testid="back-to-top">BackToTop</div>,
}))
vi.mock('@/components/http-warning-banner', () => ({
  HttpWarningBanner: () => null,
}))
vi.mock('@/components/update-notice-dialog', () => ({
  UpdateNoticeDialog: () => <div data-testid="update-notice-dialog">更新公告入口</div>,
}))
vi.mock('@/components/electron/TitleBar', () => ({
  TitleBar: () => <div data-testid="electron-title-bar">TitleBar</div>,
}))
vi.mock('@/components/ui/announcer-context', () => ({
  useAnnounce: () => layoutMocks.announce,
}))
vi.mock('@/components/ui/skip-nav', () => ({
  SkipNav: () => null,
}))
vi.mock('@/components/ui/tooltip', () => ({
  TooltipProvider: ({ children }: { children: ReactNode }) => children,
}))
vi.mock('@/components/use-theme', () => ({
  useTheme: () => ({ setTheme: vi.fn(), theme: layoutMocks.theme }),
}))
vi.mock('@/hooks/use-auth', () => ({
  useAuthGuard: () => ({ checking: layoutMocks.checking }),
}))
vi.mock('@/hooks/use-background', () => ({
  useBackground: () => ({ config: { type: layoutMocks.pageBgType } }),
}))
vi.mock('@/lib/keyboard', () => ({
  matchesShortcut: layoutMocks.matchesShortcut,
}))
vi.mock('@/lib/runtime', () => ({
  isElectron: () => layoutMocks.electron,
}))
vi.mock('./Header', () => ({
  Header: ({
    actualTheme,
    mobileMenuOpen,
    onMobileMenuToggle,
    onSearchOpenChange,
    onSidebarToggle,
    onTopbarToggle,
    onWorkspaceNavigate,
    searchOpen,
    sidebarOpen,
    topbarCollapsed,
    workspaceMode,
  }: {
    actualTheme: 'light' | 'dark'
    mobileMenuOpen: boolean
    onMobileMenuToggle: () => void
    onSearchOpenChange: (open: boolean) => void
    onSidebarToggle: () => void
    onTopbarToggle: () => void
    onWorkspaceNavigate: (to: '/' | '/chat' | '/logs') => void
    searchOpen: boolean
    sidebarOpen: boolean
    topbarCollapsed: boolean
    workspaceMode: 'settings' | 'chat' | 'logs'
  }) => (
    <div
      data-testid="header"
      data-actual-theme={actualTheme}
      data-mobile-menu-open={String(mobileMenuOpen)}
      data-search-open={String(searchOpen)}
      data-sidebar-open={String(sidebarOpen)}
      data-topbar-collapsed={String(topbarCollapsed)}
      data-workspace-mode={workspaceMode}
    >
      <button type="button" onClick={onSidebarToggle}>
        切换侧栏模式
      </button>
      <button type="button" onClick={onMobileMenuToggle}>
        切换移动菜单
      </button>
      <button type="button" onClick={() => onSearchOpenChange(!searchOpen)}>
        切换搜索
      </button>
      <button type="button" onClick={onTopbarToggle}>
        切换顶栏
      </button>
      <button type="button" onClick={() => onWorkspaceNavigate('/chat')}>
        切换到麦麦聊天
      </button>
      <button type="button" onClick={() => onWorkspaceNavigate('/logs')}>
        切换到日志
      </button>
      <button type="button" onClick={() => onWorkspaceNavigate('/')}>
        切换到设置
      </button>
    </div>
  ),
}))
vi.mock('./Sidebar', () => ({
  Sidebar: ({
    onSidebarFix,
    sidebarOpen,
  }: {
    onSidebarFix: () => void
    sidebarOpen: boolean
  }) => (
    <div data-testid="sidebar" data-sidebar-open={String(sidebarOpen)}>
      <button type="button" onClick={onSidebarFix}>
        切换为固定模式
      </button>
    </div>
  ),
}))
vi.mock('./use-menu-sections', () => ({
  useMenuSections: () => layoutMocks.menuSections,
}))

function getHeader() {
  return screen.getByTestId('header')
}

function getWorkspaceContent(container: HTMLElement) {
  return container.querySelector('[data-dashboard-workspace-content="true"]')
}

function getMain() {
  return document.querySelector('[data-dashboard-main="true"]')
}

function getMobileOverlay() {
  return document.querySelector('.bg-black\\/50')
}

function flushMicrotasks() {
  return act(async () => {
    await Promise.resolve()
  })
}

describe('Layout 工作区切换', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.clearAllMocks()
    localStorage.clear()
    routerMocks.pathname = '/'
    routerMocks.status = 'idle'
    routerMocks.navigate.mockImplementation(() => Promise.resolve())
    layoutMocks.checking = false
    layoutMocks.electron = false
    layoutMocks.theme = 'light'
    layoutMocks.pageBgType = 'none'
    layoutMocks.menuSections = []
    layoutMocks.matchesShortcut.mockReturnValue(false)
    vi.spyOn(window, 'requestAnimationFrame').mockImplementation((callback) => {
      return window.setTimeout(() => callback(0), 0)
    })
    vi.spyOn(window, 'cancelAnimationFrame').mockImplementation((frameId) => {
      window.clearTimeout(frameId)
    })
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  it('目标 workspace 提交新 Outlet 前保持隐藏，避免旧首页闪现', async () => {
    const view = render(
      <Layout>
        <div>首页内容</div>
      </Layout>
    )
    // 冲刷 UpdateNoticeDialog 的 lazy import，避免首个用例在 act 外完成挂起
    await flushMicrotasks()

    fireEvent.click(screen.getByRole('button', { name: '切换到麦麦聊天' }))
    act(() => {
      vi.advanceTimersByTime(280 + 180)
    })
    expect(routerMocks.navigate).toHaveBeenCalledWith({ to: '/chat' })

    // 模拟 pathname 已切换，但 Outlet 仍短暂保留旧首页的并发提交窗口。
    routerMocks.pathname = '/chat'
    routerMocks.status = 'pending'
    view.rerender(
      <Layout>
        <div>首页内容</div>
      </Layout>
    )

    const workspaceContent = view.container.querySelector(
      '[data-dashboard-workspace-content="true"]'
    )
    expect(workspaceContent).toHaveClass('invisible')

    routerMocks.status = 'idle'
    view.rerender(
      <Layout>
        <div>聊天内容</div>
      </Layout>
    )
    act(() => {
      vi.advanceTimersByTime(0)
    })

    expect(workspaceContent).not.toHaveClass('invisible')
    expect(screen.queryByText('首页内容')).not.toBeInTheDocument()
    expect(screen.getByText('聊天内容')).toBeInTheDocument()
  })

  it('侧栏宽度使用 CSS 过渡且不启用会拉伸内容的 FLIP 尺寸缩放', () => {
    localStorage.setItem('maibot-layout-sidebar-open', 'false')
    const view = render(
      <Layout>
        <div>首页内容</div>
      </Layout>
    )

    const sidebarLayout = view.container.querySelector(
      '[data-dashboard-sidebar-layout="true"]'
    )
    expect(sidebarLayout).toHaveAttribute('data-motion-layout', 'false')
    expect(sidebarLayout).toHaveClass('transition-[width]', 'duration-[220ms]')
    expect(sidebarLayout).toHaveStyle({
      width: 'var(--layout-sidebar-collapsed-width)',
    })

    fireEvent.click(screen.getAllByRole('button', { name: '切换为固定模式' })[0])
    expect(sidebarLayout).toHaveAttribute('data-motion-layout', 'false')
    expect(sidebarLayout).toHaveClass('transition-none')
    expect(sidebarLayout).toHaveStyle({ width: 'var(--layout-sidebar-width)' })
    expect(screen.getAllByTestId('sidebar')[0]).toHaveAttribute('data-sidebar-open', 'true')

    fireEvent.click(screen.getByRole('button', { name: '切换侧栏模式' }))
    expect(sidebarLayout).toHaveAttribute('data-motion-layout', 'false')
    expect(sidebarLayout).not.toHaveClass('transition-none')
    expect(sidebarLayout).toHaveStyle({
      width: 'var(--layout-sidebar-collapsed-width)',
    })
  })

  it('离开设置工作区时先收起侧栏宽度再导航', () => {
    const { container } = render(
      <Layout>
        <div>首页内容</div>
      </Layout>
    )

    fireEvent.click(screen.getByRole('button', { name: '切换到麦麦聊天' }))
    act(() => {
      vi.advanceTimersByTime(280)
    })

    const sidebarLayout = container.querySelector('[data-dashboard-sidebar-layout="true"]')
    expect(sidebarLayout).toHaveStyle({ width: '0px' })
    expect(sidebarLayout).toHaveClass('overflow-hidden')
    expect(getMain()).toHaveClass('overflow-hidden')
    expect(routerMocks.navigate).not.toHaveBeenCalled()

    act(() => {
      vi.advanceTimersByTime(180)
    })
    expect(routerMocks.navigate).toHaveBeenCalledWith({ to: '/chat' })
  })

  it('非设置工作区切走时跳过侧栏退场，导航失败后恢复可交互', async () => {
    routerMocks.pathname = '/chat'
    routerMocks.navigate.mockImplementation(() => Promise.reject(new Error('导航失败')))
    const { container } = render(
      <Layout>
        <div>聊天内容</div>
      </Layout>
    )

    fireEvent.click(screen.getByRole('button', { name: '切换到日志' }))
    act(() => {
      vi.advanceTimersByTime(279)
    })
    expect(routerMocks.navigate).not.toHaveBeenCalled()

    act(() => {
      vi.advanceTimersByTime(1)
    })
    expect(routerMocks.navigate).toHaveBeenCalledWith({ to: '/logs' })
    await flushMicrotasks()

    expect(getWorkspaceContent(container)).not.toHaveClass('invisible')
    expect(getHeader()).toHaveAttribute('data-workspace-mode', 'chat')
  })

  it('切回设置工作区时等路由空闲后再侧栏入场', () => {
    routerMocks.pathname = '/logs'
    const view = render(
      <Layout>
        <div>日志内容</div>
      </Layout>
    )

    fireEvent.click(screen.getByRole('button', { name: '切换到设置' }))
    act(() => {
      vi.advanceTimersByTime(280)
    })
    expect(routerMocks.navigate).toHaveBeenCalledWith({ to: '/' })

    routerMocks.pathname = '/'
    routerMocks.status = 'idle'
    view.rerender(
      <Layout>
        <div>首页内容</div>
      </Layout>
    )
    act(() => {
      vi.advanceTimersByTime(0)
    })
    expect(getWorkspaceContent(view.container)).toHaveClass('invisible')
    expect(view.container.querySelector('[data-dashboard-sidebar-layout="true"]')).toBeInTheDocument()

    act(() => {
      vi.advanceTimersByTime(180)
    })
    expect(getWorkspaceContent(view.container)).not.toHaveClass('invisible')

    act(() => {
      vi.advanceTimersByTime(280)
    })
    expect(getHeader()).toHaveAttribute('data-workspace-mode', 'settings')
  })

  it('过渡进行中忽略后续工作区切换', () => {
    render(
      <Layout>
        <div>首页内容</div>
      </Layout>
    )

    fireEvent.click(screen.getByRole('button', { name: '切换到麦麦聊天' }))
    fireEvent.click(screen.getByRole('button', { name: '切换到日志' }))
    act(() => {
      vi.advanceTimersByTime(280 + 180)
    })
    expect(routerMocks.navigate).toHaveBeenCalledTimes(1)
    expect(routerMocks.navigate).toHaveBeenCalledWith({ to: '/chat' })
  })

  it('卸载时清掉未触发的导航定时器', () => {
    const { unmount } = render(
      <Layout>
        <div>首页内容</div>
      </Layout>
    )

    fireEvent.click(screen.getByRole('button', { name: '切换到日志' }))
    unmount()
    act(() => {
      vi.advanceTimersByTime(1000)
    })
    expect(routerMocks.navigate).not.toHaveBeenCalled()
  })
})

describe('Layout 壳层、快捷键与公告入口', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.clearAllMocks()
    localStorage.clear()
    routerMocks.pathname = '/'
    routerMocks.status = 'idle'
    routerMocks.navigate.mockImplementation(() => Promise.resolve())
    layoutMocks.checking = false
    layoutMocks.electron = false
    layoutMocks.theme = 'light'
    layoutMocks.pageBgType = 'none'
    layoutMocks.menuSections = []
    layoutMocks.matchesShortcut.mockReturnValue(false)
    vi.spyOn(window, 'requestAnimationFrame').mockImplementation((callback) => {
      return window.setTimeout(() => callback(0), 0)
    })
    vi.spyOn(window, 'cancelAnimationFrame').mockImplementation((frameId) => {
      window.clearTimeout(frameId)
    })
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  it('认证检查中只展示校验文案，不挂载壳层', () => {
    layoutMocks.checking = true
    render(
      <Layout>
        <div>首页内容</div>
      </Layout>
    )

    expect(screen.getByText('layout.verifyingLogin')).toBeInTheDocument()
    expect(document.querySelector('[data-dashboard-shell="true"]')).not.toBeInTheDocument()
    expect(screen.queryByTestId('update-notice-dialog')).not.toBeInTheDocument()
  })

  it('挂载更新公告入口，Electron 下补 TitleBar 与顶栏留白', async () => {
    layoutMocks.electron = true
    render(
      <Layout>
        <div>首页内容</div>
      </Layout>
    )

    await flushMicrotasks()
    expect(screen.getByTestId('update-notice-dialog')).toBeInTheDocument()
    expect(screen.getByTestId('electron-title-bar')).toBeInTheDocument()
    expect(document.querySelector('[data-dashboard-shell="true"]')).toHaveClass('pt-8')
  })

  it('非法本地存储回退默认壳层，折叠顶栏与搜索开关会写回', () => {
    localStorage.setItem('maibot-layout-sidebar-open', 'maybe')
    localStorage.setItem('maibot-layout-topbar-collapsed', 'maybe')
    render(
      <Layout>
        <div>首页内容</div>
      </Layout>
    )

    expect(getHeader()).toHaveAttribute('data-sidebar-open', 'true')
    expect(getHeader()).toHaveAttribute('data-topbar-collapsed', 'false')
    expect(getHeader()).toHaveAttribute('data-search-open', 'false')

    fireEvent.click(screen.getByRole('button', { name: '切换顶栏' }))
    fireEvent.click(screen.getByRole('button', { name: '切换搜索' }))
    expect(getHeader()).toHaveAttribute('data-topbar-collapsed', 'true')
    expect(getHeader()).toHaveAttribute('data-search-open', 'true')
    expect(localStorage.getItem('maibot-layout-topbar-collapsed')).toBe('true')
    expect(localStorage.getItem('maibot-layout-sidebar-open')).toBe('true')

    fireEvent.click(screen.getByRole('button', { name: '切换搜索' }))
    expect(getHeader()).toHaveAttribute('data-search-open', 'false')
  })

  it('命令面板快捷键命中后打开搜索，未命中则保持关闭', () => {
    render(
      <Layout>
        <div>首页内容</div>
      </Layout>
    )

    act(() => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'k', ctrlKey: true }))
    })
    expect(layoutMocks.matchesShortcut).toHaveBeenCalledWith(expect.any(KeyboardEvent), ['mod', 'k'])
    expect(getHeader()).toHaveAttribute('data-search-open', 'false')

    layoutMocks.matchesShortcut.mockReturnValue(true)
    const event = new KeyboardEvent('keydown', { key: 'k', metaKey: true, cancelable: true })
    act(() => {
      window.dispatchEvent(event)
    })
    expect(event.defaultPrevented).toBe(true)
    expect(getHeader()).toHaveAttribute('data-search-open', 'true')
  })

  it('移动端遮罩只在设置工作区打开，点击后关闭菜单', () => {
    render(
      <Layout>
        <div>首页内容</div>
      </Layout>
    )

    expect(getMobileOverlay()).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '切换移动菜单' }))
    expect(getHeader()).toHaveAttribute('data-mobile-menu-open', 'true')
    expect(getMobileOverlay()).toBeInTheDocument()

    fireEvent.click(getMobileOverlay() as Element)
    expect(getHeader()).toHaveAttribute('data-mobile-menu-open', 'false')
    expect(getMobileOverlay()).not.toBeInTheDocument()
  })

  it('沉浸模式收起壳层，再次进入时保留首次快照并在退出后恢复', () => {
    render(
      <Layout>
        <div>首页内容</div>
      </Layout>
    )

    fireEvent.click(screen.getByRole('button', { name: '切换移动菜单' }))
    act(() => {
      window.dispatchEvent(
        new CustomEvent('maibot-layout-immersive-change', { detail: { immersive: true } })
      )
    })
    expect(getHeader()).toHaveAttribute('data-sidebar-open', 'false')
    expect(getHeader()).toHaveAttribute('data-topbar-collapsed', 'true')
    expect(getHeader()).toHaveAttribute('data-mobile-menu-open', 'false')
    expect(getMobileOverlay()).not.toBeInTheDocument()
    expect(localStorage.getItem('maibot-layout-sidebar-open')).toBe('false')
    expect(localStorage.getItem('maibot-layout-topbar-collapsed')).toBe('true')

    fireEvent.click(screen.getByRole('button', { name: '切换侧栏模式' }))
    expect(getHeader()).toHaveAttribute('data-sidebar-open', 'true')

    act(() => {
      window.dispatchEvent(
        new CustomEvent('maibot-layout-immersive-change', { detail: { immersive: true } })
      )
    })
    act(() => {
      window.dispatchEvent(
        new CustomEvent('maibot-layout-immersive-change', { detail: { immersive: false } })
      )
    })
    expect(getHeader()).toHaveAttribute('data-sidebar-open', 'true')
    expect(getHeader()).toHaveAttribute('data-topbar-collapsed', 'false')
  })

  it('未进入沉浸模式时 restore 事件不改写当前壳层', () => {
    render(
      <Layout>
        <div>首页内容</div>
      </Layout>
    )

    fireEvent.click(screen.getByRole('button', { name: '切换顶栏' }))
    act(() => {
      window.dispatchEvent(new CustomEvent('maibot-layout-immersive-change'))
    })
    expect(getHeader()).toHaveAttribute('data-topbar-collapsed', 'true')
    expect(getHeader()).toHaveAttribute('data-sidebar-open', 'true')
  })

  it.each([
    ['/chat', 'chat', false],
    ['/logs', 'logs', false],
    ['/statistics', 'logs', false],
    ['/reasoning-process/detail', 'logs', false],
    ['/planner-monitor', 'settings', false],
    ['/config/bot', 'settings', true],
  ] as const)('路径 %s 映射工作区 %s，返回顶部=%s', (pathname, workspace, showBackToTop) => {
    routerMocks.pathname = pathname
    render(
      <Layout>
        <div>页面内容</div>
      </Layout>
    )

    expect(getHeader()).toHaveAttribute('data-workspace-mode', workspace)
    expect(Boolean(screen.queryByTestId('back-to-top'))).toBe(showBackToTop)
    if (workspace === 'settings') {
      expect(screen.getAllByTestId('sidebar').length).toBeGreaterThan(0)
    } else {
      expect(screen.queryByTestId('sidebar')).not.toBeInTheDocument()
    }
  })

  it('设置页无背景时主区铺底色，聊天页与自定义背景保持透明', () => {
    const settingsView = render(
      <Layout>
        <div>首页内容</div>
      </Layout>
    )
    expect(getMain()).toHaveClass('bg-background', 'overflow-y-auto')
    settingsView.unmount()

    layoutMocks.pageBgType = 'image'
    render(
      <Layout>
        <div>首页内容</div>
      </Layout>
    )
    expect(getMain()).toHaveClass('bg-transparent')
  })

  it('聊天工作区主区透明且不滚动，system 主题跟随 matchMedia', () => {
    routerMocks.pathname = '/chat'
    layoutMocks.theme = 'system'
    vi.spyOn(window, 'matchMedia').mockImplementation(
      (query) =>
        ({
          matches: query.includes('prefers-color-scheme: dark'),
          media: query,
          onchange: null,
          addListener: () => {},
          removeListener: () => {},
          addEventListener: () => {},
          removeEventListener: () => {},
          dispatchEvent: () => false,
        }) as MediaQueryList
    )

    render(
      <Layout>
        <div>聊天内容</div>
      </Layout>
    )
    expect(getMain()).toHaveClass('bg-transparent', 'overflow-hidden')
    expect(getHeader()).toHaveAttribute('data-actual-theme', 'dark')
  })

  it('路由解析后更新标题、播报并在主区外时回收焦点', () => {
    let onResolved: (() => void) | undefined
    const unsubscribe = vi.fn()
    layoutMocks.menuSections = [
      {
        title: 'sidebar.groups.botConfig',
        items: [{ path: '/config/bot', label: 'sidebar.menu.botMainConfig' }],
      },
    ]
    routerMocks.subscribe.mockImplementation((_event?: string, callback?: () => void) => {
      onResolved = callback
      return unsubscribe
    })

    const { unmount } = render(
      <Layout>
        <button type="button">内部按钮</button>
      </Layout>
    )
    expect(routerMocks.subscribe).toHaveBeenCalledWith('onResolved', expect.any(Function))

    const main = document.getElementById('main-content')
    expect(main).not.toBeNull()
    const focusSpy = vi.spyOn(main as HTMLElement, 'focus')
    document.body.tabIndex = -1
    document.body.focus()

    routerMocks.pathname = '/config/bot'
    act(() => {
      onResolved?.()
    })
    act(() => {
      vi.advanceTimersByTime(0)
    })
    expect(document.title).toBe('sidebar.menu.botMainConfig — MaiBot Dashboard')
    expect(layoutMocks.announce).toHaveBeenCalledWith(
      'a11y.navigatedTo:sidebar.menu.botMainConfig',
      'polite'
    )
    expect(focusSpy).toHaveBeenCalledWith({ preventScroll: true })

    focusSpy.mockClear()
    screen.getByRole('button', { name: '内部按钮' }).focus()
    routerMocks.pathname = '/chat'
    act(() => {
      onResolved?.()
    })
    act(() => {
      vi.advanceTimersByTime(0)
    })
    expect(document.title).toBe('workspace.chat — MaiBot Dashboard')
    expect(focusSpy).not.toHaveBeenCalled()

    routerMocks.pathname = '/unknown-page'
    act(() => {
      onResolved?.()
    })
    expect(document.title).toBe('MaiBot Dashboard')
    expect(layoutMocks.announce).toHaveBeenCalledWith(
      'a11y.navigatedTo:MaiBot Dashboard',
      'polite'
    )

    unmount()
    expect(unsubscribe).toHaveBeenCalled()
  })
})
