import { createElement, type ReactNode } from 'react'

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { DetailedStatisticsData } from './types'

const httpMocks = vi.hoisted(() => ({
  get: vi.fn(),
}))

vi.mock('@/lib/http', () => ({
  backendApi: { get: httpMocks.get },
}))

const sampleData: DetailedStatisticsData = {
  generated_at: '2026-07-01T12:00:00',
  periods: [],
  trends: {},
  metrics: {},
}

const richerData: DetailedStatisticsData = {
  generated_at: '2026-07-02T08:00:00',
  periods: [
    {
      key: 'all_time',
      start_time: '2026-06-01T12:00:00',
      end_time: '2026-07-02T08:00:00',
      summary: {
        online_time: 3600,
        total_messages: 0,
        total_replies: 0,
        total_requests: 0,
        total_tokens: 0,
        input_tokens: 0,
        output_tokens: 0,
        cache_hit_tokens: 0,
        cache_miss_tokens: 0,
        cache_hit_rate: null,
        total_cost: 0,
        cost_per_100_messages: 0,
        cost_per_100_messages_excluding_replies: 0,
        cost_per_100_replies: 0,
        cost_per_hour: 0,
        tokens_per_hour: 0,
      },
      models: [],
      modules: [],
      request_types: [],
      chats: [],
      distributions: {
        owner_costs: [],
        model_costs: [],
        module_costs: [],
        request_type_costs: [],
        chat_messages: [],
        chat_costs: [],
      },
    },
  ],
  trends: {
    '24h': {
      time_labels: [],
      total_cost_data: [],
      cost_by_model: {},
      cost_by_module: {},
      message_by_chat: {},
    },
  },
  metrics: {
    '7d': {
      time_labels: [],
      cost_per_100_messages: [],
      cost_per_hour: [],
      tokens_per_hour: [],
      cost_per_100_replies: [],
    },
  },
}

function createDeferred<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((nextResolve, nextReject) => {
    resolve = nextResolve
    reject = nextReject
  })
  return { promise, resolve, reject }
}

function createWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  })
  return function Wrapper({ children }: { children: ReactNode }) {
    return createElement(QueryClientProvider, { client: queryClient }, children)
  }
}

async function renderStatisticsHook() {
  const { useDetailedStatistics } = await import('./useDetailedStatistics')
  let view!: ReturnType<typeof renderHook>
  await act(async () => {
    view = renderHook(() => useDetailedStatistics(), { wrapper: createWrapper() })
    await Promise.resolve()
  })
  return view
}

describe('useDetailedStatistics', () => {
  beforeEach(() => {
    vi.resetModules()
    httpMocks.get.mockReset()
  })

  it('无缓存时进入加载态，成功后写入数据', async () => {
    const deferred = createDeferred<DetailedStatisticsData>()
    httpMocks.get.mockImplementation(() => deferred.promise)
    const { result } = await renderStatisticsHook()

    expect(result.current.data).toBeNull()
    expect(result.current.loading).toBe(true)
    expect(result.current.error).toBeNull()

    await act(async () => {
      deferred.resolve(sampleData)
    })
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.data).toEqual(sampleData)
    expect(result.current.error).toBeNull()
    expect(httpMocks.get).toHaveBeenCalledWith('/api/webui/statistics/detailed')
  })

  it('请求失败时暴露 Error.message，刷新成功后清除错误', async () => {
    httpMocks.get
      .mockRejectedValueOnce(new Error('统计接口暂不可用'))
      .mockResolvedValueOnce(richerData)

    const { result } = await renderStatisticsHook()
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.error).toBe('统计接口暂不可用')
    expect(result.current.data).toBeNull()

    await act(async () => {
      await result.current.refresh()
    })
    expect(result.current.error).toBeNull()
    expect(result.current.data).toEqual(richerData)
    expect(result.current.loading).toBe(false)
  })

  it('空消息 Error 与非 Error 失败时 error 为 null', async () => {
    httpMocks.get.mockRejectedValueOnce(new Error('   '))
    const emptyMessage = await renderStatisticsHook()
    await waitFor(() => expect(emptyMessage.result.current.loading).toBe(false))
    expect(emptyMessage.result.current.error).toBeNull()
    expect(emptyMessage.result.current.data).toBeNull()
    emptyMessage.unmount()

    vi.resetModules()
    httpMocks.get.mockRejectedValueOnce('network down')
    const nonError = await renderStatisticsHook()
    await waitFor(() => expect(nonError.result.current.loading).toBe(false))
    expect(nonError.result.current.error).toBeNull()
    expect(nonError.result.current.data).toBeNull()
  })

  it('缓存命中时第二次挂载立即带上已有数据', async () => {
    httpMocks.get.mockResolvedValue(sampleData)
    const first = await renderStatisticsHook()
    await waitFor(() => expect(first.result.current.data).toEqual(sampleData))
    first.unmount()

    const deferred = createDeferred<DetailedStatisticsData>()
    httpMocks.get.mockReset()
    httpMocks.get.mockImplementation(() => deferred.promise)
    const second = await renderStatisticsHook()
    expect(second.result.current.data).toEqual(sampleData)
    expect(second.result.current.error).toBeNull()

    await act(async () => {
      deferred.resolve(richerData)
    })
    await waitFor(() => expect(second.result.current.data).toEqual(richerData))
    expect(httpMocks.get).toHaveBeenCalledTimes(1)
  })

  it('卸载后过期成功响应不写入缓存', async () => {
    const deferred = createDeferred<DetailedStatisticsData>()
    httpMocks.get.mockImplementation(() => deferred.promise)

    const first = await renderStatisticsHook()
    expect(first.result.current.data).toBeNull()
    first.unmount()

    await act(async () => {
      deferred.resolve(sampleData)
      await deferred.promise
    })

    httpMocks.get.mockReset()
    const deferredNext = createDeferred<DetailedStatisticsData>()
    httpMocks.get.mockImplementation(() => deferredNext.promise)
    const second = await renderStatisticsHook()
    expect(second.result.current.data).toBeNull()
    expect(second.result.current.loading).toBe(true)

    await act(async () => {
      deferredNext.resolve(richerData)
    })
    await waitFor(() => expect(second.result.current.data).toEqual(richerData))
  })

  it('较旧的成功响应不会覆盖更新的请求', async () => {
    const older = createDeferred<DetailedStatisticsData>()
    const newer = createDeferred<DetailedStatisticsData>()
    let callCount = 0
    httpMocks.get.mockImplementation(() => {
      callCount += 1
      return callCount === 1 ? older.promise : newer.promise
    })

    const { result } = await renderStatisticsHook()
    act(() => {
      void result.current.refresh()
    })

    await act(async () => {
      older.resolve(sampleData)
      await older.promise
    })
    expect(result.current.data).toBeNull()
    expect(result.current.loading).toBe(true)

    await act(async () => {
      newer.resolve(richerData)
    })
    await waitFor(() => expect(result.current.data).toEqual(richerData))
    expect(result.current.loading).toBe(false)
    expect(result.current.error).toBeNull()
  })

  it('较旧的失败响应不会写入 error', async () => {
    const older = createDeferred<DetailedStatisticsData>()
    const newer = createDeferred<DetailedStatisticsData>()
    let callCount = 0
    httpMocks.get.mockImplementation(() => {
      callCount += 1
      return callCount === 1 ? older.promise : newer.promise
    })

    const { result } = await renderStatisticsHook()
    act(() => {
      void result.current.refresh()
    })

    await act(async () => {
      older.reject(new Error('旧请求失败'))
      await older.promise.catch(() => undefined)
    })
    expect(result.current.error).toBeNull()
    expect(result.current.loading).toBe(true)

    await act(async () => {
      newer.resolve(sampleData)
    })
    await waitFor(() => expect(result.current.data).toEqual(sampleData))
    expect(result.current.error).toBeNull()
  })

  it('空数据对象也会被成功写入', async () => {
    const emptyData: DetailedStatisticsData = {
      generated_at: '2026-01-01T00:00:00',
      periods: [],
      trends: {},
      metrics: {},
    }
    httpMocks.get.mockResolvedValue(emptyData)
    const { result } = await renderStatisticsHook()
    await waitFor(() => expect(result.current.data).toEqual(emptyData))
    expect(result.current.loading).toBe(false)
    expect(result.current.error).toBeNull()
  })
})
