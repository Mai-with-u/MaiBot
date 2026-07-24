import { forwardRef, type HTMLAttributes, type ReactNode } from 'react'

import { act, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { Layout } from './Layout'

const routerMocks = vi.hoisted(() => ({
  navigate: vi.fn(() => Promise.resolve()),
  pathname: '/',
  status: 'idle' as 'idle' | 'pending',
  subscribe: vi.fn(() => () => {}),
}))

vi.mock('@tanstack/react-router', () => ({
  useRouter: () => ({
    navigate: routerMocks.navigate,
    state: { location: { pathname: routerMocks.pathname } },
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
      transition?: unknown
      variants?: unknown
    }
  >(({ animate, initial, transition, variants, ...props }, ref) => (
    <div
      ref={ref}
      data-motion-configured={
        [animate, initial, transition, variants].some(Boolean) ? 'true' : undefined
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
  useTranslation: () => ({ t: (key: string) => key }),
}))

vi.mock('@/components/background-layer', () => ({
  BackgroundLayer: () => null,
}))
vi.mock('@/components/back-to-top', () => ({
  BackToTop: () => null,
}))
vi.mock('@/components/http-warning-banner', () => ({
  HttpWarningBanner: () => null,
}))
vi.mock('@/components/update-notice-dialog', () => ({
  UpdateNoticeDialog: () => null,
}))
vi.mock('@/components/electron/TitleBar', () => ({
  TitleBar: () => null,
}))
vi.mock('@/components/ui/announcer', () => ({
  useAnnounce: () => vi.fn(),
}))
vi.mock('@/components/ui/skip-nav', () => ({
  SkipNav: () => null,
}))
vi.mock('@/components/ui/tooltip', () => ({
  TooltipProvider: ({ children }: { children: ReactNode }) => children,
}))
vi.mock('@/components/use-theme', () => ({
  useTheme: () => ({ setTheme: vi.fn(), theme: 'light' }),
}))
vi.mock('@/hooks/use-auth', () => ({
  useAuthGuard: () => ({ checking: false }),
}))
vi.mock('@/hooks/use-background', () => ({
  useBackground: () => ({ config: { type: 'none' } }),
}))
vi.mock('@/lib/keyboard', () => ({
  matchesShortcut: () => false,
}))
vi.mock('@/lib/runtime', () => ({
  isElectron: () => false,
}))
vi.mock('./Header', () => ({
  Header: ({
    onWorkspaceNavigate,
  }: {
    onWorkspaceNavigate: (to: '/' | '/chat' | '/logs') => void
  }) => (
    <button type="button" onClick={() => onWorkspaceNavigate('/chat')}>
      切换到麦麦聊天
    </button>
  ),
}))
vi.mock('./Sidebar', () => ({
  Sidebar: () => <div>侧栏</div>,
}))
vi.mock('./use-menu-sections', () => ({
  useMenuSections: () => [],
}))

describe('Layout 工作区切换', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.clearAllMocks()
    routerMocks.pathname = '/'
    routerMocks.status = 'idle'
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

  it('目标 workspace 提交新 Outlet 前保持隐藏，避免旧首页闪现', () => {
    const view = render(
      <Layout>
        <div>首页内容</div>
      </Layout>
    )

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
})
