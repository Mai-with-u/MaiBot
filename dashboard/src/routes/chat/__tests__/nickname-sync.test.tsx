import '@/i18n'
import { QueryClientProvider } from '@tanstack/react-query'
import {
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
  Outlet,
  RouterProvider,
} from '@tanstack/react-router'
import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import { StrictMode } from 'react'
import { expect, it, vi } from 'vitest'

import { BotConfigQuerySync } from '@/components/bot-config-query-sync'
import { getHostRuntime, setHostRuntime } from '@/host/state'
import { BOT_CONFIG_QUERY_KEY, BOT_CONFIG_UPDATED_EVENT, updateBotConfig } from '@/lib/config-api'
import { createQueryClient } from '@/lib/query'

import { ChatPage } from '..'

const bot = vi.hoisted(() => ({
  configuredName: '麦麦',
  sessionName: '麦麦',
  sessions: new Map<string, (message: Record<string, unknown>) => void>(),
}))

vi.mock('@/components/tour/use-tour', () => ({ useTour: () => ({ startTour: vi.fn() }) }))

vi.mock('@/lib/maisaka-monitor-client', () => ({
  maisakaMonitorClient: {
    subscribe: async () => async () => {},
    onConnectionChange: () => () => {},
  },
}))

vi.mock('@/lib/chat-ws-client', () => ({
  chatWsClient: {
    onConnectionChange: () => () => {},
    onSessionMessage: (id: string, listener: (message: Record<string, unknown>) => void) => {
      bot.sessions.set(id, listener)
      return () => bot.sessions.delete(id)
    },
    openSession: async (id: string) => {
      bot.sessions.get(id)?.({ type: 'session_info', bot_name: bot.sessionName })
    },
    releaseSession: () => {},
  },
}))

it.each([200, 503])('opens and saves with one config read each, then shows the new nickname after a cross-tab save (GET %i)', async (crossTabReadStatus) => {
  const previousRuntime = getHostRuntime()
  setHostRuntime({ ...previousRuntime, host: true, selected_bot_id: 'bot-a', selected_bot_running: true })
  const storageKey = `${BOT_CONFIG_UPDATED_EVENT}:bot:bot-a`
  let configReadStatus = 200
  const fetchMock = vi.fn(async (url: string, options?: RequestInit) => {
    if (url.endsWith('/api/webui/config/bot')) {
      if (options?.method === 'POST') {
        bot.configuredName = JSON.parse(String(options.body)).bot.nickname
        return Response.json({ success: true })
      }
      if (configReadStatus === 503) {
        return Response.json({ detail: 'Configuration is temporarily unavailable' }, { status: 503 })
      }
      return Response.json({ config: { bot: { nickname: bot.configuredName } } })
    }
    if (url.includes('/api/chat/sessions?')) return Response.json({ sessions: [] })
    throw new Error(`Unexpected request: ${url}`)
  })
  vi.stubGlobal('fetch', fetchMock)
  const configReads = () => fetchMock.mock.calls.filter(
    ([url, options]) => url.endsWith('/api/webui/config/bot') && (options?.method ?? 'GET') === 'GET',
  ).length

  const queryClient = createQueryClient()
  const root = createRootRoute({ component: Outlet })
  const chat = createRoute({ getParentRoute: () => root, path: '/chat', component: ChatPage })
  const settings = createRoute({ getParentRoute: () => root, path: '/settings', component: () => <div>Settings</div> })
  const router = createRouter({
    routeTree: root.addChildren([chat, settings]),
    history: createMemoryHistory({ initialEntries: ['/chat'] }),
  })
  const otherTab = document.createElement('iframe')
  Object.defineProperty(HTMLElement.prototype, 'scrollTo', { configurable: true, value: () => {} })

  try {
    // Restore the mocked backend and clear the request cache before measuring a fresh page open.
    bot.sessionName = '麦麦'
    await updateBotConfig({ bot: { nickname: '麦麦' } })
    localStorage.removeItem(storageKey)
    fetchMock.mockClear()

    const { container } = render(
      <StrictMode>
        <QueryClientProvider client={queryClient}>
          <BotConfigQuerySync />
          <RouterProvider router={router} />
        </QueryClientProvider>
      </StrictMode>,
    )
    const title = () => container.querySelector('[data-conversation-heading]')
    await waitFor(() => expect(title()).toHaveTextContent('麦麦'))
    await waitFor(() => expect(queryClient.isFetching()).toBe(0))
    expect(configReads()).toBe(1)

    await act(async () => {
      await updateBotConfig({ bot: { nickname: '小满' } })
    })
    await waitFor(() => expect(title()).toHaveTextContent('小满'))
    await waitFor(() => expect(queryClient.isFetching()).toBe(0))
    expect(configReads()).toBe(2)
    expect(localStorage.getItem(storageKey)).not.toBeNull()

    await act(async () => { await router.navigate({ to: '/settings' }) })
    await screen.findByText('Settings')
    expect(title()).toBeNull()

    document.body.append(otherTab)
    const otherWindow = otherTab.contentWindow
    if (!otherWindow) throw new Error('Could not create a second browser document')
    const updateFromOtherTab = async (key: string, value: string) => {
      const delivered = new Promise<void>((resolve) => {
        window.addEventListener('storage', () => resolve(), { once: true })
      })
      otherWindow.localStorage.setItem(key, value)
      await delivered
    }

    await act(() => updateFromOtherTab(`${BOT_CONFIG_UPDATED_EVENT}:bot:bot-b`, 'different-bot-save'))
    expect(queryClient.getQueryState(BOT_CONFIG_QUERY_KEY)?.isInvalidated).toBe(false)

    bot.configuredName = '春分'
    bot.sessionName = '春分'
    configReadStatus = crossTabReadStatus
    const otherTabValue = 'another-tab-save'
    await act(() => updateFromOtherTab(storageKey, otherTabValue))
    expect(configReads()).toBe(2)

    await act(async () => { await router.navigate({ to: '/chat' }) })
    await waitFor(() => expect(queryClient.getQueryState(BOT_CONFIG_QUERY_KEY)?.status).toBe(
      crossTabReadStatus === 503 ? 'error' : 'success',
    ))
    await waitFor(() => {
      expect(title()).toHaveTextContent('春分')
      expect(container.querySelector('[data-chat-sidebar-section="local"] [data-conversation-item-heading]'))
        .toHaveTextContent('春分')
    })
    await waitFor(() => expect(queryClient.isFetching()).toBe(0))
    expect(configReads()).toBe(3)
    expect(localStorage.getItem(storageKey)).toBe(otherTabValue)
  } finally {
    cleanup()
    queryClient.clear()
    otherTab.remove()
    localStorage.removeItem(storageKey)
    localStorage.removeItem(`${BOT_CONFIG_UPDATED_EVENT}:bot:bot-b`)
    Reflect.deleteProperty(HTMLElement.prototype, 'scrollTo')
    setHostRuntime(previousRuntime)
    vi.unstubAllGlobals()
  }
})
