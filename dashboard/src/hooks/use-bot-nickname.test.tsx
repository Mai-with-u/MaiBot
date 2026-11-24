import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, renderHook, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { LOCAL_CHAT_BOT_NICKNAME_QUERY_KEY } from '@/lib/bot-nickname'

import { useBotNickname } from './use-bot-nickname'

const mockGetBotConfigCached = vi.hoisted(() => vi.fn())

vi.mock('@/lib/config-api', () => ({
  BOT_CONFIG_QUERY_KEY: ['bot-config'],
  getBotConfigCached: mockGetBotConfigCached,
}))

function createWrapper(queryClient: QueryClient) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  }
}

describe('useBotNickname', () => {
  let queryClient: QueryClient | undefined

  beforeEach(() => {
    mockGetBotConfigCached.mockReset()
  })

  afterEach(() => {
    queryClient?.clear()
    queryClient = undefined
  })

  it('marks config unavailable when loading fails so known names can be used', async () => {
    mockGetBotConfigCached.mockRejectedValue(new Error('配置读取失败'))
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    queryClient = client

    const { result } = renderHook(() => useBotNickname(), {
      wrapper: createWrapper(client),
    })

    await waitFor(() => expect(client.getQueryState(['bot-config'])?.status).toBe('error'))
    expect(result.current).toEqual({ nickname: null, isConfigLoaded: false })
  })

  it('marks a successfully loaded empty nickname so stale session names are ignored', async () => {
    mockGetBotConfigCached.mockResolvedValue({ bot: { nickname: '' } })
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    queryClient = client

    const { result } = renderHook(() => useBotNickname(), {
      wrapper: createWrapper(client),
    })

    await waitFor(() => expect(client.getQueryState(['bot-config'])?.status).toBe('success'))
    expect(result.current).toEqual({ nickname: null, isConfigLoaded: true })
  })

  it('uses a newer session nickname after a failed refresh and keeps the config when the session name is older', async () => {
    mockGetBotConfigCached
      .mockResolvedValueOnce({ bot: { nickname: '小满' } })
      .mockRejectedValueOnce(new Error('配置读取失败'))
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    queryClient = client

    const { result } = renderHook(() => useBotNickname(), {
      wrapper: createWrapper(client),
    })

    await waitFor(() => expect(result.current.nickname).toBe('小满'))
    const configUpdatedAt = client.getQueryState(['bot-config'])?.dataUpdatedAt ?? 0
    await act(async () => {
      client.setQueryData(LOCAL_CHAT_BOT_NICKNAME_QUERY_KEY, '旧会话名', { updatedAt: configUpdatedAt - 1 })
      await client.invalidateQueries({ queryKey: ['bot-config'], exact: true })
    })

    await waitFor(() => expect(client.getQueryState(['bot-config'])?.status).toBe('error'))
    expect(result.current).toEqual({ nickname: '小满', isConfigLoaded: true })

    act(() => {
      client.setQueryData(LOCAL_CHAT_BOT_NICKNAME_QUERY_KEY, '新会话名', { updatedAt: configUpdatedAt + 1 })
    })
    await waitFor(() => expect(result.current).toEqual({ nickname: '新会话名', isConfigLoaded: true }))
  })
})
