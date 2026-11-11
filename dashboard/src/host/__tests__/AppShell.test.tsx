import { useEffect, type ReactNode } from 'react'
import { act, render, screen, waitFor } from '@testing-library/react'
import {
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
  Outlet,
  RouterProvider,
} from '@tanstack/react-router'
import { afterEach, expect, it, vi } from 'vitest'

import { AppShell } from '@/components/app-shell'
import { setHostRuntime } from '../state'

vi.mock('@/components/layout', () => ({
  Layout: ({ children }: { children: ReactNode }) => <div>{children}</div>,
}))
vi.mock('@/lib/layout-version', () => ({ useEffectiveLayoutVersion: () => 'classic' }))
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))

afterEach(() => {
  setHostRuntime({
    host: false,
    authenticated: false,
    selected_bot_id: null,
    selected_bot_running: false,
    bots: [],
  })
})

it('停止的 Bot 从配置页返回管理页时，不在路由交接中挂载旧配置页', async () => {
  setHostRuntime({
    host: true,
    authenticated: true,
    selected_bot_id: 'bot-a',
    selected_bot_running: false,
    bots: [],
  })
  const loadBotConfig = vi.fn()
  function ConfigPage() {
    useEffect(() => {
      loadBotConfig()
    }, [])
    return <div>Bot configuration</div>
  }

  let releaseBots!: () => void
  const botsReady = new Promise<void>((resolve) => {
    releaseBots = resolve
  })
  const root = createRootRoute({ component: Outlet })
  const protectedRoute = createRoute({
    getParentRoute: () => root,
    id: 'protected',
    component: () => (
      <AppShell>
        <Outlet />
      </AppShell>
    ),
  })
  const config = createRoute({
    getParentRoute: () => protectedRoute,
    path: '/config/bot',
    component: ConfigPage,
  })
  const bots = createRoute({
    getParentRoute: () => protectedRoute,
    path: '/bots',
    beforeLoad: () => botsReady,
    component: () => <div>Bot management</div>,
  })
  const router = createRouter({
    routeTree: root.addChildren([protectedRoute.addChildren([config, bots])]),
    history: createMemoryHistory({ initialEntries: ['/config/bot'] }),
    defaultPendingMs: 10_000,
  })
  render(<RouterProvider router={router} />)
  await screen.findByRole('heading', { name: 'hostManager.selectedStopped' })
  expect(loadBotConfig).not.toHaveBeenCalled()

  let navigation!: Promise<void>
  act(() => {
    navigation = router.navigate({ to: '/bots' })
  })
  try {
    await waitFor(() => expect(router.state.location.pathname).toBe('/bots'))
    expect(router.state.matches.at(-1)?.pathname).toBe('/config/bot')
    expect(loadBotConfig).not.toHaveBeenCalled()
  } finally {
    await act(async () => {
      releaseBots()
      await navigation
    })
  }
  expect(await screen.findByText('Bot management')).toBeInTheDocument()
  expect(loadBotConfig).not.toHaveBeenCalled()
})
