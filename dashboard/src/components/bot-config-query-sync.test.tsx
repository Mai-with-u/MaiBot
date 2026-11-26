import { QueryClient, QueryClientProvider, useQuery } from '@tanstack/react-query'
import { act, render, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { BOT_CONFIG_QUERY_KEY, BOT_CONFIG_UPDATED_EVENT } from '@/lib/config-api'

import { BotConfigQuerySync } from './bot-config-query-sync'

type BotConfig = Record<string, unknown>

function createDeferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((resolvePromise) => {
    resolve = resolvePromise
  })
  return { promise, resolve }
}

function ConfigQuery({ queryFn }: { queryFn: () => Promise<BotConfig> }) {
  useQuery({ queryKey: BOT_CONFIG_QUERY_KEY, queryFn, retry: false })
  return null
}

describe('BotConfigQuerySync', () => {
  let queryClient: QueryClient | undefined

  afterEach(() => {
    queryClient?.clear()
    queryClient = undefined
  })

  it('keeps refreshed config when the canceled initial request resolves late', async () => {
    const requests: ReturnType<typeof createDeferred<BotConfig>>[] = []
    const queryFn = vi.fn(() => {
      const request = createDeferred<BotConfig>()
      requests.push(request)
      return request.promise
    })
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    queryClient = client

    render(
      <QueryClientProvider client={client}>
        <BotConfigQuerySync />
        <ConfigQuery queryFn={queryFn} />
      </QueryClientProvider>
    )

    await waitFor(() => expect(queryFn).toHaveBeenCalledTimes(1))
    act(() => window.dispatchEvent(new Event(BOT_CONFIG_UPDATED_EVENT)))
    await waitFor(() => expect(queryFn).toHaveBeenCalledTimes(2))

    const updatedConfig = { bot: { nickname: '小满' } }
    await act(async () => {
      requests[1].resolve(updatedConfig)
      await requests[1].promise
    })
    await waitFor(() => expect(client.getQueryData(BOT_CONFIG_QUERY_KEY)).toEqual(updatedConfig))

    await act(async () => {
      requests[0].resolve({ bot: { nickname: '麦麦' } })
      await requests[0].promise
    })
    expect(client.getQueryData(BOT_CONFIG_QUERY_KEY)).toEqual(updatedConfig)
  })
})
