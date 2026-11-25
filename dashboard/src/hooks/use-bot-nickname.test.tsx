import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, renderHook, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { localChatBotNicknameQueryKey, localChatInfoQueryKey } from '@/lib/bot-nickname'

import { useBotNickname } from './use-bot-nickname'

const mockGetBotConfigSnapshotCached = vi.hoisted(() => vi.fn())
const mockGetChatInfo = vi.hoisted(() => vi.fn())

vi.mock('@/lib/config-api', () => ({
  BOT_CONFIG_QUERY_KEY: ['bot-config'],
  getBotConfigSnapshotCached: mockGetBotConfigSnapshotCached,
}))

vi.mock('@/lib/http', () => ({ backendApi: { get: mockGetChatInfo } }))

function createWrapper(queryClient: QueryClient) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  }
}

describe('useBotNickname', () => {
  let queryClient: QueryClient | undefined

  beforeEach(() => {
    mockGetBotConfigSnapshotCached.mockReset()
    mockGetChatInfo.mockRejectedValue(new Error('聊天信息读取失败'))
  })

  afterEach(() => {
    queryClient?.clear()
    queryClient = undefined
  })

  it('marks config unavailable when loading fails so known names can be used', async () => {
    mockGetBotConfigSnapshotCached.mockRejectedValue(new Error('配置读取失败'))
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    queryClient = client

    const { result } = renderHook(() => useBotNickname(), {
      wrapper: createWrapper(client),
    })

    await waitFor(() => expect(client.getQueryState(['bot-config'])?.status).toBe('error'))
    expect(result.current).toEqual({ nickname: null, isResolved: false })
  })

  it('marks a successfully loaded empty nickname so stale session names are ignored', async () => {
    mockGetBotConfigSnapshotCached.mockResolvedValue({ config: { bot: { nickname: '' } } })
    mockGetChatInfo.mockImplementation(async (_path: string, { onSend }: { onSend?: () => void }) => {
      onSend?.()
      return { bot_name: '旧聊天名称', bot_qq: null }
    })
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    queryClient = client

    const { result } = renderHook(() => useBotNickname(), {
      wrapper: createWrapper(client),
    })

    await waitFor(() => expect(client.getQueryState(['bot-config'])?.status).toBe('success'))
    await waitFor(() => expect(client.getQueryData(localChatInfoQueryKey(null))).toMatchObject({ bot_name: '旧聊天名称' }))
    expect(result.current).toEqual({ nickname: null, isResolved: true })
  })

  it('uses a newer session nickname after a failed refresh and keeps the config when the session name is older', async () => {
    mockGetBotConfigSnapshotCached
      .mockResolvedValueOnce({ config: { bot: { nickname: '小满' } } })
      .mockRejectedValueOnce(new Error('配置读取失败'))
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    queryClient = client

    const { result } = renderHook(() => useBotNickname(), {
      wrapper: createWrapper(client),
    })

    await waitFor(() => expect(result.current.nickname).toBe('小满'))
    const configUpdatedAt = client.getQueryState(['bot-config'])?.dataUpdatedAt ?? 0
    const sessionNicknameKey = localChatBotNicknameQueryKey(null)
    await act(async () => {
      client.setQueryData(sessionNicknameKey, {
        nickname: '旧会话名',
        request: { order: 1, startedAt: configUpdatedAt - 1 },
      })
      await client.invalidateQueries({ queryKey: ['bot-config'], exact: true })
    })

    await waitFor(() => expect(client.getQueryState(['bot-config'])?.status).toBe('error'))
    expect(result.current).toEqual({ nickname: '小满', isResolved: true })

    act(() => {
      client.setQueryData(sessionNicknameKey, {
        nickname: '新会话名',
        request: { order: 2, startedAt: configUpdatedAt + 1 },
      })
    })
    await waitFor(() => expect(result.current).toEqual({ nickname: '新会话名', isResolved: true }))
  })
})
