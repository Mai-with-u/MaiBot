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
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { BotConfigQuerySync } from '@/components/bot-config-query-sync'
import { ObservationConversations } from '@/components/observation-layout/ObservationConversations'
import { getHostRuntime, setHostRuntime } from '@/host/state'
import { localChatBotNicknameQueryKey, localChatInfoQueryKey } from '@/lib/bot-nickname'
import { chatWsClient } from '@/lib/chat-ws-client'
import { BOT_CONFIG_QUERY_KEY, BOT_CONFIG_UPDATED_EVENT, updateBotConfig } from '@/lib/config-api'
import { createQueryClient } from '@/lib/query'
import type { WsEventEnvelope } from '@/lib/unified-ws'

import { ChatPage } from '..'

const bot = vi.hoisted(() => ({
  configuredName: '麦麦',
  sessionName: '麦麦',
  sessionNicknameVersion: undefined as string | number | undefined,
  openedSessions: new Set<string>(),
  sessionOpenAttempts: 0,
  sessionInfoDeliveries: 0,
  pendingWsHandshake: null as Promise<void> | null,
  pendingApiBase: null as Promise<void> | null,
  pendingSessionRead: null as Promise<void> | null,
  pendingSessionInfo: null as Promise<void> | null,
  listeners: new Set<(message: WsEventEnvelope) => void>(),
}))

vi.mock('@/components/tour/use-tour', () => ({ useTour: () => ({ startTour: vi.fn() }) }))

vi.mock('@/lib/api-base', async (importOriginal) => {
  const apiBase = await importOriginal<typeof import('@/lib/api-base')>()
  return {
    ...apiBase,
    getApiBaseUrl: async () => {
      if (bot.pendingApiBase) await bot.pendingApiBase
      return apiBase.getApiBaseUrl()
    },
  }
})

vi.mock('@/lib/maisaka-monitor-client', () => ({
  maisakaMonitorClient: {
    subscribe: async () => async () => {},
    onConnectionChange: () => () => {},
  },
}))

vi.mock('@/lib/unified-ws', () => ({
  unifiedWsClient: {
    addEventListener: (listener: (message: WsEventEnvelope) => void) => {
      bot.listeners.add(listener)
      return () => bot.listeners.delete(listener)
    },
    getStatus: () => 'connected',
    onConnectionChange: () => () => {},
    onReconnect: () => () => {},
    call: async ({ domain, method, session, onSend }: {
      domain: string
      method: string
      session?: string
      onSend?: () => void
    }) => {
      if (domain === 'chat' && method === 'session.open' && session) {
        bot.sessionOpenAttempts += 1
        if (bot.pendingWsHandshake) await bot.pendingWsHandshake
        onSend?.()
        bot.openedSessions.add(session)
        if (bot.pendingSessionRead) await bot.pendingSessionRead
        const botName = bot.sessionName
        const nicknameVersion = bot.sessionNicknameVersion
        // session.open is acknowledged before session_info arrives on the socket.
        const delivery = bot.pendingSessionInfo ?? new Promise<void>((resolve) => setTimeout(resolve, 0))
        void delivery.then(() => {
          bot.sessionInfoDeliveries += 1
          bot.listeners.forEach((listener) => listener({
            op: 'event',
            domain: 'chat',
            event: 'session_info',
            session,
            data: { type: 'session_info', bot_name: botName, bot_nickname_version: nicknameVersion },
          }))
        })
      }
      return {}
    },
  },
}))

afterEach(async () => {
  await Promise.all(Array.from(bot.openedSessions, (sessionId) => chatWsClient.closeSession(sessionId)))
  bot.openedSessions.clear()
  bot.sessionNicknameVersion = undefined
  bot.sessionOpenAttempts = 0
  bot.sessionInfoDeliveries = 0
  bot.pendingWsHandshake = null
  bot.pendingApiBase = null
  bot.pendingSessionRead = null
  bot.pendingSessionInfo = null
})

async function updateFromOtherTab(frame: HTMLIFrameElement, key: string, value: string) {
  const otherWindow = frame.contentWindow
  if (!otherWindow) throw new Error('Could not create a second browser document')
  const delivered = new Promise<void>((resolve) => {
    window.addEventListener('storage', () => resolve(), { once: true })
  })
  otherWindow.localStorage.setItem(key, value)
  await delivered
}

it.each([200, 503])('opens and saves with one config read each, then shows the new nickname after a cross-tab save (GET %i)', async (crossTabReadStatus) => {
  const previousRuntime = getHostRuntime()
  setHostRuntime({ ...previousRuntime, host: true, selected_bot_id: 'bot-a', selected_bot_running: true })
  const storageKey = `${BOT_CONFIG_UPDATED_EVENT}:bot:bot-a`
  let configReadStatus = 200
  let configVersion = 0
  const fetchMock = vi.fn(async (url: string, options?: RequestInit) => {
    if (url.endsWith('/api/webui/config/bot')) {
      if (options?.method === 'POST') {
        bot.configuredName = JSON.parse(String(options.body)).bot.nickname
        configVersion += 1
        return Response.json({ success: true })
      }
      if (configReadStatus === 503) {
        return Response.json({ detail: 'Configuration is temporarily unavailable' }, { status: 503 })
      }
      return Response.json({ config: { bot: { nickname: bot.configuredName } }, bot_nickname_version: String(configVersion) })
    }
    if (url.endsWith('/api/chat/info')) {
      return Response.json({ bot_name: bot.sessionName, bot_qq: null, bot_nickname_version: bot.sessionNicknameVersion })
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
  const chat = createRoute({
    getParentRoute: () => root,
    path: '/chat',
    component: () => (
      <>
        <ObservationConversations collapsed />
        <ChatPage />
      </>
    ),
  })
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
    bot.sessionNicknameVersion = String(configVersion)
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
    await waitFor(() => {
      expect(title()).toHaveTextContent('小满')
      expect(container.querySelector('[data-observation-conversations] [data-conversation-item-heading]'))
        .toHaveTextContent('小满')
    })
    await waitFor(() => expect(queryClient.isFetching()).toBe(0))
    expect(configReads()).toBe(2)
    expect(localStorage.getItem(storageKey)).not.toBeNull()

    await act(async () => { await router.navigate({ to: '/settings' }) })
    await screen.findByText('Settings')
    expect(title()).toBeNull()

    document.body.append(otherTab)

    await act(() => updateFromOtherTab(otherTab, `${BOT_CONFIG_UPDATED_EVENT}:bot:bot-b`, 'different-bot-save'))
    expect(queryClient.getQueryState(BOT_CONFIG_QUERY_KEY)?.isInvalidated).toBe(false)

    bot.configuredName = '春分'
    bot.sessionName = '春分'
    configVersion += 1
    bot.sessionNicknameVersion = String(configVersion)
    configReadStatus = crossTabReadStatus
    const otherTabValue = 'another-tab-save'
    await act(() => updateFromOtherTab(otherTab, storageKey, otherTabValue))
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

describe('observation sidebar nickname sync', () => {
  const storageKey = `${BOT_CONFIG_UPDATED_EVENT}:bot:bot-a`
  let previousRuntime: ReturnType<typeof getHostRuntime>
  let queryClient: ReturnType<typeof createQueryClient>
  let otherTab: HTMLIFrameElement

  beforeEach(() => {
    previousRuntime = getHostRuntime()
    setHostRuntime({ ...previousRuntime, host: true, selected_bot_id: 'bot-a', selected_bot_running: true })
    queryClient = createQueryClient()
    otherTab = document.createElement('iframe')
    Object.defineProperty(HTMLElement.prototype, 'scrollTo', { configurable: true, value: () => {} })
  })

  afterEach(() => {
    cleanup()
    queryClient.clear()
    otherTab.remove()
    localStorage.removeItem(storageKey)
    Reflect.deleteProperty(HTMLElement.prototype, 'scrollTo')
    setHostRuntime(previousRuntime)
    vi.unstubAllGlobals()
  })

  const unavailableConfig = () => Response.json({ detail: 'Configuration is temporarily unavailable' }, { status: 503 })
  const chatInfoResponse = (nickname: string, version?: string | number) =>
    Response.json({ bot_name: nickname, bot_qq: null, bot_nickname_version: version })

  function configReadsStartingWith(initialStatus: 200 | 503) {
    return vi.fn(unavailableConfig).mockImplementationOnce(() => initialStatus === 200
      ? Response.json({ config: { bot: { nickname: '小满' } }, bot_nickname_version: 1 })
      : unavailableConfig())
  }

  async function renderSidebar(readConfig: () => Response | Promise<Response>, readChatInfo: () => Response | Promise<Response>) {
    vi.stubGlobal('fetch', vi.fn(async (url: string, options?: RequestInit) => {
      if (url.endsWith('/api/webui/config/bot')) {
        return options?.method === 'POST' ? Response.json({ success: true }) : readConfig()
      }
      if (url.endsWith('/api/chat/info')) return readChatInfo()
      if (url.includes('/api/chat/sessions?')) return Response.json({ sessions: [] })
      throw new Error(`Unexpected request: ${url}`)
    }))
    await updateBotConfig({ bot: { nickname: '小满' } })
    localStorage.removeItem(storageKey)

    const root = createRootRoute({
      component: () => (
        <>
          <ObservationConversations collapsed />
          <Outlet />
        </>
      ),
    })
    const chat = createRoute({ getParentRoute: () => root, path: '/chat', component: ChatPage })
    const settings = createRoute({ getParentRoute: () => root, path: '/settings', component: () => <div>Settings</div> })
    const router = createRouter({
      routeTree: root.addChildren([chat, settings]),
      history: createMemoryHistory({ initialEntries: ['/settings'] }),
    })
    const { container } = render(
      <StrictMode>
        <QueryClientProvider client={queryClient}>
          <BotConfigQuerySync />
          <RouterProvider router={router} />
        </QueryClientProvider>
      </StrictMode>,
    )

    function expectSidebarNickname(nickname: string) {
      expect(container.querySelector('[data-observation-conversations] [data-conversation-item-heading]'))
        .toHaveTextContent(nickname)
      expect(container.querySelector('[data-observation-chat-stack]'))
        .toHaveAttribute('aria-label', expect.stringContaining(nickname))
    }

    const title = () => container.querySelector('[data-conversation-heading]')

    function expectChatNickname(nickname: string) {
      expect(title()).toHaveTextContent(nickname)
      expect(container.querySelector('[data-chat-sidebar-section="local"] [data-conversation-item-heading]'))
        .toHaveTextContent(nickname)
      expectSidebarNickname(nickname)
    }

    return { router, expectSidebarNickname, expectChatNickname, title }
  }

  async function notifyFromOtherTab() {
    document.body.append(otherTab)
    await act(() => updateFromOtherTab(otherTab, storageKey, 'another-tab-save'))
  }

  it.each(['before', 'after'] as const)('keeps the WebSocket nickname when stale chat info resolves %s the session message', async (responseOrder) => {
    let resolveChatInfo!: (response: Response) => void
    const pendingChatInfo = new Promise<Response>((resolve) => { resolveChatInfo = resolve })
    const readChatInfo = vi.fn(() => pendingChatInfo).mockImplementationOnce(async () => chatInfoResponse('小满'))
    const { router, expectSidebarNickname, title } = await renderSidebar(unavailableConfig, readChatInfo)
    await waitFor(() => expectSidebarNickname('小满'))
    await waitFor(() => expect(queryClient.getQueryState(BOT_CONFIG_QUERY_KEY)?.status).toBe('error'))
    expect(queryClient.getQueryData(BOT_CONFIG_QUERY_KEY)).toBeUndefined()

    bot.sessionName = '春分'
    await notifyFromOtherTab()
    await waitFor(() => expect(readChatInfo).toHaveBeenCalledTimes(2))

    async function finishChatInfoRead() {
      await act(async () => { resolveChatInfo(chatInfoResponse('小满')) })
      await waitFor(() => expect(queryClient.isFetching({ queryKey: localChatInfoQueryKey('bot-a') })).toBe(0))
    }

    if (responseOrder === 'before') await finishChatInfoRead()
    await act(async () => { await router.navigate({ to: '/chat' }) })
    await waitFor(() => {
      expect(title()).toHaveTextContent('春分')
      expectSidebarNickname('春分')
    })
    if (responseOrder === 'after') await finishChatInfoRead()

    expect(title()).toHaveTextContent('春分')
    expectSidebarNickname('春分')
    expect(queryClient.getQueryData(BOT_CONFIG_QUERY_KEY)).toBeUndefined()
    expect(readChatInfo).toHaveBeenCalledTimes(2)
  })

  it('shows fresh chat info when a config refetch fails on the settings page', async () => {
    const cachedConfig = { bot: { nickname: '小满' } }
    const readConfig = vi.fn(unavailableConfig)
      .mockImplementationOnce(() => Response.json({ config: cachedConfig }))
    const readChatInfo = vi.fn(() => chatInfoResponse('春分')).mockImplementationOnce(() => chatInfoResponse('小满'))
    const { expectSidebarNickname, title } = await renderSidebar(readConfig, readChatInfo)
    await waitFor(() => expectSidebarNickname('小满'))
    await waitFor(() => expect(queryClient.getQueryData(BOT_CONFIG_QUERY_KEY)).toMatchObject({ config: cachedConfig }))

    await notifyFromOtherTab()
    await waitFor(() => expect(queryClient.getQueryState(BOT_CONFIG_QUERY_KEY)?.status).toBe('error'))
    expect(queryClient.getQueryData(BOT_CONFIG_QUERY_KEY)).toMatchObject({ config: cachedConfig })
    await waitFor(() => expect(queryClient.getQueryData(localChatInfoQueryKey('bot-a')))
      .toMatchObject({ bot_name: '春分' }))
    expect(title()).toBeNull()
    await waitFor(() => expectSidebarNickname('春分'))
    expect(readConfig).toHaveBeenCalledTimes(2)
    expect(readChatInfo).toHaveBeenCalledTimes(2)
  })

  it.each([
    { initialConfigStatus: 200, delayedSessionInfo: false },
    { initialConfigStatus: 503, delayedSessionInfo: false },
    { initialConfigStatus: 200, delayedSessionInfo: true },
    { initialConfigStatus: 503, delayedSessionInfo: true },
  ])('keeps the open chat and sidebar in sync after a 503 refresh (initial $initialConfigStatus, delayed session info $delayedSessionInfo)', async ({ initialConfigStatus, delayedSessionInfo }) => {
    const cachedConfig = { bot: { nickname: '小满' } }
    const readConfig = vi.fn(unavailableConfig)
    if (initialConfigStatus === 200) {
      readConfig.mockImplementationOnce(() => Response.json({ config: cachedConfig }))
    }
    const readChatInfo = vi.fn(() => chatInfoResponse('春分')).mockImplementationOnce(() => chatInfoResponse('小满'))
    const { router, expectSidebarNickname, expectChatNickname } = await renderSidebar(readConfig, readChatInfo)
    await waitFor(() => expectSidebarNickname('小满'))
    await waitFor(() => expect(queryClient.isFetching()).toBe(0))

    let deliverSessionInfo = () => {}
    if (delayedSessionInfo) {
      bot.pendingSessionInfo = new Promise<void>((resolve) => { deliverSessionInfo = resolve })
    }

    try {
      bot.sessionName = '小满'
      await act(async () => { await router.navigate({ to: '/chat' }) })
      await waitFor(() => expect(bot.openedSessions.size).toBe(1))
      if (!delayedSessionInfo) {
        await waitFor(() => expect(bot.sessionInfoDeliveries).toBe(1))
        await waitFor(() => expectChatNickname('小满'))
      }
      await waitFor(() => expect(queryClient.isFetching()).toBe(0))
      const configReadsBeforeSave = readConfig.mock.calls.length

      bot.sessionName = '春分'
      await notifyFromOtherTab()
      await waitFor(() => expect(queryClient.getQueryState(BOT_CONFIG_QUERY_KEY)?.status).toBe('error'))
      expect(queryClient.getQueryData(BOT_CONFIG_QUERY_KEY))
        .toEqual(initialConfigStatus === 200 ? { config: cachedConfig, nicknameVersion: undefined } : undefined)
      await waitFor(() => expectChatNickname('春分'))

      if (delayedSessionInfo) {
        expect(bot.sessionInfoDeliveries).toBe(0)
        await act(async () => { deliverSessionInfo() })
        expect(bot.sessionInfoDeliveries).toBe(1)
      }

      await waitFor(() => expectChatNickname('春分'))
      expect(bot.sessionInfoDeliveries).toBe(1)
      expect(readConfig).toHaveBeenCalledTimes(configReadsBeforeSave + 1)
      expect(readChatInfo).toHaveBeenCalledTimes(2)
    } finally {
      deliverSessionInfo()
      bot.pendingSessionInfo = null
    }
  })

  it('uses the newer session nickname when session.open waits for the WebSocket handshake', async () => {
    const readChatInfo = vi.fn(() => chatInfoResponse('小满'))
    const { router, expectSidebarNickname, expectChatNickname } = await renderSidebar(unavailableConfig, readChatInfo)
    await waitFor(() => expectSidebarNickname('小满'))
    await waitFor(() => expect(queryClient.isFetching()).toBe(0))

    let completeHandshake!: () => void
    bot.pendingWsHandshake = new Promise<void>((resolve) => { completeHandshake = resolve })
    try {
      bot.sessionName = '小满'
      await act(async () => { await router.navigate({ to: '/chat' }) })
      await waitFor(() => expect(bot.sessionOpenAttempts).toBe(1))
      expect(bot.openedSessions.size).toBe(0)

      await notifyFromOtherTab()
      await waitFor(() => expect(readChatInfo).toHaveBeenCalledTimes(2))
      await waitFor(() => expect(queryClient.isFetching()).toBe(0))
      expect(queryClient.getQueryState(BOT_CONFIG_QUERY_KEY)?.status).toBe('error')
      expectChatNickname('小满')
      expect(bot.openedSessions.size).toBe(0)

      bot.sessionName = '春分'
      await act(async () => { completeHandshake() })
      await waitFor(() => expect(queryClient.getQueryData(localChatBotNicknameQueryKey('bot-a')))
        .toMatchObject({ nickname: '春分' }))
      await waitFor(() => expectChatNickname('春分'))
    } finally {
      completeHandshake()
      bot.pendingWsHandshake = null
    }
  })

  it('uses the newer HTTP nickname when chat info waits for the API base URL', async () => {
    let chatInfoNickname = '小满'
    const readChatInfo = vi.fn(() => chatInfoResponse(chatInfoNickname))
    const { router, expectSidebarNickname, expectChatNickname } = await renderSidebar(unavailableConfig, readChatInfo)
    await waitFor(() => expectSidebarNickname('小满'))
    await waitFor(() => expect(queryClient.isFetching()).toBe(0))

    let resolveApiBase!: () => void
    bot.pendingApiBase = new Promise<void>((resolve) => { resolveApiBase = resolve })
    try {
      await notifyFromOtherTab()
      await waitFor(() => expect(queryClient.isFetching({ queryKey: localChatInfoQueryKey('bot-a') })).toBe(1))
      expect(readChatInfo).toHaveBeenCalledTimes(1)

      bot.sessionName = '小满'
      await act(async () => { await router.navigate({ to: '/chat' }) })
      await waitFor(() => expect(queryClient.getQueryData(localChatBotNicknameQueryKey('bot-a')))
        .toMatchObject({ nickname: '小满' }))
      expectChatNickname('小满')
      expect(readChatInfo).toHaveBeenCalledTimes(1)

      chatInfoNickname = '春分'
      await act(async () => { resolveApiBase() })
      await waitFor(() => expect(queryClient.getQueryData(localChatInfoQueryKey('bot-a')))
        .toMatchObject({ bot_name: '春分' }))
      expect(queryClient.getQueryState(BOT_CONFIG_QUERY_KEY)?.status).toBe('error')
      await waitFor(() => expectChatNickname('春分'))
    } finally {
      resolveApiBase()
      bot.pendingApiBase = null
    }
  })

  it.each([200, 503] as const)('keeps the newer HTTP nickname when an older session_info arrives late (initial GET %i)', async (initialStatus) => {
    let finishHttpRead!: () => void
    const pendingHttpRead = new Promise<void>((resolve) => { finishHttpRead = resolve })
    const readChatInfo = vi.fn(async () => {
      await pendingHttpRead
      return chatInfoResponse('春分', 2)
    }).mockImplementationOnce(async () => chatInfoResponse('小满', 1))
    const { router, expectSidebarNickname, expectChatNickname } = await renderSidebar(
      configReadsStartingWith(initialStatus), readChatInfo,
    )
    let deliverSessionInfo!: () => void
    try {
      await waitFor(() => expectSidebarNickname('小满'))
      await waitFor(() => expect(queryClient.isFetching()).toBe(0))
      await notifyFromOtherTab()
      await waitFor(() => expect(readChatInfo).toHaveBeenCalledTimes(2))

      bot.pendingSessionInfo = new Promise<void>((resolve) => { deliverSessionInfo = resolve })
      bot.sessionName = '小满'
      bot.sessionNicknameVersion = 1
      await act(async () => { await router.navigate({ to: '/chat' }) })
      await waitFor(() => expect(bot.openedSessions.size).toBe(1))
      expect(bot.sessionInfoDeliveries).toBe(0)

      await act(async () => { finishHttpRead() })
      await waitFor(() => expectChatNickname('春分'))
      expect(queryClient.getQueryState(BOT_CONFIG_QUERY_KEY)?.status).toBe('error')
      await act(async () => { deliverSessionInfo() })
      await waitFor(() => expect(queryClient.getQueryData(localChatBotNicknameQueryKey('bot-a')))
        .toMatchObject({ nickname: '小满' }))
      await waitFor(() => expectChatNickname('春分'))
    } finally {
      finishHttpRead()
      deliverSessionInfo?.()
      bot.pendingSessionInfo = null
    }
  })

  it.each([200, 503] as const)('keeps the newer session nickname when an older HTTP response arrives late (initial GET %i)', async (initialStatus) => {
    let resolveOldHttp!: (response: Response) => void
    const pendingHttpResponse = new Promise<Response>((resolve) => { resolveOldHttp = resolve })
    const readChatInfo = vi.fn(() => pendingHttpResponse)
      .mockImplementationOnce(async () => chatInfoResponse('小满', 1))
    const { router, expectSidebarNickname, expectChatNickname } = await renderSidebar(
      configReadsStartingWith(initialStatus), readChatInfo,
    )
    let finishSessionRead!: () => void
    try {
      await waitFor(() => expectSidebarNickname('小满'))
      await waitFor(() => expect(queryClient.isFetching()).toBe(0))
      bot.pendingSessionRead = new Promise<void>((resolve) => { finishSessionRead = resolve })
      await act(async () => { await router.navigate({ to: '/chat' }) })
      await waitFor(() => expect(bot.openedSessions.size).toBe(1))
      expect(bot.sessionInfoDeliveries).toBe(0)

      await notifyFromOtherTab()
      await waitFor(() => expect(readChatInfo).toHaveBeenCalledTimes(2))
      const oldResponse = chatInfoResponse('小满', 1)

      bot.sessionName = '春分'
      bot.sessionNicknameVersion = 2
      await act(async () => { finishSessionRead() })
      await waitFor(() => expectChatNickname('春分'))
      expect(queryClient.getQueryState(BOT_CONFIG_QUERY_KEY)?.status).toBe('error')
      await act(async () => { resolveOldHttp(oldResponse) })
      await waitFor(() => expect(queryClient.isFetching({ queryKey: localChatInfoQueryKey('bot-a') })).toBe(0))
      await waitFor(() => expectChatNickname('春分'))
    } finally {
      finishSessionRead?.()
      resolveOldHttp(chatInfoResponse('小满', 1))
      bot.pendingSessionRead = null
    }
  })

  it('uses the newer config version from a session opened before the last successful config GET', async () => {
    const oldVersion = '1795500000000000000'
    const newVersion = '1795500000000000100'
    let finishConfigRead!: (response: Response) => void
    const pendingConfig = new Promise<Response>((resolve) => { finishConfigRead = resolve })
    const readConfig = vi.fn<() => Response | Promise<Response>>(unavailableConfig).mockReturnValueOnce(pendingConfig)
    const readChatInfo = vi.fn(() => chatInfoResponse('小满', oldVersion))
    const { router, expectSidebarNickname, expectChatNickname } = await renderSidebar(readConfig, readChatInfo)
    const oldConfig = { config: { bot: { nickname: '小满' } }, bot_nickname_version: oldVersion }
    let finishSessionRead!: () => void

    try {
      await waitFor(() => expectSidebarNickname('小满'))
      bot.pendingSessionRead = new Promise<void>((resolve) => { finishSessionRead = resolve })
      await act(async () => { await router.navigate({ to: '/chat' }) })
      await waitFor(() => expect(bot.openedSessions.size).toBe(1))
      expect(queryClient.getQueryState(BOT_CONFIG_QUERY_KEY)?.status).toBe('pending')
      expect(bot.sessionInfoDeliveries).toBe(0)

      await act(async () => { finishConfigRead(Response.json(oldConfig)) })
      await waitFor(() => expect(queryClient.getQueryData(BOT_CONFIG_QUERY_KEY))
        .toEqual({ config: oldConfig.config, nicknameVersion: oldVersion }))
      await notifyFromOtherTab()
      await waitFor(() => expect(queryClient.getQueryState(BOT_CONFIG_QUERY_KEY)?.status).toBe('error'))
      await waitFor(() => expect(queryClient.isFetching()).toBe(0))

      bot.sessionName = '春分'
      bot.sessionNicknameVersion = newVersion
      await act(async () => { finishSessionRead() })
      const sessionKey = localChatBotNicknameQueryKey('bot-a')
      await waitFor(() => expect(queryClient.getQueryData(sessionKey)).toMatchObject({ nickname: '春分' }))
      const session = queryClient.getQueryData<{ request: { startedAt: number } }>(sessionKey)!
      expect(session.request.startedAt).toBeLessThanOrEqual(queryClient.getQueryState(BOT_CONFIG_QUERY_KEY)!.dataUpdatedAt)
      await waitFor(() => expectChatNickname('春分'))
    } finally {
      finishConfigRead(Response.json(oldConfig))
      finishSessionRead?.()
      bot.pendingSessionRead = null
    }
  })

  it('keeps the newer WebSocket nickname when config recovers with a delayed older snapshot', async () => {
    const oldVersion = '1795500000000000000'
    const newVersion = '1795500000000000100'
    const oldConfig = { config: { bot: { nickname: '小满' } }, bot_nickname_version: oldVersion }
    let finishConfigRead!: (response: Response) => void
    let finishSessionRead!: () => void
    const pendingConfig = new Promise<Response>((resolve) => { finishConfigRead = resolve })
    const readConfig = vi.fn<() => Response | Promise<Response>>(unavailableConfig)
      .mockImplementationOnce(() => Response.json(oldConfig))
      .mockImplementationOnce(unavailableConfig)
      .mockReturnValueOnce(pendingConfig)
    const readChatInfo = vi.fn(() => chatInfoResponse('小满', oldVersion))
    const { router, expectSidebarNickname, expectChatNickname } = await renderSidebar(readConfig, readChatInfo)

    try {
      await waitFor(() => expectSidebarNickname('小满'))
      await waitFor(() => expect(queryClient.isFetching()).toBe(0))
      bot.pendingSessionRead = new Promise<void>((resolve) => { finishSessionRead = resolve })
      await act(async () => { await router.navigate({ to: '/chat' }) })
      await waitFor(() => expect(bot.openedSessions.size).toBe(1))

      await notifyFromOtherTab()
      await waitFor(() => expect(queryClient.getQueryState(BOT_CONFIG_QUERY_KEY)?.status).toBe('error'))
      await act(() => updateFromOtherTab(otherTab, storageKey, 'config-refresh'))
      await waitFor(() => expect(readConfig).toHaveBeenCalledTimes(3))

      bot.sessionName = '春分'
      bot.sessionNicknameVersion = newVersion
      await act(async () => { finishSessionRead() })
      await waitFor(() => expectChatNickname('春分'))

      await act(async () => { finishConfigRead(Response.json(oldConfig)) })
      await waitFor(() => expect(queryClient.getQueryState(BOT_CONFIG_QUERY_KEY)?.status).toBe('success'))
      expect(queryClient.getQueryData(BOT_CONFIG_QUERY_KEY))
        .toEqual({ config: oldConfig.config, nicknameVersion: oldVersion })
      expectChatNickname('春分')
    } finally {
      finishConfigRead(Response.json(oldConfig))
      finishSessionRead?.()
      bot.pendingSessionRead = null
    }
  })

  it('keeps a confirmed config nickname when a failed refresh still sees the older runtime version', async () => {
    const oldVersion = '1795500000000000000'
    const newVersion = '1795500000000000100'
    const readConfig = vi.fn(unavailableConfig).mockImplementationOnce(
      () => Response.json({ config: { bot: { nickname: '春分' } }, bot_nickname_version: newVersion }),
    )
    const readChatInfo = vi.fn(() => chatInfoResponse('小满', oldVersion))
    const { router, expectSidebarNickname, expectChatNickname } = await renderSidebar(readConfig, readChatInfo)
    await waitFor(() => expectSidebarNickname('春分'))
    await waitFor(() => expect(queryClient.isFetching()).toBe(0))

    bot.sessionName = '小满'
    bot.sessionNicknameVersion = oldVersion
    await act(async () => { await router.navigate({ to: '/chat' }) })
    await waitFor(() => expect(queryClient.getQueryData(localChatBotNicknameQueryKey('bot-a')))
      .toMatchObject({ nickname: '小满' }))
    expectChatNickname('春分')

    await notifyFromOtherTab()
    await waitFor(() => expect(queryClient.getQueryState(BOT_CONFIG_QUERY_KEY)?.status).toBe('error'))
    await waitFor(() => expect(queryClient.isFetching()).toBe(0))
    expect(readChatInfo).toHaveBeenCalledTimes(2)
    await waitFor(() => expectChatNickname('春分'))
  })
})
